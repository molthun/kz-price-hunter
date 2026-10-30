"""Independent C3A frame, label ledger, residue and numeric checks; submitted files remain unchanged."""
import collections
import contextlib
import importlib.util
import io
import json
import math
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
BASE = Path(__file__).parent
SOURCE = ROOT/'docs/audit_evidence/2026-10-31-c3a'
read = lambda p: json.loads(p.read_text())
key = lambda p: frozenset((p['a'],p['b']))
data = read(ROOT/'docs/audit_evidence/2026-10-30-c3/stand-data.json')
sample = read(ROOT/'scripts/k2_labeled_sample.json')
previous = read(ROOT/'docs/audit_evidence/2026-10-31-c3-codex/previous-labels.json')
original = read(ROOT/'docs/audit_evidence/2026-10-30-c3/labeled_sample.json')
quality, registry = read(BASE/'quality.json'), read(BASE/'registry.json')
assert quality == read(SOURCE/'quality.json')
assert registry == read(SOURCE/'registry.json')
groups = {'retained':data['retained'], 'added':data['added'],
          'dropped_mixed':[p for p in data['dropped'] if not((p['new_a'] or '').startswith('k2:') and (p['new_b'] or '').startswith('k2:'))],
          'dropped_both_k2':[p for p in data['dropped'] if (p['new_a'] or '').startswith('k2:') and (p['new_b'] or '').startswith('k2:')]}
layer = {key(row):name for name,group in groups.items() for row in group}
fresh = {key(row) for row in original['pairs'] if row['strata']!=['old_set']}
expected_known = {key(row) for row in previous['pairs'] if key(row) in layer and key(row) not in fresh}
known = {key(row):row for row in sample['known_pairs']}
assert len(known) == len(sample['known_pairs']) == len(expected_known) == 537
assert set(known) == expected_known
assert not set(known).intersection(fresh)
assert [(p['a'],p['b'],p['strata']) for p in sample['pairs']] == [(p['a'],p['b'],p['strata']) for p in original['pairs']]
labels = {key(row):row for row in sample['pairs']}
assert all(labels[k]['label']==row['label'] for k,row in known.items() if k in labels)
reference = read(ROOT/'scripts/k2_verified_pairs.json')
assert all(labels[key(row)]['label']=='uncertain' for row in reference['pairs'] if row.get('same') is None)
for row in read(ROOT/'docs/audit_evidence/2026-10-31-c3-codex/independent-summary.json')['manufacturer_backed_label_errors']:
    assert labels[key(row)]['label']=='same'

# Replay the review builder with writes captured in memory only.
spec = importlib.util.spec_from_file_location('submitted_label_review',SOURCE/'apply_label_review.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
captured = {}
def capture(path,text,*args,**kwargs):
    captured[path] = json.loads(text)
    return len(text)
with patch.object(Path,'write_text',capture),contextlib.redirect_stdout(io.StringIO()):
    module.main()
assert len(captured)==2 and all(value==read(path) for path,value in captured.items())
assert len(read(SOURCE/'label_changes.json')['changed'])==48

# Independent implementation of the stated point estimates and Wilson endpoints.
def wilson(n,total,z=2.734):
    p=n/total
    scale=1+z*z/total
    center=(p+z*z/(2*total))/scale
    half=z*math.sqrt(p*(1-p)/total+z*z/(4*total*total))/scale
    return round(center-half,4),round(center+half,4)

packed = lambda p: 'different' if p['label']=='packaging' else p['label']
calculated = {}
for name,population in groups.items():
    ks=collections.Counter(packed(p) for k,p in known.items() if layer[k]==name)
    ss=collections.Counter(packed(p) for p in sample['pairs'] if name in p['strata'])
    ns,nd,nu=ss['same'],ss['different'],ss['uncertain']
    cs,cd,cu=ks['same'],ks['different'],ks['uncertain']
    n=ns+nd+nu
    pool=len(population)-sum(ks.values())
    calculated[name]={'size':len(population),'known':sum(ks.values()),'known_same':cs,'known_different':cd,'known_open':cu,
        'pool':pool,'drawn':n,'drawn_same':ns,'drawn_different':nd,'drawn_open':nu,
        'true':cs+pool*ns/n,'false':cd+pool*nd/n,'open':cu+pool*nu/n,
        'true_low':cs+pool*wilson(ns,n)[0], 'true_high':cs+cu+pool*wilson(n-nd,n)[1],
        'false_low':cd+pool*wilson(nd,n)[0], 'false_high':cd+cu+pool*wilson(n-ns,n)[1]}
    assert math.isclose(sum(calculated[name][k] for k in ('true','false','open')),len(population))
    assert {k:round(v,1) for k,v in calculated[name].items()} == quality['weighted']['owner_decision']['layers'][name]

base=('retained','dropped_mixed','dropped_both_k2')
cand=('retained','added')
total = lambda names,field:sum(calculated[n][field] for n in names)
numeric = {'baseline':{k:round(total(base,k)) for k in ('true','false','open','true_low','true_high','false_low','false_high')},
           'candidate':{k:round(total(cand,k)) for k in ('true','false','open','true_low','true_high','false_low','false_high')},
           'candidate_vs_baseline_true':round(total(cand,'true')/total(base,'true'),3),
           'candidate_vs_baseline_true_low':round(total(cand,'true_low')/total(base,'true_high'),3),
           'false_merges_ratio':round(total(cand,'false')/total(base,'false'),3),
           'false_merges_ratio_high':round(total(cand,'false_high')/total(base,'false_low'),3),
           'baseline_true_kept':round(calculated['retained']['true']/total(base,'true'),3),
           'baseline_true_kept_low':round(calculated['retained']['true_low']/(calculated['retained']['true_low']+total(base[1:],'true_high')),3)}
assert all(value==quality['weighted']['owner_decision'][name] for name,value in numeric.items())

raw = read(BASE/'residue-live.json')
assert raw==read(SOURCE/'residue_raw.json')
assert len(raw)==682 and len({r['title'] for r in raw})==682
assert sum(r['fields'] is None for r in raw)==472
assert sum(r['fields'] is not None for r in raw)==210
assert not any((r['fields'] or {}).get('unbranded') is True for r in raw)
lost=collections.Counter()
for row in data['dropped']:
    for title,k in [(row['a'],row['new_a']),(row['b'],row['new_b'])]:
        if not(k or '').startswith('k2:'):
            lost[title]+=1
residue = read(SOURCE/'residue_titles.json')
expected = sorted([{'title':r['title'],'key':r['key'],
    'status':'no_fields_row' if r['fields'] is None else 'fields_with_brand' if r['fields'].get('brand') else 'fields_without_brand',
    'lost_pairs':lost[r['title']]} for r in raw], key=lambda r:(-r['lost_pairs'],r['title']))
assert expected==residue['rows'] and residue['in_lost_pairs']==186 and residue['not_in_lost_pairs']==496
assert registry['dropped_counts']['missing_fields_one_side']==521
assert registry['dropped_counts']['legacy_with_fields']==1 and registry['dropped_counts']['no_identity']==0

summary = {'known_pairs_exact':True,'same_selection_and_order':True,'labels_and_review_ledger_reproduced':True,
    'independent_owner_decision':numeric,'live_residue_exact':True,'residue_titles':682,'missing_fields':472,'fields_present':210,
    'in_lost_pairs':186,'not_in_lost_pairs':496,
    'limits':'Numeric reproduction assumes the submitted labels. All 530+537 label meanings were not independently certified.'}
(BASE/'independent-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
print('PASS known census membership/disjointness, unchanged sample order, 48-change replay, 13 uncertain labels, prior two confirmed errors')
print('PASS independent point estimates, all owner-decision layer bounds and ratios; live residue full 682-row equality and registry reasons')
print(json.dumps(numeric,ensure_ascii=False,indent=1))
