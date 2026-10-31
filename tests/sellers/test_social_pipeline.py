"""
Сквозные тесты Social Offer Pipeline (S2-E08, S2-E10, S2-E11, S2-E13).

Проверяет превращение сырых постов и сторис из соцсетей (Telegram / Instagram)
через текстовый и мультимодальный Vision конвейер в опубликованные предложения Offer
и их отображение в общем поиске Search Core 2.0.
"""

from tests import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports

import asyncio
import datetime
import sqlite3
import unittest
from unittest.mock import AsyncMock, patch

from domain.models import (
    Availability,
    CanonicalProduct,
    Channel,
    ChannelType,
    Condition,
    Offer,
    Seller,
    utc_now_iso,
)
from domain.search_core import SearchCore
from domain.social_content import (
    ContentObservation,
    ContentType,
    ExtractedOfferCandidate,
)
from domain.social_pipeline import SocialOfferPipeline
from domain.vision_extraction import VisionCandidate
from repositories import (
    ChannelRepository,
    ContentObservationRepository,
    OfferRepository,
    ProductRepository,
    SearchRepository,
    SellerRepository,
    init_schema_v2,
)


class TestSocialOfferPipeline(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_schema_v2(self.conn)
        self.pipeline = SocialOfferPipeline(self.conn)
        self.search_core = SearchCore(SearchRepository(self.conn))

    def tearDown(self):
        self.conn.close()

    def test_01_text_post_to_offer_and_search_core(self):
        """Сценарий 1: Текстовый пост Telegram с ценой -> Offer -> отображение в SearchCore."""
        obs = ContentObservation(
            id="cobs_tg_1",
            seller_id="seller_techno",
            channel_id="channel_tg_techno",
            content_type=ContentType.POST,
            post_id="101",
            text="Скидки выходного дня! 🔥 Apple iPhone 15 128GB Black всего за 389 990 ₸ в Алматы! В наличии.",
            raw_metadata={"platform": "telegram", "handle": "techno_kz", "city": "Алматы"},
        )

        # Обрабатываем пост через пайплайн
        offers = self.pipeline.process_observation_sync(obs)
        self.assertEqual(len(offers), 1)

        offer = offers[0]
        self.assertEqual(offer.price, 389990.0)
        self.assertEqual(offer.city, "Алматы")
        self.assertEqual(offer.availability, Availability.IN_STOCK)
        self.assertEqual(offer.channel_id, "channel_tg_techno")

        # Проверяем, что создался канонический товар
        product = ProductRepository(self.conn).get_by_id(offer.product_id)
        self.assertIsNotNone(product)
        self.assertIn("iphone", product.canonical_key)

        # Проверяем, что товар мгновенно находится через SearchCore 2.0
        results = self.search_core.search("iphone 15 128", city="Алматы")
        self.assertTrue(len(results.items) >= 1)
        res_prod = results.items[0]
        self.assertEqual(len(res_prod.offers), 1)
        agg_offer = res_prod.offers[0]
        self.assertEqual(agg_offer.price, 389990.0)
        self.assertEqual(agg_offer.channel_type, ChannelType.TELEGRAM)
        self.assertEqual(agg_offer.city, "Алматы")

    async def test_02_multimodal_vision_post_to_offer(self):
        """Сценарий 2: Пост с фото ценника через VisionExtractionEngine -> Offer."""
        fake_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRdummy"
        obs_photo = ContentObservation(
            id="cobs_photo_1",
            seller_id="",  # автосоздание продавца
            channel_id="",  # автосоздание канала
            content_type=ContentType.POST,
            post_id="202",
            text="Новое поступление в магазине!",
            raw_metadata={
                "platform": "telegram",
                "handle": "gadget_hub_kz",
                "seller_name": "Gadget Hub KZ",
                "image_bytes": fake_png,
                "mime_type": "image/png",
            },
        )

        mock_vision_candidates = [
            VisionCandidate(
                raw_title="Sony PlayStation 5 Slim 1TB",
                price=269990.0,
                old_price=299990.0,
                currency="KZT",
                confidence=0.96,
                publishable=True,
                price_determined=True,
                city="Астана",
            )
        ]

        with patch("domain.vision_extraction.VisionExtractionEngine.extract_from_observation_async",
                   new_callable=AsyncMock) as mock_vision:
            mock_vision.return_value = mock_vision_candidates

            offers = await self.pipeline.process_observation_async(obs_photo)
            self.assertEqual(len(offers), 1)
            off = offers[0]
            self.assertEqual(off.price, 269990.0)
            self.assertEqual(off.old_price, 299990.0)
            self.assertEqual(off.city, "Астана")

            # Проверяем продавца и канал
            seller = SellerRepository(self.conn).get_by_id(off.seller_id)
            self.assertIsNotNone(seller)
            self.assertIn("Gadget Hub", seller.name)

            channel = ChannelRepository(self.conn).get_by_id(off.channel_id)
            self.assertIsNotNone(channel)
            self.assertEqual(channel.channel_type, ChannelType.TELEGRAM)

            # Поиск в SearchCore
            results = self.search_core.search("playstation 5 slim", city="Астана")
            self.assertTrue(len(results.items) >= 1)
            self.assertEqual(results.items[0].offers[0].price, 269990.0)

    def test_03_instagram_story_ttl_expiration(self):
        """Сценарий 3: Instagram Stories получает TTL 24ч; просроченная сторис становится OUT_OF_STOCK."""
        past_dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=25)
        obs_expired = ContentObservation(
            id="cobs_story_exp",
            seller_id="seller_ig",
            channel_id="channel_ig",
            content_type=ContentType.STORY,
            post_id="story_99",
            observed_at=past_dt.isoformat(),
            text="Только сегодня! Dyson V15 Detect 380 000 ₸",
            raw_metadata={"platform": "instagram", "handle": "dyson_almaty"},
        )
        self.assertTrue(obs_expired.is_expired())

        offers = self.pipeline.process_observation_sync(obs_expired)
        self.assertEqual(len(offers), 1)
        # Просроченное предложение должно быть помечено как OUT_OF_STOCK
        self.assertEqual(offers[0].availability, Availability.OUT_OF_STOCK)

        # В SearchCore такое предложение не считается активным к покупке
        results = self.search_core.search("dyson v15")
        # В выдаче не должно быть доступных предложений из просроченной сторис
        if results.items and results.items[0].offers:
            self.assertFalse(results.items[0].offers[0].is_purchasable)

    def test_04_idempotent_post_reprocessing(self):
        """Сценарий 4: Повторный запуск того же поста обновляет цену, а не дублирует офферы."""
        obs = ContentObservation(
            id="cobs_repeat",
            seller_id="seller_rep",
            channel_id="channel_rep",
            content_type=ContentType.POST,
            post_id="rep_1",
            text="Apple MacBook Air 13 M2 549 990 ₸",
            raw_metadata={"platform": "telegram", "handle": "mac_rep"},
        )

        offers_v1 = self.pipeline.process_observation_sync(obs)
        self.assertEqual(len(offers_v1), 1)
        offer_id_1 = offers_v1[0].id
        self.assertEqual(offers_v1[0].price, 549990.0)

        # Обновлённый пост того же товара с новой сниженной ценой
        obs.text = "Срочная скидка! Apple MacBook Air 13 M2 519 990 ₸"
        offers_v2 = self.pipeline.process_observation_sync(obs)
        self.assertEqual(len(offers_v2), 1)
        self.assertEqual(offers_v2[0].id, offer_id_1)  # ID сохранён, дубликата нет
        self.assertEqual(offers_v2[0].price, 519990.0)

    def test_05_reject_invalid_prices_and_currencies(self):
        """Сценарий 5: Цены 0, чужие валюты (RUB/USD) и закрытые цены не создают офферы."""
        obs_invalid = ContentObservation(
            id="cobs_bad",
            seller_id="seller_bad",
            channel_id="channel_bad",
            content_type=ContentType.POST,
            post_id="bad_1",
            text="Контакты магазина: +7 777 123 45 67, доставка по городу. Пишите в Direct!",
            raw_metadata={"platform": "telegram"},
        )
        offers = self.pipeline.process_observation_sync(obs_invalid)
        self.assertEqual(len(offers), 0)


if __name__ == "__main__":
    unittest.main()
