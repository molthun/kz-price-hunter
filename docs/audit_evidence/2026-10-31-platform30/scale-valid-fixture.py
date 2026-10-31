import json, tempfile
from unittest.mock import patch
import domain.scale_bench as scale
original=scale.SearchCore.search
observations=[]
def valid_search(self,*args,**kwargs):
    c=self.search_repo.conn
    if not observations:
        c.execute("UPDATE offers SET seller_id=(SELECT seller_id FROM channels WHERE channels.id=offers.channel_id)")
        c.commit()
    r=original(self,*args,**kwargs)
    observations.append(dict(query=args[0] if args else kwargs.get('query'),outcome=r.outcome,latency_ms=r.latency_ms,total_products=r.total_products))
    return r
with tempfile.TemporaryDirectory() as t, patch.object(scale.SearchCore,'search',valid_search):
    cp=scale.run_scale_benchmark(100000,db_path=t+'/valid.db')
    print(json.dumps(dict(metrics=cp.to_dict(),observations=observations,decision=scale.evaluate_storage_decision(cp).to_dict()),indent=2,ensure_ascii=False))
