"""Independent watch_refresh fault/process/generation acceptance on synthetic data."""
import os, sys, tempfile, unittest, sqlite3, subprocess, signal
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
os.environ['DATA_DIR'] = tempfile.mkdtemp(prefix='kzph6411-mechanism-')
for key in ('OPENAI_API_KEY','GEMINI_API_KEY','TELEGRAM_BOT_TOKEN'): os.environ[key]=''
import database as db
from tests.core.test_offer_retirement import OfferRetirementTest

class Mechanism(OfferRetirementTest):
    def job(self):
        with db.get_connection() as conn:
            row = conn.execute('SELECT phase,gen FROM watch_refresh WHERE product_id=?',(self.pid('drift'),)).fetchone()
            return tuple(row) if row else None
    async def fault(self, sql, expected_phase):
        await self.prepare_deferred_return()
        with db.get_connection() as conn:
            conn.execute(sql); conn.commit()
        try:
            with self.assertRaises(sqlite3.IntegrityError): db.drain_watch_refresh()
        finally:
            with db.get_connection() as conn:
                conn.execute('DROP TRIGGER audit_fault'); conn.commit()
        self.assertEqual(self.job()[0],expected_phase)
        db.drain_watch_refresh()
        self.assertEqual((len(self.events()),self.outbox()),(1,1))
        self.assertIsNone(self.job())
    async def test_rollback_phase_change(self):
        await self.fault("CREATE TRIGGER audit_fault BEFORE UPDATE OF phase ON watch_refresh WHEN NEW.phase='current' BEGIN SELECT RAISE(ABORT,'phase'); END", 'retire')
    async def test_rollback_event_insert(self):
        await self.fault("CREATE TRIGGER audit_fault BEFORE INSERT ON watch_events BEGIN SELECT RAISE(ABORT,'event'); END", 'current')
    async def test_rollback_outbox_insert(self):
        await self.fault("CREATE TRIGGER audit_fault BEFORE INSERT ON notification_outbox BEGIN SELECT RAISE(ABORT,'outbox'); END", 'current')
    async def test_rollback_state_after_event(self):
        await self.fault("CREATE TRIGGER audit_fault BEFORE UPDATE ON watch_state WHEN NEW.was_available=1 AND OLD.was_available=0 BEGIN SELECT RAISE(ABORT,'state'); END", 'current')
    async def test_rollback_ack_delete(self):
        await self.fault("CREATE TRIGGER audit_fault BEFORE DELETE ON watch_refresh BEGIN SELECT RAISE(ABORT,'ack'); END", 'current')
    def child(self, source):
        return subprocess.Popen([sys.executable,'-u','-c',source,self.pid('drift')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=os.environ.copy())
    async def test_two_processes_stale_retire_snapshot(self):
        await self.prepare_deferred_return()
        code="""import database as d,sys
orig=d._apply_retire_transition
def paused(*a):
 print('READY',flush=True);sys.stdin.readline();return orig(*a)
d._apply_retire_transition=paused
d.refresh_retired_watches([sys.argv[1]])
"""
        children=[self.child(code),self.child(code)]
        try:
            for child in children: self.assertEqual(child.stdout.readline().strip(),'READY')
            for child in children:
                out,err=child.communicate('\n',timeout=10)
                self.assertEqual(child.returncode,0,err)
        finally:
            for child in children:
                if child.poll() is None: child.kill();child.communicate(timeout=5)
        self.assertEqual((len(self.events()),self.outbox()),(1,1))
        self.assertIsNone(self.job())
    async def kill_after(self, phase):
        await self.prepare_deferred_return()
        code="""import database as d,sys,os,signal
phase=sys.argv[2]
name='_apply_retire_transition' if phase=='retire' else 'evaluate_watches'
orig=getattr(d,name)
def killed(*a,**kw):
 result=orig(*a,**kw);os.kill(os.getpid(),signal.SIGKILL)
setattr(d,name,killed)
d.refresh_retired_watches([sys.argv[1]])
"""
        result=subprocess.run([sys.executable,'-c',code,self.pid('drift'),phase],capture_output=True,text=True,env=os.environ.copy(),timeout=10)
        self.assertEqual(result.returncode,-signal.SIGKILL)
        self.assertEqual(self.job()[0],'current')
        db.init_db()
        self.assertEqual((len(self.events()),self.outbox()),(1,1))
        self.assertIsNone(self.job())
    async def test_sigkill_after_retire_commit(self): await self.kill_after('retire')
    async def test_sigkill_after_event_commit(self): await self.kill_after('event')
    async def test_stale_generation_is_noop(self):
        await self.prepare_deferred_return()
        jobs=db._watch_refresh_jobs([self.pid('drift')],'retire')
        with db.get_connection() as conn:
            db._queue_watch_refresh_conn(conn,[self.pid('drift')]);conn.commit()
        self.assertEqual(db._apply_retire_transition(jobs),[])
        self.assertEqual(self.job(),('retire',jobs[self.pid('drift')]+1))
        db.drain_watch_refresh()
        self.assertEqual((len(self.events()),self.outbox()),(1,1))

    async def test_old_ack_cannot_delete_new_current_generation(self):
        await self.prepare_deferred_return()
        original=db.evaluate_watches
        def retire_again_after_event(offers):
            result=original(offers)
            with db.get_connection() as conn:
                conn.execute('UPDATE products SET is_active=0 WHERE id=?',(self.pid('drift'),))
                conn.execute('UPDATE offers SET is_active=0 WHERE id=?',('off_'+self.pid('drift'),))
                db._queue_watch_refresh_conn(conn,[self.pid('drift')]);conn.commit()
            jobs=db._watch_refresh_jobs([self.pid('drift')],'retire')
            db._apply_retire_transition(jobs)
            return result
        with patch.object(db,'evaluate_watches',side_effect=retire_again_after_event):
            db.refresh_retired_watches([self.pid('drift')])
        self.assertEqual(self.job(),('current',1))
        db.drain_watch_refresh()
        self.assertIsNone(self.job())
        await self.scan(['kept','drift'])
        self.assertEqual((len(self.events()),self.outbox()),(2,2))
    async def test_migrate_pending_job_from_pre_phase_schema(self):
        await self.prepare_deferred_return()
        with db.get_connection() as conn:
            conn.execute('ALTER TABLE watch_refresh DROP COLUMN gen')
            conn.execute('ALTER TABLE watch_refresh DROP COLUMN phase')
            conn.commit()
        db.init_db()
        self.assertIsNone(self.job())
        self.assertEqual((len(self.events()),self.outbox()),(1,1))

if __name__=='__main__': unittest.main(defaultTest='Mechanism',verbosity=2)
