import json,sqlite3,threading,time,tempfile
from unittest.mock import patch
from domain.scale_bench import run_scale_benchmark
from domain.search_core import SearchCore
real_connect=sqlite3.connect
real_search=SearchCore.search
timing={}
class ObservedConnection(sqlite3.Connection):
    def execute(self,sql,*args,**kwargs):
        if 'CONC_SKU' in sql:timing['worker_write_start']=time.monotonic()
        return super().execute(sql,*args,**kwargs)
    def backup(self,*args,**kwargs):
        timing['backup_start']=time.monotonic()
        try:return super().backup(*args,**kwargs)
        finally:timing['backup_end']=time.monotonic()
def connect(*args,**kwargs):
    kwargs['factory']=ObservedConnection
    return real_connect(*args,**kwargs)
def search(self,*args,**kwargs):
    if threading.current_thread() is not threading.main_thread():
        time.sleep(.25)
        timing['worker_search_sql_start']=time.monotonic()
    return real_search(self,*args,**kwargs)
with tempfile.TemporaryDirectory() as t,patch('sqlite3.connect',connect),patch.object(SearchCore,'search',search):
    cp=run_scale_benchmark(1000,db_path=t+'/overlap.db')
print(json.dumps({'metrics':cp.to_dict(),'timing':timing,'write_overlapped_backup':timing['backup_start']<=timing['worker_write_start']<=timing['backup_end'],'search_sql_started_during_backup':timing['backup_start']<=timing['worker_search_sql_start']<=timing['backup_end']},indent=2))
