"""Real operator DIFFERENT decision between social identity resolution and Offer write.
Separate SQLite connections, temporary DB only. Expected pinned identity must win.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database as db
import product_key as pk
from domain.models import Seller, Channel
from domain.social_content import ContentObservation, ExtractedOfferCandidate
from domain.social_pipeline import SocialOfferPipeline
from repositories.seller_repo import SellerRepository
from repositories.channel_repo import ChannelRepository


class PinRace(unittest.TestCase):
    def run_order(self, during_resolve):
        with tempfile.TemporaryDirectory(prefix='kzph-social-pin-') as tmp, \
             patch.object(db,'DB_PATH',Path(tmp)/'prices.db'),patch('config.DATA_DIR',Path(tmp)):
            db.init_db()
            a,b='Apple EarPods Lightning','Apple EarPods USB-C'
            fields={'brand':'Apple','model':'EarPods','sku':None,'variants':[],'unbranded':False}
            old=pk.build_key(fields,a)
            db.save_cached_canonical_keys_batch({a:old,b:old})
            for i,title in enumerate((a,b)):
                db.save_or_update_product({'id':f'p{i}','title':title,'canonical_key':old,'shop':f'shop{i}',
                    'city':'Алматы','price':1000,'url':f'https://example.invalid/{i}'})
            db.save_title_fields_batch({a:fields,b:fields},'k2','local','audit')
            db.enqueue_disputed_pair(a,b,'social concurrent operator decision')
            c=db.get_connection()
            try:
                SellerRepository(c).save_or_update(Seller(id='social_known',slug='social_known',name='Known'))
                ChannelRepository(c).save_or_update(Channel(id='social_channel',seller_id='social_known',channel_type='telegram',name='Known'))
                c.commit()
                pipeline=SocialOfferPipeline(c)
                original=pipeline._resolve_canonical_product
                decision={}
                def interleave(*args,**kwargs):
                    product=original(*args,**kwargs)
                    self.assertFalse(c.in_transaction,'Must reach the actual unprotected read/write interval')
                    decision.update(db.resolve_disputed_pair_human(pk.pair_hash(a,b),'different','audit'))
                    self.assertTrue(decision.get('ok'),decision)
                    return product
                obs=ContentObservation(seller_id='social_known',channel_id='social_channel',post_id='77',
                    raw_metadata={'platform':'telegram','handle':'audit_known'})
                if during_resolve:
                    with patch.object(pipeline,'_resolve_canonical_product',side_effect=interleave):
                        offer=pipeline.process_candidates(obs,[ExtractedOfferCandidate(raw_title=b,price=900,city='Алматы')])[0]
                    c.commit()
                else:
                    offer=pipeline.process_candidates(obs,[ExtractedOfferCandidate(raw_title=b,price=900,city='Алматы')])[0]
                    c.commit()
                    decision.update(db.resolve_disputed_pair_human(pk.pair_hash(a,b),'different','audit'))
                    self.assertTrue(decision.get('ok'),decision)
                pinned=db.title_key_pins([b])[b]
                actual=c.execute('SELECT canonical_key FROM canonical_products WHERE id=?',(offer.product_id,)).fetchone()[0]
                print('SOCIAL_OPERATOR_RACE='+json.dumps({'operator_ok':decision['ok'],'pin':pinned,'social_key':actual,
                    'order':'decision_during_resolve' if during_resolve else 'decision_after_save'}))
                self.assertEqual(actual,pinned,'Social save after the decision must preserve operator DIFFERENT')
            finally:c.close()

    def test_operator_during_social_resolution(self):
        self.run_order(True)

    def test_operator_after_social_save(self):
        self.run_order(False)


if __name__=='__main__':unittest.main(verbosity=2)
