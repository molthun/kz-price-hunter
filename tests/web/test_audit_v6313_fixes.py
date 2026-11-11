"""Регрессии полного аудита 6.31.3 (B02–B04). Без сети, на временной БД.

Повторяет docs/audit_evidence/2026-11-11-full-v6313/reproduce.py и проверяет критерии
из FULL_AUDIT_2026-11-11_V6313_CODEX.md. B01 — в test_audit_v6310_fixes.py.
"""
from tests import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import datetime
import hashlib
import hmac
import json
import os
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import auth
import backup_health
import database
import notifier
from web import server

UID = 700000011
BOT_TOKEN = "synthetic-token-for-audit"


def _sign(uid):
    data = {"auth_date": str(int(time.time())), "user": json.dumps({"id": uid})}
    key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    message = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    data["hash"] = hmac.new(key, message.encode(), hashlib.sha256).hexdigest()
    return urlencode(data)


def _now(delta_hours=0):
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=delta_hours)).isoformat()


def _seed():
    database.init_db()
    with database.get_connection() as conn:
        conn.execute("DELETE FROM offers WHERE product_id = 'b02_target'")
        conn.execute("DELETE FROM canonical_products WHERE id = 'b02_target'")
        conn.execute("INSERT OR IGNORE INTO sellers(id, slug, name, created_at, updated_at) "
                     "VALUES ('b02_s', 'b02-s', 'Fixture Store', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)")
        conn.execute("INSERT OR IGNORE INTO channels(id, seller_id, channel_type, name, created_at) "
                     "VALUES ('b02_ch', 'b02_s', 'website', 'site', CURRENT_TIMESTAMP)")
        conn.execute("INSERT INTO canonical_products(id, canonical_key, title, category, created_at, updated_at) "
                     "VALUES ('b02_target', 'b02_target', 'Test Product', 'test', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)")
        conn.executemany(
            "INSERT INTO offers(id, product_id, seller_id, channel_id, external_sku, url, price, city, observed_at, "
            "expires_at, availability, created_at, updated_at) "
            "VALUES (?, 'b02_target', 'b02_s', 'b02_ch', ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            [
                ("b02_astana", "a", "https://example.invalid/astana", 120000, "Астана", _now(), None, "in_stock"),
                ("b02_astana_2@astana", "a2", "https://example.invalid/astana2", 130000, "Астана", _now(), None, "in_stock"),
                ("b02_almaty", "b", "https://example.invalid/almaty", 80000, "Алматы", _now(), None, "in_stock"),
                ("b02_expired", "c", "https://example.invalid/expired", 40000, "Алматы", _now(),
                 "2020-01-01T00:00:00Z", "out_of_stock"),
                ("b02_astana_gone", "d", "https://example.invalid/gone", 50000, "Астана", _now(), None, "out_of_stock"),
                ("b02_astana_stale", "e", "https://example.invalid/stale", 60000, "Астана", _now(-24 * 60), None, "in_stock"),
            ])
        conn.commit()
    database.upsert_telegram_user({"id": UID, "first_name": "Synthetic"})
    database.set_user_blocked(UID, False)


class ShareCardTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        _seed()
        app = web.Application(middlewares=[auth.auth_middleware])
        app.add_routes(server.routes)
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.origin = str(self.client.make_url("")).rstrip("/")
        self.patches = [
            patch.object(auth, "expected_origin", return_value=self.origin),
            patch.object(auth, "MAINTENANCE_DEV_MODE", False),
            patch.object(server, "get_bot_token", return_value=BOT_TOKEN),
        ]
        for p in self.patches:
            p.start()
        server.prepared_message_limiter._calls.clear()

    async def asyncTearDown(self):
        for p in self.patches:
            p.stop()
        await self.client.close()

    async def _share(self, **body):
        body.setdefault("init_data", _sign(UID))
        with patch.object(notifier, "save_prepared_inline_message", return_value="fixture") as send:
            r = await self.client.post("/api/telegram/prepared-message", headers={"Origin": self.origin}, json=body)
        return r, send

    async def test_b02_city_available_offer_not_expired_from_other_city(self):
        r, send = await self._share(canonical_product_id="b02_target", city="Астана")
        self.assertEqual(r.status, 200)
        _, title, price, shop, url, city = send.call_args.args[:6]
        self.assertEqual((price, url, city), (120000, "https://example.invalid/astana", "Астана"))

    async def test_b02_shared_offer_is_used_when_available(self):
        r, send = await self._share(canonical_product_id="b02_target", city="Астана", offer_id="b02_astana_2@astana")
        self.assertEqual(r.status, 200)
        self.assertEqual(send.call_args.args[2], 130000)
        # Недоступное или чужое по городу предложение подменить нельзя
        for bad in ("b02_expired", "b02_astana_gone", "b02_astana_stale", "b02_almaty"):
            r, send = await self._share(canonical_product_id="b02_target", city="Астана", offer_id=bad)
            self.assertEqual(r.status, 200)
            self.assertEqual(send.call_args.args[4], "https://example.invalid/astana", bad)

    async def test_offer_id_with_at_sign_and_bad_offer_id_do_not_break_share(self):
        """Владелец на стенде: ID предложений вида off_kaspi_1@astana давали 400 и голую ссылку."""
        r, send = await self._share(canonical_product_id="b02_target", city="Все", offer_id="b02_astana_2@astana")
        self.assertEqual(r.status, 200)
        self.assertEqual(send.call_args.args[2], 130000)
        r, send = await self._share(canonical_product_id="b02_target", city="Астана", offer_id="bad id/../x")
        self.assertEqual(r.status, 200)
        self.assertEqual(send.call_args.args[2], 120000)

    async def test_b02_other_city_and_no_available_offer(self):
        r, send = await self._share(canonical_product_id="b02_target", city="Алматы")
        self.assertEqual(send.call_args.args[2], 80000)
        r, send = await self._share(canonical_product_id="b02_target", city="Караганда")
        self.assertEqual(r.status, 404)
        send.assert_not_called()

    async def test_b03_blocked_user_cannot_share_by_init_data(self):
        database.set_user_blocked(UID, True)
        try:
            login = await self.client.post("/api/auth/telegram-webapp", headers={"Origin": self.origin},
                                           json={"init_data": _sign(UID)})
            self.assertEqual(login.status, 403)
            r, send = await self._share(canonical_product_id="b02_target", city="Астана")
            self.assertEqual(r.status, 403)
            send.assert_not_called()
        finally:
            database.set_user_blocked(UID, False)


class BackupV2ContractTest(unittest.TestCase):
    """B04: копия без каталога V2 или очереди записи не считается восстановимой."""

    def _copy_without(self, tables):
        tmp = tempfile.mkdtemp(prefix="kzph-b04-")
        path = os.path.join(tmp, "copy.db")
        database.init_db()
        src = sqlite3.connect(database.DB_PATH)
        dst = sqlite3.connect(path)
        src.backup(dst)
        src.close()
        dst.execute("PRAGMA foreign_keys = OFF")
        dst.execute("INSERT OR IGNORE INTO products(id, title, shop, url, current_price, first_seen_price, min_price, "
                    "max_price, is_active) VALUES ('b04_p', 'Товар', 'Shop', 'https://example.invalid/p', 100, 100, "
                    "100, 100, 1)")
        for t in tables:
            dst.execute(f"DROP TABLE IF EXISTS {t}")
        dst.commit()
        dst.close()
        return path

    def test_full_copy_restores(self):
        outcome = backup_health.restore_rehearsal(self._copy_without([]))
        self.assertTrue(outcome["restored"], outcome.get("error"))
        for label in ("поиск V2", "карточка товара V2", "очередь записи"):
            self.assertIs(outcome["queries"][label], True)

    def test_copy_without_v2_catalog_is_rejected(self):
        for tables in (["canonical_products_fts", "offers", "canonical_products"], ["product_queue_spool"]):
            outcome = backup_health.restore_rehearsal(self._copy_without(tables))
            self.assertFalse(outcome["restored"], tables)
            self.assertTrue(outcome["error"])


if __name__ == "__main__":
    unittest.main()
