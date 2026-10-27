from tests import test_support  # noqa: F401 isolates DATA_DIR; must precede project imports
"""
Unit tests for S2-E19: Seller Page & Showcase.
Tests repositories (SellerRepository, OfferRepository) and domain showcase grouping.
"""

import sqlite3
import unittest
from domain.models import (
    Availability,
    CanonicalProduct,
    Channel,
    ChannelType,
    Condition,
    Offer,
    Seller,
)
from domain.seller_showcase import (
    build_seller_showcase,
    group_seller_offers_by_product,
)
from repositories.channel_repo import ChannelRepository
from repositories.offer_repo import OfferRepository
from repositories.product_repo import ProductRepository
from repositories.schema_v2 import DDL_V2
from repositories.seller_repo import SellerRepository


class SellerPageTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(DDL_V2)

        self.seller_repo = SellerRepository(self.conn)
        self.channel_repo = ChannelRepository(self.conn)
        self.product_repo = ProductRepository(self.conn)
        self.offer_repo = OfferRepository(self.conn)

        # Seed seller
        self.seller = Seller(
            id="seller_4mobile",
            slug="4mobile",
            name="4mobile",
            legal_name="ТОО 4mobile Казахстан",
            bin="123456789012",
            domain="4mobile.kz",
            phone="+7 701 000 0000",
            rating=4.8,
            is_active=True,
            metadata={"city": "Астана", "address": "ул. Достык, 10"},
        )
        self.seller_repo.save_or_update(self.seller)

        # Seed channels
        self.chan_web = Channel(
            id="chan_4mobile_web",
            seller_id=self.seller.id,
            channel_type=ChannelType.WEBSITE,
            name="Официальный сайт",
            base_url="https://4mobile.kz",
        )
        self.chan_kaspi = Channel(
            id="chan_4mobile_kaspi",
            seller_id=self.seller.id,
            channel_type=ChannelType.KASPI,
            name="Kaspi Магазин",
            external_store_id="store_4mobile",
            base_url="https://kaspi.kz/shop/4mobile",
        )
        self.chan_soc = Channel(
            id="chan_4mobile_ig",
            seller_id=self.seller.id,
            channel_type=ChannelType.INSTAGRAM,
            name="Instagram Stories & Shop",
            base_url="https://instagram.com/4mobile_kz",
        )
        self.channel_repo.save_or_update(self.chan_web)
        self.channel_repo.save_or_update(self.chan_kaspi)
        self.channel_repo.save_or_update(self.chan_soc)

        # Seed products
        self.prod1 = CanonicalProduct(
            id="prod_ip15_128",
            canonical_key="apple-iphone-15-128gb",
            title="Apple iPhone 15 128GB Black",
            category="smartphones",
            brand="Apple",
            image_url="https://cdn.example.kz/ip15.jpg",
        )
        self.prod2 = CanonicalProduct(
            id="prod_s24_256",
            canonical_key="samsung-galaxy-s24-256gb",
            title="Samsung Galaxy S24 256GB Onyx Black",
            category="smartphones",
            brand="Samsung",
            image_url="https://cdn.example.kz/s24.jpg",
        )
        self.product_repo.save_or_update(self.prod1)
        self.product_repo.save_or_update(self.prod2)

        # Seed multi-channel offers for iPhone 15
        self.off1_web = Offer(
            id="off_ip15_web",
            product_id=self.prod1.id,
            seller_id=self.seller.id,
            channel_id=self.chan_web.id,
            external_sku="SKU-IP15-W",
            url="https://4mobile.kz/iphone-15",
            price=440000.0,
            old_price=460000.0,
            condition=Condition.NEW,
            city="Астана",
        )
        self.off1_kaspi = Offer(
            id="off_ip15_kaspi",
            product_id=self.prod1.id,
            seller_id=self.seller.id,
            channel_id=self.chan_kaspi.id,
            external_sku="SKU-IP15-K",
            url="https://kaspi.kz/p/ip15",
            price=470000.0,
            condition=Condition.NEW,
            city="Астана",
        )
        self.off1_soc = Offer(
            id="off_ip15_soc",
            product_id=self.prod1.id,
            seller_id=self.seller.id,
            channel_id=self.chan_soc.id,
            external_sku="SKU-IP15-IG",
            url="https://instagram.com/p/story123",
            price=399000.0,
            condition=Condition.USED,
            city="Астана",
        )

        # Seed offer for S24
        self.off2_web = Offer(
            id="off_s24_web",
            product_id=self.prod2.id,
            seller_id=self.seller.id,
            channel_id=self.chan_web.id,
            external_sku="SKU-S24-W",
            url="https://4mobile.kz/s24",
            price=380000.0,
            condition=Condition.NEW,
            city="Алматы",
        )

        self.offer_repo.save_or_update(self.off1_web)
        self.offer_repo.save_or_update(self.off1_kaspi)
        self.offer_repo.save_or_update(self.off1_soc)
        self.offer_repo.save_or_update(self.off2_web)

    def tearDown(self):
        self.conn.close()

    def test_get_by_id_or_slug(self):
        by_id = self.seller_repo.get_by_id_or_slug("seller_4mobile")
        self.assertIsNotNone(by_id)
        self.assertEqual(by_id.name, "4mobile")

        by_slug = self.seller_repo.get_by_id_or_slug("4mobile")
        self.assertIsNotNone(by_slug)
        self.assertEqual(by_slug.id, "seller_4mobile")

        self.assertIsNone(self.seller_repo.get_by_id_or_slug("unknown_merchant"))

    def test_list_sellers_summary(self):
        summaries = self.seller_repo.list_sellers_summary()
        self.assertEqual(len(summaries), 1)
        item = summaries[0]
        self.assertEqual(item["slug"], "4mobile")
        self.assertEqual(item["active_offers_count"], 4)
        self.assertEqual(item["channels_count"], 3)

        # Filter by search
        search_res = self.seller_repo.list_sellers_summary(search="4mob")
        self.assertEqual(len(search_res), 1)
        no_res = self.seller_repo.list_sellers_summary(search="NonExistent")
        self.assertEqual(len(no_res), 0)

        # Filter by channel_type
        chan_res = self.seller_repo.list_sellers_summary(channel_type="instagram")
        self.assertEqual(len(chan_res), 1)
        no_chan = self.seller_repo.list_sellers_summary(channel_type="telegram")
        self.assertEqual(len(no_chan), 0)

    def test_get_active_offers_with_details_for_seller(self):
        # All offers
        offers = self.offer_repo.get_active_offers_with_details_for_seller(self.seller.id)
        self.assertEqual(len(offers), 4)

        # Filter by condition
        used_offers = self.offer_repo.get_active_offers_with_details_for_seller(
            self.seller.id, condition="USED"
        )
        self.assertEqual(len(used_offers), 1)
        self.assertEqual(used_offers[0]["id"], "off_ip15_soc")
        self.assertEqual(used_offers[0]["condition"], "USED")
        self.assertEqual(used_offers[0]["channel_name"], "Instagram Stories & Shop")

        # Filter by city
        astana_offers = self.offer_repo.get_active_offers_with_details_for_seller(
            self.seller.id, city="Астана"
        )
        self.assertEqual(len(astana_offers), 3)

        # Count active offers
        total_count = self.offer_repo.count_active_offers_for_seller(self.seller.id)
        self.assertEqual(total_count, 4)
        used_count = self.offer_repo.count_active_offers_for_seller(self.seller.id, condition="б/у")
        self.assertEqual(used_count, 1)

    def test_seller_aggregates(self):
        aggs = self.offer_repo.get_seller_aggregates(self.seller.id)
        self.assertEqual(aggs["conditions_breakdown"], {"NEW": 3, "USED": 1})
        self.assertIn("Астана", aggs["cities"])
        self.assertIn("Алматы", aggs["cities"])
        self.assertEqual(aggs["categories"], ["smartphones"])

    def test_group_seller_offers_by_product(self):
        offers = self.offer_repo.get_active_offers_with_details_for_seller(self.seller.id)
        grouped = group_seller_offers_by_product(offers)

        # 4 offers correspond to 2 canonical products
        self.assertEqual(len(grouped), 2)

        # First product should be S24 (min_price 380 000) or iPhone 15 (min_price 399 000)
        # S24 has price 380 000 -> comes first
        s24_item = grouped[0]
        self.assertEqual(s24_item["product_id"], self.prod2.id)
        self.assertEqual(s24_item["min_price"], 380000.0)
        self.assertEqual(len(s24_item["channels"]), 1)

        # iPhone 15 has 3 channels: Website, Kaspi, Instagram
        ip15_item = grouped[1]
        self.assertEqual(ip15_item["product_id"], self.prod1.id)
        self.assertEqual(ip15_item["min_price"], 399000.0)
        self.assertEqual(ip15_item["max_price"], 470000.0)
        self.assertEqual(len(ip15_item["channels"]), 3)
        self.assertEqual(ip15_item["conditions"], ["NEW", "USED"])

        # Check channel order: Instagram (399k) -> Website (440k) -> Kaspi (470k)
        channels = ip15_item["channels"]
        self.assertEqual(channels[0]["price"], 399000.0)
        self.assertEqual(channels[0]["channel_type"], ChannelType.INSTAGRAM)
        self.assertEqual(channels[1]["price"], 440000.0)
        self.assertEqual(channels[1]["channel_type"], ChannelType.WEBSITE)
        self.assertEqual(channels[2]["price"], 470000.0)
        self.assertEqual(channels[2]["channel_type"], ChannelType.KASPI)

    def test_build_seller_showcase(self):
        offers = self.offer_repo.get_active_offers_with_details_for_seller(self.seller.id)
        grouped = group_seller_offers_by_product(offers)
        channels = self.channel_repo.list_by_seller(self.seller.id)
        aggs = self.offer_repo.get_seller_aggregates(self.seller.id)

        showcase = build_seller_showcase(
            seller=self.seller,
            channels=channels,
            grouped_products=grouped,
            total_offers_count=len(offers),
            available_cities=aggs["cities"],
            available_categories=aggs["categories"],
            conditions_breakdown=aggs["conditions_breakdown"],
        )

        self.assertEqual(showcase["seller"]["name"], "4mobile")
        self.assertEqual(len(showcase["channels"]), 3)
        self.assertEqual(showcase["channel_types_summary"]["website"], 1)
        self.assertEqual(showcase["channel_types_summary"]["kaspi"], 1)
        self.assertEqual(showcase["channel_types_summary"]["instagram"], 1)
        self.assertEqual(showcase["stats"]["total_offers"], 4)
        self.assertEqual(showcase["stats"]["products_count"], 2)
        self.assertEqual(showcase["stats"]["min_price"], 380000.0)
        self.assertEqual(showcase["stats"]["max_price"], 470000.0)

    def test_out_of_stock_and_expired_offers_do_not_determine_min_price(self):
        """A04: истёкшие и отсутствующие предложения не становятся минимальной ценой покупки."""
        raw_offers = [
            {
                "id": "off_expired",
                "product_id": "prod_phone",
                "product_title": "Audit Phone",
                "price": 1.0,
                "availability": "out_of_stock",
                "expires_at": "2020-01-01T00:00:00+00:00",
                "condition": "NEW",
            },
            {
                "id": "off_active",
                "product_id": "prod_phone",
                "product_title": "Audit Phone",
                "price": 100000.0,
                "availability": "in_stock",
                "expires_at": "2099-01-01T00:00:00+00:00",
                "condition": "NEW",
            },
        ]
        grouped = group_seller_offers_by_product(raw_offers)
        self.assertEqual(len(grouped), 1)
        item = grouped[0]
        # Минимальная доступная цена должна быть 100 000, а не 1.0!
        self.assertEqual(item["min_price"], 100000.0)
        self.assertTrue(item["has_purchasable"])


if __name__ == "__main__":
    unittest.main()
