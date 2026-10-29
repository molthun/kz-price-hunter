from tests import test_support  # noqa: F401
"""Регрессии 6.5.2: телеметрия не переписывает выборку строками, WAL ограничен и виден в мониторинге.

На проде 6.5.1 (NAS с одним HDD) telemetry_http_samples дошла до rowid 609 млн: каждый сброс
раз в 10 с на каждый хост и бакет удалял выборку задержек и вставлял её заново, до 500 строк.
WAL при базе 2,2 ГБ — 561 МБ.
"""
import datetime
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database
import telemetry as tm


class _TelemetryCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "prices.db"
        self.patch = patch.object(database, "DB_PATH", self.db)
        self.patch.start()
        with database.get_connection() as conn:
            database._create_schema(conn.cursor())
            conn.commit()
        self.t = tm.TelemetryService()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def q(self, sql, *args):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()

    def record(self, n, host="h.example", base=10.0):
        for i in range(n):
            self.t.record_http_metric(host, "GET", 200, base + i % 300, shop="kaspi")


class SamplesInAggregateTest(_TelemetryCase):
    def test_flushes_do_not_rewrite_sample_rows(self):
        with patch.object(tm, "MAX_SAMPLES_PER_BUCKET", 500):
            for _ in range(12):  # выборка заполнена, дальше каждый сброс — как на проде во время обхода
                self.record(120)
                self.assertTrue(self.t.flush())
        # Ни одной строки в прежней таблице: при старой записи rowid рос на ~1000 за сброс
        self.assertEqual(self.q("SELECT count(*), max(rowid) FROM telemetry_http_samples")[0], (0, None))
        rows = self.q("SELECT bucket_type, total_requests, json_array_length(latency_samples), latency_p95_ms "
                      "FROM telemetry_http_aggregates WHERE host = 'h.example' ORDER BY bucket_type")
        self.assertEqual([r[0] for r in rows], ["day", "hour", "minute"])
        for bucket_type, total, kept, p95 in rows:
            self.assertEqual(total, 1440)
            self.assertEqual(kept, 500, bucket_type)
            self.assertTrue(110 <= p95 <= 129, p95)  # значения 10…129

    def test_legacy_rows_move_into_aggregate_once(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        start = now.strftime(tm.BUCKET_FORMATS["hour"])
        conn = sqlite3.connect(self.db)
        with conn:
            conn.execute("INSERT INTO telemetry_http_aggregates(bucket_type, bucket_start, host, shop, total_requests) "
                         "VALUES ('hour', ?, 'h.example', 'kaspi', 3)", (start,))
            conn.executemany("INSERT INTO telemetry_http_samples(bucket_type, bucket_start, host, shop, latency_ms) "
                             "VALUES ('hour', ?, 'h.example', 'kaspi', ?)", [(start, v) for v in (900.0, 901.0, 902.0)])
            # Строка другого ключа не трогается
            conn.execute("INSERT INTO telemetry_http_samples(bucket_type, bucket_start, host, shop, latency_ms) "
                         "VALUES ('hour', ?, 'other.example', '', 5.0)", (start,))
        conn.close()
        self.record(2, base=10.0)
        self.assertTrue(self.t.flush())
        (samples,) = self.q("SELECT latency_samples FROM telemetry_http_aggregates "
                            "WHERE bucket_type = 'hour' AND host = 'h.example'")[0]
        import json
        self.assertEqual(sorted(json.loads(samples)), [10.0, 11.0, 900.0, 901.0, 902.0])
        self.assertEqual(self.q("SELECT host FROM telemetry_http_samples"), [("other.example",)])
        # Повторный сброс берёт выборку из агрегата, а не ищет прежние строки
        self.record(1, base=20.0)
        self.assertTrue(self.t.flush())
        (n,) = self.q("SELECT json_array_length(latency_samples) FROM telemetry_http_aggregates "
                      "WHERE bucket_type = 'hour' AND host = 'h.example'")[0]
        self.assertEqual(n, 6)

    def test_prune_clears_old_samples_but_keeps_aggregate_and_p95(self):
        now = datetime.datetime(2026, 10, 29, 12, 0, tzinfo=datetime.timezone.utc)
        old = (now - datetime.timedelta(days=10)).strftime(tm.BUCKET_FORMATS["hour"])
        fresh = (now - datetime.timedelta(days=1)).strftime(tm.BUCKET_FORMATS["hour"])
        conn = sqlite3.connect(self.db)
        with conn:
            for start in (old, fresh):
                conn.execute("INSERT INTO telemetry_http_aggregates(bucket_type, bucket_start, host, latency_p95_ms, "
                             "latency_samples) VALUES ('hour', ?, 'h', 42.0, '[1.0, 2.0]')", (start,))
        conn.close()
        deleted = self.t.prune(now=now)
        self.assertEqual(deleted["samples"], 1)
        rows = dict(self.q("SELECT bucket_start, latency_samples FROM telemetry_http_aggregates"))
        self.assertIsNone(rows[old])
        self.assertEqual(rows[fresh], "[1.0, 2.0]")
        self.assertEqual(self.q("SELECT count(*) FROM telemetry_http_aggregates WHERE latency_p95_ms = 42.0")[0][0], 2)


class WalLimitTest(_TelemetryCase):
    def test_connections_limit_wal_size(self):
        with database.get_connection() as conn:
            self.assertEqual(conn.execute("PRAGMA journal_size_limit").fetchone()[0], database.WAL_SIZE_LIMIT_BYTES)
        conn = self.t._connect()
        try:
            self.assertEqual(conn.execute("PRAGMA journal_size_limit").fetchone()[0], database.WAL_SIZE_LIMIT_BYTES)
        finally:
            conn.close()

    def test_system_section_reports_and_warns_on_large_wal(self):
        import monitoring
        database.ensure_journal_mode()
        keep = sqlite3.connect(self.db)  # открытый читатель не даёт WAL исчезнуть до замера
        try:
            keep.execute("BEGIN")
            keep.execute("SELECT count(*) FROM products").fetchone()
            with database.get_connection() as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS wal_fill (x TEXT)")
                conn.executemany("INSERT INTO wal_fill VALUES (?)", [("x" * 1000,) for _ in range(300)])
                conn.commit()
            with patch.object(monitoring, "_conn", lambda: database.get_connection()):
                ok = monitoring.system_section()
                with patch.object(database, "WAL_WARN_BYTES", 1024):
                    warned = monitoring.system_section()
        finally:
            keep.rollback()
            keep.close()
        self.assertGreater(ok["wal_bytes"], 1024)
        self.assertEqual(ok["db_size_bytes"], ok["db_file_bytes"] + ok["wal_bytes"])
        self.assertNotIn("WAL", ok["reason"])
        self.assertEqual(warned["status"], monitoring.DEGRADED)
        self.assertIn("WAL", warned["reason"])


if __name__ == "__main__":
    unittest.main()
