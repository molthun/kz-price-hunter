
import dataclasses,json,sqlite3
from domain.scale_bench import ScaleCheckpoint,evaluate_storage_decision,get_current_scale_status
from repositories import init_schema_v2
base=ScaleCheckpoint('100k',100000,100000,20000,1000,2000,20.,10.,20.,100.,2.,10.,.1,100.,5.)
cases={}
for name,changes in {
 'valid':{},'no_offers':{'actual_offers':0},'short_checkpoint':{'actual_offers':500},
 'nan_latency':{'search_p95_ms':float('nan')},'nan_backup':{'backup_duration_s':float('nan')},
 'nan_write':{'write_latency_1k_ms':float('nan')},'negative_size':{'db_size_mb':-1.},
 '80ms':{'search_p95_ms':80.}
}.items():
 cp=dataclasses.replace(base,**changes);decision=evaluate_storage_decision(cp)
 cases[name]={'decision':decision.decision,'is_sqlite_sufficient':decision.is_sqlite_sufficient,'summary':decision.summary,'reasons':decision.reasons}
c=sqlite3.connect(':memory:');init_schema_v2(c);cases['initialized_without_measurements']=get_current_scale_status(c)
print(json.dumps(cases,indent=2,ensure_ascii=False))
