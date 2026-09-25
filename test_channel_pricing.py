"""
Tests for Channel-Aware Pricing, Cross-Channel savings, and Cross-Seller rankings (KZ Price Hunter 2.0).
Verifies:
1. Cross-Channel pricing: Direct vs Marketplace savings for the same merchant.
2. Cross-Seller rankings: Independent merchant competition on canonical products.
3. PricingRepository queries and matrix construction.
"""

import test_support  # must be first
import sqlite3
import unittest

from domain.channel_pricing import (
    ChannelSavings,
    CrossSellerRanking,
    DeliveryType,
    PaymentMethod,
    ProductChannelMatrix,
    SellerChannelPricing,
    build_channel_matrix,
)
from domain.models import (
    CanonicalProduct,
    Channel,
    ChannelType,
    Condition,
    Offer,
    Seller,
)
from repositories import (
    ChannelRepository,
    OfferRepository,
    PricingRepository,
    ProductRepository,
    SellerRepository,
    init_schema_v2,
)


class TestChannelPricingDomain(unittest.TestCase):
    def setUp(self):
        self.seller_4mobile = Seller(
            id="sel_4mobile",
            slug="4mobile",
            name="4mobile.kz",
            is_active=True,
        )
        self.seller_mechta = Seller(
            id="sel_mechta",
            slug="mechta",
            name="Мечта",
            is_active=True,
        )
        self.seller_sulpak = Seller(
            id="sel_sulpak",
            slug="sulpak",
            name="Sulpak",
            is_active=True,
        )
        self.sellers_map = {
            self.seller_4mobile.id: self.seller_4mobile,
            self.seller_mechta.id: self.seller_mechta,
            self.seller_sulpak.id: self.seller_sulpak,
        }

    def test_payment_and_delivery_constants(self):
        self.assertIn("cash", PaymentMethod.ALL)
        self.assertIn("kaspi_qr", PaymentMethod.ALL)
        self.assertIn("installment", PaymentMethod.ALL)
        self.assertIn("free", DeliveryType.ALL)
        self.assertIn("pickup", DeliveryType.ALL)

    def test_cross_channel_savings_4mobile_case(self):
        """
        Case 4mobile:
        Direct: 499 000 ₸ (website)
        Kaspi: 539 000 ₸ (installment 24 mos)
        """
        direct_offer = Offer(
            id="off_4m_dir",
            product_id="prod_ip15",
            seller_id="sel_4mobile",
            channel_id="chan_fourmobile_direct",
            external_sku="SKU-4M-001",
            url="https://4mobile.kz/p/iphone15",
            price=499000.0,
            city="Алматы",
            is_active=True,
            installment_available=False,
            pickup_available=True,
        )
        kaspi_offer = Offer(
            id="off_4m_kaspi",
            product_id="prod_ip15",
            seller_id="sel_4mobile",
            channel_id="kaspi:seller:4mobile",
            external_sku="KASPI-4M-001",
            url="https://kaspi.kz/shop/p/iphone15-4m",
            price=539000.0,
            city="Алматы",
            is_active=True,
            installment_available=True,
            installment_months=24,
            pickup_available=False,
        )

        matrix = build_channel_matrix(
            product_id="prod_ip15",
            canonical_key="apple-iphone-15-128gb-black",
            title="Смартфон Apple iPhone 15 128Gb черный",
            offers=[direct_offer, kaspi_offer],
            sellers_map=self.sellers_map,
            condition=Condition.NEW,
            city="Алматы",
        )

        self.assertEqual(matrix.best_overall_price, 499000.0)
        self.assertEqual(matrix.best_direct_price, 499000.0)
        self.assertEqual(matrix.best_marketplace_price, 539000.0)

        # 1 seller represented
        self.assertEqual(len(matrix.sellers_pricing), 1)
        sp = matrix.sellers_pricing[0]
        self.assertEqual(sp.seller_name, "4mobile.kz")
        self.assertEqual(sp.min_price, 499000.0)
        self.assertIsNotNone(sp.direct_offer)
        self.assertEqual(len(sp.marketplace_offers), 1)

        # Cross-channel savings verified
        self.assertEqual(len(matrix.cross_channel_opportunities), 1)
        opp = matrix.cross_channel_opportunities[0]
        self.assertEqual(opp.seller_id, "sel_4mobile")
        self.assertEqual(opp.direct_price, 499000.0)
        self.assertEqual(opp.marketplace_price, 539000.0)
        self.assertEqual(opp.savings_amount, 40000.0)
        self.assertEqual(opp.savings_percent, 7.4)
        self.assertEqual(opp.installment_months, 24)
        self.assertIn("Экономия 40,000 ₸ (7.4%)", opp.tradeoff_note)
        self.assertIn("рассрочку на 24 мес.", opp.tradeoff_note)

        # Verify to_dict serialization
        m_dict = matrix.to_dict()
        self.assertEqual(m_dict["best_overall_price"], 499000.0)
        self.assertEqual(len(m_dict["cross_channel_opportunities"]), 1)

    def test_cross_seller_competition_ranking(self):
        """
        Three independent sellers competing on iPhone 15:
        4mobile: 499 000 ₸ (Direct)
        Мечта: 519 990 ₸ (Direct)
        Sulpak: 529 990 ₸ (Direct)
        """
        off_4m = Offer(
            id="o1",
            product_id="p1",
            seller_id="sel_4mobile",
            channel_id="chan_fourmobile_direct",
            external_sku="SKU1",
            url="https://4mobile.kz/1",
            price=499000.0,
            city="Алматы",
        )
        off_mechta = Offer(
            id="o2",
            product_id="p1",
            seller_id="sel_mechta",
            channel_id="chan_mechta_direct",
            external_sku="SKU2",
            url="https://mechta.kz/1",
            price=519990.0,
            city="Алматы",
        )
        off_sulpak = Offer(
            id="o3",
            product_id="p1",
            seller_id="sel_sulpak",
            channel_id="chan_sulpak_direct",
            external_sku="SKU3",
            url="https://sulpak.kz/1",
            price=529990.0,
            city="Алматы",
        )

        matrix = build_channel_matrix(
            product_id="p1",
            canonical_key="iphone-15",
            title="iPhone 15",
            offers=[off_sulpak, off_4m, off_mechta],
            sellers_map=self.sellers_map,
            city="Алматы",
        )

        self.assertEqual(matrix.best_overall_price, 499000.0)
        self.assertEqual(len(matrix.cross_seller_rankings), 3)

        # Leader: 4mobile
        r1 = matrix.cross_seller_rankings[0]
        self.assertEqual(r1.rank, 1)
        self.assertEqual(r1.seller_id, "sel_4mobile")
        self.assertEqual(r1.best_price, 499000.0)
        self.assertEqual(r1.price_difference_from_leader, 0.0)

        # 2nd: Mechta
        r2 = matrix.cross_seller_rankings[1]
        self.assertEqual(r2.rank, 2)
        self.assertEqual(r2.seller_id, "sel_mechta")
        self.assertEqual(r2.best_price, 519990.0)
        self.assertEqual(r2.price_difference_from_leader, 20990.0)

        # 3rd: Sulpak
        r3 = matrix.cross_seller_rankings[2]
        self.assertEqual(r3.rank, 3)
        self.assertEqual(r3.seller_id, "sel_sulpak")
        self.assertEqual(r3.best_price, 529990.0)
        self.assertEqual(r3.price_difference_from_leader, 30990.0)

        # No cross-channel opportunities because each seller only has 1 direct channel
        self.assertEqual(len(matrix.cross_channel_opportunities), 0)

    def test_marketplace_cheaper_than_direct(self):
        """
        If marketplace has a promo where marketplace price is lower than direct,
        no direct savings opportunity should be reported, and best offer should be marketplace.
        """
        direct_offer = Offer(
            id="o_dir",
            product_id="p1",
            seller_id="sel_mechta",
            channel_id="chan_mechta_direct",
            external_sku="SKU-M1",
            url="https://mechta.kz/dir",
            price=200000.0,
            city="Алматы",
        )
        kaspi_offer = Offer(
            id="o_kaspi",
            product_id="p1",
            seller_id="sel_mechta",
            channel_id="kaspi:seller:mechta",
            external_sku="SKU-MK1",
            url="https://kaspi.kz/m1",
            price=190000.0,
            city="Алматы",
        )

        matrix = build_channel_matrix(
            product_id="p1",
            canonical_key="p1-key",
            title="Promo Item",
            offers=[direct_offer, kaspi_offer],
            sellers_map=self.sellers_map,
            city="Алматы",
        )

        self.assertEqual(matrix.best_overall_price, 190000.0)
        self.assertEqual(matrix.best_marketplace_price, 190000.0)
        self.assertEqual(matrix.best_direct_price, 200000.0)
        # Direct is not cheaper, so no cross-channel direct savings
        self.assertEqual(len(matrix.cross_channel_opportunities), 0)

        # But ranking correctly reflects the best offer is Kaspi
        sp = matrix.sellers_pricing[0]
        self.assertEqual(sp.min_price, 190000.0)
        self.assertEqual(sp.best_offer.id, "o_kaspi")
        self.assertEqual(matrix.cross_seller_rankings[0].best_channel_type, ChannelType.KASPI)

    def test_empty_offers_returns_empty_matrix(self):
        matrix = build_channel_matrix(
            product_id="empty_prod",
            canonical_key="empty",
            title="Empty",
            offers=[],
            sellers_map={},
        )
        self.assertEqual(matrix.best_overall_price, 0.0)
        self.assertIsNone(matrix.best_direct_price)
        self.assertIsNone(matrix.best_marketplace_price)
        self.assertEqual(len(matrix.sellers_pricing), 0)
        self.assertEqual(len(matrix.cross_channel_opportunities), 0)
        self.assertEqual(len(matrix.cross_seller_rankings), 0)


class TestPricingRepositoryIntegration(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_schema_v2(self.conn)

        self.product_repo = ProductRepository(self.conn)
        self.seller_repo = SellerRepository(self.conn)
        self.channel_repo = ChannelRepository(self.conn)
        self.offer_repo = OfferRepository(self.conn)
        self.pricing_repo = PricingRepository(self.conn)

        # Seed Product
        self.product = CanonicalProduct(
            id="prod_airpods",
            canonical_key="apple-airpods-pro-2",
            title="Наушники Apple AirPods Pro 2",
            category="Наушники",
            brand="Apple",
        )
        self.product_repo.save_or_update(self.product)

        # Seed Sellers
        self.seller_4m = Seller(
            id="sel_4m",
            slug="4mobile",
            name="4mobile",
            is_active=True,
        )
        self.seller_repo.save_or_update(self.seller_4m)

        self.seller_mechta = Seller(
            id="sel_m",
            slug="mechta",
            name="Мечта",
            is_active=True,
        )
        self.seller_repo.save_or_update(self.seller_mechta)

        # Seed Channels
        self.chan_4m_dir = Channel(
            id="chan_4m_dir",
            seller_id=self.seller_4m.id,
            channel_type=ChannelType.DIRECT,
            name="4mobile Direct",
        )
        self.chan_4m_kaspi = Channel(
            id="kaspi:seller:4m",
            seller_id=self.seller_4m.id,
            channel_type=ChannelType.KASPI,
            name="4mobile on Kaspi",
        )
        self.chan_m_dir = Channel(
            id="chan_m_dir",
            seller_id=self.seller_mechta.id,
            channel_type=ChannelType.DIRECT,
            name="Мечта Direct",
        )
        for ch in [self.chan_4m_dir, self.chan_4m_kaspi, self.chan_m_dir]:
            self.channel_repo.save_or_update(ch)

    def tearDown(self):
        self.conn.close()

    def test_get_product_channel_matrix_integration(self):
        # 4mobile direct: 115 000 ₸
        off_dir = Offer(
            id="off_air_4m_dir",
            product_id=self.product.id,
            seller_id=self.seller_4m.id,
            channel_id=self.chan_4m_dir.id,
            external_sku="SKU-AIR-DIR",
            url="https://4mobile.kz/airpods",
            price=115000.0,
            city="Алматы",
            installment_available=False,
            pickup_available=True,
            is_active=True,
        )
        # 4mobile kaspi: 129 990 ₸ with 12 mos installment
        off_kaspi = Offer(
            id="off_air_4m_kaspi",
            product_id=self.product.id,
            seller_id=self.seller_4m.id,
            channel_id=self.chan_4m_kaspi.id,
            external_sku="SKU-AIR-KASPI",
            url="https://kaspi.kz/airpods",
            price=129990.0,
            city="Алматы",
            installment_available=True,
            installment_months=12,
            pickup_available=False,
            is_active=True,
        )
        # Mechta direct: 124 990 ₸
        off_mechta = Offer(
            id="off_air_m_dir",
            product_id=self.product.id,
            seller_id=self.seller_mechta.id,
            channel_id=self.chan_m_dir.id,
            external_sku="SKU-AIR-MECHTA",
            url="https://mechta.kz/airpods",
            price=124990.0,
            city="Алматы",
            installment_available=True,
            installment_months=6,
            pickup_available=True,
            is_active=True,
        )

        for off in [off_dir, off_kaspi, off_mechta]:
            self.offer_repo.save_or_update(off)

        matrix = self.pricing_repo.get_product_channel_matrix(
            product_id=self.product.id,
            city="Алматы",
        )

        self.assertIsNotNone(matrix)
        self.assertEqual(matrix.product_id, self.product.id)
        self.assertEqual(matrix.best_overall_price, 115000.0)
        self.assertEqual(matrix.best_direct_price, 115000.0)
        self.assertEqual(matrix.best_marketplace_price, 129990.0)

        # Cross seller rankings
        self.assertEqual(len(matrix.cross_seller_rankings), 2)
        self.assertEqual(matrix.cross_seller_rankings[0].seller_id, "sel_4m")
        self.assertEqual(matrix.cross_seller_rankings[0].best_price, 115000.0)
        self.assertEqual(matrix.cross_seller_rankings[1].seller_id, "sel_m")
        self.assertEqual(matrix.cross_seller_rankings[1].best_price, 124990.0)

        # Cross channel savings
        self.assertEqual(len(matrix.cross_channel_opportunities), 1)
        savings = matrix.cross_channel_opportunities[0]
        self.assertEqual(savings.seller_id, "sel_4m")
        self.assertEqual(savings.savings_amount, 14990.0)
        self.assertAlmostEqual(savings.savings_percent, 11.5, places=1)
        self.assertEqual(savings.installment_months, 12)

    def test_find_cross_channel_savings_global(self):
        # Insert 4mobile offers
        self.offer_repo.save_or_update(
            Offer(
                id="off_1",
                product_id=self.product.id,
                seller_id=self.seller_4m.id,
                channel_id=self.chan_4m_dir.id,
                external_sku="SKU100",
                url="https://4mobile.kz/100",
                price=100000.0,
                city="Казахстан",
                is_active=True,
            )
        )
        self.offer_repo.save_or_update(
            Offer(
                id="off_2",
                product_id=self.product.id,
                seller_id=self.seller_4m.id,
                channel_id=self.chan_4m_kaspi.id,
                external_sku="SKU101",
                url="https://kaspi.kz/101",
                price=110000.0,
                city="Казахстан",
                installment_available=True,
                installment_months=24,
                is_active=True,
            )
        )

        deals = self.pricing_repo.find_cross_channel_savings(min_savings_pct=5.0)
        self.assertEqual(len(deals), 1)
        deal = deals[0]
        self.assertEqual(deal["product_id"], self.product.id)
        self.assertEqual(deal["seller_id"], "sel_4m")
        self.assertEqual(deal["direct_price"], 100000.0)
        self.assertEqual(deal["marketplace_price"], 110000.0)
        self.assertEqual(deal["savings_amount"], 10000.0)
        self.assertAlmostEqual(deal["savings_percent"], 9.1, places=1)
        self.assertEqual(deal["installment_months"], 24)

    def test_nonexistent_product_returns_none(self):
        matrix = self.pricing_repo.get_product_channel_matrix("non_existent_id")
        self.assertIsNone(matrix)

    def test_offer_attributes_persistence(self):
        """Verify new schema fields (installment_available, credit_available, pickup_available) persist accurately."""
        off = Offer(
            id="off_attr_test",
            product_id=self.product.id,
            seller_id=self.seller_4m.id,
            channel_id=self.chan_4m_dir.id,
            external_sku="SKU-ATTR",
            url="https://example.com/test",
            price=50000.0,
            installment_available=True,
            installment_months=12,
            credit_available=True,
            pickup_available=True,
            delivery_type=DeliveryType.FREE,
            payment_methods=[PaymentMethod.KASPI_QR, PaymentMethod.CASH],
        )
        self.offer_repo.save_or_update(off)

        loaded = self.offer_repo.get_by_id("off_attr_test")
        self.assertIsNotNone(loaded)
        self.assertTrue(loaded.installment_available)
        self.assertEqual(loaded.installment_months, 12)
        self.assertTrue(loaded.credit_available)
        self.assertTrue(loaded.pickup_available)
        self.assertEqual(loaded.delivery_type, "free")
        self.assertIn("kaspi_qr", loaded.payment_methods)

    def test_city_filtering_in_matrix(self):
        """Offers in specific city should be respected."""
        off_almaty = Offer(
            id="off_alm",
            product_id=self.product.id,
            seller_id=self.seller_4m.id,
            channel_id=self.chan_4m_dir.id,
            external_sku="SKU-ALM",
            url="https://4mobile.kz/alm",
            price=100000.0,
            city="Алматы",
        )
        off_astana = Offer(
            id="off_ast",
            product_id=self.product.id,
            seller_id=self.seller_mechta.id,
            channel_id=self.chan_m_dir.id,
            external_sku="SKU-AST",
            url="https://mechta.kz/ast",
            price=95000.0,
            city="Астана",
        )
        self.offer_repo.save_or_update(off_almaty)
        self.offer_repo.save_or_update(off_astana)

        # In Almaty, only 4mobile offer is active
        matrix_alm = self.pricing_repo.get_product_channel_matrix(self.product.id, city="Алматы")
        self.assertEqual(matrix_alm.best_overall_price, 100000.0)
        self.assertEqual(len(matrix_alm.cross_seller_rankings), 1)
        self.assertEqual(matrix_alm.cross_seller_rankings[0].seller_id, "sel_4m")

        # In Astana, only Mechta offer is active
        matrix_ast = self.pricing_repo.get_product_channel_matrix(self.product.id, city="Астана")
        self.assertEqual(matrix_ast.best_overall_price, 95000.0)
        self.assertEqual(len(matrix_ast.cross_seller_rankings), 1)
        self.assertEqual(matrix_ast.cross_seller_rankings[0].seller_id, "sel_m")


if __name__ == "__main__":
    unittest.main()

