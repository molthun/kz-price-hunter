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

        chan_suffix = f"_{ext}" if ext else ""
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
