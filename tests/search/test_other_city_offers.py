from tests import test_support  # noqa: F401
"""Тесты R1-03: предложения из других городов РК (other_city_offers) в карточке товара."""
import datetime
import unittest

import database
from domain.search_core import SearchCore
from repositories.search_repo import SearchRepository


class OtherCityOffersTest(unittest.TestCase):
    def setUp(self):
        database.init_db()
        self.conn = database.get_connection()
        self.addCleanup(self.conn.close)
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.prod_id = "prod_r103_radiator"
        self.conn.execute("DELETE FROM offers WHERE product_id = ?", (self.prod_id,))
        self.conn.execute("DELETE FROM canonical_products WHERE id = ?", (self.prod_id,))
        self.conn.execute(
            "INSERT INTO canonical_products (id, canonical_key, title, category, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (self.prod_id, "k2:uno:alyurad500100", "Радиатор UNO Алюрад 500/100", "diy", now, now),
        )
        for sid, name, slug, dom in (
            ("sel_12m", "12 Месяцев", "twelve_months", "12.kz"),
            ("sel_magnum", "Магнум", "magnum", "magnum.kz"),
            ("sel_td", "Технодом", "technodom", "technodom.kz"),
        ):
            self.conn.execute(
                "INSERT OR REPLACE INTO sellers (id, name, slug, domain, is_active, created_at, updated_at) VALUES (?, ?, ?, ?, 1, ?, ?)",
                (sid, name, slug, dom, now, now),
            )
            self.conn.execute(
                "INSERT OR REPLACE INTO channels (id, seller_id, channel_type, name, base_url, is_active, created_at) VALUES (?, ?, 'website', ?, ?, 1, ?)",
                (f"ch_{sid}", sid, name, f"https://{dom}", now),
            )
        self.now = now
        self.conn.commit()

    def tearDown(self):
        pass

    def _add_offer(self, oid, seller_id, city, price, url="https://12.kz/catalog/rad/"):
        self.conn.execute(
            """INSERT OR REPLACE INTO offers
               (id, product_id, seller_id, channel_id, external_sku, url, price, condition, availability, city, observed_at, is_active, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'new', 'in_stock', ?, ?, 1, ?, ?)""",
            (oid, self.prod_id, seller_id, f"ch_{seller_id}", oid, url, price, city, self.now, self.now, self.now),
        )
        self.conn.commit()

    def test_cheaper_delivery_kz_offer_in_other_city_returned_separately(self):
        # В Астане 35 000 ₸, в Алматы у 12 Месяцев (delivery_kz=True) — 27 300 ₸
        self._add_offer("off_12m_ast", "sel_12m", "Астана", 35000)
        self._add_offer("off_12m_alm", "sel_12m", "Алматы", 27300)
        # Магнум (delivery_kz=False) в Алматы за 15 000 ₸ — НЕ должен попадать в доставку по РК!
        self._add_offer("off_mag_alm", "sel_magnum", "Алматы", 15000, "https://magnum.kz/p/1")
        # Технодом в Алматы дороже (40 000 ₸ > 35 000 ₸) — НЕ должен попадать в «выгоднее»
        self._add_offer("off_td_alm", "sel_td", "Алматы", 40000, "https://www.technodom.kz/almaty/p/1")

        core = SearchCore(SearchRepository(self.conn))
        res = core.get_canonical_product(self.prod_id, city="Астана")
        self.assertIsNotNone(res)
        # Основная цена и offers — строго Астана (не смешаны с Алматы!)
        self.assertEqual(res["min_price"], 35000)
        self.assertEqual(res["offers_count"], 1)
        self.assertEqual(res["offers"][0]["city"], "Астана")

        # other_city_offers содержит только 12 Месяцев (Алматы) с честной экономией 7 700 ₸
        other = res["other_city_offers"]
        self.assertEqual(len(other), 1)
        self.assertEqual(other[0]["seller_name"], "12 Месяцев")
        self.assertEqual(other[0]["city"], "Алматы")
        self.assertEqual(other[0]["price"], 27300)
        self.assertEqual(other[0]["savings_kzt"], 7700)
        self.assertTrue(other[0]["delivery_kz"])

    def test_other_city_offers_shown_when_selected_city_has_no_offers(self):
        self._add_offer("off_12m_alm", "sel_12m", "Алматы", 27300)
        core = SearchCore(SearchRepository(self.conn))
        res = core.get_canonical_product(self.prod_id, city="Астана")
        self.assertEqual(res["offers_count"], 0)
        self.assertIsNone(res["min_price"])
        self.assertEqual(len(res["other_city_offers"]), 1)
        self.assertEqual(res["other_city_offers"][0]["price"], 27300)
        self.assertIsNone(res["other_city_offers"][0]["savings_kzt"])


if __name__ == "__main__":
    unittest.main()
