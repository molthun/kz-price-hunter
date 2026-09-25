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
from typing import Any, Dict, Optional, Tuple

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

    # 4. Check KNOWN_SELLER_MAPPINGS substrings
    for key, val in KNOWN_SELLER_MAPPINGS.items():
        if key in lower_name:
            return val

    # 5. Fallback: Transliterate and normalize safely
    slug = transliterate_to_slug(cleaned_name)
    if not slug:
        # Isolated deterministic hash per distinct shop name (never merge to common unknown_seller)
        # Без префикса: идентификатор собирается как f"seller_{slug}", иначе выходит seller_seller_<hash>
        hash_suffix = hashlib.sha256(cleaned_name.encode("utf-8")).hexdigest()[:8]
        slug = f"unnamed_{hash_suffix}"

    return slug, cleaned_name or "Неизвестный продавец", ChannelType.WEBSITE


def sync_legacy_product_to_v2(
    conn: sqlite3.Connection,
    product: Dict[str, Any],
    now: Optional[str] = None,
) -> bool:
    """
    Saves or updates a legacy product dict into the v2 tables:
    sellers, channels, canonical_products, offers, offer_price_history.
    Must be executed within an active transaction.
    """
    from model_matching import extract_canonical_key

    timestamp = now or utc_now_iso()
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
        return False

    old_price_val = product.get("old_price_on_site") or product.get("old_price")
    try:
        old_price = float(old_price_val) if old_price_val else None
    except (TypeError, ValueError):
        old_price = None

    if old_price is not None and (old_price <= price or old_price > 10_000_000):
        old_price = None

    canonical_key = product.get("canonical_key") or extract_canonical_key(title)
    if not canonical_key:
        norm_title = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ]+", "_", title.lower()).strip("_")
        canonical_key = f"norm:{category}:{norm_title}" if category else f"norm:{norm_title}"

    # 1. Resolve Seller
    seller_slug, seller_name, channel_type = resolve_seller_and_channel_meta(
        shop_name, shop_key=product.get("shop_key")
    )
    seller_id = f"seller_{seller_slug}"

    conn.execute(
        """
        INSERT INTO sellers (id, slug, name, is_active, created_at, updated_at)
        VALUES (?, ?, ?, 1, ?, ?)
        ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at
        """,
        (seller_id, seller_slug, seller_name, timestamp, timestamp),
    )

    # 2. Resolve Channel
    channel_id = f"chan_{seller_slug}_{channel_type}"
    conn.execute(
        """
        INSERT INTO channels (id, seller_id, channel_type, name, is_active, created_at)
        VALUES (?, ?, ?, ?, 1, ?)
        ON CONFLICT(id) DO NOTHING
        """,
        (channel_id, seller_id, channel_type, f"{seller_name} ({channel_type})", timestamp),
    )

    # 3. Canonical Product
    hash_id = hashlib.sha256(canonical_key.encode("utf-8")).hexdigest()[:16]
    product_id = f"prod_{hash_id}"

    conn.execute(
        """
        INSERT INTO canonical_products (
            id, canonical_key, title, category, description, image_url, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            title = excluded.title,
            category = excluded.category,
            description = CASE WHEN excluded.description != '' THEN excluded.description ELSE canonical_products.description END,
            image_url = CASE WHEN excluded.image_url != '' THEN excluded.image_url ELSE canonical_products.image_url END,
            updated_at = excluded.updated_at
        """,
        (product_id, canonical_key, title, category, description, image_url, timestamp, timestamp),
    )

    # 4. Offer
    # Stable offer ID derived from legacy product ID
    offer_id = f"off_{pid}"
    condition = Condition.normalize(product.get("condition"))
    availability = Availability.normalize(product.get("availability"))

    # Check previous price to see if history record is needed
    cur = conn.execute(
        "SELECT price, old_price FROM offers WHERE id = ?",
        (offer_id,),
    )
    existing_offer = cur.fetchone()

    conn.execute(
        """
        INSERT INTO offers (
            id, product_id, seller_id, channel_id, external_sku,
            url, image_url, price, old_price, currency, condition,
            availability, city, published_at, observed_at, is_active,
            created_at, updated_at
        ) VALUES (
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?, 'KZT', ?,
            ?, ?, ?, ?, 1,
            ?, ?
        )
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
        (
            offer_id,
            product_id,
            seller_id,
            channel_id,
            pid,
            url,
            image_url,
            price,
            old_price,
            condition,
            availability,
            city,
            product.get("published_at"),
            timestamp,
            timestamp,
            timestamp,
        ),
    )

    # 5. Price History: only if price or old_price changed (or first observation)
    if existing_offer is None or existing_offer[0] != price or existing_offer[1] != old_price:
        conn.execute(
            """
            INSERT OR REPLACE INTO offer_price_history (offer_id, price, old_price, observed_at)
            VALUES (?, ?, ?, ?)
            """,
            (offer_id, price, old_price, timestamp),
        )

    return True
