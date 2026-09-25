"""Замечания аудита Codex по S2-E05: C-E05-01…04. Офлайн, на SQLite в памяти.

Каждая проверка закрепляет поведение, которого не было и которое Codex воспроизвёл:
предположение по адресу выдавалось за найденный каталог, локальный номер площадки склеивал
разных продавцов, признак наблюдения терялся при записи, а решение модерации не двигало кандидата.
"""
import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import sqlite3
import unittest

from domain.discovery import CandidateStatus, DiscoverySource, SellerCandidate, transition_allowed
from domain.seller_identity import IdentityType, normalize_marketplace_id
from domain.source_profiler import SourceProfile, SourceProfiler
from repositories import SellerRepository, init_schema_v2
from repositories.candidate_repo import CandidateRepository


class CatalogFoundRequiresObservationTest(unittest.TestCase):
    """C-E05-01: CATALOG_FOUND означает «каталог найден», а не «предположен по адресу»."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        init_schema_v2(self.conn)
        self.repo = CandidateRepository(self.conn)

    def tearDown(self):
        self.conn.close()

    def _run(self, candidate_id, hints):
        candidate = SellerCandidate(id=candidate_id, name="Магазин Айсулу",
                                    source_type=DiscoverySource.WEBSITE,
                                    source_url="https://example.kz/")
        return self.repo.process_discovered_candidate(candidate, hints=hints)

    def test_plain_homepage_without_observation_stays_profiled(self):
        result = self._run("c_guess", None)
        self.assertEqual(result.status, CandidateStatus.PROFILED,
                         "визитка или парковочная страница не является найденным каталогом")

    def test_observed_source_reaches_catalog_found(self):
        result = self._run("c_seen", {"status_code": 200, "content_type": "text/html"})
        self.assertEqual(result.status, CandidateStatus.CATALOG_FOUND)


class MarketplaceIdNamespaceTest(unittest.TestCase):
    """C-E05-02: локальный номер площадки без её имени ключом не является."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        init_schema_v2(self.conn)
        self.repo = CandidateRepository(self.conn)
        seller_repo = SellerRepository(self.conn)
        self.seller = seller_repo.get_or_create(slug="shop_a", name="Магазин А")
        self.repo.identity_repo.add_identity_values(
            self.seller.id, IdentityType.MARKETPLACE_SELLER_ID, normalize_marketplace_id("kaspi", "42"))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _decision(self, value):
        return self.repo.identity_repo.match_candidate(
            {"name": "Магазин Б", "marketplace_seller_id": value}, self.seller).decision

    def test_same_id_on_another_marketplace_is_not_a_match(self):
        self.assertNotEqual(self._decision(normalize_marketplace_id("forte", "42")), "AUTO_MATCH",
                            "продавец 42 на Kaspi и продавец 42 на Forte — разные люди")

    def test_same_id_on_the_same_marketplace_matches(self):
        self.assertEqual(self._decision(normalize_marketplace_id("kaspi", "42")), "AUTO_MATCH")

    def test_bare_id_without_marketplace_is_not_a_key(self):
        self.assertNotEqual(self._decision("42"), "AUTO_MATCH")

    def test_normalizer_keeps_one_canonical_form(self):
        self.assertEqual(normalize_marketplace_id("Kaspi", " 42 "), "kaspi:42")
        self.assertEqual(normalize_marketplace_id("kaspi", "kaspi:42"), "kaspi:42")
        self.assertIsNone(normalize_marketplace_id("", "42"))
        self.assertIsNone(normalize_marketplace_id("kaspi", ""))


class ObservedSurvivesStorageTest(unittest.TestCase):
    """C-E05-03: признак наблюдения обязан переживать запись, иначе он бесполезен."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        init_schema_v2(self.conn)
        self.repo = CandidateRepository(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_round_trip_for_both_values(self):
        for flag in (True, False):
            self.repo.save_source_profile(SourceProfile(
                id=f"prof_{int(flag)}", candidate_id="c1",
                source_url="https://example.kz/", observed=flag))
        self.conn.commit()
        stored = {p.id: p.observed for p in self.repo.get_source_profiles_for_candidate("c1")}
        self.assertEqual(stored, {"prof_1": True, "prof_0": False})

    def test_profiler_marks_url_guess_as_unobserved(self):
        self.assertFalse(SourceProfiler.profile_url("https://example.kz/").observed)
        self.assertTrue(SourceProfiler.profile_url(
            "https://example.kz/", hints={"status_code": 200}).observed)


class ReviewResolutionAdvancesCandidateTest(unittest.TestCase):
    """C-E05-04: решение модерации обязано двигать кандидата, а переходы — проверяться."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        init_schema_v2(self.conn)
        self.repo = CandidateRepository(self.conn)
        self.seller = SellerRepository(self.conn).get_or_create(slug="mechta", name="Мечта")
        candidate = SellerCandidate(id="c1", name="Мечта Астана",
                                    source_type=DiscoverySource.WEBSITE)
        self.repo.save_candidate(candidate)
        self.repo.update_candidate_status("c1", CandidateStatus.REVIEW_REQUIRED)
        self.queue_id = self.repo.identity_repo.add_to_review_queue(
            "c1", self.seller.id, "Похожее имя", 0.65)
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_approval_links_candidate_to_seller(self):
        self.assertTrue(self.repo.resolve_candidate_review(self.queue_id, "APPROVED", actor="owner"))
        candidate = self.repo.get_candidate_by_id("c1")
        self.assertEqual(candidate.status, CandidateStatus.IDENTITY_MATCHED)
        self.assertEqual(candidate.matched_seller_id, self.seller.id)
        self.assertEqual(self.repo.identity_repo.get_pending_reviews(), [],
                         "после решения запись не должна оставаться в очереди")

    def test_rejection_closes_the_candidate(self):
        self.assertTrue(self.repo.resolve_candidate_review(self.queue_id, "REJECTED"))
        self.assertEqual(self.repo.get_candidate_by_id("c1").status, CandidateStatus.REJECTED)

    def test_second_resolution_changes_nothing(self):
        self.repo.resolve_candidate_review(self.queue_id, "APPROVED")
        self.assertFalse(self.repo.resolve_candidate_review(self.queue_id, "APPROVED"))

    def test_unknown_status_and_forbidden_jump_are_refused(self):
        with self.assertRaises(ValueError):
            self.repo.update_candidate_status("c1", "INDEXED_WITHOUT_APPROVAL")
        with self.assertRaises(ValueError):
            self.repo.update_candidate_status("c1", CandidateStatus.INDEXED)

    def test_transition_table_matches_the_plan(self):
        self.assertTrue(transition_allowed(CandidateStatus.REVIEW_REQUIRED, CandidateStatus.APPROVED))
        self.assertTrue(transition_allowed(CandidateStatus.APPROVED, CandidateStatus.INDEXED))
        self.assertFalse(transition_allowed(CandidateStatus.PROFILED, CandidateStatus.INDEXED))
        self.assertFalse(transition_allowed(CandidateStatus.REJECTED, CandidateStatus.APPROVED))


if __name__ == "__main__":
    unittest.main()
