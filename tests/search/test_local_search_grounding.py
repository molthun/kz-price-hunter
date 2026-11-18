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
        # Значение приводится к виду индекса (normalize_attribute_value): диаметр — «R16»
        self.assertEqual(plan['attributes'], {'season': 'Зимние', 'width': '205', 'profile': '55', 'diameter': 'R16'})
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
                         {'width': '205', 'profile': '55', 'diameter': 'R16'})
        self.assertEqual(self.g(q, width='55', profile='205', diameter='55'), {})
        self.assertEqual(self.g('шины R17', diameter='17', width='17'), {'diameter': 'R17'})
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


class MemoryRoleAndUnitTests(unittest.TestCase):
    """R18-01/R18-02 (аудит 6.40.18): «оперативная память» — ОЗУ; величина сверяется с единицей."""

    def g(self, query, **attrs):
        return local_search.grounded_attributes(attrs, query)

    def test_ram_phrase_is_ram_not_storage(self):
        cases = [
            ('ноутбук оперативная память 64 ГБ', dict(ram='64 ГБ', storage='64 ГБ'), {'ram': '64 ГБ'}),
            ('ноутбук оперативная память 16 ГБ', dict(ram='16 ГБ'), {'ram': '16 ГБ'}),
            ('ноутбук 32 ГБ оперативной памяти', dict(ram='32 ГБ', storage='32 ГБ'), {'ram': '32 ГБ'}),
            ('ноутбук SSD 512 ГБ оперативная память 16 ГБ', dict(ram='16 ГБ', storage='512 ГБ'),
             {'ram': '16 ГБ', 'storage': '512 ГБ'}),
            ('ноутбук 16 ГБ ОЗУ 512 ГБ SSD', dict(ram='16 ГБ', storage='512 ГБ'), {'ram': '16 ГБ', 'storage': '512 ГБ'}),
            ('встроенная память 256 ГБ', dict(ram='256 ГБ', storage='256 ГБ'), {'storage': '256 ГБ'}),
        ]
        for query, attrs, expected in cases:
            with self.subTest(query=query):
                self.assertEqual(local_search.grounded_attributes(attrs, query), expected)

    def test_memory_pair_checks_units(self):
        self.assertEqual(self.g('Xiaomi 14 8/256', ram='8 ТБ', storage='256 ТБ'), {})
        self.assertEqual(self.g('Xiaomi 14 8/256', ram='8 ГБ', storage='256 ГБ'), {'ram': '8 ГБ', 'storage': '256 ГБ'})
        self.assertEqual(self.g('Poco 12/512GB', ram='12 ГБ', storage='512 ГБ'), {'ram': '12 ГБ', 'storage': '512 ГБ'})
        self.assertEqual(self.g('Galaxy 16/1TB', ram='16 ГБ', storage='1 ТБ'), {'ram': '16 ГБ', 'storage': '1 ТБ'})
        self.assertEqual(self.g('Galaxy 16/1TB', storage='1 ГБ'), {})
        self.assertEqual(self.g('Galaxy 16/1TB', ram='16 ТБ'), {})

    def test_latin_units_are_accepted_and_normalized_to_index_form(self):
        self.assertEqual(self.g('iPhone 16 128GB', storage='128GB'), {'storage': '128 ГБ'})
        self.assertEqual(self.g('iPhone 15 1 ТБ', storage='1TB'), {'storage': '1 ТБ'})
        self.assertEqual(self.g('iPhone 16 128 ГБ', storage='128 TB'), {})


class MemoryRoleSearchTests(NumericRoleSearchTests):
    """Полный путь до выдачи и watch_target: каталог — «iPhone 16 128GB (Black)» с 6 ГБ ОЗУ."""

    def run_query(self, query, attrs):
        fields = [{'key': 'ram', 'label': 'Оперативная память'}, {'key': 'storage', 'label': 'Встроенная память'}]
        answer = {'category_id': 'phones', 'category_name': 'Смартфоны', 'confidence': .99,
                  'search_query': query, 'fields': fields, 'attributes': attrs}
        plan = local_search.clean_plan(answer, query, self.cats)
        args = {'query': query, 'city': 'Алматы', 'limit': 20}
        with patch('database.get_connection', side_effect=lambda: sqlite3.connect(self.path)):
            data = local_search.search_sync(args, {'status': 'ok', 'plan': plan}, {})
        spec = json.loads(data['search_help']['watch_target'][len(local_search.WATCH_PREFIX):])
        return plan, data, {**(spec.get('attributes') or {}), **(spec.get('inferred_attributes') or {})}

    def test_ram_phrase_drops_contradicting_storage_up_to_watch_target(self):
        plan, data, watch = self.run_query('iPhone 16 оперативная память 6 ГБ', {'ram': '6 ГБ', 'storage': '6 ГБ'})
        self.assertEqual(plan['attributes'], {'ram': '6 ГБ'})
        self.assertNotIn('storage', data['selected_attributes'])
        self.assertNotIn('storage', watch)

    def test_latin_unit_answer_keeps_product(self):
        plan, data, watch = self.run_query('iPhone 16 128GB', {'storage': '128GB'})
        self.assertEqual(plan['attributes'], {'storage': '128 ГБ'})
        self.assertEqual(data['total_products'], 1)
        self.assertEqual(watch.get('storage'), '128 ГБ')

    def test_wrong_unit_answer_does_not_become_a_condition(self):
        plan, data, watch = self.run_query('iPhone 16 128GB', {'storage': '128 ТБ', 'ram': '16 ГБ'})
        # Ошибочные «128 ТБ» и «ОЗУ 16» отброшены; верная память «128 ГБ» берётся из самого запроса
        self.assertEqual(plan['attributes'], {'storage': '128 ГБ'})
        self.assertEqual(data['total_products'], 1)
        self.assertEqual({k: v for k, v in watch.items() if k in ('ram', 'storage')}, {'storage': '128 ГБ'})


class LaptopMemorySearchTests(unittest.TestCase):
    """Стенд 6.40.19: «ноутбук оперативная память 16 ГБ» давал 0 товаров.

    Модель переносит «16 ГБ» из текста в характеристику ram; clean_plan считал это потерей числа,
    откатывал запрос к исходной фразе и сбрасывал ram, а такой фразы нет в названиях («16GB»).
    """
    T16 = 'Ноутбук ASUS VivoBook 15 X1504 16GB 512GB SSD'
    T8 = 'Ноутбук ASUS VivoBook 15 X1504 8GB 512GB SSD'
    QUERY = 'ноутбук оперативная память 16 ГБ'

    def setUp(self):
        from tests.search.test_attribute_facets_filter import create_test_db
        from tests.search.test_card_attributes_consistency import seed as seed_product
        from repositories.category_repo import CategoryRepository
        from domain.attribute_indexing import index_product_facts
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = self.tmp.name + '/db'
        conn = create_test_db()
        CategoryRepository(conn).seed_defaults_if_empty()
        for pid, title in (('l16', self.T16), ('l8', self.T8)):
            seed_product(conn, title=title, pid=pid, category='Ноутбуки', brand='ASUS')
            index_product_facts(conn, pid, [], title)
        conn.commit()
        disk = sqlite3.connect(self.path)
        conn.backup(disk)
        disk.close()
        self.cats = local_search.catalog_context(conn)
        conn.close()

    def search(self, search_query, attrs, city='Алматы'):
        answer = {'category_id': 'laptops', 'category_name': 'Ноутбуки', 'confidence': .95,
                  'search_query': search_query, 'fields': [{'key': 'ram', 'label': 'Оперативная память'}],
                  'attributes': attrs}
        plan = local_search.clean_plan(answer, self.QUERY, self.cats)
        with patch('database.get_connection', side_effect=lambda: sqlite3.connect(self.path)):
            data = local_search.search_sync({'query': self.QUERY, 'city': city, 'limit': 20},
                                            {'status': 'ok', 'plan': plan}, {})
        return plan, data

    def test_ram_moved_to_filter_finds_only_matching_laptop(self):
        # Модель сократила текст, сохранила текст целиком или оставила «16 ГБ» — результат один
        for search_query in ('ноутбук', self.QUERY, 'ноутбук 16 ГБ'):
            with self.subTest(search_query=search_query):
                plan, data = self.search(search_query, {'ram': '16 ГБ'})
                self.assertEqual(plan['attributes'], {'ram': '16 ГБ'})
                self.assertEqual([i['product']['id'] for i in data['items']], ['l16'])
                spec = json.loads(data['search_help']['watch_target'][len(local_search.WATCH_PREFIX):])
                self.assertEqual(spec.get('inferred_attributes', {}).get('ram'), '16 ГБ')
                # Память уже указана — подсказка её не переспрашивает
                self.assertNotIn('памят', data['search_help']['clarification'])

    def test_real_model_shape_empty_attributes_and_full_text(self):
        """Ответ qwen3-vl на стенде 6.40.20: attributes пусты, search_query = весь запрос."""
        for city in ('Алматы', 'Астана'):
            with self.subTest(city=city):
                seed_city = city  # фикстура продаёт в Алматы; в Астане выдачи нет, ram не выдумывается
                plan, data = self.search(self.QUERY, {}, city=seed_city)
                self.assertEqual(plan['attributes'], {'ram': '16 ГБ'})
                self.assertEqual(data['selected_attributes'], {'ram': '16 ГБ'})
                if city == 'Алматы':
                    self.assertEqual([i['product']['id'] for i in data['items']], ['l16'])
                self.assertNotIn('памят', data['search_help']['clarification'])

    def test_stale_saved_plan_without_memory_is_completed_from_the_query(self):
        stale = {'search_query': self.QUERY, 'category_id': 'laptops', 'category_name': 'Ноутбуки',
                 'parent_id': None, 'fields': [{'key': 'ram', 'label': 'Оперативная память'}],
                 'attributes': {}, 'confidence': 0.95, 'clarification': ''}
        with patch('database.get_connection', side_effect=lambda: sqlite3.connect(self.path)):
            data = local_search.search_sync({'query': self.QUERY, 'city': 'Алматы', 'limit': 20},
                                            {'status': 'ok', 'plan': stale}, {})
        self.assertEqual([i['product']['id'] for i in data['items']], ['l16'])
        spec = json.loads(data['search_help']['watch_target'][len(local_search.WATCH_PREFIX):])
        self.assertEqual(spec['inferred_attributes'].get('ram'), '16 ГБ')

    def test_lost_model_number_still_rolls_back_but_keeps_grounded_memory(self):
        """Пропажа номера модели («15») по-прежнему возвращает исходный текст — без описания памяти."""
        answer = {'category_id': 'laptops', 'category_name': 'Ноутбуки', 'confidence': .95,
                  'search_query': 'ноутбук vivobook', 'fields': [{'key': 'ram', 'label': 'Оперативная память'}],
                  'attributes': {'ram': '16 ГБ'}}
        plan = local_search.clean_plan(answer, 'ноутбук vivobook 15 оперативная память 16 ГБ', self.cats)
        self.assertEqual(plan['search_query'], 'ноутбук vivobook 15 оперативная память 16 ГБ')
        self.assertEqual(plan['attributes'], {'ram': '16 ГБ'})

    def test_ungrounded_memory_number_is_still_a_loss(self):
        """«16» без основания (номер модели) не переносится в фильтр и не считается сохранённым."""
        answer = {'category_id': 'phones', 'category_name': 'Смартфоны', 'confidence': .95,
                  'search_query': 'iPhone', 'fields': [], 'attributes': {'ram': '16 ГБ'}}
        plan = local_search.clean_plan(answer, 'iPhone 16', self.cats)
        self.assertEqual(plan['search_query'], 'iPhone 16')
        self.assertEqual(plan['attributes'], {})


class StripMemoryPhrasesTests(unittest.TestCase):
    def test_only_the_memory_value_and_its_words_are_removed(self):
        cases = [
            ('ноутбук оперативная память 16 ГБ', {'ram': '16 ГБ'}, 'ноутбук'),
            ('ноутбук 32 ГБ оперативной памяти', {'ram': '32 ГБ'}, 'ноутбук'),
            ('iPhone 16 128GB', {'storage': '128 ГБ'}, 'iPhone 16'),
            ('iPhone 16 память 128 ГБ', {'storage': '128 ГБ'}, 'iPhone 16'),
            ('Xiaomi 14 8/256', {'ram': '8 ГБ', 'storage': '256 ГБ'}, 'Xiaomi 14'),
            ('RTX 4060 8GB', {'vram': '8 ГБ'}, 'RTX 4060'),
            # Вид товара сохраняется
            ('карта памяти 64 ГБ', {'storage': '64 ГБ'}, 'карта памяти'),
            ('модуль памяти DDR5 32 ГБ', {'ram': '32 ГБ'}, 'модуль памяти DDR5'),
            # Без условий памяти текст не меняется
            ('зимняя резина 205/55 R16', {'width': '205'}, 'зимняя резина 205/55 R16'),
        ]
        for query, attrs, expected in cases:
            with self.subTest(query=query):
                self.assertEqual(local_search._strip_memory_phrases(query, attrs), expected)


class ExplicitMemoryAttributesTests(unittest.TestCase):
    def test_only_unambiguous_memory_is_derived_from_the_query(self):
        keys = {'ram', 'storage'}
        cases = {
            'ноутбук оперативная память 16 ГБ': {'ram': '16 ГБ'},
            'ноутбук 32 ГБ оперативной памяти': {'ram': '32 ГБ'},
            'ноутбук ОЗУ 8 ГБ': {'ram': '8 ГБ'},
            'ноутбук 16 ГБ ОЗУ 512 ГБ SSD': {'ram': '16 ГБ', 'storage': '512 ГБ'},
            'iPhone 16 128GB': {'storage': '128 ГБ'},
            'Xiaomi 14 8/256': {'ram': '8 ГБ', 'storage': '256 ГБ'},
            'Galaxy 16/1TB': {'ram': '16 ГБ', 'storage': '1 ТБ'},
            'iPhone 16': {},
            'ноутбук 16GB': {},
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                self.assertEqual(local_search.explicit_memory_attributes(query, keys), expected)
        # Поле, которого нет у категории, не добавляется
        self.assertEqual(local_search.explicit_memory_attributes('ноутбук ОЗУ 8 ГБ', {'storage'}), {})
