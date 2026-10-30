"""Independent remaining BOX guard check. Only temporary SQLite; verifier response mocked."""
import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tests.ai.test_local_ai import _DbCase
import database
import product_key as pk
import product_verify as pv


class Residuals(_DbCase):
    def test_core_ultra_box_without_fields_is_not_moved_into_oem_group(self):
        a = 'Intel Core Ultra 7 265K OEM'
        b = 'Процессор Intel Core Ultra 7 265K OEM'
        hidden = 'Intel Core Ultra 7 265K BOX'
        keys = {a: 'intel:core ultra7 265k:oem', b: 'intel:coreultra7265k:oem',
                hidden: 'intel:coreultra7265k:oem'}
        f = {'brand': 'Intel', 'model': 'Core Ultra 7 265K', 'sku': None,
             'edition': None, 'variants': [], 'unbranded': False}
        database.save_cached_canonical_keys_batch(keys)
        database.save_title_fields_batch({a: f, b: f}, 'k2', 'local', 'audit')
        for i, title in enumerate(keys):
            database.save_or_update_product({'id': f'p{i}', 'title': title, 'canonical_key': keys[title],
                                             'shop': f'shop{i}', 'city': 'Алматы', 'price': 1000,
                                             'url': f'https://example.kz/{i}'})
        database.enqueue_disputed_pair(a, b, 'same OEM pair with fieldless BOX member')
        async def same(*args, **kwargs):
            return {'decision': pv.SAME}
        with patch.object(pv, 'verify_pair_cached', same):
            result = asyncio.run(pv.process_disputed_queue_tick('http://unused', lambda _: None))
        actual = database.get_cached_canonical_keys_batch(keys)
        with database.get_connection() as conn:
            offers = [tuple(row) for row in conn.execute('SELECT id, product_id FROM offers ORDER BY id')]
        print('CORE_ULTRA_BOX', 'title_marks=', pk._title_marks(hidden),
              'own_key_if_fields=', pk.build_key(f, hidden), 'status=', result['status'],
              'keys=', actual, 'offers=', offers)
        self.assertEqual(result['status'], 'unresolved', 'explicit BOX member moved into OEM group')
        self.assertEqual(actual, keys)


if __name__ == '__main__':
    unittest.main(verbosity=2)
