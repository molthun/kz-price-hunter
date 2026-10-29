"""
Adapter layer mapping legacy scraper product dicts to KZ Price Hunter 2.0 entities.
Enables Zero-Downtime Dual-Write without modifying existing scraper implementations.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from domain.models import (
    Availability,
    CanonicalProduct,
    Channel,
    ChannelType,
    Condition,
    Offer,
    OfferPriceHistory,
    Seller,
    utc_now_iso,
)

logger = logging.getLogger("kz_price_hunter.domain.adapter")

from config import SHOP_KEYS

# Cyrillic transliteration mapping for deterministic slug creation
CYRILLIC_TO_LATIN = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'yo',
    'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
    'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
    'ф': 'f', 'х': 'kh', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'shch',
    'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
    'ә': 'ae', 'ғ': 'gh', 'қ': 'q', 'ң': 'ng', 'ө': 'oe', 'ұ': 'u', 'ү': 'ue', 'һ': 'h', 'і': 'i'
}

def transliterate_to_slug(text: str) -> str:
    res = []
    for ch in text.lower():
        res.append(CYRILLIC_TO_LATIN.get(ch, ch))
    slug = re.sub(r"[^a-zA-Z0-9_]+", "_", "".join(res)).strip("_")
    return slug

# Shop display name / prefix mapping to stable seller slug and channel type
KNOWN_SELLER_MAPPINGS: Dict[str, Tuple[str, str, str]] = {
    "белый ветер": ("shopkz", "Белый Ветер", ChannelType.WEBSITE),
    "shop.kz": ("shopkz", "Белый Ветер", ChannelType.WEBSITE),
    "4mobile": ("fourmobile", "4mobile", ChannelType.DIRECT),
    "kaspi": ("kaspi", "Kaspi Магазин", ChannelType.KASPI),
    "каспи": ("kaspi", "Kaspi Магазин", ChannelType.KASPI),
    "fortemarket": ("fortemarket", "ForteMarket", ChannelType.FORTE),
    "forte": ("fortemarket", "ForteMarket", ChannelType.FORTE),
    "форте": ("fortemarket", "ForteMarket", ChannelType.FORTE),
    "halyk": ("halyk", "Halyk Market", ChannelType.HALYK),
    "халык": ("halyk", "Halyk Market", ChannelType.HALYK),
    "dns": ("dns", "DNS Казахстан", ChannelType.WEBSITE),
    "днс": ("dns", "DNS Казахстан", ChannelType.WEBSITE),
    "технодом": ("technodom", "Technodom", ChannelType.WEBSITE),
    "technodom": ("technodom", "Technodom", ChannelType.WEBSITE),
    "мечта": ("mechta", "Мечта", ChannelType.WEBSITE),
    "mechta": ("mechta", "Мечта", ChannelType.WEBSITE),
    "sulpak": ("sulpak", "Sulpak", ChannelType.WEBSITE),
    "сулпак": ("sulpak", "Sulpak", ChannelType.WEBSITE),
    "alser": ("alser", "Alser", ChannelType.WEBSITE),
    "алсер": ("alser", "Alser", ChannelType.WEBSITE),
    "эврика": ("evrika", "Evrika", ChannelType.WEBSITE),
    "evrika": ("evrika", "Evrika", ChannelType.WEBSITE),
    "moon": ("moon", "Moon", ChannelType.WEBSITE),
    "flip": ("flip", "Flip.kz", ChannelType.WEBSITE),
    "флип": ("flip", "Flip.kz", ChannelType.WEBSITE),
    "tgrad": ("tgrad", "ТехноГрад", ChannelType.WEBSITE),
    "техноград": ("tgrad", "ТехноГрад", ChannelType.WEBSITE),
    "ants": ("ants", "Муравей", ChannelType.WEBSITE),
    "муравей": ("ants", "Муравей", ChannelType.WEBSITE),
    "itmag": ("itmag", "ITMag", ChannelType.WEBSITE),
    "айтмаг": ("itmag", "ITMag", ChannelType.WEBSITE),
    "ispace": ("ispace", "iSpace", ChannelType.WEBSITE),
    "айспейс": ("ispace", "iSpace", ChannelType.WEBSITE),
    "vkusmart": ("vkusmart", "Вкусмарт", ChannelType.WEBSITE),
    "вкусмарт": ("vkusmart", "Вкусмарт", ChannelType.WEBSITE),
    "12 месяцев": ("twelve_months", "12 Месяцев", ChannelType.WEBSITE),
    "двенадцать месяцев": ("twelve_months", "12 Месяцев", ChannelType.WEBSITE),
    "twelve_months": ("twelve_months", "12 Месяцев", ChannelType.WEBSITE),
    "zeta": ("zeta", "ZETA", ChannelType.WEBSITE),
    "зета": ("zeta", "ZETA", ChannelType.WEBSITE),
    "комфорт": ("komfort", "Комфорт", ChannelType.WEBSITE),
    "komfort": ("komfort", "Комфорт", ChannelType.WEBSITE),
    "лемана про": ("lemanapro", "Лемана ПРО", ChannelType.WEBSITE),
    "лемана": ("lemanapro", "Лемана ПРО", ChannelType.WEBSITE),
    "lemanapro": ("lemanapro", "Лемана ПРО", ChannelType.WEBSITE),
    "арбуз": ("arbuz", "Арбуз", ChannelType.WEBSITE),
    "arbuz": ("arbuz", "Arbuz", ChannelType.WEBSITE),
    "мастерок": ("masterok", "МастерОК", ChannelType.WEBSITE),
    "masterok": ("masterok", "МастерОК", ChannelType.WEBSITE),
    "магнум": ("magnum", "Магнум", ChannelType.WEBSITE),
    "magnum": ("magnum", "Магнум", ChannelType.WEBSITE),
    "интертоп": ("intertop", "Интертоп", ChannelType.WEBSITE),
    "intertop": ("intertop", "Интертоп", ChannelType.WEBSITE),
    "марвин": ("marwin", "Меломан / MARWIN", ChannelType.WEBSITE),
    "меломан": ("marwin", "Меломан / MARWIN", ChannelType.WEBSITE),
    "marwin": ("marwin", "Меломан / MARWIN", ChannelType.WEBSITE),
    "i-teka": ("iteka", "i-Teka", ChannelType.WEBSITE),
    "iteka": ("iteka", "i-Teka", ChannelType.WEBSITE),
    "ай-тека": ("iteka", "i-Teka", ChannelType.WEBSITE),
    "айтека": ("iteka", "i-Teka", ChannelType.WEBSITE),
    "mebel.kz": ("mebel", "Mebel.kz", ChannelType.WEBSITE),
    "mebel": ("mebel", "Mebel.kz", ChannelType.WEBSITE),
    "мебель.кз": ("mebel", "Mebel.kz", ChannelType.WEBSITE),
    "детский мир": ("detmir", "Детский мир", ChannelType.WEBSITE),
    "detmir": ("detmir", "Детский мир", ChannelType.WEBSITE),
    "аскона": ("askona", "Askona", ChannelType.WEBSITE),
    "askona": ("askona", "Askona", ChannelType.WEBSITE),
    "зоомаркет": ("zoomarket", "ZooMarket", ChannelType.WEBSITE),
    "zoomarket": ("zoomarket", "ZooMarket", ChannelType.WEBSITE),
    "планета электроники": ("planeta", "Планета Электроники", ChannelType.WEBSITE),
    "planeta": ("planeta", "Планета Электроники", ChannelType.WEBSITE),
    "kimex": ("kimex", "KIMEX", ChannelType.WEBSITE),
    "кимекс": ("kimex", "KIMEX", ChannelType.WEBSITE),
    "еврофарма": ("europharma", "Europharma", ChannelType.WEBSITE),
    "europharma": ("europharma", "Europharma", ChannelType.WEBSITE),
    "французский дом": ("french_house", "Французский Дом", ChannelType.WEBSITE),
    "french_house": ("french_house", "Французский Дом", ChannelType.WEBSITE),
}

# Inverted mapping from config.SHOP_KEYS
DISPLAY_TO_SHOP_KEY = {disp.lower(): k for k, disp in SHOP_KEYS.items()}


def resolve_seller_and_channel_meta(shop_name: str, shop_key: Optional[str] = None) -> Tuple[str, str, str]:
    """
    Given a legacy shop name or system shop key,
    returns (seller_slug, seller_display_name, channel_type).
    Guarantees that each merchant receives a distinct, non-empty slug.
    """
    cleaned_name = (shop_name or "").strip()
    cleaned_key = (shop_key or "").strip().lower()

    # 1. Exact match by system shop_key
    if cleaned_key and cleaned_key in SHOP_KEYS:
        disp = SHOP_KEYS[cleaned_key]
        ctype = ChannelType.DIRECT if cleaned_key == "fourmobile" else (
            ChannelType.KASPI if cleaned_key == "kaspi" else (
                ChannelType.FORTE if cleaned_key == "fortemarket" else (
                    ChannelType.HALYK if cleaned_key == "halyk" else ChannelType.WEBSITE
                )
            )
        )
        return cleaned_key, disp, ctype

    # 2. Check if shop_name itself is a known system key
    lower_name = cleaned_name.lower()
    if lower_name in SHOP_KEYS:
        return resolve_seller_and_channel_meta(SHOP_KEYS[lower_name], shop_key=lower_name)

    # 3. Check exact display name match from SHOP_KEYS
    if lower_name in DISPLAY_TO_SHOP_KEY:
        k = DISPLAY_TO_SHOP_KEY[lower_name]
        return resolve_seller_and_channel_meta(SHOP_KEYS[k], shop_key=k)

    # 4. Известные написания магазина — точным совпадением, а не вхождением подстроки.
    #
    # Раньше здесь искалось вхождение, и посторонние организации приклеивались к сетям по
    # совпадению букв: «Форте Банк» -> fortemarket, «Мир Каспия» и «Каспийский Берег» -> kaspi,
    # «Технодом Партнёр» -> technodom. Этап 2 плана это прямо запрещает: нельзя объединять
    # продавцов только потому, что названия похожи.
    #
    # Все 37 подключённых сетей определяются точно — по ключу реестра (шаг 1), по имени-ключу
    # (шаг 2) или по отображаемому имени (шаг 3); эта таблица добавляет им альтернативные
    # написания. Незнакомое название уходит на транслитерацию и становится отдельным продавцом:
    # если это действительно та же сеть, их сведёт SellerMatcher по сильным ключам, а не догадка
    # по буквам.
    exact = KNOWN_SELLER_MAPPINGS.get(lower_name)
    if exact:
        return exact

    # 5. Fallback: Transliterate and normalize safely
    slug = transliterate_to_slug(cleaned_name)
    if not slug:
        # Isolated deterministic hash per distinct shop name (never merge to common unknown_seller)
        # Без префикса: идентификатор собирается как f"seller_{slug}", иначе выходит seller_seller_<hash>
        hash_suffix = hashlib.sha256(cleaned_name.encode("utf-8")).hexdigest()[:8]
        slug = f"unnamed_{hash_suffix}"

    return slug, cleaned_name or "Неизвестный продавец", ChannelType.WEBSITE


def prepare_legacy_offer_v2(
    product: Dict[str, Any],
    canonical_key: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Значения v2 для товара legacy без обращения к базе: продавец, канал, канонический товар
    и предложение. None — цена вне допустимого диапазона, записывать нечего.

    Считается до захвата блокировки записи: разбор названия и адреса — чистый Python, а под
    блокировкой каждый шаг ждёт GIL наравне с потоками обхода (6.5.1).
    """
    from model_matching import extract_canonical_key

    pid = str(product["id"])
    shop_name = product.get("shop", "Белый Ветер")
    city = product.get("city", "Казахстан")
    title = product["title"]
    category = product.get("category", "")
    url = product.get("url") or ""
    image_url = product.get("image_url") or ""
    description = product.get("description") or ""

    try:
        price = float(product.get("price") or 0.0)
    except (TypeError, ValueError):
        price = 0.0

    if price <= 0 or price > 10_000_000:
        return None

    old_price_val = product.get("old_price_on_site") or product.get("old_price")
    try:
        old_price = float(old_price_val) if old_price_val else None
    except (TypeError, ValueError):
        old_price = None

    if old_price is not None and (old_price <= price or old_price > 10_000_000):
        old_price = None

    canonical_key = canonical_key or product.get("canonical_key") or extract_canonical_key(title)
    if not canonical_key:
        norm_title = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ]+", "_", title.lower()).strip("_")
        canonical_key = f"norm:{category}:{norm_title}" if category else f"norm:{norm_title}"

    # 1. Resolve Seller & 2. Resolve Channel
    seller_slug, seller_name, channel_type = resolve_seller_and_channel_meta(
        shop_name, shop_key=product.get("shop_key")
    )
    seller_id = f"seller_{seller_slug}"
    channel_id = f"chan_{seller_slug}_{channel_type}"

    norm_dom = None
    if channel_type in (ChannelType.WEBSITE, ChannelType.DIRECT) and url:
        from domain.seller_identity import normalize_domain
        norm_dom = normalize_domain(url)

    ident_id = None
    if norm_dom:
        # Идентификатор строится из самого отпечатка, а не только из продавца: иначе второй домен
        # того же продавца конфликтует по первичному ключу, предложение ON CONFLICT ниже его не
        # покрывает, и товар вообще не попадает в v2. Тот же приём уже применён в merge_sellers.
        ident_digest = hashlib.sha256(f"{seller_id}:domain:{norm_dom}".encode("utf-8")).hexdigest()[:16]
        ident_id = f"ident_{ident_digest}"

    # 3. Canonical Product
    hash_id = hashlib.sha256(canonical_key.encode("utf-8")).hexdigest()[:16]

    return {
        "seller_id": seller_id,
        "seller_slug": seller_slug,
        "seller_name": seller_name,
        "domain": norm_dom,
        "ident_id": ident_id,
        "channel_id": channel_id,
        "channel_type": channel_type,
        "channel_name": f"{seller_name} ({channel_type})",
        "product_id": f"prod_{hash_id}",
        "canonical_key": canonical_key,
        "title": title,
        "category": category,
        "description": description,
        "image_url": image_url,
        # 4. Offer: устойчивый идентификатор из идентификатора товара legacy
        "offer_id": f"off_{pid}",
        "external_sku": pid,
        "url": url,
        "price": price,
        "old_price": old_price,
        "condition": Condition.normalize(product.get("condition"), default=Condition.NEW),
        "availability": Availability.normalize(product.get("availability"), default=Availability.IN_STOCK),
        "city": city,
        "published_at": product.get("published_at"),
    }


def _json_param(rows) -> str:
    return json.dumps(rows, ensure_ascii=False)


def write_legacy_offers_v2(
    conn: sqlite3.Connection,
    items: List[Dict[str, Any]],
    timestamp: str,
    seen_sellers: Optional[set] = None,
) -> None:
    """Пишет подготовленные prepare_legacy_offer_v2 предложения в таблицы v2 наборными операторами.

    Порядок и смысл записи те же, что у прежней записи по одному товару: продавцы, отпечатки,
    каналы, канонические товары, предложения, история. Каждый оператор берёт все строки порции
    из одного JSON-параметра (json_each), поэтому на порцию приходится несколько вызовов SQLite,
    а не ~6 на товар: каждый вызов отпускает GIL, и под нагрузкой обхода возврат GIL писателю
    растягивал удержание блокировки записи до минуты (6.5.1).

    Дробные значения обратно из базы через JSON не читаются: SQLite 3.46 выводит REAL в JSON
    с 15 значащими цифрами. Цены (до 10 млн, не больше 15 значащих цифр) переносятся без потерь.
    Должно выполняться внутри открытой транзакции.
    """
    if not items:
        return

    # 1–2. Продавцы и каналы — по разу на ключ (продавец, домен, канал), в порядке первого появления
    sellers: List[Dict[str, Any]] = []
    for it in items:
        key = (it["seller_id"], it["domain"], it["channel_id"])
        if seen_sellers is not None and key in seen_sellers:
            continue
        if seen_sellers is not None:
            seen_sellers.add(key)
        elif any((s["seller_id"], s["domain"], s["channel_id"]) == key for s in sellers):
            continue
        sellers.append(it)

    if sellers:
        # Строка без домена домен не трогает: COALESCE оставляет прежний, а вставка — NULL
        conn.execute(
            """
            INSERT INTO sellers (id, slug, name, domain, is_active, created_at, updated_at)
            SELECT json_extract(value, '$.seller_id'), json_extract(value, '$.seller_slug'),
                   json_extract(value, '$.seller_name'), json_extract(value, '$.domain'), 1, ?1, ?1
            FROM json_each(?2) WHERE true
            ON CONFLICT(id) DO UPDATE SET
                domain = COALESCE(sellers.domain, excluded.domain),
                updated_at = excluded.updated_at
            """,
            (timestamp, _json_param(sellers)),
        )
        idents = [s for s in sellers if s["domain"]]
        if idents:
            conn.execute(
                """
                INSERT INTO seller_identities (id, seller_id, identity_type, identity_value, confidence, source, created_at)
                SELECT json_extract(value, '$.ident_id'), json_extract(value, '$.seller_id'), 'domain',
                       json_extract(value, '$.domain'), 1.0, 'product_url', ?1
                FROM json_each(?2) WHERE true
                ON CONFLICT(identity_type, identity_value, seller_id) DO NOTHING
                """,
                (timestamp, _json_param(idents)),
            )
        conn.execute(
            """
            INSERT INTO channels (id, seller_id, channel_type, name, is_active, created_at)
            SELECT json_extract(value, '$.channel_id'), json_extract(value, '$.seller_id'),
                   json_extract(value, '$.channel_type'), json_extract(value, '$.channel_name'), 1, ?1
            FROM json_each(?2) WHERE true
            ON CONFLICT(id) DO NOTHING
            """,
            (timestamp, _json_param(sellers)),
        )

    # 3. Канонический товар. Название и категорию задаёт одно предложение — первое записавшее
    # (title_offer_id): предложения разных магазинов делят канонический товар, и прежняя запись
    # переписывала его название чужим при каждом обходе, а с ним и полнотекстовый индекс (6.5.1).
    conn.execute(
        """
        INSERT INTO canonical_products (
            id, canonical_key, title, category, description, image_url, title_offer_id, created_at, updated_at
        )
        SELECT json_extract(value, '$.product_id'), json_extract(value, '$.canonical_key'),
               json_extract(value, '$.title'), json_extract(value, '$.category'),
               json_extract(value, '$.description'), json_extract(value, '$.image_url'),
               json_extract(value, '$.offer_id'), ?1, ?1
        FROM json_each(?2) WHERE true
        ON CONFLICT(id) DO UPDATE SET
            title = CASE WHEN canonical_products.title_offer_id IS NULL
                           OR canonical_products.title_offer_id = excluded.title_offer_id
                         THEN excluded.title ELSE canonical_products.title END,
            category = CASE WHEN canonical_products.title_offer_id IS NULL
                              OR canonical_products.title_offer_id = excluded.title_offer_id
                            THEN excluded.category ELSE canonical_products.category END,
            title_offer_id = COALESCE(canonical_products.title_offer_id, excluded.title_offer_id),
            description = CASE WHEN excluded.description != '' THEN excluded.description ELSE canonical_products.description END,
            image_url = CASE WHEN excluded.image_url != '' THEN excluded.image_url ELSE canonical_products.image_url END,
            updated_at = excluded.updated_at
        """,
        (timestamp, _json_param(items)),
    )

    # 4. Прежние значения предложений — одним чтением: json_group_array отдаёт одну строку
    offer_ids = list(dict.fromkeys(it["offer_id"] for it in items))
    row = conn.execute(
        """
        SELECT json_group_array(json_array(id, price, old_price, product_id, seller_id, channel_id, condition, city))
        FROM offers WHERE id IN (SELECT value FROM json_each(?))
        """,
        (_json_param(offer_ids),),
    ).fetchone()
    state: Dict[str, Tuple] = {r[0]: tuple(r[1:]) for r in json.loads(row[0] or "[]")}

    # 5. Price History: при первой точке, смене цены или смене измерений линии (товар, продавец,
    # канал, состояние, город). Измерения пишутся в саму точку: история не пересчитывается по
    # текущему предложению (V02). Повтор предложения в порции сравнивается с предыдущей записью.
    history: List[Dict[str, Any]] = []
    for it in items:
        dims = (it["product_id"], it["seller_id"], it["channel_id"], it["condition"], it["city"])
        existing = state.get(it["offer_id"])
        if (existing is None or existing[0] != it["price"] or existing[1] != it["old_price"]
                or tuple(existing[2:7]) != dims):
            history.append(it)
        state[it["offer_id"]] = (it["price"], it["old_price"], *dims)

    conn.execute(
        """
        INSERT INTO offers (
            id, product_id, seller_id, channel_id, external_sku,
            url, image_url, price, old_price, currency, condition,
            availability, city, published_at, observed_at, is_active,
            created_at, updated_at
        )
        SELECT json_extract(value, '$.offer_id'), json_extract(value, '$.product_id'),
               json_extract(value, '$.seller_id'), json_extract(value, '$.channel_id'),
               json_extract(value, '$.external_sku'), json_extract(value, '$.url'),
               json_extract(value, '$.image_url'), json_extract(value, '$.price'),
               json_extract(value, '$.old_price'), 'KZT', json_extract(value, '$.condition'),
               json_extract(value, '$.availability'), json_extract(value, '$.city'),
               json_extract(value, '$.published_at'), ?1, 1, ?1, ?1
        FROM json_each(?2) WHERE true
        ON CONFLICT(id) DO UPDATE SET
            product_id = excluded.product_id,
            seller_id = excluded.seller_id,
            channel_id = excluded.channel_id,
            url = excluded.url,
            image_url = CASE WHEN excluded.image_url != '' THEN excluded.image_url ELSE offers.image_url END,
            price = excluded.price,
            old_price = excluded.old_price,
            condition = excluded.condition,
            availability = excluded.availability,
            city = excluded.city,
            observed_at = excluded.observed_at,
            is_active = 1,
            updated_at = excluded.updated_at
        """,
        (timestamp, _json_param(items)),
    )

    if history:
        conn.execute(
            """
            INSERT OR REPLACE INTO offer_price_history
                (offer_id, price, old_price, observed_at, product_id, seller_id, channel_id, condition, city)
            SELECT json_extract(value, '$.offer_id'), json_extract(value, '$.price'),
                   json_extract(value, '$.old_price'), ?1, json_extract(value, '$.product_id'),
                   json_extract(value, '$.seller_id'), json_extract(value, '$.channel_id'),
                   json_extract(value, '$.condition'), json_extract(value, '$.city')
            FROM json_each(?2)
            """,
            (timestamp, _json_param(history)),
        )


def sync_legacy_product_to_v2(
    conn: sqlite3.Connection,
    product: Dict[str, Any],
    now: Optional[str] = None,
    canonical_key: Optional[str] = None,
    seen_sellers: Optional[set] = None,
) -> bool:
    """
    Saves or updates a legacy product dict into the v2 tables:
    sellers, channels, canonical_products, offers, offer_price_history.
    Must be executed within an active transaction.
    """
    item = prepare_legacy_offer_v2(product, canonical_key=canonical_key)
    if item is None:
        return False
    write_legacy_offers_v2(conn, [item], now or utc_now_iso(), seen_sellers=seen_sellers)
    return True
