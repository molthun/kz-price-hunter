from tests import test_support  # noqa: F401
import asyncio
import base64
import pathlib
import sqlite3
import unittest
from unittest.mock import AsyncMock, patch

import aiohttp
from aiohttp.test_utils import TestClient, TestServer

from domain.models import ChannelType
from domain.seller_admin import save_seller
from domain.seller_vision_upload import (
    MAX_VISION_UPLOAD_BYTES,
    VisionUploadError,
    detect_image_mime,
    extract_seller_price_image,
    publish_seller_vision_items,
    validate_image_payload,
)
from repositories.channel_repo import ChannelRepository
from repositories.offer_repo import OfferRepository
from repositories.schema_v2 import init_schema_v2


FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
FAKE_WEBP = b"RIFF\x24\x00\x00\x00WEBP" + b"\x00" * 32


class SellerVisionUploadDomainTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_schema_v2(self.conn)
        self.addCleanup(self.conn.close)

    def _create_mobilnik(self):
        seller = save_seller(self.conn, None, {
            "name": "МОБИЛЬНИК-магазин сотовых",
            "city": "Астана",
            "instagram": "@mobilnik_astana",
            "whatsapp": "+7 777 555 4433",
        })
        ChannelRepository(self.conn).get_or_create(
            seller_id=seller["id"],
            channel_type=ChannelType.WHATSAPP,
            name="WhatsApp: МОБИЛЬНИК",
            base_url="https://wa.me/77775554433",
        )
        self.conn.commit()
        return seller

    def test_validate_image_payload_checks_magic_bytes_and_size(self):
        self.assertEqual(detect_image_mime(FAKE_JPEG), "image/jpeg")
        self.assertEqual(detect_image_mime(FAKE_PNG), "image/png")
        self.assertEqual(detect_image_mime(FAKE_WEBP), "image/webp")
        self.assertEqual(validate_image_payload(FAKE_JPEG), "image/jpeg")

        with self.assertRaises(VisionUploadError):
            validate_image_payload(b"")
        with self.assertRaises(VisionUploadError):
            validate_image_payload(b"<html>not an image</html>")
        with self.assertRaises(VisionUploadError):
            validate_image_payload(b"\xff\xd8\xff" + b"0" * MAX_VISION_UPLOAD_BYTES)

    def test_extract_splits_publishable_and_rejected_without_db_writes(self):
        mock_routed = {
            "result": {
                "items": [
                    {
                        "title": "Apple iPhone 16 Pro 256GB Desert Titanium",
                        "price": 629000,
                        "old_price": 679000,
                        "currency": "KZT",
                        "is_price_occluded": False,
                        "confidence": 0.94,
                        "raw_snippet": "iPhone 16 Pro 256GB 629000 ₸",
                    },
                    {
                        "title": "Samsung Galaxy S24 Ultra 512GB",
                        "price": None,
                        "currency": "KZT",
                        "is_price_occluded": True,
                        "confidence": 0.88,
                    },
                    {
                        "title": "AirPods Pro 2",
                        "price": 18000,
                        "currency": "RUB",
                        "is_price_occluded": False,
                        "confidence": 0.91,
                    },
                    {
                        "title": "Xiaomi 14 256GB",
                        "price": 320000,
                        "currency": "KZT",
                        "is_price_occluded": False,
                        "confidence": 0.55,
                    },
                ]
            }
        }
        with patch("ai_router.run", new=AsyncMock(return_value=mock_routed)):
            res = asyncio.run(extract_seller_price_image(FAKE_PNG, default_city="Астана"))

        self.assertEqual(res["mime_type"], "image/png")
        self.assertEqual(res["city"], "Астана")
        self.assertEqual(res["total"], 4)
        self.assertEqual(len(res["publishable"]), 1)
        self.assertEqual(len(res["rejected"]), 3)
        self.assertEqual(res["publishable"][0]["raw_title"], "Apple iPhone 16 Pro 256GB Desert Titanium")
        self.assertEqual(res["publishable"][0]["price"], 629000.0)
        self.assertTrue(all(item.get("rejection_label") for item in res["rejected"]))
        # Предпросмотр не должен писать в БД
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM offers").fetchone()[0], 0)

    def test_publish_prefers_whatsapp_url_and_revalidates_on_server(self):
        seller = self._create_mobilnik()
        items = [
            {
                "raw_title": "Apple iPhone 16 Pro 256GB Desert Titanium",
                "price": 629000,
                "old_price": 679000,
                "currency": "KZT",
                "confidence": 0.95,
            },
            # Попытка протащить рубли или низкую уверенность напрямую в publish
            {
                "raw_title": "Подделка RUB",
                "price": 15000,
                "currency": "RUB",
                "confidence": 0.99,
            },
            {
                "raw_title": "Сомнительная цена",
                "price": 120000,
                "currency": "KZT",
                "confidence": 0.40,
            },
        ]
        res = publish_seller_vision_items(self.conn, seller["id"], items)
        self.assertEqual(res["published_count"], 1)
        self.assertEqual(res["skipped_count"], 2)
        self.assertEqual(res["order_url"], "https://wa.me/77775554433")
        self.assertEqual(res["city"], "Астана")

        rows = self.conn.execute(
            "SELECT id, price, old_price, url, city FROM offers WHERE seller_id = ?",
            (seller["id"],),
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["price"], 629000.0)
        self.assertEqual(rows[0]["old_price"], 679000.0)
        self.assertEqual(rows[0]["url"], "https://wa.me/77775554433")
        self.assertEqual(rows[0]["city"], "Астана")

        hist_count = self.conn.execute(
            "SELECT COUNT(*) FROM offer_price_history WHERE offer_id = ?",
            (rows[0]["id"],),
        ).fetchone()[0]
        self.assertEqual(hist_count, 1)

    def test_publish_rejects_foreign_channel(self):
        s1 = self._create_mobilnik()
        s2 = save_seller(self.conn, None, {"name": "Другой магазин", "phone": "+77012223344"})
        ch2 = ChannelRepository(self.conn).get_or_create(
            seller_id=s2["id"],
            channel_type=ChannelType.INSTAGRAM,
            name="Other IG",
            base_url="https://instagram.com/other",
        )
        self.conn.commit()
        with self.assertRaises(VisionUploadError):
            publish_seller_vision_items(
                self.conn,
                s1["id"],
                [{"raw_title": "iPhone 15 128GB", "price": 410000, "currency": "KZT", "confidence": 0.9}],
                channel_id=ch2.id,
            )


class SellerVisionUploadHttpTest(unittest.TestCase):
    def test_admin_http_endpoints_and_auth(self):
        import auth
        import database
        from tests.test_support import BrowserTestClient
        from web.server import create_app

        database.init_db()
        uid = 8299
        database.upsert_telegram_user({"id": uid, "first_name": "VisionAdmin"})
        token = database.create_session(uid)

        conn = database.get_connection()
        try:
            seller = save_seller(conn, None, {
                "name": "HTTP Мобильник Тест",
                "city": "Астана",
                "instagram": "@http_mobilnik",
                "whatsapp": "+7 700 123 4567",
            })
            seller_id = seller["id"]
        finally:
            conn.close()

        async def _scenario():
            app = create_app()
            app.cleanup_ctx.clear()
            with patch.object(auth, "ADMIN_TELEGRAM_IDS", {uid}), patch.object(auth, "PUBLIC_ORIGIN", ""):
                async with BrowserTestClient(TestServer(app)) as client:
                    # 1. Гость не имеет доступа
                    r_unauth = await client.post(
                        f"/api/admin/sellers/{seller_id}/vision-extract",
                        json={"image_base64": base64.b64encode(FAKE_JPEG).decode("ascii")},
                    )
                    self.assertIn(r_unauth.status, (401, 403))

                    # 2. Авторизованный администратор (реальная сессия)
                    client.session.cookie_jar.update_cookies({auth.SESSION_COOKIE: token})
                    mock_routed = {
                        "result": {
                            "items": [
                                {
                                    "title": "Samsung Galaxy A55 256GB",
                                    "price": 189990,
                                    "currency": "KZT",
                                    "is_price_occluded": False,
                                    "confidence": 0.92,
                                }
                            ]
                        }
                    }
                    with patch("ai_router.run", new=AsyncMock(return_value=mock_routed)):
                        # Несуществующий магазин -> 404
                        r_404 = await client.post(
                            "/api/admin/sellers/seller_missing_999/vision-extract",
                            json={"image_base64": base64.b64encode(FAKE_JPEG).decode("ascii")},
                        )
                        self.assertEqual(r_404.status, 404)

                        # Multipart upload -> 200
                        form = aiohttp.FormData()
                        form.add_field("image", FAKE_JPEG, filename="price.jpg", content_type="image/jpeg")
                        form.add_field("city", "Астана")
                        r_ext = await client.post(
                            f"/api/admin/sellers/{seller_id}/vision-extract",
                            data=form,
                        )
                        self.assertEqual(r_ext.status, 200)
                        ext_data = await r_ext.json()
                        self.assertTrue(ext_data["ok"])
                        self.assertEqual(len(ext_data["publishable"]), 1)

                        # Публикация -> 200
                        r_pub = await client.post(
                            f"/api/admin/sellers/{seller_id}/vision-publish",
                            json={"items": ext_data["publishable"], "city": "Астана"},
                        )
                        self.assertEqual(r_pub.status, 200)
                        pub_data = await r_pub.json()
                        self.assertTrue(pub_data["ok"])
                        self.assertEqual(pub_data["published_count"], 1)
                        self.assertEqual(pub_data["order_url"], "https://wa.me/77001234567")

        asyncio.run(_scenario())

    def test_admin_template_contains_vision_upload_controls(self):
        html = pathlib.Path("web/templates/admin.html").read_text(encoding="utf-8")
        for token in (
            'id="shopVisionSection"',
            'id="shopVisionFile"',
            'id="shopVisionChannel"',
            'id="shopVisionExtractBtn"',
            'id="shopVisionPublishBtn"',
            '/vision-extract',
            '/vision-publish',
        ):
            self.assertIn(token, html)


if __name__ == "__main__":
    unittest.main()
