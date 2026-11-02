"""Final-ID dedupe in Bitrix adapters; synthetic HTML, no network or database."""
from tests import test_support  # noqa: F401
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import data_quality
from scrapers.moon import MoonScraper
from scrapers.vkusmart import VkusmartScraper


class NestedCardIdentityTests(unittest.TestCase):
    adapters = (MoonScraper, VkusmartScraper)

    @staticmethod
    def card(slug='sku', pid=None, price=1000, title='Fixture', inner=''):
        identity = f' data-id="{pid}"' if pid is not None else ''
        return f'''<div class="item_block"{identity}>
          <div class="item-title"><a href="/catalog/{slug}/">{title}</a></div>
          <meta itemprop="name" content="{title}">
          <span class="price price_value" data-value="{price}">{price}</span>{inner}</div>'''

    @contextmanager
    def response(self, adapter, html):
        response = SimpleNamespace(status_code=200, text=html)
        if adapter is MoonScraper:
            with patch('scrapers.moon.requests.get', return_value=response): yield
        else:
            with patch.object(VkusmartScraper, '_get_session', return_value=SimpleNamespace(get=lambda *a, **k: response)): yield

    def fetch(self, adapter, html):
        with self.response(adapter, html):
            return adapter()._fetch_page('fixture', 'https://example.com/catalog/fixture/', 1)

    def test_nested_fallback_cards_are_unique_and_quality_is_not_artificially_degraded(self):
        html = ''.join('<div class="catalog_item_wrapp">'+self.card(slug=f'sku-{i}')+'</div>' for i in range(5))
        for adapter in self.adapters:
            with self.subTest(adapter=adapter.__name__):
                products = self.fetch(adapter, html);metrics = data_quality.measure(products)
                self.assertEqual(metrics['received'], 5);self.assertEqual(metrics['valid'], 5);self.assertEqual(metrics['duplicates'], 0)
                self.assertNotEqual(data_quality.assess(metrics, complete=False, error=None, baseline=None)['quality'], 'degraded')

    def test_native_and_fallback_same_final_id_keep_first_valid_price(self):
        html = self.card(pid='sku', price=1000)+self.card(price=1500)
        for adapter in self.adapters:
            with self.subTest(adapter=adapter.__name__):
                products=self.fetch(adapter,html);self.assertEqual(len(products),1)
                self.assertEqual(products[0]['price'],1000);self.assertEqual(products[0]['title'],'Fixture')
                self.assertEqual(products[0]['id'],'moon_sku' if adapter is MoonScraper else 'sku')

    def test_invalid_outer_price_does_not_hide_valid_inner_card(self):
        inner=self.card(pid='sku',price=1000,title='Valid inner')
        html=self.card(pid='sku',price=0,title='Invalid outer',inner=inner)
        for adapter in self.adapters:
            with self.subTest(adapter=adapter.__name__):
                products=self.fetch(adapter,html);self.assertEqual(len(products),1)
                self.assertEqual(products[0]['title'],'Valid inner');self.assertEqual(products[0]['price'],1000)

    def test_distinct_native_ids_with_same_url_and_title_remain_distinct(self):
        html=self.card(pid='variant-a')+self.card(pid='variant-b')
        for adapter in self.adapters:
            with self.subTest(adapter=adapter.__name__):
                products=self.fetch(adapter,html);self.assertEqual(len(products),2)
                self.assertEqual(len({p['id'] for p in products}),2)
                self.assertEqual(len({p['url'] for p in products}),1)

    def test_fallback_ids_and_names_are_preserved(self):
        for adapter in self.adapters:
            with self.subTest(adapter=adapter.__name__):
                products=self.fetch(adapter,self.card(slug='123',title='Original source title'))
                self.assertEqual(products[0]['id'],'moon_123' if adapter is MoonScraper else '123')
                self.assertEqual(products[0]['title'],'Original source title')

    def test_one_page_limit_does_not_claim_complete_coverage(self):
        for adapter in self.adapters:
            with self.subTest(adapter=adapter.__name__),self.response(adapter,self.card()),patch.object(adapter,'_report_scrape'):
                products=adapter()._scrape_sync('fixture','https://example.com/catalog/fixture/',max_pages=1)
                self.assertEqual(len(products),1);self.assertFalse(products.complete);self.assertTrue(products.limited)
