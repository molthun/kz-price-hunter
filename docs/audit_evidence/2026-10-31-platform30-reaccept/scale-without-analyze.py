
import json,sqlite3,tempfile
from unittest.mock import patch
from domain.scale_bench import run_scale_benchmark
original=sqlite3.connect
skipped=[]
class ConnectionWithoutAnalyze(sqlite3.Connection):
    def execute(self,sql,*args,**kwargs):
        if sql.strip().upper()=='ANALYZE':
            skipped.append(sql)
            return super().execute('SELECT 1')
        return super().execute(sql,*args,**kwargs)
def connect(*args,**kwargs):
    kwargs['factory']=ConnectionWithoutAnalyze
    return original(*args,**kwargs)
with tempfile.TemporaryDirectory() as t,patch('sqlite3.connect',connect):
    cp=run_scale_benchmark(100000,db_path=t+'/without-analysis.db')
print(json.dumps({'metrics':cp.to_dict(),'skipped_analyze':skipped},indent=2))
