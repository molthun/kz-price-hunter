"""Репозиторий источников соцсетей (S2-E06, KZ Price Hunter 2.0).

Хранит настройку каналов Telegram и профилей Instagram, чтобы добавление источника
не требовало правки кода. Ошибки сбора сохраняются в last_status/last_error — молчащий
источник не должен выглядеть здоровым.
"""

from __future__ import annotations

from typing import List, Optional

from domain.models import utc_now_iso
from domain.social_connector import SocialSource, normalize_handle, source_id
from repositories.base import BaseRepository


class SocialSourceRepository(BaseRepository):
    """CRUD источников контента и отметки результатов сбора."""

    def save_source(self, source: SocialSource) -> SocialSource:
        now = utc_now_iso()
        self.execute(
            """
            INSERT INTO social_sources (
                id, platform, handle, url, seller_id, channel_id, enabled,
                last_fetch_at, last_status, last_error, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                platform = excluded.platform,
                handle = excluded.handle,
                url = excluded.url,
                seller_id = excluded.seller_id,
                channel_id = excluded.channel_id,
                enabled = excluded.enabled,
                last_fetch_at = excluded.last_fetch_at,
                last_status = excluded.last_status,
                last_error = excluded.last_error,
                updated_at = excluded.updated_at
            """,
            (
                source.id,
                source.platform,
                source.handle,
                source.url,
                source.seller_id,
                source.channel_id,
                1 if source.enabled else 0,
                source.last_fetch_at,
                source.last_status,
                source.last_error,
                source.created_at or now,
                now,
            ),
        )
        self.commit()
        source.updated_at = now
        return source

    def add_source(self, platform: str, handle: str, url: str = "",
                   seller_id: str = "", channel_id: str = "",
                   enabled: bool = True) -> SocialSource:
        """Добавляет или обновляет источник по паре (platform, handle)."""
        normalized = normalize_handle(platform, handle)
        source = SocialSource(
            id=source_id(platform, normalized),
            platform=platform,
            handle=normalized,
            url=url,
            seller_id=seller_id,
            channel_id=channel_id,
            enabled=enabled,
        )
        return self.save_source(source)

    def get_by_id(self, source_id_value: str) -> Optional[SocialSource]:
        row = self.fetchone("SELECT * FROM social_sources WHERE id = ?", (source_id_value,))
        return SocialSource.from_row(row) if row else None

    def get_by_handle(self, platform: str, handle: str) -> Optional[SocialSource]:
        normalized = normalize_handle(platform, handle)
        row = self.fetchone(
            "SELECT * FROM social_sources WHERE platform = ? AND handle = ?",
            (platform, normalized),
        )
        return SocialSource.from_row(row) if row else None

    def list_sources(self, enabled_only: bool = False) -> List[SocialSource]:
        sql = "SELECT * FROM social_sources"
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY created_at, id"
        return [SocialSource.from_row(row) for row in self.fetchall(sql)]

    def set_enabled(self, source_id_value: str, enabled: bool) -> Optional[SocialSource]:
        source = self.get_by_id(source_id_value)
        if source is None:
            return None
        source.enabled = bool(enabled)
        return self.save_source(source)

    def mark_fetch(self, source_id_value: str, status: str, error: str = "",
                   at: Optional[str] = None) -> Optional[SocialSource]:
        """Фиксирует результат сбора: время попытки, статус и текст ошибки."""
        source = self.get_by_id(source_id_value)
        if source is None:
            return None
        source.last_fetch_at = at or utc_now_iso()
        source.last_status = status
        source.last_error = (error or "")[:500]
        return self.save_source(source)
