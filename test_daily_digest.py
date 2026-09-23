"""P13 Суточная сводка: границы суток, повторный расчёт без дублей, пустые и неполные сутки, пересказ."""
import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import asyncio
import datetime
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import ai_service
import daily_digest as digest
import database
from config import DB_PATH

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
DAY = "2026-09-22"


class DayBoundariesTest(unittest.TestCase):
    def test_utc_day_is_exact_and_half_open(self):
        start, end = digest.day_window(DAY, "UTC")
        self.assertEqual(start.isoformat(), "2026-09-22T00:00:00+00:00")
        self.assertEqual(end.isoformat(), "2026-09-23T00:00:00+00:00")
        self.assertTrue(digest.is_exact_utc_day(start, end))
        self.assertEqual(digest.day_keys(start, end), [DAY])

    def test_local_day_shifts_the_window_and_touches_two_utc_days(self):
        """Сутки Алматы начинаются раньше суток UTC — об этом нельзя умалчивать."""
        start, end = digest.day_window(DAY, "Asia/Almaty")
        self.assertEqual(start.isoformat(), "2026-09-21T19:00:00+00:00")
        self.assertEqual(end - start, datetime.timedelta(days=1))
        self.assertFalse(digest.is_exact_utc_day(start, end))
        self.assertEqual(digest.day_keys(start, end), ["2026-09-21", DAY])

    def test_unknown_timezone_falls_back_to_utc_instead_of_failing(self):
        self.assertEqual(digest.day_window(DAY, "Nowhere/Nothing"), digest.day_window(DAY, "UTC"))

    def test_previous_day_is_the_last_finished_one(self):
        self.assertEqual(digest.previous_day("UTC", NOW), DAY)
        self.assertEqual(digest.today("UTC", NOW), "2026-09-23")


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = type(DB_PATH)(self.tmp.name)
        self.patches = [patch.object(database, "DB_PATH", self.data_dir / "prices.db"),
                        patch("config.DATA_DIR", self.data_dir),
                        patch("config.SETTINGS_FILE", self.data_dir / "settings.json")]
        for p in self.patches:
            p.start()
        database.init_db()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def fill(self):
        """Сутки 2026-09-22: поиски, новый товар, снижение цены, уведомления, AI, обход и восстановление."""
        inside = datetime.datetime(2026, 9, 22, 10, 0, tzinfo=UTC)
        outside = datetime.datetime(2026, 9, 23, 10, 0, tzinfo=UTC)
        for moment, outcome in [(inside, "found"), (inside, "found"), (inside, "not_found"),
                                (outside, "found")]:
            database.record_search("ноутбук", "Алматы", "catalog", outcome, 3, now=moment)
        for moment, task in [(inside, "consultant"), (outside, "consultant")]:
            database.record_ai_usage(task, "user", "gemini", "gemini-2.5-flash", "ok",
                                     input_tokens=100, output_tokens=20, cost=0.5, now=moment)
        with database.get_connection() as conn:
            conn.execute("""INSERT INTO products (id, shop, title, category, url, current_price,
                            first_seen_price, old_price_on_site, min_price, max_price, created_at, updated_at)
                            VALUES ('p1', 'dns', 'Ноутбук', 'Ноутбуки', 'u', 90000, 100000, 0, 90000, 100000, ?, ?)""",
                         (inside.isoformat(), inside.isoformat()))
            conn.execute("""INSERT INTO products (id, shop, title, category, url, current_price,
                            first_seen_price, old_price_on_site, min_price, max_price, created_at, updated_at)
                            VALUES ('p2', 'dns', 'Старый', 'Ноутбуки', 'u', 5, 5, 0, 5, 5, ?, ?)""",
                         ("2026-09-01T10:00:00+00:00", "2026-09-01T10:00:00+00:00"))
            for pid, price, moment in [("p1", 100000, inside - datetime.timedelta(hours=1)),
                                       ("p1", 90000, inside),
                                       ("p2", 7, outside)]:
                conn.execute("INSERT INTO price_observations (product_id, price, old_price_on_site, observed_at) "
                             "VALUES (?, ?, 0, ?)", (pid, price, moment.isoformat()))
            conn.execute("""INSERT INTO telemetry_events (event_id, timestamp, type, severity, component,
                            shop, message, data_json) VALUES ('e1', ?, 'telegram_alert', 'INFO', 'telegram',
                            NULL, 'рассылка', ?)""",
                         (inside.isoformat(), json.dumps({"sent": 4, "retry": 1, "cancelled": 2})))
            conn.execute("""INSERT INTO telemetry_events (event_id, timestamp, type, severity, component,
                            shop, message, data_json) VALUES ('e2', ?, 'recovery', 'INFO', 'scraper',
                            'dns', 'магазин отвечает снова', '{}')""", (inside.isoformat(),))
            conn.execute("""INSERT INTO telemetry_events (event_id, timestamp, type, severity, component,
                            shop, message, data_json) VALUES ('e3', ?, 'telegram_alert', 'INFO', 'telegram',
                            NULL, 'после суток', ?)""",
                         (outside.isoformat(), json.dumps({"sent": 99})))
            conn.execute("""INSERT INTO source_scans (shop_key, source_url, category, scan_id, started_at,
                            finished_at, kind, quality, received, valid, rejected, with_image, accepted)
                            VALUES ('dns', 'u', 'Ноутбуки', 's1', ?, ?, 'category', 'ok', 300, 290, 10, 0, 1)""",
                         (inside.isoformat(), inside.isoformat()))
            conn.commit()

    # --- границы и сверка -------------------------------------------------

    def test_numbers_are_taken_only_from_the_requested_day(self):
        self.fill()
        r = digest.compute(DAY, "UTC", now=NOW)
        b = r["blocks"]
        self.assertEqual(b["search"]["searches"], 3, "поиск следующего дня не считается")
        self.assertEqual(b["search"]["outcomes"]["found"], 2)
        self.assertEqual(b["catalog"]["new_products"], 1)
        self.assertEqual(b["catalog"]["price_changes"], 1, "изменение следующего дня не считается")
        self.assertEqual(b["catalog"]["cheaper"], 1)
        self.assertEqual(b["catalog"]["biggest_drops"][0]["drop_pct"], 10.0)
        self.assertEqual(b["telegram"]["sent"], 4)
        self.assertEqual(b["telegram"]["attempts"], 5, "отменённые — не попытка")
        self.assertEqual(b["ai"]["requests"], 1)
        self.assertEqual(b["shops"]["scans"], 1)
        self.assertEqual(b["shops"]["recovered"], 1)
        self.assertFalse(r["partial"])
        self.assertFalse(r["empty"])

    def test_aggregates_reconcile_with_the_source_tables(self):
        """Сверка: цифры сводки должны совпадать с прямым подсчётом по тем же данным."""
        self.fill()
        b = digest.compute(DAY, "UTC", now=NOW)["blocks"]
        totals = database.search_totals(days=2, now=NOW)
        self.assertEqual(b["search"]["searches"] + 1, totals["total"], "в двух днях на один поиск больше")
        with database.get_connection() as conn:
            scans = conn.execute("SELECT COUNT(*) FROM source_scans WHERE finished_at LIKE ?",
                                 (DAY + "%",)).fetchone()[0]
        self.assertEqual(b["shops"]["scans"], scans)

    def test_local_day_marks_day_bucketed_blocks_as_approximate(self):
        self.fill()
        b = digest.compute(DAY, "Asia/Almaty", now=NOW)["blocks"]
        self.assertTrue(b["search"]["approximate"])
        self.assertTrue(b["ai"]["approximate"])
        self.assertIn("UTC", b["search"]["note"])
        self.assertFalse(b["catalog"].get("approximate"), "события режутся по точному времени")

    # --- пустые и неполные сутки -----------------------------------------

    def test_quiet_day_is_called_absence_of_data(self):
        r = digest.compute(DAY, "UTC", now=NOW)
        self.assertTrue(r["empty"])
        self.assertEqual(r["blocks"]["search"]["searches"], 0)
        self.assertIsNone(r["blocks"]["ai"]["cost_usd"], "без вызовов стоимость неизвестна, а не ноль")

    def test_unfinished_day_is_marked_partial(self):
        r = digest.compute("2026-09-23", "UTC", now=NOW)
        self.assertTrue(r["partial"])
        self.assertIn("не закончились", r["note"])

    def test_broken_block_is_reported_not_hidden(self):
        with patch.object(digest, "BLOCKS", {**digest.BLOCKS,
                                             "ai": ("Расходы AI", lambda *a: (_ for _ in ()).throw(
                                                 RuntimeError("таблица занята")))}):
            r = digest.compute(DAY, "UTC", now=NOW)
        self.assertIn("ai", r["unavailable"])
        self.assertFalse(r["empty"], "недоступный блок — не пустые сутки")

    # --- хранение ---------------------------------------------------------

    def test_recompute_keeps_one_record_and_same_numbers(self):
        self.fill()
        first = digest.report(DAY, "UTC", now=NOW)
        second = digest.report(DAY, "UTC", refresh=True, now=NOW)
        self.assertEqual(first["blocks"], second["blocks"], "цифры воспроизводимы")
        with database.get_connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM daily_reports").fetchone()[0], 1)

    def test_stored_finished_day_is_reused_without_recomputing(self):
        self.fill()
        digest.report(DAY, "UTC", now=NOW)
        with patch.object(digest, "compute", side_effect=AssertionError("пересчёта быть не должно")):
            again = digest.report(DAY, "UTC", now=NOW)
        self.assertEqual(again["day"], DAY)

    def test_unfinished_day_is_always_recomputed(self):
        digest.report("2026-09-23", "UTC", now=NOW)
        later = NOW + datetime.timedelta(hours=1)
        with patch.object(digest, "compute", wraps=digest.compute) as spy:
            digest.report("2026-09-23", "UTC", now=later)
        self.assertEqual(spy.call_count, 1)

    def test_summary_survives_a_recompute(self):
        self.fill()
        report = digest.report(DAY, "UTC", now=NOW)
        digest.save(report, summary="Спокойные сутки.", provider="gemini")
        digest.save(digest.compute(DAY, "UTC", now=NOW))
        self.assertEqual(digest.load(DAY, "UTC")["summary"], "Спокойные сутки.")

    def test_old_reports_are_pruned_by_the_declared_term(self):
        self.fill()
        digest.report(DAY, "UTC", now=NOW)
        digest.save(digest.compute("2025-01-01", "UTC", now=NOW))
        self.assertEqual(database.prune_daily_reports(now=NOW), 1)
        self.assertIsNotNone(digest.load(DAY, "UTC"))

    def test_retention_policy_mentions_the_new_table(self):
        import backup_health
        tables = {row.get("table") for row in backup_health.retention_policy()}
        self.assertIn("daily_reports", tables)


class SummaryTest(unittest.TestCase):
    """Пересказ не должен добавлять событий и чисел, которых в отчёте нет."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = type(DB_PATH)(self.tmp.name)
        self.patches = [patch.object(database, "DB_PATH", self.data_dir / "prices.db"),
                        patch("config.DATA_DIR", self.data_dir),
                        patch("config.SETTINGS_FILE", self.data_dir / "settings.json")]
        for p in self.patches:
            p.start()
        database.init_db()
        self.config = {"enabled": True, "has_ai": True, "ai_search_enabled": True, "ai_provider": "auto",
                       "gemini_api_key": "g", "openai_api_key": "", "openai_api_base": "",
                       "gemini_model": "gemini-2.5-flash", "openai_model": "gpt-4o-mini"}

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def run_summary(self, text, has_ai=True):
        report = digest.report(DAY, "UTC", now=NOW)      # отчёт уже сохранён: пересказ ложится к нему

        async def fake(prompt, key, *, timeout=30, scan=False):
            self.prompt = prompt
            sink = ai_service.usage_sink.get()
            if sink is not None:
                sink.append({"provider": "gemini", "outcome": "ok", "model": "gemini-2.5-flash",
                             "input_tokens": 50, "output_tokens": 20})
            return {"summary": text}

        self.config["has_ai"] = has_ai
        with patch("config.get_ai_config", return_value=self.config), \
             patch.object(ai_service, "call_gemini_api", fake):
            return report, asyncio.run(digest.summarize(report))

    def test_summary_with_field_references_is_rendered_by_the_service(self):
        report, result = self.run_summary(
            "За сутки поисков {blocks.search.searches}, новых товаров {blocks.catalog.new_products}. "
            "Вероятно, сервис простаивал.")
        self.assertIsNone(result["rejected"])
        self.assertIn("поисков 0", result["summary"])
        self.assertIn("Вероятно", result["summary"])
        self.assertEqual(digest.load(DAY, "UTC")["summary"], result["summary"])

    def test_invented_numbers_cancel_the_summary_but_not_the_report(self):
        report, result = self.run_summary("Продажи выросли на 37 %, обработано 4200 запросов.")
        self.assertIsNone(result["summary"])
        self.assertIn("написала числа сама", result["rejected"])
        self.assertIsNone(digest.load(DAY, "UTC")["summary"], "плохой пересказ не сохраняется")
        self.assertEqual(report["blocks"]["search"]["searches"], 0, "цифры отчёта остаются")

    def test_small_invented_number_is_rejected_too(self):
        """M04: «12 ошибок» при нулевых фактах — выдумка, а не нумерация пункта."""
        report, result = self.run_summary("За сутки было 12 ошибок и 3 обхода.")
        self.assertIsNone(result["summary"])
        self.assertIn("написала числа сама", result["rejected"])

    def test_a_known_number_attached_to_the_wrong_metric_is_refused(self):
        """Критерий M04: число из отчёта, приписанное не тому показателю, тоже не проходит."""
        report, result = self.run_summary("Ошибок AI: 0.")
        self.assertIsNone(result["summary"])
        self.assertIn("написала числа сама", result["rejected"])

    def test_numbered_list_with_references_is_kept(self):
        report, result = self.run_summary(
            "Итоги:\n1. Поисков {blocks.search.searches}.\n2. Новых товаров {blocks.catalog.new_products}.")
        self.assertIsNone(result["rejected"])
        self.assertIn("1. Поисков 0", result["summary"])

    def test_prompt_states_the_rules_and_wraps_data(self):
        self.run_summary("Сутки без происшествий.")
        self.assertIn("НЕ ПИШИ ЧИСЕЛ", self.prompt)
        self.assertIn("Доступные ссылки на показатели", self.prompt)
        self.assertIn("{blocks.search.searches}", self.prompt)
        self.assertIn("<<<ДАННЫЕ>>>", self.prompt)
        self.assertIn("вероятно", self.prompt)

    def test_without_ai_report_is_still_available(self):
        report = digest.compute(DAY, "UTC", now=NOW)
        self.config["has_ai"] = False
        with patch("config.get_ai_config", return_value=self.config):
            result = asyncio.run(digest.summarize(report))
        self.assertIsNone(result["summary"])
        self.assertIn("AI недоступен", result["rejected"])
        self.assertEqual(report["blocks"]["search"]["searches"], 0)


class PermissionsTest(unittest.IsolatedAsyncioTestCase):
    async def test_only_admin_sees_the_digest(self):
        import auth
        import web.server as server
        from aiohttp.test_utils import TestServer
        from test_support import BrowserTestClient as TestClient
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patches = [patch.object(database, "DB_PATH", type(DB_PATH)(os.path.join(tmp.name, "prices.db"))),
                   patch("config.DATA_DIR", type(DB_PATH)(tmp.name))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        database.init_db()
        for uid in (9301, 9302):
            database.upsert_telegram_user({"id": uid, "first_name": "U"})
        tokens = {uid: database.create_session(uid) for uid in (9301, 9302)}
        app = server.create_app()
        app.cleanup_ctx.clear()
        async with TestClient(TestServer(app)) as client:
            self.assertEqual((await client.get("/api/admin/monitoring/daily")).status, 401)
            client.session.cookie_jar.update_cookies({auth.SESSION_COOKIE: tokens[9301]})
            self.assertEqual((await client.get("/api/admin/monitoring/daily")).status, 403)
            with patch.object(auth, "ADMIN_TELEGRAM_IDS", {9302}):
                client.session.cookie_jar.update_cookies({auth.SESSION_COOKIE: tokens[9302]})
                res = await client.get("/api/admin/monitoring/daily")
                self.assertEqual(res.status, 200)
                body = await res.json()
                self.assertIn("blocks", body["report"])
                bad = await client.get("/api/admin/monitoring/daily?day=вчера")
                self.assertEqual(bad.status, 400)


if __name__ == "__main__":
    unittest.main()


class TelegramDigestTest(unittest.TestCase):
    """Отправка сводки администратору: отдельное включение, один раз за сутки, понятный текст."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = type(DB_PATH)(self.tmp.name)
        self.patches = [patch.object(database, "DB_PATH", self.data_dir / "prices.db"),
                        patch("config.DATA_DIR", self.data_dir),
                        patch("config.SETTINGS_FILE", self.data_dir / "settings.json")]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        database.init_db()
        database.upsert_telegram_user({"id": 501, "first_name": "Админ"})
        database.upsert_telegram_user({"id": 502, "first_name": "Человек"})
        self.admins = patch("config.ADMIN_TELEGRAM_IDS", {501})
        self.admins.start()
        self.addCleanup(self.admins.stop)
        self.view = {"enabled": True, "hour": 10, "tz": "UTC"}

    def outbox(self):
        with database.get_connection() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM notification_outbox ORDER BY id")]

    # --- когда отправлять -------------------------------------------------

    def test_disabled_by_default(self):
        import config
        self.assertFalse(config.SYSTEM_DEFAULTS["daily_digest_telegram_enabled"],
                         "план просил отдельное включение, а не включение по умолчанию")
        with patch.object(digest, "settings_view", return_value={**self.view, "enabled": False}):
            self.assertIsNone(digest.due_day(NOW))
            self.assertEqual(digest.queue_if_due(NOW), 0)

    def test_nothing_is_sent_before_the_chosen_hour(self):
        early = datetime.datetime(2026, 9, 23, 9, 0, tzinfo=UTC)
        with patch.object(digest, "settings_view", return_value=self.view):
            self.assertIsNone(digest.due_day(early))
            self.assertEqual(digest.due_day(NOW), DAY, "после назначенного часа — вчерашние сутки")

    def test_hour_is_counted_in_the_chosen_timezone(self):
        """05:00 UTC — это уже 10:00 в Алматы, сводка должна уйти."""
        moment = datetime.datetime(2026, 9, 23, 5, 0, tzinfo=UTC)
        with patch.object(digest, "settings_view", return_value={**self.view, "tz": "Asia/Almaty"}):
            self.assertEqual(digest.due_day(moment), DAY)
        with patch.object(digest, "settings_view", return_value=self.view):
            self.assertIsNone(digest.due_day(moment), "по UTC ещё рано")

    # --- очередь ----------------------------------------------------------

    def test_one_message_per_day_even_if_checked_many_times(self):
        with patch.object(digest, "settings_view", return_value=self.view):
            self.assertEqual(digest.queue_if_due(NOW), 1)
            for _ in range(5):
                self.assertEqual(digest.queue_if_due(NOW), 0, "повторная проверка не плодит писем")
        queue = self.outbox()
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["user_id"], 501)
        self.assertEqual(queue[0]["alert_id"], digest.digest_alert_id(DAY))

    def test_next_day_gets_its_own_message(self):
        with patch.object(digest, "settings_view", return_value=self.view):
            digest.queue_if_due(NOW)
            digest.queue_if_due(NOW + datetime.timedelta(days=1))
        self.assertEqual(len(self.outbox()), 2)

    def test_digest_ids_cannot_collide_with_watch_events(self):
        """Своё пространство идентификаторов: сводка не отменит чужое сообщение."""
        self.assertLess(digest.digest_alert_id(DAY), -digest.DIGEST_ALERT_BASE)
        self.assertNotEqual(digest.digest_alert_id(DAY), digest.digest_alert_id("2026-09-21"))

    def test_only_admins_receive_it(self):
        with patch.object(digest, "settings_view", return_value=self.view):
            digest.queue_if_due(NOW)
        self.assertEqual([q["user_id"] for q in self.outbox()], [501])

    def test_blocked_admin_is_not_a_recipient(self):
        database.set_user_blocked(501, True)
        with patch.object(digest, "settings_view", return_value=self.view):
            self.assertEqual(digest.queue_if_due(NOW), 0)

    def test_report_is_stored_and_reused_for_the_message(self):
        with patch.object(digest, "settings_view", return_value=self.view):
            digest.queue_if_due(NOW)
        self.assertIsNotNone(digest.load(DAY, "UTC"), "сводка сохранена и её можно открыть в мониторинге")

    # --- текст ------------------------------------------------------------

    def test_message_has_the_numbers_without_any_ai(self):
        report = digest.compute(DAY, "UTC", now=NOW)
        text = "\n".join(digest.message_lines(report))
        self.assertIn(f"Сводка за {DAY}", text)
        self.assertIn("Поиски", text)
        self.assertIn("вызовов не было", text, "без вызовов стоимость не показывается нулём")
        self.assertIn("посчитаны кодом", text)
        self.assertNotIn("None", text)

    def test_quiet_and_partial_days_are_named(self):
        quiet = "\n".join(digest.message_lines(digest.compute(DAY, "UTC", now=NOW)))
        self.assertIn("отсутствие данных", quiet)
        partial = "\n".join(digest.message_lines(digest.compute("2026-09-23", "UTC", now=NOW)))
        self.assertIn("не закончились", partial)

    def test_summary_is_added_only_when_it_exists(self):
        report = digest.compute(DAY, "UTC", now=NOW)
        self.assertNotIn("🗒", "\n".join(digest.message_lines(report)))
        report["summary"] = "Спокойные сутки."
        self.assertIn("Спокойные сутки.", "\n".join(digest.message_lines(report)))

    def test_titles_from_shops_are_escaped(self):
        report = digest.compute(DAY, "UTC", now=NOW)
        report["blocks"]["catalog"]["biggest_drops"] = [
            {"title": "<img src=x onerror=alert(1)>", "was": 100, "now": 90, "drop_pct": 10}]
        text = "\n".join(digest.message_lines(report))
        self.assertIn("&lt;img", text)
        self.assertNotIn("<img", text)

    # --- доставка ---------------------------------------------------------

    def send(self):
        import notifier
        sent = []
        with patch.object(notifier, "get_bot_token", lambda: "token"), \
             patch.object(notifier, "telegram_api",
                          side_effect=lambda method, payload, **kw: sent.append(payload) or _digest_ok()):
            notifier.deliver_pending(limit=5)
        return sent

    def test_delivery_sends_one_message_and_closes_the_task(self):
        with patch.object(digest, "settings_view", return_value=self.view):
            digest.queue_if_due(NOW)
        sent = self.send()
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0]["chat_id"], 501)
        self.assertIn("Сводка за", sent[0]["text"])
        self.assertEqual([q["status"] for q in self.outbox()], ["sent"])
        self.assertEqual(self.send(), [], "повторной отправки после перезапуска нет")

    def test_message_for_a_former_admin_is_cancelled(self):
        with patch.object(digest, "settings_view", return_value=self.view):
            digest.queue_if_due(NOW)
        with patch("config.ADMIN_TELEGRAM_IDS", set()):
            self.assertEqual(self.send(), [])
        self.assertEqual([q["status"] for q in self.outbox()], ["cancelled"])

    def test_failed_delivery_returns_to_the_queue(self):
        import notifier
        with patch.object(digest, "settings_view", return_value=self.view):
            digest.queue_if_due(NOW)
        with patch.object(notifier, "get_bot_token", lambda: "token"), \
             patch.object(notifier, "send_daily_digest",
                          return_value=notifier.DeliveryResult("retry", None, "сеть")):
            notifier.deliver_pending(limit=5)
        self.assertEqual([q["status"] for q in self.outbox()], ["pending"])


def _digest_ok():
    class _R:
        status_code = 200

        @staticmethod
        def json():
            return {"ok": True}
    return _R()


class TelegramSwitchApiTest(unittest.IsolatedAsyncioTestCase):
    async def test_switch_is_admin_only_and_validated(self):
        import auth
        import config
        import web.server as server
        from aiohttp.test_utils import TestServer
        from test_support import BrowserTestClient as TestClient
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patches = [patch.object(database, "DB_PATH", type(DB_PATH)(os.path.join(tmp.name, "prices.db"))),
                   patch("config.DATA_DIR", type(DB_PATH)(tmp.name)),
                   patch("config.SETTINGS_FILE", type(DB_PATH)(os.path.join(tmp.name, "settings.json")))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        database.init_db()
        for uid in (9401, 9402):
            database.upsert_telegram_user({"id": uid, "first_name": "U"})
        tokens = {uid: database.create_session(uid) for uid in (9401, 9402)}
        app = server.create_app()
        app.cleanup_ctx.clear()
        async with TestClient(TestServer(app)) as client:
            self.assertEqual((await client.post("/api/admin/monitoring/daily/telegram",
                                                json={"enabled": True})).status, 401)
            client.session.cookie_jar.update_cookies({auth.SESSION_COOKIE: tokens[9401]})
            self.assertEqual((await client.post("/api/admin/monitoring/daily/telegram",
                                                json={"enabled": True})).status, 403)
            with patch.object(auth, "ADMIN_TELEGRAM_IDS", {9402}):
                client.session.cookie_jar.update_cookies({auth.SESSION_COOKIE: tokens[9402]})
                res = await client.post("/api/admin/monitoring/daily/telegram",
                                        json={"enabled": True, "hour": 7})
                self.assertEqual(res.status, 200)
                body = await res.json()
                self.assertTrue(body["telegram"]["enabled"])
                self.assertEqual(body["telegram"]["hour"], 7)
                self.assertTrue(config.load_settings()["daily_digest_telegram_enabled"])
                bad = await client.post("/api/admin/monitoring/daily/telegram", json={"hour": 47})
                self.assertEqual(bad.status, 400)
                empty = await client.post("/api/admin/monitoring/daily/telegram", json={})
                self.assertEqual(empty.status, 400)


class AuditFixesTest(unittest.TestCase):
    """M05 и M06: счётчики событий считают все, а неизвестная стоимость не выдаётся за ноль."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = type(DB_PATH)(self.tmp.name)
        for p in [patch.object(database, "DB_PATH", self.data_dir / "prices.db"),
                  patch("config.DATA_DIR", self.data_dir),
                  patch("config.SETTINGS_FILE", self.data_dir / "settings.json")]:
            p.start()
            self.addCleanup(p.stop)
        database.init_db()
        self.inside = datetime.datetime(2026, 9, 22, 10, 0, tzinfo=UTC)

    def add_events(self, kind, count):
        with database.get_connection() as conn:
            for i in range(count):
                conn.execute("""INSERT INTO telemetry_events (event_id, timestamp, type, severity,
                                component, shop, message, data_json)
                                VALUES (?, ?, ?, 'INFO', 'scraper', 'dns', 'событие', '{}')""",
                             (f"{kind}-{i}", self.inside.isoformat(), kind))
            conn.commit()

    def blocks(self):
        return digest.compute(DAY, "UTC", now=NOW)["blocks"]

    # --- M05 ---

    def test_event_counters_are_exact_for_any_number(self):
        for count in (0, 5, 8, 100):
            with self.subTest(count=count):
                with database.get_connection() as conn:
                    conn.execute("DELETE FROM telemetry_events")
                    conn.commit()
                self.add_events("recovery", count)
                shops = self.blocks()["shops"]
                self.assertEqual(shops["recovered"], count)
                self.assertLessEqual(len(shops["events"]["recovery"]), 5, "примеров не больше пяти")

    def test_degradation_counter_is_exact_too(self):
        self.add_events("degradation", 9)
        self.assertEqual(self.blocks()["shops"]["degraded"], 9)

    def test_telegram_message_shows_the_full_count(self):
        self.add_events("recovery", 8)
        text = "\n".join(digest.message_lines(digest.compute(DAY, "UTC", now=NOW)))
        self.assertIn("восстановлений 8", text)

    # --- M06 ---

    def test_calls_without_prices_mean_unknown_cost_not_zero(self):
        for i in range(3):
            database.record_ai_usage("consultant", "user", "gemini", "gemini-2.5-flash", "ok",
                                     input_tokens=10, output_tokens=5, cost=None, now=self.inside)
        ai = self.blocks()["ai"]
        self.assertEqual(ai["requests"], 3)
        self.assertIsNone(ai["cost_usd"])
        self.assertEqual(ai["cost_state"], "unknown")
        self.assertIn("неизвестна", digest._cost_text(ai))

    def test_partially_priced_calls_are_named_partial(self):
        database.record_ai_usage("consultant", "user", "gemini", "gemini-2.5-flash", "ok",
                                 cost=0.5, now=self.inside)
        database.record_ai_usage("normalize", "internal", "gemini", "gemini-2.5-flash", "ok",
                                 cost=None, now=self.inside)
        ai = self.blocks()["ai"]
        self.assertEqual(ai["cost_state"], "partial")
        self.assertEqual((ai["priced_requests"], ai["unpriced_requests"]), (1, 1))
        self.assertIn("из 2 вызовов", digest._cost_text(ai))

    def test_real_zero_price_is_a_known_zero(self):
        database.record_ai_usage("consultant", "user", "gemini", "gemini-2.5-flash", "ok",
                                 cost=0.0, now=self.inside)
        ai = self.blocks()["ai"]
        self.assertEqual((ai["cost_state"], ai["cost_usd"]), ("known", 0.0))
        self.assertEqual(digest._cost_text(ai), "$0.0")

    def test_no_calls_is_not_a_zero_cost(self):
        ai = self.blocks()["ai"]
        self.assertEqual(ai["cost_state"], "no_calls")
        self.assertIsNone(ai["cost_usd"])
        self.assertIn("вызовов не было", digest._cost_text(ai))


class MixedCostTest(unittest.TestCase):
    """M06: вызовы с ценой и без неё попадают в одну строку агрегата — неполнота обязана быть видна."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = type(DB_PATH)(self.tmp.name)
        for p in [patch.object(database, "DB_PATH", self.data_dir / "prices.db"),
                  patch("config.DATA_DIR", self.data_dir),
                  patch("config.SETTINGS_FILE", self.data_dir / "settings.json")]:
            p.start()
            self.addCleanup(p.stop)
        database.init_db()
        self.inside = datetime.datetime(2026, 9, 22, 10, 0, tzinfo=UTC)

    def record(self, cost):
        database.record_ai_usage("consultant", "user", "gemini", "gemini-2.5-flash", "ok",
                                 cost=cost, now=self.inside)

    def ai(self):
        return digest.compute(DAY, "UTC", now=NOW)["blocks"]["ai"]

    def test_same_row_with_and_without_price_is_partial(self):
        """Воспроизведение аудита: два одинаковых по ключу вызова, у одного цены нет."""
        self.record(None)
        self.record(0.5)
        ai = self.ai()
        self.assertEqual(ai["requests"], 2)
        self.assertEqual(ai["priced_requests"], 1)
        self.assertEqual(ai["unpriced_requests"], 1)
        self.assertEqual(ai["cost_state"], "partial")
        self.assertIn("из 2 вызовов", digest._cost_text(ai))

    def test_order_does_not_matter(self):
        self.record(0.5)
        self.record(None)
        ai = self.ai()
        self.assertEqual((ai["cost_state"], ai["priced_requests"]), ("partial", 1))

    def test_all_priced_in_one_row_is_known(self):
        self.record(0.25)
        self.record(0.25)
        ai = self.ai()
        self.assertEqual(ai["cost_state"], "known")
        self.assertEqual(ai["cost_usd"], 0.5)

    def test_all_unpriced_in_one_row_is_unknown(self):
        self.record(None)
        self.record(None)
        ai = self.ai()
        self.assertEqual(ai["cost_state"], "unknown")
        self.assertIsNone(ai["cost_usd"])

    def test_rows_written_before_the_counter_are_called_incomplete(self):
        """Прежние строки без счётчика: покрытие неизвестно, и так и сказано."""
        self.record(0.5)
        with database.get_connection() as conn:
            conn.execute("UPDATE ai_usage SET priced_requests = NULL")
            conn.commit()
        ai = self.ai()
        self.assertEqual(ai["cost_state"], "partial")
        self.assertEqual(ai["legacy_requests"], 1)
        self.assertIn("до раздельного учёта", digest._cost_text(ai))

    def test_incompleteness_reaches_the_telegram_message(self):
        self.record(None)
        self.record(0.5)
        text = "\n".join(digest.message_lines(digest.compute(DAY, "UTC", now=NOW)))
        self.assertIn("из 2 вызовов", text)
