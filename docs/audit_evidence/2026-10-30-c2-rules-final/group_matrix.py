"""Independent CPU group boundary matrix, using only temporary SQLite and a mocked verifier."""
import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tests.ai.test_local_ai import _DbCase
import database
import product_verify as pv


class GroupMatrix(_DbCase):
    def state(self):
        with database.get_connection() as conn:
            return {name: [tuple(r) for r in conn.execute(f'SELECT * FROM {name} ORDER BY 1, 2')]
                    for name in ('title_canonical_cache', 'products', 'offers',
                                 'offer_price_history', 'identity_aliases')}

    def run_case(self, model, side, kind):
        a, b = f'Intel {model} OEM', f'Процессор Intel {model} OEM'
        hidden = (f'Системный блок Intel {model}' if kind == 'system' else f'Intel {model} {kind.upper()}')
        keys = {a: 'intel:old-a:oem', b: 'intel:old-b:oem', hidden: f'intel:old-{side}:oem'}
        f = {'brand': 'Intel', 'model': model, 'sku': None, 'edition': None,
             'variants': [], 'unbranded': False}
        database.save_cached_canonical_keys_batch(keys)
        database.save_title_fields_batch({a: f, b: f}, 'k2', 'local', 'audit')
        for i, title in enumerate(keys):
            database.save_or_update_product({'id': f'p{i}', 'title': title, 'canonical_key': keys[title],
                                             'shop': f'shop{i}', 'city': 'Алматы', 'price': 1000,
                                             'url': f'https://example.kz/{i}'})
        before = self.state()
        database.enqueue_disputed_pair(a, b, 'same OEM pair')
        async def same(*args, **kwargs):
            return {'decision': pv.SAME}
        with patch.object(pv, 'verify_pair_cached', same):
            result = asyncio.run(pv.process_disputed_queue_tick('http://unused', lambda _: None))
        print(model, side, kind, result['status'])
        if kind != 'tray':
            self.assertEqual(result['status'], 'unresolved')
            self.assertEqual(self.state(), before)
        else:
            self.assertEqual(result['status'], 'resolved')
            self.assertEqual(len(set(database.get_cached_canonical_keys_batch(keys).values())), 1)
            with database.get_connection() as conn:
                self.assertEqual(conn.execute('SELECT COUNT(DISTINCT product_id) FROM offers').fetchone()[0], 1)


def case(model, side, kind):
    def test(self):
        self.run_case(model, side, kind)
    return test


for family, model in [('ultra', 'Core Ultra 7 265K'), ('core5', 'Core 5 120')]:
    for side in ('a', 'b'):
        for kind in ('box', 'system', 'tray'):
            setattr(GroupMatrix, f'test_{family}_{side}_{kind}', case(model, side, kind))

if __name__ == '__main__':
    unittest.main(verbosity=2)
