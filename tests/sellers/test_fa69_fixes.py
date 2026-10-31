"""Регрессии замечаний аудита 6.9.0 (FA69-01…14): утверждения желаемого поведения вместо probe-дефектов."""
from tests import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestServer
from PIL import Image

import domain.social_media as social_media
from domain.discovery import CandidateStatus, DiscoverySource, SellerCandidate, candidate_actions
from domain.models import Channel, Condition, Offer, Seller
from domain.search_core import SearchCore
from domain.social_connector import FetchedPost
from domain.social_content import ContentObservation, ExtractedOfferCandidate
from domain.social_media import MediaError, check_url, ip_allowed, prepare_image
from domain.social_pipeline import SocialOfferPipeline
from domain.vision_extraction import VisionExtractionEngine, parse_vision_response
from product_key import build_key
from repositories import init_schema_v2
from repositories.candidate_repo import CandidateRepository
from repositories.channel_repo import ChannelRepository
from repositories.offer_repo import OfferRepository
from repositories.product_repo import ProductRepository
from repositories.search_repo import SearchRepository
from repositories.seller_repo import SellerRepository
from repositories.social_source_repo import SocialSourceRepository
from social_ingest import ingest_source, ingest_source_async

IG_URL = "https://www.instagram.com/p/AUDITPOST/"
TITLE = "Apple iPhone 15 128GB"


def db():
    conn = sqlite3.connect(":memory:")
    init_schema_v2(conn)
    return conn


def observation(post="1", platform="instagram", url=IG_URL, **kw):
    return ContentObservation(post_id=post, raw_metadata={
        "platform": platform, "handle": "audit_shop", "post_url": url}, **kw)


def candidate(price=100000, city="Алматы", condition=Condition.NEW, **kw):
    return ExtractedOfferCandidate(raw_title=kw.pop("title", TITLE), price=price, city=city,
                                   condition=condition, **kw)


def connector_for(posts):
    return type("FakeConnector", (), {"fetch": lambda self, handle: posts})()


class OfferIdentityTests(unittest.TestCase):
    def setUp(self):
        self.conn = db()
        self.pipeline = SocialOfferPipeline(self.conn)

    def rows(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM offers ORDER BY price")]

    def test_fa69_03_cities_and_conditions_coexist_and_update_independently(self):
        new = self.pipeline.process_candidates(
            observation("1", observed_at="2026-10-30T10:00:00+00:00"), [candidate()])[0]
        used = self.pipeline.process_candidates(
            observation("2", observed_at="2026-10-30T10:00:00+00:00"),
            [candidate(70000, "Астана", Condition.USED)])[0]
        self.assertNotEqual(new.id, used.id)
        self.pipeline.process_candidates(
            observation("3", observed_at="2026-10-31T10:00:00+00:00"), [candidate(95000)])
        self.assertEqual([(r["city"], r["condition"], r["price"]) for r in self.rows()],
                         [("Астана", "USED", 70000), ("Алматы", "NEW", 95000)])

    def test_fa69_03_same_post_is_idempotent(self):
        obs = observation("1", published_at="2026-10-30T10:00:00+00:00",
                          observed_at="2026-10-30T11:00:00+00:00")
        first = self.pipeline.process_candidates(obs, [candidate()])[0]
        again = self.pipeline.process_candidates(obs, [candidate()])[0]
        self.assertEqual(first.id, again.id)
        self.assertEqual(len(self.rows()), 1)

    def test_fa69_04_result_does_not_depend_on_order(self):
        newest = lambda: observation("new", published_at="2026-10-31T10:00:00+00:00")
        oldest = lambda: observation("old", published_at="2026-10-01T10:00:00+00:00")
        for order in ((newest, oldest), (oldest, newest)):
            conn = db()
            pipeline = SocialOfferPipeline(conn)
            for make in order:
                pipeline.process_candidates(make(), [candidate(100000 if make is newest else 150000)])
            row = dict(conn.execute("SELECT price, published_at, observed_at FROM offers").fetchone())
            self.assertEqual(row["price"], 100000)
            self.assertIn("10-31", row["published_at"])
            self.assertIn("10-31", row["observed_at"])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM offer_price_history").fetchone()[0], 2)

    def test_fa69_04_real_ingest_recollection_does_not_refresh_price(self):
        source = SocialSourceRepository(self.conn).add_source("telegram", "audit_replay")
        posts = [FetchedPost(post_id="new", text=f"{TITLE} 100000 ₸", published_at="2026-10-01T10:00:00+00:00"),
                 FetchedPost(post_id="old", text=f"{TITLE} 150000 ₸", published_at="2026-09-01T10:00:00+00:00")]
        for _ in range(2):
            result = ingest_source(source, connector=connector_for(posts), conn=self.conn)
            self.assertEqual(result.status, "OK")
        row = self.rows()[0]
        self.assertEqual(row["price"], 100000)
        self.assertIn("2026-10-01", row["published_at"])
        self.assertIn("2026-10-01", row["observed_at"])

    def test_fa69_04_older_observation_without_publish_time_cannot_move_back(self):
        self.pipeline.process_candidates(observation("n", observed_at="2026-10-31T10:00:00+00:00"), [candidate()])
        self.pipeline.process_candidates(observation("o", observed_at="2026-10-01T10:00:00+00:00"),
                                         [candidate(150000)])
        row = self.rows()[0]
        self.assertEqual(row["price"], 100000)
        self.assertIn("10-31", row["observed_at"])

    def test_fa69_05_same_handle_on_two_platforms_gives_two_sellers(self):
        first = self.pipeline.process_candidates(observation("1"), [candidate()])[0]
        second = self.pipeline.process_candidates(observation("2", platform="telegram", url=""), [candidate()])[0]
        self.assertNotEqual(first.seller_id, second.seller_id)

    def test_fa69_05_confirmed_seller_links_two_platforms(self):
        SellerRepository(self.conn).save_or_update(Seller(id="biz", slug="biz", name="Biz"))
        first = self.pipeline.process_candidates(observation("1", seller_id="biz"), [candidate()])[0]
        second = self.pipeline.process_candidates(
            observation("2", platform="telegram", url="", seller_id="biz"), [candidate()])[0]
        self.assertEqual((first.seller_id, second.seller_id), ("biz", "biz"))
        self.assertNotEqual(first.channel_id, second.channel_id)

    def _existing_k2(self):
        key = build_key({"brand": "Apple", "model": "iPhone 15", "variants": ["128gb"]}, title=TITLE)
        return ProductRepository(self.conn).get_or_create_canonical(key, TITLE, "smartphones", brand="Apple")

    def test_fa69_06_social_offer_joins_existing_k2_card(self):
        existing = self._existing_k2()
        with patch("ai_router.run", new_callable=AsyncMock) as model:
            saved = self.pipeline.process_candidates(observation(), [candidate()])[0]
            other_title = self.pipeline.process_candidates(
                observation("2"), [candidate(title="iPhone 15 128 ГБ черный", city="Астана")])[0]
        self.assertEqual(saved.product_id, existing.id)
        self.assertEqual(other_title.product_id, existing.id)
        self.assertEqual(model.await_count, 0)

    def test_fa69_06_operator_pin_wins_over_matching_card(self):
        self._existing_k2()
        title_hash = __import__("hashlib").sha256(TITLE.encode()).hexdigest()
        self.conn.execute("CREATE TABLE title_key_pins (title_hash TEXT, title TEXT, canonical_key TEXT)")
        self.conn.execute("INSERT INTO title_key_pins VALUES (?, ?, ?)", (title_hash, TITLE, "k2:human:split"))
        saved = self.pipeline.process_candidates(observation(), [candidate()])[0]
        self.assertEqual(ProductRepository(self.conn).get_by_id(saved.product_id).canonical_key, "k2:human:split")

    def test_fa69_06_dual_sim_is_not_linked_by_parsed_title(self):
        existing = self._existing_k2()
        saved = self.pipeline.process_candidates(observation(), [candidate(title=f"{TITLE} Dual SIM")])[0]
        self.assertNotEqual(saved.product_id, existing.id)

    def test_fa69_07_units_do_not_merge_and_reach_search(self):
        vision = VisionExtractionEngine.extract_from_structured([
            {"title": "Говядина", "price": 1000, "confidence": .95, "is_unit_price": True, "unit": "g", "quantity": 100},
            {"title": "Говядина", "price": 6000, "confidence": .95, "is_unit_price": True, "unit": "kg", "quantity": 1},
        ])
        small = self.pipeline.process_candidates(observation("1"), [vision[0].to_offer_candidate()])[0]
        large = self.pipeline.process_candidates(observation("2"), [vision[1].to_offer_candidate()])[0]
        self.assertNotEqual(small.product_id, large.product_id)
        self.assertEqual(large.raw_payload["price_basis"]["unit"], "kg")
        self.assertNotEqual(ProductRepository(self.conn).get_by_id(large.product_id).category, "electronics")
        items = SearchCore(SearchRepository(self.conn)).search("говядина").items
        self.assertEqual(len(items), 2)
        offer = next(i.offers[0] for i in items if i.offers[0].unit == "kg")
        self.assertTrue(offer.is_unit_price)
        self.assertEqual(offer.quantity, 1)

    def test_fa69_07_equal_amount_is_one_product_other_packs_differ(self):
        def pack(**kw):
            return candidate(title="Молоко", **kw).price_basis()
        self.assertEqual(pack(unit="g", quantity=1000)["pack"], pack(unit="kg", quantity=1)["pack"])
        self.assertNotEqual(pack(unit="l", quantity=1)["pack"], pack(unit="l", quantity=1.5)["pack"])
        self.assertEqual(pack(unit="l", quantity=1, size_or_volume="1.5 л")["pack"], "pack:1500ml")
        self.assertEqual(pack(unit="pcs", quantity=6)["pack"], "pack:6pcs")
        self.assertIsNone(pack(unit="pcs", quantity=1))
        self.assertIsNone(pack(size_or_volume="256GB"))
        self.assertNotEqual(pack(unit="kg", quantity=1, is_unit_price=True)["pack"], pack(unit="kg", quantity=1)["pack"])

    def test_fa69_11_permalinks(self):
        first = self.pipeline.process_candidates(observation("1"), [candidate()])[0]
        self.assertEqual(first.url, IG_URL)
        second_url = "https://www.instagram.com/p/SECOND/"
        second = self.pipeline.process_candidates(
            observation("2", url=second_url, observed_at="2030-01-01T00:00:00+00:00"), [candidate()])[0]
        self.assertEqual(second.url, second_url)
        tg = self.pipeline.process_candidates(observation("77", platform="telegram", url=""), [candidate()])[0]
        self.assertEqual(tg.url, "https://t.me/audit_shop/77")
        no_link = self.pipeline.process_candidates(observation("9", url=""), [candidate(city="Астана")])[0]
        self.assertEqual(no_link.url, "")

    def test_fa69_12_history_is_written_with_dimensions_and_no_duplicates(self):
        obs1 = observation("1", published_at="2026-10-30T10:00:00+00:00")
        saved = self.pipeline.process_candidates(obs1, [candidate()])[0]
        self.pipeline.process_candidates(obs1, [candidate()])
        self.pipeline.process_candidates(observation("2", published_at="2026-10-31T10:00:00+00:00"),
                                         [candidate(90000)])
        history = OfferRepository(self.conn).get_price_history(saved.id)
        self.assertEqual([h.price for h in history], [100000, 90000])
        row = dict(self.conn.execute(
            "SELECT product_id, seller_id, channel_id, condition, city FROM offer_price_history LIMIT 1").fetchone())
        self.assertEqual((row["product_id"], row["condition"], row["city"]), (saved.product_id, "NEW", "Алматы"))


class VisionResponseTests(unittest.IsolatedAsyncioTestCase):
    CONFIG = {"enabled": True, "has_ai": True, "ai_provider": "auto", "has_local": True,
              "local_api_base": "http://unused.invalid/v1", "local_model": "audit",
              "gemini_api_key": "mock", "gemini_model": "audit"}

    def test_confidence_rules(self):
        for bad in (None, "высокая", "0.9", float("nan"), float("inf"), 1.5, -0.1, True):
            item = {"title": "Товар один", "price": 1000, "confidence": bad}
            good = {"title": "Товар два", "price": 2000, "confidence": .9}
            parsed = VisionExtractionEngine.extract_from_structured([item, good])
            self.assertFalse(parsed[0].publishable, bad)
            self.assertEqual(parsed[0].rejection_reason, "invalid_confidence")
            self.assertTrue(parsed[1].publishable)
        missing = VisionExtractionEngine.extract_from_structured([{"title": "Товар", "price": 1000}])[0]
        self.assertFalse(missing.publishable)

    def test_schema_validation(self):
        self.assertEqual(parse_vision_response({"items": []}), {"items": []})
        for bad in ({"wrong_schema": True}, [], {"items": {}}, {"items": ["x"]},
                    {"items": [{"title": 5}]}, {"items": [{"title": "x", "price": [1]}]}):
            with self.assertRaises(ValueError):
                parse_vision_response(bad)

    async def _run_router(self, local_answer):
        gemini_answer = {"items": [{"title": "Товар", "price": 1000, "confidence": .9}]}
        with patch("ai_service.call_local_api", new_callable=AsyncMock, return_value=local_answer) as local, \
                patch("ai_service.call_gemini_api", new_callable=AsyncMock, return_value=gemini_answer) as gemini, \
                patch("ai_router.budget_allows", return_value=True), \
                patch("ai_router.record_usage") as usage, patch("local_ai.pause_reason", return_value=None):
            data = await VisionExtractionEngine.extract_from_image(b"audit-image-content", config=self.CONFIG)
        return data, local, gemini, usage

    async def test_fa69_13_wrong_schema_falls_back_and_counts_bad_response(self):
        data, local, gemini, usage = await self._run_router({"wrong_schema": True})
        self.assertEqual((local.await_count, gemini.await_count, len(data)), (1, 1, 1))
        self.assertIn("bad_response", [c.kwargs.get("outcome") for c in usage.call_args_list])

    async def test_fa69_13_valid_empty_list_does_not_fall_back(self):
        data, _local, gemini, _usage = await self._run_router({"items": []})
        self.assertEqual((data, gemini.await_count), ([], 0))


def jpeg(size=(3000, 2000)):
    out = io.BytesIO()
    Image.new("RGB", size, "white").save(out, format="JPEG")
    return out.getvalue()


class MediaFetchTests(unittest.IsolatedAsyncioTestCase):
    """Настоящий HTTP на loopback: политика адресов ослаблена только здесь и отдельно проверена ниже."""

    async def asyncSetUp(self):
        self.big = jpeg()

        async def photo(_request):
            return web.Response(body=self.big, content_type="image/jpeg")

        async def hop(_request):
            raise web.HTTPFound("/photo.jpg")

        async def internal(_request):
            raise web.HTTPFound("http://169.254.169.254/latest/meta-data")

        async def page(_request):
            return web.Response(text="<html>", content_type="text/html")

        async def fake_image(_request):
            return web.Response(body=b"not an image at all", content_type="image/jpeg")

        async def huge(_request):
            return web.Response(body=b"x" * 2048, content_type="image/jpeg")

        app = web.Application()
        app.add_routes([web.get("/photo.jpg", photo), web.get("/hop", hop), web.get("/internal", internal),
                        web.get("/page", page), web.get("/fake.jpg", fake_image), web.get("/huge.jpg", huge)])
        self.server = TestServer(app)
        await self.server.start_server()
        self.base = str(self.server.make_url("/")).rstrip("/")
        real_allowed = ip_allowed
        patches = [patch.object(social_media, "ALLOWED_SCHEMES", {"http", "https"}),
                   patch.object(social_media, "ALLOWED_PORTS", {80, 443, self.server.port}),
                   patch.object(social_media, "ip_allowed", lambda h: h == "127.0.0.1" or real_allowed(h))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    async def asyncTearDown(self):
        await self.server.close()

    async def test_fa69_02_full_path_from_connector_to_search(self):
        conn = db()
        source = SocialSourceRepository(conn).add_source("telegram", "audit_photo")
        post = FetchedPost(post_id="1", text="Прайс на картинке", media_urls=[f"{self.base}/hop"],
                           published_at="2030-01-01T00:00:00+00:00")
        answer = {"result": {"items": [{"title": TITLE, "price": 399990, "confidence": .95}]}}
        with patch("ai_router.run", new_callable=AsyncMock, return_value=answer) as model:
            result = await ingest_source_async(source, connector=connector_for([post]), conn=conn, with_vision=True)
        self.assertEqual((result.status, result.offers, model.await_count), ("OK", 1, 1))
        self.assertEqual((result.media_attempted, result.media_fetched, result.media_failed), (1, 1, 0))
        sent = Image.open(io.BytesIO(model.await_args.kwargs["image_bytes"]))
        self.assertLessEqual(max(sent.size), social_media.MAX_IMAGE_SIDE)
        self.assertEqual(model.await_args.kwargs["mime_type"], "image/jpeg")
        self.assertIs(model.await_args.kwargs["validate"], parse_vision_response)
        found = SearchCore(SearchRepository(conn)).search("iphone 15", now=None).items
        self.assertEqual(found[0].offers[0].price, 399990)

    async def test_media_failure_is_visible_apart_from_text_success(self):
        conn = db()
        source = SocialSourceRepository(conn).add_source("telegram", "audit_photo")
        post = FetchedPost(post_id="1", text=f"{TITLE} 100000 ₸", media_urls=[f"{self.base}/page"])
        with patch("ai_router.run", new_callable=AsyncMock) as model:
            result = await ingest_source_async(source, connector=connector_for([post]), conn=conn, with_vision=True)
        self.assertEqual((result.status, result.offers, model.await_count), ("OK", 1, 0))
        self.assertEqual(result.media_failed, 1)
        self.assertIn("unsupported_format", result.media_error)
        self.assertIn("unsupported_format", SocialSourceRepository(conn).get_by_id(source.id).last_error)
        self.assertNotIn(self.base, result.media_error)

    async def test_model_failure_is_visible(self):
        conn = db()
        source = SocialSourceRepository(conn).add_source("telegram", "audit_photo")
        post = FetchedPost(post_id="1", text="Прайс", media_urls=[f"{self.base}/photo.jpg"])
        with patch("ai_router.run", new_callable=AsyncMock, side_effect=RuntimeError("no provider")):
            result = await ingest_source_async(source, connector=connector_for([post]), conn=conn, with_vision=True)
        self.assertEqual((result.status, result.model_failed), ("OK", 1))

    async def test_rejections(self):
        for path, code in (("/internal", "private_address"), ("/page", "unsupported_format"),
                           ("/fake.jpg", "bad_image"), ("/missing", "http_404")):
            with self.assertRaises(MediaError, msg=path) as ctx:
                await social_media.fetch_image(self.base + path)
            self.assertEqual(ctx.exception.code, code)
        with patch.object(social_media, "MAX_IMAGE_BYTES", 1024):
            with self.assertRaises(MediaError) as ctx:
                await social_media.fetch_image(f"{self.base}/huge.jpg")
            self.assertEqual(ctx.exception.code, "too_large")


class MediaPolicyTests(unittest.IsolatedAsyncioTestCase):
    def test_address_policy(self):
        for host in ("127.0.0.1", "10.0.0.1", "192.168.1.1", "169.254.169.254", "::1", "::ffff:127.0.0.1", "fc00::1"):
            self.assertFalse(ip_allowed(host), host)
        self.assertTrue(ip_allowed("8.8.8.8"))

    def test_url_policy(self):
        for url in ("http://example.com/a.jpg", "file:///etc/passwd", "https://u:p@example.com/a.jpg",
                    "https://example.com:8443/a.jpg", "https://127.0.0.1/a.jpg", "https://[::1]/a.jpg", "нет"):
            with self.assertRaises(MediaError, msg=url):
                check_url(url)
        check_url("https://cdn.example.com/a.jpg")

    async def test_resolver_refuses_name_pointing_inside(self):
        resolver = social_media.PublicOnlyResolver()
        answers = [{"host": "8.8.8.8"}, {"host": "10.0.0.5"}]
        with patch.object(resolver._resolver, "resolve", new_callable=AsyncMock, return_value=answers):
            with self.assertRaises(OSError):
                await resolver.resolve("rebind.example.com", 443)
        await resolver.close()

    def test_pixel_limit_and_format(self):
        with patch.object(social_media, "MAX_IMAGE_PIXELS", 100):
            with self.assertRaises(MediaError) as ctx:
                prepare_image(jpeg((50, 50)))
            self.assertEqual(ctx.exception.code, "too_many_pixels")
        out = io.BytesIO()
        Image.new("RGB", (5, 5)).save(out, format="GIF")
        with self.assertRaises(MediaError) as ctx:
            prepare_image(out.getvalue())
        self.assertEqual(ctx.exception.code, "unsupported_format")


class SellerSearchTests(unittest.TestCase):
    def setUp(self):
        self.conn = db()

    def add(self, sid, price, metadata=None, name=None, channel="website", title=None):
        SellerRepository(self.conn).save_or_update(Seller(id=sid, slug=sid, name=name or sid, metadata=metadata or {}))
        ChannelRepository(self.conn).save_or_update(Channel(id=sid, seller_id=sid, channel_type=channel, name=sid))
        product = ProductRepository(self.conn).get_or_create_canonical(
            "audit:" + (title or sid), f"Apple iPhone 15 128GB {title or sid}", "electronics", brand="Apple")
        OfferRepository(self.conn).save_or_update(Offer(
            id=sid, product_id=product.id, seller_id=sid, channel_id=sid, external_sku=sid,
            url="https://example.invalid", price=price))

    def search(self, **kw):
        return SearchCore(SearchRepository(self.conn)).search("iphone 15", **kw)

    def test_fa69_08_metadata_flag_on_any_channel(self):
        self.add("ivan", 100, {"is_private": True}, name="Иван", channel="telegram")
        self.add("oleg", 110, {"is_private_seller": True}, name="Олег", channel="website")
        self.add("shop", 120, {"is_private": False}, name="Магазин", channel="telegram")
        flags = {i.offers[0].seller_id: i.offers[0].is_private_seller for i in self.search().items}
        self.assertEqual(flags, {"ivan": True, "oleg": True, "shop": False})

    def test_fa69_08_broken_metadata_does_not_break_search(self):
        self.add("broken", 100)
        self.add("private", 110, {"is_private": True})
        self.conn.execute("UPDATE sellers SET metadata_json = '{not json' WHERE id = 'broken'")
        found = self.search()
        self.assertEqual(found.outcome != "ERROR", True)
        flags = {i.offers[0].seller_id: i.offers[0].is_private_seller for i in found.items}
        self.assertEqual(flags, {"broken": False, "private": True})

    def test_fa69_08_name_rule_is_kept(self):
        self.add("a", 100, name="Частное лицо")
        self.assertTrue(self.search().items[0].offers[0].is_private_seller)

    def test_fa69_09_filter_applies_before_limit_both_ways(self):
        self.add("business", 100000)
        self.add("private", 120000, {"is_private": True}, channel="classifieds")
        for sort_by, seller_type, expected in (("price_asc", "private", "private"), ("price_asc", "business", "business"),
                                               ("price_desc", "business", "business"), ("price_desc", "private", "private"),
                                               ("relevance", "private", "private")):
            found = self.search(seller_type=seller_type, limit=1, sort_by=sort_by)
            self.assertEqual([i.offers[0].seller_id for i in found.items], [expected], (sort_by, seller_type))

    def test_fa69_09_mixed_product_keeps_only_requested_offers(self):
        self.add("business", 100000, title="same")
        self.add("private", 120000, {"is_private": True}, channel="classifieds", title="same")
        found = self.search(seller_type="private", limit=1, sort_by="price_asc")
        self.assertEqual([o.seller_id for o in found.items[0].offers], ["private"])
        self.assertEqual(found.items[0].min_price, 120000)


class DiscoveryLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.conn = db()
        self.repo = CandidateRepository(self.conn)

    def make(self, status, **kw):
        cand = SellerCandidate(id=f"cand_{status}", name=f"Audit {status}", source_type=DiscoverySource.WEBSITE,
                               source_url="https://example.invalid", status=status, **kw)
        self.repo.save_candidate(cand)
        return cand

    def test_fa69_10_reprofiling_never_cancels_decisions(self):
        for status in (CandidateStatus.REJECTED, CandidateStatus.APPROVED, CandidateStatus.INDEXED,
                       CandidateStatus.REVIEW_REQUIRED, CandidateStatus.IDENTITY_MATCHED):
            cand = self.make(status, matched_seller_id="seller_x")
            after = self.repo.profile_candidate(cand.id)
            self.assertEqual(after.status, status)
            self.assertEqual(self.repo.get_candidate_by_id(cand.id).status, status)
            self.assertEqual(self.repo.get_candidate_by_id(cand.id).matched_seller_id, "seller_x")
            self.assertTrue(self.repo.get_source_profiles_for_candidate(cand.id))

    def test_fa69_10_reprofiling_is_repeatable_and_never_goes_back(self):
        for status in (CandidateStatus.DISCOVERED, CandidateStatus.PROFILED, CandidateStatus.CATALOG_FOUND):
            cand = self.make(status)
            first = self.repo.profile_candidate(cand.id, hints={"format": "yml"}).status
            second = self.repo.profile_candidate(cand.id).status
            self.assertEqual(first, second)
            if status == CandidateStatus.CATALOG_FOUND:
                self.assertEqual(second, status)

    def test_fa69_10_ui_actions_match_backend_for_all_statuses(self):
        for status in sorted(CandidateStatus.ALL):
            actions = candidate_actions(status)
            cand = self.make(status)
            self.conn.execute("SAVEPOINT probe")
            if actions["approve"]:
                self.assertIsNotNone(self.repo.approve_onboarding_candidate(cand.id), status)
            elif status != CandidateStatus.INDEXED:
                with self.assertRaises(ValueError, msg=status):
                    self.repo.approve_onboarding_candidate(cand.id)
            self.conn.execute("ROLLBACK TO probe")
            self.conn.execute("RELEASE probe")
            self.conn.execute("SAVEPOINT probe")
            if actions["reject"]:
                self.repo.approve_onboarding_candidate(cand.id, decision="REJECTED")
                self.assertEqual(self.repo.get_candidate_by_id(cand.id).status, CandidateStatus.REJECTED)
            self.conn.execute("ROLLBACK TO probe")
            self.conn.execute("RELEASE probe")
            self.assertEqual(actions["profile"], status != CandidateStatus.REJECTED)

    def test_fa69_10_profiled_path_to_approval_goes_through_review(self):
        cand = self.make(CandidateStatus.PROFILED)
        self.assertFalse(candidate_actions(cand.status)["approve"])
        self.assertTrue(candidate_actions(cand.status)["review"])
        self.repo.update_candidate_status(cand.id, CandidateStatus.REVIEW_REQUIRED)
        self.assertIsNotNone(self.repo.approve_onboarding_candidate(cand.id))
        self.assertEqual(self.repo.get_candidate_by_id(cand.id).status, CandidateStatus.INDEXED)

    def test_fa69_14_count_matches_list_filters(self):
        for suffix, city in (("one", "Алматы"), ("two", "Астана")):
            self.repo.save_candidate(SellerCandidate(
                id="c_" + suffix, name="Audit " + suffix, source_type=DiscoverySource.WEBSITE,
                domain=suffix + ".invalid", phone="+7700" + suffix, city=city))
        for filters in ({"domain": "one.invalid"}, {"phone": "two"}, {"domain": "one", "city": "Астана"},
                        {"domain": "two", "city": "Астана", "query": "Audit"}, {"domain": "none"}, {}):
            items = self.repo.find_candidates(**filters)
            self.assertEqual(self.repo.count_candidates(**filters), len(items), filters)
        self.assertEqual(self.repo.find_candidates(domain="one", offset=1), [])


class MonitoringContractTests(unittest.TestCase):
    def test_fa69_01_overview_returns_every_ui_section_on_empty_db(self):
        import re

        import database
        import monitoring
        with tempfile.TemporaryDirectory() as tmp, patch.object(database, "DB_PATH", Path(tmp) / "m.db"):
            database.init_db()
            data = monitoring.overview({}, [], {}, 3600)
            self.assertEqual(data["sections"]["discovery"]["status"], monitoring.HEALTHY)
            self.assertEqual(data["sections"]["discovery"]["reason"], "Кандидатов пока нет")
        with open("web/templates/monitoring.html", encoding="utf-8") as f:
            html = f.read()
        block = re.search(r"const SECTIONS = \[(.*?)\];", html, re.S).group(1)
        for section in re.findall(r"\['(\w+)',", block):
            if section != "overview":
                self.assertIn(section, data["sections"])

    def test_fa69_01_discovery_read_error_is_not_green(self):
        import monitoring
        with patch("repositories.candidate_repo.CandidateRepository.get_discovery_stats", side_effect=RuntimeError):
            self.assertEqual(monitoring.discovery_section()["status"], monitoring.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
