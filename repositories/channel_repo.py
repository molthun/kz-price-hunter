"""
Channel Repository for KZ Price Hunter 2.0.
"""

from __future__ import annotations

import json
from typing import List, Optional

from domain.models import Channel, utc_now_iso
from repositories.base import BaseRepository


class ChannelRepository(BaseRepository):
    """
    CRUD and lookup operations for merchant sales channels.
    """

    def save_or_update(self, channel: Channel) -> Channel:
        sql = """
        INSERT INTO channels (
            id, seller_id, channel_type, name, external_store_id,
            base_url, is_active, metadata_json, created_at
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        ON CONFLICT(id) DO UPDATE SET
            channel_type=excluded.channel_type,
            name=excluded.name,
            external_store_id=COALESCE(excluded.external_store_id, channels.external_store_id),
            base_url=COALESCE(excluded.base_url, channels.base_url),
            is_active=excluded.is_active,
            metadata_json=excluded.metadata_json
        """
        now = utc_now_iso()
        params = (
            channel.id,
            channel.seller_id,
            channel.channel_type,
            channel.name,
            channel.external_store_id,
            channel.base_url,
            1 if channel.is_active else 0,
            json.dumps(channel.metadata, ensure_ascii=False),
            channel.created_at or now,
        )
        self.execute(sql, params)
        return channel

    def get_by_id(self, channel_id: str) -> Optional[Channel]:
        row = self.fetchone("SELECT * FROM channels WHERE id = ?", (channel_id,))
        return Channel.from_row(row) if row else None

    def get_or_create(
        self,
        seller_id: str,
        channel_type: str,
        name: str,
        external_store_id: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> Channel:
        # Check by unique constraint
        sql = """
        SELECT * FROM channels
        WHERE seller_id = ? AND channel_type = ? AND COALESCE(external_store_id, '') = ?
        """
        ext = external_store_id or ""
        row = self.fetchone(sql, (seller_id, channel_type, ext))
        if row:
            return Channel.from_row(row)

        if ext:
            import hashlib
            import re
            clean_sfx = re.sub(r"[^a-zA-Z0-9_-]+", "_", ext).strip("_")
            if len(clean_sfx) > 24 or not clean_sfx:
                clean_sfx = hashlib.sha256(ext.encode()).hexdigest()[:10]
            chan_suffix = f"_{clean_sfx}"
        else:
            chan_suffix = ""

        clean_seller = seller_id.replace("seller_", "")
        channel_id = f"chan_{clean_seller}_{channel_type}{chan_suffix}"

        channel = Channel(
            id=channel_id,
            seller_id=seller_id,
            channel_type=channel_type,
            name=name,
            external_store_id=external_store_id,
            base_url=base_url,
            is_active=True,
        )
        return self.save_or_update(channel)

    def list_by_seller(self, seller_id: str, active_only: bool = True) -> List[Channel]:
        sql = "SELECT * FROM channels WHERE seller_id = ?"
        if active_only:
            sql += " AND is_active = 1"
        sql += " ORDER BY channel_type ASC"
        rows = self.fetchall(sql, (seller_id,))
        return [Channel.from_row(r) for r in rows]

    def update_channel(self, channel_id: str, updates: dict) -> Optional[Channel]:
        """Обновление параметров канала."""
        existing = self.get_by_id(channel_id)
        if not existing:
            return None
        if "name" in updates and updates["name"]:
            existing.name = str(updates["name"]).strip()
        if "base_url" in updates:
            existing.base_url = str(updates["base_url"]).strip() if updates["base_url"] else None
        if "external_store_id" in updates:
            existing.external_store_id = str(updates["external_store_id"]).strip() if updates["external_store_id"] else None
        if "is_active" in updates:
            existing.is_active = bool(updates["is_active"])
        return self.save_or_update(existing)

    def delete_channel(self, channel_id: str) -> bool:
        """Мягкое отключение канала (is_active = 0) с сохранением целостности истории."""
        ch = self.get_by_id(channel_id)
        if not ch:
            return False
        ch.is_active = False
        self.save_or_update(ch)
        return True
