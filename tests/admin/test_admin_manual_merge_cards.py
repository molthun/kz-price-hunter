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

    def test_mm04_same_title_different_canonical_keys_binds_strictly_by_canonical_key(self):
        """Регрессия MM-04: при двух канонических записях с одинаковым заголовком выбирается запись по canonical_key,
        даже если у чужой записи более свежий updated_at."""
        title_a = "Apple iPhone 15 Black"
        title_b = "iPhone 15 128GB Black"

        conn = database.get_connection()
        try:
            conn.execute(
                "INSERT INTO sellers (id, name, slug, rating, domain, is_active, metadata_json, created_at, updated_at) "
                "VALUES ('s_right', 'Technodom', 'technodom', 4.8, 'technodom.kz', 1, '{}', '2026-11-28', '2026-11-28')"
            )
            conn.execute(
                "INSERT INTO sellers (id, name, slug, rating, domain, is_active, metadata_json, created_at, updated_at) "
                "VALUES ('s_wrong', 'OtherShop', 'othershop', 4.5, 'other.kz', 1, '{}', '2026-11-28', '2026-11-28')"
            )
            conn.execute(
                "INSERT INTO channels (id, seller_id, channel_type, name, created_at) "
                "VALUES ('ch_right', 's_right', 'website', 'Technodom', '2026-11-28')"
            )
            conn.execute(
                "INSERT INTO channels (id, seller_id, channel_type, name, created_at) "
                "VALUES ('ch_wrong', 's_wrong', 'website', 'OtherShop', '2026-11-28')"
            )
            pr = ProductRepository(conn)
            orf = OfferRepository(conn)

            # Правильная карточка с нужным ключом (более старый updated_at)
            pr.save_or_update(CanonicalProduct(
                id="prod_right",
                canonical_key="apple_iphone_15_128_black_a",
                title=title_a,
                category="phones",
                brand="Apple",
                model="iPhone 15",
                image_url="https://cdn.technodom.kz/right-128.jpg",
                attributes={"storage": "128GB"},
            ))
            conn.execute(
                "UPDATE canonical_products SET updated_at = '2026-11-01T00:00:00Z' WHERE id = 'prod_right'"
            )
            orf.save_or_update(Offer(
                id="off_right",
                product_id="prod_right",
                seller_id="s_right",
                channel_id="ch_right",
                external_sku="sku_right",
                url="https://technodom.kz/right-128",
                price=380000.0,
                condition="NEW",
                city="Алматы",
            ))

            # Чужая карточка с ТЕМ ЖЕ заголовком, другим ключом и БОЛЕЕ СВЕЖИМ updated_at
            pr.save_or_update(CanonicalProduct(
                id="prod_wrong",
                canonical_key="apple_iphone_15_256_black_wrong",
                title=title_a,
                category="phones",
                brand="Apple",
                model="iPhone 15 256GB",
                image_url="https://cdn.other.kz/wrong-256.jpg",
                attributes={"storage": "256GB"},
            ))
            conn.execute(
                "UPDATE canonical_products SET updated_at = '2026-11-28T23:59:59Z' WHERE id = 'prod_wrong'"
            )
            orf.save_or_update(Offer(
                id="off_wrong",
                product_id="prod_wrong",
                seller_id="s_wrong",
                channel_id="ch_wrong",
                external_sku="sku_wrong",
                url="https://other.kz/wrong-256",
                price=499000.0,
                condition="NEW",
                city="Астана",
            ))

            # Сохраняем кэш ключа для title_a -> apple_iphone_15_128_black_a
            conn.commit()
        finally:
            conn.close()

        database.save_cached_canonical_key(title_a, "apple_iphone_15_128_black_a")
        database.enqueue_disputed_pair(title_a, title_b, "mm04_test", force=True)

        data = database.get_disputed_product_pairs(status="pending", limit=20, offset=0)
        self.assertEqual(len(data["items"]), 1)
        item = data["items"][0]
        card = item["card_a"] if item["title_a"] == title_a else item["card_b"]
        self.assertEqual(card["match_status"], "canonical_key")
        self.assertEqual(card["product_id"], "prod_right")
        self.assertEqual(card["image_url"], "https://cdn.technodom.kz/right-128.jpg")
        self.assertEqual(card["attributes"]["storage"], "128GB")
        self.assertEqual(len(card["offers"]), 1)
        self.assertEqual(card["offers"][0]["shop"], "Technodom")
        self.assertEqual(card["offers"][0]["url"], "https://technodom.kz/right-128")

    def test_mm04_unkeyed_legacy_row_excluded_from_bound_canonical_card(self):
        """Регрессия остатка MM-04: при подтверждённой канонической привязке legacy-строка с пустым canonical_key
        не должна подмешивать посторонние фото/описание/цену/ссылку в карточку."""
        title_a = "Apple iPhone 15 128GB Black"
        title_b = "iPhone 15 128 ГБ Черный"

        conn = database.get_connection()
        try:
            conn.execute(
                "INSERT INTO sellers (id, name, slug, rating, domain, is_active, metadata_json, created_at, updated_at) "
                "VALUES ('s_td', 'Technodom', 'technodom', 4.8, 'technodom.kz', 1, '{}', '2026-11-28', '2026-11-28')"
            )
            conn.execute(
                "INSERT INTO channels (id, seller_id, channel_type, name, created_at) "
                "VALUES ('ch_td', 's_td', 'website', 'Technodom', '2026-11-28')"
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
                image_url="https://cdn.technodom.kz/iphone15-128.jpg",
                attributes={"storage": "128GB"},
            ))
            pr.save_or_update(CanonicalProduct(
                id="prod_other",
                canonical_key="apple_iphone_15_256_black_other",
                title=title_a,
                category="phones",
                brand="Apple",
                model="iPhone 15 256GB",
                image_url="https://cdn.other.kz/iphone15-256.jpg",
                attributes={"storage": "256GB"},
            ))
            orf.save_or_update(Offer(
                id="off_a",
                product_id="prod_a",
                seller_id="s_td",
                channel_id="ch_td",
                external_sku="sku_a",
                url="https://technodom.kz/p/iphone15-128",
                price=389990.0,
                condition="NEW",
                city="Алматы",
            ))
            # Посторонняя legacy-строка с тем же названием и ПУСТЫМ canonical_key
            conn.execute(
                "INSERT INTO products (id, url, shop, category, title, first_seen_price, current_price, old_price_on_site, min_price, max_price, image_url, description, canonical_key) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "legacy_unbound",
                    "https://example.test/unbound-legacy",
                    "UnknownShop",
                    "phones",
                    title_a,
                    49000,
                    49000,
                    0,
                    49000,
                    49000,
                    "https://example.test/fake.jpg",
                    "Постороннее legacy-описание",
                    None,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        database.save_cached_canonical_key(title_a, "apple_iphone_15_128_black_a")
        database.enqueue_disputed_pair(title_a, title_b, "mm04_legacy_unbound", force=True)

        data = database.get_disputed_product_pairs(status="pending", limit=20, offset=0)
        self.assertEqual(len(data["items"]), 1)
        item = data["items"][0]
        card = item["card_a"] if item["title_a"] == title_a else item["card_b"]
        offers = item["offers_a"] if item["title_a"] == title_a else item["offers_b"]
        self.assertEqual(card["match_status"], "canonical_key")
        self.assertEqual(card["product_id"], "prod_a")
        self.assertEqual(len(card["offers"]), 1)
        self.assertEqual(card["offers"][0]["url"], "https://technodom.kz/p/iphone15-128")
        self.assertEqual(card["offers"][0]["current_price"], 389990.0)
        self.assertEqual(len(offers), 1)
        self.assertEqual(offers[0]["url"], "https://technodom.kz/p/iphone15-128")
        self.assertNotIn("https://example.test/fake.jpg", card["images"])
        self.assertEqual(card["description"], "")

    def test_primary_side_selection_chooses_surviving_canonical_key_for_same_verdict(self):
        """v6.70.0: оператор может явно выбрать основную карточку ('a' или 'b') при склейке same."""
        # Канонический порядок в enqueue_disputed_pair: title_a < title_b
        title_a = "A_Card Sony WH-1000XM5 Black"
        title_b = "B_Card Sony WH-1000XM5 Черный"
        key_a = "k2:sony:wh1000xm5:aaa_longer_key"
        key_b = "k2:sony:wh1000xm5:bbb_longer_key_2"

        database.save_cached_canonical_key(title_a, key_a)
        database.save_cached_canonical_key(title_b, key_b)
        conn = database.get_connection()
        try:
            conn.execute(
                "INSERT INTO products (id, url, shop, category, title, first_seen_price, current_price, old_price_on_site, min_price, max_price, canonical_key) "
                "VALUES ('p_a', 'https://shop1.kz/xm5', 'Shop1', 'headphones', ?, 150000, 150000, 0, 150000, 150000, ?)",
                (title_a, key_a),
            )
            conn.execute(
                "INSERT INTO products (id, url, shop, category, title, first_seen_price, current_price, old_price_on_site, min_price, max_price, canonical_key) "
                "VALUES ('p_b', 'https://shop2.kz/xm5', 'Shop2', 'headphones', ?, 155000, 155000, 0, 155000, 155000, ?)",
                (title_b, key_b),
            )
            conn.commit()
        finally:
            conn.close()

        database.enqueue_disputed_pair(title_a, title_b, "primary_side_b_test", force=True)
        ph = database.pair_hash(title_a, title_b)

        # Недопустимое значение primary_side отклоняется
        bad = database.resolve_disputed_pair_human(ph, "same", "admin", "bad", primary_side="z")
        self.assertFalse(bad["ok"])

        # Выбираем основную карточку 'b' (даже несмотря на то, что key_a короче и раньше по алфавиту)
        res = database.resolve_disputed_pair_human(ph, "same", "admin", "выбрана карточка 2", primary_side="b")
        self.assertTrue(res["ok"])
        self.assertEqual(res["canonical_key"], key_b)
        self.assertEqual(res["primary_side"], "b")

        keys_after = database.get_cached_canonical_keys_batch([title_a, title_b])
        self.assertEqual(keys_after[title_a], key_b)
        self.assertEqual(keys_after[title_b], key_b)

        conn = database.get_connection()
        try:
            prod_keys = dict(conn.execute("SELECT id, canonical_key FROM products WHERE id IN ('p_a', 'p_b')").fetchall())
        finally:
            conn.close()
        self.assertEqual(prod_keys["p_a"], key_b)
        self.assertEqual(prod_keys["p_b"], key_b)

    def test_primary_side_a_overrides_shorter_key_b(self):
        title_a = "A_Card Dyson Supersonic HD07"
        title_b = "B_Card Фен Dyson HD07"
        key_a = "k2:dyson:supersonic:hd07_long_key"
        key_b = "k2:dyson:hd07"

        database.save_cached_canonical_key(title_a, key_a)
        database.save_cached_canonical_key(title_b, key_b)
        database.enqueue_disputed_pair(title_a, title_b, "primary_side_a_test", force=True)
        ph = database.pair_hash(title_a, title_b)

        res = database.resolve_disputed_pair_human(ph, "same", "admin", "выбрана карточка 1", primary_side="a")
        self.assertTrue(res["ok"])
        self.assertEqual(res["canonical_key"], key_a)
        self.assertEqual(res["primary_side"], "a")
        keys_after = database.get_cached_canonical_keys_batch([title_a, title_b])
        self.assertEqual(keys_after[title_a], key_a)
        self.assertEqual(keys_after[title_b], key_a)

    def test_mm04_ambiguous_title_without_confirmed_key_marks_ambiguity_and_hides_wrong_card(self):
        """Регрессия MM-04: при двух карточках с одинаковым заголовком и без подтверждённого ключа
        API обозначает неоднозначность (ambiguous_title) и не подставляет случайную карточку."""
        title_ambig = "Samsung Galaxy S24"
        title_other = "Смартфон Samsung S24"

        conn = database.get_connection()
        try:
            pr = ProductRepository(conn)
            pr.save_or_update(CanonicalProduct(
                id="prod_s24_128",
                canonical_key="samsung_s24_128",
                title=title_ambig,
                category="phones",
                image_url="https://cdn.example/s24-128.jpg",
                attributes={"storage": "128GB"},
            ))
            pr.save_or_update(CanonicalProduct(
                id="prod_s24_256",
                canonical_key="samsung_s24_256",
                title=title_ambig,
                category="phones",
                image_url="https://cdn.example/s24-256.jpg",
                attributes={"storage": "256GB"},
            ))
            conn.commit()
        finally:
            conn.close()

        database.enqueue_disputed_pair(title_ambig, title_other, "mm04_ambig", force=True)
        data = database.get_disputed_product_pairs(status="pending", limit=20, offset=0)
        self.assertEqual(len(data["items"]), 1)
        item = data["items"][0]
        card = item["card_a"] if item["title_a"] == title_ambig else item["card_b"]
        self.assertEqual(card["match_status"], "ambiguous_title")
        self.assertIsNone(card["product_id"])
        self.assertEqual(card["ambiguous_count"], 2)
        self.assertEqual(card["image_url"], "")
        self.assertEqual(card["attributes"], {})
        self.assertEqual(card["offers"], [])


if __name__ == "__main__":
    unittest.main()
