"""Independent check of corrected extension membership, review replay and phase-specific weights."""
import collections
import contextlib
import importlib.util
import io
import json
import math
from pathlib import Path
import random
import subprocess
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).parent
SRC=ROOT/'docs/audit_evidence/2026-10-31-c3e'
EXT=ROOT/'docs/audit_evidence/2026-11-01-c2-ext'
read=lambda p:json.loads(p.read_text())
key=lambda r:frozenset((r['a'],r['b']))
data=read(ROOT/'docs/audit_evidence/2026-10-30-c3/stand-data.json')
s=read(ROOT/'scripts/k2_labeled_sample.json')
before=json.loads(subprocess.check_output(['git','show','c0199e5:scripts/k2_labeled_sample.json'],cwd=ROOT))
assert s['known_pairs']==before['known_pairs'] and s['pairs'][:530]==before['pairs'][:530]
assert [(r['a'],r['b'],r['strata']) for r in s['pairs']]==[(r['a'],r['b'],r['strata']) for r in before['pairs']]
raw=read(EXT/'raw_drawn_pairs.json')
order=read(SRC/'ext_blind_order.json')
assert len(order)==len(raw)==740
assert {frozenset(p) for p in order}=={key(r) for r in raw}
review=read(SRC/'ext_blind_review.json')
labels={frozenset(pair):'same' for pair in order}
for label in ('different','packaging','uncertain'):
    for index in review[label]:labels[frozenset(order[int(index)])]=label
for index in review['uncertain_packaging']:labels[frozenset(order[index])]='uncertain'
extended=[r for r in s['pairs'] if r.get('phase')=='ext']
assert len(extended)==740 and all(r['label']==labels[key(r)] for r in extended)
assert collections.Counter(r['label'] for r in extended)=={'same':658,'different':23,'packaging':1,'uncertain':58}
for index in (108,210,277,358,595,624):
    assert next(r for r in extended if key(r)==key(raw[index]))['label']=='different'

# Replay the applicator over its immutable old input; intercept all writes and the sample read.
spec=importlib.util.spec_from_file_location('ext_review',SRC/'apply_ext_review.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
actual_read=Path.read_text;captured={}
sample_path=ROOT/'scripts/k2_labeled_sample.json'
def read_intercept(path,*args,**kwargs):
    return json.dumps(before,ensure_ascii=False) if path==sample_path else actual_read(path,*args,**kwargs)
def write_intercept(path,value,*args,**kwargs):
    captured[path]=json.loads(value);return len(value)
with patch.object(Path,'read_text',read_intercept),patch.object(Path,'write_text',write_intercept),contextlib.redirect_stdout(io.StringIO()):module.main()
assert all(value==read(path) for path,value in captured.items())

groups={'retained':data['retained'],'added':data['added'],
 'dropped_mixed':[r for r in data['dropped'] if not((r['new_a'] or '').startswith('k2:') and (r['new_b'] or '').startswith('k2:'))],
 'dropped_both_k2':[r for r in data['dropped'] if (r['new_a'] or '').startswith('k2:') and (r['new_b'] or '').startswith('k2:')]}
layers={key(r):name for name,group in groups.items() for r in group}
fixed=collections.defaultdict(list);drawn=collections.defaultdict(list)
for r in s['known_pairs']:fixed[layers[key(r)]].append(r)
for r in s['pairs']:
    layer=layers.get(key(r))
    if r['strata']==['old_set'] or layer is None:continue
    if layer in ('added','retained') and r.get('phase')!='ext':fixed[layer].append(r)
    elif r.get('phase')=='ext' or layer in ('dropped_mixed','dropped_both_k2'):drawn[layer].append(r)
assert {n:len(fixed[n]) for n in groups}=={'retained':165,'added':366,'dropped_mixed':46,'dropped_both_k2':160}
assert len(groups['added'])-len(fixed['added'])==1914 and len(groups['retained'])-len(fixed['retained'])==839
assert len(drawn['added'])==500 and len(drawn['retained'])==240
def bounds(n,total):
    z=2.734;p=n/total;den=1+z*z/total
    center=(p+z*z/(2*total))/den
    half=z*math.sqrt(p*(1-p)/total+z*z/(4*total*total))/den
    return round(center-half,4),round(center+half,4)
out={}
for name,group in groups.items():
    label=lambda r:'different' if r['label']=='packaging' else r['label']
    k=collections.Counter(label(r) for r in fixed[name]);d=collections.Counter(label(r) for r in drawn[name])
    a,b,u=d['same'],d['different'],d['uncertain'];n=sum(d.values());pool=len(group)-sum(k.values())
    out[name]={'true':k['same']+pool*a/n,'false':k['different']+pool*b/n,'open':k['uncertain']+pool*u/n,
      'true_low':k['same']+pool*bounds(a,n)[0],'true_high':k['same']+k['uncertain']+pool*bounds(n-b,n)[1],
      'false_low':k['different']+pool*bounds(b,n)[0],'false_high':k['different']+k['uncertain']+pool*bounds(n-a,n)[1]}
q=read(HERE/'quality.json')
assert q==read(SRC/'quality.json') and read(HERE/'registry.json')==read(SRC/'registry.json')
for name,row in out.items():
    assert all(round(v,1)==q['weighted']['owner_decision']['layers'][name][k] for k,v in row.items())
base=('retained','dropped_mixed','dropped_both_k2');cand=('retained','added')
total=lambda names,field:sum(out[n][field] for n in names)
result={'baseline':{k:round(total(base,k)) for k in ('true','false','open','true_low','true_high','false_low','false_high')},
 'candidate':{k:round(total(cand,k)) for k in ('true','false','open','true_low','true_high','false_low','false_high')},
 'candidate_vs_baseline_true_low':round(total(cand,'true_low')/total(base,'true_high'),3),
 'false_merges_ratio_high':round(total(cand,'false_high')/total(base,'false_low'),3)}
assert all(v==q['weighted']['owner_decision'][k] for k,v in result.items())
(HERE/'quality-independent-summary.json').write_text(json.dumps({'review_replayed':True,'six_previous_label_errors_corrected':True,
 'old_rows_unchanged':True,'fixed_counts':{n:len(fixed[n]) for n in groups},'independent_metrics':result,
 'limits':'All remaining label meanings and sources were not independently certified.'},ensure_ascii=False,indent=2))
print('PASS review replay, all six corrected labels, frozen rows, phase frames and independent owner-decision arithmetic')
print(json.dumps(result,indent=1))
