"""Independent behavior probes for the new class marks and persisted data paths.
All SQLite is temporary; no real models/search/notifications.
"""
import asyncio
import hashlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tests.ai.test_local_ai import _DbCase
import database
import product_key as pk
import product_verify as pv
from scripts import migrate_to_k2 as mig


def cpu():
    return {'brand': 'AMD', 'model': 'Ryzen 7 7700X', 'sku': None, 'edition': None,
            'variants': [], 'unbranded': False}


class Residuals(_DbCase):
    def product(self, pid, title, key, shop):
        database.save_or_update_product({'id': pid, 'title': title, 'canonical_key': key, 'shop': shop,
                                         'city': 'Алматы', 'price': 1000, 'url': f'https://example.kz/{pid}'})

    def test_ram_frequency_is_not_manufacturer_part_number(self):
        a, b = 'Оперативная память Kingston DDR4-3200 Fury Beast 16GB', 'Оперативная память Kingston DDR4-3200 ValueRAM 16GB'
        fa = {'brand': 'Kingston', 'model': 'Fury Beast', 'sku': None, 'variants': [], 'unbranded': False}
        fb = {**fa, 'model': 'ValueRAM'}
        ka, kb = pk.build_key(fa, a), pk.build_key(fb, b)
        print('RAM_FREQUENCY_AS_SKU', ka, kb)
        self.assertNotEqual(ka, kb, 'DDR4-3200 is a common specification, not a unique manufacturer identity')

    def test_rescore_old_article_keys_keeps_box_mark(self):
        box_a, box_b, oem = 'AMD Ryzen 7 7700X BOX black', 'AMD Ryzen 7 7700X BOX white', 'Процессор AMD Ryzen 7 7700X OEM'
        old = 'k2:amd:ryzen77700x'
        mapping = {t: cpu() for t in [box_a, box_b, oem]}
        database.save_title_fields_batch(mapping, 'k2', 'local', 'old-extraction')
        database.save_cached_canonical_keys_batch({t: old for t in mapping})
        database.save_title_articles_batch([(article, hashlib.sha256(t.encode()).hexdigest(), old)
                                           for t in mapping for article in pk.article_tokens(cpu())])
        for i, t in enumerate(mapping):
            self.product(f'p{i}', t, old, f'shop{i}')
        result = asyncio.run(mig.run_migration(str(database.DB_PATH), '', '', rescore_only=True,
                                               batch_size=1, run_id='audit-rules'))
        actual = database.get_cached_canonical_keys_batch(mapping)
        with database.get_connection() as conn:
            offers = [tuple(r) for r in conn.execute('SELECT id,product_id FROM offers ORDER BY id')]
        print('RESCORE_OLD_ANCHOR', actual, offers, result['run'])
        self.assertIn('box', pk.marks_of(actual[box_a]), 'fixed key from an old article row stripped the new box mark')
        self.assertEqual(actual[box_a], actual[box_b])
        self.assertNotEqual(actual[box_a], actual[oem])

    def test_worker_checks_box_for_member_without_fields(self):
        a, b, hidden = 'AMD Ryzen 7 7700X OEM', 'Процессор AMD Ryzen 7 7700X OEM', 'AMD Ryzen 7 7700X BOX'
        old = {a: 'amd:ryzen7 7700x:oem', b: 'amd:ryzen77700x:oem', hidden: 'amd:ryzen77700x:oem'}
        database.save_cached_canonical_keys_batch(old)
        database.save_title_fields_batch({a: cpu(), b: cpu()}, 'k2', 'gemini', 'fake')
        for i, t in enumerate(old):
            self.product(f'p{i}', t, old[t], f'shop{i}')
        database.enqueue_disputed_pair(a, b, 'same actual OEM pair')
        async def same(*args, **kwargs):
            return {'decision': pv.SAME}
        with patch.object(pv, 'verify_pair_cached', same):
            result = asyncio.run(pv.process_disputed_queue_tick('http://unused', lambda _: None))
        actual = database.get_cached_canonical_keys_batch(old)
        with database.get_connection() as conn:
            offers = [tuple(r) for r in conn.execute('SELECT id,product_id FROM offers ORDER BY id')]
        print('BOX_MEMBER_NO_FIELDS', result['status'], actual, offers)
        self.assertEqual(result['status'], 'unresolved', 'valid SAME of two OEM titles dragged BOX into their group')
        self.assertEqual(actual, old)

    def test_class_mark_does_not_depend_on_model_word_placement(self):
        cases = [
            ('wifi', 'Материнская плата ASRock B850M-X WIFI', 'ASRock B850M-X WIFI',
             {'brand': 'ASRock', 'model': 'B850M-X WIFI', 'sku': 'AUDIT-SKU12345', 'variants': [], 'unbranded': False},
             'B850M-X'),
            ('flex', 'Jabra Evolve2 65 Flex Link380a MS Stereo', 'Гарнитура Jabra Evolve2 65 Flex Link380a MS Stereo',
             {'brand': 'Jabra', 'model': 'Evolve2 65 Flex', 'sku': '26699-999-999', 'variants': [], 'unbranded': False},
             'Evolve2 65'),
        ]
        for name, a, b, fa, model_b in cases:
            with self.subTest(name=name):
                fb = {**fa, 'model': model_b}
                ka, kb = pk.build_key(fa, a), pk.build_key(fb, b)
                conflict = pk.keys_conflict(ka, kb)
                pair = {pk.pair_hash(a, b): 'same'}
                linked = pk.resolve_keys_with_articles({a: fa, b: fb}, pair_verdicts=pair)
                print('MARK_PLACEMENT', name, ka, kb, 'conflict=', conflict, 'SAME-linked=', linked)
                self.assertFalse(conflict, 'the same modifier in model versus title created conflicting hard marks')
                self.assertEqual(linked[a], linked[b])


if __name__ == '__main__':
    unittest.main(verbosity=2)
