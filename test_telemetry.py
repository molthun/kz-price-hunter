"""P01 Telemetry Foundation: агрегация HTTP, p95, очистка данных, retention, fail-open, scan_id."""
import asyncio
import datetime
import json
import os
import random
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


MARKERS = ("FAKE_PASSWORD", "FAKE_SIGNATURE", "FAKE_COOKIE", "FAKE_AUTH", "FAKE_TOKEN", "alice")


class AuditA01SecretsTest(TelemetryDbCase):
    """A01: сырой текст ошибок и чувствительные поля не попадают ни в память, ни в БД."""

    def assert_clean(self, text):
        for marker in MARKERS:
            self.assertNotIn(marker, text)

    def all_event_text(self):
        mem = " ".join((e["message"] or "") + (e["data_json"] or "") for e in self.t.get_recent_events())
        self.t.flush()
        db = " ".join((r["message"] or "") + (r["data_json"] or "")
                      for r in self.rows("SELECT message, data_json FROM telemetry_events"))
        return mem, db

    def test_codex_repro_direct_error_text(self):
        url = "https://alice:FAKE_PASSWORD@shop.example/api?signature=FAKE_SIGNATURE"
        self.t.record_http_metric("shop.example", "GET", 0, 10, error="ConnectionError: " + url, url=url)
        for text in self.all_event_text():
            self.assert_clean(text)
        (e,) = self.rows("SELECT data_json FROM telemetry_events")
        data = json.loads(e["data_json"])
        self.assertEqual((data["host"], data["error"], data["kind"]), ("shop.example", "ConnectionError", "connection"))

    def test_transport_exception_through_limiter(self):
        url = "https://alice:FAKE_PASSWORD@shop.example/api?signature=FAKE_SIGNATURE&page=2"

        def boom():
            raise ConnectionError(f"Failed {url} Cookie: sid=FAKE_COOKIE Authorization: Bearer FAKE_AUTH")

        with self.assertRaises(ConnectionError):
            http._limited("GET", url, boom)
        for text in self.all_event_text():
            self.assert_clean(text)
        (a,) = self.agg("hour", "shop.example")
        self.assertEqual((a["errors"], a["connection_errors"]), (1, 1))

    def test_nested_payload_and_message(self):
        payload = {
            "shop_key": "kaspi",
            "headers": {"Cookie": "sid=FAKE_COOKIE", "Authorization": "Bearer FAKE_AUTH", "Accept": "text/html"},
            "pages": [{"X-Amz-Signature": "FAKE_SIGNATURE", "api_key": "FAKE_TOKEN"}],
            "note": "see https://alice:FAKE_PASSWORD@h.example/x?token=FAKE_TOKEN&q=1 and Set-Cookie: a=FAKE_COOKIE",
            "deep": {"a": {"b": {"c": {"d": {"e": {"f": {"g": "FAKE_TOKEN"}}}}}}},
        }
        self.t.record_event("x", "INFO", "system", "GET https://alice:FAKE_PASSWORD@h.example/?sig=FAKE_SIGNATURE",
                            data=payload)
        for text in self.all_event_text():
            self.assert_clean(text)
        data = json.loads(self.rows("SELECT data_json FROM telemetry_events")[0]["data_json"])
        self.assertEqual(data["shop_key"], "kaspi")  # не секрет: полезные поля остаются
        self.assertEqual(data["headers"]["Accept"], "text/html")
        self.assertIn("q=1", data["note"])

    def test_serialization_failure_is_safe(self):
        class Evil:
            def __str__(self):
                raise RuntimeError("FAKE_TOKEN")

        out = tm.sanitize_payload({"x": Evil(), "y": "https://alice:FAKE_PASSWORD@h/"})
        json.loads(out)
        self.assert_clean(out)


class AuditA02SizeTest(unittest.TestCase):
    """A02: итоговый UTF-8 JSON валиден и не больше лимита для любых символов."""

    def test_limit_unicode_escapes_controls(self):
        for value in ("😀" * 10000, "кириллица" * 2000, '"' * 9000, "\\" * 9000, "\x01\n\t" * 5000,
                      '"\\😀ж\x00' * 3000, "a" * 5000):
            for data in ({"x": value}, [value, value], value):
                out = tm.sanitize_payload(data)
                json.loads(out)
                self.assertLessEqual(len(out.encode("utf-8")), tm.MAX_DATA_JSON_BYTES)
        small = tm.sanitize_payload({"x": "ж" * 10})
        self.assertEqual(json.loads(small), {"x": "ж" * 10})

    def test_codex_repro_emoji(self):
        self.assertLessEqual(len(tm.sanitize_payload({"x": "😀" * 10000}).encode()), 4096)


class AuditA03ReservoirTest(TelemetryDbCase):
    """A03: p95 не зависит систематически от порядка и от переполнения буфера до flush."""

    def p95_for(self, values, host, flush_every=None):
        for i, v in enumerate(values, 1):
            self.t.record_http_metric(host, "GET", 200, v)
            if flush_every and i % flush_every == 0:
                self.t.flush()
        self.t.flush()
        (a,) = self.agg("hour", host)
        self.assertEqual(a["total_requests"], len(values))
        return a["latency_p95_ms"]

    def test_codex_repro_slow_tail_after_2000(self):
        random.seed(42)
        values = [1.0] * 2000 + [1000.0] * 1000
        self.assertEqual(self.p95_for(values, "slow-tail.example"), 1000.0)
        self.assertEqual(self.p95_for(list(reversed(values)), "fast-tail.example"), 1000.0)
        self.assertEqual(self.p95_for(values, "flushes.example", flush_every=700), 1000.0)

    def test_tail_fraction_estimated_without_order_bias(self):
        # 3% медленных: p95 должен оставаться быстрым независимо от места хвоста в потоке
        random.seed(7)
        values = [10.0] * 9700 + [1000.0] * 300
        for host, seq, every in (("a.example", values, None), ("b.example", values[::-1], None),
                                 ("c.example", values, 1234)):
            self.assertEqual(self.p95_for(seq, host, every), 10.0, host)

    def test_memory_bounded(self):
        for i in range(5000):
            self.t.record_http_metric("m.example", "GET", 200, float(i))
        self.assertTrue(all(len(a.latencies) <= tm.MAX_SAMPLES_PER_BUCKET for a in self.t._pending_http.values()))
        with patch.object(tm, "MAX_PENDING_HTTP_KEYS", 3):
            t = tm.TelemetryService()
            for i in range(5):
                t.record_http_metric(f"h{i}.example", "GET", 200, 1.0)
            self.assertLessEqual(len(t._pending_http), 3)
            self.assertGreater(t.stats["dropped_http"], 0)

    def test_merge_reservoirs_proportional(self):
        rng = random.Random(1)
        merged = tm.merge_reservoirs([1.0] * 500, 9000, [2.0] * 500, 1000, 500, rng)
        self.assertEqual(len(merged), 500)
        self.assertTrue(20 <= merged.count(2.0) <= 80)  # ожидание 50 (10%)
        self.assertEqual(sorted(tm.merge_reservoirs([1.0, 2.0], 2, [], 0, 500, rng)), [1.0, 2.0])


class AuditA05HttpDiagnosticsTest(TelemetryDbCase):
    """A05: коды различимы, неуспешные ответы связаны со scan_id и категорией."""

    def test_page_200_then_403_linked_to_scan_and_category(self):
        trace = {}
        tokens = [(tm.current_scan_id, tm.current_scan_id.set("scan-a05")),
                  (tm.current_category, tm.current_category.set("Смартфоны")),
                  (tm.current_shop, tm.current_shop.set("Shop")),
                  (tm.current_http_trace, tm.current_http_trace.set(trace))]
        try:
            http._limited("GET", "https://a05.example/c?page=1", lambda: ok())
            http._limited("GET", "https://a05.example/c?page=2&token=FAKE_TOKEN", lambda: ok(403, b"no"))
        finally:
            for var, token in reversed(tokens):
                var.reset(token)
        self.assertEqual(trace, {"requests": 2, "bytes": 12, "status_codes": {"200": 1, "403": 1}})
        self.t.flush()
        (a,) = self.agg("hour", "a05.example")
        self.assertEqual(json.loads(a["status_codes"]), {"200": 1, "403": 1})
        self.assertEqual(a["total_requests"], 2)
        (e,) = self.rows("SELECT scan_id, category, shop, severity, data_json FROM telemetry_events")
        self.assertEqual((e["scan_id"], e["category"], e["shop"], e["severity"]),
                         ("scan-a05", "Смартфоны", "Shop", "WARNING"))
        data = json.loads(e["data_json"])
        self.assertEqual(data["status"], 403)
        self.assertIn("page=2", data["url"])
        self.assertNotIn("FAKE_TOKEN", e["data_json"])

    def test_401_403_404_500_distinguished(self):
        for code in (401, 403, 404, 500, 404):
            http._limited("GET", "https://codes.example/", lambda code=code: ok(code))
        self.t.flush()
        (a,) = self.agg("day", "codes.example")
        self.assertEqual(json.loads(a["status_codes"]), {"401": 1, "403": 1, "404": 2, "500": 1})
        self.assertEqual((a["status_4xx"], a["status_5xx"]), (4, 1))
        sev = sorted((json.loads(r["data_json"])["status"], r["severity"])
                     for r in self.rows("SELECT severity, data_json FROM telemetry_events"))
        self.assertEqual(sev, [(401, "WARNING"), (403, "WARNING"), (404, "WARNING"), (404, "WARNING"),
                               (500, "ERROR")])

    def test_diagnostic_events_rate_limited_counters_exact(self):
        for _ in range(tm.MAX_HTTP_DIAG_EVENTS_PER_MINUTE + 5):
            http._limited("GET", "https://flood.example/", lambda: ok(404))
        self.t.flush()
        self.assertLessEqual(len(self.rows("SELECT * FROM telemetry_events")), tm.MAX_HTTP_DIAG_EVENTS_PER_MINUTE)
        (a,) = self.agg("hour", "flood.example")
        self.assertEqual(json.loads(a["status_codes"]), {"404": tm.MAX_HTTP_DIAG_EVENTS_PER_MINUTE + 5})

    def test_existing_table_gets_status_codes_column(self):
        conn = sqlite3.connect(self.db)
        with conn:
            conn.execute("ALTER TABLE telemetry_http_aggregates DROP COLUMN status_codes")
        conn.close()
        with database.get_connection() as c:
            database._create_schema(c.cursor())
            c.commit()
        self.assertIn("status_codes", {r["name"] for r in self.rows("PRAGMA table_info(telemetry_http_aggregates)")})


class AuditA04ScanOutcomeTest(unittest.IsolatedAsyncioTestCase):
    """A04: исход обхода — completed / failed / cancelled / lease_lost; состояние очищается всегда."""

    async def run_scan(self, body, keeper=None, cancel_after=None):
        import web.server as server
        svc = tm.TelemetryService()

        async def default_keeper(work):
            await asyncio.sleep(3600)

        patches = [patch.object(tm, "telemetry", svc),
                   patch.object(server, "acquire_scheduler_lease", return_value=True),
                   patch.object(server, "release_scheduler_lease"),
                   patch.object(server, "_scan_task_body", body),
                   patch.object(server, "_keep_scheduler_lease", keeper or default_keeper)]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in reversed(patches)])
        server.scan_state["is_running"] = False
        server.scan_state["error"] = None
        task = asyncio.create_task(server._do_scan_task(["x"], scan_type="manual"))
        if cancel_after is not None:
            await asyncio.sleep(cancel_after)
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        self.assertFalse(server.scan_state["is_running"])
        server.release_scheduler_lease.assert_called()
        self.assertIsNone(tm.current_scan_id.get())
        (end,) = [e for e in svc.get_recent_events() if e["type"] == tm.EVENT_SCAN_END]
        return json.loads(end["data_json"])["outcome"], end["severity"], task

    async def test_completed(self):
        async def body(*a):
            pass
        outcome, severity, _ = await self.run_scan(body)
        self.assertEqual((outcome, severity), ("completed", "INFO"))

    async def test_failed_body(self):
        import web.server as server

        async def body(*a):
            server.scan_state["error"] = "Ошибка сканирования (ValueError)"
        outcome, severity, _ = await self.run_scan(body)
        self.assertEqual((outcome, severity), ("failed", "ERROR"))

    async def test_cancelled_is_not_success(self):
        async def body(*a):
            await asyncio.sleep(3600)
        outcome, severity, task = await self.run_scan(body, cancel_after=0.05)
        self.assertTrue(task.cancelled())
        self.assertEqual((outcome, severity), ("cancelled", "WARNING"))

    async def test_lease_lost(self):
        import web.server as server

        async def body(*a):
            await asyncio.sleep(3600)

        async def losing_keeper(work):
            await asyncio.sleep(0.05)
            server._lease_state["lost"] = True
            work.cancel()
        outcome, severity, task = await self.run_scan(body, keeper=losing_keeper)
        self.assertFalse(task.cancelled())
        self.assertEqual((outcome, severity), ("lease_lost", "WARNING"))


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
