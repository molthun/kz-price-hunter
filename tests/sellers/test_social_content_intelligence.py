"""S2-E08: Social Content Intelligence & Stories TTL.

Проверяем поведением: детерминированный разбор цен/состояний/городов из текста
Telegram и Instagram, защиту от ложных цен (телефоны, объём памяти), TTL сторис,
просроченные наблюдения не становятся доступными предложениями, персистентность
и очистку по сроку.
"""
from tests import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import datetime
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
from domain.search_core import SearchCore
from domain.social_connector import InstagramConnector, SocialPlatform, SocialSource, posts_to_observations
from domain.social_content import (
    ContentObservation,
    ContentType,
    ExtractedOfferCandidate,
    SocialContentExtractor,
)
from repositories import (
    ChannelRepository,
    ContentObservationRepository,
    OfferRepository,
    ProductRepository,
    SearchRepository,
    SellerRepository,
    init_schema_v2,
)

NOW_ISO = datetime.datetime.now(datetime.timezone.utc).isoformat()
EXPIRED_ISO = "2026-10-02T10:00:00+00:00"
BEFORE_EXPIRY_ISO = "2026-10-01T12:00:00+00:00"
AFTER_EXPIRY_ISO = "2026-10-05T00:00:00+00:00"


def observation(text: str, content_type: str = ContentType.POST, **kwargs) -> ContentObservation:
    params = dict(
        seller_id="seller_1",
        channel_id="channel_1",
        content_type=content_type,
        post_id="post_1",
        text=text,
        observed_at=BEFORE_EXPIRY_ISO,
    )
    params.update(kwargs)
    return ContentObservation(**params)


class PriceExtractionTest(unittest.TestCase):
    def prices(self, text: str):
        return [(c.raw_title, c.price, c.old_price)
                for c in SocialContentExtractor.extract_candidates(observation(text))]

    def test_explicit_tenge_price(self):
        offers = self.prices("iPhone 15 256GB 499 000 тг")
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0][1], 499000.0)
        self.assertIn("iphone", offers[0][0].lower())

    def test_tenge_symbol_and_kzt(self):
        self.assertEqual(self.prices("Samsung S24 500000 ₸")[0][1], 500000.0)
        self.assertEqual(self.prices("Ноутбук Asus 450 000 KZT")[0][1], 450000.0)

    def test_thousands_suffix(self):
        self.assertEqual(self.prices("Dyson V15 250к")[0][1], 250000.0)

    def test_keyword_price_without_currency(self):
        self.assertEqual(self.prices("PlayStation 5 цена: 45 000")[0][1], 45000.0)

    def test_was_now_discount(self):
        result = self.prices("iPhone 15 было 550 000 тг стало 499 000 тг")
        self.assertEqual(result[0][1], 499000.0)
        self.assertEqual(result[0][2], 550000.0)

    def test_arrow_discount(self):
        result = self.prices("RTX 4070 550 000 -> 499 000")
        self.assertEqual(result[0][1], 499000.0)
        self.assertEqual(result[0][2], 550000.0)


class FalsePositiveTest(unittest.TestCase):
    def test_phone_number_is_not_a_price(self):
        self.assertEqual(
            SocialContentExtractor.extract_candidates(
                observation("Пишите на 87011234567")), [])

    def test_storage_capacity_is_not_a_price(self):
        offers = SocialContentExtractor.extract_candidates(
            observation("iPhone 15 256GB 499 000 тг"))
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0].price, 499000.0)

    def test_text_without_price_has_no_candidates(self):
        self.assertEqual(
            SocialContentExtractor.extract_candidates(observation("Новые поступления в магазине")), [])

    def test_empty_text_has_no_candidates(self):
        self.assertEqual(SocialContentExtractor.extract_candidates(observation("")), [])


class AttributeExtractionTest(unittest.TestCase):
    def test_used_condition(self):
        cand = SocialContentExtractor.extract_candidates(
            observation("iPhone 14 б/у 300 000 тг"))[0]
        self.assertEqual(cand.condition, Condition.USED)

    def test_refurbished_and_open_box_conditions(self):
        refurb = SocialContentExtractor.extract_candidates(
            observation("MacBook Air восстановленный 400 000 тг"))[0]
        open_box = SocialContentExtractor.extract_candidates(
            observation("Телевизор витринный образец 180 000 тг"))[0]
        self.assertEqual(refurb.condition, Condition.REFURBISHED)
        self.assertEqual(open_box.condition, Condition.OPEN_BOX)

    def test_city_from_text(self):
        cand = SocialContentExtractor.extract_candidates(
            observation("Ноутбук Asus Алматы 450 000 тг"))[0]
        self.assertEqual(cand.city, "Алматы")

    def test_city_inherited_from_post_header(self):
        text = "Доставка по Казахстану, Алматы\niPhone 15 499 000 тг"
        cand = SocialContentExtractor.extract_candidates(observation(text))[0]
        self.assertEqual(cand.city, "Алматы")

    def test_multiline_post_yields_one_candidate_per_priced_line(self):
        text = "iPhone 15 399 000 тг\nSamsung S24 350 000 тг"
        offers = SocialContentExtractor.extract_candidates(observation(text))
        self.assertEqual(len(offers), 2)
        self.assertEqual({o.price for o in offers}, {399000.0, 350000.0})

    def test_candidates_inherit_observation_expiry(self):
        obs = observation("iPhone 15 499 000 тг", content_type=ContentType.STORY)
        cand = SocialContentExtractor.extract_candidates(obs)[0]
        self.assertEqual(cand.expires_at, obs.expires_at)


class StoryTtlTest(unittest.TestCase):
    def test_story_gets_24h_ttl_from_observation_time(self):
        obs = observation("iPhone 15 499 000 тг", content_type=ContentType.STORY,
                          observed_at="2026-10-01T10:00:00+00:00")
        self.assertEqual(obs.expires_at, "2026-10-02T10:00:00+00:00")

    def test_regular_post_has_no_default_ttl(self):
        obs = observation("iPhone 15 499 000 тг", content_type=ContentType.POST)
        self.assertIsNone(obs.expires_at)
        self.assertFalse(obs.is_expired(AFTER_EXPIRY_ISO))

    def test_observation_is_expired_only_after_its_ttl(self):
        obs = observation("iPhone 15 499 000 тг", content_type=ContentType.STORY,
                          observed_at="2026-10-01T10:00:00+00:00")
        self.assertFalse(obs.is_expired(BEFORE_EXPIRY_ISO))
        self.assertTrue(obs.is_expired(AFTER_EXPIRY_ISO))

    def test_naive_story_timestamps_are_interpreted_as_utc(self):
        obs = observation("iPhone 15 499 000 тг", content_type=ContentType.STORY,
                          observed_at="2026-10-01T10:00:00")
        self.assertEqual(obs.expires_at, "2026-10-02T10:00:00")
        self.assertTrue(obs.is_expired("2026-10-02T10:00:00+00:00"))
        self.assertTrue(obs.is_expired("2026-10-02T13:00:00+03:00"))

    def test_naive_now_and_expiry_compare_as_utc(self):
        obs = observation("iPhone 15 499 000 тг", content_type=ContentType.STORY,
                          expires_at="2026-10-02T10:00:00")
        self.assertFalse(obs.is_expired("2026-10-02T09:59:59"))
        self.assertTrue(obs.is_expired("2026-10-02T10:00:00"))

    def test_default_now_treats_naive_past_expiry_as_expired(self):
        obs = observation("iPhone 15 499 000 тг", content_type=ContentType.STORY,
                          expires_at="2020-01-02T00:00:00")
        self.assertTrue(obs.is_expired())

    def test_expiry_boundary_is_the_same_in_every_timezone_representation(self):
        # All expiry strings denote 10:00 UTC; include a previous local date.
        expiries = ("2026-10-02T10:00:00", "2026-10-02T10:00:00Z",
                    "2026-10-02T15:00:00+05:00", "2026-10-01T22:00:00-12:00")
        times = (("2026-10-02T09:59:59.999999", False),
                 ("2026-10-02T14:59:59.999999+05:00", False),
                 ("2026-10-02T10:00:00", True),
                 ("2026-10-02T05:00:00-05:00", True),
                 ("2026-10-02T10:00:00.000001Z", True))
        for expiry in expiries:
            obs = observation("iPhone 15 499 000 тг", expires_at=expiry)
            for now, expected in times:
                with self.subTest(expiry=expiry, now=now):
                    self.assertEqual(obs.is_expired(now), expected)

    def test_expired_observation_never_becomes_purchasable_offer(self):
        obs = observation("iPhone 15 499 000 тг", content_type=ContentType.STORY)
        candidate = ExtractedOfferCandidate(raw_title="iPhone 15", price=499000.0)
        offer = SocialContentExtractor.candidate_to_offer(
            candidate, obs, product_id="product_1", external_sku="SKU-1")
        self.assertEqual(offer.availability, Availability.OUT_OF_STOCK)
        self.assertEqual(offer.expires_at, obs.expires_at)

    def test_fresh_observation_keeps_candidate_availability(self):
        obs = observation("iPhone 15 499 000 тг", content_type=ContentType.STORY,
                          expires_at="2099-01-01T00:00:00+00:00")
        candidate = ExtractedOfferCandidate(raw_title="iPhone 15", price=499000.0)
        offer = SocialContentExtractor.candidate_to_offer(
            candidate, obs, product_id="product_1", external_sku="SKU-1")
        self.assertEqual(offer.availability, Availability.IN_STOCK)

    def test_expired_naive_story_becomes_out_of_stock_offer(self):
        obs = observation("iPhone 15 499 000 тг", content_type=ContentType.STORY,
                          observed_at="2020-01-01T00:00:00")
        candidate = ExtractedOfferCandidate(raw_title="iPhone 15", price=499000.0)
        offer = SocialContentExtractor.candidate_to_offer(
            candidate, obs, product_id="product_1", external_sku="SKU-1")
        self.assertEqual(offer.availability, Availability.OUT_OF_STOCK)

    def test_instagram_offer_without_permalink_has_no_fabricated_telegram_url(self):
        obs = observation("iPhone 15 499 000 тг", raw_metadata={
            "platform": "instagram", "post_url": ""})
        candidate = ExtractedOfferCandidate(raw_title="iPhone 15", price=499000.0)
        offer = SocialContentExtractor.candidate_to_offer(
            candidate, obs, product_id="product_1", external_sku="SKU-1")
        self.assertEqual(offer.url, "")
        self.assertNotIn("t.me", offer.url)

    def test_missing_post_url_metadata_does_not_invent_telegram_link(self):
        obs = observation("iPhone 15 499 000 тг", raw_metadata={"platform": "instagram"})
        candidate = ExtractedOfferCandidate(raw_title="iPhone 15", price=499000.0)
        offer = SocialContentExtractor.candidate_to_offer(
            candidate, obs, product_id="product_1", external_sku="SKU-1")
        self.assertEqual(offer.url, "")

    def test_blank_post_url_is_treated_as_missing(self):
        obs = observation("iPhone 15 499 000 тг", raw_metadata={
            "platform": "instagram", "post_url": "   "})
        candidate = ExtractedOfferCandidate(raw_title="iPhone 15", price=499000.0)
        offer = SocialContentExtractor.candidate_to_offer(
            candidate, obs, product_id="product_1", external_sku="SKU-1")
        self.assertEqual(offer.url, "")

    def test_instagram_permalink_is_kept_on_offer(self):
        permalink = "https://www.instagram.com/p/abc/"
        obs = observation("iPhone 15 499 000 тг", raw_metadata={
            "platform": "instagram", "post_url": permalink})
        candidate = ExtractedOfferCandidate(raw_title="iPhone 15", price=499000.0)
        offer = SocialContentExtractor.candidate_to_offer(
            candidate, obs, product_id="product_1", external_sku="SKU-1")
        self.assertEqual(offer.url, permalink)

    def test_instagram_parse_to_offer_preserves_only_the_actual_permalink(self):
        source = SocialSource(id="src_ig", platform=SocialPlatform.INSTAGRAM,
                              handle="17841400000000000", seller_id="seller_1",
                              channel_id="channel_1")
        permalink = "https://www.instagram.com/p/abc/"
        for value in (None, "", "   ", permalink):
            with self.subTest(permalink=value):
                item = {"id": "17900000000000001", "caption": "iPhone 15 499 000 тг"}
                if value is not None:
                    item["permalink"] = value
                posts = InstagramConnector().parse_media({"data": [item]}, source.handle)
                obs = posts_to_observations(posts, source)[0]
                candidates = SocialContentExtractor.extract_candidates(obs)
                self.assertEqual(len(candidates), 1)
                offer = SocialContentExtractor.candidate_to_offer(candidates[0], obs, "product_1")
                self.assertEqual(offer.url, (value or "").strip())
                self.assertNotIn("t.me", offer.url)


class ContentObservationRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_schema_v2(self.conn)
        self.repo = ContentObservationRepository(self.conn)

    def test_roundtrip_keeps_only_technical_fields(self):
        obs = observation(
            "iPhone 15 499 000 тг",
            media_urls=["https://cdn.example.invalid/a.jpg"],
            raw_metadata={"post_url": "https://t.me/example/1", "views": 42},
        )
        self.repo.save_observation(obs)
        loaded = self.repo.get_by_id(obs.id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.media_urls, [])
        self.assertEqual(loaded.raw_metadata, {"post_url": "https://t.me/example/1"})
        self.assertEqual(loaded.text, "")

    def test_repeated_save_updates_existing_observation(self):
        obs = observation("iPhone 15 499 000 тг", raw_metadata={"post_url": "https://t.me/example/1"})
        self.repo.save_observation(obs)
        obs.raw_metadata["post_url"] = "https://t.me/example/2"
        self.repo.save_observation(obs)
        self.assertEqual(self.repo.get_by_id(obs.id).raw_metadata["post_url"], "https://t.me/example/2")

    def test_find_by_post(self):
        obs = observation("iPhone 15 499 000 тг", post_id="post_42")
        self.repo.save_observation(obs)
        self.assertEqual(self.repo.find_by_post("channel_1", "post_42").id, obs.id)
        self.assertIsNone(self.repo.find_by_post("channel_1", "missing"))

    def test_expired_observations_are_hidden_by_default(self):
        fresh = observation("iPhone 15 499 000 тг", post_id="post_fresh")
        expired = observation("iPhone 14 300 000 тг", post_id="post_expired",
                              content_type=ContentType.STORY,
                              observed_at="2026-10-01T10:00:00+00:00")
        self.repo.save_observation(fresh)
        self.repo.save_observation(expired)
        self.assertEqual([o.id for o in self.repo.find_by_seller(
            "seller_1", now_iso=AFTER_EXPIRY_ISO)], [fresh.id])
        self.assertEqual(len(self.repo.find_by_seller(
            "seller_1", include_expired=True)), 2)
        self.assertEqual([o.id for o in self.repo.find_by_channel(
            "channel_1", now_iso=AFTER_EXPIRY_ISO)], [fresh.id])

    def test_purge_removes_only_expired_rows(self):
        fresh = observation("iPhone 15 499 000 тг", post_id="post_fresh")
        expired = observation("iPhone 14 300 000 тг", post_id="post_expired",
                              content_type=ContentType.STORY,
                              observed_at="2026-10-01T10:00:00+00:00")
        self.repo.save_observation(fresh)
        self.repo.save_observation(expired)
        self.assertEqual(self.repo.purge_expired(now_iso=AFTER_EXPIRY_ISO), 1)
        self.assertIsNone(self.repo.get_by_id(expired.id))
        self.assertIsNotNone(self.repo.get_by_id(fresh.id))

    def test_extract_candidates_works_on_observation_in_memory(self):
        obs = observation("iPhone 15 499 000 тг")
        self.repo.save_observation(obs)
        candidates = self.repo.extract_candidates(obs)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].price, 499000.0)
        self.assertEqual(self.repo.extract_candidates(self.repo.get_by_id(obs.id)), [])


class SearchCoreTtlTest(unittest.TestCase):
    """Истёкший TTL не должен попадать в purchasable и «лучшую цену» Search Core."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_schema_v2(self.conn)
        product_repo = ProductRepository(self.conn)
        seller_repo = SellerRepository(self.conn)
        channel_repo = ChannelRepository(self.conn)
        offer_repo = OfferRepository(self.conn)
        self.search_core = SearchCore(SearchRepository(self.conn))

        product_repo.save_or_update(CanonicalProduct(
            id="prod_social_1",
            canonical_key="apple_iphone_15_256",
            title="Apple iPhone 15 256GB",
            category="Смартфоны",
            brand="Apple",
            model="iPhone 15",
        ))
        seller_repo.save_or_update(Seller(id="s_tg", slug="tg_resale", name="TG Resale", rating=4.5))
        channel_repo.save_or_update(Channel(
            id="ch_tg", seller_id="s_tg", channel_type=ChannelType.TELEGRAM, name="Telegram"))
        offer_repo.save_or_update(Offer(
            id="off_fresh", product_id="prod_social_1", seller_id="s_tg", channel_id="ch_tg",
            external_sku="SKU-FRESH", url="https://t.me/example/1", price=499000.0,
            condition=Condition.NEW, city="Алматы", observed_at=NOW_ISO,
            expires_at="2099-01-01T00:00:00+00:00",
        ))
        offer_repo.save_or_update(Offer(
            id="off_expired", product_id="prod_social_1", seller_id="s_tg", channel_id="ch_tg",
            external_sku="SKU-EXPIRED", url="https://t.me/example/2", price=399000.0,
            condition=Condition.NEW, city="Алматы", observed_at=NOW_ISO,
            expires_at=EXPIRED_ISO,
        ))

    def test_expired_offer_is_not_purchasable_and_not_best_price(self):
        response = self.search_core.search("iphone 15")
        self.assertEqual(len(response.items), 1)
        item = response.items[0]
        by_id = {offer.id: offer for offer in item.offers}
        self.assertTrue(by_id["off_fresh"].is_purchasable)
        self.assertFalse(by_id["off_expired"].is_purchasable)
        self.assertEqual(item.min_price, 499000.0)
        self.assertEqual(item.best_offer.id, "off_fresh")
        self.assertEqual(item.offers_count, 2)

    def test_offer_without_ttl_stays_purchasable(self):
        offer_repo = OfferRepository(self.conn)
        offer_repo.save_or_update(Offer(
            id="off_no_ttl", product_id="prod_social_1", seller_id="s_tg", channel_id="ch_tg",
            external_sku="SKU-NO-TTL", url="https://example.invalid/3", price=480000.0,
            condition=Condition.NEW, city="Алматы", observed_at=NOW_ISO,
        ))
        response = self.search_core.search("iphone 15")
        by_id = {offer.id: offer for offer in response.items[0].offers}
        self.assertTrue(by_id["off_no_ttl"].is_purchasable)
        # просроченный off_expired (399 000) исключён, значит лучшая цена — 480 000
        self.assertEqual(response.items[0].min_price, 480000.0)

    def test_ttl_with_timezone_offset_matches_python_and_sql(self):
        """A06: хронологическое сравнение TTL в SQL совпадает с Python при смещениях зон."""
        from repositories.content_repo import ContentObservationRepository
        from domain.social_content import ContentObservation
        repo = ContentObservationRepository(self.conn)
        # expires at 13:00 -05:00 = 18:00 UTC
        obs = ContentObservation(id="ttl1", seller_id="s_tg", channel_id="ch_tg",
                                 expires_at="2026-10-27T13:00:00-05:00")
        repo.save_observation(obs)
        # В 17:00 UTC (за 1 час до истечения): Python считает НЕ истёкшим
        self.assertFalse(obs.is_expired("2026-10-27T17:00:00+00:00"))
        # SQL find_by_seller обязан возвращать наблюдение
        found = repo.find_by_seller("s_tg", now_iso="2026-10-27T17:00:00+00:00")
        self.assertTrue(any(o.id == "ttl1" for o in found))
        # SQL purge НЕ должен его удалять
        purged = repo.purge_expired("2026-10-27T17:00:00+00:00")
        self.assertEqual(purged, 0)
        # В 19:00 UTC (через час после истечения): Python считает истёкшим
        self.assertTrue(obs.is_expired("2026-10-27T19:00:00+00:00"))
        # SQL purge обязан его удалить
        purged = repo.purge_expired("2026-10-27T19:00:00+00:00")
        self.assertEqual(purged, 1)


if __name__ == "__main__":
    unittest.main()
