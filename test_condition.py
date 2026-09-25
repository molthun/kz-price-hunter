"""
Tests for Condition / Second-hand market (KZ Price Hunter 2.0).
Verifies:
1. Normalization of NEW, USED, REFURBISHED, OPEN_BOX and UNKNOWN defaults (Auditing B2 & уценка).
2. Trap words avoidance (бумага, обувь, тумбу, etc.).
3. Strict non-contamination invariant: Used/Open Box prices never contaminate 'best new price'.
4. ConditionBreakdown price tier calculation.
5. Integration with PricingRepository and SQLite.
"""

import test_support  # must be first
import sqlite3
import unittest

from domain.channel_pricing import (
    ProductChannelMatrix,
    build_channel_matrix,
)
from domain.condition import (
    ConditionBreakdown,
    ConditionFilter,
    build_condition_breakdown,
    filter_offers_by_condition,
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


class TestConditionNormalizationAndTraps(unittest.TestCase):
    def test_default_behavior_is_unknown(self):
        """B2 audit requirement: absence of data is UNKNOWN, not NEW."""
        self.assertEqual(Condition.normalize(None), Condition.UNKNOWN)
        self.assertEqual(Condition.normalize(""), Condition.UNKNOWN)
        self.assertEqual(Condition.normalize("   "), Condition.UNKNOWN)

    def test_contextual_retail_default_is_new(self):
        """When explicitly specified for trusted retailers, default is NEW."""
        self.assertEqual(Condition.normalize(None, default=Condition.NEW), Condition.NEW)
        self.assertEqual(Condition.normalize("", default=Condition.NEW), Condition.NEW)

    def test_trap_words_not_mistaken_for_used(self):
        """Words containing 'бу' as a substring must NOT trigger USED."""
        traps = [
            "бумага офисная А4",
            "тумбу под телевизор",
            "обувь мужская зимняя",
            "бутылка для воды",
            "букет цветов",
            "бамбуковый столик",
            "трибуна для выступлений",
        ]
        for trap in traps:
            self.assertNotEqual(
                Condition.normalize(trap),
                Condition.USED,
                f"Trap '{trap}' falsely recognized as USED",
            )

    def test_trap_words_not_mistaken_for_new(self):
        """Words containing 'нов' as a substring must NOT falsely trigger NEW without context."""
        traps = [
            "основа для макияжа",
            "инновационный датчик",
            "установка кондиционера",
        ]
        for trap in traps:
            self.assertEqual(
                Condition.normalize(trap),
                Condition.UNKNOWN,
                f"Trap '{trap}' falsely recognized as NEW",
            )

    def test_open_box_and_discount_nuances(self):
        """Audit requirement: 'уценка' and 'витрина' should be OPEN_BOX, not USED."""
        self.assertEqual(Condition.normalize("уценка"), Condition.OPEN_BOX)
        self.assertEqual(Condition.normalize("Уцененный товар"), Condition.OPEN_BOX)
        self.assertEqual(Condition.normalize("витринный образец"), Condition.OPEN_BOX)
        self.assertEqual(Condition.normalize("витрина"), Condition.OPEN_BOX)
        self.assertEqual(Condition.normalize("повреждена упаковка"), Condition.OPEN_BOX)
        self.assertEqual(Condition.normalize("вскрыта коробка"), Condition.OPEN_BOX)
        self.assertEqual(Condition.normalize("open box"), Condition.OPEN_BOX)
        self.assertEqual(Condition.normalize("распакованный"), Condition.OPEN_BOX)

    def test_refurbished_nuances(self):
        """Refurbished / repaired goods."""
        self.assertEqual(Condition.normalize("Refurbished"), Condition.REFURBISHED)
        self.assertEqual(Condition.normalize("восстановленный"), Condition.REFURBISHED)
        self.assertEqual(Condition.normalize("после ремонта"), Condition.REFURBISHED)
        self.assertEqual(Condition.normalize("после сц"), Condition.REFURBISHED)
        self.assertEqual(Condition.normalize("отремонтированный"), Condition.REFURBISHED)

    def test_used_nuances(self):
        """Real used items."""
        self.assertEqual(Condition.normalize("б/у"), Condition.USED)
        self.assertEqual(Condition.normalize("БУ"), Condition.USED)
        self.assertEqual(Condition.normalize("б.у."), Condition.USED)
        self.assertEqual(Condition.normalize("бывший в употреблении"), Condition.USED)
        self.assertEqual(Condition.normalize("с пробегом"), Condition.USED)
        self.assertEqual(Condition.normalize("second-hand"), Condition.USED)


class TestConditionFilter(unittest.TestCase):
    def test_strict_isolation(self):
        self.assertTrue(ConditionFilter.matches("new", Condition.NEW))
        self.assertFalse(ConditionFilter.matches("new", Condition.USED))
        self.assertFalse(ConditionFilter.matches("new", Condition.OPEN_BOX))
        self.assertFalse(ConditionFilter.matches("new", Condition.REFURBISHED))

        self.assertTrue(ConditionFilter.matches("used", Condition.USED))
        self.assertFalse(ConditionFilter.matches("used", Condition.NEW))

        self.assertTrue(ConditionFilter.matches("open_box", Condition.OPEN_BOX))
        self.assertFalse(ConditionFilter.matches("open_box", Condition.USED))

        self.assertTrue(ConditionFilter.matches("refurbished", Condition.REFURBISHED))
        self.assertFalse(ConditionFilter.matches("refurbished", Condition.NEW))

        self.assertTrue(ConditionFilter.matches("all", Condition.NEW))
        self.assertTrue(ConditionFilter.matches("all", Condition.USED))
        self.assertTrue(ConditionFilter.matches("all", Condition.OPEN_BOX))
        self.assertTrue(ConditionFilter.matches("all", Condition.REFURBISHED))


class TestConditionSegregationAndBreakdown(unittest.TestCase):
    def setUp(self):
        self.seller_mechta = Seller(id="s_mechta", slug="mechta", name="Мечта")
        self.seller_sulpak = Seller(id="s_sulpak", slug="sulpak", name="Sulpak")
        self.seller_used = Seller(id="s_used", slug="olx_seller", name="Частный продавец")
        self.seller_masterok = Seller(id="s_masterok", slug="masterok", name="MasterOK")
        self.sellers_map = {
            self.seller_mechta.id: self.seller_mechta,
            self.seller_sulpak.id: self.seller_sulpak,
            self.seller_used.id: self.seller_used,
            self.seller_masterok.id: self.seller_masterok,
        }

        # Case from SEARCH_PLATFORM_PLAN.md: MacBook Air M5
        # New: 589 000 ₸ (Mechta), 599 000 ₸ (Sulpak)
        # Used: 430 000 ₸ (Частный продавец)
        # Open Box / Уценка: 520 000 ₸ (MasterOK витрина)
        self.off_new_1 = Offer(
            id="off_m_new",
            product_id="macbook_m5",
            seller_id="s_mechta",
            channel_id="chan_mechta_dir",
            external_sku="SKU-MB-M",
            url="https://mechta.kz/m5",
            price=589000.0,
            condition=Condition.NEW,
        )
        self.off_new_2 = Offer(
            id="off_s_new",
            product_id="macbook_m5",
            seller_id="s_sulpak",
            channel_id="chan_sulpak_dir",
            external_sku="SKU-MB-S",
            url="https://sulpak.kz/m5",
            price=599000.0,
            condition=Condition.NEW,
        )
        self.off_used = Offer(
            id="off_used_1",
            product_id="macbook_m5",
            seller_id="s_used",
            channel_id="classifieds:user123",
            external_sku="SKU-MB-USED",
            url="https://market.kz/m5-used",
            price=430000.0,
            condition=Condition.USED,
        )
        self.off_open_box = Offer(
            id="off_ob_1",
            product_id="macbook_m5",
            seller_id="s_masterok",
            channel_id="chan_masterok_dir",
            external_sku="SKU-MB-OB",
            url="https://masterok.kz/m5-ob",
            price=520000.0,
            condition=Condition.OPEN_BOX,
        )
        self.all_offers = [self.off_new_1, self.off_new_2, self.off_used, self.off_open_box]

    def test_build_condition_breakdown(self):
        breakdown = build_condition_breakdown(
            product_id="macbook_m5",
            title="Apple MacBook Air M5 16/512",
            offers=self.all_offers,
        )

        self.assertEqual(breakdown.best_new_price, 589000.0)
        self.assertEqual(breakdown.new_offers_count, 2)
        self.assertEqual(breakdown.best_used_price, 430000.0)
        self.assertEqual(breakdown.used_offers_count, 1)
        self.assertEqual(breakdown.best_open_box_price, 520000.0)
        self.assertEqual(breakdown.open_box_offers_count, 1)
        self.assertIsNone(breakdown.best_refurbished_price)
        self.assertEqual(breakdown.refurbished_offers_count, 0)
        self.assertEqual(breakdown.total_active_offers, 4)

    def test_strict_non_contamination_of_best_new_price(self):
        """
        CRITICAL INVARIANT:
        When querying for NEW goods (default search mode),
        used price (430 000 ₸) or open-box price (520 000 ₸) must NEVER
        contaminate best_overall_price.
        """
        matrix = build_channel_matrix(
            product_id="macbook_m5",
            canonical_key="macbook-air-m5-16-512",
            title="Apple MacBook Air M5 16/512",
            offers=self.all_offers,
            sellers_map=self.sellers_map,
            condition=Condition.NEW,
        )

        # 1. Best overall price for NEW must strictly be 589 000 ₸, NOT 430 000 ₸
        self.assertEqual(matrix.best_overall_price, 589000.0)
        self.assertEqual(matrix.best_new_price, 589000.0)
        self.assertEqual(matrix.best_used_price, 430000.0)
        self.assertEqual(matrix.best_open_box_price, 520000.0)

        # 2. Rankings must only contain NEW offers (Mechta & Sulpak)
        self.assertEqual(len(matrix.cross_seller_rankings), 2)
        self.assertEqual(matrix.cross_seller_rankings[0].seller_id, "s_mechta")
        self.assertEqual(matrix.cross_seller_rankings[0].best_price, 589000.0)
        self.assertEqual(matrix.cross_seller_rankings[1].seller_id, "s_sulpak")
        self.assertEqual(matrix.cross_seller_rankings[1].best_price, 599000.0)

        # 3. Used and Open Box sellers are NOT in sellers_pricing
        seller_ids_in_matrix = {sp.seller_id for sp in matrix.sellers_pricing}
        self.assertNotIn("s_used", seller_ids_in_matrix)
        self.assertNotIn("s_masterok", seller_ids_in_matrix)

    def test_used_query_mode(self):
        """When querying for USED goods, matrix reflects only used offers."""
        matrix = build_channel_matrix(
            product_id="macbook_m5",
            canonical_key="macbook-air-m5-16-512",
            title="Apple MacBook Air M5 16/512",
            offers=self.all_offers,
            sellers_map=self.sellers_map,
            condition=Condition.USED,
        )

        self.assertEqual(matrix.best_overall_price, 430000.0)
        self.assertEqual(len(matrix.cross_seller_rankings), 1)
        self.assertEqual(matrix.cross_seller_rankings[0].seller_id, "s_used")
        self.assertEqual(matrix.cross_seller_rankings[0].best_price, 430000.0)

    def test_all_query_mode(self):
        """When querying ALL goods, all sellers are represented, with overall lowest price."""
        matrix = build_channel_matrix(
            product_id="macbook_m5",
            canonical_key="macbook-air-m5-16-512",
            title="Apple MacBook Air M5 16/512",
            offers=self.all_offers,
            sellers_map=self.sellers_map,
            condition=ConditionFilter.ALL,
        )

        # Lowest overall across all conditions is used (430 000 ₸)
        self.assertEqual(matrix.best_overall_price, 430000.0)
        self.assertEqual(len(matrix.cross_seller_rankings), 4)
        # Leader is used seller
        self.assertEqual(matrix.cross_seller_rankings[0].seller_id, "s_used")
        # Breakdown still preserves separate tiers
        self.assertEqual(matrix.best_new_price, 589000.0)
        self.assertEqual(matrix.best_used_price, 430000.0)
        self.assertEqual(matrix.best_open_box_price, 520000.0)


class TestPricingRepositoryConditionIntegration(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_schema_v2(self.conn)

        self.product_repo = ProductRepository(self.conn)
        self.seller_repo = SellerRepository(self.conn)
        self.channel_repo = ChannelRepository(self.conn)
        self.offer_repo = OfferRepository(self.conn)
        self.pricing_repo = PricingRepository(self.conn)

        # Canonical Product
        self.product = CanonicalProduct(
            id="prod_rtx5090",
            canonical_key="nvidia-geforce-rtx-5090",
            title="Видеокарта NVIDIA GeForce RTX 5090 32GB",
            category="Видеокарты",
            brand="NVIDIA",
        )
        self.product_repo.save_or_update(self.product)

        # Sellers
        self.seller_dns = Seller(id="sel_dns", slug="dns", name="DNS", is_active=True)
        self.seller_priv = Seller(id="sel_priv", slug="priv", name="Частное лицо", is_active=True)
        self.seller_repo.save_or_update(self.seller_dns)
        self.seller_repo.save_or_update(self.seller_priv)

        # Channels
        self.chan_dns = Channel(
            id="chan_dns_dir",
            seller_id=self.seller_dns.id,
            channel_type=ChannelType.DIRECT,
            name="DNS Магазин",
        )
        self.chan_olx = Channel(
            id="chan_olx_priv",
            seller_id=self.seller_priv.id,
            channel_type=ChannelType.CLASSIFIEDS,
            name="OLX",
        )
        self.channel_repo.save_or_update(self.chan_dns)
        self.channel_repo.save_or_update(self.chan_olx)

        # Offers:
        # DNS New: 1 450 000 ₸
        # Private Used: 1 100 000 ₸
        self.offer_repo.save_or_update(
            Offer(
                id="off_dns_new",
                product_id=self.product.id,
                seller_id=self.seller_dns.id,
                channel_id=self.chan_dns.id,
                external_sku="SKU-RTX-NEW",
                url="https://dns-shop.kz/5090",
                price=1450000.0,
                condition=Condition.NEW,
                city="Алматы",
                is_active=True,
            )
        )
        self.offer_repo.save_or_update(
            Offer(
                id="off_priv_used",
                product_id=self.product.id,
                seller_id=self.seller_priv.id,
                channel_id=self.chan_olx.id,
                external_sku="SKU-RTX-USED",
                url="https://olx.kz/5090",
                price=1100000.0,
                condition=Condition.USED,
                city="Алматы",
                is_active=True,
            )
        )

    def tearDown(self):
        self.conn.close()

    def test_repository_get_condition_breakdown(self):
        breakdown = self.pricing_repo.get_condition_breakdown(self.product.id)
        self.assertIsNotNone(breakdown)
        self.assertEqual(breakdown.best_new_price, 1450000.0)
        self.assertEqual(breakdown.new_offers_count, 1)
        self.assertEqual(breakdown.best_used_price, 1100000.0)
        self.assertEqual(breakdown.used_offers_count, 1)
        self.assertEqual(breakdown.total_active_offers, 2)

    def test_repository_matrix_query_isolates_new_from_used(self):
        """Querying for NEW goods must never return 1 100 000 ₸ as the best price."""
        matrix = self.pricing_repo.get_product_channel_matrix(
            self.product.id,
            condition=Condition.NEW,
        )
        self.assertIsNotNone(matrix)
        self.assertEqual(matrix.best_overall_price, 1450000.0)
        self.assertEqual(len(matrix.cross_seller_rankings), 1)
        self.assertEqual(matrix.cross_seller_rankings[0].seller_id, "sel_dns")

        # But condition breakdown is attached and reports used tier
        self.assertIsNotNone(matrix.condition_breakdown)
        self.assertEqual(matrix.best_used_price, 1100000.0)

    def test_repository_matrix_query_for_used(self):
        matrix = self.pricing_repo.get_product_channel_matrix(
            self.product.id,
            condition=Condition.USED,
        )
        self.assertIsNotNone(matrix)
        self.assertEqual(matrix.best_overall_price, 1100000.0)
        self.assertEqual(len(matrix.cross_seller_rankings), 1)
        self.assertEqual(matrix.cross_seller_rankings[0].seller_id, "sel_priv")


if __name__ == "__main__":
    unittest.main()
