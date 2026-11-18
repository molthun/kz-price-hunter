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
