"""Приёмка 6.40.31 (R31-01…03): ключ k2 не склеивает разные комплекты и фасовки и не выдумывает диагональ.

Проверяется и сам ключ, и весь путь: настоящий ai_service._normalize_items → save_title_fields_batch →
save_or_update_products_batch → canonical_products/offers на временной БД. Ответ AI — фикстура.
"""
from tests import test_support  # noqa: F401
import pathlib
import tempfile
import unittest
from unittest.mock import patch

import ai_service
import config
import database
import product_key as pk


def fields(brand, model, number=None, unit=None):
    return dict(brand=brand, model=model, sku=None, edition=None,
                variants=[dict(value=number, unit=unit)] if number is not None else [], unbranded=False)


def canonical_groups(items):
    """Сколько канонических карточек получают товары после нормализации и сохранения."""
    with tempfile.TemporaryDirectory() as tmp:
        with patch.object(database, 'DB_PATH', pathlib.Path(tmp) / 'prices.db'), \
                patch.object(config, 'DATA_DIR', pathlib.Path(tmp)):
            database.init_db()
            titles = [t for t, _ in items]
            keys = ai_service._normalize_items({'items': [dict(title=t, **f) for t, f in items]},
                                               titles, 'k2', 'test-fixture', 'no-real-ai')
            database.save_title_fields_batch({t: f for t, f in items}, 'k2', 'test-fixture', 'no-real-ai')
            database.save_or_update_products_batch([
                dict(id=f'p{i}', shop='Test shop', city='Алматы', title=t, price=1000 + i, category='Тест',
                     url=f'https://example.invalid/{i}', canonical_key=keys[t]) for i, t in enumerate(titles)])
            with database.get_connection() as conn:
                groups = conn.execute('SELECT COUNT(*) FROM canonical_products').fetchone()[0]
                offer_groups = conn.execute('SELECT COUNT(DISTINCT product_id) FROM offers').fetchone()[0]
            return keys, groups, offer_groups


class MemoryKitTest(unittest.TestCase):
    """R31-01: «2x16GB» не превращается в «6GB», комплекты разного объёма не склеиваются."""
    RAM = ('Kingston', 'Fury Beast')

    def key(self, title, number, unit='GB'):
        return pk.build_key(fields(*self.RAM, number, unit), title)

    def test_kits_keep_total_and_module_count(self):
        k32 = self.key('Оперативная память Kingston Fury Beast DDR5 2x16GB', 32)
        k64 = self.key('Оперативная память Kingston Fury Beast DDR5 4x16GB', 64)
        self.assertEqual(k32, 'k2:kingston:furybeast:2pcs+32gb+ddr5')
        self.assertEqual(k64, 'k2:kingston:furybeast:4pcs+64gb+ddr5')
        for key in (k32, k64):
            self.assertNotIn('6gb', key)
        self.assertTrue(pk.keys_conflict(k32, k64))

    def test_equivalent_kit_records_share_one_key(self):
        base = self.key('Оперативная память Kingston Fury Beast DDR5 2x16GB', 32)
        for title, number in [('Оперативная память Kingston Fury Beast DDR5 2 x 16 ГБ', 32),
                              ('Оперативная память Kingston Fury Beast DDR5 2×16GB', 16),    # поштучно в ответе
                              ('Оперативная память Kingston Fury Beast DDR5 2х16 GB', 32),    # кириллическая х
                              ('Оперативная память Kingston Fury Beast DDR5 32GB (2x16GB)', 16),
                              ('Оперативная память Kingston Fury Beast DDR5 32 ГБ kit 2 x 16 ГБ', 32)]:
            with self.subTest(title=title):
                self.assertEqual(self.key(title, number), base)

    def test_kit_differs_from_single_module_of_same_total(self):
        kit = self.key('Оперативная память Kingston Fury Beast DDR5 2x16GB', 32)
        single = self.key('Оперативная память Kingston Fury Beast DDR5 32GB', 32)
        self.assertEqual(single, 'k2:kingston:furybeast:32gb+ddr5')
        self.assertNotEqual(kit, single)

    def test_kit_with_total_in_title_from_stand(self):
        title = 'Комплект модулей памяти Kingston FURY Beast 64 GB kit (2x32GB) KF560C36BBEK2-64'
        key = pk.build_key(fields('Kingston', 'KF560C36BBEK2-64', 64, 'GB'), title)
        self.assertEqual(key, 'k2:kingston:kf560c36bbek264:2pcs+64gb')
        self.assertNotIn('2gb', key)

    def test_digit_is_never_cut_out_of_a_number(self):
        for title in ('Kingston DDR5 2x16GB', 'Kingston DDR5 4x16GB', 'Kingston DDR4 2x32GB', 'Kingston 12x16GB'):
            with self.subTest(title=title):
                values = {(v['value'], v['unit']) for v in pk.title_variants(title)}
                self.assertFalse({(6.0, 'gb'), (2.0, 'gb')} & values, values)

    def test_full_path_keeps_kits_apart_and_equivalents_together(self):
        _, groups, offers = canonical_groups([
            ('Оперативная память Kingston Fury Beast DDR5 2x16GB', fields(*self.RAM, 32, 'GB')),
            ('Оперативная память Kingston Fury Beast DDR5 4x16GB', fields(*self.RAM, 64, 'GB'))])
        self.assertEqual((groups, offers), (2, 2))
        keys, groups, _ = canonical_groups([
            ('Оперативная память Kingston Fury Beast DDR5 2x16GB', fields(*self.RAM, 32, 'GB')),
            ('Оперативная память Kingston Fury Beast DDR5 32GB (2 x 16 ГБ)', fields(*self.RAM, 16, 'GB'))])
        self.assertEqual(len(set(keys.values())), 1, keys)
        self.assertEqual(groups, 1)


class PackCompositionTest(unittest.TestCase):
    """R31-02: состав упаковки (число × величина одной) остаётся в ключе наряду с её разбором."""
    FOOD = ('Royal Canin', 'British Shorthair Adult')
    DRINK = ('Coca-Cola', 'Classic')

    def test_equal_totals_with_different_packs_differ(self):
        pairs = [
            (('Корм Royal Canin British Shorthair Adult 12 x 85 г', fields(*self.FOOD, 85, 'г')),
             ('Корм Royal Canin British Shorthair Adult 6 x 170 г', fields(*self.FOOD, 170, 'г'))),
            (('Корм Royal Canin British Shorthair Adult 12 x 85 г', fields(*self.FOOD, 85, 'г')),
             ('Корм Royal Canin British Shorthair Adult 1020 г', fields(*self.FOOD, 1020, 'г'))),
            (('Напиток Coca-Cola Classic 6 x 0.5 л', fields(*self.DRINK, 0.5, 'л')),
             ('Напиток Coca-Cola Classic 2 x 1.5 л', fields(*self.DRINK, 1.5, 'л'))),
        ]
        for a, b in pairs:
            with self.subTest(a=a[0], b=b[0]):
                ka, kb = pk.build_key(a[1], a[0]), pk.build_key(b[1], b[0])
                self.assertNotEqual(ka, kb)
                self.assertTrue(pk.keys_conflict(ka, kb))
                _, groups, offers = canonical_groups([a, b])
                self.assertEqual((groups, offers), (2, 2))

    def test_equivalent_pack_records_share_one_key(self):
        expected = 'k2:royalcanin:britishshorthairadult:12pcs+85g'
        for title, number, unit in [('Корм Royal Canin British Shorthair Adult 12 x 85 г', 85, 'г'),
                                    ('Корм Royal Canin British Shorthair Adult 12 × 0.085 кг', 0.085, 'кг'),
                                    ('Корм Royal Canin British Shorthair Adult 12х85г', 1020, 'г'),   # сумма в ответе
                                    ('Корм Royal Canin British Shorthair Adult 12 * 85 гр', 1.02, 'кг')]:
            with self.subTest(title=title):
                self.assertEqual(pk.build_key(fields(*self.FOOD, number, unit), title), expected)
        keys, groups, _ = canonical_groups([
            ('Корм Royal Canin British Shorthair Adult 12 x 85 г', fields(*self.FOOD, 85, 'г')),
            ('Корм Royal Canin British Shorthair Adult 12 × 0.085 кг', fields(*self.FOOD, 1020, 'г'))])
        self.assertEqual(len(set(keys.values())), 1, keys)
        self.assertEqual(groups, 1)

    def test_single_pack_units_still_canonical(self):
        self.assertEqual(pk.build_key(fields(*self.FOOD, 500, 'г'), 'Корм Royal Canin British Shorthair Adult 500 г'),
                         pk.build_key(fields(*self.FOOD, 0.5, 'кг'), 'Корм Royal Canin British Shorthair Adult 0,5 кг'))


class ScreenRoleTest(unittest.TestCase):
    """R31-03: диагональ из сантиметров — только у самого экрана."""

    def test_accessory_for_screen_gets_no_diagonal(self):
        self.assertEqual(pk.build_key(fields('CablePro', 'CP12345'), 'Кабель для монитора CablePro CP12345 100cm'),
                         'k2:cablepro:cp12345')
        self.assertEqual(pk.build_key(fields('Brateck', 'LDA20'), 'Кронштейн Brateck LDA20 для телевизора 140 см'),
                         'k2:brateck:lda20')
        self.assertFalse(pk._allow_cm_diagonal('Подставка под монитор Ergo 100 см'))

    def test_marketing_smart_is_not_a_screen(self):
        towel = 'ARENA полотенце Beach smart 710 170x90 см'
        key = pk.build_key(fields('ARENA', 'Beach smart 710', 170, 'см'), towel)
        self.assertNotRegex(key, r'\d+in')
        self.assertFalse(pk._allow_cm_diagonal(towel))

    def test_real_screens_keep_cm_diagonal(self):
        self.assertEqual(pk.build_key(fields('Konka', 'KT-Q682WB'), 'Konka KT-Q682WB 140 см черный'),
                         'k2:konka:ktq682wb:55in')
        self.assertEqual(pk.build_key(fields('Samsung', 'CU7100'), 'Телевизор Samsung CU7100 55" 123x71x8 см'),
                         'k2:samsung:cu7100:55in')
        self.assertEqual(pk.build_key(fields('Xiaomi', 'A Pro'), 'Телевизор Xiaomi Smart TV A Pro 140 см'),
                         'k2:xiaomi:apro:55in')
        self.assertEqual(pk.build_key(fields('AOC', 'Q27G2U'), 'Монитор AOC Q27G2U 68 см с кабелем HDMI'),
                         'k2:aoc:q27g2u')    # 27" уже в номере модели
        self.assertTrue(pk._allow_cm_diagonal('Монитор AOC Q27G2U 68 см с кабелем HDMI'))


if __name__ == '__main__':
    unittest.main()
