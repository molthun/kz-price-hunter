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


class OtherCityOffersAcceptanceR103Test(OtherCityOffersTest):
    """Регрессии приёмки 6.42.0 (R103-A01…A03)."""

    def _set(self, oid, **cols):
        for col, val in cols.items():
            self.conn.execute(f"UPDATE offers SET {col} = ? WHERE id = ?", (val, oid))
        self.conn.commit()

    def _other(self, city="Астана"):
        res = SearchCore(SearchRepository(self.conn)).get_canonical_product(self.prod_id, city=city)
        return res, res["other_city_offers"]

    def _ago(self, **delta):
        return (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(**delta)).isoformat()

    # R103-A01: единые правила покупки и свежести
    def test_unpurchasable_candidates_are_not_advertised(self):
        cases = {
            "stale": {"observed_at": self._ago(days=30)},
            "expired": {"expires_at": self._ago(hours=1)},
            "unknown_availability": {"availability": "unknown"},
            "out_of_stock": {"availability": "out_of_stock"},
        }
        for name, cols in cases.items():
            with self.subTest(case=name):
                self._add_offer("off_local", "sel_12m", "Астана", 35000)
                self._add_offer("off_cand", "sel_12m", "Алматы", 27300)
                self._set("off_cand", **cols)
                res, other = self._other()
                self.assertEqual(other, [])
                self.assertEqual(res["min_price"], 35000)

    def test_unpurchasable_candidates_hidden_without_local_offers(self):
        self._add_offer("off_cand", "sel_12m", "Алматы", 27300)
        self._set("off_cand", observed_at=self._ago(days=30))
        res, other = self._other()
        self.assertIsNone(res["min_price"])
        self.assertEqual(other, [])

    def test_freshness_falls_back_from_observed_at_to_updated_at(self):
        self._add_offer("off_local", "sel_12m", "Астана", 35000)
        self._add_offer("off_cand", "sel_12m", "Алматы", 27300)
        self._set("off_cand", observed_at="", updated_at=self._ago(days=30))
        self.assertEqual(self._other()[1], [])
        self._set("off_cand", updated_at=self._ago(hours=1))
        other = self._other()[1]
        self.assertEqual([o["price"] for o in other], [27300])
        self.assertTrue(other[0]["observed_at"])
        self.assertTrue(other[0]["freshness"])

    # R103-A02: только подтверждённый известный город РК, отличный от выбранного
    def test_only_confirmed_other_kz_city_is_offered(self):
        self._add_offer("off_local", "sel_12m", "Астана", 35000)
        for i, city in enumerate(("Регион не подтверждён", "Где-то в РК")):
            self._add_offer(f"off_bad_{i}", "sel_12m", city, 20000 + i)
        # общеказахстанские метки входят в основную выдачу города, а не в «другой город»
        for i, city in enumerate(("Казахстан", "Астана / Казахстан", "Все регионы", "Все")):
            self._add_offer(f"off_kz_{i}", "sel_12m", city, 40000 + i)
        # второе предложение выбранного города — часть основной выдачи, не «другой город»
        self._add_offer("off_local_td", "sel_td", "Астана", 36000, "https://www.technodom.kz/astana/p/1")
        res, other = self._other()
        self.assertEqual(other, [])
        self.assertEqual(res["min_price"], 35000)
        self._add_offer("off_alm", "sel_12m", "Алматы", 27300)
        self.assertEqual([(o["city"], o["price"]) for o in self._other()[1]], [("Алматы", 27300)])

    # R103-A03: неподходящие строки не вытесняют подходящее предложение
    def test_ineligible_rows_do_not_fill_the_limit(self):
        self._add_offer("off_local", "sel_12m", "Астана", 35000)
        for i in range(30):
            self._add_offer(f"off_mag_{i}", "sel_magnum", "Алматы", 10000 + i, "https://magnum.kz/p/1")
        for i in range(30):
            self._add_offer(f"off_old_{i}", "sel_td", "Алматы", 11000 + i, "https://www.technodom.kz/almaty/p/1")
            self._set(f"off_old_{i}", expires_at=self._ago(hours=1))
        for i in range(60):
            self._add_offer(f"off_dup_{i}", "sel_td", "Алматы", 12000 + i, "https://www.technodom.kz/almaty/p/2")
        self._add_offer("off_12m_alm", "sel_12m", "Алматы", 27300)
        res, other = self._other()
        self.assertEqual(res["min_price"], 35000)
        self.assertEqual(res["offers_count"], 1)
        self.assertEqual([(o["seller_name"], o["price"]) for o in other],
                         [("Технодом", 12000), ("12 Месяцев", 27300)])


if __name__ == "__main__":
    unittest.main()
