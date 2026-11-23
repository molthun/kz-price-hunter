from tests import test_support  # noqa: F401

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

import database
from web import server


def _seed_deals_with_sizes(db_path: str):
    now = datetime.now(timezone.utc).isoformat()
    with patch.object(database, "DB_PATH", db_path):
        database.init_db()
        with database.get_connection() as conn:
            products = [
                ("kari_shoe_42", "Kari", "Кроссовки Kari 42", "Обувь", "https://kari.kz/p/1", "", 15000, 25000, 25000, "Алматы"),
                ("kari_shoe_39", "Kari", "Кроссовки Kari 39", "Обувь", "https://kari.kz/p/2", "", 14000, 24000, 24000, "Алматы"),
                ("kimex_jacket_l", "KIMEX", "Куртка KIMEX L", "Одежда", "https://kimex.kz/p/3", "", 30000, 50000, 50000, "Алматы"),
                ("detmir_kids_104", "Детский мир", "Комбинезон рост 104", "Детские товары", "https://detmir.kz/p/4", "", 12000, 20000, 20000, "Алматы"),
                ("tech_nosize", "Sulpak", "Наушники без размера", "Наушники", "https://sulpak.kz/p/5", "", 10000, 18000, 18000, "Алматы"),
            ]
            for pid, shop, title, cat, url, img, cur_p, first_p, old_p, city in products:
                conn.execute(
                    """
                    INSERT INTO products
                        (id, shop, title, category, url, image_url, current_price, first_seen_price,
                         old_price_on_site, min_price, max_price, canonical_key, created_at, updated_at, city, is_active)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (pid, shop, title, cat, url, img, cur_p, first_p, old_p, cur_p, old_p, pid, now, now, city),
                )
            offer_sizes = [
                ("off_kari_shoe_42", "kari_shoe_42", "41"),
                ("off_kari_shoe_42", "kari_shoe_42", "42"),
                ("off_kari_shoe_42", "kari_shoe_42", "43"),
                ("off_kari_shoe_39", "kari_shoe_39", "39"),
                ("off_kimex_jacket_l", "kimex_jacket_l", "M"),
                ("off_kimex_jacket_l", "kimex_jacket_l", "L"),
                ("off_detmir_kids_104", "detmir_kids_104", "рост 98"),
                ("off_detmir_kids_104", "detmir_kids_104", "рост 104"),
            ]
            for oid, pid, sz in offer_sizes:
                conn.execute(
                    "INSERT INTO offer_sizes (offer_id, product_id, size, updated_at) VALUES (?, ?, ?, ?)",
                    (oid, pid, sz, now),
                )
            conn.commit()


class DealsSizesFilterTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "test_deals_sizes.db"
        _seed_deals_with_sizes(self.db_path)

    def tearDown(self):
        self._tmp.cleanup()

    def test_single_size_filter_returns_matching_deals_and_available_sizes(self):
        with patch.object(database, "DB_PATH", self.db_path):
            res = database.get_store_deals(sizes=["42"])
        pids = [d["product_id"] for d in res["deals"]]
        self.assertEqual(pids, ["kari_shoe_42"])
        self.assertEqual(res["total"], 1)
        self.assertEqual(res["deals"][0]["available_sizes"], ["41", "42", "43"])

    def test_multi_size_filter_combines_shoe_clothing_and_kids_height(self):
        with patch.object(database, "DB_PATH", self.db_path):
            res = database.get_store_deals(sizes=["42", "L", "рост 104"])
        pids = {d["product_id"] for d in res["deals"]}
        self.assertEqual(pids, {"kari_shoe_42", "kimex_jacket_l", "detmir_kids_104"})
        self.assertEqual(res["total"], 3)
        by_pid = {d["product_id"]: d["available_sizes"] for d in res["deals"]}
        self.assertIn("рост 104", by_pid["detmir_kids_104"])
        self.assertIn("L", by_pid["kimex_jacket_l"])

    def test_invalid_size_filter_returns_empty_not_full_catalog(self):
        with patch.object(database, "DB_PATH", self.db_path):
            res = database.get_store_deals(sizes=["totally_invalid_xyz"])
        self.assertEqual(res["deals"], [])
        self.assertEqual(res["total"], 0)

    def test_no_size_filter_returns_all_deals_with_available_sizes_attached(self):
        with patch.object(database, "DB_PATH", self.db_path):
            res = database.get_store_deals()
        self.assertEqual(res["total"], 5)
        by_pid = {d["product_id"]: d["available_sizes"] for d in res["deals"]}
        self.assertEqual(by_pid["kari_shoe_42"], ["41", "42", "43"])
        self.assertEqual(by_pid["tech_nosize"], [])


class DealsV2SizesApiTest(AioHTTPTestCase):
    async def get_application(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "test_deals_api.db"
        _seed_deals_with_sizes(self.db_path)
        app = web.Application()
        app.router.add_get("/api/v2/deals", server.deals_v2_handler)
        app.router.add_get("/api/deals", server.deals_handler)
        return app

    async def tearDownAsync(self):
        await super().tearDownAsync()
        self._tmp.cleanup()

    async def test_api_v2_deals_filters_by_sizes_and_exposes_available_sizes(self):
        with patch.object(database, "DB_PATH", self.db_path):
            resp = await self.client.get("/api/v2/deals?sizes=42,L,%D1%80%D0%BE%D1%81%D1%82+104")
            self.assertEqual(resp.status, 200)
            data = await resp.json()
        self.assertTrue(data["ok"])
        pids = {d["product_id"] for d in data["deals"]}
        self.assertEqual(pids, {"kari_shoe_42", "kimex_jacket_l", "detmir_kids_104"})
        for d in data["deals"]:
            self.assertIsInstance(d.get("available_sizes"), list)
            self.assertTrue(len(d["available_sizes"]) >= 1)

    async def test_api_v2_deals_invalid_sizes_returns_empty(self):
        with patch.object(database, "DB_PATH", self.db_path):
            resp = await self.client.get("/api/v2/deals?sizes=not_a_size")
            self.assertEqual(resp.status, 200)
            data = await resp.json()
        self.assertEqual(data["deals"], [])
        self.assertEqual(data["total"], 0)


def _seed_same_number_other_kind(db_path: str):
    """DS-01: куртка с номерным размером 42 и кеды в смешанной категории «Скидки»."""
    now = datetime.now(timezone.utc).isoformat()
    with patch.object(database, "DB_PATH", db_path):
        with database.get_connection() as conn:
            rows = [
                ("kimex_jacket_42", "KIMEX", "Куртка KIMEX женская", "KIMEX: 👗 Женская одежда", 30000, 50000),
                ("intertop_kedy_42", "Интертоп", "Кеды Converse", "Интертоп: 🔥 Скидки и распродажи", 20000, 32000),
                ("intertop_puhovik_42", "Интертоп", "Пуховик зимний", "Интертоп: 🔥 Скидки и распродажи", 40000, 70000),
            ]
            for pid, shop, title, cat, cur_p, old_p in rows:
                conn.execute(
                    """
                    INSERT INTO products
                        (id, shop, title, category, url, image_url, current_price, first_seen_price,
                         old_price_on_site, min_price, max_price, canonical_key, created_at, updated_at, city, is_active)
                    VALUES (?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?, ?, 'Алматы', 1)
                    """,
                    (pid, shop, title, cat, f"https://example.kz/{pid}", cur_p, old_p, old_p, cur_p, old_p, pid, now, now),
                )
                for sz in ("40", "42", "44"):
                    conn.execute(
                        "INSERT INTO offer_sizes (offer_id, product_id, size, updated_at) VALUES (?, ?, ?, ?)",
                        (f"off_{pid}", pid, sz, now),
                    )
            conn.commit()


class OfferSizeKindTest(unittest.TestCase):
    def test_category_decides_and_mixed_category_falls_back_to_title(self):
        from domain.sizes import offer_size_kind
        self.assertEqual(offer_size_kind("Kari: 👠 Женская обувь", "Сапоги"), "shoe")
        self.assertEqual(offer_size_kind("KIMEX: 👟 Мужские кроссовки", ""), "shoe")
        self.assertEqual(offer_size_kind("Интертоп: 👕 Мужская одежда", "Куртка"), "apparel")
        self.assertEqual(offer_size_kind("Детский мир: 👕 Одежда и обувь", "Комбинезон зимний"), "apparel")
        self.assertEqual(offer_size_kind("Детский мир: 👕 Одежда и обувь", "Ботинки детские"), "shoe")
        self.assertEqual(offer_size_kind("Интертоп: 🔥 Скидки и распродажи", "Кеды Converse"), "shoe")
        self.assertEqual(offer_size_kind("Интертоп: 🔥 Скидки и распродажи", "Пуховик"), "apparel")
        self.assertEqual(offer_size_kind("Интертоп: 🔥 Скидки и распродажи", "Marco Tozzi Бoтильоны"), "shoe")  # латинская o
        for title in ("Tamaris Ботфорты", "Tommy Hilfiger Тапки", "Caprice Слингбэки", "Тапочки FUTURINO Home"):
            self.assertEqual(offer_size_kind("Интертоп: 🔥 Скидки и распродажи", title), "shoe", title)
        self.assertEqual(offer_size_kind("Kari: 💍 Ювелирные украшения", "Ювелирное кольцо"), "apparel")
        self.assertEqual(offer_size_kind("Kari: 🧣 Женские аксессуары", "Шапка-ушанка"), "apparel")
        self.assertEqual(offer_size_kind(None, None), "apparel")


class DealsProfileSizesKindTest(unittest.TestCase):
    """DS-01: профильный размер сохраняет вид — обувь 42 не находит куртку 42."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "test_deals_kinds.db"
        _seed_deals_with_sizes(self.db_path)
        _seed_same_number_other_kind(self.db_path)

    def tearDown(self):
        self._tmp.cleanup()

    def _pids(self, **kw):
        with patch.object(database, "DB_PATH", self.db_path):
            res = database.get_store_deals(**kw)
        return {d["product_id"] for d in res["deals"]}, res

    def test_shoe_42_matches_only_shoes(self):
        pids, res = self._pids(profile_sizes={"shoe": "42", "clothing": "", "kids_height": ""})
        self.assertEqual(pids, {"kari_shoe_42", "intertop_kedy_42"})
        self.assertEqual(res["total"], 2)
        self.assertTrue(all(d["size_kind"] == "shoe" for d in res["deals"]))

    def test_clothing_l_and_height_104_match_only_apparel(self):
        pids, _ = self._pids(profile_sizes={"shoe": "", "clothing": "L", "kids_height": "рост 104"})
        self.assertEqual(pids, {"kimex_jacket_l", "detmir_kids_104"})

    def test_full_profile_never_returns_jacket_or_puhovik_42(self):
        pids, _ = self._pids(profile_sizes={"shoe": "42", "clothing": "L", "kids_height": "104"})
        self.assertEqual(pids, {"kari_shoe_42", "intertop_kedy_42", "kimex_jacket_l", "detmir_kids_104"})
        self.assertNotIn("kimex_jacket_42", pids)
        self.assertNotIn("intertop_puhovik_42", pids)

    def test_generic_sizes_param_keeps_its_contract(self):
        pids, _ = self._pids(sizes=["42"])
        self.assertEqual(pids, {"kari_shoe_42", "kimex_jacket_42", "intertop_kedy_42", "intertop_puhovik_42"})

    def test_empty_or_invalid_profile_returns_empty_not_catalog(self):
        for profile in ({"shoe": "", "clothing": "", "kids_height": ""},
                        {"shoe": "L", "clothing": "42", "kids_height": "abc"}):
            pids, res = self._pids(profile_sizes=profile)
            self.assertEqual(pids, set(), profile)
            self.assertEqual(res["total"], 0)

    def test_no_filter_attaches_size_kind(self):
        _, res = self._pids()
        kinds = {d["product_id"]: d["size_kind"] for d in res["deals"]}
        self.assertEqual(kinds["kimex_jacket_42"], "apparel")
        self.assertEqual(kinds["intertop_kedy_42"], "shoe")


class DealsV2ProfileSizesApiTest(AioHTTPTestCase):
    async def get_application(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "test_deals_kinds_api.db"
        _seed_deals_with_sizes(self.db_path)
        _seed_same_number_other_kind(self.db_path)
        app = web.Application()
        app.router.add_get("/api/v2/deals", server.deals_v2_handler)
        app.router.add_get("/api/deals", server.deals_handler)
        return app

    async def tearDownAsync(self):
        await super().tearDownAsync()
        self._tmp.cleanup()

    async def test_v2_typed_shoe_size_excludes_jacket_42(self):
        with patch.object(database, "DB_PATH", self.db_path):
            resp = await self.client.get("/api/v2/deals?shoe_size=42&clothing_size=&kids_height=")
            self.assertEqual(resp.status, 200)
            data = await resp.json()
        pids = {d["product_id"] for d in data["deals"]}
        self.assertEqual(pids, {"kari_shoe_42", "intertop_kedy_42"})
        self.assertTrue(all(d["size_kind"] == "shoe" for d in data["deals"]))

    async def test_v1_typed_full_profile(self):
        with patch.object(database, "DB_PATH", self.db_path):
            resp = await self.client.get(
                "/api/deals?shoe_size=42&clothing_size=L&kids_height=%D1%80%D0%BE%D1%81%D1%82+104")
            self.assertEqual(resp.status, 200)
            data = await resp.json()
        pids = {d["product_id"] for d in data["deals"]}
        self.assertEqual(pids, {"kari_shoe_42", "intertop_kedy_42", "kimex_jacket_l", "detmir_kids_104"})


if __name__ == "__main__":
    unittest.main()
