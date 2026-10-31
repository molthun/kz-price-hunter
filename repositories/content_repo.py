"""
Repository for Social Content Observations (S2-E08, KZ Price Hunter 2.0).
Persists raw Telegram/Instagram posts and stories, keeps their TTL, and purges expired rows.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, List, Optional

from domain.models import utc_now_iso
from domain.social_content import (
    ContentObservation,
    ExtractedOfferCandidate,
    SocialContentExtractor,
)
from domain.vision_extraction import VisionCandidate, VisionExtractionEngine
from repositories.base import BaseRepository

logger = logging.getLogger("kz_price_hunter.repositories.content")


def normalize_iso_utc(ts: Optional[str]) -> Optional[str]:
    """Нормализует ISO-строку времени к UTC (YYYY-MM-DDTHH:MM:SS+00:00).

    A06: Обеспечивает согласованность лексикографического сравнения в SQL
    с хронологическим временем для любых временных зон и смещений.
    """
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt.isoformat()
    except Exception:
        return ts


class ContentObservationRepository(BaseRepository):
    """CRUD, TTL и выборки по сырому контенту соцсетей и мессенджеров."""

    def save_observation(self, observation: ContentObservation) -> ContentObservation:
        """Вставляет наблюдение или обновляет уже сохранённое с тем же id."""
        now = utc_now_iso()
        norm_expires = normalize_iso_utc(observation.expires_at)
        self.execute(
            """
            INSERT INTO content_observations (
                id, seller_id, channel_id, content_type, post_id, text,
                media_urls, published_at, observed_at, expires_at, raw_metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                seller_id = excluded.seller_id,
                channel_id = excluded.channel_id,
                content_type = excluded.content_type,
                post_id = excluded.post_id,
                text = excluded.text,
                media_urls = excluded.media_urls,
                published_at = excluded.published_at,
                observed_at = excluded.observed_at,
                expires_at = excluded.expires_at,
                raw_metadata = excluded.raw_metadata
            """,
            (
                observation.id,
                observation.seller_id,
                observation.channel_id,
                observation.content_type,
                observation.post_id,
                observation.text,
                json.dumps(observation.media_urls or [], ensure_ascii=False),
                observation.published_at,
                observation.observed_at or now,
                norm_expires,
                json.dumps(observation.raw_metadata or {}, ensure_ascii=False),
            ),
        )
        self.commit()
        return observation

    def get_by_id(self, observation_id: str) -> Optional[ContentObservation]:
        row = self.fetchone(
            "SELECT * FROM content_observations WHERE id = ?", (observation_id,))
        return ContentObservation.from_row(row) if row else None

    def find_by_post(self, channel_id: str, post_id: str) -> Optional[ContentObservation]:
        row = self.fetchone(
            "SELECT * FROM content_observations WHERE channel_id = ? AND post_id = ?",
            (channel_id, post_id),
        )
        return ContentObservation.from_row(row) if row else None

    def _with_ttl_filter(self, include_expired: bool, now_iso: Optional[str]) -> tuple:
        """Возвращает SQL-условие и параметр для фильтра по сроку жизни."""
        if include_expired:
            return "", ()
        ref = normalize_iso_utc(now_iso) or utc_now_iso()
        return " AND (expires_at IS NULL OR expires_at > ?)", (ref,)

    def find_by_seller(
        self,
        seller_id: str,
        limit: int = 50,
        include_expired: bool = False,
        now_iso: Optional[str] = None,
    ) -> List[ContentObservation]:
        ttl_sql, ttl_params = self._with_ttl_filter(include_expired, now_iso)
        rows = self.fetchall(
            "SELECT * FROM content_observations WHERE seller_id = ?" + ttl_sql +
            " ORDER BY observed_at DESC LIMIT ?",
            (seller_id, *ttl_params, limit),
        )
        return [ContentObservation.from_row(row) for row in rows]

    def find_by_channel(
        self,
        channel_id: str,
        limit: int = 50,
        include_expired: bool = False,
        now_iso: Optional[str] = None,
    ) -> List[ContentObservation]:
        ttl_sql, ttl_params = self._with_ttl_filter(include_expired, now_iso)
        rows = self.fetchall(
            "SELECT * FROM content_observations WHERE channel_id = ?" + ttl_sql +
            " ORDER BY observed_at DESC LIMIT ?",
            (channel_id, *ttl_params, limit),
        )
        return [ContentObservation.from_row(row) for row in rows]

    def purge_expired(self, now_iso: Optional[str] = None) -> int:
        """Удаляет наблюдения, срок жизни которых истёк. Возвращает число удалённых строк."""
        ref = normalize_iso_utc(now_iso) or utc_now_iso()
        cur = self.execute(
            "DELETE FROM content_observations "
            "WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (ref,),
        )
        self.commit()
        return cur.rowcount or 0

    @staticmethod
    def extract_vision_candidates(observation: ContentObservation) -> List[VisionCandidate]:
        """Извлекает кандидатов из медиа/OCR/vision-разметки наблюдения (S2-E10)."""
        return VisionExtractionEngine.extract_from_observation(observation)

    @staticmethod
    def extract_candidates(observation: ContentObservation) -> List[ExtractedOfferCandidate]:
        """Детерминированно извлекает кандидатов-предложения из текста и медиа наблюдения."""
        text_candidates = list(SocialContentExtractor.extract_candidates(observation))
        meta = observation.raw_metadata or {}
        has_vision_input = bool(
            meta.get("vision_items")
            or meta.get("ocr_text")
            or meta.get("image_text")
        )
        if not has_vision_input:
            return text_candidates

        seen = {(c.raw_title.strip().lower(), round(float(c.price), 2)) for c in text_candidates}
        vision_candidates = VisionExtractionEngine.extract_from_observation(observation)
        for vc in vision_candidates:
            if not vc.publishable or vc.price is None or vc.price <= 0:
                continue
            key = (vc.raw_title.strip().lower(), round(float(vc.price), 2))
            if key in seen:
                continue
            seen.add(key)
            text_candidates.append(
                ExtractedOfferCandidate(
                    raw_title=vc.raw_title,
                    price=float(vc.price),
                    old_price=float(vc.old_price) if vc.old_price else None,
                    condition=vc.condition,
                    city=vc.city,
                    availability=vc.availability,
                    confidence=float(vc.confidence),
                    expires_at=observation.expires_at,
                    raw_snippet=vc.raw_snippet or vc.raw_title,
                    is_unit_price=vc.is_unit_price,
                    quantity=vc.quantity,
                    unit=vc.unit,
                    size_or_volume=vc.size_or_volume,
                )
            )
        return text_candidates
