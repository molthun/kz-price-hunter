"""A05 (аудит 18.11.2026): теги карточки, индекс и фильтр выдачи согласованы.

Живой случай стенда: «Смартфон Apple iPhone 16 128GB (Black)» с attributes storage="128 ГБ",
color="Белый" и без записи индекса на текущем названии. Клик по «128 ГБ» давал 0 товаров.
Правило: кликабельно (filterable_attributes) только то, по чему фильтр найдёт сам товар.
"""
from tests import test_support  # noqa: F401
import json
import unittest

from domain.attribute_indexing import index_product_facts
from domain.search_core import SearchCore, reconcile_card_attributes
from domain.models import CanonicalProduct
from repositories.search_repo import SearchRepository
from tests.search.test_attribute_facets_filter import create_test_db

TITLE = 'Смартфон Apple iPhone 16 128GB (Black)'
TV = 'Телевизор LG 55" 4K 120 Гц'


def seed(conn, title=TITLE, attrs=None, pid='p1', price=400000, category='Смартфоны', brand='Apple'):
    conn.execute("INSERT OR IGNORE INTO sellers (id, name, slug, created_at, updated_at) VALUES ('s1','Shop','shop','2026-01-01','2026-01-01')")
    conn.execute("INSERT OR IGNORE INTO channels (id, seller_id, channel_type, name, created_at) VALUES ('ch1','s1','ONLINE','Shop','2026-01-01')")
    conn.execute("INSERT INTO canonical_products (id, canonical_key, title, category, brand, attributes_json, created_at, updated_at) "
                 "VALUES (?,?,?,?,?,?,?,?)", (pid, pid, title, category, brand,
                                               json.dumps(attrs or {}, ensure_ascii=False), '2026-01-01', '2026-01-01'))
    conn.execute("INSERT INTO offers (id, product_id, seller_id, channel_id, external_sku, url, price, city, availability, observed_at, created_at, updated_at) "
                 "VALUES (?,?,'s1','ch1',?,?,?,'Алматы','in_stock','2026-01-01','2026-01-01','2026-01-01')",
                 ('o' + pid, pid, 'sku' + pid, 'http://x/' + pid, price))
    conn.commit()


class CardAttributeConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.conn = create_test_db()
        self.addCleanup(self.conn.close)
        SearchRepository._cat_cache = {}
        self.core = SearchCore(SearchRepository(self.conn))

    def card(self, query='iPhone 16', **kw):
        resp = self.core.search(query=query, **kw)
        return {c.product.id: c for c in resp.items}

    def assert_click_keeps_product(self, query, pid):
        card = self.card(query)[pid]
        self.assertTrue(card.filterable_attributes, 'нечего проверять')
        for key, value in card.filterable_attributes.items():
            with self.subTest(key=key, value=value):
                again = self.card(query, attributes={key: value})
                self.assertIn(pid, again, f'клик по своей характеристике {key}={value} убрал товар')

    def test_unindexed_attributes_are_not_offered_as_filters(self):
        seed(self.conn, attrs={'storage': '128 ГБ', 'color': 'Белый'})
        card = self.card()['p1']
        self.assertEqual(card.filterable_attributes, {})
        # Значение, противоречащее названию, на карточке не показывается
        self.assertEqual(card.product.attributes['color'], 'Черный')
        self.assertEqual(card.product.attributes['storage'], '128 ГБ')
        # Именно этот фильтр раньше прятал товар — его и нельзя предлагать
        self.assertEqual(self.card(attributes={'storage': '128 ГБ'}), {})

    def test_indexed_attributes_are_clickable_and_keep_the_product(self):
        seed(self.conn, attrs={'storage': '128 ГБ', 'color': 'Белый'})
        index_product_facts(self.conn, 'p1', [{'name': 'Объем встроенной памяти', 'value': '128 gb'}], TITLE)
        self.conn.commit()
        card = self.card()['p1']
        self.assertEqual(card.filterable_attributes.get('storage'), '128 ГБ')   # нормализация единиц
        self.assertEqual(card.filterable_attributes.get('color'), 'Черный')     # из названия
        self.assertEqual(card.product.attributes['color'], 'Черный')
        self.assert_click_keeps_product('iPhone 16', 'p1')

    def test_title_change_makes_old_index_non_clickable(self):
        seed(self.conn)
        index_product_facts(self.conn, 'p1', [], TITLE)
        self.conn.execute("UPDATE canonical_products SET title=? WHERE id='p1'",
                          ('Смартфон Apple iPhone 16 256GB (Black)',))
        self.conn.commit()
        card = self.card()['p1']
        # Индекс привязан к прежнему названию: «128 ГБ» не фильтр и не отображается вместо нового
        self.assertNotIn('storage', card.filterable_attributes)
        self.assertEqual(card.product.attributes.get('storage'), '256 ГБ')
        index_product_facts(self.conn, 'p1', [], 'Смартфон Apple iPhone 16 256GB (Black)')
        self.conn.commit()
        self.assertEqual(self.card()['p1'].filterable_attributes.get('storage'), '256 ГБ')
        self.assert_click_keeps_product('iPhone 16', 'p1')

    def test_tv_diagonal_and_refresh_rate(self):
        seed(self.conn, title=TV, pid='tv', attrs={'screen_diagonal': '65"'}, category='Телевизоры', brand='LG')
        index_product_facts(self.conn, 'tv', [], TV)
        self.conn.commit()
        card = self.card('Телевизор LG')['tv']
        self.assertEqual(card.filterable_attributes.get('screen_diagonal'), '55"')
        self.assertEqual(card.filterable_attributes.get('refresh_rate'), '120 Гц')
        self.assertEqual(card.product.attributes['screen_diagonal'], '55"')
        self.assert_click_keeps_product('Телевизор LG', 'tv')

    def test_serialized_item_carries_filterable_attributes(self):
        seed(self.conn)
        index_product_facts(self.conn, 'p1', [], TITLE)
        self.conn.commit()
        item = self.core.search(query='iPhone 16').to_dict()['items'][0]
        self.assertEqual(item['filterable_attributes'], {'color': 'Черный', 'storage': '128 ГБ'})
        self.assertEqual(item['product']['attributes']['color'], 'Черный')

    def test_reconcile_does_not_mutate_the_original_product(self):
        product = CanonicalProduct(id='x', canonical_key='x', title=TITLE, category='Смартфоны',
                                   attributes={'color': 'Белый'})
        new, filterable = reconcile_card_attributes(product, {'storage': '128 ГБ', 'warranty': '1 год'})
        self.assertEqual(product.attributes, {'color': 'Белый'})
        self.assertEqual(filterable, {'storage': '128 ГБ'})
        self.assertEqual(new.attributes['color'], 'Черный')


if __name__ == '__main__':
    unittest.main()
