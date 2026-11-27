from tests import test_support  # noqa: F401
"""Названия с HTML-сущностями в уже записанных товарах и правка данных (приёмка Codex 27.11, 6.62.0).

Codex на `37a47fe`: для существующего товара `Monitor 27&#34;` вызов `get_or_create_canonical()`
не менял `canonical_products.title`. Здесь — обновление строки и поискового индекса, сохранение ID,
ключа склейки, предложений, цен и истории, витрина с резервным ключом (хэш названия) и
повторяемая правка старых данных `scripts/single_live/fix_catalog_integrity.py`.
"""
import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database
from domain.channel_sync import match_or_create_canonical_product
from repositories.product_repo import ProductRepository
from repositories.schema_v2 import init_schema_v2
from scrapers.showcase_scanners import ShowcaseOffer

RAW = "Монитор 27&#34; LG UltraGear 27GR93U-B"
CLEAN = 'Монитор 27" LG UltraGear 27GR93U-B'
ROOT = Path(__file__).resolve().parents[2]


def _load_fix():
    spec = importlib.util.spec_from_file_location("fix_catalog_integrity",
                                                  ROOT / "scripts/single_live/fix_catalog_integrity.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Db(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "t.db"
        p = patch.object(database, "DB_PATH", self.db_path)
        p.start()
        self.addCleanup(p.stop)
        database.init_db()
        with database.get_connection() as conn:
            init_schema_v2(conn)
            conn.commit()

    def conn(self):
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        self.addCleanup(c.close)
        return c

    def _fts(self, c, term):
        return [r[0] for r in c.execute(
            "SELECT cp.id FROM canonical_products_fts f JOIN canonical_products cp ON cp.rowid = f.rowid "
            "WHERE canonical_products_fts MATCH ?", (term,))]

    def _seed_raw_product(self, c, pid="prod_mon", key="lg 27gr93u-b"):
        c.execute("INSERT INTO canonical_products (id, canonical_key, title, category, item_kind, description, image_url, "
                  "attributes_json, created_at, updated_at) VALUES (?, ?, ?, 'Мониторы', 'device', '', '', '{}', 't', 't')",
                  (pid, key, RAW))
        c.execute("INSERT INTO sellers (id, name, slug, domain, is_active, created_at, updated_at) VALUES "
                  "('s', 'Shop', 'shop', 'shop.kz', 1, 't', 't')")
        c.execute("INSERT INTO channels (id, seller_id, channel_type, name, external_store_id, base_url, is_active, "
                  "metadata_json, created_at) VALUES ('ch', 's', 'website', 'Shop', '1', 'https://shop.kz', 1, '{}', 't')")
        c.execute("INSERT INTO offers (id, product_id, seller_id, channel_id, external_sku, url, price, old_price, currency, "
                  "condition, availability, city, is_active, observed_at, created_at, updated_at) VALUES "
                  "('o1', ?, 's', 'ch', 'x', 'u', 150000, 120000, 'KZT', 'new', 'in_stock', 'Алматы', 1, 't', 't', 't')", (pid,))
        c.executemany("INSERT INTO offer_price_history (offer_id, price, old_price, observed_at, product_id) VALUES (?, ?, ?, ?, ?)",
                      [("o1", 160000, 180000, "t1", pid), ("o1", 150000, 120000, "t2", pid),
                       ("o1", 150000, float("inf"), "t3", pid), ("o1", 150000, None, "t4", pid)])
        c.commit()


class ExistingTitleTest(_Db):
    def test_get_or_create_updates_existing_row_and_index_keeping_identity(self):
        c = self.conn()
        self._seed_raw_product(c)
        self.assertEqual(self._fts(c, '"34"'), ["prod_mon"])
        product = ProductRepository(c).get_or_create_canonical("lg 27gr93u-b", RAW, "Мониторы")
        c.commit()
        self.assertEqual((product.id, product.canonical_key, product.title), ("prod_mon", "lg 27gr93u-b", CLEAN))
        row = c.execute("SELECT id, canonical_key, title FROM canonical_products").fetchall()
        self.assertEqual([tuple(r) for r in row], [("prod_mon", "lg 27gr93u-b", CLEAN)])
        self.assertEqual(self._fts(c, '"34"'), [])
        self.assertEqual(self._fts(c, "UltraGear"), ["prod_mon"])
        self.assertEqual(c.execute("SELECT COUNT(*) FROM offers WHERE product_id = 'prod_mon'").fetchone()[0], 1)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM offer_price_history").fetchone()[0], 4)
        # Повторный вызов ничего не меняет
        before = c.execute("SELECT updated_at FROM canonical_products").fetchone()[0]
        ProductRepository(c).get_or_create_canonical("lg 27gr93u-b", CLEAN, "Мониторы")
        self.assertEqual(c.execute("SELECT updated_at FROM canonical_products").fetchone()[0], before)

    def test_showcase_with_fallback_key_finds_the_same_product(self):
        # Название без модели: ключ — хэш названия витрины. После 6.62.1 он считается от исходного
        # названия, поэтому тот же товар витрины не отделяется от уже записанного
        c = self.conn()
        raw = "Набор посуды &quot;Домашний&quot; 12 предметов"
        offer = ShowcaseOffer(marketplace="kaspi", merchant_id="m", external_sku="1", title=raw, url="https://kaspi.kz/p",
                              image_url="", in_stock=True, city="Алматы", price=10000.0, old_price=None)
        pid, created = match_or_create_canonical_product(c, offer, "t1")
        self.assertTrue(created)
        self.assertEqual(c.execute("SELECT title FROM canonical_products WHERE id = ?", (pid,)).fetchone()[0],
                         'Набор посуды "Домашний" 12 предметов')
        self.assertEqual(match_or_create_canonical_product(c, offer, "t2"), (pid, False))
        # Товар, записанный до правки с сущностью в названии, тоже находится
        c.execute("UPDATE canonical_products SET title = ? WHERE id = ?", (raw, pid))
        self.assertEqual(match_or_create_canonical_product(c, offer, "t3"), (pid, False))
        self.assertEqual(c.execute("SELECT COUNT(*) FROM canonical_products").fetchone()[0], 1)

    def test_legacy_save_stores_readable_title_and_same_key(self):
        res = database.save_or_update_product({"id": "kaspi_1", "shop": "Kaspi Магазин", "city": "Алматы",
                                               "title": RAW, "price": 150000, "url": "https://kaspi.kz/shop/p/1"})
        self.assertTrue(res["is_new"])
        c = self.conn()
        legacy = c.execute("SELECT title, canonical_key FROM products WHERE id = 'kaspi_1'").fetchone()
        self.assertEqual(legacy["title"], CLEAN)
        from model_matching import extract_canonical_key
        self.assertEqual(legacy["canonical_key"], extract_canonical_key(RAW))
        cp = c.execute("SELECT title FROM canonical_products WHERE canonical_key = ?", (legacy["canonical_key"],)).fetchone()
        self.assertEqual(cp["title"], CLEAN)

    def test_legacy_save_survives_infinite_price(self):
        res = database.save_or_update_product({"id": "x", "title": "Товар", "price": float("inf"), "url": "u"})
        self.assertFalse(res["is_new"])
        self.assertEqual(res["current_price"], 0)


class DataFixTest(_Db):
    def test_fix_counts_applies_and_is_idempotent(self):
        fix = _load_fix()
        c = self.conn()
        self._seed_raw_product(c)
        c.execute("INSERT INTO products (id, shop, city, title, category, url, current_price, first_seen_price, min_price, "
                  "max_price, created_at, updated_at) VALUES ('leg1', 'Forte Market', 'Алматы', ?, '', 'u', 1, 1, 1, 1, 't', 't')",
                  ("Клей &amp; герметик 0&#43;",))
        c.commit()
        c.close()
        dry = fix.run(str(self.db_path))
        self.assertEqual(dry["old_price"], {"offers": 1, "offer_price_history": 2})
        self.assertEqual(dry["titles"], {"canonical_products": 1, "products": 1})
        c = self.conn()
        self.assertEqual(c.execute("SELECT title FROM canonical_products").fetchone()[0], RAW)  # без --apply ничего

        applied = fix.run(str(self.db_path), apply=True)
        self.assertEqual(applied["old_price"], dry["old_price"])
        c = self.conn()
        self.assertEqual(c.execute("SELECT id, canonical_key, title FROM canonical_products").fetchone()[:],
                         ("prod_mon", "lg 27gr93u-b", CLEAN))
        self.assertEqual(c.execute("SELECT title FROM products WHERE id = 'leg1'").fetchone()[0], "Клей & герметик 0+")
        self.assertEqual(self._fts(c, '"34"'), [])
        self.assertEqual(self._fts(c, "UltraGear"), ["prod_mon"])
        self.assertEqual([r[0] for r in c.execute("SELECT id FROM products_fts WHERE products_fts MATCH 'герметик'")],
                         ["leg1"])
        hist = [tuple(r) for r in c.execute("SELECT observed_at, price, old_price FROM offer_price_history ORDER BY observed_at")]
        self.assertEqual(hist, [("t1", 160000.0, 180000.0), ("t2", 150000.0, None), ("t3", 150000.0, None),
                                ("t4", 150000.0, None)])
        self.assertEqual(tuple(c.execute("SELECT price, old_price FROM offers").fetchone()), (150000.0, None))

        again = fix.run(str(self.db_path), apply=True)
        self.assertEqual(again["old_price"], {"offers": 0, "offer_price_history": 0})
        self.assertEqual(again["titles"], {"canonical_products": 0, "products": 0})

    def test_fix_rejects_missing_path_and_empty_db_without_creating_files(self):
        import subprocess
        import sys as _sys

        fix = _load_fix()
        missing = Path(self._tmp.name) / "does_not_exist.db"
        for apply in (False, True):
            with self.assertRaises(FileNotFoundError):
                fix.run(str(missing), apply=apply)
            self.assertFalse(missing.exists(), "скрипт не должен создавать файл по несуществующему пути")

        proc = subprocess.run(
            [_sys.executable, str(ROOT / "scripts/single_live/fix_catalog_integrity.py"), str(missing)],
            capture_output=True, text=True,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse(missing.exists())

        empty_db = Path(self._tmp.name) / "empty.db"
        sqlite3.connect(empty_db).close()
        with self.assertRaises(ValueError):
            fix.run(str(empty_db), apply=False)

    def test_fix_dry_run_succeeds_read_only_under_active_wal_writer(self):
        fix = _load_fix()
        c = self.conn()
        c.execute("PRAGMA journal_mode=WAL")
        self._seed_raw_product(c)
        c.close()

        writer = sqlite3.connect(self.db_path, isolation_level=None)
        self.addCleanup(writer.close)
        writer.execute("BEGIN IMMEDIATE")
        try:
            dry = fix.run(str(self.db_path), apply=False)
            self.assertEqual(dry["old_price"], {"offers": 1, "offer_price_history": 2})
            self.assertEqual(dry["titles"], {"canonical_products": 1, "products": 0})
        finally:
            writer.execute("ROLLBACK")

    def test_fix_agrees_with_valid_old_price_at_1_5e308_boundary(self):
        from domain.price_rules import valid_old_price

        fix = _load_fix()
        c = self.conn()
        self._seed_raw_product(c)
        # Конечное 1.5e308 при текущей 100 принимается valid_old_price — скрипт не должен считать его ошибкой
        self.assertEqual(valid_old_price(1.5e308, 100.0), 1.5e308)
        c.execute(
            "INSERT INTO offers (id, product_id, seller_id, channel_id, external_sku, url, price, old_price, currency, "
            "condition, availability, city, is_active, observed_at, created_at, updated_at) VALUES "
            "('o_big', 'prod_mon', 's', 'ch', 'big', 'u2', 100.0, 1.5e308, 'KZT', 'new', 'in_stock', 'Алматы', 1, 't', 't', 't')"
        )
        c.execute(
            "INSERT INTO offer_price_history (offer_id, price, old_price, observed_at, product_id) "
            "VALUES ('o_big', 100.0, 1.5e308, 't5', 'prod_mon')"
        )
        c.commit()
        c.close()

        dry = fix.run(str(self.db_path), apply=False)
        self.assertEqual(dry["old_price"], {"offers": 1, "offer_price_history": 2})
        fix.run(str(self.db_path), apply=True)

        c = self.conn()
        self.assertEqual(c.execute("SELECT old_price FROM offers WHERE id = 'o_big'").fetchone()[0], 1.5e308)
        self.assertEqual(
            c.execute("SELECT old_price FROM offer_price_history WHERE offer_id = 'o_big'").fetchone()[0], 1.5e308
        )


if __name__ == "__main__":
    unittest.main()
