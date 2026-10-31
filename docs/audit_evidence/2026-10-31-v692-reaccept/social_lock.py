"""Real second writer waits for social transaction and then applies operator DIFFERENT."""
import json
import threading
import unittest
from unittest.mock import patch

import database as db
from tests.sellers import test_fa69r_fixes as fixtures


class SocialLock(fixtures.OperatorDecisionTests):
    def test_social_writer_blocks_operator_and_final_pin_wins(self):
        trying,finished=threading.Event(),threading.Event()
        result={};errors=[];started=[]
        real_connect=db.get_connection
        original=self.pipeline._product_for
        def connect(*args,**kwargs):
            c=real_connect(*args,**kwargs)
            if threading.current_thread().name=='audit-social-operator':
                c.set_trace_callback(lambda sql:trying.set() if sql.upper().startswith('BEGIN IMMEDIATE') else None)
            return c
        def operator():
            try:result.update(db.resolve_disputed_pair_human(fixtures.pk.pair_hash(fixtures.A,fixtures.B),'different','audit'))
            except BaseException as exc:errors.append(type(exc).__name__)
            finally:finished.set()
        worker=threading.Thread(target=operator,name='audit-social-operator')
        def after_read(*args,**kwargs):
            value=original(*args,**kwargs)
            if self.c.in_transaction and not started:
                started.append(True);worker.start()
                self.assertTrue(trying.wait(2),'Operator reached BEGIN IMMEDIATE')
                self.assertFalse(finished.wait(.05),'Operator must wait for the social writer')
            return value
        with patch.object(db,'get_connection',side_effect=connect),patch.object(self.pipeline,'_product_for',side_effect=after_read):
            offer=self.pipeline.process_candidates(self.observation(),[fixtures.cand(900,fixtures.B)])[0]
            self.c.commit()
            worker.join(5)
        self.assertTrue(finished.is_set());self.assertFalse(errors,errors);self.assertTrue(result.get('ok'),result)
        actual=self.key_of(offer);pin=self.pinned()
        print('SOCIAL_LOCK='+json.dumps({'blocked_until_commit':True,'key':actual,'pin':pin}))
        self.assertEqual(actual,pin)


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite([SocialLock('test_social_writer_blocks_operator_and_final_pin_wins')]))
    raise SystemExit(not result.wasSuccessful())
