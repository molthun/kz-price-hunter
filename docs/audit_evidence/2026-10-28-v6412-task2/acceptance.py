"""Independent audit: immutable dirty snapshot, synthetic data, no external delivery."""
import os,sys,tempfile,time,threading,json,unittest,subprocess
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,os.environ.get('AUDIT_CODE_DIR',str(Path.cwd())))
os.environ['DATA_DIR']=tempfile.mkdtemp(prefix='kzph6412-independent-')
for key in ('OPENAI_API_KEY','GEMINI_API_KEY','TELEGRAM_BOT_TOKEN'):os.environ[key]=''
import database as db
from tests.core.test_queue_contract import QueueContractBase,prod,WATCH_USER
from web import server
BASE=Path(os.environ.get('AUDIT_PREVIOUS_CODE',str(Path.cwd().parent/(Path.cwd().name+'-base'))))
class Audit(QueueContractBase):
    async def test_upgrade_partial_200_chunk_to_100_preserves_drop(self):
        tag=os.urandom(4).hex()
        items=[prod(f'{tag}-{i}',200) for i in range(300)]
        db.save_or_update_products_batch(items)
        pid=items[150]['id']
        db.upsert_telegram_user({'id':WATCH_USER,'first_name':'Synthetic'})
        watch=db.create_watch(WATCH_USER,{'kind':'product','target':pid,'condition':'any_drop','cooldown_hours':0,'quiet_from':'','quiet_to':''})
        db.evaluate_watches(db.watched_offers([pid]))
        q=self.qm();products=[dict(p,price=100) for p in items]
        receipt=await q.enqueue(products,'Contract',{},shop_key=tag)
        item=await q.dequeue(timeout=.05)
        self.assertTrue(db.spool_apply_chunk(receipt.task_id,item['claim_token'],0,products[:200])) # actual previous-release chunk
        await q.nack(item,error='restart across upgrade')
        resumed=await q.dequeue(timeout=.05)
        with patch.object(server,'check_market_arbitrage',return_value=None):
            await server._execute_queue_item(resumed)
        history=db.spool_history(receipt.task_id)
        events=[e for e in db.watch_events(WATCH_USER) if e['watch_id']==watch['id']]
        print(json.dumps({'case':'upgrade','original_price':200,'saved_history_price':history[pid]['old_price'],'watch_events':len(events)}),flush=True)
        self.assertTrue(await q.ack(resumed))
        self.assertEqual(len(events),1,'upgrade lost price-drop event for overlapping old/new chunks')
    async def test_rollback_partial_100_chunk_to_real_6411_persists_all(self):
        tag=os.urandom(4).hex();products=[prod(f'{tag}-{i}',100) for i in range(300)]
        q=self.qm();receipt=await q.enqueue(products,'Contract',{},shop_key=tag)
        item=await q.dequeue(timeout=.05)
        self.assertTrue(db.spool_apply_chunk(receipt.task_id,item['claim_token'],0,products[:db.SPOOL_CHUNK_SIZE]))
        await q.nack(item,error='restart across rollback')
        import release_gate
        gate=release_gate.rollback_check(str(db.DB_PATH),str(BASE),sys.executable)
        code="""import sys,os,asyncio,json
sys.path.insert(0,os.getcwd())
from unittest.mock import patch
from queue_service import ProductQueueManager
from web import server
async def main():
 q=ProductQueueManager();item=await q.dequeue(timeout=.1)
 with patch.object(server,'check_market_arbitrage',return_value=None): await server._execute_queue_item(item)
 print(json.dumps({'ack':await q.ack(item)}))
asyncio.run(main())
"""
        env=dict(os.environ,PYTHONPATH=str(BASE))
        run=subprocess.run([sys.executable,'-c',code],cwd=BASE,env=env,capture_output=True,text=True,timeout=20)
        self.assertEqual(run.returncode,0,run.stderr)
        with db.get_connection() as conn:
            count=conn.execute('SELECT COUNT(*) FROM products WHERE id LIKE ?', (tag+'-%',)).fetchone()[0]
            remaining=conn.execute('SELECT COUNT(*) FROM product_queue_spool WHERE task_id=?',(receipt.task_id,)).fetchone()[0]
        print(json.dumps({'case':'rollback','producer_chunk_size':db.SPOOL_CHUNK_SIZE,'release_gate_ok':gate.get('ok'),'expected':300,'persisted':count,'remaining_tasks':remaining,'previous_worker':run.stdout.strip()}),flush=True)
        self.assertEqual(count,300,'previous-release worker ACKed incomplete products after chunk-size change')
    async def test_implicit_write_execution_is_measured_as_hold(self):
        acquired=threading.Event();observed=[]
        with db.get_connection() as conn:
            def delayed():
                acquired.set();time.sleep(.2);return 'synthetic'
            conn.create_function('audit_delay',0,delayed)
            def contender():
                acquired.wait(2);t0=time.monotonic()
                with db.get_connection() as other:
                    other.execute('BEGIN IMMEDIATE');observed.append((time.monotonic()-t0)*1000);other.rollback()
            t=threading.Thread(target=contender);t.start()
            db.reset_write_tx_stats()
            conn.execute("INSERT OR REPLACE INTO schema_metadata(name,value) VALUES('audit_slow_sql',audit_delay())")
            conn.commit();t.join(3)
        stats=db.get_write_tx_stats()['operations']['test_implicit_write_execution_is_measured_as_hold']
        print(json.dumps({'case':'implicit_write_timing','blocked_contender_ms':observed,'stats':stats}),flush=True)
        self.assertGreater(observed[0],150)
        self.assertGreater(stats['max_hold_ms'],150,'SQL execution while holding lock was attributed to wait, not hold')
if __name__=='__main__':unittest.main(defaultTest='Audit',verbosity=2)
