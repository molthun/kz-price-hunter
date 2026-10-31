"""Independent loss-extension draw/replay and arithmetic; reads repository artifacts only."""
from pathlib import Path
import subprocess,json,random,collections,math,importlib.util
ROOT=Path(__file__).resolve().parents[3]; HERE=Path(__file__).parent; SRC=HERE.parent/'2026-10-31-c2-loss-ext'
read=lambda p:json.loads(p.read_text()); key=lambda r:frozenset((r['a'],r['b']))
before=json.loads(subprocess.check_output(['git','show','e7b876b:scripts/k2_labeled_sample.json'],cwd=ROOT)); sample=read(ROOT/'scripts/k2_labeled_sample.json'); data=read(ROOT/'docs/audit_evidence/2026-10-30-c3/stand-data.json'); chosen=read(SRC/'strata_sealed.json'); labels=read(SRC/'labels.json')
assert sample['pairs'][:len(before['pairs'])]==before['pairs'] and sample['known_pairs']==before['known_pairs']
groups={'retained':data['retained'],'added':data['added'],'dropped_mixed':[],'dropped_both_k2':[]}
for r in data['dropped']:groups['dropped_both_k2' if all((r[k] or '').startswith('k2:') for k in ('new_a','new_b')) else 'dropped_mixed'].append(r)
layers={key(r):n for n,rs in groups.items() for r in rs}; seen={key(r) for r in before['pairs']+before['known_pairs']}
rng=random.Random(20261103); draw=[];pools={}
for name in ('dropped_mixed','dropped_both_k2'):
 pool=sorted([sorted(key(r)) for r in groups[name] if key(r) not in seen]);pools[name]=len(pool)
 draw += [{'a':a,'b':b,'strata':[name]} for a,b in rng.sample(pool,140)]
rng.shuffle(draw);assert draw==chosen and pools=={'dropped_mixed':416,'dropped_both_k2':224}
assert len({key(r) for r in chosen})==280 and not {key(r) for r in chosen}&seen
assert (SRC/'unlabeled_titles.txt').read_text()==''.join(f'{i}|{r["a"]} ## {r["b"]}\n' for i,r in enumerate(chosen))
spec=importlib.util.spec_from_file_location('apply_labels',SRC/'apply_labels.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
assert m.merged(before,chosen,labels)==sample
fixed=collections.defaultdict(list);drawn=collections.defaultdict(list)
for r in sample['known_pairs']:fixed[layers[key(r)]].append(r['label'])
for r in sample['pairs']:
 name=layers.get(key(r))
 if name is None or r['strata']==['old_set']:continue
 (drawn if r.get('phase')=='ext' else fixed)[name].append(r['label'])
assert {k:len(v) for k,v in fixed.items()}=={'retained':165,'added':366,'dropped_mixed':106,'dropped_both_k2':220}
assert {k:len(v) for k,v in drawn.items()}=={'retained':240,'added':500,'dropped_mixed':140,'dropped_both_k2':140}
q=read(HERE/'quality.json');assert q==read(SRC/'quality.json') and read(HERE/'registry.json')==read(SRC/'registry.json')
def bound(n,total):
 z=2.734;p=n/total;den=1+z*z/total;c=(p+z*z/(2*total))/den;h=z*math.sqrt(p*(1-p)/total+z*z/(4*total*total))/den
 return round(c-h,4),round(c+h,4)
metrics={}
for scenario,pack in [('owner_decision','different'),('packaging_different','different'),('packaging_same','same'),('packaging_excluded','packaging')]:
 out={}
 for name,rows in groups.items():
  transform=lambda values:collections.Counter(pack if x=='packaging' else x for x in values)
  k,d=transform(fixed[name]),transform(drawn[name]);n=sum(d.values());pool=len(rows)-sum(k.values());a,b=d['same'],d['different'];ko=sum(k.values())-k['same']-k['different']
  out[name]={'true':k['same']+pool*a/n,'false':k['different']+pool*b/n,'open':ko+pool*(n-a-b)/n,'true_low':k['same']+pool*bound(a,n)[0],'true_high':k['same']+ko+pool*bound(n-b,n)[1],'false_low':k['different']+pool*bound(b,n)[0],'false_high':k['different']+ko+pool*bound(n-a,n)[1]}
  assert all(round(v,1)==q['weighted'][scenario]['layers'][name][field] for field,v in out[name].items())
 base=('retained','dropped_mixed','dropped_both_k2'); cand=('retained','added'); total=lambda ns,f:sum(out[n][f] for n in ns)
 result={'baseline':{f:round(total(base,f)) for f in out['retained']},'candidate':{f:round(total(cand,f)) for f in out['retained']},'candidate_vs_baseline_true_low':round(total(cand,'true_low')/total(base,'true_high'),3),'false_merges_ratio_high':round(total(cand,'false_high')/total(base,'false_low'),3)}
 assert all(q['weighted'][scenario][k]==v for k,v in result.items());metrics[scenario]=result
summary={'PASS':True,'old_rows_unchanged':True,'sampling_seed_and_order_exact':True,'merge_replayed':True,'pools':pools,'labels':dict(collections.Counter(x['label'] for x in labels.values())),'fixed_counts':{k:len(v) for k,v in fixed.items()},'drawn_counts':{k:len(v) for k,v in drawn.items()},'independent_metrics':metrics}
(HERE/'independent-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2));print(json.dumps(summary,ensure_ascii=False,indent=1))
