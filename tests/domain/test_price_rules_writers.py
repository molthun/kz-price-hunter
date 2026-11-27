from tests import test_support  # noqa: F401
"""Инвариант прежней цены для всех писателей offers и offer_price_history (приёмка Codex 27.11, 6.61.0).

Прежняя цена хранится только когда обе цены конечны, текущая положительна и прежняя строго выше.
Codex на `37a47fe`: `_valid_old_price` пропускал бесконечность; писатели вне `OfferRepository`
(`domain/adapter.py`, `repositories/history_repo.py`) правило не применяли, NaN проходил адаптер.
"""
import math
import os
import sqlite3
import tempfile
import unittest

from domain.adapter import prepare_legacy_offer_v2
from domain.channel_sync import upsert_showcase_offer
from domain.models import Offer
from domain.price_rules import finite_price, valid_old_price
from repositories.history_repo import HistoryRepository
from repositories.offer_repo import OfferRepository, _valid_old_price
from repositories.schema_v2 import init_schema_v2
from scrapers.showcase_scanners import ShowcaseOffer

INF, NAN = float("inf"), float("nan")
BAD_OLD = (None, 0, -5, "", "abc", "100", INF, -INF, NAN, "inf", "nan", True, 1000, 999.0)  # при цене 1000


class PriceRuleTest(unittest.TestCase):
    def test_rule(self):
        self.assertEqual(valid_old_price(1200, 1000), 1200.0)
        self.assertEqual(valid_old_price("1200", "1000"), 1200.0)
        for old in BAD_OLD:
            with self.subTest(old=old):
                if old == "100":
                    continue
                self.assertIsNone(valid_old_price(old, 1000))
        self.assertIsNone(valid_old_price("100", 1000))  # ниже текущей
        for price in (None, 0, -1, INF, NAN, "x"):
            with self.subTest(price=price):
                self.assertIsNone(valid_old_price(1200, price))
                self.assertIsNone(finite_price(price))
        self.assertIsNone(_valid_old_price(INF, 1000))

    def test_adapter_rejects_nan_and_infinite_prices(self):
        base = {"id": "p1", "shop": "Белый Ветер", "city": "Алматы", "title": "Ноутбук Acer Aspire 5 A515",
                "url": "https://shop.kz/p1"}
        self.assertIsNone(prepare_legacy_offer_v2({**base, "price": NAN}))
        self.assertIsNone(prepare_legacy_offer_v2({**base, "price": INF}))
        self.assertIsNone(prepare_legacy_offer_v2({**base, "price": "nan"}))
        for old in (NAN, INF, "inf", 900, 1000):
            with self.subTest(old=old):
                meta = prepare_legacy_offer_v2({**base, "price": 1000, "old_price_on_site": old})
                self.assertIsNotNone(meta)
                self.assertIsNone(meta["old_price"])
        self.assertEqual(prepare_legacy_offer_v2({**base, "price": 1000, "old_price": 1500})["old_price"], 1500.0)


class _Db(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.conn = sqlite3.connect(os.path.join(self._tmp.name, "p.db"))
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        init_schema_v2(self.conn)
        self.conn.execute("INSERT INTO canonical_products (id, canonical_key, title, category, brand, model, item_kind, "
                          "description, image_url, attributes_json, created_at, updated_at) VALUES ('prod', 'k', 'Пылесос LG', "
                          "'Пылесосы', 'LG', 'VK', 'device', '', '', '{}', '2026-11-01', '2026-11-01')")
        self.conn.execute("INSERT INTO sellers (id, name, slug, domain, is_active, created_at, updated_at) VALUES "
                          "('s', 'LG', 'lg', 'lg.kz', 1, '2026-11-01', '2026-11-01')")
        self.conn.execute("INSERT INTO channels (id, seller_id, channel_type, name, external_store_id, base_url, is_active, "
                          "metadata_json, created_at) VALUES ('ch', 's', 'marketplace', 'Kaspi', '1', 'https://kaspi.kz', 1, '{}', '2026-11-01')")
        self.conn.commit()

    def _bad_rows(self, table):
        """Строки, нарушающие инвариант (бесконечность SQLite хранит как ±Inf, NaN — как NULL)."""
        return [tuple(r) for r in self.conn.execute(
            f"SELECT price, old_price FROM {table} WHERE old_price IS NOT NULL AND NOT "
            f"(old_price > price AND price > 0 AND old_price < 1e308)")]


class HistoryRepositoryTest(_Db):
    def test_record_point_and_batch_apply_rule(self):
        self.conn.execute("INSERT INTO offers (id, product_id, seller_id, channel_id, external_sku, url, price, currency, "
                          "condition, availability, city, is_active, observed_at, created_at, updated_at) VALUES "
                          "('o1', 'prod', 's', 'ch', 'x', 'u', 1000, 'KZT', 'new', 'in_stock', 'Алматы', 1, 't', 't', 't')")
        repo = HistoryRepository(self.conn)
        for i, old in enumerate((INF, 900, 1000, "abc", -1)):
            repo.record_point("o1", 1000, old, observed_at=f"2026-11-27T0{i}:00:00Z")
        repo.record_batch([{"offer_id": "o1", "price": 1000, "old_price": o, "observed_at": f"2026-11-27T1{i}:00:00Z"}
                           for i, o in enumerate((INF, 1000, 500))])
        repo.record_point("o1", 1000, 1300, observed_at="2026-11-27T20:00:00Z")
        repo.record_batch([{"offer_id": "o1", "price": 1000, "old_price": 1400, "observed_at": "2026-11-27T21:00:00Z"}])
        self.assertEqual(self._bad_rows("offer_price_history"), [])
        kept = [r[0] for r in self.conn.execute("SELECT old_price FROM offer_price_history WHERE old_price IS NOT NULL "
                                                "ORDER BY observed_at")]
        self.assertEqual(kept, [1300.0, 1400.0])


class OfferRepositoryTest(_Db):
    def test_infinite_old_price_not_stored(self):
        repo = OfferRepository(self.conn)
        off = Offer(id="o2", product_id="prod", seller_id="s", channel_id="ch", external_sku="y", url="u",
                    price=1000.0, old_price=INF, condition="new", city="Алматы")
        repo.save_or_update(off)
        repo.add_price_observation("o2", price=1000.0, old_price=INF, observed_at="2026-11-27T05:00:00Z")
        self.assertEqual(self._bad_rows("offers"), [])
        self.assertEqual(self._bad_rows("offer_price_history"), [])


class ShowcaseTest(_Db):
    def _scan(self, price, old, at):
        offer = ShowcaseOffer(marketplace="kaspi", merchant_id="m", external_sku="SKU", title="Пылесос LG",
                              url="https://kaspi.kz/p", image_url="", in_stock=True, city="Астана",
                              price=price, old_price=old)
        oid = upsert_showcase_offer(self.conn, offer, product_id="prod", seller_id="s", channel_id="ch", now_iso=at)
        return self.conn.execute("SELECT price, old_price FROM offers WHERE id = ?", (oid,)).fetchone()

    def test_fall_rise_repeat_and_non_finite(self):
        self.assertIsNone(self._scan(1000.0, None, "t1")["old_price"])
        self.assertEqual(self._scan(800.0, None, "t2")["old_price"], 1000.0)   # снижение: прежняя цена
        self.assertIsNone(self._scan(800.0, None, "t3")["old_price"])          # повтор без скидки витрины
        self.assertIsNone(self._scan(900.0, None, "t4")["old_price"])          # рост: 800 не записывается
        self.assertEqual(self._scan(900.0, 1200.0, "t5")["old_price"], 1200.0)  # скидка витрины
        self.assertIsNone(self._scan(900.0, INF, "t6")["old_price"])           # бесконечность
        self.assertIsNone(self._scan(900.0, NAN, "t7")["old_price"])
        self.assertIsNone(self._scan(900.0, 900.0, "t8")["old_price"])         # равна текущей
        self.assertEqual(self._bad_rows("offers"), [])
        self.assertEqual(self._bad_rows("offer_price_history"), [])
        prices = [r[0] for r in self.conn.execute("SELECT price FROM offer_price_history ORDER BY observed_at")]
        self.assertEqual(prices, [1000.0, 800.0, 800.0, 900.0, 900.0, 900.0, 900.0, 900.0])
        self.assertTrue(all(math.isfinite(p) for p in prices))


if __name__ == "__main__":
    unittest.main()
