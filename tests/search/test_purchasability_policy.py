"""Одно правило доступности для поиска до LIMIT, итоговой карточки и наблюдений (C07–C09, R07–R09).

Сценарии отчёта Codex 28.10.2026: stale-кандидат с числовым минимумом, нулевая цена, точная
граница TTL, выключенный канал и продавец, устаревшее наблюдение и возврат в продажу.
"""

from tests import test_support  # noqa: F401
import datetime
import sqlite3
import unittest

import database
from domain.models import CanonicalProduct, Channel, Offer, Seller
from domain.purchasability import offer_expired, offer_purchasable
from domain.search_core import SearchCore
from repositories import (ChannelRepository, OfferRepository, ProductRepository, SearchRepository,
                          SellerRepository, init_schema_v2)

NOW = datetime.datetime(2026, 10, 28, 12, 0, tzinfo=datetime.timezone.utc)
STALE = "2020-01-01T00:00:00+00:00"


class SearchCandidatesTest(unittest.TestCase):
    """Отбор до LIMIT по цене берёт только покупаемые предложения (R07, R09)."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_schema_v2(self.conn)
        SellerRepository(self.conn).save_or_update(Seller(id="s", slug="audit", name="Audit"))
        ChannelRepository(self.conn).save_or_update(
            Channel(id="c", seller_id="s", channel_type="website", name="Web"))
        self.core = SearchCore(SearchRepository(self.conn))

    def tearDown(self):
        self.conn.close()

    def add(self, n, price, **offer):
        ProductRepository(self.conn).save_or_update(
            CanonicalProduct(id=f"p{n}", canonical_key=f"k{n}", title="Audit Laptop", category="laptops"))
        values = dict(id=f"o{n}", product_id=f"p{n}", seller_id="s", channel_id="c", external_sku=str(n),
                      url="https://example.invalid", price=price, observed_at=NOW.isoformat())
        values.update(offer)
        OfferRepository(self.conn).save_or_update(Offer(**values))

    def first(self, limit):
        res = self.core.search("Audit Laptop", sort_by="price_asc", limit=limit, now=NOW)
        return [(item.product.id, item.min_price) for item in res.items]

    def assert_available_wins(self):
        self.assertEqual(self.first(1), [("p1", 100.0)])
        self.assertEqual(self.first(50)[0], ("p1", 100.0))

    def test_stale_only_candidate_does_not_displace_available(self):
        self.add(0, 1, observed_at=STALE)
        self.add(1, 100)
        self.assert_available_wins()

    def test_zero_price_candidate_does_not_displace_available(self):
        self.add(0, 0)
        self.add(1, 100)
        self.assert_available_wins()

    def test_out_of_stock_and_expired_candidates_do_not_displace_available(self):
        self.add(0, 1, availability="out_of_stock")
        self.add(2, 2, expires_at=(NOW - datetime.timedelta(hours=1)).isoformat())
        self.add(1, 100)
        self.assert_available_wins()

    def test_exact_ttl_boundary_agrees_between_sql_and_python(self):
        self.add(0, 1, expires_at=NOW.isoformat())
        self.add(1, 100)
        self.assertTrue(offer_expired(NOW.isoformat(), NOW))
        rows = SearchRepository(self.conn).search_products("Audit Laptop", sort_by="price_asc", limit=1, now=NOW)
        self.assertEqual(rows[0]["product"].id, "p1")
        # За секунду до срока предложение ещё покупаемо и выигрывает по цене
        before = NOW - datetime.timedelta(seconds=1)
        rows = SearchRepository(self.conn).search_products("Audit Laptop", sort_by="price_asc", limit=1, now=before)
        self.assertEqual(rows[0]["product"].id, "p0")

    def test_empty_observed_at_falls_back_to_stale_updated_at(self):
        # F02: пустое время наблюдения — смотрим updated_at, как Python
        self.add(0, 1, observed_at="")
        self.conn.execute("UPDATE offers SET observed_at = '', updated_at = ? WHERE id = 'o0'", (STALE,))
        self.add(1, 100)
        self.assert_available_wins()

    def test_mixed_offers_of_one_product_use_purchasable_minimum(self):
        self.add(0, 500)
        OfferRepository(self.conn).save_or_update(Offer(
            id="o0-stale", product_id="p0", seller_id="s", channel_id="c", external_sku="0s",
            url="https://example.invalid", price=1, observed_at=STALE))
        self.add(1, 100)
        self.assertEqual(self.first(1), [("p1", 100.0)])
        self.assertEqual(dict(self.first(50))["p0"], 500.0)


class SqlPythonAgreementTest(unittest.TestCase):
    """F02: SQL-условия и Python-правило дают один ответ на одних и тех же сохранённых строках."""

    CASES = [
        # (observed_at, updated_at, expires_at)
        ("", STALE, None),                                              # пусто → updated_at (stale)
        ("", NOW.isoformat(), None),                                    # пусто → updated_at (fresh)
        ((NOW - datetime.timedelta(hours=71)).astimezone(datetime.timezone(datetime.timedelta(hours=5))).isoformat(),
         STALE, None),                                                  # смещение +05:00, ещё aging
        ((NOW - datetime.timedelta(hours=73)).astimezone(datetime.timezone(datetime.timedelta(hours=5))).isoformat(),
         NOW.isoformat(), None),                                        # смещение +05:00, уже stale
        ("not-a-date", STALE, None),                                    # нечитаемое — неизвестно, не stale
        (NOW.isoformat(), NOW.isoformat(), ""),                         # пустой TTL
        (NOW.isoformat(), NOW.isoformat(), (NOW + datetime.timedelta(seconds=1)).isoformat()),
        (NOW.isoformat(), NOW.isoformat(), NOW.astimezone(datetime.timezone(datetime.timedelta(hours=5))).isoformat()),
    ]

    def test_sql_matches_python_on_stored_rows(self):
        from domain.purchasability import sql_purchasable_conditions
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        init_schema_v2(conn)
        SellerRepository(conn).save_or_update(Seller(id="s", slug="a", name="A"))
        ChannelRepository(conn).save_or_update(Channel(id="c", seller_id="s", channel_type="website", name="W"))
        ProductRepository(conn).save_or_update(CanonicalProduct(id="p", canonical_key="k", title="T", category="x"))
        conditions, params = sql_purchasable_conditions(NOW)
        for n, (observed, updated, expires) in enumerate(self.CASES):
            with self.subTest(observed=observed, updated=updated, expires=expires):
                OfferRepository(conn).save_or_update(Offer(
                    id=f"o{n}", product_id="p", seller_id="s", channel_id="c", external_sku=str(n),
                    url="https://example.invalid", price=10, observed_at=observed or NOW.isoformat()))
                conn.execute("UPDATE offers SET observed_at = ?, updated_at = ?, expires_at = ? WHERE id = ?",
                             (observed, updated, expires, f"o{n}"))
                row = dict(conn.execute("SELECT * FROM offers WHERE id = ?", (f"o{n}",)).fetchone())
                in_sql = conn.execute(f"SELECT 1 FROM offers o WHERE o.id = ? AND {' AND '.join(conditions)}",
                                      [f"o{n}", *params]).fetchone() is not None
                self.assertEqual(in_sql, offer_purchasable(row, NOW))
        conn.close()


class PolicyUnitTest(unittest.TestCase):
    def test_each_rule_alone_makes_offer_unpurchasable(self):
        base = {"price": 100, "availability": "in_stock", "observed_at": NOW.isoformat(),
                "is_active": 1, "channel_is_active": 1, "seller_is_active": 1}
        self.assertTrue(offer_purchasable(base, NOW))
        for change in ({"price": 0}, {"availability": "out_of_stock"}, {"expires_at": NOW.isoformat()},
                       {"is_active": 0}, {"channel_is_active": 0}, {"seller_is_active": 0},
                       {"observed_at": STALE}):
            with self.subTest(change=change):
                self.assertFalse(offer_purchasable({**base, **change}, NOW))


class WatchedOffersTest(unittest.TestCase):
    """Наблюдения видят доступность по тому же правилу (R08)."""

    USER = 987654322

    @classmethod
    def setUpClass(cls):
        database.init_db()
        database.upsert_telegram_user({"id": cls.USER, "first_name": "Policy"})

    def setUp(self):
        with database.get_connection() as conn:
            conn.execute("DELETE FROM watch_events")
            conn.execute("DELETE FROM watch_state")
            conn.execute("DELETE FROM watches")
            conn.commit()

    def offer(self, key, seller_active=True, channel_active=True, observed_at=None, availability="in_stock"):
        with database.get_connection() as conn:
            SellerRepository(conn).save_or_update(Seller(id=f"ws-{key}", slug=f"w-{key}", name=f"Watch {key}",
                                                         is_active=seller_active))
            ChannelRepository(conn).save_or_update(Channel(id=f"wc-{key}", seller_id=f"ws-{key}",
                                                           channel_type="website", name="Web",
                                                           is_active=channel_active))
            ProductRepository(conn).save_or_update(CanonicalProduct(id=f"wp-{key}", canonical_key=f"watch-{key}",
                                                                    title=f"Watch {key} Laptop", category="laptops"))
            OfferRepository(conn).save_or_update(Offer(
                id=f"off_{key}", product_id=f"wp-{key}", seller_id=f"ws-{key}", channel_id=f"wc-{key}",
                external_sku=key, url="https://example.invalid", price=50, availability=availability,
                observed_at=observed_at or datetime.datetime.now(datetime.timezone.utc).isoformat()))
            conn.commit()
        return database.create_watch(self.USER, {"kind": "product", "target": f"wp-{key}",
                                                 "condition": "target_price", "threshold": 100,
                                                 "quiet_from": "", "quiet_to": "", "cooldown_hours": 0})

    def check(self, key):
        rows = database.watched_offers([f"off_{key}"])
        return rows[0]["is_available"], database.evaluate_watches(rows)

    def test_stale_observation_does_not_notify(self):
        self.offer("stale", observed_at=STALE)
        self.assertEqual(self.check("stale"), (0, 0))

    def test_disabled_channel_does_not_notify(self):
        self.offer("nochannel", channel_active=False)
        self.assertEqual(self.check("nochannel"), (0, 0))

    def test_disabled_seller_does_not_notify(self):
        self.offer("noseller", seller_active=False)
        self.assertEqual(self.check("noseller"), (0, 0))

    def test_available_offer_notifies(self):
        self.offer("ok")
        self.assertEqual(self.check("ok"), (1, 1))

    def test_back_in_stock_after_channel_reenabled(self):
        self.offer("back", channel_active=False)
        database.create_watch(self.USER, {"kind": "product", "target": "wp-back", "condition": "back_in_stock",
                                          "quiet_from": "", "quiet_to": "", "cooldown_hours": 0})
        self.assertEqual(self.check("back"), (0, 0))
        with database.get_connection() as conn:
            conn.execute("UPDATE channels SET is_active = 1 WHERE id = 'wc-back'")
            conn.commit()
        available, events = self.check("back")
        self.assertEqual(available, 1)
        reasons = sorted(e["reason"] for e in database.watch_events(self.USER))
        self.assertEqual(events, 2)  # цель по цене и возврат в продажу
        self.assertIn("товар снова в продаже", reasons)


if __name__ == "__main__":
    unittest.main()
