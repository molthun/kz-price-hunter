
import json,sqlite3,tempfile
from unittest.mock import patch
from domain.scale_bench import run_scale_benchmark
original=sqlite3.connect
class WithoutDbstat(sqlite3.Connection):
    def execute(self,sql,*args,**kwargs):
        if 'FROM dbstat' in sql:
            raise sqlite3.OperationalError('audit: dbstat unavailable')
        return super().execute(sql,*args,**kwargs)
def connect(*args,**kwargs):
    kwargs['factory']=WithoutDbstat
    return original(*args,**kwargs)
with tempfile.TemporaryDirectory() as t:
    with patch('sqlite3.connect',connect),patch('resource.getrusage',side_effect=OSError('audit: unavailable')):
        cp=run_scale_benchmark(1000,db_path=t+'/fallback.db')
    c=original(t+'/fallback.db')
    actual=dict(c.execute('SELECT name,SUM(pgsize) FROM dbstat GROUP BY name'))
    c.close()
print(json.dumps({'metrics':cp.to_dict(),'metric_keys':list(cp.to_dict()),'actual_table_bytes':actual},indent=2))
