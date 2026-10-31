"""
Social Offer Pipeline (KZ Price Hunter 2.0).

Превращает сырые наблюдения из соцсетей и мессенджеров (ContentObservation)
в опубликованные предложения (Offer) и канонические товары (CanonicalProduct).

Связывает:
  ContentObservation (Telegram / Instagram)
         ↓
  VisionExtractionEngine (мультимодальное чтение постеров/ценников)
         +
  SocialContentExtractor (текстовый детерминированный разбор)
         ↓
  ExtractedOfferCandidate (валидированные кандидаты)
         ↓
  CanonicalProduct resolution (extract_canonical_key -> ProductRepository)
         ↓
  Seller & Channel resolution (SellerRepository + ChannelRepository)
         ↓
  Offer persistence (OfferRepository -> offers table -> SearchCore index)
"""

from __future__ import annotations

import datetime
import hashlib
import logging
import re
import sqlite3
import uuid
from typing import Any, Dict, List, Optional, Tuple

from domain.models import (
    Availability,
    CanonicalProduct,
    Channel,
    ChannelType,
    Condition,
    Offer,
    Seller,
    utc_now_iso,
)
from domain.purchasability import offer_expired
from domain.social_content import (
    ContentObservation,
    ContentType,
    ExtractedOfferCandidate,
    SocialContentExtractor,
)
from domain.vision_extraction import (
    MIN_VISION_PUBLISH_CONFIDENCE,
    VisionCandidate,
    VisionExtractionEngine,
)
from model_matching import extract_canonical_key
from repositories.channel_repo import ChannelRepository
from repositories.offer_repo import OfferRepository
from repositories.product_repo import ProductRepository
from repositories.seller_repo import SellerRepository

logger = logging.getLogger("kz_price_hunter.social_pipeline")


class SocialOfferPipeline:
    """Конвейер сквозной обработки наблюдений из соцсетей в предложения каталога."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.products = ProductRepository(conn)
        self.sellers = SellerRepository(conn)
        self.channels = ChannelRepository(conn)
        self.offers = OfferRepository(conn)

    def _ensure_seller_and_channel(
        self,
        observation: ContentObservation,
    ) -> Tuple[str, str]:
        """Гарантирует существование активного продавца и канала для наблюдения."""
        seller_id = observation.seller_id
        channel_id = observation.channel_id
        meta = observation.raw_metadata or {}

        # 1. Продавец: если seller_id передан, но отсутствует в БД — создаём его с этим ID
        seller = self.sellers.get_by_id(seller_id) if seller_id else None
        if not seller:
            handle = str(meta.get("handle") or seller_id or "social_seller").strip().lstrip("@")
            slug = f"social_{handle}".lower()
            name = str(meta.get("seller_name") or meta.get("title") or f"@{handle}")
            if seller_id:
                seller = Seller(
                    id=seller_id,
                    slug=slug,
                    name=name,
                    is_active=True,
                )
                self.sellers.save_or_update(seller)
            else:
                seller = self.sellers.get_or_create(slug=slug, name=name)
                seller_id = seller.id

        # 2. Канал: если channel_id передан, но отсутствует — создаём его с этим ID
        channel = self.channels.get_by_id(channel_id) if channel_id else None
        if not channel:
            platform = str(meta.get("platform") or "telegram").lower()
            ch_type = ChannelType.INSTAGRAM if "instagram" in platform else ChannelType.TELEGRAM
            handle = str(meta.get("handle") or channel_id or "channel").strip().lstrip("@")
            base_url = f"https://t.me/{handle}" if ch_type == ChannelType.TELEGRAM else f"https://instagram.com/{handle}"
            if channel_id:
                channel = Channel(
                    id=channel_id,
                    seller_id=seller_id,
                    channel_type=ch_type,
                    name=f"{platform.title()}: @{handle}",
                    base_url=base_url,
                    is_active=True,
                )
                self.channels.save_or_update(channel)
            else:
                channel = self.channels.get_or_create(
                    seller_id=seller_id,
                    channel_type=ch_type,
                    name=f"{platform.title()}: @{handle}",
                    base_url=base_url,
                )
                channel_id = channel.id

        return seller_id, channel_id

    PROMO_NOISE_RE = re.compile(
        r"\b(?:скидк[аи]|акци[яи]|распродаж[аи]|sale|срочн(?:о|ая)|супер|шок|внимание|горячее|предложение)\b",
        re.IGNORECASE,
    )

    def _resolve_canonical_product(self, raw_title: str) -> CanonicalProduct:
        """Сопоставляет название товара с каноническим продуктом или создаёт новый."""
        clean_title = self.PROMO_NOISE_RE.sub(" ", raw_title)
        clean_title = " ".join(clean_title.split()).strip(" -—:•|,!")
        canonical_key = extract_canonical_key(clean_title) or extract_canonical_key(raw_title)
        if not canonical_key:
            # Детерминированный fallback-ключ для нестандартных названий
            clean = " ".join(raw_title.lower().split())
            key_hash = hashlib.sha256(clean.encode("utf-8")).hexdigest()[:12]
            canonical_key = f"k2:social:{key_hash}"

        return self.products.get_or_create_canonical(
            canonical_key=canonical_key,
            title=clean_title or raw_title,
            category="electronics",  # базовая категория, уточняется при нормализации
        )

    def process_candidates(
        self,
        observation: ContentObservation,
        candidates: List[ExtractedOfferCandidate],
    ) -> List[Offer]:
        """Превращает готовых кандидатов наблюдения в сохранённые предложения Offer."""
        if not candidates:
            return []

        seller_id, channel_id = self._ensure_seller_and_channel(observation)
        saved_offers: List[Offer] = []

        for cand in candidates:
            if not cand.raw_title or cand.price is None or cand.price <= 0:
                continue

            product = self._resolve_canonical_product(cand.raw_title)

            # Детерминированный SKU для оффера соцсети
            if observation.post_id:
                # Если в посте несколько товаров, добавляем хэш названия к post_id
                title_suffix = hashlib.sha256(cand.raw_title.lower().encode("utf-8")).hexdigest()[:6]
                external_sku = f"SOC-{seller_id[:8]}-{observation.post_id}-{title_suffix}"
            else:
                raw_slug = "".join(c if c.isalnum() else "-" for c in cand.raw_title[:24].strip()).upper()
                external_sku = f"SOC-{seller_id[:8]}-{raw_slug}"

            # Проверяем, существует ли уже такой оффер от этого продавца для этого товара
            existing_offer = self.offers.get_by_channel_sku(channel_id, external_sku)
            if not existing_offer:
                existing_offers = self.offers.get_active_offers_for_product(product.id)
                existing_offer = next(
                    (o for o in existing_offers if o.seller_id == seller_id and o.channel_id == channel_id),
                    None,
                )

            offer_id = existing_offer.id if existing_offer else f"off_soc_{uuid.uuid4().hex[:12]}"

            # Проверка срока годности: просроченное наблюдение переводит оффер в OUT_OF_STOCK
            is_exp = observation.is_expired() or offer_expired(observation.expires_at)
            availability = Availability.OUT_OF_STOCK if is_exp else cand.availability

            url = str(observation.raw_metadata.get("post_url") or existing_offer.url if existing_offer else "").strip()
            if not url and observation.post_id:
                ch_meta = observation.raw_metadata or {}
                handle = str(ch_meta.get("handle") or "").strip().lstrip("@")
                if handle and ch_meta.get("platform") == "telegram":
                    url = f"https://t.me/{handle}/{observation.post_id}"

            offer = Offer(
                id=offer_id,
                product_id=product.id,
                seller_id=seller_id,
                channel_id=channel_id,
                external_sku=external_sku,
                url=url,
                price=float(cand.price),
                old_price=float(cand.old_price) if cand.old_price else None,
                condition=cand.condition,
                availability=availability,
                city=cand.city,
                observed_at=observation.observed_at or utc_now_iso(),
                expires_at=observation.expires_at,
                is_active=True,
            )

            saved = self.offers.save_or_update(offer)
            saved_offers.append(saved)

        return saved_offers

    def process_observation_sync(
        self,
        observation: ContentObservation,
    ) -> List[Offer]:
        """Синхронная обработка наблюдения (текстовый парсинг + OCR разметка)."""
        text_candidates = SocialContentExtractor.extract_candidates(observation)
        meta = observation.raw_metadata or {}

        # Если есть OCR разметка — добавляем vision кандидатов
        if meta.get("vision_items") or meta.get("ocr_text") or meta.get("image_text"):
            vc_list = VisionExtractionEngine.extract_from_observation(observation)
            for vc in vc_list:
                cand = vc.to_offer_candidate(expires_at=observation.expires_at)
                if cand:
                    text_candidates.append(cand)

        return self.process_candidates(observation, text_candidates)

    async def process_observation_async(
        self,
        observation: ContentObservation,
        *,
        config: Optional[Dict[str, Any]] = None,
    ) -> List[Offer]:
        """Асинхронная обработка наблюдения с полноценным мультимодальным Vision по изображениям."""
        # 1. Текстовые кандидаты
        candidates = list(SocialContentExtractor.extract_candidates(observation))
        seen_keys = {
            (extract_canonical_key(c.raw_title) or c.raw_title.lower(), round(c.price, 2))
            for c in candidates
        }

        # 2. Мультимодальные Vision кандидаты (из картинки / постера / сторис)
        vision_candidates = await VisionExtractionEngine.extract_from_observation_async(
            observation,
            config=config,
        )

        for vc in vision_candidates:
            if not vc.publishable or not vc.price_determined or vc.price is None or vc.price <= 0:
                continue
            cand = vc.to_offer_candidate(expires_at=observation.expires_at)
            if not cand:
                continue

            c_key = extract_canonical_key(cand.raw_title) or cand.raw_title.lower()
            key_pair = (c_key, round(cand.price, 2))
            if key_pair in seen_keys:
                continue
            seen_keys.add(key_pair)
            candidates.append(cand)

        return self.process_candidates(observation, candidates)
