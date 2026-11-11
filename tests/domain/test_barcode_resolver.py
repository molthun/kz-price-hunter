"""Тесты модуля Barcode Resolver (EAN-13, EAN-8, UPC-A, GTIN-14)."""
from tests import test_support  # noqa: F401
import json
import unittest
from unittest.mock import patch

from domain.barcode_resolver import (
    is_valid_barcode,
    _clean_product_title,
    get_cached_barcode,
    save_cached_barcode,
    resolve_barcode,
    link_barcode_to_products,
    get_product_barcodes,
)


class BarcodeChecksumValidationTests(unittest.TestCase):
    def test_valid_ean13_barcodes(self):
        # 4870237159941 - салфетки Tompi (Казахстан)
        self.assertTrue(is_valid_barcode("4870237159941"))
        # Популярные валидные EAN-13
        self.assertTrue(is_valid_barcode("4006381333931"))
        self.assertTrue(is_valid_barcode("4600605018699"))

    def test_invalid_ean13_checksum(self):
        # Неверная контрольная цифра (последняя цифра 0 вместо 1)
        self.assertFalse(is_valid_barcode("4870237159940"))
        # Не цифры или неправильная длина
        self.assertFalse(is_valid_barcode("487023715994"))
        self.assertFalse(is_valid_barcode("48702371599412"))
        self.assertFalse(is_valid_barcode("abcdef1234567"))
        self.assertFalse(is_valid_barcode(""))
        self.assertFalse(is_valid_barcode(None))

    def test_valid_ean8_and_upca(self):
        # EAN-8 (8 цифр): 46006054 (4*3 + 6*1 + 0*3 + 0*1 + 6*3 + 0*1 + 5*3 = 12+6+0+0+18+0+15=51, (10-(51%10))%10 = 9 != 4)
        # Валидный EAN-8: 96385074 (9*3+6*1+3*3+8*1+5*3+0*1+7*3 = 27+6+9+8+15+0+21 = 86, check = 4)
        self.assertTrue(is_valid_barcode("96385074"))
        # Невалидный EAN-8
        self.assertFalse(is_valid_barcode("96385070"))


class CleanProductTitleTests(unittest.TestCase):
    def test_clean_junk_words_from_search_result(self):
        raw = "Влаж салф 100 шт - Штрих-код: 4870237159941 barcode-list.ru"
        clean = _clean_product_title(raw)
        self.assertEqual(clean, "Влаж салф 100 шт")

        raw2 = "Купить Зубная щетка электрическая в интернет-магазине Magnum"
        clean2 = _clean_product_title(raw2)
        self.assertEqual(clean2, "Купить Зубная щетка электрическая")


class BarcodeResolverAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolve_barcode_from_cache(self):
        # Сохраняем в кэш
        save_cached_barcode("4870237159941", "Влажные салфетки Tompi 100 шт", "Tompi")
        cached = get_cached_barcode("4870237159941")
        self.assertIsNotNone(cached)
        self.assertEqual(cached["title"], "Влажные салфетки Tompi 100 шт")

        # resolve_barcode берет из кэша
        res = await resolve_barcode("4870237159941")
        self.assertIsNotNone(res)
        self.assertEqual(res["title"], "Влажные салфетки Tompi 100 шт")

    async def test_resolve_invalid_barcode_returns_none(self):
        res = await resolve_barcode("12345")
        self.assertIsNone(res)

    async def test_resolve_barcode_searxng_fallback(self):
        # Если в кэше нет, но SearXNG вернул заголовок
        fake_results = json.dumps({
            "results": [
                {
                    "title": "Влаж салф 100 шт - Штрих-код: 4870237159941",
                    "content": "Этот штрих-код встречается в следующих товарах: Влажные салфетки Tompi 100 шт",
                    "url": "https://barcode-list.ru/barcode/4870237159941"
                }
            ]
        }).encode("utf-8")

        mock_resp = unittest.mock.MagicMock()
        mock_resp.read.return_value = fake_results
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp), \
             patch("domain.barcode_resolver.get_cached_barcode", return_value=None):
            res = await resolve_barcode("4870237159941")
            self.assertIsNotNone(res)
            self.assertIn("Tompi", res["title"])

    async def test_link_barcode_to_product(self):
        pid = "test_prod_link_1"
        barcode = "4870237159941"
        success = link_barcode_to_products(pid, barcode, source="test")
        self.assertTrue(success)

        barcodes = get_product_barcodes(pid)
        self.assertIn(barcode, barcodes)

        # Невалидный штрихкод не привязывается
        self.assertFalse(link_barcode_to_products(pid, "00000000"))

    async def test_resolve_from_product_barcodes(self):
        import database
        barcode = "4006381333931"
        pid = "test_prod_canon_1"

        # Создаем canonical_products строку если таблицы есть
        try:
            with database.get_connection() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS canonical_products (
                        id TEXT PRIMARY KEY,
                        canonical_key TEXT,
                        title TEXT,
                        category TEXT,
                        brand TEXT,
                        attributes_json TEXT,
                        created_at TEXT,
                        updated_at TEXT
                    )
                """)
                conn.execute(
                    "INSERT OR REPLACE INTO canonical_products (id, canonical_key, title, category, brand, created_at, updated_at) VALUES (?, ?, ?, ?, ?, '', '')",
                    (pid, "key_1", "Stabilo Boss Highlighter", "Канцтовары", "Stabilo")
                )
                conn.commit()
        except Exception:
            pass

        link_barcode_to_products(pid, barcode, source="test")

        # Очищаем кэш чтобы проверить именно product_barcodes
        with database.get_connection() as conn:
            conn.execute("DELETE FROM barcode_cache WHERE barcode = ?", (barcode,))
            conn.commit()

        res = await resolve_barcode(barcode)
        self.assertIsNotNone(res)
        self.assertEqual(res["barcode"], barcode)
        self.assertIn("Stabilo", res["title"])
