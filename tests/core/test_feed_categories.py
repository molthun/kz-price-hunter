from tests import test_support  # must be first
import sqlite3
import unittest

from domain.category import MASTER_TAXONOMY
from catalog_classifier import CatalogClassifier
from domain.feed_categories import SHOP_FEEDS, apply_feed_categories, apply_section_categories, section_category
from repositories.schema_v2 import DDL_V2


class FeedCategoriesTest(unittest.TestCase):
    def test_section_names(self):
        for raw, expected in (
            ("Мечта: 💨 Утюги и отпариватели", "appliances"),
            ("Forte: 🛠 Строительство и ремонт", "tools_repair"),
            ("Europharma: 🦠 Антибиотики", "medicine"),
            ("ITMag: 🗄 Корпуса", "pc_components"),
            ("Sulpak: 🔥 Распродажа", None),
            ("Flip: Электроника", None),
            ("MasterOK: 🔩 Запчасти", None),
            ("", None),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(section_category(raw), expected)

    def test_rules_beat_section_and_broad_section_keeps_exact(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.executescript(DDL_V2)
        conn.execute("CREATE TABLE IF NOT EXISTS products (id TEXT, shop TEXT, category TEXT, is_active INTEGER)")
        conn.execute("INSERT INTO sellers (id, slug, name, created_at, updated_at) VALUES ('s', 's', 'S', 'x', 'x')")
        conn.execute("INSERT INTO channels (id, seller_id, channel_type, name, created_at) VALUES ('c', 's', 'w', 'W', 'x')")
        for pid, title, raw, stored in (
            ("glass", "Защитное стекло для iPhone 15", "Forte: 📱 Смартфоны", "Смартфоны"),
            ("mem", "Kingston FURY 16GB", "ITMag: 🧠 Оперативная память", "SSD накопители"),
            ("cream", "Маска для волос Garnier", "Arbuz: 🧴 Личная гигиена и косметика", "Уход за волосами"),
            ("ai", "Непонятный товар", "Sulpak: 🔥 Распродажа", "Дом и сад"),
        ):
            conn.execute("INSERT INTO products VALUES (?, 'X', ?, 1)", (pid, raw))
            conn.execute("INSERT INTO canonical_products (id, canonical_key, title, category, created_at, updated_at) "
                         "VALUES (?, ?, ?, ?, 'x', 'x')", (pid, pid, title, stored))
            conn.execute("INSERT INTO offers (id, product_id, seller_id, channel_id, external_sku, url, price, currency, condition, "
                         "availability, city, is_active, observed_at, created_at, updated_at) VALUES "
                         "(?, ?, 's', 'c', ?, 'u', 1, 'KZT', 'NEW', 'in_stock', 'A', 1, 'x', 'x', 'x')", ("off_" + pid, pid, pid))
        apply_section_categories(conn, CatalogClassifier())
        got = dict(conn.execute("SELECT id, category FROM canonical_products").fetchall())
        self.assertEqual(got["glass"], "Защитные стекла и пленки")
        self.assertEqual(got["mem"], "Оперативная память")
        self.assertEqual(got["cream"], "Уход за волосами")
        self.assertEqual(got["ai"], "Дом и сад")
        pinned = {r[0] for r in conn.execute("SELECT product_id FROM category_ai_verdicts")}
        self.assertNotIn("ai", pinned)                  # без решения товар остаётся в очереди модели

    def test_table_uses_known_categories(self):
        ids = {c.id for c in MASTER_TAXONOMY}
        for shop, table in SHOP_FEEDS.items():
            self.assertLessEqual(set(table.values()), ids)

    def test_feed_beats_model_and_is_stable(self):
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        conn.executescript(DDL_V2)
        conn.execute("CREATE TABLE IF NOT EXISTS products (id TEXT, shop TEXT, category TEXT, is_active INTEGER)")
        conn.execute("INSERT INTO products VALUES ('p', 'Белый Ветер', 'Товары для сада и огорода', 1)")
        conn.execute("INSERT INTO sellers (id, slug, name, created_at, updated_at) VALUES ('s', 's', 'S', 'x', 'x')")
        conn.execute("INSERT INTO channels (id, seller_id, channel_type, name, created_at) VALUES ('c', 's', 'w', 'W', 'x')")
        conn.execute("INSERT INTO canonical_products (id, canonical_key, title, category, created_at, updated_at) "
                     "VALUES ('x', 'x', 'Шина для пил', 'Шины и диски', 'x', 'x')")
        conn.execute("INSERT INTO offers (id, product_id, seller_id, channel_id, external_sku, url, price, currency, condition, "
                     "availability, city, is_active, observed_at, created_at, updated_at) VALUES "
                     "('off_p', 'x', 's', 'c', 'p', 'u', 1, 'KZT', 'NEW', 'in_stock', 'A', 1, 'x', 'x', 'x')")
        conn.execute("INSERT INTO category_ai_verdicts VALUES ('x', 'a', 'Шины и диски', 0.95, 'qwen', 1, 'x')")
        self.assertEqual(apply_feed_categories(conn), 1)
        self.assertEqual(conn.execute("SELECT category FROM canonical_products").fetchone()[0], "Сад и огород")
        self.assertEqual(conn.execute("SELECT model FROM category_ai_verdicts").fetchone()[0], "feed")
        self.assertEqual(apply_feed_categories(conn), 0)


if __name__ == "__main__":
    unittest.main()
