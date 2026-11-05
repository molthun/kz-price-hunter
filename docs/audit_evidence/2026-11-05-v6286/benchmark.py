"""Synthetic read-only-in-memory comparison of ac9286a and current GPU filter."""
import ast
import json
import re
import sqlite3
import statistics
import subprocess
import time
from typing import Optional

from repositories.search_repo import SearchRepository, _gpu_compact, _sql_compact, GPU_MODEL_SUFFIXES

BASE = 'ac9286afd12f11153113870df8830af690717dd2'
old_source = subprocess.check_output(['git','show',BASE+':repositories/search_repo.py'],text=True)
tree = ast.parse(old_source)
cls = next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='SearchRepository')
fn = next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='_model_filter')
fn.decorator_list = []
namespace = dict(Optional=Optional,re=re,_gpu_compact=_gpu_compact,_sql_compact=_sql_compact,GPU_MODEL_SUFFIXES=GPU_MODEL_SUFFIXES)
exec(compile(ast.Module(body=[fn],type_ignores=[]),'<baseline _model_filter>','exec'),namespace)
old_filter = namespace['_model_filter']

c = sqlite3.connect(':memory:')
c.executescript('CREATE TABLE canonical_products(id TEXT PRIMARY KEY,title TEXT,model TEXT,category TEXT); CREATE INDEX idx_cat ON canonical_products(category);')
families = ['GeForce RTX 4060','GeForce RTX 4060 Ti','GeForce RTX 4070','GeForce RTX 4070 Super',
            'GeForce RTX 4070 Ti','GeForce RTX 4070 Ti Super','Radeon RX 7900 XT','Radeon RX 7900 XTX','Arc B580']
seps = ['', ' ', '  ', '\t', '\u00a0', '\u202f']
rows = []
for i in range(100000):
    if i % 100 == 0:
        n = i // 100
        model = seps[n % len(seps)].join(families[n % len(families)].split())
        rows.append((str(i),'Видеокарта '+model+' 12GB',model if n % 2 else None,'gpus'))
    else:
        rows.append((str(i),'Смартфон Samsung Galaxy S24 256GB','Galaxy S24','smartphones'))
c.executemany('INSERT INTO canonical_products VALUES(?,?,?,?)',rows)
c.commit()

def read(impl,family):
    sql,args = impl(family)
    return c.execute('SELECT p.id FROM canonical_products p WHERE 1=1 '+sql,args).fetchall()

result = {'baseline_sha':BASE,'candidate_sha':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
          'rows':100000,'gpu_rows':1000,'storage':':memory:','repetitions':3,'note':'Synthetic model predicate, not full API or stand latency; other regression processes may run concurrently.','models':[]}
for family in families:
    assert read(old_filter,family) == read(SearchRepository._model_filter,family),family
    timings = {'old':[],'new':[]}
    for turn in range(3):
        ordered = [('old',old_filter),('new',SearchRepository._model_filter)]
        if turn % 2:
            ordered.reverse()
        for label,impl in ordered:
            start = time.perf_counter()
            read(impl,family)
            timings[label].append((time.perf_counter()-start)*1000)
    vm = {}
    for label,impl in [('old',old_filter),('new',SearchRepository._model_filter)]:
        ticks = [0]
        def progress():
            ticks[0] += 1
            return 0
        c.set_progress_handler(progress,1000)
        read(impl,family)
        c.set_progress_handler(None,0)
        vm[label] = ticks[0]*1000
    old_ms = statistics.median(timings['old'])
    new_ms = statistics.median(timings['new'])
    result['models'].append({'model':family,'results':len(read(SearchRepository._model_filter,family)),
                             'old_median_ms':round(old_ms,3),'new_median_ms':round(new_ms,3),
                             'speedup':round(old_ms/new_ms,2),'approx_vm_steps':vm})
print(json.dumps(result,ensure_ascii=False,indent=2))
