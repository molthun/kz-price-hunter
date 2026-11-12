from tests import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import asyncio
import threading
import time
import unittest
from unittest.mock import patch
import auth
import database
from database import init_db, get_connection, upsert_telegram_user, get_user
from database import create_bot_auth_token, approve_bot_auth_token, claim_bot_auth_token
import telegram_bot


class TestBotAuthLogin(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        init_db()

    def test_database_token_lifecycle(self):
        token = create_bot_auth_token(ttl_seconds=60)
        self.assertTrue(bool(token))
        # Initial status is pending (claim returns None)
        self.assertIsNone(claim_bot_auth_token(token))

        # Approve token with user_id 555001
        ok = approve_bot_auth_token(token, 555001)
        self.assertTrue(ok)

        # Claim returns user_id and consumes token
        claimed_uid = claim_bot_auth_token(token)
        self.assertEqual(claimed_uid, 555001)

        # Subsequent claim returns -1 (expired/consumed)
        self.assertEqual(claim_bot_auth_token(token), -1)

    def test_invalid_or_expired_token(self):
        self.assertEqual(claim_bot_auth_token("non_existent_token_123"), -1)
        self.assertFalse(approve_bot_auth_token("non_existent_token_123", 12345))

    async def test_telegram_bot_login_command(self):
        token = create_bot_auth_token(ttl_seconds=60)
        sent_messages = []

        async def fake_send(session, bot_token, chat_id, text, reply_markup=None, message_thread_id=None):
            sent_messages.append({"chat_id": chat_id, "text": text})
            return {"ok": True}

        orig_send = telegram_bot.send_tg_message
        telegram_bot.send_tg_message = fake_send
        try:
            from_user = {
                "id": 888777,
                "first_name": "Николай",
                "username": "nikolay_kz",
                "language_code": "ru"
            }
            await telegram_bot.handle_start_command(
                None, "fake_token", 888777,
                from_user=from_user,
                payload=f"login_{token}"
            )
            self.assertEqual(len(sent_messages), 1)
            self.assertIn("Вход выполнен, Николай!", sent_messages[0]["text"])

            # Token should now be approved
            uid = claim_bot_auth_token(token)
            self.assertEqual(uid, 888777)
        finally:
            telegram_bot.send_tg_message = orig_send

    async def test_telegram_bot_auth_payload_creates_token_login(self):
        sent_messages = []

        async def fake_send(session, bot_token, chat_id, text, reply_markup=None, message_thread_id=None):
            sent_messages.append({"chat_id": chat_id, "text": text, "markup": reply_markup})
            return {"ok": True}

        orig_send = telegram_bot.send_tg_message
        orig_app_url = telegram_bot.APP_URL
        telegram_bot.send_tg_message = fake_send
        telegram_bot.APP_URL = "https://shdev.molthun.ru"
        try:
            from_user = {
                "id": 999888,
                "first_name": "Тест",
                "username": "test_user",
                "language_code": "ru"
            }
            await telegram_bot.handle_start_command(
                None, "fake_token", 999888,
                from_user=from_user,
                payload="auth"
            )
            self.assertEqual(len(sent_messages), 1)
            self.assertIn("Привет, Тест!", sent_messages[0]["text"])
            markup = sent_messages[0]["markup"]
            self.assertIsNotNone(markup)
            self.assertIn("inline_keyboard", markup)
            buttons = markup["inline_keyboard"][0]
            self.assertEqual(buttons[0]["text"], "🔑 Войти на сайт")
            self.assertIn("/api/auth/token-login?token=", buttons[0]["url"])
        finally:
            telegram_bot.send_tg_message = orig_send
            telegram_bot.APP_URL = orig_app_url

    async def test_token_login_handler_success(self):
        from aiohttp.test_utils import make_mocked_request
        from aiohttp import web
        from web.server import token_login_handler

        upsert_telegram_user({
            "id": 777666,
            "first_name": "LoginTester",
            "username": "logintester"
        })
        token = create_bot_auth_token(ttl_seconds=60)
        approve_bot_auth_token(token, 777666)

        req = make_mocked_request("GET", f"/api/auth/token-login?token={token}&next=/profile")
        resp = await token_login_handler(req)
        self.assertEqual(resp.status, 302)
        self.assertEqual(resp.location, "/profile")
        self.assertIn("kzph_session", resp.cookies)
        self.assertTrue(bool(resp.cookies["kzph_session"].value))


class _BarrierConnection:
    """Соединение, которое придерживает DELETE погашения до прихода второго потока.

    Оба запроса успевают прочитать подтверждённую строку и только потом удаляют её —
    ровно та гонка, которую нашёл аудит 12.11 на двух настоящих соединениях SQLite."""

    def __init__(self, conn, barrier):
        self._conn = conn
        self._barrier = barrier

    def __enter__(self):
        self._conn.__enter__()
        return self

    def __exit__(self, *exc):
        return self._conn.__exit__(*exc)

    def execute(self, sql, *args):
        if sql.startswith("DELETE FROM bot_auth_tokens WHERE token"):
            try:
                self._barrier.wait(timeout=5)
            except threading.BrokenBarrierError:
                pass
        return self._conn.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _racing_connections():
    barrier = threading.Barrier(2)
    real = database.get_connection
    return patch.object(database, "get_connection", lambda: _BarrierConnection(real(), barrier))


class TestBotAuthTokenSingleUse(unittest.IsolatedAsyncioTestCase):
    """P1 аудита 12.11: одна подтверждённая ссылка даёт ровно одну сессию."""

    def setUp(self):
        init_db()
        self.uid = 777100
        upsert_telegram_user({"id": self.uid, "first_name": "Race"})
        self.token = create_bot_auth_token(ttl_seconds=60)
        self.assertTrue(approve_bot_auth_token(self.token, self.uid))

    def _session_count(self):
        with get_connection() as conn:
            return conn.execute("SELECT count(*) FROM sessions WHERE user_id = ?", (self.uid,)).fetchone()[0]

    def test_parallel_claims_redeem_once(self):
        results = []
        with _racing_connections():
            threads = [threading.Thread(target=lambda: results.append(claim_bot_auth_token(self.token)))
                       for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(10)
        self.assertEqual(sorted(results), [-1, self.uid])
        self.assertEqual(claim_bot_auth_token(self.token), -1)

    async def test_parallel_token_login_issues_one_session(self):
        from aiohttp.test_utils import make_mocked_request
        from web.server import token_login_handler

        async def call():
            req = make_mocked_request("GET", f"/api/auth/token-login?token={self.token}&next=/profile")
            try:
                return await token_login_handler(req)
            except Exception as exc:  # HTTPFound без cookie на проигравшем запросе
                return exc

        before = self._session_count()
        with _racing_connections():
            responses = await asyncio.gather(call(), call())
        self.assertTrue(all(r.status == 302 for r in responses))
        with_cookie = [r for r in responses if "kzph_session" in r.cookies]
        self.assertEqual(len(with_cookie), 1, "вторая сессия по той же ссылке")
        self.assertEqual(self._session_count() - before, 1)

    async def test_parallel_poll_and_token_login_issue_one_session(self):
        from aiohttp.test_utils import make_mocked_request
        from web.server import token_login_handler, bot_auth_poll_handler

        async def login():
            req = make_mocked_request("GET", f"/api/auth/token-login?token={self.token}")
            try:
                return await token_login_handler(req)
            except Exception as exc:
                return exc

        async def poll():
            req = make_mocked_request("GET", f"/api/auth/bot-poll?token={self.token}")
            return await bot_auth_poll_handler(req)

        before = self._session_count()
        with _racing_connections():
            login_resp, poll_resp = await asyncio.gather(login(), poll())
        winners = int("kzph_session" in login_resp.cookies) + int(poll_resp.status == 200)
        self.assertEqual(winners, 1)
        if poll_resp.status != 200:
            self.assertEqual(poll_resp.status, 410)
        self.assertEqual(self._session_count() - before, 1)

    def test_repeat_approval_cannot_rebind_token(self):
        self.assertTrue(approve_bot_auth_token(self.token, self.uid), "повтор тем же пользователем безопасен")
        self.assertFalse(approve_bot_auth_token(self.token, 777200), "чужой аккаунт не перехватывает ссылку")
        self.assertEqual(claim_bot_auth_token(self.token), self.uid)
        self.assertFalse(approve_bot_auth_token(self.token, self.uid), "погашенную ссылку не подтвердить")
        self.assertEqual(claim_bot_auth_token(self.token), -1)

    def test_expired_token_is_neither_approved_nor_redeemed(self):
        pending = create_bot_auth_token(ttl_seconds=60)
        with get_connection() as conn:
            conn.execute("UPDATE bot_auth_tokens SET expires_at = ? WHERE token IN (?, ?)",
                         (time.time() - 1, pending, self.token))
            conn.commit()
        self.assertFalse(approve_bot_auth_token(pending, self.uid))
        self.assertEqual(claim_bot_auth_token(pending), -1)
        self.assertEqual(claim_bot_auth_token(self.token), -1, "подтверждённая, но истёкшая ссылка")


if __name__ == "__main__":
    unittest.main()
