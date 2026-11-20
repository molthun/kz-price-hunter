"""Совместимость идентичности предложений после R1 (docs/STAND_WAVE_CYCLE_2026-11-20.md §7).

6.41.0 сделал «Алматы / Казахстан» и фид Белого Ветра общенациональными для поиска, но записи прода
хранятся под прежними суффиксами id (…@unknown, …@astana). Смена суффикса без переноса данных
создавала новые id и снимала старые, разрывая историю цен и наблюдения. Суффикс id остаётся прежним,
а смысл города (location_slug) — новым.
"""
from tests import test_support  # noqa: F401
import sqlite3
import unittest
from unittest.mock import patch

import database
from offer_identity import assign_offer_ids, id_location_slug, location_slug, offer_id


def _id(shop, raw, city):
    return offer_id({"id": raw, "shop": shop, "city": city})


class LegacyIdSuffixTest(unittest.TestCase):
    def test_relabeled_cities_keep_stored_id_suffix(self):
        # Форма id как в проде 6.40.33 (проверено GET /api/products, 20 902 записи, 0 расхождений)
        cases = [
            ("Kari", "123", "Алматы / Казахстан", "kari_123@unknown"),
            ("Детский мир", "77", "Алматы / Казахстан", "detmir_77@unknown"),
            ("Французский Дом", "9", "Алматы / Казахстан", "french_house_9@unknown"),
            ("Белый Ветер", "555", "Астана / Казахстан", "shopkz_555@astana"),
            ("Белый Ветер", "555", "Астана", "shopkz_555@astana"),
            ("Forcecom", "42", "Астана / Казахстан", "fc_42@kz"),
            ("Лемана ПРО", "8", "Казахстан", "lemanapro_8@kz"),
            ("Технодом", "302075", "Астана", "td_302075@astana"),
            ("Технодом", "302075", "Алматы", "td_302075@almaty"),
            ("Sulpak", "888", "Алматы", "sulpak_888@almaty"),
            ("12 Месяцев", "573273", "Регион не подтверждён", "twelve_months_573273@unknown"),
        ]
        for shop, raw, city, expected in cases:
            with self.subTest(shop=shop, city=city):
                self.assertEqual(_id(shop, raw, city), expected)

    def test_city_meaning_stays_nationwide(self):
        """Поиск и межгородской блок по-прежнему считают эти метки общенациональными."""
        for label in ("Алматы / Казахстан", "Астана / Казахстан", "Казахстан"):
            self.assertEqual(location_slug(label), "kz")
        self.assertEqual(id_location_slug("Алматы / Казахстан"), "unknown")
        self.assertEqual(id_location_slug("Астана / Казахстан"), "kz")
        self.assertEqual(id_location_slug("Астана / Казахстан", "Белый Ветер"), "astana")

    def test_shopkz_feed_items_keep_stored_ids(self):
        from scrapers.shopkz import ShopKzScraper
        import inspect
        self.assertIn('"city": "Астана / Казахстан"', inspect.getsource(ShopKzScraper))
        items = assign_offer_ids([{"id": "100", "shop": "Белый Ветер", "city": "Астана / Казахстан"}])
        self.assertEqual(items[0]["id"], "shopkz_100@astana")


class NoRetirementAfterRelabelTest(unittest.TestCase):
    """Полный обход общенационального магазина не снимает существующие записи прода."""

    def test_complete_crawl_keeps_existing_records_active(self):
        url = "https://kz.kari.com/catalog/zhenschinam/sumki-i-ryukzaki/"
        existing = ("kari_1@unknown", "kari_2@unknown")
        conn = sqlite3.connect(":memory:")
        try:
            conn.executescript("""CREATE TABLE products(id TEXT PRIMARY KEY,is_active INTEGER);
                CREATE TABLE product_sources(product_id TEXT,shop_key TEXT,source_url TEXT,active INTEGER,
                UNIQUE(product_id,shop_key,source_url));""")
            for pid in existing:
                conn.execute("INSERT INTO products VALUES (?,1)", (pid,))
                conn.execute("INSERT INTO product_sources VALUES (?,?,?,1)", (pid, "kari", url))
            crawl = assign_offer_ids([{"id": raw, "shop": "Kari", "city": "Алматы / Казахстан"}
                                      for raw in ("1", "2")])
            with patch.object(database, "_retire_mirrored_offers_conn"), \
                 patch.object(database, "_queue_watch_refresh_conn"):
                retired = database._reconcile_source_conn(conn, "kari", url, [x["id"] for x in crawl], True)
            self.assertEqual(retired, [])
            self.assertEqual({r[0] for r in conn.execute("SELECT id FROM products WHERE is_active=1")},
                             set(existing))
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
