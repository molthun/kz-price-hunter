import asyncio
import unittest
from tests import test_support  # noqa: F401
import auth
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


if __name__ == "__main__":
    unittest.main()
