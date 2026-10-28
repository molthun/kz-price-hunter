"""Independent G02 startup acceptance, isolated synthetic data; no external delivery."""
import os
import sys
import tempfile
import unittest
import sqlite3
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
os.environ['DATA_DIR'] = tempfile.mkdtemp(prefix='kzph647-edges-')
for key in ('OPENAI_API_KEY', 'GEMINI_API_KEY', 'TELEGRAM_BOT_TOKEN'):
    os.environ[key] = ''
import database as db
from tests.core.test_offer_retirement import OfferRetirementTest

class StartupEdges(OfferRetirementTest):
    async def prepare_old(self, already_retired=False):
        await self.scan(['kept', 'old'])
        self.watch('old', 'back_in_stock')
        db.evaluate_watches(db.watched_offers([self.pid('old')]))
        with db.get_connection() as conn:
            conn.execute('UPDATE products SET is_active=0 WHERE id=?', (self.pid('old'),))
            conn.execute('UPDATE product_sources SET active=0 WHERE product_id=?', (self.pid('old'),))
            if already_retired:
                conn.execute('UPDATE offers SET is_active=0 WHERE id=?', ('off_' + self.pid('old'),))
            conn.commit()

    async def test_repeated_startup_and_same_price_return_no_duplicate(self):
        await self.prepare_old()
        db.init_db()
        db.init_db()
        self.assert_available('old', False)
        self.assertEqual(len(self.events()), 0)
        await self.scan(['kept', 'old'])
        db.init_db()
        await self.scan(['kept', 'old'])
        self.assertEqual(len(self.events()), 1)

    async def test_watch_failure_then_restart_retries_obligation(self):
        await self.prepare_old()
        with patch.object(db, 'evaluate_watches', side_effect=sqlite3.OperationalError('synthetic busy')):
            db.init_db()
        self.assert_available('old', False)
        db.init_db()  # failed watch update must be recoverable on another startup
        await self.scan(['kept', 'old'])
        self.assertEqual(len(self.events()), 1, 'lost startup obligation after transient watch failure')

    async def test_upgrade_from_646_repairs_already_retired_offer_watch(self):
        await self.prepare_old(already_retired=True)
        db.init_db()
        self.assert_available('old', False)
        await self.scan(['kept', 'old'])
        self.assertEqual(len(self.events()), 1, '6.4.6 retirement excluded from startup repair')

if __name__ == '__main__':
    unittest.main(defaultTest='StartupEdges', verbosity=2)
