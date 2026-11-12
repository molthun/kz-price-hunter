"""Тесты регламентного мониторинга дрейфа качества локального сопоставления."""
from tests import test_support  # noqa: F401  isolates DATA_DIR
import datetime
import unittest
from unittest.mock import patch, MagicMock

import local_matching_monitor as lmm
import service_monitor as sm


class LocalMatchingMonitorTest(unittest.TestCase):
    def setUp(self):
        lmm.reset_audit_state()

    def tearDown(self):
        lmm.reset_audit_state()

    def test_evaluate_drift_insufficient_data(self):
        mock_summary = {
            "compared": 10,
            "scheduled": 15,
            "matched": 9,
            "mismatch": 1,
            "match_rate": 0.90,
            "coverage": 0.66,
            "by_kind": {"matched": 9, "mismatch": 1},
        }
        with patch("database.normalize_shadow_summary", return_value=mock_summary):
            res = lmm.evaluate_drift(days=1, min_samples=20)
            self.assertFalse(res["drift_detected"])
            self.assertEqual(res["status"], "insufficient_data")
            self.assertIn("Недостаточно данных", res["reason"])

    def test_evaluate_drift_healthy(self):
        mock_summary = {
            "compared": 100,
            "scheduled": 110,
            "matched": 92,
            "mismatch": 8,
            "match_rate": 0.92,
            "coverage": 0.909,
            "by_kind": {"matched": 92, "mismatch": 8},
        }
        with patch("database.normalize_shadow_summary", return_value=mock_summary):
            res = lmm.evaluate_drift(days=1, min_samples=20)
            self.assertFalse(res["drift_detected"])
            self.assertEqual(res["status"], "healthy")
            self.assertIn("в норме", res["reason"])

    def test_evaluate_drift_degraded_low_match_rate(self):
        mock_summary = {
            "compared": 50,
            "scheduled": 55,
            "matched": 35,
            "mismatch": 15,
            "match_rate": 0.70,
            "coverage": 0.909,
            "by_kind": {"matched": 35, "mismatch": 15},
        }
        with patch("database.normalize_shadow_summary", return_value=mock_summary):
            res = lmm.evaluate_drift(days=1, min_samples=20)
            self.assertTrue(res["drift_detected"])
            self.assertEqual(res["status"], "degraded")
            self.assertIn("ниже порога", res["reason"])

    def test_evaluate_drift_degraded_low_coverage(self):
        mock_summary = {
            "compared": 30,
            "scheduled": 150,
            "matched": 29,
            "mismatch": 1,
            "match_rate": 0.966,
            "coverage": 0.20,
            "by_kind": {"matched": 29, "mismatch": 1},
        }
        with patch("database.normalize_shadow_summary", return_value=mock_summary):
            res = lmm.evaluate_drift(days=1, min_samples=20)
            self.assertTrue(res["drift_detected"])
            self.assertEqual(res["status"], "degraded")
            self.assertIn("охват выборки", res["reason"])

    def test_audit_scheduled_drift_emits_telemetry_on_drift(self):
        mock_summary = {
            "compared": 50,
            "scheduled": 50,
            "matched": 30,
            "mismatch": 20,
            "match_rate": 0.60,
            "coverage": 1.0,
            "by_kind": {"matched": 30, "mismatch": 20},
        }
        with patch("database.normalize_shadow_summary", return_value=mock_summary), \
             patch("telemetry.telemetry.record_event") as mock_record:
            res = lmm.audit_scheduled_drift(days=1, min_samples=20)
            self.assertTrue(res["drift_detected"])
            self.assertTrue(mock_record.called)
            kwargs = mock_record.call_args.kwargs
            self.assertEqual(kwargs["event_type"], "matching_drift")

    def test_scheduled_audit_if_due_respects_interval(self):
        mock_summary = {
            "compared": 5,
            "scheduled": 5,
            "matched": 5,
            "mismatch": 0,
            "match_rate": 1.0,
            "coverage": 1.0,
            "by_kind": {"matched": 5},
        }
        t0 = datetime.datetime(2026, 11, 12, 10, 0, tzinfo=datetime.timezone.utc)
        t1 = t0 + datetime.timedelta(hours=2)
        t2 = t0 + datetime.timedelta(hours=7)

        with patch("database.normalize_shadow_summary", return_value=mock_summary):
            res0 = lmm.scheduled_audit_if_due(interval_seconds=6 * 3600, now=t0)
            self.assertIsNotNone(res0)

            res1 = lmm.scheduled_audit_if_due(interval_seconds=6 * 3600, now=t1)
            self.assertIsNone(res1)

            res2 = lmm.scheduled_audit_if_due(interval_seconds=6 * 3600, now=t2)
            self.assertIsNotNone(res2)

    def test_service_monitor_issue_codes_integration(self):
        data = {
            "sections": {
                "shops": {"available": True, "status": "healthy", "counts": {"unknown": 0, "disabled": 0}, "items": []},
                "queue": {"available": True, "status": "healthy", "counts": {"dead_letter": 0, "rejected": 0}, "worker_running": True},
                "scanning": {"available": True, "status": "healthy"},
                "local": {"available": True, "status": "healthy", "paused": False, "configured": True, "errors_24h": 0, "matching_drift": True},
                "notifications": {"available": True, "status": "healthy", "counts": {"failed": 0}},
                "events": {"available": True, "status": "healthy", "enabled": True, "flusher_alive": True, "errors_24h": 0},
            }
        }
        codes = sm.issue_codes(data)
        self.assertIn("local_matching_drift", codes)

        data["sections"]["local"]["matching_drift"] = False
        codes_clean = sm.issue_codes(data)
        self.assertNotIn("local_matching_drift", codes_clean)
