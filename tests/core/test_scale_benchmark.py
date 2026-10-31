"""
Tests for Scale Benchmarking and Storage Decision Engine (Stage 30).
Verifies:
1. run_scale_benchmark measurements: search p95, write latency, backup duration, file sizes.
2. evaluate_storage_decision: threshold evaluation (STAY_ON_SQLITE vs EVALUATE_POSTGRESQL).
3. get_current_scale_status: live database inspection and checkpoint progression.
4. HTTP API: GET /api/v2/admin/scale/summary role security and payload.
"""

from tests import test_support  # must be first
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from aiohttp.test_utils import make_mocked_request

from domain.scale_bench import (
    ScaleCheckpoint,
    evaluate_storage_decision,
    get_current_scale_status,
    run_scale_benchmark,
)
from repositories import init_schema_v2
from web.server import admin_scale_summary_handler


class TestScaleBenchmark(unittest.TestCase):
    def test_run_scale_benchmark_metrics(self):
        """Проверяет снятие метрик масштабирования на синтетическом каталоге."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            metrics = run_scale_benchmark(
                target_offers=500,
                checkpoint_name="test_500",
            )
            self.assertEqual(metrics.target_offers, 500)
            self.assertEqual(metrics.actual_offers, 500)
            self.assertTrue(metrics.total_products > 0)
            self.assertTrue(metrics.total_sellers > 0)
            self.assertTrue(metrics.total_channels > 0)
            self.assertTrue(metrics.search_p95_ms >= 0.0)
            self.assertTrue(metrics.write_latency_1k_ms >= 0.0)
            self.assertTrue(metrics.db_size_mb > 0.0)
            self.assertTrue(metrics.backup_duration_s >= 0.0)
            self.assertTrue(metrics.startup_ms >= 0.0)

    def test_evaluate_storage_decision_sqlite_sufficient(self):
        """Проверяет вердикт STAY_ON_SQLITE при штатных показателях SLA."""
        normal_cp = ScaleCheckpoint(
            name="100k",
            target_offers=100000,
            actual_offers=100000,
            total_products=20000,
            total_sellers=200,
            total_channels=400,
            search_p95_ms=18.5,
            search_avg_ms=10.2,
            write_latency_1k_ms=15.0,
            db_size_mb=120.0,
            fts_size_mb=15.0,
            price_history_size_mb=25.0,
            backup_duration_s=0.25,
            memory_mb=70.0,
            startup_ms=12.0,
        )
        dec = evaluate_storage_decision(normal_cp)
        self.assertEqual(dec.decision, "STAY_ON_SQLITE")
        self.assertTrue(dec.is_sqlite_sufficient)
        self.assertIn("SQLite полностью справляется", dec.summary)

    def test_evaluate_storage_decision_postgresql_required(self):
        """Проверяет переход в EVALUATE_POSTGRESQL при превышении порогов SLA."""
        degraded_cp = ScaleCheckpoint(
            name="5m",
            target_offers=5000000,
            actual_offers=5000000,
            total_products=500000,
            total_sellers=5000,
            total_channels=10000,
            search_p95_ms=145.0,  # > 100ms
            search_avg_ms=90.0,
            write_latency_1k_ms=620.0,  # > 500ms
            db_size_mb=12500.0,  # > 10GB
            fts_size_mb=2500.0,
            price_history_size_mb=3500.0,
            backup_duration_s=42.0,  # > 30s
            memory_mb=1200.0,
            startup_ms=450.0,
        )
        dec = evaluate_storage_decision(degraded_cp)
        self.assertEqual(dec.decision, "EVALUATE_POSTGRESQL")
        self.assertFalse(dec.is_sqlite_sufficient)
        self.assertTrue(any("Поисковая задержка p95" in r for r in dec.reasons))
        self.assertTrue(any("Задержка пакетной записи" in r for r in dec.reasons))
        self.assertTrue(any("Размер базы данных" in r for r in dec.reasons))
        self.assertTrue(any("Время создания консистентного бэкапа" in r for r in dec.reasons))

    def test_get_current_scale_status(self):
        """Проверяет сбор текущих показателей масштабирования базы данных."""
        conn = sqlite3.connect(":memory:")
        init_schema_v2(conn)
        status = get_current_scale_status(conn)
        self.assertIn("current_counts", status)
        self.assertIn("checkpoints", status)
        self.assertIn("sqlite_verdict", status)
        self.assertEqual(len(status["checkpoints"]), 5)
        self.assertEqual(status["checkpoints"][0]["name"], "100k")
        conn.close()


class TestScaleAdminApi(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        init_schema_v2(self.conn)

    def tearDown(self):
        self.conn.close()

    async def test_admin_scale_summary_endpoint(self):
        """Проверяет эндпоинт GET /api/v2/admin/scale/summary (ролевой доступ и тело ответа)."""
        import database
        orig_conn = database.get_connection
        database.get_connection = lambda: self.conn
        try:
            # 1. Неавторизованный запрос -> 401
            req_anon = make_mocked_request("GET", "/api/v2/admin/scale/summary")
            resp_anon = await admin_scale_summary_handler(req_anon)
            self.assertEqual(resp_anon.status, 401)

            # 2. Не админ -> 403
            req_user = make_mocked_request("GET", "/api/v2/admin/scale/summary")
            req_user["user"] = {"id": 10, "username": "buyer"}
            with patch("auth.is_admin", return_value=False):
                resp_user = await admin_scale_summary_handler(req_user)
                self.assertEqual(resp_user.status, 403)

            # 3. Администратор -> 200
            req_admin = make_mocked_request("GET", "/api/v2/admin/scale/summary")
            req_admin["user"] = {"id": 1, "is_admin": True, "username": "admin"}
            with patch("auth.is_admin", return_value=True):
                resp_admin = await admin_scale_summary_handler(req_admin)
                self.assertEqual(resp_admin.status, 200)

            import json
            body = json.loads(resp_admin.text)
            self.assertTrue(body.get("ok"))
            self.assertIn("checkpoints", body)
            self.assertIn("sqlite_verdict", body)
        finally:
            database.get_connection = orig_conn


if __name__ == "__main__":
    unittest.main()
