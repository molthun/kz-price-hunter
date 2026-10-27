"""S2-E06: коннекторы Telegram/Instagram, реестр источников и сбор контента.

Проверяем без сети: коннекторам передаётся поддельный HTTP-ответ, сервису — поддельный
коннектор. Ошибки источников не должны бросаться наружу и не должны выглядеть пустым
каталогом; повторный сбор не плодит наблюдения.
"""
from tests import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import json
import sqlite3
import unittest

from domain.social_connector import (
    ConnectorRateLimited,
    ConnectorUnavailable,
    FetchedPost,
    InstagramConnector,
    SocialPlatform,
    SocialSource,
    TelegramChannelConnector,
    ContentType,
    build_connector,
    normalize_handle,
    observation_id,
    posts_to_observations,
    source_id,
)
from repositories import ContentObservationRepository, SocialSourceRepository, init_schema_v2
from social_ingest import IngestStatus, ingest_source

TG_HTML = """
<html><body>
  <div class="tgme_widget_message_wrap">
    <div class="tgme_widget_message" data-post="shopkz_test/42">
      <div class="tgme_widget_message_text">iPhone 15 256GB — 499 000 тг<br/>Алматы, доставка</div>
      <a class="tgme_widget_message_photo_wrap"
         style="background-image:url('https://cdn.example.invalid/a.jpg')"></a>
      <time datetime="2026-10-01T10:00:00+00:00"></time>
    </div>
  </div>
  <div class="tgme_widget_message_wrap">
    <div class="tgme_widget_message" data-post="shopkz_test/43">
      <div class="tgme_widget_message_text">Samsung S24 350 000 тг</div>
    </div>
  </div>
  <div class="tgme_widget_message_wrap">
    <div class="tgme_widget_message" data-post="shopkz_test/44">
      <div class="tgme_widget_message_text">  </div>
    </div>
  </div>
</body></html>
"""

IG_PAYLOAD = {
    "data": [
        {
            "id": "17900000000000001",
            "caption": "Pixel 8 Pro 128GB — 320 000 тг, Алматы",
            "media_url": "https://cdn.example.invalid/ig1.jpg",
            "media_type": "IMAGE",
            "timestamp": "2026-10-01T10:00:00+0000",
            "permalink": "https://www.instagram.com/p/abc/",
        },
        {
            "id": "17900000000000002",
            "caption": "Карусель: комплект",
            "media_type": "CAROUSEL_ALBUM",
            "timestamp": "2026-10-02T11:30:00+0000",
            "children": {"data": [
                {"media_url": "https://cdn.example.invalid/ig2a.jpg"},
                {"media_url": "https://cdn.example.invalid/ig2b.jpg"},
            ]},
        },
    ]
}


class FakeResponse:
    def __init__(self, status_code=200, text="", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}

    def json(self):
        return json.loads(self.text)


class FakeConnector:
    platform = SocialPlatform.TELEGRAM

    def __init__(self, posts=None, exc=None):
        self.posts = posts or []
        self.exc = exc
        self.calls = 0

    def fetch(self, handle):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.posts


def fetched(post_id="1", text="iPhone 15 256GB 499 000 тг", **kwargs) -> FetchedPost:
    params = dict(post_id=post_id, text=text, platform=SocialPlatform.TELEGRAM,
                  channel_handle="shopkz_test")
    params.update(kwargs)
    return FetchedPost(**params)


class HandleTest(unittest.TestCase):
    def test_handle_normalization(self):
        self.assertEqual(normalize_handle(SocialPlatform.TELEGRAM, "@Shop_KZ"), "shop_kz")
        self.assertEqual(normalize_handle(SocialPlatform.TELEGRAM, "https://t.me/s/shop_kz"), "shop_kz")
        self.assertEqual(normalize_handle(SocialPlatform.TELEGRAM, "t.me/shop_kz"), "shop_kz")
        self.assertEqual(normalize_handle(SocialPlatform.INSTAGRAM, "https://instagram.com/shop.kz"), "shop.kz")
        self.assertEqual(normalize_handle(SocialPlatform.INSTAGRAM, "17841400000000000"), "17841400000000000")

    def test_invalid_handle_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_handle(SocialPlatform.TELEGRAM, "плохой канал!")
        with self.assertRaises(ValueError):
            normalize_handle("unknown", "shop")

    def test_deterministic_ids(self):
        self.assertEqual(source_id("telegram", "shop"), source_id("telegram", "shop"))
        self.assertNotEqual(source_id("telegram", "shop"), source_id("instagram", "shop"))
        self.assertEqual(observation_id("telegram", "shop", "42"),
                         observation_id("telegram", "shop", "42"))
        self.assertNotEqual(observation_id("telegram", "shop", "42"),
                            observation_id("telegram", "shop", "43"))


class TelegramConnectorTest(unittest.TestCase):
    def test_parse_posts_text_media_and_time(self):
        posts = TelegramChannelConnector().parse_posts(TG_HTML, "shopkz_test")
        self.assertEqual([p.post_id for p in posts], ["42", "43"])  # пустое служебное пропущено
        first = posts[0]
        self.assertIn("iPhone 15", first.text)
        self.assertEqual(first.media_urls, ["https://cdn.example.invalid/a.jpg"])
        self.assertEqual(first.published_at, "2026-10-01T10:00:00+00:00")
        self.assertEqual(first.post_url, "https://t.me/shopkz_test/42")

    def test_fetch_uses_public_preview_and_normalizes_handle(self):
        requested = {}

        def http_get(url, **kwargs):
            requested["url"] = url
            return FakeResponse(200, TG_HTML)

        posts = TelegramChannelConnector(http_get=http_get).fetch("@ShopKZ_Test")
        self.assertIn("https://t.me/s/shopkz_test", requested["url"])
        self.assertEqual(len(posts), 2)

    def test_429_raises_rate_limited_with_retry_after(self):
        connector = TelegramChannelConnector(
            http_get=lambda url, **kwargs: FakeResponse(429, "", {"Retry-After": "120"}))
        with self.assertRaises(ConnectorRateLimited) as ctx:
            connector.fetch("shopkz_test")
        self.assertEqual(ctx.exception.retry_after, 120.0)

    def test_closed_channel_raises_unavailable(self):
        connector = TelegramChannelConnector(
            http_get=lambda url, **kwargs: FakeResponse(403, "forbidden"))
        with self.assertRaises(ConnectorUnavailable):
            connector.fetch("shopkz_test")

    def test_empty_page_is_not_an_error(self):
        connector = TelegramChannelConnector(http_get=lambda url, **kwargs: FakeResponse(200, ""))
        self.assertEqual(connector.fetch("shopkz_test"), [])


class InstagramConnectorTest(unittest.TestCase):
    def payload_response(self):
        return FakeResponse(200, json.dumps(IG_PAYLOAD))

    def test_parse_media_and_carousel(self):
        posts = InstagramConnector().parse_media(IG_PAYLOAD, "17841400000000000")
        self.assertEqual([p.post_id for p in posts], ["17900000000000001", "17900000000000002"])
        self.assertEqual(posts[0].published_at, "2026-10-01T10:00:00+00:00")
        self.assertEqual(posts[0].post_url, "https://www.instagram.com/p/abc/")
        self.assertEqual(len(posts[1].media_urls), 2)
        self.assertIsNone(posts[1].post_url)

    def test_instagram_media_without_permalink_stays_without_url(self):
        payload = {
            "data": [{
                "id": "17900000000000099",
                "caption": "Pixel 8 Pro 320 000 тг",
                "media_url": "https://cdn.example.invalid/ig99.jpg",
                "timestamp": "2026-10-01T10:00:00+0000",
            }]
        }
        posts = InstagramConnector().parse_media(payload, "17841400000000000")
        self.assertEqual(len(posts), 1)
        self.assertIsNone(posts[0].post_url)

    def test_parse_stories_marks_content_type(self):
        posts = InstagramConnector().parse_media(
            IG_PAYLOAD, "17841400000000000", content_type=ContentType.STORY)
        self.assertEqual(posts[0].content_type, ContentType.STORY)

    def test_fetch_requires_token(self):
        connector = InstagramConnector(access_token="", http_get=lambda url, **kwargs: self.payload_response())
        with self.assertRaises(ConnectorUnavailable):
            connector.fetch_media("17841400000000000")

    def test_fetch_requires_numeric_business_id(self):
        connector = InstagramConnector(access_token="TOKEN",
                                       http_get=lambda url, **kwargs: self.payload_response())
        with self.assertRaises(ConnectorUnavailable):
            connector.fetch_media("shop.kz")

    def test_fetch_media_with_token(self):
        requested = {}

        def http_get(url, **kwargs):
            requested["url"] = url
            return self.payload_response()

        posts = InstagramConnector(access_token="TOKEN", http_get=http_get).fetch_media(
            "17841400000000000")
        self.assertEqual(len(posts), 2)
        self.assertIn("/17841400000000000/media", requested["url"])

    def test_error_text_does_not_leak_token(self):
        connector = InstagramConnector(
            access_token="SECRET_TOKEN_123",
            http_get=lambda url, **kwargs: FakeResponse(403, ""))
        with self.assertRaises(ConnectorUnavailable) as ctx:
            connector.fetch_media("17841400000000000")
        self.assertNotIn("SECRET_TOKEN_123", str(ctx.exception))


class ObservationMappingTest(unittest.TestCase):
    def source(self, **kwargs) -> SocialSource:
        params = dict(id="src_tg_test", platform=SocialPlatform.TELEGRAM,
                      handle="shopkz_test", seller_id="seller_1", channel_id="ch_1")
        params.update(kwargs)
        return SocialSource(**params)

    def test_posts_become_observations_with_deterministic_ids(self):
        posts = [fetched("42"), fetched("43")]
        first = posts_to_observations(posts, self.source())
        second = posts_to_observations(posts, self.source())
        self.assertEqual([o.id for o in first], [o.id for o in second])
        self.assertEqual(len({o.id for o in first}), 2)
        self.assertEqual(first[0].raw_metadata["platform"], "telegram")
        self.assertEqual(first[0].raw_metadata["post_url"], "https://t.me/shopkz_test/42")
        self.assertEqual(first[0].seller_id, "seller_1")

    def test_instagram_without_permalink_does_not_get_telegram_url(self):
        post = fetched(
            "17900000000000099",
            platform=SocialPlatform.INSTAGRAM,
            channel_handle="17841400000000000",
            post_url=None,
        )
        source = self.source(
            id="src_ig_test",
            platform=SocialPlatform.INSTAGRAM,
            handle="17841400000000000",
        )
        observation = posts_to_observations([post], source)[0]
        self.assertEqual(observation.raw_metadata["platform"], "instagram")
        self.assertEqual(observation.raw_metadata["post_url"], "")
        self.assertNotIn("t.me", observation.raw_metadata["post_url"])

    def test_story_observation_gets_ttl(self):
        post = fetched("story-1", content_type=ContentType.STORY)
        observation = posts_to_observations([post], self.source())[0]
        self.assertIsNotNone(observation.expires_at)
        self.assertFalse(observation.is_expired())  # только что собранная сторис ещё жива
        self.assertTrue(observation.is_expired("2099-01-01T00:00:00+00:00"))

    def test_build_connector_by_platform(self):
        self.assertIsInstance(build_connector(SocialPlatform.TELEGRAM), TelegramChannelConnector)
        self.assertIsInstance(build_connector(SocialPlatform.INSTAGRAM), InstagramConnector)
        with self.assertRaises(ValueError):
            build_connector("unknown")


class SocialSourceRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_schema_v2(self.conn)
        self.repo = SocialSourceRepository(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_add_source_is_idempotent(self):
        first = self.repo.add_source("telegram", "@ShopKZ_Test")
        second = self.repo.add_source("telegram", "https://t.me/s/shopkz_test")
        self.assertEqual(first.id, second.id)
        self.assertEqual(len(self.repo.list_sources()), 1)
        self.assertEqual(first.handle, "shopkz_test")

    def test_enabled_filter_and_toggle(self):
        source = self.repo.add_source("telegram", "shopkz_test")
        self.repo.add_source("instagram", "17841400000000000", enabled=False)
        self.assertEqual(len(self.repo.list_sources()), 2)
        self.assertEqual([s.id for s in self.repo.list_sources(enabled_only=True)], [source.id])
        self.repo.set_enabled(source.id, False)
        self.assertEqual(self.repo.list_sources(enabled_only=True), [])

    def test_mark_fetch_persists_status_and_error(self):
        source = self.repo.add_source("telegram", "shopkz_test")
        updated = self.repo.mark_fetch(source.id, IngestStatus.ERROR, "UNAVAILABLE: HTTP 403")
        loaded = self.repo.get_by_id(source.id)
        self.assertEqual(updated.last_status, IngestStatus.ERROR)
        self.assertIn("HTTP 403", loaded.last_error)
        self.assertIsNotNone(loaded.last_fetch_at)

    def test_get_by_handle(self):
        source = self.repo.add_source("telegram", "shopkz_test")
        self.assertEqual(self.repo.get_by_handle("telegram", "@ShopKZ_Test").id, source.id)
        self.assertIsNone(self.repo.get_by_handle("telegram", "other"))


class IngestServiceTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_schema_v2(self.conn)
        self.sources = SocialSourceRepository(self.conn)
        self.source = self.sources.add_source(
            "telegram", "shopkz_test", seller_id="seller_1", channel_id="ch_1")
        self.observations = ContentObservationRepository(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_success_saves_observations_and_candidates(self):
        connector = FakeConnector([fetched("1"), fetched("2", text="Samsung S24 350 000 тг")])
        result = ingest_source(self.source, connector=connector, conn=self.conn)
        self.assertEqual(result.status, IngestStatus.OK)
        self.assertEqual(result.posts, 2)
        self.assertEqual(result.observations, 2)
        self.assertGreaterEqual(result.candidates, 2)
        stored = self.observations.find_by_seller("seller_1", include_expired=True)
        self.assertEqual(len(stored), 2)
        self.assertEqual(self.sources.get_by_id(self.source.id).last_status, IngestStatus.OK)

    def test_empty_source_is_empty_not_ok(self):
        result = ingest_source(self.source, connector=FakeConnector([]), conn=self.conn)
        self.assertEqual(result.status, IngestStatus.EMPTY)
        self.assertEqual(self.sources.get_by_id(self.source.id).last_status, IngestStatus.EMPTY)

    def test_rate_limit_is_recorded_not_raised(self):
        connector = FakeConnector(exc=ConnectorRateLimited("Telegram: 429", 60))
        result = ingest_source(self.source, connector=connector, conn=self.conn)
        self.assertEqual(result.status, IngestStatus.RATE_LIMITED)
        self.assertEqual(self.sources.get_by_id(self.source.id).last_status, IngestStatus.RATE_LIMITED)

    def test_unavailable_source_is_error_with_message(self):
        connector = FakeConnector(exc=ConnectorUnavailable("Telegram: канал недоступен (HTTP 403)"))
        result = ingest_source(self.source, connector=connector, conn=self.conn)
        self.assertEqual(result.status, IngestStatus.ERROR)
        self.assertIn("HTTP 403", result.error)
        self.assertEqual(self.sources.get_by_id(self.source.id).last_status, IngestStatus.ERROR)

    def test_disabled_source_is_skipped(self):
        self.sources.set_enabled(self.source.id, False)
        source = self.sources.get_by_id(self.source.id)
        connector = FakeConnector([fetched("1")])
        result = ingest_source(source, connector=connector, conn=self.conn)
        self.assertEqual(result.status, IngestStatus.DISABLED)
        self.assertEqual(connector.calls, 0)

    def test_repeated_ingest_updates_instead_of_duplicating(self):
        connector = FakeConnector([fetched("1")])
        ingest_source(self.source, connector=connector, conn=self.conn)
        ingest_source(self.source, connector=connector, conn=self.conn)
        stored = self.observations.find_by_seller("seller_1", include_expired=True)
        self.assertEqual(len(stored), 1)
        self.assertEqual(connector.calls, 2)


if __name__ == "__main__":
    unittest.main()
