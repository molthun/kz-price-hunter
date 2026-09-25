"""
Tests for Seller Identity, Deterministic Matcher and Seller Graph (KZ Price Hunter 2.0).
Verifies Kazakhstan business normalizers, golden dataset matches, and atomic seller merging.
"""

import test_support  # must be first
import sqlite3
import unittest

from domain.models import Channel, ChannelType, Offer, Seller
from domain.seller_identity import (
    IdentityType,
    MatchDecision,
    SellerIdentity,
    normalize_bin,
    normalize_business_name,
    normalize_domain,
    normalize_phone,
)
from domain.seller_matcher import SellerMatcher
from repositories import (
    ChannelRepository,
    OfferRepository,
    ProductRepository,
    SellerIdentityRepository,
    SellerRepository,
    init_schema_v2,
)


class TestSellerNormalizers(unittest.TestCase):
    def test_phone_normalization(self):
        self.assertEqual(normalize_phone("+7 (701) 123-45-67"), "+77011234567")
        self.assertEqual(normalize_phone("87011234567"), "+77011234567")
        self.assertEqual(normalize_phone("77011234567"), "+77011234567")
        self.assertEqual(normalize_phone("8 (727) 222-33-44"), "+77272223344")
        self.assertEqual(normalize_phone("+7 777 999 88 11"), "+77779998811")
        self.assertEqual(normalize_phone("8-705-555-44-33"), "+77055554433")

        # Invalid cases
        self.assertIsNone(normalize_phone(None))
        self.assertIsNone(normalize_phone(""))
        self.assertIsNone(normalize_phone("123"))
        self.assertIsNone(normalize_phone("не телефон"))

    def test_domain_normalization(self):
        self.assertEqual(normalize_domain("https://www.shop.kz/catalog/phone?sort=price"), "shop.kz")
        self.assertEqual(normalize_domain("http://4mobile.kz:8080/"), "4mobile.kz")
        self.assertEqual(normalize_domain("WWW.DNS-SHOP.KZ/"), "dns-shop.kz")
        self.assertEqual(normalize_domain("m.kaspi.kz"), "kaspi.kz")
        self.assertEqual(normalize_domain("technodom.kz"), "technodom.kz")

        # Invalid cases
        self.assertIsNone(normalize_domain(None))
        self.assertIsNone(normalize_domain(""))
        self.assertIsNone(normalize_domain("just text"))

    def test_bin_normalization(self):
        self.assertEqual(normalize_bin("123456789012"), "123456789012")
        self.assertEqual(normalize_bin("123 456 789 012"), "123456789012")
        self.assertEqual(normalize_bin("БИН 980140001234"), "980140001234")

        # Invalid cases
        self.assertIsNone(normalize_bin(None))
        self.assertIsNone(normalize_bin(""))
        self.assertIsNone(normalize_bin("12345"))  # too short
        self.assertIsNone(normalize_bin("1234567890123"))  # too long

    def test_business_name_normalization(self):
        self.assertEqual(normalize_business_name('ТОО "Белый Ветер KZ"'), "белый ветер kz")
        self.assertEqual(normalize_business_name('ИП «4Mobile»'), "4mobile")
        self.assertEqual(normalize_business_name('АО "Technodom Оперейтор"'), "technodom оперейтор")
        self.assertEqual(normalize_business_name('ООО "Мир Мебели"'), "мир мебели")


class TestSellerMatcherGoldenDataset(unittest.TestCase):
    """Verifies golden dataset rules from docs/SEARCH_PLATFORM_PLAN.md."""

    def test_case_1_same_seller_different_channels(self):
        # 4mobile direct == 4mobile marketplace (same phone / same domain) -> AUTO_MATCH
        seller = Seller(
            id="seller_4mobile",
            slug="4mobile",
            name="4mobile",
            domain="4mobile.kz",
            phone="+77001234567",
        )
        candidate = {
            "name": "4mobile Kaspi",
            "domain": "https://4mobile.kz",
            "phone": "8 (700) 123-45-67",
            "marketplace_seller_id": "4m_kaspi",
        }
        res = SellerMatcher.match_candidate_against_seller(candidate, seller)
        self.assertEqual(res.decision, MatchDecision.AUTO_MATCH)
        self.assertEqual(res.target_seller_id, "seller_4mobile")
        self.assertGreaterEqual(res.confidence, 0.98)

    def test_case_2_dns_different_cities(self):
        # DNS Алматы == DNS Астана (same BIN / same domain) -> AUTO_MATCH
        seller = Seller(
            id="seller_dns",
            slug="dns",
            name="DNS Казахстан",
            domain="dns-shop.kz",
            bin="150140001122",
        )
        candidate = {
            "name": "DNS Астана",
            "domain": "www.dns-shop.kz",
            "bin": "150140001122",
            "city": "Астана",
        }
        res = SellerMatcher.match_candidate_against_seller(candidate, seller)
        self.assertEqual(res.decision, MatchDecision.AUTO_MATCH)
        self.assertEqual(res.target_seller_id, "seller_dns")
        self.assertEqual(res.confidence, 1.0)

    def test_case_3_apple_store_vs_reseller(self):
        # Apple Store Kazakhstan == Apple reseller -> REJECT / distinct
        seller = Seller(
            id="seller_apple_official",
            slug="apple_official",
            name="Apple Store Kazakhstan",
            domain="apple.com",
            phone="+77271112233",
        )
        candidate = {
            "name": "Apple Reseller Almaty",
            "domain": "applereseller.kz",
            "phone": "87019998877",
        }
        res = SellerMatcher.match_candidate_against_seller(candidate, seller)
        self.assertEqual(res.decision, MatchDecision.REJECT)

    def test_case_4_mir_mebeli_different_contacts(self):
        # Два "Мир мебели" с разными телефонами/БИН -> REJECT (False Positive prevention!)
        seller = Seller(
            id="seller_mebel_1",
            slug="mir_mebeli_almaty",
            name="Мир Мебели",
            phone="+77273334455",
            bin="111140001111",
            domain="mebel-almaty.kz",
        )
        candidate = {
            "name": "Мир мебели",
            "phone": "+77172556677",
            "bin": "222240002222",
            "domain": "mebel-astana.kz",
        }
        res = SellerMatcher.match_candidate_against_seller(candidate, seller)
        self.assertEqual(res.decision, MatchDecision.REJECT)
        self.assertTrue(len(res.conflicting_identities) > 0)

    def test_case_5_distinctive_name_without_contacts_goes_to_review(self):
        # Редкое/уникальное название без контактов не склеивается автоматически, а отправляется в REVIEW
        seller = Seller(
            id="seller_vintage_shop",
            slug="vintage_audio_kz",
            name="Винтажный Аудио Клуб Алматы",
        )
        candidate = {
            "name": "Винтажный Аудио Клуб Алматы",
            # No phone, no domain, no BIN
        }
        res = SellerMatcher.match_candidate_against_seller(candidate, seller)
        self.assertEqual(res.decision, MatchDecision.REVIEW)
        self.assertEqual(res.target_seller_id, "seller_vintage_shop")


class TestSellerIdentityRepositoryAndMerge(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_schema_v2(self.conn)
        self.seller_repo = SellerRepository(self.conn)
        self.channel_repo = ChannelRepository(self.conn)
        self.product_repo = ProductRepository(self.conn)
        self.offer_repo = OfferRepository(self.conn)
        self.identity_repo = SellerIdentityRepository(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_identity_crud_and_lookup(self):
        seller = self.seller_repo.get_or_create(slug="sulpak", name="Sulpak")
        self.identity_repo.add_identity_values(
            seller_id=seller.id,
            identity_type=IdentityType.DOMAIN,
            identity_value="sulpak.kz",
            source="config",
        )
        self.identity_repo.add_identity_values(
            seller_id=seller.id,
            identity_type=IdentityType.PHONE,
            identity_value="+77273330000",
            source="website_footer",
        )

        identities = self.identity_repo.get_identities_by_seller(seller.id)
        self.assertEqual(len(identities), 2)

        # Lookup by phone
        matched_sellers = self.identity_repo.find_sellers_by_identity(IdentityType.PHONE, "+77273330000")
        self.assertEqual(matched_sellers, [seller.id])

        # Lookup by domain
        matched_by_dom = self.identity_repo.find_sellers_by_identity(IdentityType.DOMAIN, "sulpak.kz")
        self.assertEqual(matched_by_dom, [seller.id])

    def test_review_queue(self):
        qid = self.identity_repo.add_to_review_queue(
            candidate_seller_id="cand_123",
            matched_seller_id="seller_4mobile",
            reason="Ambiguous name match",
            confidence=0.65,
            details={"candidate_name": "4Mobile Plus"},
        )
        self.assertTrue(qid.startswith("srq_"))

        pending = self.identity_repo.get_pending_reviews()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["candidate_seller_id"], "cand_123")
        self.assertEqual(pending[0]["status"], "PENDING")

    def test_atomic_merge_sellers(self):
        # Create Seller A (Duplicate merchant created from marketplace scraper)
        seller_a = self.seller_repo.save_or_update(
            Seller(id="seller_4m_dupe", slug="4m_dupe", name="4Mobile Kaspi Store")
        )
        chan_a = self.channel_repo.get_or_create(
            seller_id=seller_a.id, channel_type=ChannelType.KASPI, name="Kaspi Channel"
        )

        # Create Seller B (Canonical merchant)
        seller_b = self.seller_repo.save_or_update(
            Seller(id="seller_4m_main", slug="4mobile", name="4Mobile Official", domain="4mobile.kz")
        )
        chan_b = self.channel_repo.get_or_create(
            seller_id=seller_b.id, channel_type=ChannelType.DIRECT, name="Web Direct"
        )

        # Create Product & Offers
        prod = self.product_repo.get_or_create_canonical("iphone:15", "Apple iPhone 15", "smartphones")
        off1 = Offer(
            id="off_from_a",
            product_id=prod.id,
            seller_id=seller_a.id,
            channel_id=chan_a.id,
            external_sku="SKU-A",
            url="https://kaspi.kz/p/1",
            price=399000.0,
            city="Алматы",
        )
        self.offer_repo.save_or_update(off1)

        off2 = Offer(
            id="off_from_b",
            product_id=prod.id,
            seller_id=seller_b.id,
            channel_id=chan_b.id,
            external_sku="SKU-B",
            url="https://4mobile.kz/p/1",
            price=389000.0,
            city="Алматы",
        )
        self.offer_repo.save_or_update(off2)

        # Add identity to Seller A
        self.identity_repo.add_identity_values(
            seller_id=seller_a.id,
            identity_type=IdentityType.PHONE,
            identity_value="+77001234567",
        )

        # Execute atomic merge
        success = self.identity_repo.merge_sellers(
            source_seller_id=seller_a.id, target_seller_id=seller_b.id
        )
        self.assertTrue(success)

        # Verify Seller A is deactivated
        updated_a = self.seller_repo.get_by_id(seller_a.id)
        self.assertEqual(updated_a.is_active, 0)

        # Verify Seller B has merge history in metadata
        updated_b = self.seller_repo.get_by_id(seller_b.id)
        self.assertIn(seller_a.id, updated_b.metadata.get("merged_from", []))

        # Verify Offer from A is now linked to Seller B
        updated_off1 = self.offer_repo.get_by_id("off_from_a")
        self.assertEqual(updated_off1.seller_id, seller_b.id)

        # Verify Channel from A is now linked to Seller B
        updated_chan_a = self.channel_repo.get_by_id(chan_a.id)
        self.assertEqual(updated_chan_a.seller_id, seller_b.id)

        # Verify identities of A were moved to B
        b_identities = self.identity_repo.get_identities_by_seller(seller_b.id)
        self.assertEqual(len(b_identities), 1)
        self.assertEqual(b_identities[0].identity_value, "+77001234567")

        # Verify SQLite integrity check
        cur = self.conn.cursor()
        cur.execute("PRAGMA integrity_check")
        self.assertEqual(cur.fetchone()[0], "ok")


class TestPlatformDomains(unittest.TestCase):
    """Домен площадки не является отпечатком продавца.

    На instagram.com и kaspi.kz торгуют тысячи разных людей. Совпадение такого домена не значит
    ничего, а матчер считал его сильным ключом и склеивал разных продавцов с уверенностью 0.98 —
    то самое автоматическое слияние, которое этап запрещает.
    """

    def test_platform_domain_never_merges_different_sellers(self):
        for domain in ("instagram.com", "kaspi.kz", "wixsite.com", "t.me", "shop.instagram.com"):
            seller = Seller(id="s_a", slug="a", name="Магазин Айсулу", domain=f"https://{domain}/a")
            result = SellerMatcher.match_candidate_against_seller(
                {"name": "Бутик Данияра", "domain": f"https://{domain}/b"}, seller)
            self.assertNotEqual(result.decision, MatchDecision.AUTO_MATCH,
                                f"{domain}: разные продавцы на площадке не должны склеиваться")

    def test_own_domain_still_matches(self):
        """Собственный домен магазина остаётся сильным ключом — защита не должна ломать полезное."""
        seller = Seller(id="s_m", slug="mechta", name="Мечта", domain="https://mechta.kz/")
        result = SellerMatcher.match_candidate_against_seller(
            {"name": "Мечта Астана", "domain": "https://mechta.kz/p/1"}, seller)
        self.assertEqual(result.decision, MatchDecision.AUTO_MATCH)


class TestMergeSellersEdgeCases(unittest.TestCase):
    """Пересечение товаров и неделимость слияния.

    Один и тот же товар у обоих продавцов — типичная причина слияния, а не исключение:
    уникальный индекс (channel_id, external_sku) не даёт перенести такой оффер «в лоб».
    """

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        init_schema_v2(self.conn)
        self.identity_repo = SellerIdentityRepository(self.conn)
        cur = self.conn.cursor()
        for sid in ("src", "tgt"):
            cur.execute("INSERT INTO sellers (id, slug, name, created_at, updated_at) VALUES (?,?,?,?,?)",
                        (sid, sid, sid, "t", "t"))
        cur.execute("""INSERT INTO canonical_products (id, canonical_key, title, category, created_at, updated_at)
                       VALUES ('p','k','Товар','Категория','t','t')""")
        for sid, ctype in (("src", "telegram"), ("src", "website"), ("tgt", "website")):
            cur.execute("""INSERT INTO channels (id, seller_id, channel_type, name, created_at)
                           VALUES (?,?,?,?,?)""", (f"chan_{sid}_{ctype}", sid, ctype, ctype, "t"))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _add_offer(self, oid, seller, channel, sku, price, observed):
        self.conn.execute(
            """INSERT INTO offers (id, product_id, seller_id, channel_id, external_sku, url,
                                   price, city, observed_at, created_at, updated_at)
               VALUES (?, 'p', ?, ?, ?, ?, ?, 'Алматы', ?, 't', 't')""",
            (oid, seller, channel, sku, f"https://example.kz/{oid}", price, observed))
        self.conn.execute(
            "INSERT INTO offer_price_history (offer_id, price, old_price, observed_at) VALUES (?,?,NULL,?)",
            (oid, price, observed))

    def test_colliding_sku_keeps_fresher_offer_and_its_history(self):
        self._add_offer("off_tgt", "tgt", "chan_tgt_website", "SKU-1", 1000, "2026-09-01")
        self._add_offer("off_src", "src", "chan_src_website", "SKU-1", 900, "2026-09-20")
        self.conn.commit()

        self.assertTrue(self.identity_repo.merge_sellers("src", "tgt"))

        rows = self.conn.execute(
            "SELECT id, price, observed_at FROM offers WHERE external_sku = 'SKU-1'").fetchall()
        self.assertEqual(len(rows), 1, "после слияния остаётся одно предложение на SKU в канале")
        self.assertEqual(rows[0][1], 900.0, "побеждает более свежее наблюдение")
        self.assertEqual(rows[0][2], "2026-09-20")

        history = self.conn.execute(
            "SELECT COUNT(*) FROM offer_price_history WHERE offer_id = ?", (rows[0][0],)).fetchone()[0]
        self.assertEqual(history, 2, "история проигравшего переходит победителю, а не теряется")
        orphans = self.conn.execute(
            "SELECT COUNT(*) FROM offer_price_history WHERE offer_id = 'off_src'").fetchone()[0]
        self.assertEqual(orphans, 0, "осиротевшая история не остаётся")

    def test_failed_merge_leaves_nothing_behind(self):
        """Наполовину слитый граф хуже неслитого: продавец активен, а каналы уже уехали."""
        from unittest.mock import patch
        self._add_offer("off_tgt", "tgt", "chan_tgt_website", "SKU-1", 1000, "2026-09-01")
        self._add_offer("off_src", "src", "chan_src_website", "SKU-1", 900, "2026-09-20")
        self.conn.commit()

        with patch.object(SellerIdentityRepository, "_move_offers_to_channel",
                          side_effect=RuntimeError("сбой посередине")):
            with self.assertRaises(RuntimeError):
                self.identity_repo.merge_sellers("src", "tgt")
        self.conn.commit()

        owner = self.conn.execute(
            "SELECT seller_id FROM channels WHERE id = 'chan_src_telegram'").fetchone()[0]
        self.assertEqual(owner, "src", "канал не должен оставаться у цели после сбоя")
        active = self.conn.execute("SELECT is_active FROM sellers WHERE id = 'src'").fetchone()[0]
        self.assertEqual(active, 1, "источник не должен оставаться деактивированным")


if __name__ == "__main__":
    unittest.main()
