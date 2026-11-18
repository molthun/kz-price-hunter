"""Приёмка 6.40.21 (R21-01…03): выдача, цель наблюдения и её сопоставление — одна семантика.

Проверяется полное поведение: товары выдачи и ответ настоящего matches_watch по сформированной
цели, для нового ответа модели и сохранённого плана, с выбранной категорией и без, в обоих городах.
"""
from tests import test_support  # noqa: F401
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import local_search
from domain.attribute_indexing import index_product_facts
from repositories.catalog_expansion_repo import CatalogExpansionRepository
from repositories.category_repo import CategoryRepository
from tests.search.test_attribute_facets_filter import create_test_db
from tests.search.test_card_attributes_consistency import seed

FIELDS = [{'key': 'ram', 'label': 'Оперативная память'}, {'key': 'storage', 'label': 'Встроенная память'}]
CATEGORY_NAMES = {'laptops': ('Ноутбуки', 'ASUS'), 'phones': ('Смартфоны', 'Xiaomi')}


class Catalog:
    def __init__(self, case, cid, products, drop_keys=()):
        name, brand = CATEGORY_NAMES[cid]
        self.tmp = tempfile.TemporaryDirectory()
        case.addCleanup(self.tmp.cleanup)
        self.path, self.cid, self.name, self.ids = self.tmp.name + '/db', cid, name, [p for p, _ in products]
        conn = create_test_db()
        CategoryRepository(conn).seed_defaults_if_empty()
        for pid, title in products:
            seed(conn, pid=pid, title=title, category=name, brand=brand)
            index_product_facts(conn, pid, [], title)
        for key in drop_keys:   # неполный индекс раздела создаётся только во временной БД
            conn.execute('DELETE FROM product_search_attributes WHERE key=?', (key,))
        conn.commit()
        disk = sqlite3.connect(self.path)
        conn.backup(disk)
        disk.close()
        self.cats = local_search.catalog_context(conn)
        conn.close()

    def connect(self):
        return sqlite3.connect(self.path)

    def run(self, query, rewritten, mode='new', city='Алматы', category=None, attrs=None):
        answer = {'category_id': self.cid, 'category_name': self.name, 'confidence': .99,
                  'search_query': rewritten, 'fields': FIELDS, 'attributes': attrs or {}}
        plan = local_search.clean_plan(answer, query, self.cats)
        if mode == 'saved':
            conn = self.connect()
            CatalogExpansionRepository(conn).save_intent(query, plan)
            conn.commit()
            conn.close()
        understanding = {'status': 'ok', 'plan': plan} if mode == 'new' else {'status': 'unavailable'}
        args = {'query': query, 'city': city, 'limit': 20}
        if category:
            args['category'] = category
        with patch('database.get_connection', side_effect=self.connect):
            data = local_search.search_sync(args, understanding, {})
            target = data['search_help']['watch_target']
            watch = {pid for pid in self.ids
                     if local_search.matches_watch(target, {'canonical_product_id': pid, 'price': 400000})}
        return {i['product']['id'] for i in data['items']}, watch, data


class WatchMatchesResultsTests(unittest.TestCase):
    """R21-01: цель принимает каждый подходящий товар своей выдачи и отклоняет 8GB."""
    QUERY = 'ноутбук оперативная память 16 ГБ'

    def test_target_accepts_its_own_results(self):
        cat = Catalog(self, 'laptops', [('l16', 'Ноутбук ASUS VivoBook 15 X1504 16GB 512GB SSD'),
                                        ('l8', 'Ноутбук ASUS VivoBook 15 X1504 8GB 512GB SSD')])
        for rewritten in (self.QUERY, 'ноутбук', 'ноутбук 16 ГБ'):
            for mode in ('new', 'saved'):
                for category in (None, 'laptops'):
                    for city in ('Алматы', 'Астана'):
                        with self.subTest(rewritten=rewritten, mode=mode, category=category, city=city):
                            items, watch, data = cat.run(self.QUERY, rewritten, mode, city, category)
                            self.assertEqual(watch, {'l16'})
                            self.assertEqual(items, {'l16'} if city == 'Алматы' else set())
                            spec = json.loads(data['search_help']['watch_target'][len(local_search.WATCH_PREFIX):])
                            self.assertEqual(spec['inferred_attributes'], {'ram': '16 ГБ'})
                            self.assertNotIn('памят', spec['query'])

    def test_target_saved_by_6_40_21_with_raw_text_still_matches(self):
        cat = Catalog(self, 'laptops', [('l16', 'Ноутбук ASUS VivoBook 15 X1504 16GB 512GB SSD'),
                                        ('l8', 'Ноутбук ASUS VivoBook 15 X1504 8GB 512GB SSD')])
        legacy = local_search.WATCH_PREFIX + json.dumps(dict(
            query=self.QUERY, original_query=self.QUERY, category='laptops', attributes={},
            inferred_attributes={'ram': '16 ГБ'}, min_price=None, max_price=None,
            brand=None, model=None, kind=None), ensure_ascii=False)
        with patch('database.get_connection', side_effect=cat.connect):
            got = {pid for pid in cat.ids if local_search.matches_watch(legacy, {'canonical_product_id': pid, 'price': 1})}
        self.assertEqual(got, {'l16'})

    def test_model_and_kind_stay_strict_in_the_target(self):
        cat = Catalog(self, 'laptops', [('l16', 'Ноутбук ASUS VivoBook 15 X1504 16GB 512GB SSD'),
                                        ('z16', 'Ноутбук ASUS ZenBook 14 16GB 512GB SSD')])
        query = 'ноутбук ASUS VivoBook оперативная память 16 ГБ'
        for rewritten in (query, 'ноутбук ASUS VivoBook'):
            with self.subTest(rewritten=rewritten):
                items, watch, _ = cat.run(query, rewritten)
                self.assertEqual(items, {'l16'})
                self.assertEqual(watch, {'l16'})


class ModelNumberEqualToMemoryTests(unittest.TestCase):
    """R21-02: номер модели 16 не «покрывается» ОЗУ 16 ГБ."""

    def setUp(self):
        self.cat = Catalog(self, 'laptops', [('m15', 'Ноутбук ASUS VivoBook 15 16GB 512GB SSD'),
                                             ('m16', 'Ноутбук ASUS VivoBook 16 16GB 512GB SSD')])

    def test_lost_model_number_equal_to_ram_rolls_back(self):
        query = 'ноутбук ASUS VivoBook 16 ОЗУ 16 ГБ'
        for rewritten in ('ноутбук ASUS VivoBook', 'ноутбук ASUS VivoBook 16 ГБ'):
            for mode in ('new', 'saved'):
                with self.subTest(rewritten=rewritten, mode=mode):
                    items, watch, data = self.cat.run(query, rewritten, mode)
                    self.assertEqual(items, {'m16'})
                    self.assertEqual(watch, {'m16'})
                    self.assertEqual(data['selected_attributes'], {'ram': '16 ГБ'})

    def test_stale_saved_plan_without_model_number_is_not_trusted(self):
        query = 'ноутбук ASUS VivoBook 16 ОЗУ 16 ГБ'
        stale = {'search_query': 'ноутбук ASUS VivoBook', 'category_id': 'laptops', 'category_name': 'Ноутбуки',
                 'parent_id': None, 'fields': FIELDS, 'attributes': {'ram': '16 ГБ'}, 'confidence': .99,
                 'clarification': ''}
        conn = self.cat.connect()
        CatalogExpansionRepository(conn).save_intent(query, stale)
        conn.commit()
        conn.close()
        with patch('database.get_connection', side_effect=self.cat.connect):
            data = local_search.search_sync({'query': query, 'city': 'Алматы', 'limit': 20}, {'status': 'unavailable'}, {})
            target = data['search_help']['watch_target']
            watch = {p for p in self.cat.ids if local_search.matches_watch(target, {'canonical_product_id': p, 'price': 1})}
        self.assertEqual({i['product']['id'] for i in data['items']}, {'m16'})
        self.assertEqual(watch, {'m16'})

    def test_different_numbers_keep_working(self):
        query = 'ноутбук ASUS VivoBook 15 ОЗУ 16 ГБ'
        items, watch, data = self.cat.run(query, 'ноутбук ASUS VivoBook 15')
        self.assertEqual(items, {'m15'})
        self.assertEqual(watch, {'m15'})
        plan = local_search.clean_plan({'category_id': 'laptops', 'category_name': 'Ноутбуки', 'confidence': .99,
                                        'search_query': 'ноутбук ASUS VivoBook 15', 'fields': FIELDS,
                                        'attributes': {}}, query, self.cat.cats)
        self.assertEqual(plan['search_query'], 'ноутбук ASUS VivoBook 15')   # не откатывается


class PartialIndexMemoryPairTests(unittest.TestCase):
    """R21-03: половина «8/256» без заполненного поля остаётся условием текста."""
    QUERY = 'Xiaomi 14 8/256'
    RAM_VARIANTS = [('p128', 'Смартфон Xiaomi 14 8/128GB'), ('p256', 'Смартфон Xiaomi 14 8/256GB')]
    STORAGE_VARIANTS = [('r8', 'Смартфон Xiaomi 14 8/256GB'), ('r12', 'Смартфон Xiaomi 14 12/256GB')]

    def check(self, products, drop, expected, selected):
        cat = Catalog(self, 'phones', products, drop_keys=drop)
        for rewritten in (self.QUERY, 'Xiaomi 14', 'Xiaomi 14 8 ГБ 256 ГБ'):
            for mode in ('new', 'saved'):
                with self.subTest(drop=drop, rewritten=rewritten, mode=mode):
                    items, watch, data = cat.run(self.QUERY, rewritten, mode)
                    self.assertEqual(items, expected)
                    self.assertEqual(watch, expected)
                    self.assertEqual(data['selected_attributes'], selected)

    def test_only_ram_indexed(self):
        self.check(self.RAM_VARIANTS, ('storage',), {'p256'}, {'ram': '8 ГБ'})

    def test_only_storage_indexed(self):
        self.check(self.STORAGE_VARIANTS, ('ram',), {'r8'}, {'storage': '256 ГБ'})

    def test_nothing_indexed(self):
        self.check(self.RAM_VARIANTS, ('ram', 'storage'), {'p256'}, {})

    def test_fully_indexed(self):
        self.check(self.RAM_VARIANTS, (), {'p256'}, {'ram': '8 ГБ', 'storage': '256 ГБ'})
        self.check(self.STORAGE_VARIANTS, (), {'r8'}, {'ram': '8 ГБ', 'storage': '256 ГБ'})


class MemoryTextHelpersTests(unittest.TestCase):
    def test_pair_is_removed_only_when_both_parts_are_filters(self):
        strip = local_search._strip_memory_phrases
        self.assertEqual(strip('Xiaomi 14 8/256', {'ram': '8 ГБ', 'storage': '256 ГБ'}), 'Xiaomi 14')
        self.assertEqual(strip('Xiaomi 14 8/256', {'ram': '8 ГБ'}), 'Xiaomi 14 8/256')
        self.assertEqual(strip('Xiaomi 14 8/256GB', {'storage': '256 ГБ'}), 'Xiaomi 14 8/256GB')

    def test_unapplied_memory_is_restored_from_the_query(self):
        restore = local_search._restore_unapplied_memory
        grounded = {'ram': '8 ГБ', 'storage': '256 ГБ'}
        self.assertEqual(restore('Xiaomi 14', 'Xiaomi 14 8/256', grounded, {'ram': '8 ГБ'}), 'Xiaomi 14 8/256')
        self.assertEqual(restore('Xiaomi 14', 'Xiaomi 14 8/256', grounded, grounded), 'Xiaomi 14')
        self.assertEqual(restore('ноутбук', 'ноутбук ОЗУ 16 ГБ', {'ram': '16 ГБ'}, {}), 'ноутбук 16 гб')
        self.assertEqual(restore('ноутбук 16GB', 'ноутбук ОЗУ 16 ГБ', {'ram': '16 ГБ'}, {}), 'ноутбук 16GB')

    def test_numbers_are_compared_as_a_multiset(self):
        loses = local_search._rewrite_loses_constraints
        ram = {'ram': '16 ГБ'}
        self.assertTrue(loses('ноутбук ASUS VivoBook 16 ОЗУ 16 ГБ', 'ноутбук ASUS VivoBook', ram))
        self.assertTrue(loses('ноутбук ASUS VivoBook 16 ОЗУ 16 ГБ', 'ноутбук ASUS VivoBook 16 ГБ', ram))
        self.assertFalse(loses('ноутбук ASUS VivoBook 16 ОЗУ 16 ГБ', 'ноутбук ASUS VivoBook 16', ram))
        self.assertFalse(loses('ноутбук ASUS VivoBook 15 ОЗУ 16 ГБ', 'ноутбук ASUS VivoBook 15', ram))
        self.assertTrue(loses('iPhone 16 Pro 256GB', 'iPhone 16 256GB', {'storage': '256 ГБ'}))


if __name__ == '__main__':
    unittest.main()
