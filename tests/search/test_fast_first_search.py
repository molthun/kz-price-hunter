from tests import test_support  # noqa: F401
import asyncio
import json
import os
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

import local_search
from domain.models import CanonicalProduct, Offer
from repositories import init_schema_v2
from repositories.catalog_expansion_repo import CatalogExpansionRepository, DDL as EXPANSION_DDL
from repositories.category_repo import CategoryRepository
from repositories.offer_repo import OfferRepository
from repositories.product_repo import ProductRepository
from web import server as web_server


class _FakeReq:
    def __init__(self, query_dict):
        self.query = query_dict
        self.headers = {}
        self.remote = "127.0.0.1"


class FastFirstSearchTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "fast_search.db")
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        init_schema_v2(conn)
        conn.executescript(EXPANSION_DDL)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS product_source_evidence (
                product_id TEXT PRIMARY KEY,
                signature TEXT, facts_hash TEXT, facts_json TEXT,
                images_json TEXT, sources_json TEXT, status TEXT,
                checked_at TEXT, next_attempt TEXT
            );
        """)
        CategoryRepository(conn)
        for cid, name, parent in (
            ("shoes", "Обувь", None),
            ("phones", "Смартфоны", "electronics"),
        ):
            conn.execute(
                "INSERT OR REPLACE INTO categories (id, name, parent_id, icon, aliases_json, attributes_schema_json, search_priority, is_active, created_at, updated_at) "
                "VALUES (?, ?, ?, '', '[]', '{}', 0, 1, '2026-11-27', '2026-11-27')",
                (cid, name, parent),
            )
        conn.execute(
            "INSERT OR REPLACE INTO sellers (id, name, slug, rating, domain, is_active, metadata_json, created_at, updated_at) "
            "VALUES ('s1', 'Kari', 'kari', 4.8, 'kari.kz', 1, '{}', '2026-11-27', '2026-11-27')"
        )
        conn.execute(
            "INSERT OR REPLACE INTO sellers (id, name, slug, rating, domain, is_active, metadata_json, created_at, updated_at) "
            "VALUES ('s2', 'Sulpak', 'sulpak', 4.7, 'sulpak.kz', 1, '{}', '2026-11-27', '2026-11-27')"
        )
        conn.execute(
            "INSERT OR REPLACE INTO channels (id, seller_id, channel_type, name, created_at) "
            "VALUES ('c1', 's1', 'website', 'Kari KZ', '2026-11-27')"
        )
        conn.execute(
            "INSERT OR REPLACE INTO channels (id, seller_id, channel_type, name, created_at) "
            "VALUES ('c2', 's2', 'website', 'Sulpak KZ', '2026-11-27')"
        )
        pr = ProductRepository(conn)
        orf = OfferRepository(conn)
        pr.save_or_update(CanonicalProduct(
            id="p_shoe_astana", canonical_key="k_shoe_1",
            title="Кроссовки Nike Runner", category="shoes", brand="Nike",
        ))
        orf.save_or_update(Offer(
            id="o_shoe_1", product_id="p_shoe_astana", seller_id="s1", channel_id="c1",
            external_sku="sku1", url="http://ex.com/1", price=25000, condition="new", city="Астана",
        ))
        conn.execute(
            "INSERT INTO offer_sizes (offer_id, product_id, size, updated_at) VALUES ('o_shoe_1', 'p_shoe_astana', '42', '2026-11-27')"
        )

        # Варианты Galaxy S24 / S24+ / S24 Ultra для проверки точного варианта в preview и final
        for pid, title, price in (
            ("p_s24", "Смартфон Samsung Galaxy S24 256Gb", 340000),
            ("p_s24p", "Смартфон Samsung Galaxy S24+ 256Gb", 410000),
            ("p_s24u", "Смартфон Samsung Galaxy S24 Ultra 256Gb", 520000),
        ):
            pr.save_or_update(CanonicalProduct(
                id=pid, canonical_key=f"k_{pid}", title=title, category="phones", brand="Samsung",
            ))
            orf.save_or_update(Offer(
                id=f"o_{pid}", product_id=pid, seller_id="s2", channel_id="c2",
                external_sku=f"sku_{pid}", url=f"http://ex.com/{pid}", price=price, condition="new", city="Алматы",
            ))
        conn.commit()
        conn.close()
        local_search._plans.clear()
        local_search._failures.clear()
        local_search._pending.clear()

    def tearDown(self):
        local_search._plans.clear()
        local_search._failures.clear()
        local_search._pending.clear()
        self.tmpdir.cleanup()

    def _get_conn(self):
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        return c

    def _analytics_count(self, query=None):
        c = self._get_conn()
        try:
            if query is None:
                return c.execute("SELECT COUNT(*) FROM search_events").fetchone()[0]
            return c.execute("SELECT COUNT(*) FROM search_events WHERE query = ?", (query,)).fetchone()[0]
        finally:
            c.close()

    def _demand_rows(self):
        c = self._get_conn()
        try:
            return [dict(r) for r in c.execute("SELECT query, demand FROM catalog_expansions").fetchall()]
        finally:
            c.close()

    def test_preview_returns_cards_before_slow_model_and_deduplicates_llm_and_analytics(self):
        """Контрольный сценарий: модель задержана, превью приходит сразу, модель и аналитика вызываются по 1 разу."""
        model_calls = {"n": 0}
        release_model = None

        async def slow_model(prompt, base, model, api_key, timeout=18.0):
            model_calls["n"] += 1
            await release_model.wait()
            return {
                "search_query": "кроссовки",
                "category_id": "shoes",
                "category_name": "Обувь",
                "parent_id": None,
                "confidence": 0.95,
                "attributes": {},
                "fields": [
                    {"key": "season", "label": "Сезон"},
                    {"key": "material", "label": "Материал"},
                    {"key": "gender", "label": "Пол"},
                ],
                "clarification": "",
            }

        async def scenario():
            nonlocal release_model
            release_model = asyncio.Event()
            q = {"q": "кроссовки 42", "city": "Астана", "seller": "kari", "max_price": "30000", "limit": "20"}
            with patch.dict(os.environ, {"LOCAL_SEARCH_ENABLED": "1"}), \
                 patch("database.get_connection", side_effect=self._get_conn), \
                 patch("config.get_ai_config", return_value={"local_api_base": "http://127.0.0.1:11434/v1", "local_model": "qwen3-vl:8b-instruct"}), \
                 patch("local_ai.pause_reason", return_value=None), \
                 patch("ai_service.call_local_api", new=AsyncMock(side_effect=slow_model)):
                t0 = time.monotonic()
                p_resp = await web_server.search_v2_handler(_FakeReq({**q, "phase": "preview"}))
                preview_ms = (time.monotonic() - t0) * 1000.0
                preview = json.loads(p_resp.text)

                self.assertEqual(p_resp.status, 200)
                self.assertEqual(preview["search_phase"], "preview")
                self.assertTrue(preview["ai_pending"])
                self.assertEqual(preview["total_products"], 1)
                self.assertEqual(preview["items"][0]["product"]["id"], "p_shoe_astana")
                self.assertNotIn("watch_target", preview["search_help"])
                self.assertLess(preview_ms, 500.0)
                # Превью не пишет аналитику и спрос
                self.assertEqual(self._analytics_count(), 0)
                self.assertEqual(self._demand_rows(), [])

                final_task = asyncio.create_task(web_server.search_v2_handler(_FakeReq({**q, "phase": "final"})))
                await asyncio.sleep(0.05)
                self.assertFalse(final_task.done())
                release_model.set()
                f_resp = await final_task
                final = json.loads(f_resp.text)

                self.assertEqual(f_resp.status, 200)
                self.assertEqual(final["search_phase"], "final")
                self.assertFalse(final["ai_pending"])
                self.assertEqual(final["total_products"], 1)
                self.assertEqual(final["items"][0]["product"]["id"], "p_shoe_astana")
                self.assertEqual(final["selected_attributes"].get("size"), "42")
                self.assertIn("watch_target", final["search_help"])
                # Модель вызвана ровно 1 раз (final подхватил _pending из preview)
                self.assertEqual(model_calls["n"], 1)
                # Аналитика и спрос записаны ровно 1 раз
                self.assertEqual(self._analytics_count(), 1)
                self.assertEqual(len(self._demand_rows()), 1)
                self.assertEqual(self._demand_rows()[0]["demand"], 1)

                # Повторный поиск с прогретым кешем: phase=preview сразу отдаёт final без 2-го запроса и без вызова модели
                w_resp = await web_server.search_v2_handler(_FakeReq({**q, "phase": "preview"}))
                warm = json.loads(w_resp.text)
                self.assertEqual(warm["search_phase"], "final")
                self.assertFalse(warm["ai_pending"])
                self.assertTrue(warm["local_ai"]["cached"])
                self.assertEqual(model_calls["n"], 1)
                self.assertEqual(self._analytics_count(), 2)

        asyncio.run(scenario())

    def test_exact_plus_variant_parity_between_preview_and_final(self):
        async def phone_model(prompt, base, model, api_key, timeout=18.0):
            await asyncio.sleep(0.05)
            return {
                "search_query": "Galaxy S24+",
                "category_id": "phones",
                "category_name": "Смартфоны",
                "parent_id": None,
                "confidence": 0.95,
                "attributes": {},
                "fields": [
                    {"key": "storage", "label": "Память"},
                    {"key": "ram", "label": "ОЗУ"},
                    {"key": "color", "label": "Цвет"},
                ],
                "clarification": "",
            }

        async def scenario():
            q = {"q": "Galaxy S24+", "city": "Алматы", "seller": "sulpak", "max_price": "450000", "limit": "20"}
            with patch.dict(os.environ, {"LOCAL_SEARCH_ENABLED": "1"}), \
                 patch("database.get_connection", side_effect=self._get_conn), \
                 patch("config.get_ai_config", return_value={"local_api_base": "http://127.0.0.1:11434/v1", "local_model": "qwen3-vl:8b-instruct"}), \
                 patch("local_ai.pause_reason", return_value=None), \
                 patch("ai_service.call_local_api", new=AsyncMock(side_effect=phone_model)):
                p = json.loads((await web_server.search_v2_handler(_FakeReq({**q, "phase": "preview"}))).text)
                f = json.loads((await web_server.search_v2_handler(_FakeReq({**q, "phase": "final"}))).text)

            self.assertEqual([it["product"]["id"] for it in p["items"]], ["p_s24p"])
            self.assertEqual([it["product"]["id"] for it in f["items"]], ["p_s24p"])

        asyncio.run(scenario())

    def test_empty_preview_hides_watch_until_final_or_model_failure(self):
        async def failing_model(prompt, base, model, api_key, timeout=18.0):
            await asyncio.sleep(0.05)
            return None

        async def scenario():
            q = {"q": "несуществующий редкий товар", "city": "Астана", "limit": "20"}
            with patch.dict(os.environ, {"LOCAL_SEARCH_ENABLED": "1"}), \
                 patch("database.get_connection", side_effect=self._get_conn), \
                 patch("config.get_ai_config", return_value={"local_api_base": "http://127.0.0.1:11434/v1", "local_model": "qwen3-vl:8b-instruct"}), \
                 patch("local_ai.pause_reason", return_value=None), \
                 patch("ai_service.call_local_api", new=AsyncMock(side_effect=failing_model)):
                p = json.loads((await web_server.search_v2_handler(_FakeReq({**q, "phase": "preview"}))).text)
                self.assertEqual(p["search_phase"], "preview")
                self.assertTrue(p["ai_pending"])
                self.assertEqual(p["total_products"], 0)
                self.assertNotIn("watch_target", p["search_help"])

                f = json.loads((await web_server.search_v2_handler(_FakeReq({**q, "phase": "final"}))).text)
                self.assertEqual(f["search_phase"], "final")
                self.assertFalse(f["ai_pending"])
                self.assertEqual(f["local_ai"]["status"], "unavailable")
                self.assertEqual(f["total_products"], 0)
                self.assertIn("watch_target", f["search_help"])
                self.assertEqual(self._analytics_count(), 1)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
