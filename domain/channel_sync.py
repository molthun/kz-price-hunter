"""Синхронизация предложений витрины продавца на маркетплейсе с каталогом товаров."""
from __future__ import annotations
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from domain.adapter import canonical_product_id
from domain.attribute_indexing import index_product_facts
from domain.image_quality import upgrade_image_url
from model_matching import extract_canonical_key
from scrapers.showcase_scanners import (
    ShowcaseOffer,
    extract_marketplace_merchant_info,
    KaspiShowcaseScanner,
    HalykShowcaseScanner,
    ForteShowcaseScanner,
    WildberriesShowcaseScanner,
    OzonShowcaseScanner,
)

logger = logging.getLogger("kz_price_hunter.channel_sync")


def make_fallback_canonical_key(title: str) -> str:
    """Генерирует резервный канонический ключ при отсутствии точной модели."""
    cleaned = re.sub(r'[^\w\s-]', '', title.lower()).strip()
    words = [w for w in cleaned.split() if len(w) > 1][:8]
    slug = "-".join(words) or "item"
    h = hashlib.sha256(title.strip().lower().encode("utf-8")).hexdigest()[:8]
    return f"raw:{slug}:{h}"


def match_or_create_canonical_product(
    conn,
    offer: ShowcaseOffer,
    now_iso: str,
) -> Tuple[str, bool]:
    """Находит существующий товар или создаёт новый в canonical_products."""
    title = offer.title.strip()
    extracted_key = extract_canonical_key(title)
    canonical_key = extracted_key if extracted_key else make_fallback_canonical_key(title)
    prod_id = canonical_product_id(canonical_key)

    # 1. Проверяем по prod_id или canonical_key
    row = conn.execute(
        "SELECT id FROM canonical_products WHERE id = ? OR canonical_key = ?",
        (prod_id, canonical_key),
    ).fetchone()

    if row and row[0]:
        return row[0], False

    # 2. Проверяем по точному совпадению названия (без учёта регистра)
    row_title = conn.execute(
        "SELECT id FROM canonical_products WHERE title = ? COLLATE NOCASE",
        (title,),
    ).fetchone()
    if row_title and row_title[0]:
        return row_title[0], False

    # 3. Создаём новый канонический товар
    category = offer.category or "Разное"
    conn.execute('''
        INSERT INTO canonical_products (
            id, canonical_key, title, category, brand, model,
            description, image_url, attributes_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        prod_id,
        canonical_key,
        title,
        category,
        None,
        None,
        "",
        upgrade_image_url(offer.image_url),
        "{}",
        now_iso,
        now_iso,
    ))

    # FTS синхронизация (если есть таблица canonical_products_fts)
    try:
        conn.execute('''
            INSERT INTO canonical_products_fts (rowid, title, category)
            SELECT rowid, title, category FROM canonical_products WHERE id = ?
        ''', (prod_id,))
    except Exception:
        pass

    return prod_id, True


def upsert_showcase_offer(
    conn,
    offer: ShowcaseOffer,
    product_id: str,
    seller_id: str,
    channel_id: str,
    now_iso: str,
) -> str:
    """Добавляет или обновляет предложение продавца в таблице offers."""
    sku_clean = re.sub(r'[^\w-]', '', offer.external_sku) or hashlib.md5(offer.url.encode()).hexdigest()[:12]
    offer_id = f"off_{channel_id}_{sku_clean}"[:64]
    avail = "in_stock" if offer.in_stock else "out_of_stock"
    from content_retention import source_free
    clean_raw = source_free(offer.raw_data or {})
    raw_payload = json.dumps(clean_raw, ensure_ascii=False)

    # Отслеживание динамики цены при повторном наблюдении
    effective_old_price = offer.old_price
    try:
        cur_row = conn.execute("SELECT price, old_price FROM offers WHERE id = ?", (offer_id,)).fetchone()
        if cur_row and cur_row[0] is not None and float(cur_row[0]) != float(offer.price):
            if not effective_old_price:
                effective_old_price = float(cur_row[0])
    except Exception:
        pass

    conn.execute('''
        INSERT INTO offers (
            id, product_id, seller_id, channel_id, external_sku,
            url, image_url, price, old_price, currency,
            condition, availability, city, is_active,
            raw_payload_json, observed_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            product_id=excluded.product_id,
            price=excluded.price,
            old_price=excluded.old_price,
            url=excluded.url,
            image_url=COALESCE(excluded.image_url, offers.image_url),
            availability=excluded.availability,
            observed_at=excluded.observed_at,
            updated_at=excluded.updated_at,
            is_active=1
    ''', (
        offer_id,
        product_id,
        seller_id,
        channel_id,
        offer.external_sku,
        offer.url,
        upgrade_image_url(offer.image_url),
        offer.price,
        effective_old_price,
        "KZT",
        "NEW",
        avail,
        offer.city or "Алматы",
        1,
        raw_payload,
        now_iso,
        now_iso,
        now_iso,
    ))

    # Запись в историю цен со всеми измерениями витрины
    try:
        conn.execute('''
            INSERT OR IGNORE INTO offer_price_history (
                offer_id, price, old_price, observed_at, product_id, seller_id, channel_id, condition, city
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (offer_id, offer.price, effective_old_price, now_iso, product_id, seller_id, channel_id, "NEW", offer.city or "Казахстан"))
    except Exception:
        try:
            conn.execute('''
                INSERT OR IGNORE INTO offer_price_history (offer_id, price, old_price, observed_at)
                VALUES (?, ?, ?, ?)
            ''', (offer_id, offer.price, effective_old_price, now_iso))
        except Exception:
            pass

    # Индексация характеристик из названия/данных
    try:
        index_product_facts(conn, product_id, [], offer.title)
    except Exception:
        pass

    return offer_id


async def sync_channel_showcase(
    conn,
    channel_id: str,
    max_pages: int = 15,
) -> Dict[str, Any]:
    """Синхронизирует витрину продавца по его каналу на маркетплейсе."""
    row = conn.execute(
        "SELECT id, seller_id, channel_type, name, external_store_id, base_url, metadata_json FROM channels WHERE id = ?",
        (channel_id,),
    ).fetchone()

    if not row:
        return {"ok": False, "error": f"Канал {channel_id} не найден"}

    ch_id, seller_id, channel_type, ch_name, ext_store_id, base_url, meta_json = row

    ch_type_lower = (channel_type or "").strip().lower()
    known_mp_types = {
        "wildberries": "wildberries",
        "wb": "wildberries",
        "ozon": "ozon",
        "kaspi": "kaspi",
        "forte": "forte",
        "fortemarket": "forte",
        "halyk": "halyk",
    }
    declared_mp = known_mp_types.get(ch_type_lower)

    url_mp, url_merchant = extract_marketplace_merchant_info(base_url or "") if base_url else (None, None)

    # Validate against conflicting configuration
    if declared_mp and url_mp and declared_mp != url_mp:
        return {
            "ok": False,
            "error": f"Конфликт конфигурации канала: channel_type='{channel_type}' ({declared_mp}), но base_url='{base_url}' указывает на {url_mp}",
        }

    mp = declared_mp or url_mp
    merchant_id = url_merchant

    if not merchant_id and ext_store_id:
        ext_s = str(ext_store_id).strip()
        if ":" in ext_s:
            id_prefix, _, id_val = ext_s.partition(":")
            id_mp = known_mp_types.get(id_prefix.lower())
            if declared_mp and id_mp and declared_mp != id_mp:
                return {
                    "ok": False,
                    "error": f"Конфликт конфигурации канала: channel_type='{channel_type}' ({declared_mp}), но external_store_id='{ext_store_id}' указывает на {id_mp}",
                }
            merchant_id = id_val.strip()
            if not mp:
                mp = id_mp
        else:
            merchant_id = ext_s

    if not mp:
        combined = f"{base_url or ''} {ch_name or ''}".lower()
        if "kaspi" in combined:
            mp = "kaspi"
        elif "halyk" in combined:
            mp = "halyk"
        elif "forte" in combined:
            mp = "forte"
        elif "wildberries" in combined or "wb" in combined:
            mp = "wildberries"
        elif "ozon" in combined:
            mp = "ozon"
        elif ext_store_id and str(ext_store_id).isdigit():
            mp = "kaspi"
        else:
            mp = ch_type_lower or "kaspi"

    if not merchant_id:
        merchant_id = ext_store_id or base_url or ""

    if not merchant_id:
        return {"ok": False, "error": f"У канала {channel_id} не указан идентификатор или ссылка витрины мерчанта"}

    logger.info(f"Начало сбора витрины: канал={ch_id}, маркетплейс={mp}, мерчант={merchant_id}")

    # Запуск сканера
    offers: List[ShowcaseOffer] = []
    try:
        if mp == "kaspi":
            scanner = KaspiShowcaseScanner()
            offers = await scanner.scan_merchant_showcase(merchant_id, max_pages=max_pages)
        elif mp == "halyk":
            h_scanner = HalykShowcaseScanner()
            offers = await h_scanner.scan_merchant_showcase(merchant_id, max_pages=max_pages)
        elif mp in ("forte", "fortemarket"):
            f_scanner = ForteShowcaseScanner()
            offers = await f_scanner.scan_merchant_showcase(merchant_id, max_pages=max_pages)
        elif mp in ("wildberries", "wb"):
            wb_scanner = WildberriesShowcaseScanner()
            offers = await wb_scanner.scan_merchant_showcase(merchant_id, max_pages=max_pages)
        elif mp == "ozon":
            ozon_scanner = OzonShowcaseScanner()
            offers = await ozon_scanner.scan_merchant_showcase(merchant_id, max_pages=max_pages)
        else:
            return {"ok": False, "error": f"Неподдерживаемый маркетплейс для витрины: {mp}"}
    except Exception as exc:
        logger.error(f"Сбой сбора витрины {mp}:{merchant_id}: {exc}")
        now_err_iso = datetime.now(timezone.utc).isoformat()
        try:
            current_meta = json.loads(meta_json or "{}") if isinstance(meta_json, str) else (meta_json or {})
        except Exception:
            current_meta = {}
        sync_history = current_meta.get("sync_history") or []
        if not isinstance(sync_history, list):
            sync_history = []
        sync_history.insert(0, {
            "synced_at": now_err_iso,
            "error": str(exc),
            "status": "error",
        })
        # last_sync_at — время последнего полного сбора: отказ его не трогает, иначе витрина
        # выглядит свежей (G7-A03). Планировщик отсчитывает повтор от last_attempt_at.
        current_meta.update({
            "last_attempt_at": now_err_iso,
            "last_sync_status": "error",
            "last_sync_error": str(exc),
            "marketplace": mp,
            "merchant_id": merchant_id,
            "sync_history": sync_history[:25],
        })
        conn.execute(
            "UPDATE channels SET metadata_json = ? WHERE id = ?",
            (json.dumps(current_meta, ensure_ascii=False), ch_id),
        )
        if hasattr(conn, "commit"):
            conn.commit()
        return {
            "ok": False,
            "channel_id": ch_id,
            "marketplace": mp,
            "merchant_id": merchant_id,
            "status": "error",
            "error": str(exc),
        }

    # Полнота обхода: у сканеров WB/Ozon она явная; прежние сканеры её не сообщают (None)
    coverage = getattr(offers, "status", None)
    coverage_reason = getattr(offers, "reason", None)
    sync_status = "limited" if coverage == "limited" else "ok"

    if not offers and coverage is None:
        logger.warning(f"Сканер витрины вернул 0 предложений: mp={mp}, merchant={merchant_id}")
        return {
            "ok": True,
            "channel_id": ch_id,
            "marketplace": mp,
            "merchant_id": merchant_id,
            "scanned_count": 0,
            "created_products": 0,
            "offers_upserted": 0,
        }

    now_iso = datetime.now(timezone.utc).isoformat()
    new_products_count = 0
    upserted_offers_count = 0
    scanned_watch_offers = []

    for off in offers:
        try:
            prod_id, is_new = match_or_create_canonical_product(conn, off, now_iso)
            if is_new:
                new_products_count += 1
            offer_id = upsert_showcase_offer(conn, off, prod_id, seller_id, ch_id, now_iso)
            upserted_offers_count += 1
            scanned_watch_offers.append({
                "id": prod_id,
                "product_id": prod_id,
                "canonical_product_id": prod_id,
                "offer_id": offer_id,
                "title": off.title,
                "current_price": off.price,
                "price": off.price,
                "old_price": off.old_price,
                "is_available": off.in_stock,
                "url": off.url,
                "image_url": off.image_url,
                "seller_id": seller_id,
                "seller_name": ch_name,
                "seller_slug": seller_id,
                "channel_id": ch_id,
                "channel_type": mp,
                "shop": ch_name or mp,
                "city": off.city or "Алматы",
                "condition": "new",
                "item_condition": "new",
            })
        except Exception as ex:
            logger.exception(f"Ошибка при сохранении оффера {off.external_sku}: {ex}")
            continue

    # Фиксация предложений в БД перед вызовом других подсистем (во избежание database is locked)
    if hasattr(conn, "commit"):
        conn.commit()

    # Оценка активных наблюдений за ценами пользователей (после фиксации предложений в БД)
    notifications_queued = 0
    if scanned_watch_offers:
        try:
            from database import evaluate_watches
            notifications_queued = evaluate_watches(scanned_watch_offers)
            if notifications_queued > 0:
                logger.info(f"Синхронизация витрины {ch_id}: создано {notifications_queued} уведомлений по наблюдениям")
        except Exception as wex:
            logger.warning(f"Ошибка при оценке наблюдений для канала {ch_id}: {wex}")

    # Обновление метаданных канала и истории запусков
    try:
        current_meta = json.loads(meta_json or "{}") if isinstance(meta_json, str) else {}
    except Exception:
        current_meta = {}

    sync_history = current_meta.get("sync_history") or []
    if not isinstance(sync_history, list):
        sync_history = []
    sync_history.insert(0, {
        "synced_at": now_iso,
        "scanned_count": len(offers),
        "created_products": new_products_count,
        "offers_upserted": upserted_offers_count,
        "notifications_queued": notifications_queued,
        "status": sync_status,
        **({"reason": coverage_reason} if coverage_reason else {}),
    })

    current_meta.update({
        "last_attempt_at": now_iso,
        "last_sync_status": sync_status,
        "marketplace": mp,
        "merchant_id": merchant_id,
        "sync_history": sync_history[:25],
    })
    current_meta.pop("last_sync_error", None)
    if sync_status == "ok":
        # Свежесть и число товаров канала — только по полному обходу; неполный обход обновил
        # увиденные предложения, но не подтверждает, что витрина собрана целиком (G7-A03/A04)
        current_meta.update({"last_sync_at": now_iso, "products_count": upserted_offers_count})
        current_meta.pop("last_sync_reason", None)
    else:
        current_meta["last_sync_reason"] = coverage_reason

    conn.execute(
        "UPDATE channels SET metadata_json = ?, is_active = 1 WHERE id = ?",
        (json.dumps(current_meta, ensure_ascii=False), ch_id),
    )

    if hasattr(conn, "commit"):
        conn.commit()

    logger.info(f"Витрина успешно синхронизирована: канал={ch_id}, товаров={upserted_offers_count}, новых={new_products_count}")
    return {
        "ok": True,
        "status": sync_status,
        "complete": sync_status == "ok",
        **({"reason": coverage_reason} if coverage_reason else {}),
        "channel_id": ch_id,
        "marketplace": mp,
        "merchant_id": merchant_id,
        "scanned_count": len(offers),
        "created_products": new_products_count,
        "offers_upserted": upserted_offers_count,
        "notifications_queued": notifications_queued,
    }
