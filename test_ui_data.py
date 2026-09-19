"""P04: счётчики витрины совпадают со списками при том же городе и определении (U01–U03)."""
import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import os
import tempfile
import unittest
from unittest.mock import patch

import database
from config import DB_PATH


class CountersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [patch.object(database, "DB_PATH", type(DB_PATH)(os.path.join(self.tmp.name, "prices.db"))),
                        patch("config.DATA_DIR", type(DB_PATH)(self.tmp.name))]
        for p in self.patches:
            p.start()
        database.init_db()
        rows = []
        for i, city in enumerate(["Астана"] * 6 + ["Алматы"] * 4):
            rows.append({"id": f"d{i}", "title": f"Телевизор Samsung QE{50 + i}", "price": 100000,
                         "old_price_on_site": 150000, "shop": "Sulpak", "city": city,
                         "url": f"https://s.example/{i}", "category": "ТВ"})
        # Две строки одной модели в одном магазине — витрина склеивает их в одно предложение
        rows.append(dict(rows[0], id="d0_dup", url="https://s.example/dup", title=rows[0]["title"]))
        rows.append({"id": "plain", "title": "Без скидки", "price": 1000, "shop": "Sulpak", "city": "Астана",
                     "url": "https://s.example/plain"})
        database.save_or_update_products_batch(rows)
        database.record_alert("d1", "ZERO_GLITCH", 1000000, 100000, 90.0, 900000, shop="Sulpak", city="Астана")
        database.record_alert("d7", "ZERO_GLITCH", 1000000, 100000, 90.0, 900000, shop="Sulpak", city="Алматы")
        database.invalidate_alerts_cache()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        database.invalidate_alerts_cache()
        self.tmp.cleanup()

    def test_stats_equal_lists_for_each_city(self):
        settings = database.merge_user_settings({})
        for city in ("Астана", "Алматы", None):
            with self.subTest(city=city):
                stats = database.get_stats(settings, city=city)
                deals = database.get_store_deals(city=city, deal_type="all", limit=150)
                anomalies = database.get_alerts(limit=500, city=city, alert_type="anomaly", user_settings=settings)
                self.assertEqual(stats["total_store_deals"], deals["total"])
                self.assertEqual(stats["total_discounts"], deals["total"])
                self.assertEqual(stats["total_anomalies"], len(anomalies))
                self.assertEqual(deals["shops_summary"][0]["count"], deals["total"])  # «Все» в фильтре витрины

    def test_city_scoping_and_dedup(self):
        settings = database.merge_user_settings({})
        self.assertEqual(database.get_stats(settings, city="Астана")["total_store_deals"], 6)   # дубль модели склеен
        self.assertEqual(database.get_stats(settings, city="Алматы")["total_store_deals"], 4)
        self.assertEqual(database.get_stats(settings)["city"], "Все")
        self.assertEqual(database.get_stats(settings, city="Все")["total_anomalies"], 2)
        self.assertEqual(database.get_stats(settings)["total_products"], 12)  # вся база, не город

    def test_cache_invalidated_with_alerts_cache(self):
        settings = database.merge_user_settings({})
        before = database.get_stats(settings, city="Астана")["total_store_deals"]
        database.save_or_update_products_batch([{"id": "new", "title": "Холодильник LG GA-B509", "price": 200000,
                                                 "old_price_on_site": 300000, "shop": "Sulpak", "city": "Астана",
                                                 "url": "https://s.example/new"}])
        database.invalidate_alerts_cache()
        self.assertEqual(database.get_stats(settings, city="Астана")["total_store_deals"], before + 1)


if __name__ == "__main__":
    unittest.main()
