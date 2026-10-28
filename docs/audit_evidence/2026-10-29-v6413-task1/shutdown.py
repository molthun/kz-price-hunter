"""Independent cancellation acceptance; temporary data and controlled page duration."""
import os,sys,tempfile,threading,asyncio,unittest,json
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path.cwd()))
os.environ['DATA_DIR']=tempfile.mkdtemp(prefix='kzph6413-shutdown-')
for key in ('OPENAI_API_KEY','GEMINI_API_KEY','TELEGRAM_BOT_TOKEN'):os.environ[key]=''
import database as db
from web import server

class Shutdown(unittest.IsolatedAsyncioTestCase):
    async def test_shield_waits_for_submitted_operation_on_cancel(self):
        started=threading.Event();release=threading.Event();finished=threading.Event()
        def operation():
            started.set()
            release.wait(timeout=3)
            finished.set()
        task=asyncio.create_task(server._run_db_shielded(operation))
        try:
            self.assertTrue(await asyncio.to_thread(started.wait,1))
            task.cancel();await asyncio.sleep(.05)
            early=task.done();thread_finished=finished.is_set()
            print(json.dumps({'case':'helper_cancel','task_done_before_release':early,'thread_finished':thread_finished}),flush=True)
        finally:
            release.set()
            await asyncio.gather(task,return_exceptions=True)
            self.assertTrue(await asyncio.to_thread(finished.wait,1))
            await asyncio.sleep(.01)
        self.assertFalse(early,'cancelled helper returned before its submitted DB operation completed')
    async def test_worker_waits_for_current_page_before_cancellation_finishes(self):
        db.init_db()
        ids=[f'cancel-{i}' for i in range(600)]
        with db.get_connection() as conn:
            conn.execute('DELETE FROM watch_refresh');db._queue_watch_refresh_conn(conn,ids);conn.commit()
        started=threading.Event();release=threading.Event();finished=threading.Event()
        original=db.drain_watch_refresh
        def page(*args):
            started.set();release.wait(timeout=3)
            try:return original(*args)
            finally:finished.set()
        with patch.object(db,'drain_watch_refresh',side_effect=page):
            task=asyncio.create_task(server.startup_watch_refresh_worker())
            try:
                self.assertTrue(await asyncio.to_thread(started.wait,1))
                task.cancel();await asyncio.sleep(.05)
                early=task.done();before=db.watch_refresh_count()
            finally:
                release.set()
                await asyncio.gather(task,return_exceptions=True)
                self.assertTrue(await asyncio.to_thread(finished.wait,1))
                await asyncio.sleep(.01)
        after=db.watch_refresh_count()
        print(json.dumps({'case':'worker_cancel','worker_done_before_page_release':early,'jobs_before_release':before,'jobs_after_page_finishes':after}),flush=True)
        self.assertEqual((before,after),(600,100))
        self.assertFalse(early,'worker cancellation finished while the DB page was still running')

if __name__=='__main__':unittest.main(verbosity=2)
