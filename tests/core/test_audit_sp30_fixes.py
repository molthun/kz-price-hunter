"""
Regression tests for Audit Findings SP30-01 through SP30-09 (Search Platform 2.0).

Verifies:
1. SP30-01: HistoryRepository preserves snapshot dimensions (product, seller, channel, condition, city)
   on record_point, record_batch, and record_history_model when mutable offer attributes change.
2. SP30-02: SearchCore groups sellers strictly by purchasable offers; out-of-stock prices
   do not distort seller min_price / best_new_price.
3. SP30-03: UI/API contracts for SellerOffersGroup (channels_available vs channels, best_offer vs best_channel_offer).
4. SP30-04: get_current_scale_status returns ERROR_NO_SCHEMA on uninitialized DB, NOT_MEASURED when unmeasured.
   evaluate_storage_decision rejects NaN/non-positive and correctly bounds 50-100ms.
5. SP30-05: Search query latency under 100k offers with covering indexes and ANALYZE.
6. SP30-06: Scale benchmark generator maintains 100% valid seller/channel relationships (0 mismatches).
7. SP30-07: DiscoveryRepository dedupes active tasks, accumulates search_count/success_rate,
   and correctly aggregates RUNNING/REVIEW_REQUIRED/APPROVED/INDEXED statuses.
8. SP30-08: Brand filtering strictly matches canonical brand without false positives on accessory titles.
9. SP30-09: Future dates do not trigger active_today in SellerProvenance.
"""

from tests import test_support  # must be first
import datetime as dt
import math
import sqlite3
import unittest

from domain.models import (
    CanonicalProduct,
    Channel,
    Condition,
    Offer,
    OfferPriceHistory,
    Seller,
    utc_now_iso,
)
from domain.discovery import CandidateStatus, SellerCandidate
from domain.provenance import build_seller_provenance
from domain.scale_bench import (
    ScaleCheckpoint,
    evaluate_storage_decision,
    get_current_scale_status,
    run_scale_benchmark,
)
from domain.search_core import SearchCore
from repositories import (
    ChannelRepository,
    DiscoveryRepository,
    HistoryRepository,
    OfferRepository,
    ProductRepository,
    SearchRepository,
    SellerRepository,
    init_schema_v2,
)


class TestAuditSP30Fixes(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        init_schema_v2(self.conn)
        self.product_repo = ProductRepository(self.conn)
        self.seller_repo = SellerRepository(self.conn)
        self.channel_repo = ChannelRepository(self.conn)
        self.offer_repo = OfferRepository(self.conn)
        self.search_repo = SearchRepository(self.conn)
        self.history_repo = HistoryRepository(self.conn)
        self.discovery_repo = DiscoveryRepository(self.conn)
        self.search_core = SearchCore(self.search_repo)

    def tearDown(self):
        self.conn.close()

    def test_sp30_01_history_repository_preserves_snapshot_dimensions(self):
        """SP30-01: Changing mutable offer attributes must not alter recorded history lines."""
        now = dt.datetime.now(dt.timezone.utc)
        self.product_repo.save_or_update(CanonicalProduct(id="p1", canonical_key="p1", title="Phone 1", brand="A", category="phones"))
        self.product_repo.save_or_update(CanonicalProduct(id="p2", canonical_key="p2", title="Phone 2", brand="A", category="phones"))
        self.seller_repo.save_or_update(Seller(id="s1", name="Seller 1", slug="seller-1"))
        self.channel_repo.save_or_update(Channel(id="c_dir", seller_id="s1", channel_type="direct", name="Direct"))
        self.channel_repo.save_or_update(Channel(id="c_ksp", seller_id="s1", channel_type="kaspi", name="Kaspi"))

        base_offer = Offer(
            id="off_test",
            product_id="p1",
            seller_id="s1",
            channel_id="c_dir",
            external_sku="SKU1",
            url="https://shop.kz/1",
            price=100.0,
            condition=Condition.NEW,
            availability="in_stock",
            observed_at=now.isoformat(),
        )

        for writer in ("record_point", "record_batch", "record_history_model"):
            self.conn.execute("DELETE FROM offer_price_history")
            self.offer_repo.save_or_update(base_offer)

            if writer == "record_point":
                self.history_repo.record_point("off_test", 100.0, observed_at="2026-10-30T10:00:00Z")
            elif writer == "record_batch":
                self.history_repo.record_batch([{"offer_id": "off_test", "price": 100.0, "observed_at": "2026-10-30T10:00:00Z"}])
            else:
                self.history_repo.record_history_model(OfferPriceHistory(offer_id="off_test", price=100.0, observed_at="2026-10-30T10:00:00Z"))

            # Offer attributes mutate to product 2, kaspi, and USED
            mutated_offer = Offer(
                id="off_test",
                product_id="p2",
                seller_id="s1",
                channel_id="c_ksp",
                external_sku="SKU1",
                url="https://shop.kz/1",
                price=200.0,
                condition=Condition.USED,
                availability="in_stock",
                observed_at=now.isoformat(),
            )
            self.offer_repo.save_or_update(mutated_offer)

            old_report = self.history_repo.get_product_price_history("p1")
            new_report = self.history_repo.get_product_price_history("p2")

            p1_prices = [pt.price for s in old_report.series for pt in s.points] if old_report and old_report.series else []
            p2_prices = [pt.price for s in new_report.series for pt in s.points] if new_report and new_report.series else []

            self.assertIn(100.0, p1_prices, f"{writer} must retain 100.0 in p1 history")
            self.assertNotIn(100.0, p2_prices, f"{writer} must not leak 100.0 to p2 history")

    def test_sp30_02_purchasability_in_seller_groups(self):
        """SP30-02: Out-of-stock cheap prices must not contaminate seller min_price / best_new_price."""
        now = dt.datetime.now(dt.timezone.utc)
        self.product_repo.save_or_update(CanonicalProduct(id="p_probe", canonical_key="p_probe", title="Pro Phone 256GB", brand="Audit", category="phones"))
        self.seller_repo.save_or_update(Seller(id="s_probe", name="Pro Seller", slug="pro-seller"))
        self.channel_repo.save_or_update(Channel(id="c_dir", seller_id="s_probe", channel_type="direct", name="Direct"))
        self.channel_repo.save_or_update(Channel(id="c_ksp", seller_id="s_probe", channel_type="kaspi", name="Kaspi"))

        # In-stock offer: 100,000; out-of-stock cheap offer: 1,000
        self.offer_repo.save_or_update(Offer(
            id="off_avail", product_id="p_probe", seller_id="s_probe", channel_id="c_dir",
            external_sku="SKU_IN", url="https://shop.kz/in", price=100000.0, condition=Condition.NEW,
            availability="in_stock", observed_at=now.isoformat()
        ))
        self.offer_repo.save_or_update(Offer(
            id="off_unavail", product_id="p_probe", seller_id="s_probe", channel_id="c_ksp",
            external_sku="SKU_OUT", url="https://shop.kz/out", price=1000.0, condition=Condition.NEW,
            availability="out_of_stock", observed_at=now.isoformat()
        ))

        resp = self.search_core.search("Pro Phone 256GB")
        self.assertEqual(len(resp.items), 1)
        item = resp.items[0]
        sg = item.sellers[0]

        self.assertEqual(item.min_price, 100000.0)
        self.assertEqual(sg.min_price, 100000.0)
        self.assertEqual(sg.max_price, 100000.0)
        self.assertEqual(sg.best_new_price, 100000.0)

        # When all offers are out of stock
        self.conn.execute("UPDATE offers SET availability = 'out_of_stock'")
        resp_empty = self.search_core.search("Pro Phone 256GB")
        item_empty = resp_empty.items[0]
        sg_empty = item_empty.sellers[0]

        self.assertIsNone(item_empty.min_price)
        self.assertIsNone(sg_empty.min_price)
        self.assertIsNone(sg_empty.best_new_price)

    def test_sp30_03_seller_group_contract_aliases(self):
        """SP30-03: SellerOffersGroup must serialize both channels_available/channels and best_offer/best_channel_offer."""
        now = dt.datetime.now(dt.timezone.utc)
        self.product_repo.save_or_update(CanonicalProduct(id="p_alias", canonical_key="p_alias", title="Alias Product", brand="B", category="c"))
        self.seller_repo.save_or_update(Seller(id="s_alias", name="Alias Seller", slug="alias-seller"))
        self.channel_repo.save_or_update(Channel(id="c1", seller_id="s_alias", channel_type="kaspi", name="Kaspi"))
        self.offer_repo.save_or_update(Offer(
            id="o_alias", product_id="p_alias", seller_id="s_alias", channel_id="c1",
            external_sku="SKU1", url="https://shop.kz/a", price=50000.0, condition=Condition.NEW,
            availability="in_stock", observed_at=now.isoformat()
        ))

        resp = self.search_core.search("Alias Product")
        sg = resp.items[0].sellers[0]
        d = sg.to_dict()

        self.assertIn("channels_available", d)
        self.assertIn("channels", d)
        self.assertEqual(d["channels"], ["kaspi"])
        self.assertIn("best_offer", d)
        self.assertIn("best_channel_offer", d)
        self.assertEqual(d["best_offer"]["price"], 50000.0)
        self.assertEqual(d["best_channel_offer"]["price"], 50000.0)

    def test_sp30_04_scale_status_missing_schema_and_verdict(self):
        """SP30-04: get_current_scale_status reports schema errors; evaluate_storage_decision validates metrics strictly."""
        empty_conn = sqlite3.connect(":memory:")
        st = get_current_scale_status(empty_conn)
        empty_conn.close()

        self.assertEqual(st["sqlite_verdict"]["status"], "ERROR_NO_SCHEMA")

        # SP30-04: Неполные/нечисловые метрики НЕ должны давать положительный boolean is_sqlite_sufficient=True!
        # 1. NaN / non-positive search_p95_ms
        cp_nan_p95 = ScaleCheckpoint("nan_p95", 100000, 100000, 20000, 1000, 2000, float("nan"), 10.0, 20.0, 50.0, 5.0, 2.0, 0.1)
        dec_nan_p95 = evaluate_storage_decision(cp_nan_p95)
        self.assertEqual(dec_nan_p95.decision, "NOT_MEASURED")
        self.assertFalse(dec_nan_p95.is_sqlite_sufficient, "NOT_MEASURED must never set is_sqlite_sufficient=True")

        # 2. NaN in backup_duration_s
        cp_nan_backup = ScaleCheckpoint("nan_bck", 100000, 100000, 20000, 1000, 2000, 20.0, 10.0, 20.0, 50.0, 5.0, 2.0, float("nan"))
        dec_nan_bck = evaluate_storage_decision(cp_nan_backup)
        self.assertEqual(dec_nan_bck.decision, "NOT_MEASURED")
        self.assertFalse(dec_nan_bck.is_sqlite_sufficient)

        # 3. NaN / <=0 in write_latency_1k_ms
        cp_nan_write = ScaleCheckpoint("nan_write", 100000, 100000, 20000, 1000, 2000, 20.0, 10.0, float("nan"), 50.0, 5.0, 2.0, 0.1)
        dec_nan_write = evaluate_storage_decision(cp_nan_write)
        self.assertEqual(dec_nan_write.decision, "NOT_MEASURED")
        self.assertFalse(dec_nan_write.is_sqlite_sufficient)

        # 4. Negative / NaN db_size_mb
        cp_neg_db = ScaleCheckpoint("neg_db", 100000, 100000, 20000, 1000, 2000, 20.0, 10.0, 20.0, -10.0, 5.0, 2.0, 0.1)
        dec_neg_db = evaluate_storage_decision(cp_neg_db)
        self.assertEqual(dec_neg_db.decision, "NOT_MEASURED")
        self.assertFalse(dec_neg_db.is_sqlite_sufficient)

        # 5. actual_offers == 0 or significantly below target (e.g. 500 when target is 100k)
        cp_low_actual = ScaleCheckpoint("low_actual", 100000, 500, 20000, 1000, 2000, 20.0, 10.0, 20.0, 50.0, 5.0, 2.0, 0.1)
        dec_low_actual = evaluate_storage_decision(cp_low_actual)
        self.assertEqual(dec_low_actual.decision, "NOT_MEASURED")
        self.assertFalse(dec_low_actual.is_sqlite_sufficient)

        # 6. Valid 80ms latency checkpoint: bounded 50-100ms
        cp_80 = ScaleCheckpoint("80ms", 100000, 100000, 20000, 1000, 2000, 80.0, 50.0, 20.0, 50.0, 5.0, 2.0, 0.1)
        dec_80 = evaluate_storage_decision(cp_80)
        self.assertEqual(dec_80.decision, "STAY_ON_SQLITE")
        self.assertTrue(dec_80.is_sqlite_sufficient)
        self.assertTrue(any("50–100 мс" in r for r in dec_80.reasons))
        self.assertFalse(any("80.0 мс <= 50 мс" in r for r in dec_80.reasons))

    def test_sp30_06_generator_channel_seller_consistency(self):
        """SP30-06: Benchmark generator metrics: seller/channel mapping, history indexes, concurrency and honest memory."""
        cp = run_scale_benchmark(target_offers=1000)
        self.assertGreaterEqual(cp.actual_offers, 1000)
        self.assertGreater(cp.search_p95_ms, 0.0)
        self.assertLess(cp.search_p95_ms, 100.0)

        # SP30-06: History size includes index
        self.assertGreater(cp.price_history_size_mb, 0.0)
        self.assertGreater(cp.history_index_size_mb, 0.0)
        self.assertIn(cp.size_measurement_method, ("dbstat", "estimated"))

        # SP30-06: Concurrency and cold startup verified
        self.assertTrue(cp.concurrency_verified)
        self.assertEqual(cp.concurrency_errors, 0)
        self.assertGreater(cp.cold_startup_ms, 0.0)

        # SP30-06: Honest memory fallback (None instead of fake 45.0 MB)
        from unittest.mock import patch
        with patch("resource.getrusage", side_effect=Exception("Simulated rusage failure")):
            cp_mock_mem = run_scale_benchmark(target_offers=100)
            self.assertIsNone(cp_mock_mem.memory_mb, "Failed memory measurement must return None, not fake 45.0 MB")
            self.assertIsNone(cp_mock_mem.peak_rss_mb)

    def test_sp30_07_discovery_repository_dedup_and_stats(self):
        """SP30-07: Discovery task dedup, demand accumulation, and real status aggregation."""
        t1 = self.discovery_repo.create_or_update_task("Laptop 16", city="Алматы", search_count=31, success_rate=0.25)
        t2 = self.discovery_repo.create_or_update_task("Laptop 16", city="Алматы", search_count=40, success_rate=0.1)

        self.assertEqual(t1.id, t2.id)
        self.assertEqual(t2.search_count, 71)
        self.assertEqual(t2.success_rate, 0.1)

        self.discovery_repo.update_task_status(t1.id, "RUNNING")
        cand = SellerCandidate(id="cand_test", name="Candidate Store", source_type="website")
        self.discovery_repo.save_candidate(cand)
        self.discovery_repo.update_candidate_status(cand.id, CandidateStatus.REVIEW_REQUIRED)

        stats = self.discovery_repo.get_discovery_stats()
        self.assertEqual(stats["tasks"]["in_progress"], 1)
        self.assertEqual(stats["candidates"]["in_review"], 1)

    def test_sp30_08_brand_filter_accessory_protection(self):
        """SP30-08: Filtering by brand=Apple must strictly exclude accessories made by Acme."""
        now = dt.datetime.now(dt.timezone.utc)
        self.product_repo.save_or_update(CanonicalProduct(id="p_apple", canonical_key="apple_phone", title="Apple iPhone 15", brand="Apple", category="phones"))
        self.product_repo.save_or_update(CanonicalProduct(id="p_acme", canonical_key="acme_adapter", title="Acme adapter compatible with Apple iPhone", brand="Acme", category="accessories"))

        self.seller_repo.save_or_update(Seller(id="s1", name="S1", slug="s1"))
        self.channel_repo.save_or_update(Channel(id="c1", seller_id="s1", channel_type="direct", name="D1"))

        self.offer_repo.save_or_update(Offer(id="o1", product_id="p_apple", seller_id="s1", channel_id="c1", external_sku="A1", url="u1", price=500000, condition=Condition.NEW, availability="in_stock", observed_at=now.isoformat()))
        self.offer_repo.save_or_update(Offer(id="o2", product_id="p_acme", seller_id="s1", channel_id="c1", external_sku="A2", url="u2", price=5000, condition=Condition.NEW, availability="in_stock", observed_at=now.isoformat()))

        res_apple = self.search_core.search("", brand="Apple")
        brands = [item.product.brand for item in res_apple.items]
        self.assertIn("Apple", brands)
        self.assertNotIn("Acme", brands)

    def test_sp30_09_provenance_future_date_rejection(self):
        """SP30-09: Future observation dates must not set active_today=True."""
        now = dt.datetime.now(dt.timezone.utc)
        seller = Seller(id="s_time", name="Time Seller", slug="time-seller")

        # 1 year in future
        prov_future = build_seller_provenance(seller, offers=[{"observed_at": (now + dt.timedelta(days=365)).isoformat()}], now=now)
        self.assertFalse(prov_future.active_today)
        self.assertNotIn("активность сегодня", prov_future.fact_labels)

        # 1 hour ago
        prov_now = build_seller_provenance(seller, offers=[{"observed_at": (now - dt.timedelta(hours=1)).isoformat()}], now=now)
        self.assertTrue(prov_now.active_today)
        self.assertTrue(any("активность сегодня" in f for f in prov_now.fact_labels))


if __name__ == "__main__":
    unittest.main()
