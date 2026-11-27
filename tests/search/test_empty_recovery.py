from tests import test_support  # noqa: F401
import asyncio
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import local_search
from domain.models import CanonicalProduct, Offer
from repositories import init_schema_v2
from repositories.catalog_expansion_repo import DDL as EXPANSION_DDL
from repositories.category_repo import CategoryRepository
from repositories.offer_repo import OfferRepository
from repositories.product_repo import ProductRepository
from web import server as web_server


class _FakeReq:
    def __init__(self, query_dict, remote="127.0.0.1"):
        self.query = query_dict
        self.headers = {}
        self.remote = remote


class EmptyRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmpdir.name, "empty_recovery.db")
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
        for sid, name, slug in (
            ("s1", "Kari", "kari"),
            ("s2", "Intertop", "intertop"),
            ("s3", "Sulpak", "sulpak"),
        ):
            conn.execute(
                "INSERT OR REPLACE INTO sellers (id, name, slug, rating, domain, is_active, metadata_json, created_at, updated_at) "
                "VALUES (?, ?, ?, 4.8, 'ex.kz', 1, '{}', '2026-11-27', '2026-11-27')",
                (sid, name, slug),
            )
            conn.execute(
                "INSERT OR REPLACE INTO channels (id, seller_id, channel_type, name, created_at) "
                "VALUES (?, ?, 'website', ?, '2026-11-27')",
                (f"c_{sid}", sid, name),
            )

        pr = ProductRepository(conn)
        orf = OfferRepository(conn)

        # Кроссовки 42 в Астане у Kari (30 000) и у Intertop (20 000), и в Алматы у Kari (15 000)
        for pid, oid, sid, city, price, size in (
            ("p_shoe_1", "o_shoe_1", "s1", "Астана", 30000.0, "42"),
            ("p_shoe_2", "o_shoe_2", "s2", "Астана", 20000.0, "42"),
            ("p_shoe_3", "o_shoe_3", "s1", "Алматы", 15000.0, "42"),
            ("p_shoe_4", "o_shoe_4", "s1", "Астана", 12000.0, "40"),  # другой размер — не должен попадать при size=42
        ):
            pr.save_or_update(CanonicalProduct(
                id=pid, canonical_key=f"k_{pid}", title=f"Кроссовки Nike Runner {pid}", category="shoes", brand="Nike"
            ))
            orf.save_or_update(Offer(
                id=oid, product_id=pid, seller_id=sid, channel_id=f"c_{sid}",
                external_sku=f"sku_{oid}", url=f"http://ex.kz/{oid}", price=price, condition="new", city=city,
            ))
            conn.execute(
                "INSERT INTO offer_sizes (offer_id, product_id, size, updated_at) VALUES (?, ?, ?, '2026-11-27')",
                (oid, pid, size),
            )

        # Galaxy S24 (Астана) и Galaxy S24+ (Алматы) — для проверки сохранения точного варианта Plus
        pr.save_or_update(CanonicalProduct(
            id="p_s24", canonical_key="k_s24", title="Смартфон Samsung Galaxy S24 256Gb", category="phones", brand="Samsung"
        ))
        orf.save_or_update(Offer(
            id="o_s24", product_id="p_s24", seller_id="s3", channel_id="c_s3",
            external_sku="sku_s24", url="http://ex.kz/s24", price=340000.0, condition="new", city="Астана",
        ))
        pr.save_or_update(CanonicalProduct(
            id="p_s24p", canonical_key="k_s24p", title="Смартфон Samsung Galaxy S24+ 256Gb", category="phones", brand="Samsung"
        ))
        orf.save_or_update(Offer(
            id="o_s24p", product_id="p_s24p", seller_id="s3", channel_id="c_s3",
            external_sku="sku_s24p", url="http://ex.kz/s24p", price=410000.0, condition="new", city="Алматы",
        ))
        conn.commit()
        conn.close()
        local_search._plans.clear()
        local_search._failures.clear()
        local_search._pending.clear()
        local_search._preview_tasks.clear()
        local_search._preview_continuations.clear()

    def tearDown(self):
        local_search._plans.clear()
        local_search._failures.clear()
        local_search._pending.clear()
        local_search._preview_tasks.clear()
        local_search._preview_continuations.clear()
        self.tmpdir.cleanup()

    def _get_conn(self):
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        return c

    def _counts(self):
        c = self._get_conn()
        try:
            ev = c.execute("SELECT COUNT(*) FROM search_events").fetchone()[0]
            dem = c.execute("SELECT COALESCE(SUM(demand), 0) FROM catalog_expansions").fetchone()[0]
            return ev, dem
        finally:
            c.close()

    def test_zero_matches_anywhere_returns_empty_recovery_and_single_analytics_write(self):
        async def unclear_model(prompt, base, model, api_key, timeout=18.0):
            return {}

        async def scenario():
            q = {"q": "квантовый синхрофазотрон", "city": "Астана", "max_price": "1000", "seller": "kari", "limit": "20"}
            with patch.dict(os.environ, {"LOCAL_SEARCH_ENABLED": "1"}), \
                 patch("database.get_connection", side_effect=self._get_conn), \
                 patch("config.get_ai_config", return_value={"local_api_base": "http://127.0.0.1:11434/v1", "local_model": "qwen3-vl:8b-instruct"}), \
                 patch("local_ai.pause_reason", return_value=None), \
                 patch("ai_service.call_local_api", new=AsyncMock(side_effect=unclear_model)):
                resp = await web_server.search_v2_handler(_FakeReq(q, remote="10.2.0.1"))
                d = json.loads(resp.text)

            self.assertEqual(resp.status, 200)
            self.assertEqual(d["total_products"], 0)
            self.assertEqual(d["recovery_actions"], [])
            # Аналитика и спрос записаны ровно 1 раз (вспомогательные пробы не пишут!)
            self.assertEqual(self._counts(), (1, 1))

        asyncio.run(scenario())

    def test_single_useful_action_preserves_plus_variant_and_keeps_original_watch_target(self):
        async def phone_model(prompt, base, model, api_key, timeout=18.0):
            return {
                "search_query": "Galaxy S24+",
                "category_id": "phones",
                "category_name": "Смартфоны",
                "parent_id": None,
                "confidence": 0.95,
                "attributes": {},
                "fields": [{"key": "a", "label": "A"}, {"key": "b", "label": "B"}, {"key": "c", "label": "C"}],
                "clarification": "",
            }

        async def scenario():
            # В Астане нет S24+ (есть только обычный S24), а в Алматы есть 1 S24+
            q = {"q": "Galaxy S24+", "city": "Астана", "limit": "20"}
            with patch.dict(os.environ, {"LOCAL_SEARCH_ENABLED": "1"}), \
                 patch("database.get_connection", side_effect=self._get_conn), \
                 patch("config.get_ai_config", return_value={"local_api_base": "http://127.0.0.1:11434/v1", "local_model": "qwen3-vl:8b-instruct"}), \
                 patch("local_ai.pause_reason", return_value=None), \
                 patch("ai_service.call_local_api", new=AsyncMock(side_effect=phone_model)):
                d = json.loads((await web_server.search_v2_handler(_FakeReq(q, remote="10.2.0.2"))).text)
                self.assertEqual(d["total_products"], 0)
                self.assertEqual(len(d["recovery_actions"]), 1)
                act = d["recovery_actions"][0]
                self.assertEqual(act["id"], "all_cities")
                self.assertEqual(act["count"], 1)
                self.assertIn("1 предложение", act["label"])
                # Наблюдение сохраняет исходный город Астана до клика
                self.assertEqual(d["search_help"]["watch_city"], "Астана")
                self.assertEqual(self._counts(), (1, 1))

                # Применение действия (убираем city) даёт ровно 1 S24+ (не обычный S24)
                d_all = json.loads((await web_server.search_v2_handler(_FakeReq({"q": "Galaxy S24+", "limit": "20"}, remote="10.2.0.3"))).text)
                self.assertEqual(d_all["total_products"], 1)
                self.assertEqual([it["product"]["id"] for it in d_all["items"]], ["p_s24p"])

        asyncio.run(scenario())

    def test_multiple_useful_actions_preserve_size_42_and_do_not_inflate_analytics(self):
        async def shoe_model(prompt, base, model, api_key, timeout=18.0):
            return {
                "search_query": "кроссовки",
                "category_id": "shoes",
                "category_name": "Обувь",
                "parent_id": None,
                "confidence": 0.95,
                "attributes": {},
                "fields": [{"key": "a", "label": "A"}, {"key": "b", "label": "B"}, {"key": "c", "label": "C"}],
                "clarification": "",
            }

        async def scenario():
            # Кроссовки 42, Астана, max_price=22000, seller=kari -> 0 товаров!
            # Ослабление 1 (all_cities): найдёт p_shoe_3 (Алматы, Kari, 15 000, 42) -> 1
            # Ослабление 2 (clear_price): найдёт p_shoe_1 (Астана, Kari, 30 000, 42) -> 1 (p_shoe_4 имеет размер 40 — отсеян!)
            # Ослабление 3 (clear_seller): найдёт p_shoe_2 (Астана, Intertop, 20 000, 42) -> 1
            q = {"q": "кроссовки 42", "city": "Астана", "max_price": "22000", "seller": "kari", "limit": "20"}
            with patch.dict(os.environ, {"LOCAL_SEARCH_ENABLED": "1"}), \
                 patch("database.get_connection", side_effect=self._get_conn), \
                 patch("config.get_ai_config", return_value={"local_api_base": "http://127.0.0.1:11434/v1", "local_model": "qwen3-vl:8b-instruct"}), \
                 patch("local_ai.pause_reason", return_value=None), \
                 patch("ai_service.call_local_api", new=AsyncMock(side_effect=shoe_model)):
                d = json.loads((await web_server.search_v2_handler(_FakeReq(q, remote="10.2.0.4"))).text)

            self.assertEqual(d["total_products"], 0)
            by_id = {a["id"]: a for a in d["recovery_actions"]}
            self.assertEqual(set(by_id.keys()), {"all_cities", "clear_price", "clear_seller"})
            self.assertEqual(by_id["all_cities"]["count"], 1)
            self.assertEqual(by_id["clear_price"]["count"], 1)
            self.assertEqual(by_id["clear_seller"]["count"], 1)
            # Исходный watch_target сохраняет max_price=22000 и size=42
            self.assertEqual(d["search_help"]["watch_city"], "Астана")
            self.assertIn('"max_price": 22000.0', d["search_help"]["watch_target"])
            self.assertIn('"size": "42"', d["search_help"]["watch_target"])
            # 3 пробы не увеличили аналитику и спрос
            self.assertEqual(self._counts(), (1, 1))

        asyncio.run(scenario())

    def test_non_ai_search_v2_also_computes_recovery_actions(self):
        async def scenario():
            q = {"q": "кроссовки", "city": "Астана", "max_price": "22000", "seller": "kari", "attributes": json.dumps({"size": "42"}), "limit": "20"}
            with patch.dict(os.environ, {"LOCAL_SEARCH_ENABLED": "0"}), \
                 patch("database.get_connection", side_effect=self._get_conn):
                d = json.loads((await web_server.search_v2_handler(_FakeReq(q, remote="10.2.0.5"))).text)
            self.assertEqual(d["total_products"], 0)
            ids = {a["id"] for a in d["recovery_actions"]}
            self.assertEqual(ids, {"all_cities", "clear_price", "clear_seller"})
            ev, _ = self._counts()
            self.assertEqual(ev, 1)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
