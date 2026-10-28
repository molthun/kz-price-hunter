"""Диагностический снимок: доступ, состав и выводы о проблемах. Без сети."""
from tests import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import datetime
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp.test_utils import AioHTTPTestCase

import config
import diagnostics
from web import server

TOKEN = "диагностический-токен-для-тестов"


class DiagnosticsAccessTest(AioHTTPTestCase):
    async def get_application(self):
        app = server.create_app()
        app.cleanup_ctx.clear()
        return app

    async def test_hidden_without_token(self):
        """Без токена страницы не существует — не 403, а именно 404."""
        with patch.object(config, "DIAG_TOKEN", TOKEN):
            for path in ("/api/diag", "/diag"):
                resp = await self.client.get(path)
                self.assertEqual(resp.status, 404, path)

    async def test_hidden_when_token_not_configured(self):
        """Пустой DIAG_TOKEN выключает диагностику целиком, а не открывает её всем."""
        with patch.object(config, "DIAG_TOKEN", ""):
            resp = await self.client.get("/api/diag", headers={"X-Diag-Token": ""})
            self.assertEqual(resp.status, 404)

    async def test_wrong_token_is_rejected(self):
        with patch.object(config, "DIAG_TOKEN", TOKEN):
            resp = await self.client.get("/api/diag", headers={"X-Diag-Token": TOKEN + "x"})
            self.assertEqual(resp.status, 404)

    async def test_snapshot_for_agent(self):
        with patch.object(config, "DIAG_TOKEN", TOKEN):
            resp = await self.client.get("/api/diag", headers={"X-Diag-Token": TOKEN})
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        for key in ("status", "problems", "service", "runtime", "database", "queue", "scan", "errors"):
            self.assertIn(key, data)
        self.assertIn(data["status"], ("ok", "warning", "critical"))
        self.assertIsInstance(data["problems"], list)
        self.assertIsNotNone(data["runtime"]["event_loop_lag_ms"])
        self.assertEqual(data["runtime"]["thread_pool_size"], config.worker_thread_count())
        self.assertIn("journal_mode", data["database"])

    async def test_page_renders_without_scripts(self):
        with patch.object(config, "DIAG_TOKEN", TOKEN):
            resp = await self.client.get("/diag", headers={"X-Diag-Token": TOKEN})
        self.assertEqual(resp.status, 200)
        body = await resp.text()
        self.assertIn("Диагностика KZ Price Hunter", body)
        self.assertNotIn("<script", body, "страница должна открываться без скриптов и внешних файлов")
        self.assertEqual(resp.headers.get("X-Robots-Tag"), "noindex, nofollow")


class DatabaseSnapshotTest(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.db = Path(self._dir.name) / "prices.db"
        conn = sqlite3.connect(self.db)
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("CREATE TABLE products (id INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO products (id) VALUES (1), (2)")
        conn.execute("CREATE TABLE schema_metadata (name TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO schema_metadata VALUES ('schema_version', '5')")
        conn.execute("""CREATE TABLE telemetry_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT, timestamp TEXT, type TEXT,
            severity TEXT, component TEXT, message TEXT, created_at REAL)""")
        conn.commit()
        conn.close()

    def _connect(self):
        return sqlite3.connect(self.db, timeout=5)

    def test_reports_mode_and_rows(self):
        snap = diagnostics.database_snapshot(self._connect, self.db)
        self.assertEqual(str(snap["journal_mode"]).lower(), "wal")
        self.assertEqual(snap["rows"]["products"], 2)
        self.assertEqual(snap["schema_version"], "5")
        self.assertIsNotNone(snap["size_mb"])

    def test_missing_tables_do_not_break_snapshot(self):
        """У старой базы части таблиц нет — снимок обязан это пережить."""
        snap = diagnostics.database_snapshot(self._connect, self.db)
        self.assertIsNone(snap["rows"]["offers"])

    def test_unreachable_database_is_reported(self):
        def broken():
            raise sqlite3.OperationalError("database is locked")
        snap = diagnostics.database_snapshot(broken, self.db)
        self.assertIn("database is locked", snap["error"])

    def test_recent_errors_are_collected(self):
        """Окно отбирается по индексированной колонке timestamp, поэтому метки — относительные."""
        now = datetime.datetime.now(datetime.timezone.utc)

        def stamp(minutes):
            return (now - datetime.timedelta(minutes=minutes)).strftime(diagnostics.EVENT_TIMESTAMP_FORMAT)

        conn = self._connect()
        conn.executemany(
            "INSERT INTO telemetry_events (timestamp, type, severity, component, message, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(stamp(1), "scan_failed", "ERROR", "scraper", "Zeta упал", 0),
             (stamp(2), "db_locked", "CRITICAL", "system", "database is locked", 0),
             (stamp(3), "ok", "INFO", "system", "всё хорошо", 0),
             (stamp(60 * 25), "old", "ERROR", "scraper", "вчерашняя", 0)])
        conn.commit()
        conn.close()
        errors = diagnostics.recent_errors(self._connect, hours=1)
        self.assertEqual(errors["total"], 2, "берутся только ошибки за окно, без INFO и вчерашних")
        self.assertEqual(errors["by_component"], {"scraper": 1, "system": 1})
        self.assertEqual(errors["last"][0]["message"], "Zeta упал")

    def test_recent_errors_use_the_timestamp_index(self):
        """Снимок не должен читать всю телеметрию: за 30 дней там сотни тысяч строк."""
        conn = self._connect()
        conn.execute("CREATE INDEX idx_telemetry_events_ts ON telemetry_events(timestamp, type)")
        plan = " ".join(str(r[-1]) for r in conn.execute(
            "EXPLAIN QUERY PLAN SELECT timestamp FROM telemetry_events "
            "WHERE timestamp >= ? AND severity IN ('ERROR', 'CRITICAL') "
            "ORDER BY timestamp DESC LIMIT 20", ("2026-01-01T00:00:00.000Z",)))
        conn.close()
        self.assertIn("idx_telemetry_events_ts", plan)
        self.assertNotIn("TEMP B-TREE", plan, "сортировка должна идти по индексу")


class ProblemDetectionTest(unittest.TestCase):
    def _snapshot(self, **over):
        base = {"database": {"journal_mode": "wal"},
                "runtime": {"event_loop_lag_ms": 10.0},
                "queue": {"size": 5, "worker_running": True},
                "errors": {"total": 0, "by_component": {}},
                "service": {"uptime_seconds": 10000}}
        base.update(over)
        return base

    def _codes(self, **over):
        return {p["code"] for p in diagnostics.detect_problems(self._snapshot(**over))}

    def test_healthy_snapshot_has_no_problems(self):
        self.assertEqual(self._codes(), set())

    def test_database_outside_wal_is_critical(self):
        problems = diagnostics.detect_problems(self._snapshot(database={"journal_mode": "delete"}))
        self.assertEqual(problems[0]["code"], "db_not_wal")
        self.assertEqual(problems[0]["severity"], "critical")
        self.assertIn("ждут писателей", problems[0]["meaning"])

    def test_unreachable_database_is_critical(self):
        self.assertIn("db_unreachable", self._codes(database={"error": "OperationalError: locked"}))

    def test_loop_lag_levels(self):
        self.assertIn("loop_lag", self._codes(runtime={"event_loop_lag_ms": 300.0}))
        self.assertIn("loop_stalled", self._codes(runtime={"event_loop_lag_ms": 5000.0}))

    def test_queue_backlog_and_overflow(self):
        self.assertIn("queue_backlog", self._codes(queue={"size": 250, "worker_running": True}))
        self.assertIn("queue_overflow", self._codes(queue={"size": 5000, "worker_running": True}))

    def test_stopped_writer_is_critical(self):
        self.assertIn("writer_down", self._codes(queue={"size": 0, "worker_running": False}))

    def test_error_rate_names_the_worst_component(self):
        problems = diagnostics.detect_problems(
            self._snapshot(errors={"total": 50, "by_component": {"scraper": 10, "system": 40}}))
        self.assertIn("system", problems[0]["problem"])

    def test_recent_restart_is_only_a_note(self):
        problems = diagnostics.detect_problems(self._snapshot(service={"uptime_seconds": 30}))
        self.assertEqual([p["severity"] for p in problems], ["info"])


if __name__ == "__main__":
    unittest.main()
