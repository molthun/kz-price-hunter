"""P03 Monitoring Center V1: статусы без ложного зелёного, инциденты, доступ, масштаб 25/50/100 источников."""
import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import asyncio
import datetime
import os
import tempfile
import time
import unittest
from unittest.mock import patch

import database
import monitoring as mon
import telemetry as tm
from config import DB_PATH

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def ts(hours_ago, now=NOW):
    return (now - datetime.timedelta(hours=hours_ago)).isoformat()


class ShopStatusTest(unittest.TestCase):
    """Правила статуса магазина на управляемом времени."""

    def status(self, scan, quality=None, enabled=True):
        return mon.shop_status(enabled, scan, quality, NOW)[0]

    def test_all_states(self):
        ok = {"status": "complete", "last_success_at": ts(2), "last_items": 500, "failure_count": 0}
        self.assertEqual(self.status(ok, "ok"), "healthy")
        self.assertEqual(self.status(ok, None), "healthy")
        self.assertEqual(self.status(dict(ok, status="limited")), "limited")
        self.assertEqual(self.status(dict(ok, status="partial", last_error="Кат: Timeout")), "degraded")
        self.assertEqual(self.status(ok, "degraded"), "degraded")
        self.assertEqual(self.status(ok, "warning"), "degraded")
        self.assertEqual(self.status(dict(ok, last_success_at=ts(40))), "degraded")   # aging
        self.assertEqual(self.status(dict(ok, last_items=0)), "empty")
        self.assertEqual(self.status(dict(ok, status="failed", failure_count=1)), "degraded")
        self.assertEqual(self.status(dict(ok, status="failed", failure_count=3)), "offline")
        self.assertEqual(self.status(dict(ok, status="failed", failure_count=1, last_success_at=ts(80))), "offline")
        self.assertEqual(self.status(dict(ok, status="failed", failure_count=1, last_success_at=None)), "offline")
        self.assertEqual(self.status(ok, enabled=False), "disabled")

    def test_no_observations_never_green(self):
        for scan in (None, {}, {"status": "unknown"}, {"status": "running", "last_items": 0}):
            self.assertEqual(self.status(scan), "unknown", scan)

    def test_rate_status_and_worst(self):
        self.assertEqual(mon.rate_status(0, 0)[0], "unknown")
        self.assertEqual(mon.rate_status(10, 2)[0], "healthy")
        self.assertEqual(mon.rate_status(10, 3)[0], "degraded")
        self.assertEqual(mon.rate_status(5, 5, disabled=True)[0], "disabled")
        self.assertEqual(mon.worst(["healthy", "unknown", "limited"]), "limited")
        self.assertEqual(mon.worst(["disabled"]), "unknown")
        self.assertEqual(mon.worst(["healthy", "offline"]), "offline")


class DbCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [patch.object(database, "DB_PATH", type(DB_PATH)(os.path.join(self.tmp.name, "prices.db"))),
                        patch("config.DATA_DIR", type(DB_PATH)(self.tmp.name))]
        for p in self.patches:
            p.start()
        database.init_db()
        self.t = tm.TelemetryService()
        p = patch.object(tm, "telemetry", self.t)
        p.start()
        self.patches.append(p)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def event(self, when_hours_ago, event_type, severity, component, message, now=NOW, **kw):
        e = self.t.record_event(event_type, severity, component, message, **kw)
        e["timestamp"] = (now - datetime.timedelta(hours=when_hours_ago)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        return e

    def flush(self):
        self.assertTrue(self.t.flush())


class OverviewTest(DbCase):
    def registry(self, n):
        return {f"s{i:03d}": (f"Магазин {i} с очень длинным названием для проверки переноса строк", 3) for i in range(n)}

    def test_empty_database_is_unknown_not_green(self):
        with patch("config.get_bot_token", return_value=""):
            o = mon.overview(self.registry(3), ["s000", "s001", "s002"], {"is_running": False}, 3600, NOW)
        self.assertEqual({s["status"] for s in o["shops"]}, {"unknown"})
        self.assertEqual(o["sections"]["shops"]["status"], "unknown")
        self.assertEqual(o["sections"]["scanning"]["status"], "unknown")
        self.assertEqual(o["sections"]["search"]["status"], "unknown")
        self.assertEqual(o["sections"]["telegram"]["status"], "disabled")
        self.assertNotEqual(o["status"], "healthy")

    def test_scale_25_50_100_sources(self):
        for n in (25, 50, 100):
            reg = self.registry(n)
            for i, key in enumerate(reg):
                status = ["complete", "limited", "failed", "partial"][i % 4]
                database.record_shop_scan_result(key, 0 if status == "failed" else 100, 1.0,
                                                 "HTTP 403" if status in ("failed", "partial") else None, status)
            started = time.monotonic()
            o = mon.overview(reg, list(reg)[: n - 1], {"is_running": False}, 3600)
            elapsed = time.monotonic() - started
            self.assertEqual(len(o["shops"]), n)
            self.assertEqual(o["shops"][-1]["status"], "disabled")  # выключенный — в конце списка
            self.assertLess(elapsed, 3.0, n)
            counts = o["sections"]["shops"]["counts"]
            self.assertEqual(sum(counts.values()), n)
            self.assertGreater(counts.get("degraded", 0), 0)

    def test_scanning_section_offline_when_scans_stop(self):
        self.event(30, tm.EVENT_SCAN_END, "INFO", tm.COMPONENT_SCHEDULER, "Итог", data={"outcome": "completed"})
        self.flush()
        s = mon.scanning_section({"is_running": False}, 3600, NOW)
        self.assertEqual(s["status"], "offline")  # ожидалось не реже 2 × 3 ч
        s = mon.scanning_section({"is_running": False}, 3600, NOW - datetime.timedelta(hours=29))
        self.assertEqual(s["status"], "healthy")

    def test_search_section_error_share(self):
        for i in range(10):
            self.event(1, tm.EVENT_SEARCH_QUERY, "ERROR" if i < 3 else "INFO", tm.COMPONENT_SEARCH, "q",
                       data={"source": "summary", "outcome": "error" if i < 3 else "found", "duration_ms": 10 + i})
        self.flush()
        s = mon.search_section(NOW)
        self.assertEqual((s["status"], s["total"], s["errors"]), ("degraded", 10, 3))


class TelegramSectionTest(DbCase):
    """D02: временные ошибки доставки и ошибки воркера — неуспех; cancelled — не попытка."""

    def section(self, token="t", paused=0.0, **counts):
        import notifier
        if counts:
            self.event(1, tm.EVENT_TELEGRAM_ALERT, "INFO", tm.COMPONENT_TELEGRAM, "доставка", data=counts)
        self.flush()
        with patch("config.get_bot_token", return_value=token), patch.object(notifier, "telegram_paused_for",
                                                                               return_value=paused):
            return mon.telegram_section(NOW)

    def test_only_retry_is_degraded(self):
        s = self.section(sent=0, retry=10, failed=0, errors=0)
        self.assertEqual(s["status"], "degraded")
        self.assertIn("временных 10", s["reason"])

    def test_threshold_on_mixed_results(self):
        self.assertEqual(self.section(sent=9, retry=1)["status"], "healthy")

    def test_threshold_exceeded(self):
        self.assertEqual(self.section(sent=7, retry=2, failed=1)["status"], "degraded")

    def test_no_attempts_unknown_and_cancelled_not_attempts(self):
        self.assertEqual(self.section()["status"], "unknown")

    def test_cancelled_only_is_unknown(self):
        self.assertEqual(self.section(cancelled=5)["status"], "unknown")

    def test_disabled_and_cooldown(self):
        self.assertEqual(self.section(token="", sent=1)["status"], "disabled")

    def test_active_pause_degraded(self):
        self.assertEqual(self.section(paused=30, sent=5)["status"], "degraded")

    def test_worker_errors_count(self):
        self.event(2, tm.EVENT_SYSTEM_ERROR, "ERROR", tm.COMPONENT_TELEGRAM, "notification_worker: OperationalError",
                   data={"where": "notification_worker", "error": "OperationalError"})
        self.assertEqual(self.section()["status"], "degraded")


class IncidentTest(DbCase):
    def http403(self, hours, category, shop="Магазин"):
        self.event(hours, tm.EVENT_HTTP_REQUEST, "WARNING", tm.COMPONENT_HTTP, "HTTP 403 GET shop.example",
                   shop=shop, category=category, scan_id=f"scan{hours}{category}",
                   data={"host": "shop.example", "status": 403})

    def category_result(self, hours, category, complete=True, quality="ok", error=None, shop="Магазин"):
        self.event(hours, tm.EVENT_SCAN_CATEGORY, "WARNING" if error else "INFO", tm.COMPONENT_SCRAPER, "итог",
                   shop=shop, category=category,
                   data={"items_count": 10, "complete": complete, "error": error, "quality": {"quality": quality}})

    def only(self):
        (inc,) = mon.incidents(now=NOW, shop_keys={"Магазин": "shop"})
        return inc

    def test_repeated_403_grouped_with_hypothesis(self):
        for h in (5, 4, 3):
            self.http403(h, f"Кат {h}")
        self.flush()
        inc = self.only()
        self.assertTrue(inc["open"])
        self.assertEqual((inc["count"], inc["scale"]["categories_count"], inc["scale"]["scans"]), (3, 3, 3))
        self.assertIn("блокир", inc["hypothesis"].lower())
        self.assertEqual(inc["scale"]["hosts"], ["shop.example"])

    def test_codex_repro_other_category_success_does_not_recover(self):
        # D01: Phones — 403, затем успех только TV того же магазина: инцидент Phones остаётся открытым
        self.http403(2, "Phones")
        self.category_result(1, "TV")
        self.flush()
        inc = self.only()
        self.assertEqual((inc["state"], inc["recovered_at"]), ("open", None))
        self.assertEqual(inc["recovery_progress"], {"recovered": 0, "total": 1})

    def test_limited_or_warning_success_does_not_recover(self):
        self.http403(3, "Phones")
        self.category_result(2, "Phones", complete=False)            # limited
        self.category_result(1, "Phones", quality="warning")         # предупреждение качества
        self.flush()
        self.assertTrue(self.only()["open"])

    def test_confirmed_success_of_same_category_recovers_with_stable_id(self):
        self.http403(3, "Phones")
        self.flush()
        first_id = self.only()["id"]
        self.category_result(1, "Phones")
        self.flush()
        inc = self.only()
        self.assertEqual(inc["state"], "recovered")
        self.assertEqual(inc["id"], first_id)

    def test_partial_recovery_of_several_categories(self):
        self.http403(4, "Phones")
        self.http403(4, "TV")
        self.category_result(2, "Phones")
        self.flush()
        inc = self.only()
        self.assertTrue(inc["open"])
        self.assertEqual(inc["recovery_progress"], {"recovered": 1, "total": 2})
        self.category_result(1, "TV")
        self.flush()
        self.assertEqual(self.only()["state"], "recovered")

    def test_problem_after_success_reopens(self):
        self.http403(4, "Phones")
        self.category_result(3, "Phones")
        self.http403(2, "Phones")
        self.flush()
        self.assertTrue(self.only()["open"])

    def test_shop_recovery_event_closes_shop_level_incident(self):
        # Падение магазина без категории закрывается только полным обходом после деградации (recovery P02)
        self.event(3, tm.EVENT_DEGRADATION, "ERROR", tm.COMPONENT_SCRAPER, "Магазин shop: complete → failed",
                   data={"shop_key": "shop", "from": "complete", "to": "failed"})
        self.category_result(2, "Phones")
        self.flush()
        self.assertTrue(mon.incidents(now=NOW)[0]["open"])
        self.event(1, tm.EVENT_RECOVERY, "INFO", tm.COMPONENT_SCRAPER, "восстановлен",
                   data={"shop_key": "shop", "from": "failed", "to": "complete"})
        self.flush()
        self.assertEqual(mon.incidents(now=NOW)[0]["state"], "recovered")

    def test_http_without_category_recovers_by_host_2xx(self):
        now = datetime.datetime.now(UTC)
        self.event(0.2, tm.EVENT_HTTP_REQUEST, "WARNING", tm.COMPONENT_HTTP, "HTTP 404", now=now, shop="Магазин",
                   data={"host": "cards.example", "status": 404})
        self.flush()
        (inc,) = mon.incidents(now=now)
        self.assertTrue(inc["open"])
        self.t.record_http_metric("cards.example", "GET", 200, 10.0, shop="Магазин")
        self.flush()
        (inc,) = mon.incidents(now=now + datetime.timedelta(minutes=5))
        self.assertEqual(inc["state"], "recovered")

    def test_different_causes_are_separate_incidents(self):
        self.event(3, tm.EVENT_HTTP_REQUEST, "WARNING", tm.COMPONENT_HTTP, "403", shop="A",
                   data={"host": "a", "status": 403})
        self.event(2, tm.EVENT_HTTP_REQUEST, "ERROR", tm.COMPONENT_HTTP, "timeout", shop="A",
                   data={"host": "a", "kind": "timeout", "error": "Timeout"})
        self.event(2, tm.EVENT_SCAN_CATEGORY, "WARNING", tm.COMPONENT_SCRAPER, "[A] Кат: 3 товаров", shop="A",
                   category="Кат", data={"error": "Качество: товаров 3 при норме 200 (2%)"})
        self.flush()
        found = {(i["type"], i["hypothesis"]) for i in mon.incidents(now=NOW)}
        self.assertEqual(len(found), 3)
        self.assertTrue(any("timeout" in h for _, h in found))
        self.assertTrue(any("вёрстки" in h for _, h in found))

    def test_component_needs_explicit_success(self):
        for h in (3, 2.5):
            self.event(h, tm.EVENT_SYSTEM_ERROR, "ERROR", tm.COMPONENT_AI, "ai_normalize_worker: RuntimeError",
                       data={"where": "ai_normalize_worker", "error": "RuntimeError"})
        # Пропуск вызова (лимит) — INFO, но не успех AI
        self.event(2, tm.EVENT_AI_QUERY, "INFO", tm.COMPONENT_AI, "skip",
                   data={"outcome": "skipped_busy", "provider_called": False})
        self.flush()
        (inc,) = [i for i in mon.incidents(now=NOW) if i["type"] == "system_error"]
        self.assertTrue(inc["open"])
        # Пользовательский вызов AI не подтверждает исправность нормализации (другая операция)
        self.event(1.9, tm.EVENT_AI_QUERY, "INFO", tm.COMPONENT_AI, "ok",
                   data={"outcome": "ok", "provider_called": True, "purpose": "user"})
        self.flush()
        (inc,) = [i for i in mon.incidents(now=NOW) if i["type"] == "system_error"]
        self.assertTrue(inc["open"])
        self.event(1.8, tm.EVENT_AI_QUERY, "INFO", tm.COMPONENT_AI, "ok",
                   data={"outcome": "ok", "provider_called": True, "purpose": "normalize"})
        self.flush()
        (inc,) = [i for i in mon.incidents(now=NOW) if i["type"] == "system_error"]
        self.assertEqual(inc["state"], "recovered")
        (inc,) = [i for i in mon.incidents(now=NOW - datetime.timedelta(hours=2)) if i["type"] == "system_error"]
        self.assertTrue(inc["open"])  # меньше часа без повторов

    def ai_error(self, hours, purpose, outcome="error"):
        self.event(hours, tm.EVENT_AI_QUERY, "WARNING", tm.COMPONENT_AI, f"AI gemini/{purpose}: {outcome}",
                   data={"outcome": outcome, "provider_called": True, "purpose": purpose})

    def ai_incidents(self):
        return {i["scope"]: i for i in mon.incidents(now=NOW) if i["type"] == "ai_query"}

    def check_ai_separation(self, first, second):
        self.ai_error(3, first)
        self.ai_error(2, second)
        self.event(1, tm.EVENT_AI_QUERY, "INFO", tm.COMPONENT_AI, "ok",
                   data={"outcome": "ok", "provider_called": True, "purpose": "normalize"})
        self.flush()
        incs = self.ai_incidents()
        self.assertEqual(set(incs), {"ai:user", "ai:normalize"})
        self.assertEqual((incs["ai:normalize"]["state"], incs["ai:normalize"]["count"]), ("recovered", 1))
        self.assertEqual((incs["ai:user"]["state"], incs["ai:user"]["recovered_at"]), ("open", None))
        ids = {k: v["id"] for k, v in incs.items()}
        self.assertEqual(ids, {k: v["id"] for k, v in self.ai_incidents().items()})  # стабильны между вызовами
        self.assertEqual(len(set(ids.values())), 2)

    def test_codex_repro_ai_operations_grouped_separately(self):
        self.check_ai_separation("normalize", "user")

    def test_ai_operations_grouped_separately_reverse_order(self):
        self.check_ai_separation("user", "normalize")

    def test_same_producer_outcome_empty_also_separated(self):
        self.ai_error(3, "normalize", "empty")
        self.ai_error(2, "user", "empty")
        self.flush()
        self.assertEqual(set(self.ai_incidents()), {"ai:user", "ai:normalize"})

    def test_codex_repro_polling_not_recovered_by_delivery(self):
        # Формат реального producer: telegram_bot.py пишет WARNING system_error where=telegram_polling
        self.event(3, tm.EVENT_SYSTEM_ERROR, "WARNING", tm.COMPONENT_TELEGRAM, "telegram_polling: ClientError",
                   data={"where": "telegram_polling", "error": "ClientError"})
        self.event(0.5, tm.EVENT_TELEGRAM_ALERT, "INFO", tm.COMPONENT_TELEGRAM, "доставка", data={"sent": 1})
        self.flush()
        (inc,) = [i for i in mon.incidents(now=NOW) if i["type"] == "system_error"]
        self.assertEqual((inc["state"], inc["recovered_at"]), ("open", None))
        (inc,) = [i for i in mon.incidents(now=NOW + datetime.timedelta(hours=25)) if i["type"] == "system_error"]
        self.assertEqual((inc["state"], inc["recovered_at"]), ("quiet", None))

    def test_notification_worker_recovered_by_delivery(self):
        self.event(3, tm.EVENT_SYSTEM_ERROR, "ERROR", tm.COMPONENT_TELEGRAM, "notification_worker: OperationalError",
                   data={"where": "notification_worker", "error": "OperationalError"})
        self.event(1, tm.EVENT_TELEGRAM_ALERT, "INFO", tm.COMPONENT_TELEGRAM, "доставка", data={"sent": 2})
        self.flush()
        (inc,) = [i for i in mon.incidents(now=NOW) if i["type"] == "system_error"]
        self.assertEqual(inc["state"], "recovered")

    def test_unknown_where_has_no_component_fallback(self):
        self.event(3, tm.EVENT_SYSTEM_ERROR, "ERROR", tm.COMPONENT_AI, "new_worker: RuntimeError",
                   data={"where": "new_worker", "error": "RuntimeError"})
        self.event(1, tm.EVENT_AI_QUERY, "INFO", tm.COMPONENT_AI, "ok",
                   data={"outcome": "ok", "provider_called": True, "purpose": "normalize"})
        self.flush()
        (inc,) = [i for i in mon.incidents(now=NOW) if i["type"] == "system_error"]
        self.assertTrue(inc["open"])

    def test_component_without_success_signal_becomes_quiet_not_recovered(self):
        self.event(30, tm.EVENT_SYSTEM_ERROR, "ERROR", tm.COMPONENT_SYSTEM, "http_handler: KeyError",
                   data={"where": "http_handler", "error": "KeyError"})
        self.event(29, tm.EVENT_SEARCH_QUERY, "INFO", tm.COMPONENT_SEARCH, "q", data={"outcome": "found"})
        self.flush()
        (inc,) = [i for i in mon.incidents(now=NOW) if i["type"] == "system_error"]
        self.assertEqual((inc["state"], inc["open"], inc["recovered_at"]), ("quiet", False, None))
        (inc,) = [i for i in mon.incidents(now=NOW - datetime.timedelta(hours=20)) if i["type"] == "system_error"]
        self.assertTrue(inc["open"])

    def test_events_filters(self):
        self.event(2, tm.EVENT_HTTP_REQUEST, "WARNING", tm.COMPONENT_HTTP, "w", shop="A", scan_id="x")
        self.event(1, tm.EVENT_SCAN_CATEGORY, "INFO", tm.COMPONENT_SCRAPER, "i", shop="B", scan_id="y")
        self.flush()
        self.assertEqual([e["message"] for e in mon.events(severity="WARNING")], ["w"])
        self.assertEqual([e["message"] for e in mon.events(shop="B")], ["i"])
        self.assertEqual([e["message"] for e in mon.events(scan_id="x")], ["w"])
        self.assertEqual(len(mon.events(limit=10**6)), 2)


class HypothesisTest(unittest.TestCase):
    def test_known_and_component_hypotheses(self):
        self.assertIn("429", mon.hypothesis("shop:429 HTTP 429"))
        self.assertIn("timeout", mon.hypothesis("host:timeout"))
        self.assertIn("KeyError", mon.hypothesis("http_handler:KeyError x", "system_error", "http_handler:KeyError"))
        self.assertIn("Поиск", mon.hypothesis("Поиск", "search_query", "Поиск"))
        self.assertIn("не классифицирована", mon.hypothesis("что-то новое"))


class ShopDetailTest(DbCase):
    def test_detail_with_missing_data_and_categories(self):
        cats = [{"name": "Смартфоны", "url": "https://a/1"}, {"name": "Очень длинное название категории " * 3, "url": "https://a/2"}]
        d = mon.shop_detail("a", "A", cats, True, NOW)
        self.assertEqual(d["status"], "unknown")
        self.assertEqual([c["quality"] for c in d["categories"]], ["unknown", "unknown"])
        self.assertEqual((d["history"], d["http"], d["events"]), ([], {}, []))


class AccessTest(unittest.IsolatedAsyncioTestCase):
    """Проверка доступа: гость 401, пользователь 403, администратор 200; страница без данных открывается."""

    async def test_admin_only(self):
        import auth
        import web.server as server
        from aiohttp.test_utils import TestServer
        from test_support import BrowserTestClient as TestClient
        database.init_db()
        database.upsert_telegram_user({"id": 7001, "first_name": "User"})
        database.upsert_telegram_user({"id": 7002, "first_name": "Admin"})
        user, admin = database.create_session(7001), database.create_session(7002)
        app = server.create_app()
        app.cleanup_ctx.clear()
        paths = ["/api/admin/monitoring", "/api/admin/monitoring/shop/kaspi", "/api/admin/monitoring/events",
                 "/api/admin/monitoring/incidents"]
        with patch.object(auth, "ADMIN_TELEGRAM_IDS", {7002}):
            async with TestClient(TestServer(app)) as client:
                page = await client.get("/monitoring")
                self.assertEqual(page.status, 200)
                html = await page.text()
                # Страница — только оболочка: данные магазинов приходят из API только администратору
                for _cls, _cats, name in server.SHOP_REGISTRY.values():
                    self.assertNotIn(name, html)
                for path in paths:
                    self.assertEqual((await client.get(path)).status, 401, path)
                client.session.cookie_jar.update_cookies({auth.SESSION_COOKIE: user})
                for path in paths:
                    self.assertEqual((await client.get(path)).status, 403, path)
                client.session.cookie_jar.update_cookies({auth.SESSION_COOKIE: admin})
                for path in paths:
                    self.assertEqual((await client.get(path)).status, 200, path)
                body = await (await client.get("/api/admin/monitoring")).json()
                self.assertEqual(len(body["shops"]), len(server.SHOP_REGISTRY))
                self.assertEqual((await client.get("/api/admin/monitoring/shop/nope")).status, 404)
                inc = await (await client.get("/api/admin/monitoring/incidents")).json()
                self.assertEqual(set(inc), {"incidents", "total", "open_total"})


if __name__ == "__main__":
    unittest.main()
