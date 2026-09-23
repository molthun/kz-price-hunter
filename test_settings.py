"""P05 Settings UX: каждый параметр, старый settings.json, умолчания/сброс, ошибочные значения, права и изоляция."""
import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import config
import database
from config import DB_PATH, SYSTEM_DEFAULTS, USER_DEFAULTS


def changed_value(key, default):
    """Допустимое значение, отличное от умолчания, для любого параметра."""
    if isinstance(default, bool):
        return not default
    if key in config.ENUM_VALUES:
        return next(v for v in config.ENUM_VALUES[key] if v != default)
    if isinstance(default, (int, float)):
        low, high, _ = config.SETTING_RANGES.get(key, (0, 10**9, ""))
        value = min(high, max(low, default + 1 if default < high else default - 1))
        return type(default)(value)
    if isinstance(default, list):
        return ["тест", "проверка"]
    if isinstance(default, dict):
        if not default:                      # пустая карта по умолчанию (цены моделей): даём допустимую запись
            return {"gemini-2.5-flash": {"input": 0.3, "output": 2.5}}
        first = next(iter(default))
        return {first: not default[first]}
    return "changed"


class UserSettingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [patch.object(database, "DB_PATH", type(DB_PATH)(os.path.join(self.tmp.name, "prices.db"))),
                        patch("config.DATA_DIR", type(DB_PATH)(self.tmp.name))]
        for p in self.patches:
            p.start()
        database.init_db()
        database.upsert_telegram_user({"id": 501, "first_name": "A"})
        database.upsert_telegram_user({"id": 502, "first_name": "B"})

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def test_every_user_parameter_saves_and_reads(self):
        for key, default in USER_DEFAULTS.items():
            with self.subTest(key=key):
                value = changed_value(key, default)
                clean = config.validate_user_settings({key: value}, database.get_user(501)["settings"])
                database.save_user_settings(501, clean)
                saved = database.get_user(501)["settings"][key]
                if isinstance(default, dict):
                    self.assertEqual(saved[next(iter(value))], next(iter(value.values())))
                else:
                    self.assertEqual(saved, value)

    def test_invalid_values_rejected_with_clear_message(self):
        cases = {
            "price_glitch_drop_pct": 150, "arbitrage_min_drop_pct": 0, "min_item_price_kzt": -5,
            "max_item_price_kzt": 10**12, "min_savings_kzt": "много", "detect_zero_glitch": "yes",
            "telegram_notify_level": "LOUD", "junk_keywords": "чехол", "alert_shops": ["kaspi"],
        }
        for key, value in cases.items():
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    config.validate_user_settings({key: value})
        with self.assertRaisesRegex(ValueError, "Мин. скидка, %: допустимо от 1 до 99"):
            config.validate_user_settings({"price_glitch_drop_pct": 150})

    def test_min_not_above_max_including_saved_values(self):
        with self.assertRaisesRegex(ValueError, "Мин. цена"):
            config.validate_user_settings({"min_item_price_kzt": 500000, "max_item_price_kzt": 100000})
        # Уже сохранён максимум 100 000 — новый минимум 200 000 противоречит ему
        database.save_user_settings(501, config.validate_user_settings({"max_item_price_kzt": 100000}))
        with self.assertRaisesRegex(ValueError, "Мин. цена"):
            config.validate_user_settings({"min_item_price_kzt": 200000}, database.get_user(501)["settings"])
        config.validate_user_settings({"min_item_price_kzt": 90000}, database.get_user(501)["settings"])

    def test_unknown_and_system_keys_ignored_for_users(self):
        clean = config.validate_user_settings({"scan_interval_minutes": 5, "gemini_api_key": "x",
                                               "candidate_drop_pct": 1, "hacker": 1, "min_savings_kzt": 1000})
        self.assertEqual(clean, {"min_savings_kzt": 1000})

    def test_users_are_isolated(self):
        database.save_user_settings(501, config.validate_user_settings({"min_savings_kzt": 123456}))
        self.assertEqual(database.get_user(501)["settings"]["min_savings_kzt"], 123456)
        self.assertEqual(database.get_user(502)["settings"]["min_savings_kzt"], USER_DEFAULTS["min_savings_kzt"])

    def test_defaults_for_new_and_guest(self):
        self.assertEqual(config.merge_user_settings({}), USER_DEFAULTS)
        self.assertEqual(config.merge_user_settings(None)["telegram_notify_level"], "ALL")


class LegacySettingsFileTest(unittest.TestCase):
    """Старый однопользовательский settings.json: смысл сохраняется, лишнее игнорируется, недостающее — умолчания."""

    def test_old_file(self):
        legacy = {
            "scan_interval_minutes": 60, "enabled_shops": {"kaspi": False, "unknown_shop": True},
            "price_glitch_drop_pct": 70.0, "min_item_price_kzt": 50000, "junk_keywords": ["чехол"],
            "telegram_bot_token": "legacy-token-value", "removed_old_option": 1,
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "settings.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(legacy, f)
            with patch.object(config, "SETTINGS_FILE", type(DB_PATH)(path)):
                system = config.load_settings()
                user = config.legacy_user_settings()
                self.assertEqual(system["scan_interval_minutes"], 60)
                self.assertFalse(system["enabled_shops"]["kaspi"])
                self.assertNotIn("unknown_shop", system["enabled_shops"])
                self.assertTrue(system["enabled_shops"]["dns"])            # отсутствующий — по умолчанию
                self.assertNotIn("removed_old_option", system)
                self.assertEqual((user["price_glitch_drop_pct"], user["min_item_price_kzt"]), (70.0, 50000))
                self.assertEqual(user["junk_keywords"], ["чехол"])
                self.assertEqual(user["arbitrage_min_drop_pct"], USER_DEFAULTS["arbitrage_min_drop_pct"])


class SystemSettingsTest(unittest.TestCase):
    def test_every_system_parameter_validates(self):
        skip = {"gemini_model", "openai_model"}  # проверяются вместе с режимом manual
        for key, default in SYSTEM_DEFAULTS.items():
            if key in skip:
                continue
            with self.subTest(key=key):
                value = changed_value(key, default)
                if key == "ai_provider":
                    value = "gemini"
                if key in ("gemini_model_mode", "openai_model_mode"):
                    value = "manual"
                if key == "wave_mode":
                    value = "all"
                if key == "hot_categories":
                    value = [next(iter(config.MASTER_CATEGORIES))]
                if key == "scan_interval_minutes":
                    value = 60
                if key == "daily_digest_timezone":
                    value = "UTC"
                clean = config._validate_settings({key: value})
                self.assertIn(key, clean)

    def test_unknown_timezone_for_the_digest_is_refused(self):
        with self.assertRaises(ValueError):
            config._validate_settings({"daily_digest_timezone": "Nowhere/Nothing"})

    def test_system_ranges(self):
        for key, value in (("candidate_drop_pct", 0), ("candidate_arbitrage_drop_pct", 100),
                           ("check_interval_seconds", 5), ("scan_interval_minutes", 1),
                           ("daily_digest_hour", 24)):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    config._validate_settings({key: value})


class SettingsApiTest(unittest.IsolatedAsyncioTestCase):
    """Права: пользователь меняет только свои настройки, системные — только администратор; сброс."""

    async def test_permissions_isolation_and_reset(self):
        import auth
        import web.server as server
        from aiohttp.test_utils import TestServer
        from test_support import BrowserTestClient as TestClient
        database.init_db()
        database.upsert_telegram_user({"id": 8101, "first_name": "User"})
        database.upsert_telegram_user({"id": 8102, "first_name": "Other"})
        database.upsert_telegram_user({"id": 8103, "first_name": "Admin"})
        tokens = {uid: database.create_session(uid) for uid in (8101, 8102, 8103)}
        app = server.create_app()
        app.cleanup_ctx.clear()
        before_system = config.load_settings()
        with patch.object(auth, "ADMIN_TELEGRAM_IDS", {8103}):
            async with TestClient(TestServer(app)) as client:
                def login(uid):
                    client.session.cookie_jar.update_cookies({auth.SESSION_COOKIE: tokens[uid]})
                login(8101)
                res = await client.post("/api/me/settings", json={
                    "min_savings_kzt": 77777, "telegram_notify_enabled": True,
                    "scan_interval_minutes": 5, "candidate_drop_pct": 1})
                self.assertEqual(res.status, 200)
                self.assertEqual(config.load_settings(), before_system)       # системные не тронуты
                self.assertEqual((await client.post("/api/admin/config", json={"scan_interval_minutes": 5})).status, 403)
                res = await client.post("/api/me/settings", json={"price_glitch_drop_pct": 150})
                self.assertEqual(res.status, 400)
                self.assertIn("допустимо от 1 до 99", (await res.json())["message"])
                # Сброс: умолчания, но включённость Telegram сохраняется
                res = await client.post("/api/me/settings/reset", json={})
                body = await res.json()
                self.assertEqual(res.status, 200)
                self.assertEqual(body["settings"]["min_savings_kzt"], USER_DEFAULTS["min_savings_kzt"])
                self.assertTrue(body["settings"]["telegram_notify_enabled"])
                login(8102)
                self.assertEqual(database.get_user(8102)["settings"]["min_savings_kzt"], USER_DEFAULTS["min_savings_kzt"])
                client.session.cookie_jar.clear()
                self.assertEqual((await client.post("/api/me/settings/reset", json={})).status, 401)
                login(8103)
                self.assertEqual((await client.get("/api/admin/config")).status, 200)


if __name__ == "__main__":
    unittest.main()
