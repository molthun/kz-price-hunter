"""Independent acceptance; synthetic temporary data, no external delivery."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
os.environ['DATA_DIR'] = tempfile.mkdtemp(prefix='kzph646-accept-')
for key in ('OPENAI_API_KEY', 'GEMINI_API_KEY', 'TELEGRAM_BOT_TOKEN'):
    os.environ[key] = ''
import database as db
from web import server
from tests.core.test_offer_retirement import OfferRetirementTest

class Acceptance(OfferRetirementTest):
    async def test_upgrade_retirement_then_return_notifies_existing_watch(self):
        await self.scan(['kept', 'legacy'])
        self.watch('legacy', 'back_in_stock')
        db.evaluate_watches(db.watched_offers([self.pid('legacy')]))
        # Persisted 6.4.5 state: reconcile retired only legacy products; watch still available.
        with db.get_connection() as conn:
            conn.execute('UPDATE products SET is_active=0 WHERE id=?', (self.pid('legacy'),))
            conn.execute('UPDATE product_sources SET active=0 WHERE product_id=?', (self.pid('legacy'),))
            conn.commit()
        db.init_db()  # real 6.4.6 startup repair
        self.assert_available('legacy', False)
        self.assertEqual(len(self.events()), 0)
        await self.scan(['kept', 'legacy'])
        self.assert_available('legacy', True)
        self.assertEqual(len(self.events()), 1, 'startup repair lost back_in_stock transition')

    async def test_derived_retry_preserves_retirement_transition(self):
        await self.scan(['kept', 'gone'])
        self.watch('gone', 'back_in_stock')
        db.evaluate_watches(db.watched_offers([self.pid('gone')]))
        qm = self.qm()
        admission = await qm.enqueue([self.item('kept')], 'Contract', {}, shop_key=self.shop,
            reconcile={'key': self.shop, 'url': 'https://example.invalid/cat', 'complete': True})
        item = await qm.dequeue(timeout=0.05)
        with patch.object(server, '_evaluate_retired_watches', side_effect=RuntimeError('synthetic derived failure')):
            with self.assertRaises(RuntimeError):
                await self.run_item(qm, item)
        self.assertEqual(db.spool_retired(admission.task_id), [self.pid('gone')])
        await qm.nack(item, error='synthetic', max_retries=0)
        db.spool_resolve_failures('retry', [admission.task_id])
        item = await qm.dequeue(timeout=0.05)
        self.assertTrue(await self.run_item(qm, item))
        self.assert_available('gone', False)
        await self.scan(['kept', 'gone'])
        self.assertEqual(len(self.events()), 1)

    async def test_direct_path_retirement_and_return(self):
        async def direct(names):
            products = [self.item(n) for n in names]
            with patch.object(server, 'check_market_arbitrage', return_value=None):
                await server._save_and_detect_direct(products, 'Contract', {}, shop_key=self.shop)
            retired = db.reconcile_source(self.shop, 'https://example.invalid/cat', products, True)
            await server._evaluate_retired_watches(retired, strict=True)
        await direct(['kept', 'gone'])
        self.watch('gone', 'back_in_stock')
        db.evaluate_watches(db.watched_offers([self.pid('gone')]))
        await direct(['kept'])
        self.assert_available('gone', False)
        await direct(['kept', 'gone'])
        self.assert_available('gone', True)
        self.assertEqual(len(self.events()), 1)

if __name__ == '__main__':
    unittest.main(defaultTest='Acceptance', verbosity=2)
