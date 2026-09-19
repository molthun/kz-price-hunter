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


class IncidentTest(DbCase):
    def test_repeated_403_grouped_with_hypothesis_and_recovery(self):
        for h in (5, 4, 3):
            self.event(h, tm.EVENT_HTTP_REQUEST, "WARNING", tm.COMPONENT_HTTP, "HTTP 403 GET shop.example",
                       shop="Магазин", category=f"Кат {h}", scan_id=f"scan{h}",
                       data={"host": "shop.example", "status": 403})
        self.flush()
        (inc,) = mon.incidents(now=NOW)
        self.assertTrue(inc["open"])
        self.assertEqual((inc["count"], inc["scale"]["categories_count"], inc["scale"]["scans"]), (3, 3, 3))
        self.assertIn("блокир", inc["hypothesis"].lower())
        self.assertEqual(inc["scale"]["hosts"], ["shop.example"])
        first_id = inc["id"]
        # Успешная категория того же магазина после последнего появления — восстановление
        self.event(1, tm.EVENT_SCAN_CATEGORY, "INFO", tm.COMPONENT_SCRAPER, "ok", shop="Магазин",
                   data={"items_count": 10})
        self.flush()
        (inc,) = mon.incidents(now=NOW)
        self.assertFalse(inc["open"])
        self.assertIsNotNone(inc["recovered_at"])
        self.assertEqual(inc["id"], first_id)  # стабильный id

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

    def test_component_incident_recovers_after_quiet_period(self):
        for h in (3, 2.5):
            self.event(h, tm.EVENT_SYSTEM_ERROR, "ERROR", tm.COMPONENT_AI, "ai_normalize_worker: RuntimeError",
                       data={"where": "ai_normalize_worker", "error": "RuntimeError"})
        self.event(2, tm.EVENT_AI_QUERY, "INFO", tm.COMPONENT_AI, "ok", data={"outcome": "ok"})
        self.flush()
        (inc,) = [i for i in mon.incidents(now=NOW) if i["type"] == "system_error"]
        self.assertFalse(inc["open"])
        (inc,) = [i for i in mon.incidents(now=NOW - datetime.timedelta(hours=2.2)) if i["type"] == "system_error"]
        self.assertTrue(inc["open"])  # после ошибки прошло меньше часа тишины

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


if __name__ == "__main__":
    unittest.main()
