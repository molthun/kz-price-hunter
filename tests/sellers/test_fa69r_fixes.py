"""Регрессии повторной приёмки 6.9.1 (FA69R-01…07): сценарии Codex как утверждения желаемого поведения."""
from tests import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import base64
import datetime
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import database as db
import product_key as pk
from domain.models import Channel, Seller
from domain.search_core import SearchCore
from domain.social_connector import FetchedPost
from domain.social_content import ContentObservation, ContentType, ExtractedOfferCandidate, SocialContentExtractor, parse_unit_price
from domain.social_media import FetchedImage
from domain.social_pipeline import SocialOfferPipeline
from domain.vision_extraction import VisionExtractionEngine, parse_vision_response
from repositories import init_schema_v2
from repositories.channel_repo import ChannelRepository
from repositories.content_repo import ContentObservationRepository
from repositories.offer_repo import OfferRepository
from repositories.product_repo import ProductRepository
from repositories.search_repo import SearchRepository
from repositories.seller_repo import SellerRepository
from repositories.social_source_repo import SocialSourceRepository
from scripts.scrub_social_content import scrub
from social_ingest import ingest_source, ingest_source_async

TITLE = "Apple iPhone 15 128GB"
A, B = "Apple EarPods Lightning", "Apple EarPods USB-C"


def conn():
    c = sqlite3.connect(":memory:")
    init_schema_v2(c)
    return c


def obs(post="1", platform="instagram", handle="audit_shop", **kw):
    return ContentObservation(post_id=post, raw_metadata={"platform": platform, "handle": handle,
                                                          "post_url": "https://www.instagram.com/p/AUDIT/"}, **kw)


def cand(price=100000, title=TITLE, city="Алматы"):
    return ExtractedOfferCandidate(raw_title=title, price=price, city=city)


def connector(posts):
    return type("FixtureConnector", (), {"fetch": lambda self, handle: posts})()


class OperatorDecisionTests(unittest.TestCase):
    """Настоящий resolve_disputed_pair_human и отдельные соединения SQLite."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="kzph-fa69r-")
        self.addCleanup(tmp.cleanup)
        for p in (patch.object(db, "DB_PATH", Path(tmp.name) / "prices.db"), patch("config.DATA_DIR", Path(tmp.name))):
            p.start()
            self.addCleanup(p.stop)
        db.init_db()
        fields = {"brand": "Apple", "model": "EarPods", "sku": None, "variants": [], "unbranded": False}
        self.common = pk.build_key(fields, A)
        db.save_cached_canonical_keys_batch({A: self.common, B: self.common})
        for i, title in enumerate((A, B)):
            db.save_or_update_product({"id": f"p{i}", "title": title, "canonical_key": self.common, "shop": f"shop{i}",
                                       "city": "Алматы", "price": 1000, "url": f"https://example.invalid/{i}"})
        db.save_title_fields_batch({A: fields, B: fields}, "k2", "local", "audit")
        db.enqueue_disputed_pair(A, B, "social")
        self.c = db.get_connection()
        self.addCleanup(self.c.close)
        SellerRepository(self.c).save_or_update(Seller(id="known", slug="known", name="Known"))
        ChannelRepository(self.c).save_or_update(Channel(id="chan", seller_id="known", channel_type="telegram", name="K"))
        self.c.commit()
        self.pipeline = SocialOfferPipeline(self.c)

    def observation(self, post="77"):
        return ContentObservation(seller_id="known", channel_id="chan", post_id=post,
                                  published_at="2030-01-01T00:00:00+00:00",
                                  raw_metadata={"platform": "telegram", "handle": "known"})

    def key_of(self, offer):
        return self.c.execute("""SELECT p.canonical_key FROM offers o JOIN canonical_products p ON p.id = o.product_id
                                 WHERE o.id = ?""", (offer.id,)).fetchone()[0]

    def pinned(self, title=B):
        return db.title_key_pins([title])[title]

    def decide(self, decision="different"):
        res = db.resolve_disputed_pair_human(pk.pair_hash(A, B), decision, "audit")
        self.assertTrue(res.get("ok"), res)
        return res

    def test_decision_before_save(self):
        self.decide()
        offer = self.pipeline.process_candidates(self.observation(), [cand(900, B)])[0]
        self.assertEqual(self.key_of(offer), self.pinned())

    def test_decision_between_read_and_write(self):
        original = self.pipeline._resolve_canonical_product

        def interleave(*args, **kwargs):
            product = original(*args, **kwargs)
            self.assertFalse(self.c.in_transaction)
            self.decide()
            return product

        with patch.object(self.pipeline, "_resolve_canonical_product", side_effect=interleave):
            offer = self.pipeline.process_candidates(self.observation(), [cand(900, B)])[0]
        self.assertIn(".diff_", self.key_of(offer))
        self.assertEqual(self.key_of(offer), self.pinned())

    def test_different_after_commit_moves_offer_history_and_alias(self):
        offer = self.pipeline.process_candidates(self.observation(), [cand(900, B)])[0]
        self.c.commit()
        self.assertEqual(self.key_of(offer), self.common)
        old_pid = offer.product_id
        self.decide()
        self.assertEqual(self.key_of(offer), self.pinned())
        new_pid = OfferRepository(self.c).get_by_id(offer.id).product_id
        self.assertNotEqual(new_pid, old_pid)
        hist = self.c.execute("SELECT product_id FROM offer_price_history WHERE offer_id = ?", (offer.id,)).fetchall()
        self.assertEqual({r[0] for r in hist}, {new_pid})
        self.assertEqual(self.c.execute("SELECT COUNT(*) FROM identity_aliases WHERE kind = 'product' AND old_id = ?",
                                        (old_pid,)).fetchone()[0], 1)
        # Следующий пост остаётся на разделённом товаре и в той же строке
        again = self.pipeline.process_candidates(self.observation("78"), [cand(800, B)])[0]
        self.assertEqual((again.id, again.product_id, again.price), (offer.id, new_pid, 800))

    def test_same_moves_title_outside_cache_and_merges_lines(self):
        db.save_cached_canonical_keys_batch({B: self.common + ".other"})
        db.rekey_titles({B: self.common + ".other"})
        first = self.pipeline.process_candidates(self.observation("1"), [cand(900, A)])[0]
        second = self.pipeline.process_candidates(
            ContentObservation(seller_id="known", channel_id="chan", post_id="2",
                               published_at="2030-01-02T00:00:00+00:00",
                               raw_metadata={"platform": "telegram", "handle": "known"}), [cand(800, B)])[0]
        self.c.commit()
        self.assertNotEqual(first.product_id, second.product_id)
        self.decide("same")
        rows = self.c.execute("SELECT id, is_active, price, product_id FROM offers WHERE external_sku LIKE 'SOC-%'"
                              ).fetchall()
        self.assertEqual({r[3] for r in rows}, {rows[0][3]})
        self.assertEqual([(r[0], r[2]) for r in rows if r[1]], [(second.id, 800)])


class CaptionUnitTests(unittest.IsolatedAsyncioTestCase):
    CAPTIONS = [("audit_small", 1000, 100, "g"), ("audit_large", 6000, 1, "kg")]

    def assert_only_qualified(self, c):
        items = SearchCore(SearchRepository(c)).search("говядина").items
        offers = [o for item in items for o in item.offers]
        self.assertEqual(sorted(o.price for o in offers), [1000, 6000])
        self.assertTrue(all(o.unit and o.is_unit_price for o in offers), offers)
        self.assertEqual(len({o.product_id for o in offers}), 2)

    async def _ingest(self, answer_for=None, failure=False, vision=True):
        c = conn()
        for handle, price, quantity, unit in self.CAPTIONS:
            src = SocialSourceRepository(c).add_source("telegram", handle)
            post = FetchedPost(post_id="1", text=f"Говядина {price} ₸ за {quantity} {unit}",
                               media_urls=["https://cdn.example.invalid/photo.jpg"] if vision else [],
                               published_at="2030-01-01T10:00:00+00:00")
            if not vision:
                self.assertEqual(ingest_source(src, connector=connector([post]), conn=c).status, "OK")
                continue
            model = {"side_effect": RuntimeError("down")} if failure else {"return_value": {"result": {"items": [
                {"title": "Говядина", "price": price, "confidence": .95, "is_unit_price": True,
                 "quantity": quantity, "unit": unit}]}}}
            with patch("domain.vision_extraction.fetch_image", new_callable=AsyncMock,
                       return_value=FetchedImage(b"audit-image-content")), \
                    patch("ai_router.run", new_callable=AsyncMock, **model):
                self.assertEqual((await ingest_source_async(src, connector=connector([post]), conn=c)).status, "OK")
        return c

    async def test_with_photo(self):
        self.assert_only_qualified(await self._ingest())

    async def test_model_failure(self):
        self.assert_only_qualified(await self._ingest(failure=True))

    async def test_without_photo_and_sync(self):
        self.assert_only_qualified(await self._ingest(vision=False))

    async def test_caption_without_unit_gives_way_to_vision(self):
        c = conn()
        src = SocialSourceRepository(c).add_source("telegram", "audit_plain")
        post = FetchedPost(post_id="1", text="Говядина 6000 ₸", media_urls=["https://cdn.example.invalid/p.jpg"],
                           published_at="2030-01-01T10:00:00+00:00")
        answer = {"result": {"items": [{"title": "Говядина", "price": 6000, "confidence": .95,
                                        "is_unit_price": True, "quantity": 1, "unit": "kg"}]}}
        with patch("domain.vision_extraction.fetch_image", new_callable=AsyncMock,
                   return_value=FetchedImage(b"audit-image-content")), \
                patch("ai_router.run", new_callable=AsyncMock, return_value=answer):
            await ingest_source_async(src, connector=connector([post]), conn=c)
        offers = [o for i in SearchCore(SearchRepository(c)).search("говядина").items for o in i.offers]
        self.assertEqual([(o.price, o.unit) for o in offers], [(6000, "kg")])

    def test_text_forms(self):
        def parse(line):
            return SocialContentExtractor.extract_candidates(ContentObservation(text=line))[0]
        for line, pack in (("Говядина 6000 тг/кг", "per:1000g"), ("Говядина за 1 кг 6000 ₸", "per:1000g"),
                           ("Масло 900 ₸ за литр", "per:1000ml"), ("Молоко 1,5 л 900 ₸", "pack:1500ml"),
                           ("Вода 6 шт 1200 ₸", "pack:6pcs")):
            self.assertEqual(parse(line).price_basis()["pack"], pack, line)
        self.assertEqual(parse("Говядина за 1 кг 6000 ₸").raw_title, "Говядина")
        for line in ("Футболка / L 5000 ₸", "Samsung Galaxy S24 5G 300000 ₸", f"{TITLE} 300000 ₸", "Смартфон 1 шт 5000 ₸"):
            self.assertIsNone(parse(line).price_basis(), line)

    def test_appliance_size_keeps_model_identity(self):
        c = conn()
        title = "Samsung Galaxy S24 5G 8/256GB"
        p = SocialOfferPipeline(c)
        plain = p._product_for(title, None)[0]
        sized = ExtractedOfferCandidate(raw_title=title, price=1, size_or_volume="1.7 л")
        self.assertEqual(p._product_for(title, sized.price_basis())[0].id, plain.id)


class ContentRetentionTests(unittest.IsolatedAsyncioTestCase):
    SECRET = "Синтетическое полное описание продавца"
    TOKEN = "signed.jpg?token=synthetic"

    def post(self):
        return FetchedPost(post_id="1", text=f"{TITLE} 100000 ₸\n{self.SECRET}",
                           media_urls=[f"https://cdn.example.invalid/{self.TOKEN}"],
                           published_at="2030-01-01T10:00:00+00:00")

    def dump(self, c):
        c.commit()
        copy = sqlite3.connect(":memory:")
        c.backup(copy)
        text = "\n".join(copy.iterdump())
        copy.close()
        return text

    def assert_clean(self, c):
        dumped = self.dump(c)
        self.assertNotIn(self.SECRET, dumped)
        self.assertNotIn(self.TOKEN, dumped)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM offers").fetchone()[0], 1)
        row = c.execute("SELECT text, media_urls, raw_metadata, post_id FROM content_observations").fetchone()
        self.assertEqual((row[0], row[1], row[3]), ("", "[]", "1"))
        self.assertLessEqual(set(json.loads(row[2])), {"platform", "handle", "post_url", "source_id"})

    def test_sync_ingest(self):
        c = conn()
        src = SocialSourceRepository(c).add_source("telegram", "audit_content")
        ingest_source(src, connector=connector([self.post()]), conn=c)
        self.assert_clean(c)

    async def test_vision_ingest_success_and_model_failure(self):
        for model in ({"return_value": {"result": {"items": []}}}, {"side_effect": RuntimeError("down")}):
            c = conn()
            src = SocialSourceRepository(c).add_source("telegram", "audit_content")
            with patch("domain.vision_extraction.fetch_image", new_callable=AsyncMock,
                       return_value=FetchedImage(b"audit-image-content")), \
                    patch("ai_router.run", new_callable=AsyncMock, **model):
                await ingest_source_async(src, connector=connector([self.post()]), conn=c)
            self.assert_clean(c)

    def test_direct_save_drops_image_and_ocr_but_processing_still_sees_them(self):
        c = conn()
        payload = base64.b64encode(b"synthetic-image-content-only").decode()
        o = obs(text=self.SECRET)
        o.raw_metadata.update(image_bytes=payload, ocr_text=self.SECRET, vision_items=[{"title": self.SECRET}], image_path="/tmp/x")
        ContentObservationRepository(c).save_observation(o)
        dumped = self.dump(c)
        for leaked in (payload, self.SECRET, "/tmp/x"):
            self.assertNotIn(leaked, dumped)
        self.assertEqual(o.raw_metadata["image_bytes"], payload)
        loaded = ContentObservationRepository(c).get_by_id(o.id)
        self.assertEqual(loaded.raw_metadata["post_url"], "https://www.instagram.com/p/AUDIT/")

    def test_offer_keeps_only_bounded_title(self):
        c = conn()
        offer = SocialOfferPipeline(c).process_candidates(obs(), [ExtractedOfferCandidate(
            raw_title="Товар " + "x" * 500, price=1000, raw_snippet=self.SECRET)])[0]
        dumped = self.dump(c)
        self.assertNotIn(self.SECRET, dumped)
        self.assertNotIn("x" * 201, dumped)
        self.assertEqual(offer.raw_payload, {"source_post_id": "1"})

    def test_scrub_script_cleans_old_rows(self):
        c = conn()
        c.execute("""INSERT INTO content_observations (id, seller_id, channel_id, post_id, text, media_urls,
                     observed_at, raw_metadata) VALUES ('old', 's', 'c', '1', ?, ?, '2026-01-01', ?)""",
                  (self.SECRET, json.dumps([self.TOKEN]), json.dumps({"post_url": "u", "ocr_text": self.SECRET})))
        c.commit()
        self.assertEqual(scrub(c), 1)
        self.assertIn(self.SECRET, self.dump(c))
        self.assertEqual(scrub(c, apply=True), 1)
        self.assertNotIn(self.SECRET, self.dump(c))
        self.assertEqual(scrub(c), 0)
        self.assertEqual(json.loads(c.execute("SELECT raw_metadata FROM content_observations").fetchone()[0]), {"post_url": "u"})


class VisionTypeTests(unittest.IsolatedAsyncioTestCase):
    CONFIG = {"enabled": True, "has_ai": True, "ai_provider": "auto", "has_local": True,
              "local_api_base": "http://unused.invalid/v1", "local_model": "audit",
              "gemini_api_key": "mock", "gemini_model": "audit"}

    def test_every_field_type_rejected_by_router_and_alone_in_markup(self):
        good = {"title": "Хороший товар", "price": 2000, "confidence": .95}
        for field, values in (("unit", (["kg"], {"a": 1}, True, 5)), ("quantity", ([1], {"a": 1}, True)),
                              ("is_unit_price", ([1], {}, "да", 1)), ("is_price_occluded", ([], "нет")),
                              ("old_price", ([1], {}, True)), ("currency", (["KZT"], 1)), ("size_or_volume", (["1 л"],)),
                              ("condition", ([],)), ("city", ({},)), ("raw_snippet", ([],))):
            for value in values:
                bad = {"title": "Плохой", "price": 1000, "confidence": .95, field: value}
                with self.assertRaises(ValueError, msg=(field, value)):
                    parse_vision_response({"items": [bad, good]})
                parsed = VisionExtractionEngine.extract_from_structured([bad, good])
                self.assertEqual([c.raw_title for c in parsed], ["Хороший товар"], (field, value))

    async def test_wrong_unit_falls_back(self):
        invalid = {"items": [{"title": "Плохой", "price": 1000, "confidence": .95, "unit": ["kg"]},
                             {"title": "Хороший", "price": 2000, "confidence": .95}]}
        valid = {"items": [{"title": "Хороший", "price": 2000, "confidence": .95, "unit": "pcs"}]}
        with patch("ai_service.call_local_api", new_callable=AsyncMock, return_value=invalid), \
                patch("ai_service.call_gemini_api", new_callable=AsyncMock, return_value=valid) as gemini, \
                patch("ai_router.budget_allows", return_value=True), patch("ai_router.record_usage"), \
                patch("local_ai.pause_reason", return_value=None):
            result = await VisionExtractionEngine.extract_from_image(b"audit-image-content", config=self.CONFIG)
        self.assertEqual((gemini.await_count, [c.raw_title for c in result]), (1, ["Хороший"]))


class PostOrderTests(unittest.TestCase):
    SAME_TIME = "2026-10-31T10:00:00+00:00"

    def run_order(self, posts, platform="telegram"):
        c = conn()
        p = SocialOfferPipeline(c)
        for post, price in posts:
            p.process_candidates(obs(post, platform=platform, published_at=self.SAME_TIME), [cand(price)])
        return c

    def test_telegram_ids_are_numbers(self):
        for old, new in (("9", "10"), ("99", "100")):
            for order in (((old, 150000), (new, 100000)), ((new, 100000), (old, 150000))):
                c = self.run_order(order)
                row = c.execute("SELECT price, raw_payload_json FROM offers").fetchone()
                self.assertEqual((row[0], json.loads(row[1])["source_post_id"]), (100000, new), order)
                self.assertEqual(c.execute("SELECT COUNT(*) FROM offers").fetchone()[0], 1)

    def test_opaque_ids_do_not_depend_on_order(self):
        results = {self.run_order(order, "instagram").execute("SELECT price FROM offers").fetchone()[0]
                   for order in ((("Cab", 1000), ("Dxy", 2000)), (("Dxy", 2000), ("Cab", 1000)))}
        self.assertEqual(len(results), 1)


class StoryTtlTests(unittest.TestCase):
    NOW = datetime.datetime.now(datetime.timezone.utc)

    def at(self, hours):
        return (self.NOW + datetime.timedelta(hours=hours)).isoformat()

    def story(self, observed, published=None):
        return obs("1", content_type=ContentType.STORY, published_at=published, observed_at=observed)

    def test_recollection_does_not_revive_old_story(self):
        c = conn()
        p = SocialOfferPipeline(c)
        pub = self.at(-25)
        first = p.process_candidates(self.story(pub, pub), [cand()])[0]
        second = p.process_candidates(self.story(self.at(0), pub), [cand()])[0]
        self.assertEqual(second.expires_at, first.expires_at)
        self.assertEqual(second.availability, "out_of_stock")
        self.assertEqual(second.observed_at, first.observed_at)
        self.assertEqual(len(OfferRepository(c).get_price_history(first.id)), 1)
        self.assertFalse(SearchCore(SearchRepository(c)).search("iphone 15").items[0].has_purchasable_offers)

    def test_story_inside_24h_stays_on_sale(self):
        c = conn()
        pub = self.at(-23)
        offer = SocialOfferPipeline(c).process_candidates(self.story(self.at(0), pub), [cand()])[0]
        self.assertEqual(offer.availability, "in_stock")

    def test_without_publish_time_first_seen_expiry_holds(self):
        c = conn()
        p = SocialOfferPipeline(c)
        first = p.process_candidates(self.story(self.at(-25)), [cand()])[0]
        second = p.process_candidates(self.story(self.at(0)), [cand()])[0]
        self.assertEqual(second.expires_at, first.expires_at)
        self.assertEqual(second.availability, "out_of_stock")

    def test_timezone_offset_of_publish_time(self):
        almaty = datetime.timezone(datetime.timedelta(hours=5))
        pub = (self.NOW - datetime.timedelta(hours=25)).astimezone(almaty).isoformat()
        offer = SocialOfferPipeline(conn()).process_candidates(self.story(self.at(0), pub), [cand()])[0]
        self.assertEqual(offer.availability, "out_of_stock")

    def test_edit_of_live_story_is_a_new_measurement(self):
        c = conn()
        p = SocialOfferPipeline(c)
        pub = self.at(-2)
        first = p.process_candidates(self.story(pub, pub), [cand(100000)])[0]
        p.process_candidates(self.story(self.at(0), pub), [cand(90000)])
        self.assertEqual(len(OfferRepository(c).get_price_history(first.id)), 2)


class ChannelIdentityTests(unittest.TestCase):
    def setUp(self):
        self.c = conn()
        self.p = SocialOfferPipeline(self.c)
        SellerRepository(self.c).save_or_update(Seller(id="confirmed", slug="confirmed", name="Confirmed"))

    def offer(self, post, handle, price, **kw):
        o = ContentObservation(post_id=post, seller_id=kw.pop("seller_id", "confirmed"), published_at="2030-01-01T00:00:00+00:00",
                               raw_metadata={"platform": "instagram", "handle": handle,
                                             "post_url": f"https://www.instagram.com/p/{handle}/"}, **kw)
        return self.p.process_candidates(o, [cand(price)])[0]

    def test_two_handles_are_two_channels_and_update_independently(self):
        one = self.offer("1", "audit_one", 100000)
        two = self.offer("1", "audit_two", 90000)
        self.assertEqual(one.seller_id, two.seller_id)
        self.assertNotEqual(one.channel_id, two.channel_id)
        self.assertNotEqual(one.id, two.id)
        self.assertEqual(self.offer("1", "AUDIT_ONE", 100000).channel_id, one.channel_id)
        base_urls = {r[0] for r in self.c.execute("SELECT base_url FROM channels")}
        self.assertEqual(base_urls, {"https://instagram.com/audit_one", "https://instagram.com/audit_two"})
        repo = OfferRepository(self.c)
        self.assertEqual((repo.get_by_id(one.id).price, repo.get_by_id(two.id).price), (100000, 90000))
        self.assertEqual(repo.get_by_id(two.id).url, "https://www.instagram.com/p/audit_two/")
        self.assertEqual(len(repo.get_price_history(two.id)), 1)

    def test_explicit_channel_must_belong_to_seller(self):
        SellerRepository(self.c).save_or_update(Seller(id="other", slug="other", name="Other"))
        ChannelRepository(self.c).save_or_update(Channel(id="ch", seller_id="other", channel_type="instagram", name="X"))
        with self.assertRaises(ValueError):
            self.offer("1", "x", 100, channel_id="ch")
        self.assertEqual(self.offer("2", "x", 100, channel_id="ch", seller_id="").seller_id, "other")


class V692FixesTests(OperatorDecisionTests):
    """Точные регрессионные тесты для замечаний приёмки 6.9.2 (V692-01…03)."""

    def test_v692_01_parse_unit_price_slash_and_za_variants(self):
        from domain.social_content import parse_unit_price
        cases = [
            ("Говядина 1000 ₸/100 г", (100.0, "g")),
            ("Говядина 6000 ₸/1 кг", (1.0, "kg")),
            ("Сок 500 ₸ / 0,5 л", (0.5, "l")),
            ("Молоко 400 ₸ /0.5л", (0.5, "l")),
            ("Чай 1200 ₸/100g", (100.0, "g")),
            ("Яблоки 800 ₸/кг", (1.0, "kg")),
            ("Вода 200 ₸/л", (1.0, "l")),
            ("Яйца 600 ₸/10 шт", (10.0, "pcs")),
            ("Сыр 1500 ₸ за 100 г", (100.0, "g")),
            ("Арбуз 200 ₸ за 1 кг", (1.0, "kg")),
            ("Клубника 3000 ₸ за кг", (1.0, "kg")),
            ("iPhone 15 5G 450000 ₸", None),
        ]
        for line, expected in cases:
            with self.subTest(line=line):
                res = parse_unit_price(line)
                self.assertEqual(res, expected, f"Failed parsing: {line}")

    def test_v692_01_slash_quantity_different_products_and_basis(self):
        c = sqlite3.connect(":memory:")
        init_schema_v2(c)
        for handle, caption in [("src100", "Говядина 1000 ₸/100 г"), ("srckg", "Говядина 6000 ₸/1 кг")]:
            src = SocialSourceRepository(c).add_source("telegram", handle)
            posts = [FetchedPost(post_id="1", text=caption, published_at="2030-01-01T00:00:00+00:00")]
            result = ingest_source(src, connector=connector(posts), conn=c)
            self.assertEqual(result.status, "OK")
        search_res = SearchCore(SearchRepository(c)).search("говядина")
        offers = [o for item in search_res.items for o in item.offers]
        self.assertEqual(len(offers), 2)
        # Разные карточки CanonicalProduct
        self.assertNotEqual(offers[0].product_id, offers[1].product_id)
        # Основания цены сохранены
        by_price = {o.price: o for o in offers}
        self.assertEqual(by_price[1000].unit, "g")
        self.assertEqual(by_price[1000].quantity, 100.0)
        self.assertEqual(by_price[6000].unit, "kg")
        self.assertEqual(by_price[6000].quantity, 1.0)

    def test_v692_02_same_keeps_newer_numeric_post_both_directions(self):
        # 1. Пост 10 (1000 ₸) против поста 9 (1500 ₸)
        db.save_cached_canonical_keys_batch({B: self.common + ".other"})
        db.rekey_titles({B: self.common + ".other"})
        self.pipeline.process_candidates(self.observation("10"), [cand(1000, A)])
        self.pipeline.process_candidates(self.observation("9"), [cand(1500, B)])
        self.c.commit()
        self.decide("same")
        active = self.c.execute("SELECT price, raw_payload_json FROM offers WHERE external_sku LIKE 'SOC-%' AND is_active = 1").fetchall()
        self.assertEqual([r[0] for r in active], [1000.0])
        post_id = json.loads(active[0][1])["source_post_id"]
        self.assertEqual(post_id, "10")

    def test_v692_02_same_keeps_newer_numeric_post_99_vs_100(self):
        db.save_cached_canonical_keys_batch({B: self.common + ".other2"})
        db.rekey_titles({B: self.common + ".other2"})
        self.pipeline.process_candidates(self.observation("99"), [cand(2000, A)])
        self.pipeline.process_candidates(self.observation("100"), [cand(1800, B)])
        self.c.commit()
        self.decide("same")
        active = self.c.execute("SELECT price, raw_payload_json FROM offers WHERE external_sku LIKE 'SOC-%' AND is_active = 1").fetchall()
        self.assertEqual([r[0] for r in active], [1800.0])
        post_id = json.loads(active[0][1])["source_post_id"]
        self.assertEqual(post_id, "100")


class ConnectionProxy:
    """Соединение, у которого выбранная команда SQLite завершается отказом."""

    def __init__(self, conn, failing, result=None):
        self.conn, self.failing, self.result = conn, failing, result

    def __getattr__(self, name):
        return getattr(self.conn, name)

    def execute(self, sql, *args):
        if self.failing in sql:
            if self.result is None:
                raise sqlite3.OperationalError("database is locked")
            return type("Row", (), {"fetchone": lambda _self: self.result})()
        return self.conn.execute(sql, *args)


class ScrubSafetyTests(unittest.TestCase):
    """V693-01: файлы меняет только SQLite, разрешённые значения не страдают, итог различим."""

    SHORT = "SYNTHETIC_SHORT_POST_MARKER"
    LONG = "A" * 10000 + "SYNTHETIC_OVERFLOW_MARKER" + "B" * 10000
    IMAGE = base64.b64encode(b"SYNTHETIC_IMAGE" * 2000).decode()
    TITLE = "Samsung Galaxy S24"
    URL = "https://t.me/audit/1"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="kzph-scrub-")
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "prices.db"
        self.c = sqlite3.connect(self.path, timeout=0.05)
        self.addCleanup(self.c.close)
        self.c.execute("PRAGMA journal_mode=WAL")
        init_schema_v2(self.c)
        self.product = ProductRepository(self.c).get_or_create_canonical("audit:kept", self.TITLE, "smartphones")
        rows = [("short", self.SHORT + " " + self.TITLE, {"post_url": self.URL}),
                ("long", self.LONG, {"post_url": self.URL, "ocr_text": self.SHORT}),
                ("image", "", {"post_url": self.URL, "image_bytes": self.IMAGE})]
        for row_id, text, meta in rows:
            self.c.execute("""INSERT INTO content_observations (id, seller_id, channel_id, text, media_urls, observed_at,
                              raw_metadata) VALUES (?, 's', 'c', ?, '[]', '2026-01-01', ?)""",
                           (row_id, text, json.dumps(meta)))
        self.c.commit()
        self.c.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def leftovers(self):
        markers = (self.SHORT, "SYNTHETIC_OVERFLOW_MARKER", self.IMAGE[:200])
        files = [p for p in (self.path, Path(f"{self.path}-wal")) if p.exists()]
        return [m for m in markers for f in files if m.encode() in f.read_bytes()]

    def assert_allowed_values_kept(self):
        fresh = sqlite3.connect(self.path)
        self.addCleanup(fresh.close)
        self.assertEqual(fresh.execute("SELECT title FROM canonical_products WHERE id = ?", (self.product.id,)).fetchone()[0],
                         self.TITLE)
        rows = fresh.execute("SELECT text, media_urls, raw_metadata FROM content_observations").fetchall()
        self.assertEqual(rows, [("", "[]", json.dumps({"post_url": self.URL}))] * 3)
        self.assertEqual(fresh.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_clean_run_removes_every_form_and_keeps_allowed_values(self):
        self.assertTrue(self.leftovers())
        result = scrub(self.c, apply=True)
        self.assertEqual((int(result), result.physical_complete, result.reason), (3, True, ""))
        self.assertEqual(self.leftovers(), [])
        self.assert_allowed_values_kept()
        again = scrub(self.c, apply=True)
        self.assertEqual((int(again), again.physical_complete), (0, True))
        backup = sqlite3.connect(":memory:")
        self.addCleanup(backup.close)
        self.c.backup(backup)
        dumped = "\n".join(backup.iterdump())
        self.assertNotIn(self.SHORT, dumped)
        self.assertIn(self.TITLE, dumped)

    def test_old_reader_gives_distinct_result_then_rerun_finishes(self):
        reader = sqlite3.connect(self.path)
        self.addCleanup(reader.close)
        reader.execute("BEGIN")
        reader.execute("SELECT text FROM content_observations").fetchone()
        result = scrub(self.c, apply=True)
        self.assertEqual(int(result), 3)
        self.assertFalse(result.physical_complete)
        self.assertTrue(result.reason)
        self.assertEqual(self.c.execute("SELECT text FROM content_observations WHERE id = 'short'").fetchone()[0], "")
        self.assertTrue(self.leftovers(), "читатель удерживает старые страницы; блокировки SQLite не обходятся")
        self.assert_allowed_values_kept()
        reader.rollback()
        finished = scrub(self.c, apply=True)
        self.assertEqual((int(finished), finished.physical_complete), (0, True))
        self.assertEqual(self.leftovers(), [])

    def test_failures_are_reported_not_hidden(self):
        for failing, result, reason in (("wal_checkpoint", (1, 5, 0), "wal_busy"), ("wal_checkpoint", (0, 5, 3), "wal_not_empty"),
                                        ("wal_checkpoint", None, "sqlite_error:OperationalError"),
                                        ("VACUUM", None, "sqlite_error:OperationalError"),
                                        ("integrity_check", ("corrupt",), "integrity_failed")):
            outcome = scrub(ConnectionProxy(self.c, failing, result), apply=True)
            self.assertFalse(outcome.physical_complete, failing)
            self.assertEqual(outcome.reason, reason)
        self.assertTrue(scrub(self.c, apply=True).physical_complete)

    def test_cli_exit_codes(self):
        from scripts.scrub_social_content import EXIT_PHYSICAL_INCOMPLETE, main
        self.c.close()
        reader = sqlite3.connect(self.path)
        self.addCleanup(reader.close)
        reader.execute("BEGIN")
        reader.execute("SELECT text FROM content_observations").fetchone()
        real_connect = sqlite3.connect
        with patch("sys.argv", ["scrub", str(self.path), "--apply"]), \
                patch("sqlite3.connect", lambda path: real_connect(path, timeout=0.05)):
            self.assertEqual(main(), EXIT_PHYSICAL_INCOMPLETE)
            reader.rollback()
            self.assertEqual(main(), 0)

    def test_script_does_not_open_database_files_itself(self):
        source = Path("scripts/scrub_social_content.py").read_text(encoding="utf-8")
        for forbidden in ("open(", "read_bytes", "write_bytes", "r+b"):
            self.assertNotIn(forbidden, source)


class SmallUnitTests(unittest.IsolatedAsyncioTestCase):
    """V693-02: явное основание цены отделено от поколения связи в названии."""

    def test_explicit_forms(self):
        for line, expected in (("Шафран 2000 ₸/1g", (1.0, "g")), ("Шафран 6000 ₸/5g", (5.0, "g")),
                               ("Шафран 6000 ₸/5G", (5.0, "g")), ("Шафран 900 ₸/0.5g", (0.5, "g")),
                               ("Шафран 900 ₸/0,5 g", (0.5, "g")), ("Шафран 900 ₸ / 5 g", (5.0, "g")),
                               ("Шафран 900 ₸/5г", (5.0, "g")), ("Шафран 900 ₸ / 5 г", (5.0, "g")),
                               ("Шафран 900 ₸ за 5g", (5.0, "g")), ("Шафран /5g 900 ₸", (5.0, "g"))):
            self.assertEqual(parse_unit_price(line), expected, line)

    def test_network_and_clothes_are_not_units(self):
        for line in ("Samsung Galaxy S24 5G 300000 ₸", "Samsung Galaxy S24/5G/256GB 300000 ₸", "Роутер/4G 20000 тг",
                     "Футболка / L 5000 ₸", "Футболка/L 5000 ₸"):
            self.assertIsNone(parse_unit_price(line), line)
            parsed = SocialContentExtractor.extract_candidates(ContentObservation(text=line))[0]
            self.assertIsNone(parsed.price_basis(), line)
        kept = SocialContentExtractor.extract_candidates(ContentObservation(text="Samsung Galaxy S24/5G 300000 ₸"))[0]
        self.assertIn("5G", kept.raw_title)

    def _source(self, c, handle, price, quantity):
        src = SocialSourceRepository(c).add_source("telegram", handle)
        return src, FetchedPost(post_id="1", text=f"Шафран {price} ₸/{quantity}g",
                                media_urls=["https://cdn.example.invalid/p.jpg"],
                                published_at="2030-01-01T00:00:00+00:00")

    def assert_separate(self, c):
        offers = [o for i in SearchCore(SearchRepository(c)).search("шафран").items for o in i.offers]
        self.assertEqual(sorted((o.price, o.quantity, o.unit) for o in offers), [(2000, 1, "g"), (6000, 5, "g")])
        self.assertEqual(len({o.product_id for o in offers}), 2)

    async def test_vision_path_with_failed_model(self):
        c = conn()
        for handle, price, quantity in (("audit1g", 2000, 1), ("audit5g", 6000, 5)):
            src, post = self._source(c, handle, price, quantity)
            with patch("domain.vision_extraction.fetch_image", new_callable=AsyncMock,
                       return_value=FetchedImage(b"audit-image-content")), \
                    patch("ai_router.run", new_callable=AsyncMock, side_effect=RuntimeError("down")):
                await ingest_source_async(src, connector=connector([post]), conn=c)
        self.assert_separate(c)

    def test_sync_path(self):
        c = conn()
        for handle, price, quantity in (("audit1g", 2000, 1), ("audit5g", 6000, 5)):
            src, post = self._source(c, handle, price, quantity)
            ingest_source(src, connector=connector([post]), conn=c)
        self.assert_separate(c)


if __name__ == "__main__":
    unittest.main()
