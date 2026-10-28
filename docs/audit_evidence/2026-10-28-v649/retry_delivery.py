"""Independent retry acceptance: actual SQLite trigger fails refresh ACK after event commit."""
import os
import sys
import tempfile
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
os.environ['DATA_DIR'] = tempfile.mkdtemp(prefix='kzph649-retry-')
for key in ('OPENAI_API_KEY', 'GEMINI_API_KEY', 'TELEGRAM_BOT_TOKEN'):
    os.environ[key] = ''
import database as db
from tests.core.test_offer_retirement import OfferRetirementTest

class RetryAcceptance(OfferRetirementTest):
    async def test_retry_after_refresh_delete_failure_does_not_duplicate_event(self):
        await self.prepare_old_base()
        with patch.object(db, 'evaluate_watches', side_effect=sqlite3.OperationalError('synthetic startup busy')):
            db.init_db()
        qm = self.qm()
        receipt = await qm.enqueue([self.item('kept'), self.item('drift')], 'Contract', {}, shop_key=self.shop,
            reconcile={'key': self.shop, 'url': 'https://example.invalid/cat', 'complete': True})
        item = await qm.dequeue(timeout=0.05)
        self.assertEqual(item['task_id'], receipt.task_id)
        with db.get_connection() as conn:
            conn.execute("CREATE TRIGGER audit_fail_refresh_delete BEFORE DELETE ON watch_refresh BEGIN SELECT RAISE(ABORT, 'synthetic refresh delete failure'); END")
            conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            await self.run_item(qm, item)
        self.assertEqual(len(self.events()), 1)
        with db.get_connection() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM watch_refresh WHERE product_id=?', (self.pid('drift'),)).fetchone()[0], 1)
            conn.execute('DROP TRIGGER audit_fail_refresh_delete')
            conn.commit()
        self.assertEqual(await qm.nack(item, error='synthetic refresh delete failure'), 'pending')
        retry = await qm.dequeue(timeout=0.05)
        self.assertEqual(retry['task_id'], receipt.task_id)
        self.assertTrue(await self.run_item(qm, retry))
        events = self.events()
        with db.get_connection() as conn:
            event_ids = [-event['id'] for event in events]
            outbox_count = conn.execute('SELECT COUNT(*) FROM notification_outbox WHERE alert_id IN (' + ','.join('?' for _ in event_ids) + ')', event_ids).fetchone()[0]
        print({'event_count': len(events), 'outbox_count': outbox_count}, flush=True)
        self.assertEqual(outbox_count, len(events))
        self.assertEqual(len(events), 1, 'one retirement/return transition created duplicate events after retry')

if __name__ == '__main__':
    unittest.main(defaultTest='RetryAcceptance', verbosity=2)
