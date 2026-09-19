"""P01 Telemetry Foundation: агрегация HTTP, p95, очистка данных, retention, fail-open, scan_id."""
import asyncio
import datetime
import os
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import database
import telemetry as tm
from config import DB_PATH
from scrapers import http


class _Clock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class TelemetryDbCase(unittest.TestCase):
    """Временная БД со схемой; свой экземпляр TelemetryService подменяет синглтон в http."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "prices.db")
        self.patches = [patch.object(database, "DB_PATH", type(DB_PATH)(self.db))]
        for p in self.patches:
            p.start()
        with database.get_connection() as conn:
            database._create_schema(conn.cursor())
            conn.commit()
        self.t = tm.TelemetryService()
        self.patches.append(patch.object(http, "telemetry", self.t))
        self.patches[-1].start()
        self.clock = _Clock()
        for p in (patch.object(http, "_clock", self.clock), patch.object(http, "_sleep", self.clock.sleep)):
            self.patches.append(p)
            p.start()
        http.reset_limiter()

    def tearDown(self):
        http.reset_limiter()
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def rows(self, sql, *args):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(sql, args)]
        finally:
            conn.close()

    def agg(self, bucket_type="hour", host=None):
        sql = "SELECT * FROM telemetry_http_aggregates WHERE bucket_type = ?"
        args = [bucket_type]
        if host:
            sql += " AND host = ?"
            args.append(host)
        return self.rows(sql, *args)


def ok(status=200, body=b"x" * 10, headers=None):
    return Mock(status_code=status, content=body, headers=headers or {})


class P95Test(unittest.TestCase):
    def test_exact_percentile_not_average_of_p95(self):
        self.assertEqual(tm.calculate_p95([]), 0.0)
        self.assertEqual(tm.calculate_p95([7]), 7.0)
        self.assertEqual(tm.calculate_p95(list(range(1, 101))), 95.05)
        # Два часа: p95 = 10 и 1000; общий p95 по объединению, а не среднее 505
        hour_a = [10.0] * 100
        hour_b = [10.0] * 95 + [1000.0] * 5
        self.assertEqual(tm.calculate_p95(hour_a), 10.0)
        self.assertEqual(tm.calculate_p95(hour_a + hour_b), 10.0)
        self.assertNotEqual(tm.calculate_p95(hour_a + hour_b),
                            (tm.calculate_p95(hour_a) + tm.calculate_p95(hour_b)) / 2)


class SanitizeTest(unittest.TestCase):
    def test_url_secrets_removed(self):
        url = "https://user:pw@api.example/p?q=iphone&token=abc123&api_key=zzz&page=2"
        clean = tm.sanitize_url(url)
        for secret in ("abc123", "zzz", "pw@"):
            self.assertNotIn(secret, clean)
        self.assertIn("q=iphone", clean)
        self.assertIn("page=2", clean)

    def test_payload_limited(self):
        big = tm.sanitize_payload({"x": "a" * 10000})
        self.assertLessEqual(len(big.encode()), tm.MAX_DATA_JSON_BYTES)
        self.assertIn("_truncated", big)

    def test_error_classification(self):
        self.assertEqual(tm.classify_error("Timeout: Operation timed out after 30000 ms"), "timeout")
        self.assertEqual(tm.classify_error("ConnectionError: Failed to connect"), "connection")
        self.assertEqual(tm.classify_error("ValueError: boom"), "other")
        self.assertIsNone(tm.classify_error(None))


class HttpAggregationTest(TelemetryDbCase):
    def test_200_200_429_counters_match_requests(self):
        responses = [ok(), ok(), ok(429, b"", {"Retry-After": "10"})]
        for r in responses:
            http._limited("GET", "https://shop.example/c?page=1", lambda r=r: r)
        self.assertTrue(self.t.flush())
        for bucket in ("minute", "hour", "day"):
            (a,) = self.agg(bucket, "shop.example")
            self.assertEqual(a["total_requests"], 3, bucket)
            self.assertEqual((a["status_2xx"], a["status_429"], a["status_4xx"]), (2, 1, 0))
            self.assertEqual(a["bytes_total"], 20)
            self.assertEqual(a["retry_after_max_s"], 10.0)
            self.assertEqual(a["shop"], "")
        events = self.rows("SELECT type, severity FROM telemetry_events")
        self.assertEqual(events, [{"type": tm.EVENT_HTTP_COOLDOWN, "severity": "WARNING"}])

        # Следующий запрос отклоняется паузой: это не запрос, а отдельный счётчик
        self.clock.now += 1
        http.MAX_COOLDOWN_WAIT_SECONDS, saved = 0, http.MAX_COOLDOWN_WAIT_SECONDS
        try:
            with self.assertRaises(http.HostCooldown):
                http._limited("GET", "https://shop.example/", Mock())
        finally:
            http.MAX_COOLDOWN_WAIT_SECONDS = saved
        self.t.flush()
        (a,) = self.agg("hour", "shop.example")
        self.assertEqual((a["total_requests"], a["cooldown_rejections"]), (3, 1))

    def test_200_403(self):
        http._limited("GET", "https://b.example/", lambda: ok())
        http._limited("GET", "https://b.example/", lambda: ok(403, b"denied"))
        self.t.flush()
        (a,) = self.agg("hour", "b.example")
        self.assertEqual((a["total_requests"], a["status_2xx"], a["status_4xx"], a["status_429"], a["errors"]),
                         (2, 1, 1, 0, 0))

    def test_timeout_and_connection_errors(self):
        class Timeout(Exception):
            pass

        def timeout():
            raise Timeout("Operation timed out")

        def refused():
            raise ConnectionError("Connection refused")

        for send in (timeout, refused):
            with self.assertRaises(Exception):
                http._limited("GET", "https://t.example/x?token=SECRET", send)
        # Слот освобождён после ошибок: лимитер продолжает работать
        http._limited("GET", "https://t.example/", lambda: ok())
        self.t.flush()
        (a,) = self.agg("hour", "t.example")
        self.assertEqual((a["total_requests"], a["errors"], a["timeouts"], a["connection_errors"], a["status_2xx"]),
                         (3, 2, 1, 1, 1))
        for e in self.rows("SELECT message, data_json FROM telemetry_events"):
            self.assertNotIn("SECRET", e["message"] + (e["data_json"] or ""))

    def test_latency_excludes_limiter_wait(self):
        # Ожидание паузы/слота в лимитере не входит в задержку запроса
        def real_sleep(seconds):
            self.clock.sleep(seconds)
            time.sleep(seconds)

        with patch.object(http, "MIN_INTERVAL_SECONDS", 0.2), patch.object(http, "_sleep", real_sleep):
            for _ in range(3):
                http._limited("GET", "https://slow.example/", lambda: ok())
        self.assertAlmostEqual(sum(self.clock.sleeps), 0.4)  # реальное ожидание в лимитере
        self.t.flush()
        (a,) = self.agg("hour", "slow.example")
        self.assertLess(a["latency_sum_ms"], 100.0)

    def test_stream_response_not_read(self):
        body = Mock()
        response = Mock(status_code=200, headers={"Content-Length": "123"})
        type(response).content = property(lambda _self: body.read())
        http._limited("GET", "https://feed.example/yml", lambda: response, stream=True)
        body.read.assert_not_called()
        self.t.flush()
        (a,) = self.agg("hour", "feed.example")
        self.assertEqual(a["bytes_total"], 123)

    def test_session_passes_stream_flag(self):
        captured = {}

        def fake_limited(method, url, send, stream=False):
            captured["stream"] = stream

        with patch.object(http, "_limited", fake_limited):
            http.Session().get("https://feed.example/", stream=True)
            self.assertTrue(captured["stream"])
            http.get("https://feed.example/")
            self.assertFalse(captured["stream"])

    def test_aggregates_merge_across_flushes_and_p95_from_samples(self):
        for i in range(1, 101):
            self.t.record_http_metric("p.example", "GET", 200, float(i))
            if i % 30 == 0:
                self.t.flush()
        self.t.flush()
        (a,) = self.agg("hour", "p.example")
        self.assertEqual(a["total_requests"], 100)
        self.assertEqual(a["latency_sum_ms"], 5050.0)
        self.assertEqual(a["latency_p95_ms"], 95.05)
        self.assertEqual(len(self.agg("hour")), 1)  # одна строка на бакет, а не на запрос

    def test_reservoir_bounded(self):
        with patch.object(tm, "MAX_SAMPLES_PER_BUCKET", 50):
            for i in range(400):
                self.t.record_http_metric("r.example", "GET", 200, float(i % 100))
                if i % 97 == 0:
                    self.t.flush()
            self.t.flush()
        n = self.rows("SELECT COUNT(*) AS n FROM telemetry_http_samples WHERE bucket_type='hour' AND host='r.example'")
        self.assertEqual(n[0]["n"], 50)
        (a,) = self.agg("hour", "r.example")
        self.assertEqual(a["total_requests"], 400)
        self.assertGreater(a["latency_p95_ms"], 50.0)

    def test_shop_from_context(self):
        token = tm.current_shop.set("Shop.kz")
        try:
            http._limited("GET", "https://s.example/", lambda: ok())
        finally:
            tm.current_shop.reset(token)
        self.t.flush()
        (a,) = self.agg("hour", "s.example")
        self.assertEqual(a["shop"], "Shop.kz")


class FailOpenTest(TelemetryDbCase):
    def test_broken_db_does_not_break_requests(self):
        http._limited("GET", "https://f.example/", lambda: ok())
        with patch.object(database, "DB_PATH", type(DB_PATH)(os.path.join(self.tmp.name, "missing", "x", "db"))):
            self.assertFalse(self.t.flush())
        self.assertEqual(self.t.stats["failed_flushes"], 1)

    def test_broken_telemetry_does_not_break_requests(self):
        broken = Mock()
        broken.record_http_metric.side_effect = RuntimeError("telemetry down")
        with patch.object(http, "telemetry", broken):
            response = http._limited("GET", "https://f.example/", lambda: ok())
        self.assertEqual(response.status_code, 200)

    def test_disabled_flag(self):
        with patch.dict(os.environ, {"TELEMETRY_ENABLED": "0"}):
            http._limited("GET", "https://off.example/", lambda: ok())
            self.assertIsNone(self.t.record_event("x", "INFO", "system", "m"))
        self.t.flush()
        self.assertEqual(self.agg("hour"), [])

    def test_pending_buffer_bounded(self):
        with patch.object(tm, "MAX_PENDING_EVENTS", 3):
            for i in range(5):
                self.t.record_event("x", "INFO", "system", f"m{i}")
        self.assertEqual(self.t.stats["dropped_events"], 2)
        self.t.flush()
        self.assertEqual(len(self.rows("SELECT * FROM telemetry_events")), 3)


class RetentionTest(TelemetryDbCase):
    def test_prune_per_bucket(self):
        now = datetime.datetime(2026, 9, 19, 12, 0, tzinfo=datetime.timezone.utc)
        conn = sqlite3.connect(self.db)
        with conn:
            def add_agg(bucket_type, days):
                start = (now - datetime.timedelta(days=days)).strftime(tm.BUCKET_FORMATS[bucket_type])
                conn.execute("INSERT INTO telemetry_http_aggregates(bucket_type, bucket_start, host) VALUES (?, ?, 'h')",
                             (bucket_type, start))
                conn.execute("INSERT INTO telemetry_http_samples(bucket_type, bucket_start, host, latency_ms) VALUES (?, ?, 'h', 1)",
                             (bucket_type, start))
            for bucket_type, days in (("minute", 8), ("minute", 1), ("hour", 91), ("hour", 30),
                                      ("day", 366), ("day", 100)):
                add_agg(bucket_type, days)
            for days in (31, 1):
                ts = (now - datetime.timedelta(days=days)).strftime(tm._EVENT_TS_FORMAT)
                conn.execute("INSERT INTO telemetry_events(event_id, timestamp, type, severity, component, message) "
                             "VALUES (?, ?, 't', 'INFO', 'c', 'm')", (f"e{days}", ts))
        conn.close()

        deleted = self.t.prune(now=now)
        # Выборки живут 7 дней во всех бакетах; агрегаты — по сроку своего бакета
        self.assertEqual(deleted, {"events": 1, "samples": 5, "minute": 1, "hour": 1, "day": 1})
        left = {(r["bucket_type"]) for r in self.rows("SELECT bucket_type FROM telemetry_http_aggregates")}
        self.assertEqual(left, {"minute", "hour", "day"})
        self.assertEqual([r["event_id"] for r in self.rows("SELECT event_id FROM telemetry_events")], ["e1"])
        # p95 в агрегате остаётся после удаления выборок
        self.assertEqual(len(self.rows("SELECT * FROM telemetry_http_samples")), 1)


class EventsTest(TelemetryDbCase):
    def test_context_and_recent_events(self):
        token = tm.current_scan_id.set("scan-1")
        try:
            self.t.record_event(tm.EVENT_SCAN_START, "info", tm.COMPONENT_SCHEDULER, "start",
                                data={"api_key": "sk-ant-api03-" + "x" * 40})
        finally:
            tm.current_scan_id.reset(token)
        self.t.flush()
        (row,) = self.rows("SELECT scan_id, severity, data_json FROM telemetry_events")
        self.assertEqual((row["scan_id"], row["severity"]), ("scan-1", "INFO"))
        self.assertNotIn("x" * 40, row["data_json"])
        fresh = tm.TelemetryService()  # пустой буфер: события читаются из БД
        self.assertEqual([e["scan_id"] for e in fresh.get_recent_events(scan_id="scan-1")], ["scan-1"])


class SchemaTest(unittest.TestCase):
    """Таблицы телеметрии аддитивны: создаются init_db без повышения schema_version (откат на 5.7.1 возможен)."""

    def _init(self, db, tmp):
        with patch.object(database, "DB_PATH", db), patch("config.DATA_DIR", type(DB_PATH)(tmp)):
            database.init_db()

    def _state(self, db):
        conn = sqlite3.connect(db)
        try:
            version = conn.execute("SELECT value FROM schema_metadata WHERE name='schema_version'").fetchone()[0]
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'telemetry_%'")}
            return version, tables
        finally:
            conn.close()

    def test_new_db_has_tables_and_version_5(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = type(DB_PATH)(os.path.join(tmp, "prices.db"))
            self._init(db, tmp)
            self._init(db, tmp)  # повторный запуск идемпотентен
            version, tables = self._state(db)
        self.assertEqual(database.SCHEMA_VERSION, 5)
        self.assertEqual(version, "5")
        self.assertEqual(tables, {"telemetry_events", "telemetry_http_aggregates", "telemetry_http_samples"})

    def test_existing_v5_db_gets_tables_without_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = type(DB_PATH)(os.path.join(tmp, "prices.db"))
            self._init(db, tmp)
            conn = sqlite3.connect(db)
            with conn:
                for t in ("telemetry_events", "telemetry_http_aggregates", "telemetry_http_samples"):
                    conn.execute(f"DROP TABLE {t}")
                conn.execute("""INSERT INTO products (id, shop, city, title, url, current_price, first_seen_price, min_price, max_price)
                                VALUES ('kaspi_1@astana', 'Kaspi', 'Астана', 'T', 'https://x', 1, 1, 1, 1)""")
            conn.close()
            self._init(db, tmp)
            version, tables = self._state(db)
            backups = list(type(DB_PATH)(tmp).glob("backups/*.db"))
        self.assertEqual(version, "5")
        self.assertEqual(len(tables), 3)
        self.assertEqual(backups, [])  # версия не менялась — бэкап перед миграцией не нужен


class ScanIdPropagationTest(unittest.IsolatedAsyncioTestCase):
    async def test_scan_id_reaches_tasks_and_threads(self):
        token = tm.current_scan_id.set("scan-xyz")
        try:
            async def child():
                return await asyncio.to_thread(tm.current_scan_id.get)
            seen = await asyncio.create_task(child())
        finally:
            tm.current_scan_id.reset(token)
        self.assertEqual(seen, "scan-xyz")


if __name__ == "__main__":
    unittest.main()
