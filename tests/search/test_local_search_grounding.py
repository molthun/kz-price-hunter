"""A04 (аудит 18.11.2026): характеристика плана — только при основании в запросе.

Живой случай: «зимняя резина 205/55 R16» → модель добавила studs="Да", условие ушло в фильтр и в цель
наблюдения, а уточнение снова попросило размер. Временные БД, без сети и модели.
"""
from tests import test_support  # noqa: F401
import json
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from tests.search.test_local_catalog_growth import seed
from repositories.catalog_expansion_repo import CatalogExpansionRepository
import local_search

TIRE_FIELDS = [dict(key='season', label='Сезон'), dict(key='width', label='Ширина'),
               dict(key='profile', label='Профиль'), dict(key='diameter', label='Диаметр'),
               dict(key='studs', label='Шипы')]
CATEGORIES = [dict(id='auto', name='Авто', parent_id=None, fields={}),
              dict(id='tires', name='Шины и диски', parent_id='auto', fields={})]


def answer(**attrs):
    """Ответ модели в форме, записанной на стенде 18.11."""
    return {'category_id': 'tires', 'category_name': 'Шины и диски', 'confidence': 0.95,
            'search_query': 'зимняя резина 205/55 R16', 'fields': TIRE_FIELDS, 'attributes': attrs,
            'clarification': 'Укажите размер шины, например 205/55 R16.'}


class CleanPlanGroundingTests(unittest.TestCase):
    def plan(self, query, **attrs):
        return local_search.clean_plan({**answer(**attrs), 'search_query': query}, query, CATEGORIES)

    def test_live_case_drops_invented_studs_and_keeps_stated_size(self):
        plan = self.plan('зимняя резина 205/55 R16', season='Зимние', studs='Да',
                         width='205', profile='55', diameter='16')
        self.assertEqual(plan['attributes'], {'season': 'Зимние', 'width': '205', 'profile': '55', 'diameter': '16'})
        self.assertNotIn('studs', plan['attributes'])
        # Размер уже указан — переспрашивать его нельзя
        self.assertEqual(plan['clarification'], '')

    def test_silent_query_gets_neither_yes_nor_no(self):
        for invented in ('Да', 'Нет', True, False):
            with self.subTest(invented=invented):
                plan = self.plan('зимняя резина 205/55 R16', season='Зимние', studs=invented)
                self.assertNotIn('studs', plan['attributes'])

    def test_explicit_studs_and_no_studs_are_kept_from_the_query(self):
        cases = {'зимняя резина 205/55 R16 без шипов': 'Нет',
                 'нешипованная резина 205/55 R16': 'Нет',
                 'липучка 205/55 R16': 'Нет',
                 'шипованная резина 205/55 R16': 'Да',
                 'зимние шины с шипами 205/55 R16': 'Да'}
        for query, expected in cases.items():
            # Модель может ошибиться в полярности: решает текст запроса
            for model_value in ('Да', 'Нет'):
                with self.subTest(query=query, model=model_value):
                    plan = self.plan(query, studs=model_value)
                    self.assertEqual(plan['attributes'].get('studs'), expected)

    def test_invented_numbers_and_values_are_dropped(self):
        plan = self.plan('зимняя резина 205/55 R16', season='Всесезонные', diameter='17', width='205')
        self.assertEqual(plan['attributes'], {'width': '205'})

    def test_size_clarification_stays_when_size_is_missing(self):
        plan = self.plan('зимняя резина', season='Зимние')
        self.assertEqual(plan['attributes'], {'season': 'Зимние'})
        self.assertIn('размер', plan['clarification'])


class SearchAndWatchGroundingTests(unittest.TestCase):
    def test_stale_plan_with_studs_does_not_reach_filters_or_watch_target(self):
        """План, сохранённый до исправления (в кэше намерений или живой ответ), проверяется повторно."""
        stale = dict(search_query='зимняя резина 205/55 R16', category_id='tires', category_name='Шины и диски',
                     parent_id='auto', fields=TIRE_FIELDS, confidence=0.98,
                     attributes={'season': 'Зимние', 'studs': 'Да'},
                     clarification='Укажите размер шины, например 205/55 R16.')
        with tempfile.TemporaryDirectory() as tmp:
            path = tmp + '/db'
            conn = sqlite3.connect(path)
            seed(conn)
            # Поле studs заполнено в индексе — иначе оно и раньше не стало бы фильтром
            CatalogExpansionRepository(conn).save_attributes(
                'winter', 'Шина зимняя Audit 205/55 R16',
                {'season': {'value': 'Зимние', 'evidence': 'зимняя'}, 'studs': {'value': 'Нет', 'evidence': 'липучка'}})
            conn.commit()
            CatalogExpansionRepository(conn).save_intent('зимняя резина 205/55 R16', stale)
            conn.close()
            for city in ('Астана', 'Алматы'):
                args = dict(query='зимняя резина 205/55 R16', city=city, limit=20)
                with self.subTest(city=city), patch('database.get_connection', side_effect=lambda: sqlite3.connect(path)):
                    for understanding in ({'status': 'ok', 'plan': stale}, {'status': 'unavailable'}):
                        data = local_search.search_sync(args, understanding, {})
                        self.assertNotIn('studs', data['selected_attributes'])
                        target = data['search_help'].get('watch_target') or ''
                        self.assertTrue(target.startswith(local_search.WATCH_PREFIX), target)
                        spec = json.loads(target[len(local_search.WATCH_PREFIX):])
                        self.assertNotIn('studs', spec.get('inferred_attributes') or {})
                        self.assertNotIn('studs', spec.get('attributes') or {})
                        self.assertNotIn('размер', data['search_help']['clarification'])


if __name__ == '__main__':
    unittest.main()


class NumericRoleGroundingTests(unittest.TestCase):
    """Остаток A04 (переаудит 6.40.16): число подтверждает признак только в своей роли."""

    def g(self, query, **attrs):
        return local_search.grounded_attributes(attrs, query)

    def test_model_number_is_not_memory_or_diagonal(self):
        self.assertEqual(self.g('iPhone 16', ram='16 ГБ', storage='16 ГБ', screen_size='16"'), {})
        self.assertEqual(self.g('Galaxy S24', ram='24 ГБ'), {})
        self.assertEqual(self.g('RTX 4060', vram='4060 ГБ'), {})
        self.assertEqual(self.g('телевизор LG 55', screen_diagonal='55"'), {})

    def test_tire_size_numbers_only_in_their_positions(self):
        q = 'зимняя резина 205/55 R16'
        self.assertEqual(self.g(q, width='205', profile='55', diameter='16'),
                         {'width': '205', 'profile': '55', 'diameter': '16'})
        self.assertEqual(self.g(q, width='55', profile='205', diameter='55'), {})
        self.assertEqual(self.g('шины R17', diameter='17', width='17'), {'diameter': '17'})
        self.assertEqual(self.g('шины 16', diameter='16'), {})

    def test_explicit_memory_requests_are_kept(self):
        cases = [
            ('iPhone 16 128GB', dict(storage='128 ГБ', ram='128 ГБ'), {'storage': '128 ГБ'}),
            ('iPhone 15 Pro 1TB', dict(storage='1 ТБ'), {'storage': '1 ТБ'}),
            ('Xiaomi 14 8/256', dict(ram='8 ГБ', storage='256 ГБ'), {'ram': '8 ГБ', 'storage': '256 ГБ'}),
            ('ноутбук 16 ГБ ОЗУ 512 ГБ SSD', dict(ram='16 ГБ', storage='512 ГБ'), {'ram': '16 ГБ', 'storage': '512 ГБ'}),
            ('ноутбук озу 16гб', dict(ram='16 ГБ', storage='16 ГБ'), {'ram': '16 ГБ'}),
            ('iPhone 16 память 16 ГБ', dict(ram='16 ГБ', storage='16 ГБ'), {'storage': '16 ГБ'}),
            ('телевизор 55 дюймов 120 Гц', {'screen_diagonal': '55"', 'refresh_rate': '120 Гц'},
             {'screen_diagonal': '55"', 'refresh_rate': '120 Гц'}),
            ('RTX 4060 8GB', dict(vram='8 ГБ'), {'vram': '8 ГБ'}),
        ]
        for query, attrs, expected in cases:
            with self.subTest(query=query):
                self.assertEqual(local_search.grounded_attributes(attrs, query), expected)

    def test_ambiguous_memory_number_is_not_a_requirement(self):
        # «16GB» без пояснения — ОЗУ или память? Обязательным условием не становится
        self.assertEqual(self.g('ноутбук 16GB', ram='16 ГБ', storage='16 ГБ'), {})
        # Пояснения по обе стороны числа спорят: ОЗУ не берётся, однозначный 1 ТБ — да
        self.assertEqual(self.g('ноутбук ОЗУ 32GB SSD 1TB', ram='32 ГБ', storage='1 ТБ'), {'storage': '1 ТБ'})


class NumericRoleSearchTests(unittest.TestCase):
    """Полный путь пробы аудитора: clean_plan → search_sync → watch_target, новый и старый план."""

    def setUp(self):
        from tests.search.test_attribute_facets_filter import create_test_db
        from tests.search.test_card_attributes_consistency import seed as seed_phone, TITLE
        from repositories.category_repo import CategoryRepository
        from domain.attribute_indexing import index_product_facts
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = self.tmp.name + '/db'
        conn = create_test_db()
        CategoryRepository(conn).seed_defaults_if_empty()
        seed_phone(conn, attrs={'storage': '128 ГБ', 'ram': '6 ГБ'})
        index_product_facts(conn, 'p1', [{'name': 'Оперативная память', 'value': '6 ГБ'}], TITLE)
        conn.commit()
        disk = sqlite3.connect(self.path)
        conn.backup(disk)
        disk.close()
        self.cats = local_search.catalog_context(conn)
        conn.close()

    def run_search(self, understanding):
        args = {'query': 'iPhone 16', 'city': 'Алматы', 'limit': 20}
        with patch('database.get_connection', side_effect=lambda: sqlite3.connect(self.path)):
            return local_search.search_sync(args, understanding, {})

    def assert_no_ram(self, data):
        self.assertEqual(data['total_products'], 1)
        self.assertNotIn('ram', data['selected_attributes'])
        spec = json.loads(data['search_help']['watch_target'][len(local_search.WATCH_PREFIX):])
        self.assertNotIn('ram', spec.get('inferred_attributes') or {})
        self.assertNotIn('ram', spec.get('attributes') or {})

    def test_new_model_answer_with_ram_16(self):
        fields = [{'key': 'ram', 'label': 'Оперативная память'}]
        answer = {'category_id': 'phones', 'category_name': 'Смартфоны', 'confidence': .99,
                  'search_query': 'iPhone 16', 'fields': fields, 'attributes': {'ram': '16 ГБ'}}
        plan = local_search.clean_plan(answer, 'iPhone 16', self.cats)
        self.assertNotIn('ram', plan['attributes'])
        self.assert_no_ram(self.run_search({'status': 'ok', 'plan': plan}))

    def test_stale_saved_plan_with_ram_16(self):
        stale = dict(search_query='iPhone 16', category_id='phones', category_name='Смартфоны', parent_id=None,
                     fields=[{'key': 'ram', 'label': 'Оперативная память'}], confidence=0.99,
                     attributes={'ram': '16 ГБ'}, clarification='')
        conn = sqlite3.connect(self.path)
        CatalogExpansionRepository(conn).save_intent('iPhone 16', stale)
        conn.commit()
        conn.close()
        self.assert_no_ram(self.run_search({'status': 'ok', 'plan': stale}))
        self.assert_no_ram(self.run_search({'status': 'unavailable'}))
