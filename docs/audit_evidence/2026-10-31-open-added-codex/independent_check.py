"""Read-only replay and independent numeric verification of the open-added submission."""
import collections
import contextlib
import copy
import importlib.util
import io
import json
import math
from pathlib import Path
import re
import sys
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
HERE=Path(__file__).parent
SRC=ROOT/'docs/audit_evidence/2026-10-31-c3a'
read=lambda p:json.loads(p.read_text())
pair=lambda r:frozenset((r['a'],r['b']))
sample=read(ROOT/'scripts/k2_labeled_sample.json')
data=read(ROOT/'docs/audit_evidence/2026-10-30-c3/stand-data.json')
quality=read(HERE/'quality.json')
assert quality==read(SRC/'quality.json')
assert read(HERE/'registry.json')==read(SRC/'registry.json')
virtual={}
real_read=Path.read_text
def capture_read(path,*args,**kwargs):
    return virtual[path] if path in virtual else real_read(path,*args,**kwargs)
def capture_write(path,text,*args,**kwargs):
    virtual[path]=text
    return len(text)
def run(name):
    spec=importlib.util.spec_from_file_location(name,SRC/(name+'.py'))
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.main()
with patch.object(Path,'write_text',capture_write),patch.object(Path,'read_text',capture_read),contextlib.redirect_stdout(io.StringIO()):
    run('apply_label_review')
    prior=json.loads(virtual[ROOT/'scripts/k2_labeled_sample.json'])
    run('apply_open_added_review')
assert len(virtual)==2 and all(json.loads(text)==json.loads(real_read(path)) for path,text in virtual.items())
before={pair(r):r['label'] for name in ('pairs','known_pairs') for r in prior[name]}
changes=[r for name in ('pairs','known_pairs') for r in sample[name] if before[pair(r)]!=r['label']]
assert len(changes)==7 and collections.Counter(r['label'] for r in changes)=={'same':3,'different':4}
assert all(before[pair(r)]=='uncertain' for r in changes)
assert [(r['a'],r['b'],r['strata']) for r in prior['pairs']]==[(r['a'],r['b'],r['strata']) for r in sample['pairs']]
assert [pair(r) for r in prior['known_pairs']]==[pair(r) for r in sample['known_pairs']]

groups={'retained':data['retained'],'added':data['added'],
 'dropped_mixed':[r for r in data['dropped'] if not((r['new_a'] or '').startswith('k2:') and (r['new_b'] or '').startswith('k2:'))],
 'dropped_both_k2':[r for r in data['dropped'] if (r['new_a'] or '').startswith('k2:') and (r['new_b'] or '').startswith('k2:')]}
layers={pair(r):name for name,group in groups.items() for r in group}
def wilson(n,total):
    z=2.734;p=n/total;den=1+z*z/total
    center=(p+z*z/(2*total))/den
    half=z*math.sqrt(p*(1-p)/total+z*z/(4*total*total))/den
    return round(center-half,4),round(center+half,4)
def calculate(s):
    out={}
    packed=lambda r:'different' if r['label']=='packaging' else r['label']
    for name,group in groups.items():
        ks=collections.Counter(packed(r) for r in s['known_pairs'] if layers[pair(r)]==name)
        ss=collections.Counter(packed(r) for r in s['pairs'] if name in r['strata'])
        a,b,u=ss['same'],ss['different'],ss['uncertain'];ka,kb,ku=ks['same'],ks['different'],ks['uncertain']
        n=sum(ss.values());pool=len(group)-sum(ks.values())
        out[name]={'true':ka+pool*a/n,'false':kb+pool*b/n,'open':ku+pool*u/n,
          'true_low':ka+pool*wilson(a,n)[0],'true_high':ka+ku+pool*wilson(n-b,n)[1],
          'false_low':kb+pool*wilson(b,n)[0],'false_high':kb+ku+pool*wilson(n-a,n)[1]}
    base=('retained','dropped_mixed','dropped_both_k2');cand=('retained','added')
    total=lambda names,k:sum(out[n][k] for n in names)
    return {'true':round(total(cand,'true')),'false':round(total(cand,'false')),'open':round(total(cand,'open')),
      'false_high':round(total(cand,'false_high')),'false_ratio':round(total(cand,'false')/total(base,'false'),3),
      'false_ratio_high':round(total(cand,'false_high')/total(base,'false_low'),3),
      'true_ratio_low':round(total(cand,'true_low')/total(base,'true_high'),3)}
expected=read(SRC/'open_added_sensitivity.json')
is_cpu=lambda r:bool(re.search(r'Ryzen|Core i\d|Сore i\d',r['a']+r['b']))
for scenario in ('as_is','cpu_unspecified_same','cpu_unspecified_different','all_open_same','all_open_different'):
    s=copy.deepcopy(sample)
    for r in s['pairs']+s['known_pairs']:
        if layers.get(pair(r))=='added' and r['label']=='uncertain':
            if scenario=='all_open_same' or scenario=='cpu_unspecified_same' and is_cpu(r):r['label']='same'
            if scenario=='all_open_different' or scenario=='cpu_unspecified_different' and is_cpu(r):r['label']='different'
    actual=calculate(s)
    assert actual==expected[scenario],(scenario,actual,expected[scenario])

from scripts import c2_quality_report as c
names=('retained','dropped_mixed','dropped_both_k2','added')
rows=[{'label':'same','strata':[n]} for n in names]+[{'label':'packaging','strata':['added']}]
probe=c.weighted_estimate(rows,dict.fromkeys(names,101),None,{'added':['packaging']})['layers']['added']
assert (probe['known_open'],probe['drawn'],probe['drawn_open'],probe['true'],probe['open'])==(1,2,1,50.0,51.0)
result={'replay_two_review_stages':True,'changed':len(changes),'membership_and_order_unchanged':True,
 'all_five_sensitivity_scenarios_independently_reproduced':True,'packaging_p3_fixed':True,
 'as_is':calculate(sample),'remaining_open_added':22,'all_open_same':expected['all_open_same'],
 'label_review_limit':'Numeric reproduction does not validate the Jabra different label; see separate source review.'}
(HERE/'independent-summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps(result,ensure_ascii=False,indent=1))
