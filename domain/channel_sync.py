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
from model_matching import extract_canonical_key
from scrapers.showcase_scanners import (
    ShowcaseOffer,
    extract_marketplace_merchant_info,
    KaspiShowcaseScanner,
    HalykShowcaseScanner,
    ForteShowcaseScanner,
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
            attributes_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        prod_id,
        canonical_key,
        title,
        category,
        None,
        None,
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
    raw_payload = json.dumps(offer.raw_data or {}, ensure_ascii=False)

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
        offer.image_url,
        offer.price,
        offer.old_price,
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

    # Запись в историю цен
    try:
        conn.execute('''
            INSERT OR IGNORE INTO offer_price_history (offer_id, price, old_price, observed_at)
            VALUES (?, ?, ?, ?)
        ''', (offer_id, offer.price, offer.old_price, now_iso))
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
    target_url = base_url or ext_store_id or ""

    mp, merchant_id = extract_marketplace_merchant_info(target_url)
    if not mp:
        mp = channel_type.lower() if channel_type else "kaspi"
        merchant_id = ext_store_id or target_url

    if not merchant_id:
        return {"ok": False, "error": f"У канала {channel_id} не указан идентификатор или ссылка витрины мерчанта"}

    logger.info(f"Начало сбора витрины: канал={ch_id}, маркетплейс={mp}, мерчант={merchant_id}")

    # Запуск сканера
    offers: List[ShowcaseOffer] = []
    if mp == "kaspi":
        scanner = KaspiShowcaseScanner()
        offers = await scanner.scan_merchant_showcase(merchant_id, max_pages=max_pages)
    elif mp == "halyk":
        h_scanner = HalykShowcaseScanner()
        offers = await h_scanner.scan_merchant_showcase(merchant_id, max_pages=max_pages)
    elif mp in ("forte", "fortemarket"):
        f_scanner = ForteShowcaseScanner()
        offers = await f_scanner.scan_merchant_showcase(merchant_id, max_pages=max_pages)
    else:
        return {"ok": False, "error": f"Неподдерживаемый маркетплейс для витрины: {mp}"}

    if not offers:
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

    for off in offers:
        try:
            prod_id, is_new = match_or_create_canonical_product(conn, off, now_iso)
            if is_new:
                new_products_count += 1
            upsert_showcase_offer(conn, off, prod_id, seller_id, ch_id, now_iso)
            upserted_offers_count += 1
        except Exception as ex:
            logger.exception(f"Ошибка при сохранении оффера {off.external_sku}: {ex}")
            continue

    # Обновление метаданных канала
    try:
        current_meta = json.loads(meta_json or "{}") if isinstance(meta_json, str) else {}
    except Exception:
        current_meta = {}

    current_meta.update({
        "last_sync_at": now_iso,
        "products_count": upserted_offers_count,
        "last_sync_status": "ok",
        "marketplace": mp,
        "merchant_id": merchant_id,
    })

    conn.execute(
        "UPDATE channels SET metadata_json = ?, is_active = 1 WHERE id = ?",
        (json.dumps(current_meta, ensure_ascii=False), ch_id),
    )

    if hasattr(conn, "commit"):
        conn.commit()

    logger.info(f"Витрина успешно синхронизирована: канал={ch_id}, товаров={upserted_offers_count}, новых={new_products_count}")
    return {
        "ok": True,
        "channel_id": ch_id,
        "marketplace": mp,
        "merchant_id": merchant_id,
        "scanned_count": len(offers),
        "created_products": new_products_count,
        "offers_upserted": upserted_offers_count,
    }
