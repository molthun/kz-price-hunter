"""Unit tests for scheduler resilience, stale running status resets, and backoff."""
import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import time
import unittest

from database import (
    init_db,
    get_connection,
    reset_stale_running_scans,
    record_shop_scan_start,
    record_shop_scan_result,
    get_stale_shops,
    get_shop_scans,
)


class SchedulerResilienceTest(unittest.TestCase):
    def setUp(self):
        init_db()

    def test_reset_stale_running_scans(self):
        record_shop_scan_start("test_shop_stale")
        scans_before = get_shop_scans()
        self.assertEqual(scans_before["test_shop_stale"]["status"], "running")

        reset_count = reset_stale_running_scans()
        self.assertGreaterEqual(reset_count, 1)

        scans_after = get_shop_scans()
        self.assertEqual(scans_after["test_shop_stale"]["status"], "failed")
        self.assertIn("Прервано перезапуском", scans_after["test_shop_stale"]["last_error"])

    def test_exponential_backoff_and_stale_shops(self):
        # 1st failure -> 300s backoff
        record_shop_scan_result("test_backoff_shop", items=0, duration_sec=1.0, error="ConnectionTimeout")
        scans = get_shop_scans()
        row = scans["test_backoff_shop"]
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["failure_count"], 1)
        self.assertIsNotNone(row["next_retry_at"])
        self.assertGreater(row["next_retry_at"], time.time() + 250)

        # In backoff -> must NOT be returned by get_stale_shops
        stale = get_stale_shops(["test_backoff_shop"], max_age_seconds=60)
        self.assertNotIn("test_backoff_shop", stale)

        # Simulate backoff expired in DB
        with get_connection() as conn:
            conn.execute("UPDATE shop_scans SET next_retry_at = ? WHERE shop_key = ?",
                         (time.time() - 10, "test_backoff_shop"))
            conn.commit()

        # Now it must be returned by get_stale_shops
        stale_after = get_stale_shops(["test_backoff_shop"], max_age_seconds=60)
        self.assertIn("test_backoff_shop", stale_after)


if __name__ == "__main__":
    unittest.main()
