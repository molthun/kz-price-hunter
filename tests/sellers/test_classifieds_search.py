from tests import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import asyncio
import json
import sqlite3
import unittest
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from domain.condition import ConditionBreakdown, ConditionFilter
from domain.models import (
    CanonicalProduct,
    Channel,
    ChannelType,
    Condition,
    Offer,
    Seller,
)
from domain.search_core import SearchCore
import repositories.schema_v2 as schema_v2
from repositories.channel_repo import ChannelRepository
from repositories.offer_repo import OfferRepository
from repositories.product_repo import ProductRepository
from repositories.search_repo import SearchRepository
from repositories.seller_repo import SellerRepository
import web.server as srv


class TestClassifiedsAndConditionSearch(unittest.TestCase):
    """
    Stage 4 / S2-E04 Specification Tests:
    - Strict non-contamination invariant: Used prices NEVER contaminate or lower 'best new price'.
    - Separate price tiers: MacBook Air M5: Новые от 589 000 ₸, Б/у от 430 000 ₸.
    - Used-only goods correctly report is_used_only=True and min_price=best_used_price.
    - Strict condition filtering: condition='new' vs condition='used' vs condition='all'.
    - Seller type filtering: seller_type='business' vs seller_type='private' (Classifieds / OLX).
    """

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        schema_v2.init_schema_v2(self.conn)

        self.product_repo = ProductRepository(self.conn)
        self.seller_repo = SellerRepository(self.conn)
        self.channel_repo = ChannelRepository(self.conn)
        self.offer_repo = OfferRepository(self.conn)
        self.search_repo = SearchRepository(self.conn)
        self.search_core = SearchCore(self.search_repo)

        # 1. Product with both NEW and USED offers: MacBook Air M5
        self.prod_mac = CanonicalProduct(
            id="prod_macbook_m5",
            canonical_key="apple:macbook-air-m5:16gb-512gb",
            title="Apple MacBook Air M5 16GB 512GB Space Gray",
            category="Ноутбуки",
            brand="Apple",
            model="MacBook Air M5",
        )
        self.product_repo.save_or_update(self.prod_mac)

        # 2. Product with ONLY USED offers: iPhone X
        self.prod_iphonex = CanonicalProduct(
            id="prod_iphonex",
            canonical_key="apple:iphone-x:64gb",
            title="Apple iPhone X 64GB Silver",
            category="Смартфоны",
            brand="Apple",
            model="iPhone X",
        )
        self.product_repo.save_or_update(self.prod_iphonex)

        # Sellers
        # Business Retailer: Sulpak
        self.sel_sulpak = Seller(
            id="sel_sulpak",
            slug="sulpak",
            name="Sulpak",
            domain="sulpak.kz",
            is_active=True,
            metadata={"is_private": False},
        )
        self.seller_repo.save_or_update(self.sel_sulpak)

        # Business Retailer: Mechta
        self.sel_mechta = Seller(
            id="sel_mechta",
            slug="mechta",
            name="Mechta",
            domain="mechta.kz",
            is_active=True,
            metadata={"is_private": False},
        )
        self.seller_repo.save_or_update(self.sel_mechta)

        # Private Seller via OLX: Арман
        self.sel_private_olx = Seller(
            id="sel_private_arman",
            slug="olx-user-arman",
            name="Арман (Частное лицо)",
            is_active=True,
            metadata={"is_private": True, "is_private_seller": True},
        )
        self.seller_repo.save_or_update(self.sel_private_olx)

        # Channels
        self.ch_sulpak = Channel(
            id="chan_sulpak_site",
            seller_id=self.sel_sulpak.id,
            channel_type=ChannelType.WEBSITE,
            name="Sulpak Online",
        )
        self.ch_mechta = Channel(
            id="chan_mechta_site",
            seller_id=self.sel_mechta.id,
            channel_type=ChannelType.WEBSITE,
            name="Mechta Online",
        )
        self.ch_olx = Channel(
            id="chan_olx_classifieds",
            seller_id=self.sel_private_olx.id,
            channel_type=ChannelType.CLASSIFIEDS,
            name="OLX Казахстан",
        )
        self.channel_repo.save_or_update(self.ch_sulpak)
        self.channel_repo.save_or_update(self.ch_mechta)
        self.channel_repo.save_or_update(self.ch_olx)

        # Offers for MacBook:
        # Sulpak NEW: 589 000 ₸
        # Mechta NEW: 610 000 ₸
        # Arman OLX USED: 430 000 ₸
        self.offer_repo.save_or_update(
            Offer(
                id="off_mac_sulpak_new",
                product_id=self.prod_mac.id,
                seller_id=self.sel_sulpak.id,
                channel_id=self.ch_sulpak.id,
                external_sku="MAC-M5-SULPAK",
                url="https://sulpak.kz/mac-m5",
                price=589000.0,
                condition=Condition.NEW,
                city="Алматы",
                is_active=True,
            )
        )
        self.offer_repo.save_or_update(
            Offer(
                id="off_mac_mechta_new",
                product_id=self.prod_mac.id,
                seller_id=self.sel_mechta.id,
                channel_id=self.ch_mechta.id,
                external_sku="MAC-M5-MECHTA",
                url="https://mechta.kz/mac-m5",
                price=610000.0,
                condition=Condition.NEW,
                city="Алматы",
                is_active=True,
            )
        )
        self.offer_repo.save_or_update(
            Offer(
                id="off_mac_olx_used",
                product_id=self.prod_mac.id,
                seller_id=self.sel_private_olx.id,
                channel_id=self.ch_olx.id,
                external_sku="OLX-MAC-430",
                url="https://olx.kz/mac-m5",
                price=430000.0,
                condition=Condition.USED,
                city="Алматы",
                is_active=True,
            )
        )

        # Offers for iPhone X (Used only):
        # Arman OLX USED: 150 000 ₸
        self.offer_repo.save_or_update(
            Offer(
                id="off_iphonex_used",
                product_id=self.prod_iphonex.id,
                seller_id=self.sel_private_olx.id,
                channel_id=self.ch_olx.id,
                external_sku="OLX-IPHONEX-150",
                url="https://olx.kz/iphonex",
                price=150000.0,
                condition=Condition.USED,
                city="Алматы",
                is_active=True,
            )
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_strict_non_contamination_macbook(self):
        """Used price (430 000 ₸) is segregated into best_used_price, while best new is 589 000 ₸."""
        res = self.search_core.search("MacBook Air M5")
        self.assertEqual(len(res.items), 1)
        item = res.items[0]

        # Invariant checks
        self.assertEqual(item.best_new_price, 589000.0)
        self.assertEqual(item.best_used_price, 430000.0)
        self.assertEqual(item.min_price, 430000.0)
        self.assertFalse(item.is_used_only)
        self.assertEqual(item.new_offers_count, 2)
        self.assertEqual(item.used_offers_count, 1)

        # Condition breakdown attached
        breakdown = item.condition_breakdown
        self.assertIsNotNone(breakdown)
        self.assertEqual(breakdown["best_new_price"], 589000.0)
        self.assertEqual(breakdown["new_offers_count"], 2)
        self.assertEqual(breakdown["best_used_price"], 430000.0)
        self.assertEqual(breakdown["used_offers_count"], 1)

    def test_used_only_product_behavior(self):
        """When product has zero new offers, min_price is the best used price and is_used_only is True."""
        res = self.search_core.search("iPhone X")
        self.assertEqual(len(res.items), 1)
        item = res.items[0]

        self.assertIsNone(item.best_new_price)
        self.assertEqual(item.best_used_price, 150000.0)
        self.assertEqual(item.min_price, 150000.0)
        self.assertTrue(item.is_used_only)
        self.assertEqual(item.new_offers_count, 0)
        self.assertEqual(item.used_offers_count, 1)

    def test_filter_by_condition_new(self):
        """condition='new' must filter out used offers entirely and exclude used-only products."""
        # 1. Searching MacBook with condition=new
        res = self.search_core.search("MacBook Air M5", condition="new")
        self.assertEqual(len(res.items), 1)
        item = res.items[0]
        self.assertEqual(len(item.offers), 2)
        self.assertTrue(all(o.condition == Condition.NEW for o in item.offers))
        self.assertEqual(item.min_price, 589000.0)

        # 2. Searching iPhone X with condition=new must return 0 items
        res_x = self.search_core.search("iPhone X", condition="new")
        self.assertEqual(len(res_x.items), 0)

    def test_filter_by_condition_used(self):
        """condition='used' must return only used/open_box/refurb offers."""
        res = self.search_core.search("MacBook Air M5", condition="used")
        self.assertEqual(len(res.items), 1)
        item = res.items[0]
        self.assertEqual(len(item.offers), 1)
        self.assertEqual(item.offers[0].condition, Condition.USED)
        self.assertEqual(item.min_price, 430000.0)

    def test_filter_by_seller_type(self):
        """seller_type='business' vs 'private' (Classifieds / OLX)."""
        # Business retail only: Sulpak and Mechta only
        res_biz = self.search_core.search("MacBook Air M5", seller_type="business")
        self.assertEqual(len(res_biz.items), 1)
        item_biz = res_biz.items[0]
        self.assertEqual(len(item_biz.offers), 2)
        self.assertTrue(all(not o.is_private_seller for o in item_biz.offers))

        # Private classifieds only: Arman OLX only
        res_priv = self.search_core.search("MacBook Air M5", seller_type="private")
        self.assertEqual(len(res_priv.items), 1)
        item_priv = res_priv.items[0]
        self.assertEqual(len(item_priv.offers), 1)
        self.assertTrue(item_priv.offers[0].is_private_seller)
        self.assertEqual(item_priv.offers[0].channel_type, ChannelType.CLASSIFIEDS)


class TestClassifiedsHttpApi(unittest.IsolatedAsyncioTestCase):
    """Async HTTP integration test for /api/v2/search with Condition and SellerType."""

    async def asyncSetUp(self):
        import os
        import shutil
        import tempfile

        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test_classifieds.db")

        # Initialize schema in file
        init_conn = sqlite3.connect(self.db_path)
        schema_v2.init_schema_v2(init_conn)

        self.product_repo = ProductRepository(init_conn)
        self.seller_repo = SellerRepository(init_conn)
        self.channel_repo = ChannelRepository(init_conn)
        self.offer_repo = OfferRepository(init_conn)

        prod = CanonicalProduct(
            id="prod_macbook_m5",
            canonical_key="apple:macbook-air-m5:16gb-512gb",
            title="Apple MacBook Air M5 16GB 512GB Space Gray",
            category="Ноутбуки",
            brand="Apple",
            model="MacBook Air M5",
        )
        self.product_repo.save_or_update(prod)

        sel_biz = Seller(id="sel_b", slug="sulpak", name="Sulpak", is_active=True, metadata={"is_private": False})
        sel_priv = Seller(id="sel_p", slug="olx-user", name="Частное лицо", is_active=True, metadata={"is_private": True})
        self.seller_repo.save_or_update(sel_biz)
        self.seller_repo.save_or_update(sel_priv)

        ch_biz = Channel(id="ch_b", seller_id=sel_biz.id, channel_type=ChannelType.WEBSITE, name="Sulpak Online")
        ch_olx = Channel(id="ch_o", seller_id=sel_priv.id, channel_type=ChannelType.CLASSIFIEDS, name="OLX")
        self.channel_repo.save_or_update(ch_biz)
        self.channel_repo.save_or_update(ch_olx)

        self.offer_repo.save_or_update(
            Offer(
                id="off_new",
                product_id=prod.id,
                seller_id=sel_biz.id,
                channel_id=ch_biz.id,
                external_sku="SKU-NEW",
                url="https://sulpak.kz/m5",
                price=589000.0,
                condition=Condition.NEW,
                is_active=True,
            )
        )
        self.offer_repo.save_or_update(
            Offer(
                id="off_used",
                product_id=prod.id,
                seller_id=sel_priv.id,
                channel_id=ch_olx.id,
                external_sku="SKU-USED",
                url="https://olx.kz/m5",
                price=430000.0,
                condition=Condition.USED,
                is_active=True,
            )
        )
        init_conn.commit()
        init_conn.close()

        def _get_test_conn():
            c = sqlite3.connect(self.db_path, check_same_thread=False)
            c.row_factory = sqlite3.Row
            return c

        self.conn_patcher = patch("database.get_connection", side_effect=_get_test_conn)
        self.conn_patcher.start()

        self.app = web.Application()
        self.app.add_routes(srv.routes)
        self.server = TestServer(self.app)
        self.client = TestClient(self.server)
        await self.client.start_server()

    async def asyncTearDown(self):
        import shutil
        await self.client.close()
        await self.server.close()
        self.conn_patcher.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    async def test_search_api_condition_and_seller_type(self):
        # 1. Unfiltered search: returns both tiers, non-contaminating lead price
        resp = await self.client.get("/api/v2/search?q=MacBook")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertEqual(len(data["items"]), 1)
        item = data["items"][0]
        self.assertEqual(item["min_price"], 430000.0)
        self.assertEqual(item["best_new_price"], 589000.0)
        self.assertEqual(item["best_used_price"], 430000.0)
        self.assertFalse(item["is_used_only"])

        # 2. Filter by seller_type=private
        resp_priv = await self.client.get("/api/v2/search?q=MacBook&seller_type=private")
        data_priv = await resp_priv.json()
        self.assertEqual(len(data_priv["items"]), 1)
        item_priv = data_priv["items"][0]
        self.assertEqual(len(item_priv["offers"]), 1)
        self.assertTrue(item_priv["offers"][0]["is_private_seller"])
        self.assertEqual(item_priv["offers"][0]["price"], 430000.0)


if __name__ == "__main__":
    unittest.main()
