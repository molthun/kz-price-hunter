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
from domain.freshness_v2 import _parse_iso_utc
from domain.purchasability import offer_expired
from domain.social_media import MediaReport
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
from model_matching import _family_key, extract_canonical_key, sim_variant
import product_key
from repositories.channel_repo import ChannelRepository
from repositories.offer_repo import OfferRepository
from repositories.product_repo import ProductRepository
from repositories.seller_repo import SellerRepository

logger = logging.getLogger("kz_price_hunter.social_pipeline")

# Категорию товара соцсети определяет нормализация; подпись поста её не доказывает (FA69-07)
SOCIAL_DEFAULT_CATEGORY = "Без категории"
_EPOCH = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)


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
            # Совпадение handle на разных платформах не доказывает одного владельца: общего продавца задаёт
            # только подтверждённая связь — seller_id источника (FA69-05)
            platform = str(meta.get("platform") or "telegram").lower()
            slug = f"social_{platform}_{handle}".lower()
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

    def _title_lookup(self, sql: str, title: str) -> Optional[str]:
        """Значение ключа по названию; копия базы без таблиц кэша считается пустой."""
        title_hash = hashlib.sha256(title.strip().encode("utf-8")).hexdigest()
        try:
            row = self.conn.execute(sql, (title_hash,)).fetchone()
        except sqlite3.OperationalError:
            return None
        return row[0] if row else None

    def _derived_k2_key(self, title: str) -> Optional[str]:
        """k2-ключ существующего товара по разобранному названию.

        Только привязка к уже имеющейся карточке и только там, где название определяет исполнение целиком:
        вариант SIM или прочие отличия в названии оставляют товар отдельным.
        """
        legacy = extract_canonical_key(title) or ""
        family = _family_key(title) or ""
        if "|spec:" in legacy and legacy.split("|spec:", 1)[1].strip():
            return None
        parts = family.split(":")
        if len(parts) != 3 or sim_variant(title):
            return None
        brand, model, capacity = parts
        size = re.fullmatch(r"(\d+)gb", capacity)
        variants = [{"value": int(size.group(1)), "unit": "гб"}] if size else []
        key = product_key.build_key({"brand": brand, "model": model, "variants": variants}, title)
        return key if key and self.products.get_by_canonical_key(key) else None

    def _resolve_canonical_key(self, clean_title: str, raw_title: str) -> str:
        """Общая идентичность товара, без вызова модели (FA69-06).

        Порядок: решение оператора, сохранённый ключ названия (пустой — «не сравнивать»), карточка k2 с таким же
        названием, k2-карточка по разобранному названию, и только потом старый ключ или собственный хэш.
        """
        titles = [t for t in dict.fromkeys((clean_title, raw_title)) if t]
        for title in titles:
            pinned = self._title_lookup("SELECT canonical_key FROM title_key_pins WHERE title_hash = ?", title)
            if pinned:
                return pinned
        for title in titles:
            cached = self._title_lookup("SELECT canonical_key FROM title_canonical_cache WHERE title_hash = ?", title)
            if cached:
                return cached
            if cached == "":
                from database import no_identity_key
                return no_identity_key(title)
        for title in titles:
            rows = self.conn.execute(
                "SELECT DISTINCT canonical_key FROM canonical_products "
                "WHERE title = ? COLLATE NOCASE AND canonical_key LIKE 'k2:%' LIMIT 2",
                (title,),
            ).fetchall()
            if len(rows) == 1:
                return rows[0][0]
        for title in titles:
            derived = self._derived_k2_key(title)
            if derived:
                return derived
        legacy = extract_canonical_key(clean_title) or extract_canonical_key(raw_title)
        if legacy:
            return legacy
        # Детерминированный fallback-ключ для нестандартных названий
        clean = " ".join(raw_title.lower().split())
        return f"k2:social:{hashlib.sha256(clean.encode('utf-8')).hexdigest()[:12]}"

    def _resolve_canonical_product(self, raw_title: str, basis: Optional[Dict[str, Any]] = None) -> CanonicalProduct:
        """Сопоставляет название товара с каноническим продуктом или создаёт новый."""
        clean_title = self.PROMO_NOISE_RE.sub(" ", raw_title)
        clean_title = " ".join(clean_title.split()).strip(" -—:•|,!")
        canonical_key = self._resolve_canonical_key(clean_title, raw_title)
        title = clean_title or raw_title
        if basis:
            canonical_key = f"{canonical_key}|{basis['pack']}"
            title = f"{title} ({basis['label']})"

        return self.products.get_or_create_canonical(
            canonical_key=canonical_key,
            title=title,
            category=SOCIAL_DEFAULT_CATEGORY,
        )

    @staticmethod
    def _post_time(observation: ContentObservation) -> str:
        """Момент, к которому относится цена: публикация поста, а не его повторный сбор."""
        return observation.published_at or observation.observed_at or utc_now_iso()

    def _select_lines(
        self,
        candidates: List[ExtractedOfferCandidate],
    ) -> Dict[Tuple[str, str, str], Tuple[ExtractedOfferCandidate, CanonicalProduct, Optional[Dict[str, Any]]]]:
        """Кандидаты по строкам товар+город+состояние. Одинаковая строка внутри поста — меньшая цена."""
        lines: Dict[Tuple[str, str, str], Tuple[ExtractedOfferCandidate, CanonicalProduct, Optional[Dict[str, Any]]]] = {}
        for cand in candidates:
            if not cand.raw_title or cand.price is None or cand.price <= 0:
                continue
            basis = cand.price_basis()
            product = self._resolve_canonical_product(cand.raw_title, basis)
            key = (product.id, cand.city, cand.condition)
            if key not in lines or cand.price < lines[key][0].price:
                lines[key] = (cand, product, basis)
        return lines

    def process_candidates(
        self,
        observation: ContentObservation,
        candidates: List[ExtractedOfferCandidate],
    ) -> List[Offer]:
        """Превращает готовых кандидатов наблюдения в сохранённые предложения Offer.

        Одно предложение — канал + товар + город + состояние (FA69-03). Актуальна цена самого нового поста, порядок
        обработки на итог не влияет; более старый пост попадает только в историю (FA69-04). Повторный сбор того же
        поста не подтверждает цену: время наблюдения остаётся прежним.
        """
        if not candidates:
            return []

        seller_id, channel_id = self._ensure_seller_and_channel(observation)
        meta = observation.raw_metadata or {}
        post_id = str(observation.post_id or "")
        post_time = self._post_time(observation)
        saved_offers: List[Offer] = []

        for (product_id, city, condition), (cand, product, basis) in self._select_lines(candidates).items():
            existing = self.offers.find_social_line(channel_id, product_id, city, condition)
            is_exp = observation.is_expired() or offer_expired(observation.expires_at)
            availability = Availability.OUT_OF_STOCK if is_exp else cand.availability
            old_price = float(cand.old_price) if cand.old_price else None
            price = float(cand.price)

            previous_post = str((existing.raw_payload or {}).get("source_post_id") or "") if existing else ""
            same_post = bool(existing and post_id and previous_post == post_id)
            observed_at = post_time
            record_history = True
            self.offers.execute("SAVEPOINT social_offer")
            try:
                if existing and not same_post and self._is_older(post_time, post_id, existing, previous_post):
                    self.offers.add_price_observation(existing.id, price, old_price, post_time)
                    self.offers.execute("RELEASE SAVEPOINT social_offer")
                    continue
                if same_post:
                    unchanged = (existing.price == price and existing.old_price == old_price
                                 and existing.availability == availability)
                    if unchanged:
                        observed_at, record_history = existing.observed_at, False
                    else:
                        # Правка поста — новое измерение: время публикации о ней ничего не говорит
                        observed_at = max(
                            (existing.observed_at or "", observation.observed_at or post_time),
                            key=lambda v: _parse_iso_utc(v) or _EPOCH,
                        )

                url = str(meta.get("post_url") or "").strip()
                if not url and same_post:
                    url = existing.url
                if not url and observation.post_id:
                    handle = str(meta.get("handle") or "").strip().lstrip("@")
                    if handle and meta.get("platform") == "telegram":
                        url = f"https://t.me/{handle}/{observation.post_id}"

                sku_source = f"{product_id}|{city}|{condition}"
                payload: Dict[str, Any] = {"source_post_id": post_id}
                if basis:
                    payload["price_basis"] = {k: basis[k] for k in ("is_unit_price", "quantity", "unit", "pack")}
                offer = Offer(
                    id=existing.id if existing else f"off_soc_{uuid.uuid4().hex[:12]}",
                    product_id=product_id,
                    seller_id=seller_id,
                    channel_id=channel_id,
                    external_sku=f"SOC-{hashlib.sha256(sku_source.encode('utf-8')).hexdigest()[:20]}",
                    url=url,
                    price=price,
                    old_price=old_price,
                    condition=condition,
                    availability=availability,
                    city=city,
                    published_at=observation.published_at,
                    observed_at=observed_at,
                    expires_at=observation.expires_at,
                    is_active=True,
                    raw_payload=payload,
                )
                saved = self.offers.save_or_update(offer)
                if record_history:
                    self.offers.add_price_observation(saved.id, price, old_price, observed_at)
                self.offers.execute("RELEASE SAVEPOINT social_offer")
            except Exception:
                self.offers.execute("ROLLBACK TO SAVEPOINT social_offer")
                self.offers.execute("RELEASE SAVEPOINT social_offer")
                raise
            saved_offers.append(saved)

        return saved_offers

    @classmethod
    def _is_older(cls, post_time: str, post_id: str, existing: Offer, existing_post: str) -> bool:
        """Новее ли текущее предложение; при равном времени порядок задаёт номер поста."""
        current = _parse_iso_utc(existing.published_at or existing.observed_at) or _EPOCH
        return (_parse_iso_utc(post_time) or _EPOCH, post_id) < (current, existing_post)

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
        media_report: Optional[MediaReport] = None,
    ) -> List[Offer]:
        """Асинхронная обработка наблюдения с полноценным мультимодальным Vision по изображениям."""
        # 1. Текстовые кандидаты
        candidates = list(SocialContentExtractor.extract_candidates(observation))
        seen_keys = {
            (extract_canonical_key(c.raw_title) or c.raw_title.lower(), round(c.price, 2), None)
            for c in candidates
        }

        # 2. Мультимодальные Vision кандидаты (из картинки / постера / сторис)
        vision_candidates = await VisionExtractionEngine.extract_from_observation_async(
            observation,
            config=config,
            media_report=media_report,
        )

        for vc in vision_candidates:
            if not vc.publishable or not vc.price_determined or vc.price is None or vc.price <= 0:
                continue
            cand = vc.to_offer_candidate(expires_at=observation.expires_at)
            if not cand:
                continue

            c_key = extract_canonical_key(cand.raw_title) or cand.raw_title.lower()
            basis = cand.price_basis()
            key_pair = (c_key, round(cand.price, 2), basis["pack"] if basis else None)
            if key_pair in seen_keys:
                continue
            seen_keys.add(key_pair)
            candidates.append(cand)

        return self.process_candidates(observation, candidates)
