"""M12: выгрузка своих данных, удаление аккаунта, срок хранения уведомлений. Без сети и рабочей БД."""
import json
import os
import tempfile
import time
import unittest

_TMP = tempfile.TemporaryDirectory()
os.environ["DATA_DIR"] = _TMP.name
for key in ("TELEGRAM_BOT_TOKEN", "GEMINI_API_KEY", "OPENAI_API_KEY", "PUBLIC_ORIGIN", "APP_URL", "TRUSTED_PROXIES", "ADMIN_TELEGRAM_IDS"):
    os.environ[key] = ""
os.environ["ALLOW_DEV_LOGIN"] = "0"

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import auth  # noqa: E402
from database import (create_session, get_connection, get_user, init_db, prune_notification_outbox,  # noqa: E402
                      record_alert, upsert_telegram_user)

UID = 93001


def _seed():
    init_db()
    with get_connection() as conn:
        for table in ("notification_outbox", "alerts", "sessions"):
            conn.execute(f"DELETE FROM {table}")
        conn.execute("DELETE FROM users WHERE id = ?", (UID,))
        conn.commit()
    upsert_telegram_user({"id": UID, "first_name": "Приват", "username": "privacy_user"})
    payload = {"product": {"id": "x@astana", "title": "Телевизор", "shop": "Sulpak", "city": "Астана",
                           "url": "https://www.sulpak.kz/g/x", "price": 100000},
               "anomaly": {"type": "SUPER_DISCOUNT"}}
    alert_id = record_alert("x@astana", "SUPER_DISCOUNT", 200000, 100000, 50, 100000, deliveries=[(UID, payload)])
    return alert_id


class SelfServiceHttpTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.alert_id = _seed()
        import web.server as server
        app = server.create_app()
        app.cleanup_ctx.clear()
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.origin = {"Origin": str(self.client.make_url("/")).rstrip("/")}

    async def asyncTearDown(self):
        await self.client.close()

    def login(self):
        self.client.session.cookie_jar.update_cookies({auth.SESSION_COOKIE: create_session(UID)})

    async def test_guest_cannot_export_or_delete(self):
        self.assertEqual((await self.client.get("/api/me/export")).status, 401)
        self.assertEqual((await self.client.post("/api/me/delete", json={"confirm": True}, headers=self.origin)).status, 401)

    async def test_export_contains_own_data_without_tokens(self):
        self.login()
        resp = await self.client.get("/api/me/export")
        self.assertEqual(resp.status, 200)
        self.assertIn("attachment", resp.headers["Content-Disposition"])
        data = json.loads(await resp.text())
        self.assertEqual(data["profile"]["id"], UID)
        self.assertEqual(data["profile"]["username"], "privacy_user")
        self.assertIn("settings", data)
        self.assertEqual(len(data["sessions"]), 1)
        self.assertNotIn("token_hash", json.dumps(data))
        self.assertEqual(data["notifications"][0]["product"]["title"], "Телевизор")

    async def test_delete_requires_confirmation_and_origin(self):
        self.login()
        self.assertEqual((await self.client.post("/api/me/delete", json={}, headers=self.origin)).status, 400)
        self.assertEqual((await self.client.post("/api/me/delete", json={"confirm": "yes"}, headers=self.origin)).status, 400)
        # Без Origin изменяющий запрос отклоняется ещё до обработчика
        raw = await self.client.session.post(self.client.make_url("/api/me/delete"), json={"confirm": True})
        self.assertEqual(raw.status, 403)
        self.assertIsNotNone(get_user(UID))

    async def test_delete_removes_account_sessions_and_notifications(self):
        self.login()
        resp = await self.client.post("/api/me/delete", json={"confirm": True}, headers=self.origin)
        self.assertEqual(resp.status, 200)
        self.assertEqual((await resp.json())["deleted"], {"sessions": 1, "notifications": 1, "users": 1})
        self.assertIsNone(get_user(UID))
        with get_connection() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM sessions WHERE user_id = ?", (UID,)).fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT count(*) FROM notification_outbox WHERE user_id = ?", (UID,)).fetchone()[0], 0)
            # Общий алерт остаётся в ленте
            self.assertEqual(conn.execute("SELECT count(*) FROM alerts WHERE id = ?", (self.alert_id,)).fetchone()[0], 1)
        me = await (await self.client.get("/api/me")).json()
        self.assertIsNone(me["user"])


class OutboxRetentionTest(unittest.TestCase):
    def test_prune_keeps_pending_and_recent(self):
        _seed()
        old = time.time() - 40 * 86400
        with get_connection() as conn:
            conn.execute("DELETE FROM notification_outbox")
            rows = [(1, UID, "{}", "sent", old), (2, UID, "{}", "pending", old), (3, UID, "{}", "sent", time.time()),
                    (4, UID, "{}", "failed", old)]
            conn.executemany("INSERT INTO notification_outbox (alert_id, user_id, payload, status, created_at) VALUES (?,?,?,?,?)", rows)
            conn.commit()
        self.assertEqual(prune_notification_outbox(30), 2)
        with get_connection() as conn:
            left = sorted(r[0] for r in conn.execute("SELECT alert_id FROM notification_outbox"))
        self.assertEqual(left, [2, 3])


if __name__ == "__main__":
    unittest.main()
