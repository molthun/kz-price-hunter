from tests import test_support  # noqa: F401
import json
import tempfile
import unittest
from pathlib import Path

import database
from domain.models import CanonicalProduct, Offer
from repositories.offer_repo import OfferRepository
from repositories.product_repo import ProductRepository


class AdminManualMergeCardsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_db_path = database.DB_PATH
        database.DB_PATH = Path(self._tmp.name) / "test_admin_merge.db"
        self.addCleanup(self._restore_db)
        database.init_db()

    def _restore_db(self):
        database.DB_PATH = self._orig_db_path
        self._tmp.cleanup()

    def test_disputed_pairs_enriched_with_v2_photos_description_attributes_and_offers(self):
        title_a = "Смартфон Apple iPhone 15 128GB Black"
        title_b = "Apple iPhone 15 128 ГБ Черный"

        conn = database.get_connection()
        try:
            conn.execute(
                "INSERT INTO sellers (id, name, slug, rating, domain, is_active, metadata_json, created_at, updated_at) "
                "VALUES ('s_tech', 'Technodom', 'technodom', 4.8, 'technodom.kz', 1, '{}', '2026-11-28', '2026-11-28')"
            )
            conn.execute(
                "INSERT INTO sellers (id, name, slug, rating, domain, is_active, metadata_json, created_at, updated_at) "
                "VALUES ('s_mechta', 'Mechta', 'mechta', 4.7, 'mechta.kz', 1, '{}', '2026-11-28', '2026-11-28')"
            )
            conn.execute(
                "INSERT INTO channels (id, seller_id, channel_type, name, created_at) "
                "VALUES ('ch_tech', 's_tech', 'website', 'Technodom', '2026-11-28')"
            )
            conn.execute(
                "INSERT INTO channels (id, seller_id, channel_type, name, created_at) "
                "VALUES ('ch_mechta', 's_mechta', 'website', 'Mechta', '2026-11-28')"
            )
            pr = ProductRepository(conn)
            orf = OfferRepository(conn)

            pr.save_or_update(CanonicalProduct(
                id="prod_a",
                canonical_key="apple_iphone_15_128_black_a",
                title=title_a,
                category="phones",
                brand="Apple",
                model="iPhone 15",
                image_url="https://cdn.technodom.kz/iphone15-main.jpg",
                attributes={"storage": "128GB", "color": "Black", "nfc": True},
            ))
            conn.execute(
                "UPDATE canonical_products SET description = ? WHERE id = 'prod_a'",
                ("Смартфон с экраном 6.1 OLED Super Retina XDR и чипом A16 Bionic",),
            )
            orf.save_or_update(Offer(
                id="off_a",
                product_id="prod_a",
                seller_id="s_tech",
                channel_id="ch_tech",
                external_sku="sku_a",
                url="https://technodom.kz/p/iphone-15-black",
                image_url="https://cdn.technodom.kz/iphone15-alt.jpg",
                price=389990.0,
                old_price=419990.0,
                condition="NEW",
                city="Алматы",
                raw_payload={"description": "Смартфон с экраном 6.1 OLED Super Retina XDR и чипом A16 Bionic"},
            ))

            # У второго товара описание лежит только в raw_payload_json предложения, а фото — на предложении
            pr.save_or_update(CanonicalProduct(
                id="prod_b",
                canonical_key="apple_iphone_15_128_black_b",
                title=title_b,
                category="phones",
                brand="Apple",
                model="iPhone 15",
                description="",
                image_url="javascript:alert(1)",  # небезопасный URL должен отфильтроваться
                attributes={"storage": "128GB"},
            ))
            orf.save_or_update(Offer(
                id="off_b",
                product_id="prod_b",
                seller_id="s_mechta",
                channel_id="ch_mechta",
                external_sku="sku_b",
                url="https://mechta.kz/product/iphone-15-128",
                image_url="https://cdn.mechta.kz/iphone15.jpg",
                price=384990.0,
                condition="NEW",
                city="Астана",
            ))
            conn.execute(
                "UPDATE offers SET raw_payload_json = ? WHERE id = 'off_b'",
                (json.dumps({"short_description": "OLED 6.1 дюйма, USB-C, 128 ГБ"}, ensure_ascii=False),),
            )
            conn.commit()
        finally:
            conn.close()

        enqueued = database.enqueue_disputed_pair(title_a, title_b, "manual_test", force=True)
        self.assertTrue(enqueued)

        data = database.get_disputed_product_pairs(status="pending", limit=20, offset=0)
        self.assertEqual(len(data["items"]), 1)
        item = data["items"][0]

        # enqueue_disputed_pair канонически сортирует (title_a, title_b)
        by_title = {
            item["title_a"]: (item["card_a"], item["offers_a"]),
            item["title_b"]: (item["card_b"], item["offers_b"]),
        }
        card_a, offers_a = by_title[title_a]
        card_b, offers_b = by_title[title_b]
        self.assertIsNotNone(card_a)
        self.assertIsNotNone(card_b)

        # Карточка A: фото + доп. фото, описание, бренд/модель/атрибуты, предложение
        self.assertEqual(card_a["image_url"], "https://cdn.technodom.kz/iphone15-main.jpg")
        self.assertEqual(
            card_a["images"],
            ["https://cdn.technodom.kz/iphone15-main.jpg", "https://cdn.technodom.kz/iphone15-alt.jpg"],
        )
        self.assertIn("Super Retina XDR", card_a["description"])
        self.assertEqual(card_a["brand"], "Apple")
        self.assertEqual(card_a["model"], "iPhone 15")
        self.assertEqual(card_a["attributes"]["storage"], "128GB")
        self.assertEqual(card_a["offers_count"], 1)
        self.assertEqual(card_a["offers"][0]["shop"], "Technodom")
        self.assertEqual(card_a["offers"][0]["city"], "Алматы")
        self.assertEqual(card_a["offers"][0]["url"], "https://technodom.kz/p/iphone-15-black")
        # offers также заполнен из v2 при пустой legacy products
        self.assertEqual(len(offers_a), 1)
        self.assertEqual(len(offers_b), 1)

        # Карточка B: javascript: отброшен, фото взято из offers, описание — из raw_payload_json
        self.assertEqual(card_b["image_url"], "https://cdn.mechta.kz/iphone15.jpg")
        self.assertEqual(card_b["description"], "OLED 6.1 дюйма, USB-C, 128 ГБ")
        self.assertEqual(card_b["offers"][0]["shop"], "Mechta")
        self.assertEqual(card_b["offers"][0]["current_price"], 384990.0)

    def test_legacy_products_fallback_when_no_v2_canonical_exists(self):
        title_a = "Пылесос Dyson V15 Detect"
        title_b = "Dyson V15 Detect Absolute"

        conn = database.get_connection()
        try:
            conn.execute(
                "INSERT INTO products (id, url, shop, category, title, first_seen_price, current_price, old_price_on_site, min_price, max_price, image_url, description) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("legacy_dyson", "https://shop.kz/dyson-v15", "Sulpak", "vacuums", title_a, 349000, 349000, 399000, 349000, 399000, "https://shop.kz/dyson.jpg", "Беспроводной пылесос с лазерной подсветкой"),
            )
            conn.commit()
        finally:
            conn.close()

        database.enqueue_disputed_pair(title_a, title_b, "legacy_fallback", force=True)
        data = database.get_disputed_product_pairs(status="pending", limit=20, offset=0)
        self.assertEqual(len(data["items"]), 1)
        item = data["items"][0]
        card_a = item["card_a"] if item["title_a"] == title_a else item["card_b"]
        self.assertEqual(card_a["image_url"], "https://shop.kz/dyson.jpg")
        self.assertEqual(card_a["category"], "vacuums")
        self.assertIn("лазерной подсветкой", card_a["description"])
        self.assertEqual(card_a["min_price"], 349000.0)
        self.assertEqual(card_a["offers"][0]["shop"], "Sulpak")


if __name__ == "__main__":
    unittest.main()
