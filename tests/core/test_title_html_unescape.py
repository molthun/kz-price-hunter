from tests import test_support  # noqa: F401
import sqlite3
import unittest

from domain.models import CanonicalProduct
from repositories import init_schema_v2
from repositories.product_repo import ProductRepository
from scrapers.base import clean_product_title, validate_product_item


class TitleHtmlUnescapeTest(unittest.TestCase):
    def test_clean_product_title_unescapes_entities_and_normalizes_spaces(self):
        cases = {
            "Монитор 27&#34; LG 27GR93U-B черный": 'Монитор 27" LG 27GR93U-B черный',
            "BIBS Набор De Lux Latex 0&#43; месяцев": "BIBS Набор De Lux Latex 0+ месяцев",
            "ThinkPad T14, 14&quot; WUXGA": 'ThinkPad T14, 14" WUXGA',
            "Bluetooth гарнитура Bang &amp; Olufsen Beoplay H95": "Bluetooth гарнитура Bang & Olufsen Beoplay H95",
            "Отпариватель  &nbsp;Centek CT-2370": "Отпариватель Centek CT-2370",
            "Монитор 32&amp;#34; MSI": 'Монитор 32" MSI',
            "Обычный смартфон Apple iPhone 15 Pro": "Обычный смартфон Apple iPhone 15 Pro",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(clean_product_title(raw), expected)

    def test_validate_product_item_cleans_title(self):
        item = {
            "id": "fm_1",
            "title": "Монитор 27&#34; LG &amp; Co  &nbsp;Pro",
            "price": 150000,
            "url": "https://market.forte.kz/items/1",
        }
        cleaned = validate_product_item(item, default_shop="Forte Market")
        self.assertIsNotNone(cleaned)
        self.assertEqual(cleaned["title"], 'Монитор 27" LG & Co Pro')

    def test_product_repository_cleans_title_on_save_and_create(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        init_schema_v2(conn)
        repo = ProductRepository(conn)

        p = CanonicalProduct(
            id="p_html_1",
            canonical_key="k_html_1",
            title="Монитор 31.5&#34; Iiyama &amp; Co &nbsp;PROLITE",
            category="monitors",
        )
        repo.save_or_update(p)
        self.assertEqual(p.title, 'Монитор 31.5" Iiyama & Co PROLITE')
        row = conn.execute("SELECT title FROM canonical_products WHERE id = 'p_html_1'").fetchone()
        self.assertEqual(row["title"], 'Монитор 31.5" Iiyama & Co PROLITE')

        p2 = repo.get_or_create_canonical(
            canonical_key="k_html_2",
            title="Затирка MAPEI 112&quot; 0&#43;",
            category="tools_repair",
        )
        self.assertEqual(p2.title, 'Затирка MAPEI 112" 0+')
        conn.close()


if __name__ == "__main__":
    unittest.main()
