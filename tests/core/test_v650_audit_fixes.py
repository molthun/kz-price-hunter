"""Регрессии единого аудита 6.5.0 (Codex, V01–V07) на рабочих границах.

Фикстура — штатная база TestDatabasePlatformV2Complete (4mobile: сайт 499 000, Kaspi 539 000,
б/у 420 000). Унаследованные тесты фикстуры здесь не запускаются (load_tests ниже).
"""
from tests import test_support  # noqa: F401
import asyncio
import datetime
import json
import sqlite3
import unittest
from unittest.mock import patch

import domain.freshness_v2 as freshness_v2
from domain.models import (
    Availability,
    CanonicalProduct,
    Channel,
    ChannelType,
    Condition,
    Offer,
    Seller,
)
from domain.purchasability import offer_purchasable, sql_purchasable_conditions
from domain.search_assistant import SearchAssistant
from domain.search_core import SearchCore
from domain.seller_onboarding import SellerOnboardingSubmission
from domain.social_content import ContentObservation
from domain.vision_extraction import VisionExtractionEngine
from repositories.candidate_repo import CandidateRepository
from repositories.channel_repo import ChannelRepository
from repositories.offer_repo import OfferRepository
from repositories.pricing_repo import PricingRepository
from repositories.product_repo import ProductRepository
from repositories.schema_v2 import init_schema_v2
from repositories.search_repo import SearchRepository
from repositories.seller_identity_repo import SellerIdentityRepository
from repositories.seller_repo import SellerRepository
from tests.core.test_search_platform_v2_complete import TestDatabasePlatformV2Complete
from tests.monitoring.test_telemetry import P0102Case
import telemetry as tm

NOW = datetime.datetime(2026, 10, 29, 12, 0, tzinfo=datetime.timezone.utc)


def _iso(hours_ago: float) -> str:
    return (NOW - datetime.timedelta(hours=hours_ago)).isoformat()


class V650Case(TestDatabasePlatformV2Complete):
    def _offer(self, offer_id, *, channel_id="", price=100000.0, condition=Condition.NEW,
               city="Алматы", product_id="prod_iphone16pro_256", observed_at=None, **extra):
        offer = Offer(
            id=offer_id, product_id=product_id, seller_id="seller_4mobile",
            channel_id=channel_id or self._channel("website").id, external_sku=offer_id,
            price=price, condition=condition, availability=Availability.IN_STOCK, city=city,
            url=f"https://4mobile.kz/{offer_id}", observed_at=observed_at or NOW.isoformat(),
            is_active=True, **extra)
        OfferRepository(self.conn).save_or_update(offer)
        return offer

    def _channel(self, channel_type, store="4mobile_web"):
        return ChannelRepository(self.conn).get_or_create(
            seller_id="seller_4mobile", channel_type=channel_type, name=f"4mobile ({channel_type})",
            external_store_id=store, base_url="https://4mobile.kz")


class OnboardingIdentityTest(V650Case):
    """V01: одобрение несопоставленной заявки не трогает чужого продавца."""

    def _submit(self, name, url, **kw):
        repo = CandidateRepository(self.conn)
        return repo, repo.submit_self_registration(
            SellerOnboardingSubmission(name=name, city="Алматы", website_url=url, **kw))

    def test_domain_prefix_collision_creates_separate_seller(self):
        sellers = SellerRepository(self.conn)
        sellers.save_or_update(Seller(id="seller_example", slug="example", name="Original retailer",
                                      domain="example.kz"))
        repo, res = self._submit("Unrelated Outlet", "https://example.com")
        self.assertIsNone(res.matched_seller_id)

        seller = repo.approve_onboarding_candidate(res.candidate.id)

        original = sellers.get_by_id("seller_example")
        self.assertEqual((original.name, original.domain), ("Original retailer", "example.kz"))
        self.assertNotEqual(seller.id, "seller_example")
        self.assertEqual((seller.name, seller.domain), ("Unrelated Outlet", "example.com"))
        self.assertNotEqual(seller.slug, "example")
        channels = ChannelRepository(self.conn).list_by_seller(seller.id)
        self.assertTrue(channels)
        self.assertFalse(ChannelRepository(self.conn).list_by_seller("seller_example"))

    def test_same_name_different_domain_does_not_merge(self):
        sellers = SellerRepository(self.conn)
        sellers.save_or_update(Seller(id="seller_gadget", slug="gadget-kz", name="Gadget",
                                      domain="gadget.kz"))
        repo, res = self._submit("Gadget", "https://gadget-shop.kz")
        seller = repo.approve_onboarding_candidate(res.candidate.id)
        self.assertNotEqual(seller.id, "seller_gadget")
        self.assertEqual(sellers.get_by_id("seller_gadget").domain, "gadget.kz")

    def test_repeat_approval_is_idempotent(self):
        repo, res = self._submit("Idem Store", "https://idem-store.kz", instagram="@idem_store")
        first = repo.approve_onboarding_candidate(res.candidate.id)
        channels = len(ChannelRepository(self.conn).list_by_seller(first.id))
        second = repo.approve_onboarding_candidate(res.candidate.id)
        self.assertEqual(first.id, second.id)
        self.assertEqual(len(ChannelRepository(self.conn).list_by_seller(first.id)), channels)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM sellers WHERE name = 'Idem Store'").fetchone()[0], 1)

    def test_review_suggestion_is_not_a_confirmed_match(self):
        """Подсказка REVIEW в очередь — реальный продавец; без подтверждения модератора — новый продавец."""
        repo, res = self._submit("4mobile.kz", "https://4mobile.kz")
        queue = self.conn.execute(
            "SELECT matched_seller_id FROM seller_review_queue WHERE candidate_seller_id = ?",
            (res.candidate.id,)).fetchone()
        if res.matched_seller_id is None:
            self.assertIn(queue["matched_seller_id"], (None, "seller_4mobile"))
            seller = repo.approve_onboarding_candidate(res.candidate.id)
            self.assertNotEqual(seller.id, "seller_4mobile")
        self.assertEqual(SellerRepository(self.conn).get_by_id("seller_4mobile").name, "4mobile.kz")

    def test_moderator_confirmed_merge_only_fills_gaps(self):
        sellers = SellerRepository(self.conn)
        sellers.save_or_update(Seller(id="seller_known", slug="known", name="Known Shop"))
        repo, res = self._submit("Known Shop Renamed", "https://known-shop.kz", bin="111122223333")
        seller = repo.approve_onboarding_candidate(res.candidate.id, seller_id="seller_known")
        self.assertEqual(seller.id, "seller_known")
        known = sellers.get_by_id("seller_known")
        self.assertEqual(known.name, "Known Shop")
        self.assertEqual(known.bin, "111122223333")

    def test_unknown_confirmed_seller_is_rejected(self):
        repo, res = self._submit("Nobody", "https://nobody-shop.kz")
        with self.assertRaises(ValueError):
            repo.approve_onboarding_candidate(res.candidate.id, seller_id="seller_missing")


class PriceHistorySnapshotTest(V650Case):
    """V02: прошлые точки сохраняют измерения момента наблюдения."""

    def _sync(self, condition, price, when, city="Алматы"):
        from domain.adapter import sync_legacy_product_to_v2
        sync_legacy_product_to_v2(self.conn, {
            "id": "hist-1", "shop": "4mobile.kz", "title": "Смартфон Apple iPhone 15 128GB Black",
            "category": "Смартфоны", "url": "https://4mobile.kz/p/hist-1", "price": price,
            "condition": condition, "city": city}, now=when)
        self.conn.commit()

    def _series(self, **filters):
        product_id = self.conn.execute(
            "SELECT product_id FROM offers WHERE id = 'off_hist-1'").fetchone()[0]
        return PricingRepository(self.conn).get_product_price_history(product_id, **filters).to_dict()["series"]

    def test_condition_change_keeps_old_points_new(self):
        self._sync("NEW", 499000, "2026-01-01T00:00:00+00:00")
        self._sync("USED", 400000, "2026-02-01T00:00:00+00:00")
        by_cond = {s["condition"]: [p["price"] for p in s["points"]] for s in self._series()}
        self.assertEqual(by_cond, {"NEW": [499000.0], "USED": [400000.0]})
        new_only = self._series(condition="NEW")
        self.assertEqual([[p["price"] for p in s["points"]] for s in new_only], [[499000.0]])

    def test_condition_change_with_same_price_starts_new_line(self):
        self._sync("NEW", 450000, "2026-01-01T00:00:00+00:00")
        self._sync("USED", 450000, "2026-02-01T00:00:00+00:00")
        self.assertEqual(sorted(s["condition"] for s in self._series()), ["NEW", "USED"])

    def test_seller_merge_moves_history_to_winner(self):
        self._sync("NEW", 499000, "2026-01-01T00:00:00+00:00")
        source_seller = self.conn.execute(
            "SELECT seller_id FROM offers WHERE id = 'off_hist-1'").fetchone()[0]
        SellerRepository(self.conn).save_or_update(Seller(id="seller_winner", slug="winner", name="Winner"))
        SellerIdentityRepository(self.conn).merge_sellers(source_seller, "seller_winner")
        self.conn.commit()
        self.assertEqual({s["seller_id"] for s in self._series()}, {"seller_winner"})

    def test_simultaneous_points_of_two_offers_are_kept(self):
        web = self._channel("website")
        at = "2026-03-01T00:00:00+00:00"
        for oid, city, price in (("o-ala", "Алматы", 100000.0), ("o-ast", "Астана", 105000.0)):
            self._offer(oid, channel_id=web.id, city=city, price=price, observed_at=at,
                        product_id="prod_lenovo_thinkbook_16")
            OfferRepository(self.conn).add_price_observation(oid, price, observed_at=at)
        self.conn.commit()
        report = PricingRepository(self.conn).get_product_price_history("prod_lenovo_thinkbook_16").to_dict()
        prices = sorted(p["price"] for s in report["series"] for p in s["points"]
                        if p["observed_at"] == at)
        self.assertEqual(prices, [100000.0, 105000.0])


class FreshnessPolicyAgreementTest(unittest.TestCase):
    """V03: одно правило свежести в Python и SQL для всех типов каналов и состояний."""

    def test_sql_and_python_agree_on_every_channel_condition_and_boundary(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        init_schema_v2(conn)
        SellerRepository(conn).save_or_update(Seller(id="s", slug="s", name="S"))
        ProductRepository(conn).save_or_update(CanonicalProduct(id="p", canonical_key="k", title="T", category="x"))
        conditions, params = sql_purchasable_conditions(NOW, channel="c")
        n = 0
        for ct in sorted(ChannelType.ALL | {ChannelType.UNKNOWN}):
            ChannelRepository(conn).save_or_update(Channel(id=f"c-{ct}", seller_id="s", channel_type=ct, name=ct))
            for cond in sorted(Condition.ALL):
                limit = freshness_v2.purchasable_max_age_hours(ct, cond)
                for age in (limit - 0.5, limit + 0.5):
                    n += 1
                    oid = f"o{n}"
                    OfferRepository(conn).save_or_update(Offer(
                        id=oid, product_id="p", seller_id="s", channel_id=f"c-{ct}", external_sku=oid,
                        url="https://example.invalid", price=10, condition=cond, observed_at=_iso(age)))
                    row = dict(conn.execute(
                        "SELECT o.*, c.channel_type FROM offers o JOIN channels c ON c.id = o.channel_id "
                        "WHERE o.id = ?", (oid,)).fetchone())
                    in_sql = conn.execute(
                        f"SELECT 1 FROM offers o JOIN channels c ON c.id = o.channel_id "
                        f"WHERE o.id = ? AND {' AND '.join(conditions)}", [oid, *params]).fetchone() is not None
                    py = offer_purchasable(row, NOW)
                    with self.subTest(channel=ct, condition=cond, age=age):
                        self.assertEqual(in_sql, py)
                        self.assertEqual(py, age < limit)
                        self.assertEqual(freshness_v2.offer_freshness(row, NOW).is_purchasable_freshness, py)
        conn.close()

    def test_expired_food_is_not_purchasable_and_website_unchanged(self):
        food = {"price": 1000, "availability": "in_stock", "is_active": 1, "observed_at": _iso(30),
                "channel_type": ChannelType.FOOD_AGGREGATOR}
        self.assertTrue(freshness_v2.offer_freshness(food, NOW).is_expired)
        self.assertFalse(offer_purchasable(food, NOW))
        site = dict(food, channel_type=ChannelType.WEBSITE)
        self.assertTrue(offer_purchasable(site, NOW))
        self.assertFalse(offer_purchasable(dict(site, observed_at=_iso(73)), NOW))
        self.assertTrue(offer_purchasable(dict(site, observed_at="not-a-date"), NOW))

    def test_provenance_matches_purchasability(self):
        from domain.provenance import build_offer_provenance
        row = {"id": "x", "price": 1000, "availability": "in_stock", "observed_at": _iso(30),
               "channel_type": ChannelType.FOOD_AGGREGATOR,
               "raw_payload_json": json.dumps({"source_type": "website"})}
        prov = build_offer_provenance(row, now=NOW)
        self.assertEqual(prov.freshness, "EXPIRED")
        self.assertFalse(offer_purchasable(row, NOW))


class SearchFreshnessTest(V650Case):
    """V03 на рабочем пути поиска: устаревшая еда не становится лучшей ценой."""

    def test_stale_food_offer_is_not_purchasable_in_search(self):
        food = self._channel(ChannelType.FOOD_AGGREGATOR, store="4mobile_food")
        seen = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=30)).isoformat()
        self._offer("o-food", channel_id=food.id, price=1000.0, observed_at=seen)
        self.conn.commit()
        result = SearchCore(SearchRepository(self.conn)).search(query="iPhone 16 Pro", sort_by="price_asc")
        offers = {o.id: o for item in result.items for o in item.offers}
        self.assertIn("o-food", offers)
        self.assertFalse(offers["o-food"].is_purchasable)
        self.assertEqual(offers["o-food"].freshness, "stale")
        self.assertNotEqual(result.items[0].best_offer.id, "o-food")


class MonitoringTotalsTest(V650Case):
    """V04: сводка мониторинга покрывает все предложения и не выдаёт ошибку чтения за ноль."""

    def test_totals_cover_offers_beyond_first_page(self):
        from domain.monitoring_v2 import build_monitoring_overview_v2
        base = dict(self.conn.execute("SELECT * FROM offers WHERE id = 'off_4m_direct_iphone16pro'").fetchone())
        rows = [dict(base, id=f"bulk-{i}", external_sku=f"bulk-{i}") for i in range(5001)]
        cols = list(base)
        self.conn.executemany(f"INSERT INTO offers ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                              [tuple(r[c] for c in cols) for r in rows])
        # Проблемное предложение — последним, за прежней границей выборки 5000
        food = self._channel(ChannelType.FOOD_AGGREGATOR, store="4mobile_food")
        self._offer("z-last-expired", channel_id=food.id, observed_at=_iso(24 * 30))
        self.conn.commit()
        actual = self.conn.execute("SELECT COUNT(*) FROM offers WHERE is_active = 1").fetchone()[0]
        level = build_monitoring_overview_v2(self.conn).offer_level
        self.assertEqual(level["total_active_offers"], actual)
        self.assertEqual(level["fresh_offers"] + level["stale_offers"] + level["expired_offers"]
                         + level["unknown_freshness_offers"], actual)
        self.assertGreaterEqual(level["expired_offers"], 1)
        self.assertEqual(level["by_source_freshness_type"]["food"]["expired"], 1)

    def test_read_error_is_reported_not_zero(self):
        from domain.monitoring_v2 import build_monitoring_overview_v2
        self.conn.execute("DROP TABLE seller_review_queue")
        overview = build_monitoring_overview_v2(self.conn)
        self.assertIsNone(overview.seller_level["pending_review_queue"])
        self.assertTrue(any("seller_review_queue" in e for e in overview.read_errors))


class AssistantTermsTest(V650Case):
    """V05: рассрочка — только по данным предложения; сравнение — тот же товар и состояние."""

    def _ask(self, query, **kw):
        return SearchAssistant(SearchCore(SearchRepository(self.conn))).ask(query, city="Алматы", **kw)

    def test_channel_type_alone_is_not_installment(self):
        r = self._ask("iPhone в рассрочку")
        self.assertIsNone(r.best_installment_offer)
        self.assertIsNone(r.cross_channel_comparison)
        self.assertIn("неизвестны", r.explanation)
        self.assertNotIn("рассрочкой у", r.explanation)

    def test_used_cheaper_offer_is_not_compared_with_new_installment(self):
        self.conn.execute("UPDATE offers SET installment_available = 1 WHERE id = 'off_4m_kaspi_iphone16pro'")
        self.conn.execute("UPDATE offers SET price = 600000 WHERE id = 'off_4m_direct_iphone16pro'")
        self.conn.commit()
        r = self._ask("iPhone в рассрочку")
        self.assertEqual(r.best_overall_offer["condition"], "USED")
        self.assertEqual(r.best_installment_offer["id"], "off_4m_kaspi_iphone16pro")
        # Дешевле рассрочки только б/у — сравнивать не с чем, разницы «способов покупки» нет
        self.assertIsNone(r.cross_channel_comparison)
        self.assertNotIn("119 000", r.explanation)
        self.assertIn("б/у", r.explanation)

    def test_direct_seller_with_confirmed_installment_is_supported(self):
        self.conn.execute("UPDATE offers SET installment_months = 6 WHERE id = 'off_4m_direct_iphone16pro'")
        self.conn.commit()
        r = self._ask("iPhone в рассрочку", condition="new")
        self.assertEqual(r.best_installment_offer["id"], "off_4m_direct_iphone16pro")
        self.assertIn("6 мес.", r.explanation)

    def test_same_condition_comparison_reports_difference(self):
        self.conn.execute("UPDATE offers SET installment_available = 1 WHERE id = 'off_4m_kaspi_iphone16pro'")
        self.conn.commit()
        r = self._ask("iPhone в рассрочку")
        cmp_ = r.cross_channel_comparison
        self.assertEqual((cmp_["condition"], cmp_["price_difference"]), ("NEW", 40000.0))
        self.assertEqual(cmp_["direct_offer"]["id"], "off_4m_direct_iphone16pro")


class VisionBoundaryTest(unittest.TestCase):
    """V06: этап частичный — изображение без готового текста цену не даёт и не угадывает."""

    def _obs(self, **meta):
        return ContentObservation(id="obs-1", content_type="STORY", text="", raw_metadata=meta,
                                  media_urls=["https://example.invalid/story.jpg"])

    def test_image_without_text_yields_no_candidates(self):
        from repositories.content_repo import ContentObservationRepository
        obs = self._obs()
        self.assertEqual(VisionExtractionEngine.extract_from_observation(obs), [])
        self.assertEqual(ContentObservationRepository.extract_candidates(obs), [])

    def test_image_text_is_read_like_ocr_text(self):
        obs = self._obs(image_text="iPhone 15 128GB — 389 990 ₸")
        candidates = VisionExtractionEngine.extract_from_observation(obs)
        self.assertTrue(candidates)
        self.assertEqual(candidates[0].price, 389990.0)

    def test_no_phantom_ai_tasks(self):
        import ai_router
        self.assertNotIn("vision_extraction", ai_router.TASKS)
        self.assertNotIn("search_assistant", ai_router.TASKS)


class RequestCorrelationTest(P0102Case):
    """V07: рабочий middleware сам выдаёт request_id; параллельные запросы не смешиваются."""

    def test_parallel_requests_get_distinct_ids_in_events_and_pool(self):
        from aiohttp import web
        from aiohttp.test_utils import TestClient, TestServer
        import web.server as server

        async def handler(request):
            await asyncio.sleep(0.05)  # запросы перекрываются во времени
            ids = await server.run_db(tm.get_correlation_ids)
            tm.telemetry.record_event("probe", tm.SEVERITY_INFO, tm.COMPONENT_SYSTEM, "probe",
                                      data={"tag": request.query["tag"]})
            return web.json_response(ids)

        async def scenario():
            app = web.Application(middlewares=[server.telemetry_middleware])
            app.router.add_get("/api/v2/seller/{id_or_slug}", handler)
            async with TestClient(TestServer(app)) as client:
                responses = await asyncio.gather(
                    client.get("/api/v2/seller/alpha", params={"tag": "a"}),
                    client.get("/api/v2/seller/beta", params={"tag": "b"}))
                out = []
                for r in responses:
                    out.append((r.headers.get("X-Request-ID"), await r.json()))
                return out, tm.get_correlation_ids()

        (a, b), after = asyncio.run(scenario())
        self.assertTrue(a[0] and b[0])
        self.assertNotEqual(a[0], b[0])
        self.assertEqual((a[1]["request_id"], a[1]["seller_id"]), (a[0], "alpha"))
        self.assertEqual((b[1]["request_id"], b[1]["seller_id"]), (b[0], "beta"))
        self.assertIsNone(after["request_id"])
        events = {e["data"]["tag"]: e for e in self.events("probe")}
        self.assertEqual(events["a"]["request_id"], a[0])
        self.assertEqual(events["b"]["request_id"], b[0])
        self.assertEqual(events["a"]["data"]["seller_id"], "alpha")

    def test_social_ingest_events_carry_source_id(self):
        import social_ingest

        seen = {}

        def fake(source, connector=None, conn=None, now_iso=None):
            seen.update(tm.get_correlation_ids())
            return "done"

        source = type("S", (), {"id": "src-42", "seller_id": "seller_x"})()
        with patch.object(social_ingest, "_ingest_source", fake):
            self.assertEqual(social_ingest.ingest_source(source), "done")
        self.assertEqual((seen["source_id"], seen["seller_id"]), ("src-42", "seller_x"))
        self.assertIsNone(tm.get_correlation_ids()["source_id"])


def load_tests(loader, standard_tests, pattern):
    """Только тесты этого модуля: унаследованные от фикстур наборы запускаются в своих модулях."""
    suite = unittest.TestSuite()
    for cls in (OnboardingIdentityTest, PriceHistorySnapshotTest, FreshnessPolicyAgreementTest,
                SearchFreshnessTest, MonitoringTotalsTest, AssistantTermsTest, VisionBoundaryTest,
                RequestCorrelationTest):
        for name in sorted(vars(cls)):
            if name.startswith("test_"):
                suite.addTest(cls(name))
    return suite
