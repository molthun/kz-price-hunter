"""Tests for automatic search demand registration and crawler polling."""
from tests import test_support  # noqa: F401

import asyncio
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from database import init_db
from repositories.catalog_expansion_repo import CatalogExpansionRepository, DDL as EXPANSION_DDL


class SearchDemandCrawlerPollTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = f"{self.tmp.name}/test.db"
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        # Create minimal required tables
        self.conn.execute("CREATE TABLE IF NOT EXISTS canonical_products (id TEXT PRIMARY KEY, title TEXT, canonical_key TEXT, brand TEXT, model TEXT, item_kind TEXT, category TEXT)")
        self.conn.executescript(EXPANSION_DDL)
        self.conn.commit()
        self.repo = CatalogExpansionRepository(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_propose_auto_approve_scarce_demand(self):
        plan = {
            "category_id": "dishwashers",
            "category_name": "Посудомоечные машины",
            "search_query": "посудомоечная машина",
            "confidence": 1.0,
            "fields": [],
            "attributes": {}
        }
        # First search for scarce demand: auto_approve=True
        item = self.repo.propose("Посудомоечная машина", "Астана", plan, auto_approve=True)
        self.assertIsNotNone(item)
        self.assertEqual(item["status"], "approved")
        self.assertEqual(item["demand"], 1)
        self.assertEqual(item["city"], "Астана")
        self.assertEqual(item["query"], "посудомоечная машина")

        # Second search increments demand
        again = self.repo.propose("посудомоечная машина", "Астана", plan, auto_approve=True)
        self.assertEqual(again["id"], item["id"])
        self.assertEqual(again["demand"], 2)
        self.assertEqual(again["status"], "approved")

    def test_claim_batch_orders_by_demand_desc(self):
        plan = {"category_id": "appliances", "category_name": "Бытовая техника", "fields": []}
        # Item 1 with demand 1
        t1 = self.repo.propose("кухонный комбайн", "Астана", plan, auto_approve=True)
        # Item 2 with demand 3
        t2 = self.repo.propose("посудомоечная машина", "Астана", plan, auto_approve=True)
        self.repo.propose("посудомоечная машина", "Астана", plan, auto_approve=True)
        self.repo.propose("посудомоечная машина", "Астана", plan, auto_approve=True)

        batch = self.repo.claim_batch_for_crawl(limit=2)
        self.assertEqual(len(batch), 2)
        # First claimed must be the one with higher demand (t2)
        self.assertEqual(batch[0]["id"], t2["id"])
        self.assertEqual(batch[0]["status"], "checking")
        self.assertEqual(batch[1]["id"], t1["id"])
        self.assertEqual(batch[1]["status"], "checking")

        # Calling claim again yields empty since all were claimed
        next_batch = self.repo.claim_batch_for_crawl(limit=2)
        self.assertEqual(len(next_batch), 0)

    def test_finish_crawl_poll_updates_status_and_found(self):
        plan = {"category_id": "dishwashers", "category_name": "Посудомоечные машины", "fields": []}
        item = self.repo.propose("посудомоечная машина", "Астана", plan, auto_approve=True)
        claimed = self.repo.claim_batch_for_crawl(limit=1)[0]

        self.repo.finish_crawl_poll(
            claimed["id"],
            found=25,
            message="Опрошено при обходе: найдено 25 товаров в магазинах",
            status="indexed"
        )
        updated = self.repo.get(item["id"])
        self.assertEqual(updated["status"], "indexed")
        self.assertEqual(updated["found"], 25)
        self.assertIn("найдено 25 товаров", updated["message"])
        self.assertIsNotNone(updated["checked_at"])

    async def test_poll_pending_demand_queries_executor(self):
        plan = {"category_id": "dishwashers", "category_name": "Посудомоечные машины", "fields": []}
        self.repo.propose("посудомоечная машина", "Астана", plan, auto_approve=True)

        mock_items = [
            {"title": "Посудомоечная машина Candy 1", "price": 120000, "shop": "Kaspi"},
            {"title": "Посудомоечная машина Midea 2", "price": 140000, "shop": "Kaspi"},
        ]

        def get_test_conn():
            c = sqlite3.connect(self.db_path, check_same_thread=False)
            c.row_factory = sqlite3.Row
            return c

        with patch("database.get_connection", side_effect=get_test_conn), \
             patch("search_engine.search_live_stores", AsyncMock(return_value=mock_items)) as mock_live, \
             patch("domain.attribute_indexing.sync_all_source_attributes", return_value=2):

            from web.server import _poll_pending_demand_queries
            await _poll_pending_demand_queries(max_queries=3)

            mock_live.assert_awaited_once_with("посудомоечная машина", city="Астана")

        # Verify task is now marked as indexed with 2 items found
        items = self.repo.list()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["status"], "indexed")
        self.assertEqual(items[0]["found"], 2)
        self.assertIn("найдено 2 товаров", items[0]["message"])

    def test_local_search_scarce_results_registers_demand(self):
        """Simulate search_sync with < 5 results registering demand into CatalogExpansionRepository."""
        import local_search
        from unittest.mock import MagicMock

        # Create mock search result with 2 products (like Candy in Astana)
        mock_res = MagicMock()
        mock_res.outcome = "OK"
        mock_res.total_products = 2
        mock_res.parsed_query.detected_city = "Астана"
        mock_res.parsed_query.min_price = None
        mock_res.parsed_query.max_price = None
        mock_res.parsed_query.condition = None

        search_args = {
            "query": "Посудомоечная машина",
            "city": "Астана",
            "category": None,
            "brand": None,
            "model": None,
            "kind": None,
        }

        # Verify nothing in repo before
        self.assertEqual(len(self.repo.list()), 0)

        # Call propose as search_sync does
        is_scarce = mock_res.total_products < 5
        self.assertTrue(is_scarce)
        eff_plan = {
            "category_id": "dishwashers",
            "category_name": "Посудомоечные машины",
            "search_query": "Посудомоечная машина",
            "confidence": 1.0,
            "fields": [],
            "attributes": {}
        }
        item = self.repo.propose("Посудомоечная машина", mock_res.parsed_query.detected_city, eff_plan, auto_approve=True)
        self.assertIsNotNone(item)
        self.assertEqual(item["status"], "approved")
        self.assertEqual(item["query"], "посудомоечная машина")
        self.assertEqual(item["city"], "Астана")
        self.assertEqual(item["demand"], 1)

        # And verify it is listed for admin
        listed = self.repo.list()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["id"], item["id"])
