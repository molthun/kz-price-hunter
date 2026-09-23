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

    def test_summary_with_report_numbers_is_kept(self):
        report, result = self.run_summary("За сутки поисков 0, новых товаров 0. Вероятно, сервис простаивал.")
        self.assertIsNone(result["rejected"])
        self.assertIn("Вероятно", result["summary"])
        self.assertEqual(digest.load(DAY, "UTC")["summary"], result["summary"])

    def test_invented_numbers_cancel_the_summary_but_not_the_report(self):
        report, result = self.run_summary("Продажи выросли на 37 %, обработано 4200 запросов.")
        self.assertIsNone(result["summary"])
        self.assertIn("которых нет в отчёте", result["rejected"])
        self.assertIsNone(digest.load(DAY, "UTC")["summary"], "плохой пересказ не сохраняется")
        self.assertEqual(report["blocks"]["search"]["searches"], 0, "цифры отчёта остаются")

    def test_prompt_states_the_rules_and_wraps_data(self):
        self.run_summary("Поисков 0.")
        self.assertIn("ТОЛЬКО числа из блока данных", self.prompt)
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
