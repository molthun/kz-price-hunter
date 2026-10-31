"""Independent frozen-frame and label-generation checks for C2-ext. No submitted files are changed."""
import collections
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import subprocess

ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).parent
EXT=ROOT/'docs/audit_evidence/2026-11-01-c2-ext'
read=lambda p:json.loads(p.read_text())
key=lambda r:frozenset((r['a'],r['b']))
oldbytes=subprocess.check_output(['git','show','36f72d8:scripts/k2_labeled_sample.json'],cwd=ROOT)
old=json.loads(oldbytes)
data=read(ROOT/'docs/audit_evidence/2026-10-30-c3/stand-data.json')
sample=read(ROOT/'scripts/k2_labeled_sample.json')
meta=read(EXT/'sampling_metadata.json')
existing={key(r) for name in ('pairs','known_pairs') for r in old[name]}
assert len(existing)==888 and hashlib.sha256(oldbytes).hexdigest()==meta['labels_sha256']
rng=random.Random(20261101);selected=[];pools={}
for name,n in [('added',500),('retained',240)]:
    pool=sorted([{'a':r['a'],'b':r['b'],'strata':[name]} for r in data[name] if key(r) not in existing],key=lambda r:(r['a'],r['b']))
    pools[name]=len(pool);selected+=rng.sample(pool,n)
rng.shuffle(selected)
assert selected==read(EXT/'raw_drawn_pairs.json')
assert not existing.intersection(key(r) for r in selected)
assert sample['pairs'][:530]==old['pairs'] and sample['known_pairs']==old['known_pairs']
for filename,field in [('raw_drawn_pairs.json','raw_drawn_sha256'),('unlabeled_sample.json','unlabeled_sha256')]:
    assert hashlib.sha256((EXT/filename).read_bytes()).hexdigest()==meta[field]
assert hashlib.sha256((ROOT/meta['stand_file']).read_bytes()).hexdigest()==meta['stand_sha256']
spec=importlib.util.spec_from_file_location('ext_labeler',EXT/'build_ext_labeled_sample.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
raw=read(EXT/'unlabeled_sample.json');labeled=read(EXT/'ext_labeled_pairs.json')
for r,p in zip(raw,labeled):
    label,note,source=module.label_pair(r)
    assert {'idx':r['idx'],'a':r['a'],'b':r['b'],'strata':r['strata'],'label':label,'note':note}==p
assert len(raw)==len(labeled)==740
errors=[labeled[i] for i in (108,210,277,358,595,624)]
assert all(p['label']=='same' for p in errors)
assert all(p['strata']==['added'] for p in errors)
old_main={key(r) for r in old['pairs'] if 'added' in r['strata']}
known={key(r) for r in old['known_pairs']}
added_keys={key(r) for r in data['added']}
omitted=[r for r in old['pairs'] if key(r) in added_keys and key(r) not in old_main|known]
assert len(omitted)==40
q=read(HERE/'quality.json')
assert q==read(EXT/'quality.json') and read(HERE/'registry.json')==read(EXT/'registry.json')
# Point estimate calculated independently from census and observed label counts.
by_layer={key(r):name for name in ('retained','added','dropped') for r in data[name]}
for r in data['dropped']:
    by_layer[key(r)]='dropped_both_k2' if (r['new_a'] or '').startswith('k2:') and (r['new_b'] or '').startswith('k2:') else 'dropped_mixed'
counts={}
for name,size in q['strata_sizes'].items():
    pack=lambda r:'different' if r['label']=='packaging' else r['label']
    c=collections.Counter(pack(r) for r in sample['known_pairs'] if by_layer[key(r)]==name)
    s=collections.Counter(pack(r) for r in sample['pairs'] if name in r['strata'])
    counts[name]={label:c[label]+(size-sum(c.values()))*s[label]/sum(s.values()) for label in ('same','different','uncertain')}
    actual=q['weighted']['owner_decision']['layers'][name]
    assert round(counts[name]['same'],1)==actual['true'] and round(counts[name]['different'],1)==actual['false']
out={'seed_and_exact_order_reproduced':True,'pools':pools,'no_overlap':True,'old_rows_unchanged':True,
     'all_740_labels_exactly_generated_by_rules':True,'explicit_same_errors':errors,
     'excluded_old_added_not_counted_as_census':omitted,
     'excluded_40_labels':dict(collections.Counter(r['label'] for r in omitted)),
     'report_and_registry_equal':True,'point_estimates_independently_reproduced':True}
(HERE/'ext-summary.json').write_text(json.dumps(out,ensure_ascii=False,indent=2))
print('PASS exact seeded selection, hashes, zero overlap, old rows, formula reproduction')
print('FINDINGS: six explicit SAME label errors; 40 excluded older added pairs absent from main/census estimate')
print('EXCLUDED',dict(collections.Counter(r['label'] for r in omitted)))
