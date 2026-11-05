from tests import test_support  # noqa: F401
import unittest
"""Тесты для domain/channel_scheduler.py."""
import asyncio
import json
import sqlite3
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

from domain.channel_scheduler import (
    is_marketplace_channel,
    parse_iso_datetime,
    get_channel_sync_schedule,
    get_due_channel_ids,
    run_channel_sync_batch,
    DEFAULT_SYNC_INTERVAL_HOURS,
)


def _init_test_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE sellers (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE channels (
            id TEXT PRIMARY KEY,
            seller_id TEXT NOT NULL,
            channel_type TEXT NOT NULL,
            name TEXT NOT NULL,
            external_store_id TEXT,
            base_url TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            metadata_json TEXT,
            created_at TEXT NOT NULL DEFAULT '2026-01-01'
        )
    """)
    return conn


class ChannelSchedulerTests(unittest.TestCase):
    def test_is_marketplace_channel(self):
        assert is_marketplace_channel("marketplace", "https://kaspi.kz/shop/m/123/products/")
        assert is_marketplace_channel("kaspi", None)
        assert is_marketplace_channel("halyk", None)
        assert is_marketplace_channel("forte", None)
        assert is_marketplace_channel("website", "https://market.forte.kz/merchant/999")
        assert not is_marketplace_channel("website", "https://technodom.kz")
        assert not is_marketplace_channel("telegram", "https://t.me/shop")

    def test_parse_iso_datetime(self):
        dt = parse_iso_datetime("2026-11-03T18:00:00Z")
        assert dt is not None
        assert dt.year == 2026
        assert dt.tzinfo is not None

        dt2 = parse_iso_datetime("invalid")
        assert dt2 is None
        assert parse_iso_datetime(None) is None

    def test_get_channel_sync_schedule_due_calculation(self):
        conn = _init_test_db()
        conn.execute("INSERT INTO sellers VALUES ('sel_1', 'Seller 1')")

        now = datetime.now(timezone.utc)
        fresh_sync = (now - timedelta(hours=1)).isoformat()
        stale_sync = (now - timedelta(hours=14)).isoformat()

        # Канал 1: никогда не синхронизировался (is_due = True)
        conn.execute("""
            INSERT INTO channels (id, seller_id, channel_type, name, base_url, is_active, metadata_json)
            VALUES ('ch_never', 'sel_1', 'marketplace', 'Kaspi Shop', 'https://kaspi.kz/shop/m/1/', 1, '{}')
        """)

        # Канал 2: свежий (is_due = False)
        conn.execute("""
            INSERT INTO channels (id, seller_id, channel_type, name, base_url, is_active, metadata_json)
            VALUES ('ch_fresh', 'sel_1', 'marketplace', 'Kaspi Fresh', 'https://kaspi.kz/shop/m/2/', 1, ?)
        """, (json.dumps({"last_sync_at": fresh_sync, "sync_interval_hours": 12}),))

        # Канал 3: устаревший (is_due = True)
        conn.execute("""
            INSERT INTO channels (id, seller_id, channel_type, name, base_url, is_active, metadata_json)
            VALUES ('ch_stale', 'sel_1', 'forte', 'Forte Stale', 'https://forte.kz/merchant/3', 1, ?)
        """, (json.dumps({"last_sync_at": stale_sync, "sync_interval_hours": 12}),))

        schedule = get_channel_sync_schedule(conn, default_interval_hours=12)
        assert len(schedule) == 3

        # Проверяем флаги
        by_id = {item["channel_id"]: item for item in schedule}
        assert by_id["ch_never"]["is_due"] is True
        assert by_id["ch_stale"]["is_due"] is True
        assert by_id["ch_fresh"]["is_due"] is False

        # В начале списка идут due каналы
        due_ids = get_due_channel_ids(conn, default_interval_hours=12)
        assert "ch_never" in due_ids
        assert "ch_stale" in due_ids
        assert "ch_fresh" not in due_ids

    def test_run_channel_sync_batch_success(self):
        conn = _init_test_db()
        conn.execute("INSERT INTO sellers VALUES ('sel_1', 'Seller 1')")
        conn.execute("""
            INSERT INTO channels (id, seller_id, channel_type, name, base_url, is_active, metadata_json)
            VALUES ('ch_test', 'sel_1', 'marketplace', 'Kaspi Shop', 'https://kaspi.kz/shop/m/1/', 1, '{}')
        """)

        with patch("domain.channel_scheduler.sync_channel_showcase", new_callable=AsyncMock) as mock_sync:
            mock_sync.return_value = {"ok": True, "scanned_count": 5, "offers_upserted": 5}

            result = asyncio.run(run_channel_sync_batch(
                conn_getter=lambda: conn,
                channel_ids=["ch_test"],
                delay_between_channels=0,
            ))

            assert result["ok"] is True
            assert result["processed"] == 1
            assert result["succeeded"] == 1
            assert result["failed"] == 0
            mock_sync.assert_called_once()

    def test_run_channel_sync_batch_with_error(self):
        conn = _init_test_db()
        conn.execute("INSERT INTO sellers VALUES ('sel_1', 'Seller 1')")
        conn.execute("""
            INSERT INTO channels (id, seller_id, channel_type, name, base_url, is_active, metadata_json)
            VALUES ('ch_err', 'sel_1', 'marketplace', 'Kaspi Shop', 'https://kaspi.kz/shop/m/1/', 1, '{}')
        """)

        with patch("domain.channel_scheduler.sync_channel_showcase", new_callable=AsyncMock) as mock_sync:
            mock_sync.side_effect = RuntimeError("Network timeout")

            result = asyncio.run(run_channel_sync_batch(
                conn_getter=lambda: conn,
                channel_ids=["ch_err"],
                delay_between_channels=0,
            ))

            assert result["ok"] is True
            assert result["processed"] == 1
            assert result["succeeded"] == 0
            assert result["failed"] == 1
            assert "Network timeout" in result["results"][0]["result"]["error"]


if __name__ == "__main__":
    unittest.main()
