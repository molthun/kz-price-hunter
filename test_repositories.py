"""
Tests for Repositories in KZ Price Hunter 2.0.
"""

import test_support  # must be first
import sqlite3
import unittest

from domain.models import ChannelType, Condition, Offer
from repositories import (
    ChannelRepository,
    OfferRepository,
    ProductRepository,
    SellerRepository,
    init_schema_v2,
)


class TestRepositories(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        init_schema_v2(self.conn)
        self.seller_repo = SellerRepository(self.conn)
        self.channel_repo = ChannelRepository(self.conn)
        self.product_repo = ProductRepository(self.conn)
        self.offer_repo = OfferRepository(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_schema_v2_idempotency(self):
        # Calling init_schema_v2 multiple times must not raise
        init_schema_v2(self.conn)
        init_schema_v2(self.conn)

    def test_seller_lifecycle(self):
        seller = self.seller_repo.get_or_create(
            slug="4mobile",
            name="4mobile",
            domain="4mobile.kz",
            phone="+77001234567",
        )
        self.assertEqual(seller.slug, "4mobile")
        self.assertEqual(seller.name, "4mobile")

        # Second call returns same seller
        seller2 = self.seller_repo.get_or_create(
            slug="4mobile",
            name="4mobile Store",
        )
        self.assertEqual(seller.id, seller2.id)

        # Lookup by slug and id
        by_slug = self.seller_repo.get_by_slug("4mobile")
        self.assertIsNotNone(by_slug)
        self.assertEqual(by_slug.id, seller.id)

        by_id = self.seller_repo.get_by_id(seller.id)
        self.assertIsNotNone(by_id)
        self.assertEqual(by_id.slug, "4mobile")

        # List sellers
        sellers = self.seller_repo.list_sellers()
        self.assertEqual(len(sellers), 1)

    def test_channel_lifecycle(self):
        seller = self.seller_repo.get_or_create(slug="dns", name="DNS Казахстан")
        chan_direct = self.channel_repo.get_or_create(
            seller_id=seller.id,
            channel_type=ChannelType.DIRECT,
            name="DNS Магазин",
            base_url="https://www.dns-shop.kz",
        )
        self.assertEqual(chan_direct.channel_type, ChannelType.DIRECT)

        chan_kaspi = self.channel_repo.get_or_create(
            seller_id=seller.id,
            channel_type=ChannelType.KASPI,
            name="DNS на Kaspi",
            external_store_id="dns_kaspi_id_999",
        )
        self.assertEqual(chan_kaspi.channel_type, ChannelType.KASPI)

        # List channels for seller
        channels = self.channel_repo.list_by_seller(seller.id)
        self.assertEqual(len(channels), 2)

    def test_canonical_product_and_cross_channel_pricing(self):
        # 1. Create canonical product
        product = self.product_repo.get_or_create_canonical(
            canonical_key="apple:iphone:16:pro:256gb",
            title="Смартфон Apple iPhone 16 Pro 256GB Black Titanium",
            category="smartphones",
            brand="Apple",
            model="iPhone 16 Pro",
        )
        self.assertTrue(product.id.startswith("prod_"))

        # 2. Create sellers
        seller_4mobile = self.seller_repo.get_or_create(slug="4mobile", name="4mobile")
        seller_mechta = self.seller_repo.get_or_create(slug="mechta", name="Мечта")

        # 3. Create channels
        chan_4m_direct = self.channel_repo.get_or_create(
            seller_id=seller_4mobile.id,
            channel_type=ChannelType.DIRECT,
            name="4mobile Direct",
        )
        chan_4m_kaspi = self.channel_repo.get_or_create(
            seller_id=seller_4mobile.id,
            channel_type=ChannelType.KASPI,
            name="4mobile Kaspi",
            external_store_id="4m_kaspi",
        )
        chan_mechta_direct = self.channel_repo.get_or_create(
            seller_id=seller_mechta.id,
            channel_type=ChannelType.DIRECT,
            name="Мечта Магазин",
        )

        # 4. Create Offers
        # Offer A: 4mobile direct @ 499 000
        off1 = Offer(
            id="off_4m_direct_1",
            product_id=product.id,
            seller_id=seller_4mobile.id,
            channel_id=chan_4m_direct.id,
            external_sku="SKU-4M-1",
            url="https://4mobile.kz/p/1",
            price=499000.0,
            old_price=520000.0,
            condition=Condition.NEW,
            city="Астана",
        )
        self.offer_repo.save_or_update(off1)

        # Offer B: 4mobile on Kaspi @ 539 000 (Cross-channel)
        off2 = Offer(
            id="off_4m_kaspi_1",
            product_id=product.id,
            seller_id=seller_4mobile.id,
            channel_id=chan_4m_kaspi.id,
            external_sku="SKU-4M-KASPI",
            url="https://kaspi.kz/shop/p/1",
            price=539000.0,
            condition=Condition.NEW,
            city="Астана",
            payment_methods=["installment_24"],
        )
        self.offer_repo.save_or_update(off2)

        # Offer C: Mechta direct @ 519 000 (Cross-seller)
        off3 = Offer(
            id="off_mechta_1",
            product_id=product.id,
            seller_id=seller_mechta.id,
            channel_id=chan_mechta_direct.id,
            external_sku="SKU-MECHTA-1",
            url="https://mechta.kz/p/1",
            price=519000.0,
            old_price=549000.0,
            condition=Condition.NEW,
            city="Астана",
        )
        self.offer_repo.save_or_update(off3)

        # Offer D: Used offer @ 390 000 (Condition USED)
        off_used = Offer(
            id="off_used_1",
            product_id=product.id,
            seller_id=seller_4mobile.id,
            channel_id=chan_4m_direct.id,
            external_sku="SKU-4M-USED",
            url="https://4mobile.kz/p/used",
            price=390000.0,
            condition=Condition.USED,
            city="Астана",
        )
        self.offer_repo.save_or_update(off_used)

        # 5. Queries:
        # Query NEW offers for product sorted by price
        new_offers = self.offer_repo.get_active_offers_for_product(
            product_id=product.id, condition=Condition.NEW
        )
        self.assertEqual(len(new_offers), 3)
        # Check sort order: 499 000, 519 000, 539 000
        self.assertEqual(new_offers[0].price, 499000.0)
        self.assertEqual(new_offers[0].channel_id, chan_4m_direct.id)
        self.assertEqual(new_offers[1].price, 519000.0)
        self.assertEqual(new_offers[1].seller_id, seller_mechta.id)
        self.assertEqual(new_offers[2].price, 539000.0)
        self.assertEqual(new_offers[2].channel_id, chan_4m_kaspi.id)

        # Query USED offers: separate from NEW best price!
        used_offers = self.offer_repo.get_active_offers_for_product(
            product_id=product.id, condition=Condition.USED
        )
        self.assertEqual(len(used_offers), 1)
        self.assertEqual(used_offers[0].price, 390000.0)

    def test_price_history_recording(self):
        seller = self.seller_repo.get_or_create(slug="sulpak", name="Sulpak")
        channel = self.channel_repo.get_or_create(
            seller_id=seller.id, channel_type=ChannelType.DIRECT, name="Sulpak Web"
        )
        prod = self.product_repo.get_or_create_canonical(
            canonical_key="rtx:5090", title="GeForce RTX 5090", category="components"
        )
        from domain.models import Offer
        offer = Offer(
            id="off_sulpak_rtx5090",
            product_id=prod.id,
            seller_id=seller.id,
            channel_id=channel.id,
            external_sku="SKU-RTX",
            url="https://sulpak.kz/p/rtx",
            price=1200000.0,
            city="Алматы",
        )
        self.offer_repo.save_or_update(offer)

        # Add price points
        self.offer_repo.add_price_observation("off_sulpak_rtx5090", 1200000.0, observed_at="2026-09-20T10:00:00Z")
        self.offer_repo.add_price_observation("off_sulpak_rtx5090", 1150000.0, old_price=1200000.0, observed_at="2026-09-22T10:00:00Z")
        self.offer_repo.add_price_observation("off_sulpak_rtx5090", 1100000.0, old_price=1150000.0, observed_at="2026-09-25T10:00:00Z")

        history = self.offer_repo.get_price_history("off_sulpak_rtx5090")
        self.assertEqual(len(history), 3)
        self.assertEqual(history[0].price, 1200000.0)
        self.assertEqual(history[2].price, 1100000.0)

        # B3 check: limit=2 should return the 2 newest points in chronological order
        history_limit = self.offer_repo.get_price_history("off_sulpak_rtx5090", limit=2)
        self.assertEqual(len(history_limit), 2)
        self.assertEqual(history_limit[0].price, 1150000.0)
        self.assertEqual(history_limit[1].price, 1100000.0)


if __name__ == "__main__":
    unittest.main()
