"""
Tests for Seller Discovery and Source Profiler (KZ Price Hunter 2.0).
Verifies:
1. Audit resolutions B2 (generic names in RU and KZ), D1 (shared phone rejection),
   D2 (registrable domain extraction), D3 (BIN checksum validation).
2. 7-Tier Source Profiler classification.
3. CandidateRepository lifecycle pipeline (DISCOVERED -> PROFILED -> MATCHED / REVIEW / CATALOG_FOUND).
"""

import test_support  # must be first
import sqlite3
import unittest

from domain.discovery import (
    CandidateStatus,
    DiscoverySource,
    SellerCandidate,
)
from domain.models import Seller
from domain.seller_identity import (
    IdentityType,
    MatchDecision,
    get_registrable_domain,
    is_generic_business_name,
    is_weak_identity,
    normalize_bin,
    normalize_phone,
    validate_kz_bin_checksum,
)
from domain.seller_matcher import SellerMatcher
from domain.source_profiler import (
    CatalogFormat,
    ExtractionTier,
    SourceProfile,
    SourceProfiler,
)
from repositories import (
    CandidateRepository,
    ChannelRepository,
    SellerIdentityRepository,
    SellerRepository,
    init_schema_v2,
)


class TestAuditResolutions(unittest.TestCase):
    def test_b2_generic_business_names_detection(self):
        """Audit B2: Dynamic generic retail words in Russian and Kazakh."""
        # Russian generic combinations
        self.assertTrue(is_generic_business_name("Аптека"))
        self.assertTrue(is_generic_business_name("Аптека Плюс"))
        self.assertTrue(is_generic_business_name("Строймаркет"))
        self.assertTrue(is_generic_business_name("Продуктовый Центр"))
        self.assertTrue(is_generic_business_name("Мир Мебели"))
        self.assertTrue(is_generic_business_name("Планета Электроники Шоп"))
        self.assertTrue(is_generic_business_name("Зоомаркет Онлайн"))

        # Kazakh generic combinations
        self.assertTrue(is_generic_business_name("Дәріхана"))
        self.assertTrue(is_generic_business_name("Дүкен"))
        self.assertTrue(is_generic_business_name("Сауда Орталығы"))
        self.assertTrue(is_generic_business_name("Жиһаз Дүкені"))
        self.assertTrue(is_generic_business_name("Құрылыс Маркеті"))

        # Distinctive business names (must NOT be treated as generic)
        self.assertFalse(is_generic_business_name("4mobile"))
        self.assertFalse(is_generic_business_name("Мечта"))
        self.assertFalse(is_generic_business_name("Sulpak"))
        self.assertFalse(is_generic_business_name("Белый Ветер"))
        self.assertFalse(is_generic_business_name("Альтаир Мебель"))
        self.assertFalse(is_generic_business_name("Арзан Дәріхана"))  # "Арзан" is brand adjective

    def test_b2_generic_names_prevent_false_reviews_in_matcher(self):
        """
        Two unrelated merchants named 'Аптека Плюс' or 'Строймаркет' without shared
        strong keys must be REJECTED, not sent to manual REVIEW queue.
        """
        cand = {"name": "Аптека Плюс", "phone": "+77011112233"}
        seller = Seller(id="s1", slug="apteka_plus", name="Аптека Плюс", phone="+77029998877")

        res = SellerMatcher.match_candidate_against_seller(cand, seller)
        self.assertEqual(res.decision, MatchDecision.REJECT)

    def test_d1_shared_hotlines_rejected_as_weak_identity(self):
        """Audit D1: Marketplace and bank shared call center numbers must not trigger AUTO_MATCH."""
        kaspi_hotline = "+77272585989"
        forte_hotline = "+77272587575"
        freephone = "+78000805555"
        short_num = "9999"
        private_phone = "+77011234567"

        self.assertTrue(is_weak_identity(IdentityType.PHONE, kaspi_hotline))
        self.assertTrue(is_weak_identity(IdentityType.PHONE, forte_hotline))
        self.assertTrue(is_weak_identity(IdentityType.PHONE, freephone))
        self.assertTrue(is_weak_identity(IdentityType.PHONE, short_num))
        self.assertFalse(is_weak_identity(IdentityType.PHONE, private_phone))

    def test_d2_registrable_domain_extraction(self):
        """Audit D2: Subdomains reconciled to registrable domain."""
        self.assertEqual(get_registrable_domain("catalog.shop.kz"), "shop.kz")
        self.assertEqual(get_registrable_domain("astana.mechta.kz"), "mechta.kz")
        self.assertEqual(get_registrable_domain("https://m.shop.kz/category/phone"), "shop.kz")
        self.assertEqual(get_registrable_domain("sub.domain.com.kz"), "domain.com.kz")
        self.assertEqual(get_registrable_domain("shop.kz"), "shop.kz")

    def test_d2_subdomain_reconciliation_in_matcher(self):
        """Regional subdomain matching parent domain."""
        cand = {"name": "Мечта Астана", "domain": "astana.mechta.kz"}
        seller = Seller(id="s_m", slug="mechta", name="Мечта", domain="mechta.kz")

        res = SellerMatcher.match_candidate_against_seller(cand, seller)
        self.assertEqual(res.decision, MatchDecision.AUTO_MATCH)
        self.assertIn("registrable domain match", res.explanation)

    def test_d3_bin_checksum_validation(self):
        """Audit D3: Kazakhstan national BIN/IIN checksum algorithm."""
        # 12-digit numbers with valid checksum
        # Real format: 980140000000 -> let's construct a mathematically valid BIN
        # Weights: 1..11, sum mod 11
        # E.g. digits: 0 1 0 1 4 0 0 0 0 0 1
        # w1 = 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 1
        # sum = 0*1 + 1*2 + 0*3 + 1*4 + 4*5 + 0*6 + 0*7 + 0*8 + 0*9 + 0*10 + 1*1 = 2 + 4 + 20 + 1 = 27
        # 27 % 11 = 5. So check digit is 5 -> "010140000015"
        valid_bin = "010140000015"
        self.assertTrue(validate_kz_bin_checksum(valid_bin))

        # Corrupted check digit
        invalid_bin = "010140000016"
        self.assertFalse(validate_kz_bin_checksum(invalid_bin))

        # Wrong lengths
        self.assertFalse(validate_kz_bin_checksum("123"))
        self.assertFalse(validate_kz_bin_checksum(None))


class TestSourceProfiler(unittest.TestCase):
    def test_tier_1_feed_and_api(self):
        p_yml = SourceProfiler.profile_url("https://4mobile.kz/price_list.yml")
        self.assertEqual(p_yml.extraction_tier, ExtractionTier.FEED_API)
        self.assertEqual(p_yml.catalog_format, CatalogFormat.YML)
        self.assertTrue(p_yml.has_prices)

        p_api = SourceProfiler.profile_url("https://shop.kz/api/v2/catalog/items.json")
        self.assertEqual(p_api.extraction_tier, ExtractionTier.FEED_API)
        self.assertEqual(p_api.catalog_format, CatalogFormat.JSON_API)

    def test_tier_3_structured_website(self):
        p_struct = SourceProfiler.profile_url("https://mechta.kz", hints={"has_schema_org": True})
        self.assertEqual(p_struct.extraction_tier, ExtractionTier.STRUCTURED_HTML)
        self.assertEqual(p_struct.catalog_format, CatalogFormat.SCHEMA_ORG)

    def test_tier_4_ssr_html(self):
        p_html = SourceProfiler.profile_url("https://random-store.kz/catalog/phones")
        self.assertEqual(p_html.extraction_tier, ExtractionTier.SSR_HTML)
        self.assertEqual(p_html.catalog_format, CatalogFormat.HTML)

    def test_tier_5_telegram_messenger(self):
        p_tg = SourceProfiler.profile_url("https://t.me/apple_almaty_store")
        self.assertEqual(p_tg.extraction_tier, ExtractionTier.MESSENGER_TEXT)
        self.assertEqual(p_tg.catalog_format, CatalogFormat.TELEGRAM)
        self.assertTrue(p_tg.capabilities["order"])

    def test_tier_6_and_7_instagram(self):
        p_ig_text = SourceProfiler.profile_url("https://instagram.com/kz_store")
        self.assertEqual(p_ig_text.extraction_tier, ExtractionTier.SOCIAL_TEXT)
        self.assertEqual(p_ig_text.catalog_format, CatalogFormat.INSTAGRAM_TEXT)

        p_ig_vision = SourceProfiler.profile_url("https://instagram.com/kz_store", hints={"has_stories": True})
        self.assertEqual(p_ig_vision.extraction_tier, ExtractionTier.SOCIAL_VISION)
        self.assertEqual(p_ig_vision.catalog_format, CatalogFormat.INSTAGRAM_VISION)


class TestCandidateRepositoryWorkflow(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_schema_v2(self.conn)

        self.seller_repo = SellerRepository(self.conn)
        self.identity_repo = SellerIdentityRepository(self.conn)
        self.candidate_repo = CandidateRepository(self.conn)

        # Seed known seller: 4mobile
        self.seller = Seller(
            id="sel_4mobile",
            slug="4mobile",
            name="4mobile",
            domain="4mobile.kz",
            phone="+77001112233",
            bin="010140000015",
            is_active=True,
        )
        self.seller_repo.save_or_update(self.seller)
        self.identity_repo.add_identity_values(self.seller.id, IdentityType.DOMAIN, "4mobile.kz")
        self.identity_repo.add_identity_values(self.seller.id, IdentityType.PHONE, "+77001112233")
        self.identity_repo.add_identity_values(self.seller.id, IdentityType.BIN, "010140000015")

    def tearDown(self):

        self.conn.close()

    def test_candidate_auto_matched_by_phone(self):
        """A candidate with the same verified phone auto-matches existing seller."""
        cand = SellerCandidate(
            id="cand_1",
            name="4mobile на Kaspi",
            source_type=DiscoverySource.MARKETPLACE_SELLER,
            source_url="https://kaspi.kz/merchant/4mobile",
            phone="+77001112233",
        )

        res = self.candidate_repo.process_discovered_candidate(cand)
        self.assertEqual(res.status, CandidateStatus.IDENTITY_MATCHED)
        self.assertEqual(res.matched_seller_id, "sel_4mobile")
        self.assertIn("Auto-matched", res.notes)

        # Verify source profile was saved
        profiles = self.candidate_repo.get_source_profiles_for_candidate(cand.id)
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0].seller_id, "sel_4mobile")

    def test_candidate_review_required_on_distinctive_name_similarity(self):
        """A candidate with same distinctive name but unknown phone goes to review queue."""
        cand = SellerCandidate(
            id="cand_2",
            name="4mobile",
            source_type=DiscoverySource.WEBSITE,
            source_url="https://4mobile-new.kz",
        )

        res = self.candidate_repo.process_discovered_candidate(cand)
        self.assertEqual(res.status, CandidateStatus.REVIEW_REQUIRED)

        # Review queue record must be created
        items = self.identity_repo.get_review_queue(status="PENDING")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["candidate_seller_id"], "cand_2")
        self.assertEqual(items[0]["matched_seller_id"], "sel_4mobile")

    def test_candidate_catalog_found_for_new_independent_merchant(self):
        """A new merchant with structured YML catalog is classified as CATALOG_FOUND."""
        cand = SellerCandidate(
            id="cand_3",
            name="Техносклад Астана",
            source_type=DiscoverySource.WEBSITE,
            source_url="https://technosklad.kz/export/yandex.yml",
            domain="technosklad.kz",
            phone="+77172009988",
        )

        # Пока источник не открывали, это предположение по адресу, а не найденный каталог:
        # CATALOG_FOUND требует подтверждённого наблюдения (C-E05-01).
        guessed = self.candidate_repo.process_discovered_candidate(cand)
        self.assertEqual(guessed.status, CandidateStatus.PROFILED)

        res = self.candidate_repo.process_discovered_candidate(
            cand, hints={"status_code": 200, "content_type": "application/xml"})
        self.assertEqual(res.status, CandidateStatus.CATALOG_FOUND)
        self.assertIsNone(res.matched_seller_id)

        # Source profile has Tier 1
        profiles = self.candidate_repo.get_source_profiles_for_candidate(cand.id)
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0].extraction_tier, ExtractionTier.FEED_API)
        self.assertEqual(profiles[0].catalog_format, CatalogFormat.YML)


if __name__ == "__main__":
    unittest.main()
