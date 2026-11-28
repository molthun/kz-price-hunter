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

    def _seed_v2_only_pair_with_control(self):
        """Создаёт 2 карточки V2 с предложениями, историей цен, размерами, избранным и контрольную постороннюю карточку (без legacy products)."""
        title_a = "Apple iPhone 15 128 ГБ Черный"
        title_b = "Смартфон Apple iPhone 15 128GB Black"
        title_ctrl = "Смартфон Samsung Galaxy S24 Ultra 256GB"
        key_a = "apple_iphone_15_128_black_a"
        key_b = "apple_iphone_15_128_black_b"
        key_ctrl = "samsung_s24_ultra_256"

        conn = database.get_connection()
        try:
            conn.execute(
                "INSERT INTO sellers (id, name, slug, rating, domain, is_active, metadata_json, created_at, updated_at) "
                "VALUES ('s_a', 'Technodom', 'technodom', 4.8, 'technodom.kz', 1, '{}', '2026-11-28', '2026-11-28')"
            )
            conn.execute(
                "INSERT INTO sellers (id, name, slug, rating, domain, is_active, metadata_json, created_at, updated_at) "
                "VALUES ('s_b', 'Mechta', 'mechta', 4.7, 'mechta.kz', 1, '{}', '2026-11-28', '2026-11-28')"
            )
            conn.execute(
                "INSERT INTO channels (id, seller_id, channel_type, name, created_at) "
                "VALUES ('ch_a', 's_a', 'website', 'Technodom', '2026-11-28')"
            )
            conn.execute(
                "INSERT INTO channels (id, seller_id, channel_type, name, created_at) "
                "VALUES ('ch_b', 's_b', 'website', 'Mechta', '2026-11-28')"
            )
            pr = ProductRepository(conn)
            orf = OfferRepository(conn)

            pr.save_or_update(CanonicalProduct(
                id="prod_a", canonical_key=key_a, title=title_a, category="phones", brand="Apple", model="iPhone 15",
            ))
            pr.save_or_update(CanonicalProduct(
                id="prod_b", canonical_key=key_b, title=title_b, category="phones", brand="Apple", model="iPhone 15",
            ))
            pr.save_or_update(CanonicalProduct(
                id="prod_ctrl", canonical_key=key_ctrl, title=title_ctrl, category="phones", brand="Samsung", model="S24 Ultra",
            ))

            orf.save_or_update(Offer(
                id="off_a", product_id="prod_a", seller_id="s_a", channel_id="ch_a",
                external_sku="sku_a", url="https://technodom.kz/a", price=389990.0, condition="NEW", city="Алматы",
            ))
            orf.save_or_update(Offer(
                id="off_b", product_id="prod_b", seller_id="s_b", channel_id="ch_b",
                external_sku="sku_b", url="https://mechta.kz/b", price=384990.0, condition="NEW", city="Астана",
            ))
            orf.save_or_update(Offer(
                id="off_ctrl", product_id="prod_ctrl", seller_id="s_a", channel_id="ch_a",
                external_sku="sku_ctrl", url="https://technodom.kz/ctrl", price=599990.0, condition="NEW", city="Алматы",
            ))

            # История цен V2
            hist_cols = {r[1] for r in conn.execute("PRAGMA table_info(offer_price_history)").fetchall()}
            if "product_id" in hist_cols:
                conn.execute(
                    "INSERT INTO offer_price_history (offer_id, product_id, price, old_price, observed_at) VALUES ('off_a', 'prod_a', 399990, 419990, '2026-11-27T10:00:00Z')"
                )
                conn.execute(
                    "INSERT INTO offer_price_history (offer_id, product_id, price, old_price, observed_at) VALUES ('off_b', 'prod_b', 394990, 409990, '2026-11-27T10:00:00Z')"
                )
                conn.execute(
                    "INSERT INTO offer_price_history (offer_id, product_id, price, old_price, observed_at) VALUES ('off_ctrl', 'prod_ctrl', 609990, 629990, '2026-11-27T10:00:00Z')"
                )
            else:
                conn.execute(
                    "INSERT INTO offer_price_history (offer_id, price, old_price, observed_at) VALUES ('off_a', 399990, 419990, '2026-11-27T10:00:00Z')"
                )
                conn.execute(
                    "INSERT INTO offer_price_history (offer_id, price, old_price, observed_at) VALUES ('off_b', 394990, 409990, '2026-11-27T10:00:00Z')"
                )
                conn.execute(
                    "INSERT INTO offer_price_history (offer_id, price, old_price, observed_at) VALUES ('off_ctrl', 609990, 629990, '2026-11-27T10:00:00Z')"
                )

            # Размеры и избранное
            conn.execute("INSERT INTO offer_sizes (offer_id, product_id, size, updated_at) VALUES ('off_a', 'prod_a', '128', '2026-11-28')")
            conn.execute("INSERT INTO offer_sizes (offer_id, product_id, size, updated_at) VALUES ('off_b', 'prod_b', '128', '2026-11-28')")
            conn.execute("INSERT INTO offer_sizes (offer_id, product_id, size, updated_at) VALUES ('off_ctrl', 'prod_ctrl', '256', '2026-11-28')")
            conn.execute(
                "INSERT INTO user_favorites (user_id, item_id, canonical_product_id, title, shop, saved_price, saved_at) "
                "VALUES (1, 'fav_a', 'prod_a', ?, 'Technodom', 389990, '2026-11-28T00:00:00Z')",
                (title_a,),
            )
            conn.execute(
                "INSERT INTO user_favorites (user_id, item_id, canonical_product_id, title, shop, saved_price, saved_at) "
                "VALUES (2, 'fav_b', 'prod_b', ?, 'Mechta', 384990, '2026-11-28T00:00:00Z')",
                (title_b,),
            )
            conn.execute(
                "INSERT INTO user_favorites (user_id, item_id, canonical_product_id, title, shop, saved_price, saved_at) "
                "VALUES (3, 'fav_ctrl', 'prod_ctrl', ?, 'Technodom', 599990, '2026-11-28T00:00:00Z')",
                (title_ctrl,),
            )
            conn.commit()
        finally:
            conn.close()

        database.save_cached_canonical_key(title_a, key_a)
        database.save_cached_canonical_key(title_b, key_b)
        database.save_cached_canonical_key(title_ctrl, key_ctrl)
        database.enqueue_disputed_pair(title_a, title_b, "mm05_v2_only", force=True)
        return database.pair_hash(title_a, title_b), key_a, key_b

    def test_mm05_v2_merge_primary_b_moves_offers_history_sizes_favorites_and_keeps_control(self):
        ph, key_a, key_b = self._seed_v2_only_pair_with_control()
        res = database.resolve_disputed_pair_human(ph, "same", "admin", "choose b", primary_side="b")
        self.assertTrue(res["ok"])
        self.assertEqual(res["canonical_key"], key_b)
        self.assertEqual(res["primary_side"], "b")
        self.assertEqual(res["primary_product_id"], "prod_b")
        self.assertEqual(res["v2_offers_moved"], 1)

        conn = database.get_connection()
        try:
            offers = dict(conn.execute("SELECT id, product_id FROM offers").fetchall())
            self.assertEqual(offers["off_a"], "prod_b")
            self.assertEqual(offers["off_b"], "prod_b")
            self.assertEqual(offers["off_ctrl"], "prod_ctrl")

            hist_cols = {r[1] for r in conn.execute("PRAGMA table_info(offer_price_history)").fetchall()}
            if "product_id" in hist_cols:
                hist = dict(conn.execute("SELECT offer_id, product_id FROM offer_price_history").fetchall())
                self.assertEqual(hist["off_a"], "prod_b")
                self.assertEqual(hist["off_b"], "prod_b")
                self.assertEqual(hist["off_ctrl"], "prod_ctrl")

            sizes = dict(conn.execute("SELECT offer_id, product_id FROM offer_sizes").fetchall())
            self.assertEqual(sizes["off_a"], "prod_b")
            self.assertEqual(sizes["off_b"], "prod_b")
            self.assertEqual(sizes["off_ctrl"], "prod_ctrl")

            favs = dict(conn.execute("SELECT item_id, canonical_product_id FROM user_favorites").fetchall())
            self.assertEqual(favs["fav_a"], "prod_b")
            self.assertEqual(favs["fav_b"], "prod_b")
            self.assertEqual(favs["fav_ctrl"], "prod_ctrl")

            aliases = {tuple(r) for r in conn.execute("SELECT kind, old_id, new_id FROM identity_aliases").fetchall()}
            self.assertIn(("product", "prod_a", "prod_b"), aliases)
            self.assertIn(("key", key_a, key_b), aliases)
        finally:
            conn.close()

    def test_mm05_v2_merge_primary_a_moves_offers_history_sizes_favorites_and_keeps_control(self):
        ph, key_a, key_b = self._seed_v2_only_pair_with_control()
        res = database.resolve_disputed_pair_human(ph, "same", "admin", "choose a", primary_side="a")
        self.assertTrue(res["ok"])
        self.assertEqual(res["canonical_key"], key_a)
        self.assertEqual(res["primary_side"], "a")
        self.assertEqual(res["primary_product_id"], "prod_a")
        self.assertEqual(res["v2_offers_moved"], 1)

        conn = database.get_connection()
        try:
            offers = dict(conn.execute("SELECT id, product_id FROM offers").fetchall())
            self.assertEqual(offers["off_a"], "prod_a")
            self.assertEqual(offers["off_b"], "prod_a")
            self.assertEqual(offers["off_ctrl"], "prod_ctrl")

            sizes = dict(conn.execute("SELECT offer_id, product_id FROM offer_sizes").fetchall())
            self.assertEqual(sizes["off_b"], "prod_a")
            self.assertEqual(sizes["off_ctrl"], "prod_ctrl")

            favs = dict(conn.execute("SELECT item_id, canonical_product_id FROM user_favorites").fetchall())
            self.assertEqual(favs["fav_b"], "prod_a")
            self.assertEqual(favs["fav_ctrl"], "prod_ctrl")

            aliases = {tuple(r) for r in conn.execute("SELECT kind, old_id, new_id FROM identity_aliases").fetchall()}
            self.assertIn(("product", "prod_b", "prod_a"), aliases)
            self.assertIn(("key", key_b, key_a), aliases)
        finally:
            conn.close()

    def test_mm05_ambiguous_v2_binding_refuses_merge_and_leaves_pair_pending(self):
        title_ambig = "Samsung Galaxy S24"
        title_other = "Смартфон Samsung S24"
        conn = database.get_connection()
        try:
            pr = ProductRepository(conn)
            pr.save_or_update(CanonicalProduct(id="p1", canonical_key="s24_128", title=title_ambig, category="phones"))
            pr.save_or_update(CanonicalProduct(id="p2", canonical_key="s24_256", title=title_ambig, category="phones"))
            conn.commit()
        finally:
            conn.close()

        database.enqueue_disputed_pair(title_ambig, title_other, "mm05_ambig", force=True)
        ph = database.pair_hash(title_ambig, title_other)
        res = database.resolve_disputed_pair_human(ph, "same", "admin", "try merge", primary_side="a")
        self.assertFalse(res["ok"])
        self.assertIn("Неоднозначная привязка", res["error"])
        self.assertEqual(database.get_disputed_pair_status(ph), "pending")

    def test_mm06_showcase_rescan_after_manual_merge_preserves_primary_product_and_new_price(self):
        """Регрессия MM-06: повторный обход витрины (match_or_create_canonical_product + upsert_showcase_offer)
        с новой ценой не разваливает ручную склейку V2 и не затрагивает контрольный товар."""
        from domain.channel_sync import match_or_create_canonical_product, upsert_showcase_offer
        from scrapers.showcase_scanners import ShowcaseOffer

        title_a = "A_Кофемашина Delonghi Magnifica Start"
        title_b = "B_Делонги Магнифика Старт автоматическая"
        title_ctrl = "Наушники Sony WH-1000XM5 Контроль"
        now1 = "2026-11-28T06:00:00Z"
        now2 = "2026-11-28T07:00:00Z"

        conn = database.get_connection()
        try:
            conn.execute(
                "INSERT INTO sellers (id, name, slug, rating, domain, is_active, metadata_json, created_at, updated_at) "
                "VALUES ('s_kaspi', 'KaspiStore', 'kaspistore', 4.9, 'kaspi.kz', 1, '{}', '2026-11-28', '2026-11-28')"
            )
            conn.execute(
                "INSERT INTO channels (id, seller_id, channel_type, name, created_at) "
                "VALUES ('ch_kaspi', 's_kaspi', 'kaspi', 'KaspiStore', '2026-11-28')"
            )

            off_a_1 = ShowcaseOffer(
                marketplace="kaspi", merchant_id="m_kaspi",
                external_sku="sku_mm06_a", title=title_a, price=389990.0,
                url="https://kaspi.kz/shop/p/a", category="phones", city="Алматы",
            )
            off_b_1 = ShowcaseOffer(
                marketplace="kaspi", merchant_id="m_kaspi",
                external_sku="sku_mm06_b", title=title_b, price=384990.0,
                url="https://kaspi.kz/shop/p/b", category="phones", city="Алматы",
            )
            off_ctrl_1 = ShowcaseOffer(
                marketplace="kaspi", merchant_id="m_kaspi",
                external_sku="sku_mm06_ctrl", title=title_ctrl, price=159990.0,
                url="https://kaspi.kz/shop/p/ctrl", category="headphones", city="Алматы",
            )

            pid_a, _ = match_or_create_canonical_product(conn, off_a_1, now1)
            pid_b, _ = match_or_create_canonical_product(conn, off_b_1, now1)
            pid_ctrl, _ = match_or_create_canonical_product(conn, off_ctrl_1, now1)
            self.assertNotEqual(pid_a, pid_b)

            oid_a = upsert_showcase_offer(conn, off_a_1, pid_a, "s_kaspi", "ch_kaspi", now1)
            oid_b = upsert_showcase_offer(conn, off_b_1, pid_b, "s_kaspi", "ch_kaspi", now1)
            oid_ctrl = upsert_showcase_offer(conn, off_ctrl_1, pid_ctrl, "s_kaspi", "ch_kaspi", now1)

            key_a = conn.execute("SELECT canonical_key FROM canonical_products WHERE id = ?", (pid_a,)).fetchone()[0]
            key_b = conn.execute("SELECT canonical_key FROM canonical_products WHERE id = ?", (pid_b,)).fetchone()[0]
            conn.commit()
        finally:
            conn.close()

        database.save_cached_canonical_key(title_a, key_a)
        database.save_cached_canonical_key(title_b, key_b)
        database.enqueue_disputed_pair(title_a, title_b, "mm06_showcase", force=True)
        ph = database.pair_hash(title_a, title_b)

        # Выбираем основную карточку 'b' (pid_b)
        verdict_res = database.resolve_disputed_pair_human(ph, "same", "admin", "merge to b", primary_side="b")
        self.assertTrue(verdict_res["ok"])
        self.assertEqual(verdict_res["primary_product_id"], pid_b)

        # Повторяем обход витрины с новыми ценами
        conn = database.get_connection()
        try:
            off_a_2 = ShowcaseOffer(
                marketplace="kaspi", merchant_id="m_kaspi",
                external_sku="sku_mm06_a", title=title_a, price=379990.0,
                url="https://kaspi.kz/shop/p/a", category="phones", city="Алматы",
            )
            off_b_2 = ShowcaseOffer(
                marketplace="kaspi", merchant_id="m_kaspi",
                external_sku="sku_mm06_b", title=title_b, price=374990.0,
                url="https://kaspi.kz/shop/p/b", category="phones", city="Алматы",
            )
            off_ctrl_2 = ShowcaseOffer(
                marketplace="kaspi", merchant_id="m_kaspi",
                external_sku="sku_mm06_ctrl", title=title_ctrl, price=154990.0,
                url="https://kaspi.kz/shop/p/ctrl", category="headphones", city="Алматы",
            )

            pid_a_after, _ = match_or_create_canonical_product(conn, off_a_2, now2)
            pid_b_after, _ = match_or_create_canonical_product(conn, off_b_2, now2)
            pid_ctrl_after, _ = match_or_create_canonical_product(conn, off_ctrl_2, now2)

            self.assertEqual(pid_a_after, pid_b, "Второй обход витрины должен сохранять выбранную основную карточку pid_b")
            self.assertEqual(pid_b_after, pid_b)
            self.assertEqual(pid_ctrl_after, pid_ctrl)

            upsert_showcase_offer(conn, off_a_2, pid_a_after, "s_kaspi", "ch_kaspi", now2)
            upsert_showcase_offer(conn, off_b_2, pid_b_after, "s_kaspi", "ch_kaspi", now2)
            upsert_showcase_offer(conn, off_ctrl_2, pid_ctrl_after, "s_kaspi", "ch_kaspi", now2)
            conn.commit()

            offers_map = dict(conn.execute("SELECT id, product_id FROM offers").fetchall())
            self.assertEqual(offers_map[oid_a], pid_b)
            self.assertEqual(offers_map[oid_b], pid_b)
            self.assertEqual(offers_map[oid_ctrl], pid_ctrl)

            hist_cols = {r[1] for r in conn.execute("PRAGMA table_info(offer_price_history)").fetchall()}
            if "product_id" in hist_cols:
                hist_a = conn.execute(
                    "SELECT product_id, price FROM offer_price_history WHERE offer_id = ? ORDER BY observed_at",
                    (oid_a,),
                ).fetchall()
                self.assertEqual(len(hist_a), 2)
                self.assertTrue(all(r[0] == pid_b for r in hist_a))
                self.assertEqual(hist_a[-1][1], 379990.0)
                hist_ctrl = conn.execute(
                    "SELECT product_id FROM offer_price_history WHERE offer_id = ?",
                    (oid_ctrl,),
                ).fetchall()
                self.assertTrue(all(r[0] == pid_ctrl for r in hist_ctrl))
        finally:
            conn.close()

    def test_mm07_partial_rekey_alias_does_not_redirect_unpinned_showcase_offer(self):
        """Регрессия MM-07: частичный перенос другого названия через rekey_titles_conn (с записью identity_aliases)
        не должен уводить незакреплённое витринное предложение и его новую цену на чужую карточку."""
        from domain.channel_sync import match_or_create_canonical_product, upsert_showcase_offer
        from scrapers.showcase_scanners import ShowcaseOffer

        title_showcase = "Блендер Braun MultiQuick 5 Витрина"
        title_legacy = "Погружной блендер Braun MQ5 Старое название"
        now1 = "2026-11-28T06:00:00Z"
        now2 = "2026-11-28T07:00:00Z"

        conn = database.get_connection()
        try:
            conn.execute(
                "INSERT INTO sellers (id, name, slug, rating, domain, is_active, metadata_json, created_at, updated_at) "
                "VALUES ('s_mm07', 'KaspiStore', 'kaspistore', 4.9, 'kaspi.kz', 1, '{}', '2026-11-28', '2026-11-28')"
            )
            conn.execute(
                "INSERT INTO channels (id, seller_id, channel_type, name, created_at) "
                "VALUES ('ch_mm07', 's_mm07', 'kaspi', 'KaspiStore', '2026-11-28')"
            )

            off_1 = ShowcaseOffer(
                marketplace="kaspi", merchant_id="m_mm07",
                external_sku="sku_mm07_sh", title=title_showcase, price=35000.0,
                url="https://kaspi.kz/shop/p/mm07", category="kitchen", city="Алматы",
            )
            pid_orig, _ = match_or_create_canonical_product(conn, off_1, now1)
            oid_sh = upsert_showcase_offer(conn, off_1, pid_orig, "s_mm07", "ch_mm07", now1)
            key_orig = conn.execute("SELECT canonical_key FROM canonical_products WHERE id = ?", (pid_orig,)).fetchone()[0]

            # Старое предложение с другим названием на той же исходной карточке pid_orig
            conn.execute(
                "INSERT INTO products (id, url, shop, category, title, first_seen_price, current_price, old_price_on_site, min_price, max_price, canonical_key) "
                "VALUES ('leg_mm07', 'https://old.kz/mq5', 'OldShop', 'kitchen', ?, 34000, 34000, 0, 34000, 34000, ?)",
                (title_legacy, key_orig),
            )
            OfferRepository(conn).save_or_update(Offer(
                id="off_leg_mm07", product_id=pid_orig, seller_id="s_mm07", channel_id="ch_mm07",
                external_sku="sku_leg_mm07", url="https://old.kz/mq5", price=34000.0, condition="NEW", city="Алматы",
            ))

            # Штатный частичный перенос ТОЛЬКО title_legacy на другой ключ (создаёт исторические identity_aliases)
            database.rekey_titles_conn(conn, {title_legacy: "k2:braun:mq5:other_split"})
            conn.commit()

            # Убеждаемся, что алиасы от частичного переноса действительно записались
            aliases = conn.execute("SELECT kind, old_id, new_id FROM identity_aliases WHERE old_id IN (?, ?)", (pid_orig, key_orig)).fetchall()
            self.assertGreaterEqual(len(aliases), 1)

            # Повторный обход витринного предложения с новой ценой — оно НЕ имеет пина и должно остаться на pid_orig
            off_2 = ShowcaseOffer(
                marketplace="kaspi", merchant_id="m_mm07",
                external_sku="sku_mm07_sh", title=title_showcase, price=33000.0,
                url="https://kaspi.kz/shop/p/mm07", category="kitchen", city="Алматы",
            )
            pid_after, _ = match_or_create_canonical_product(conn, off_2, now2)
            self.assertEqual(pid_after, pid_orig, "Исторический алиас частичного переноса не должен уводить витрину на чужой ID")
            upsert_showcase_offer(conn, off_2, pid_after, "s_mm07", "ch_mm07", now2)
            conn.commit()

            cur_pid = conn.execute("SELECT product_id FROM offers WHERE id = ?", (oid_sh,)).fetchone()[0]
            self.assertEqual(cur_pid, pid_orig)
            hist_cols = {r[1] for r in conn.execute("PRAGMA table_info(offer_price_history)").fetchall()}
            if "product_id" in hist_cols:
                hist = conn.execute(
                    "SELECT product_id, price FROM offer_price_history WHERE offer_id = ? ORDER BY observed_at",
                    (oid_sh,),
                ).fetchall()
                self.assertEqual(len(hist), 2)
                self.assertTrue(all(r[0] == pid_orig for r in hist))
                self.assertEqual(hist[-1][1], 33000.0)
        finally:
            conn.close()

    def test_mm07_current_pin_takes_priority_over_historical_identity_aliases(self):
        """Регрессия MM-07: актуальный пин в title_key_pins имеет приоритет над историческими identity_aliases."""
        from domain.channel_sync import match_or_create_canonical_product, upsert_showcase_offer
        from scrapers.showcase_scanners import ShowcaseOffer

        title_a = "A_Тостер Philips HD2581"
        title_b = "B_Филипс Тостер 2581"
        now1 = "2026-11-28T06:00:00Z"
        now2 = "2026-11-28T07:00:00Z"

        conn = database.get_connection()
        try:
            conn.execute(
                "INSERT INTO sellers (id, name, slug, rating, domain, is_active, metadata_json, created_at, updated_at) "
                "VALUES ('s_pin', 'KaspiStore', 'kaspistore', 4.9, 'kaspi.kz', 1, '{}', '2026-11-28', '2026-11-28')"
            )
            conn.execute(
                "INSERT INTO channels (id, seller_id, channel_type, name, created_at) "
                "VALUES ('ch_pin', 's_pin', 'kaspi', 'KaspiStore', '2026-11-28')"
            )
            off_a = ShowcaseOffer(
                marketplace="kaspi", merchant_id="m_pin",
                external_sku="sku_pin_a", title=title_a, price=19000.0,
                url="https://kaspi.kz/shop/p/pin_a", category="kitchen", city="Алматы",
            )
            off_b = ShowcaseOffer(
                marketplace="kaspi", merchant_id="m_pin",
                external_sku="sku_pin_b", title=title_b, price=18500.0,
                url="https://kaspi.kz/shop/p/pin_b", category="kitchen", city="Алматы",
            )
            pid_a, _ = match_or_create_canonical_product(conn, off_a, now1)
            pid_b, _ = match_or_create_canonical_product(conn, off_b, now1)
            oid_a = upsert_showcase_offer(conn, off_a, pid_a, "s_pin", "ch_pin", now1)
            upsert_showcase_offer(conn, off_b, pid_b, "s_pin", "ch_pin", now1)
            key_a = conn.execute("SELECT canonical_key FROM canonical_products WHERE id = ?", (pid_a,)).fetchone()[0]
            key_b = conn.execute("SELECT canonical_key FROM canonical_products WHERE id = ?", (pid_b,)).fetchone()[0]

            # Создаём постороннюю историческую цель и исторические алиасы от key_b / pid_b к ней
            ProductRepository(conn).save_or_update(CanonicalProduct(
                id="prod_stale_alias", canonical_key="k2:stale:alias", title="Старая посторонняя карточка", category="kitchen",
            ))
            conn.execute("INSERT OR IGNORE INTO identity_aliases (kind, old_id, new_id) VALUES ('key', ?, 'k2:stale:alias')", (key_b,))
            conn.execute("INSERT OR IGNORE INTO identity_aliases (kind, old_id, new_id) VALUES ('product', ?, 'prod_stale_alias')", (pid_b,))
            conn.commit()
        finally:
            conn.close()

        database.save_cached_canonical_key(title_a, key_a)
        database.save_cached_canonical_key(title_b, key_b)
        database.enqueue_disputed_pair(title_a, title_b, "mm07_pin_prio", force=True)
        ph = database.pair_hash(title_a, title_b)
        res = database.resolve_disputed_pair_human(ph, "same", "admin", "pin to b", primary_side="b")
        self.assertTrue(res["ok"])

        conn = database.get_connection()
        try:
            off_a_2 = ShowcaseOffer(
                marketplace="kaspi", merchant_id="m_pin",
                external_sku="sku_pin_a", title=title_a, price=17900.0,
                url="https://kaspi.kz/shop/p/pin_a", category="kitchen", city="Алматы",
            )
            pid_after, _ = match_or_create_canonical_product(conn, off_a_2, now2)
            self.assertEqual(pid_after, pid_b, "Актуальный пин title_key_pins должен побеждать исторический identity_aliases")
            upsert_showcase_offer(conn, off_a_2, pid_after, "s_pin", "ch_pin", now2)
            conn.commit()
            self.assertEqual(conn.execute("SELECT product_id FROM offers WHERE id = ?", (oid_a,)).fetchone()[0], pid_b)
        finally:
            conn.close()

    def _assert_mm08_uncached_primary_side(self, chosen_side: str):
        ph, key_a, key_b = self._seed_v2_only_pair_with_control()
        # Удаляем из title_canonical_cache ключ выбранной стороны (как при unique_title без кэша)
        title_to_uncache = "Apple iPhone 15 128 ГБ Черный" if chosen_side == "a" else "Смартфон Apple iPhone 15 128GB Black"
        expected_pid = "prod_a" if chosen_side == "a" else "prod_b"
        expected_key = key_a if chosen_side == "a" else key_b

        conn = database.get_connection()
        try:
            conn.execute("DELETE FROM title_canonical_cache WHERE title = ?", (title_to_uncache,))
            conn.commit()
        finally:
            conn.close()

        res = database.resolve_disputed_pair_human(ph, "same", "admin", f"choose uncached {chosen_side}", primary_side=chosen_side)
        self.assertTrue(res["ok"])
        self.assertEqual(res["primary_side"], chosen_side)
        self.assertEqual(res["canonical_key"], expected_key)
        self.assertEqual(res["primary_product_id"], expected_pid)

        conn = database.get_connection()
        try:
            offers = dict(conn.execute("SELECT id, product_id FROM offers").fetchall())
            self.assertEqual(offers["off_a"], expected_pid)
            self.assertEqual(offers["off_b"], expected_pid)
            self.assertEqual(offers["off_ctrl"], "prod_ctrl")
            hist_cols = {r[1] for r in conn.execute("PRAGMA table_info(offer_price_history)").fetchall()}
            if "product_id" in hist_cols:
                hist = dict(conn.execute("SELECT offer_id, product_id FROM offer_price_history").fetchall())
                self.assertEqual(hist["off_a"], expected_pid)
                self.assertEqual(hist["off_b"], expected_pid)
                self.assertEqual(hist["off_ctrl"], "prod_ctrl")
        finally:
            conn.close()

    def test_mm08_uncached_primary_side_a_preserves_prod_a(self):
        self._assert_mm08_uncached_primary_side("a")

    def test_mm08_uncached_primary_side_b_preserves_prod_b(self):
        self._assert_mm08_uncached_primary_side("b")

    def test_mm08_unconfirmable_primary_side_refused_and_kept_pending(self):
        title_a = "A_Неизвестная карточка без V2"
        title_b = "B_Подтверждённая карточка V2"
        conn = database.get_connection()
        try:
            ProductRepository(conn).save_or_update(CanonicalProduct(
                id="prod_only_b", canonical_key="k2:only_b", title=title_b, category="phones",
            ))
            conn.commit()
        finally:
            conn.close()
        database.save_cached_canonical_key(title_b, "k2:only_b")
        database.enqueue_disputed_pair(title_a, title_b, "mm08_unconfirmable", force=True)
        ph = database.pair_hash(title_a, title_b)

        res = database.resolve_disputed_pair_human(ph, "same", "admin", "choose unconfirmed a", primary_side="a")
        self.assertFalse(res["ok"])
        self.assertIn("Не удалось подтвердить основную карточку", res["error"])
        self.assertEqual(database.get_disputed_pair_status(ph), "pending")

    def test_search_catalog_cards_for_matching_supports_cyrillic_tokens_key_and_offers_summary(self):
        """v6.71.0: поиск карточек каталога для ручной постановки дублей (кириллица, ключи, сводка предложений)."""
        conn = database.get_connection()
        try:
            conn.execute(
                "INSERT INTO sellers (id, name, slug, rating, domain, is_active, metadata_json, created_at, updated_at) "
                "VALUES ('s_lg1', 'Technodom', 'technodom', 4.8, 'technodom.kz', 1, '{}', '2026-11-28', '2026-11-28')"
            )
            conn.execute(
                "INSERT INTO sellers (id, name, slug, rating, domain, is_active, metadata_json, created_at, updated_at) "
                "VALUES ('s_lg2', 'Mechta', 'mechta', 4.7, 'mechta.kz', 1, '{}', '2026-11-28', '2026-11-28')"
            )
            conn.execute(
                "INSERT INTO channels (id, seller_id, channel_type, name, created_at) "
                "VALUES ('ch_lg1', 's_lg1', 'website', 'Technodom', '2026-11-28')"
            )
            conn.execute(
                "INSERT INTO channels (id, seller_id, channel_type, name, created_at) "
                "VALUES ('ch_lg2', 's_lg2', 'website', 'Mechta', '2026-11-28')"
            )
            pr = ProductRepository(conn)
            orf = OfferRepository(conn)

            pr.save_or_update(CanonicalProduct(
                id="p_ac1", canonical_key="k2:lg:dualcool_09", title="Кондиционер LG Dualcool P09EP2",
                category="air_conditioners", brand="LG", model="P09EP2",
                image_url="javascript:evil()",
            ))
            pr.save_or_update(CanonicalProduct(
                id="p_ac2", canonical_key="k2:lg:p09ep2_alt", title="Сплит-система LG P09EP2 Dualcool",
                category="air_conditioners", brand="LG", model="P09EP2",
                image_url="https://cdn.mechta.kz/lg-p09.jpg",
            ))
            pr.save_or_update(CanonicalProduct(
                id="p_other", canonical_key="k2:apple:iphone15", title="Смартфон Apple iPhone 15",
                category="phones", brand="Apple", model="iPhone 15",
            ))
            orf.save_or_update(Offer(
                id="o_ac1", product_id="p_ac1", seller_id="s_lg1", channel_id="ch_lg1",
                external_sku="sku_ac1", url="https://technodom.kz/ac1",
                image_url="https://cdn.technodom.kz/lg-ac1.jpg", price=289990.0, condition="NEW", city="Алматы",
            ))
            orf.save_or_update(Offer(
                id="o_ac2", product_id="p_ac2", seller_id="s_lg2", channel_id="ch_lg2",
                external_sku="sku_ac2", url="https://mechta.kz/ac2",
                price=279990.0, condition="NEW", city="Астана",
            ))
            conn.commit()
        finally:
            conn.close()

        # Короткий запрос (< 2 символов) возвращает пустой список
        self.assertEqual(database.search_catalog_cards_for_matching("a"), [])

        # Кириллический регистронезависимый поиск по нескольким токенам
        res_cyr = database.search_catalog_cards_for_matching("кондиционер lg")
        self.assertEqual(len(res_cyr), 1)
        self.assertEqual(res_cyr[0]["product_id"], "p_ac1")
        self.assertEqual(res_cyr[0]["image_url"], "https://cdn.technodom.kz/lg-ac1.jpg")
        self.assertEqual(res_cyr[0]["offers_count"], 1)
        self.assertEqual(res_cyr[0]["min_price"], 289990.0)
        self.assertEqual(res_cyr[0]["shops"], ["Technodom"])

        # Поиск по общим токенам модели находит обе несклеенные карточки
        res_both = database.search_catalog_cards_for_matching("lg p09ep2")
        self.assertEqual({r["product_id"] for r in res_both}, {"p_ac1", "p_ac2"})

        # Поиск по каноническому ключу
        res_key = database.search_catalog_cards_for_matching("k2:lg:p09ep2_alt")
        self.assertEqual([r["product_id"] for r in res_key], ["p_ac2"])

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
