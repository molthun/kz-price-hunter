"""Independent C3 sample/frame/confusion audit. Does not modify submitted data or labels."""
import collections
import copy
import hashlib
import json
from pathlib import Path
import random

ROOT = Path(__file__).resolve().parents[3]
BASE = Path(__file__).parent
SUBMITTED = ROOT / 'docs/audit_evidence/2026-10-30-c3'
read = lambda path: json.loads(path.read_text())
data, live = read(SUBMITTED/'stand-data.json'), read(BASE/'stand-live.json')
labels, raw = read(SUBMITTED/'labeled_sample.json'), read(SUBMITTED/'unlabeled_sample.json')
previous = read(BASE/'previous-labels.json')  # git show d127f4e:scripts/k2_labeled_sample.json
reference = read(ROOT/'scripts/k2_verified_pairs.json')
quality, registry = read(BASE/'quality.json'), read(BASE/'registry.json')
pair = lambda row: tuple(sorted((row['a'], row['b'])))
prior = {pair(row): row for row in previous['pairs']}
rows = {pair(row): row for row in labels['pairs']}
assert len(rows) == len(labels['pairs']) == 530
assert len({pair(row) for row in raw}) == 320
assert not set(prior).intersection(pair(row) for row in raw)
assert labels == read(ROOT/'scripts/k2_labeled_sample.json')
assert quality == read(SUBMITTED/'quality.json')
assert registry == read(SUBMITTED/'registry.json')
assert all(data[name] == live[name] for name in data if name not in ('eval_inputs','focus_v2'))
assert all(row == live['eval_inputs'][title] for title, row in data['eval_inputs'].items())
freeze = lambda row: tuple(sorted(row.items()))
assert set(map(freeze,data['focus_v2'])) == {freeze(row) for row in live['focus_v2'] if row['title'] in data['eval_inputs']}

groups = {
    'retained': data['retained'],
    'dropped_mixed': [r for r in data['dropped'] if not(r['new_a'].startswith('k2:') and r['new_b'].startswith('k2:'))],
    'dropped_both_k2': [r for r in data['dropped'] if r['new_a'].startswith('k2:') and r['new_b'].startswith('k2:')],
    'added': data['added'],
}
frame = {}
rng = random.Random(20261031)
selected = []
for name, amount in [('retained',60), ('dropped_mixed',60), ('dropped_both_k2',60), ('added',100)]:
    group = groups[name]
    fresh = [row for row in group if pair(row) not in prior]
    excluded = [prior[pair(row)] for row in group if pair(row) in prior]
    chosen = rng.sample(fresh, amount)
    assert {pair(row) for row in chosen} == {pair(row) for row in raw if name in row['strata']}
    selected += [{'a':r['a'],'b':r['b'],'strata':[name]} for r in chosen]
    frame[name] = {'population':len(group), 'sampling_frame':len(fresh), 'excluded_known':len(excluded),
                   'excluded_previous_labels':dict(collections.Counter(r['label'] for r in excluded))}
already = {pair(row) for row in selected}
merge_pool = [r for r in registry['added'] if r['cause'] in ('linked_by_article','own_keys_equal_old_identity_differs')
              and pair(r) not in prior and pair(r) not in already]
assert len(merge_pool) == 414
selected += [{'a':r['a'],'b':r['b'],'strata':['new_merges']} for r in rng.sample(merge_pool,40)]
rng.shuffle(selected)
assert selected == raw

keys = {}
for row in data['dropped']:
    keys[pair(row)] = {row['a']:(row['old'],row['new_a']), row['b']:(row['old'],row['new_b'])}
for row in data['added']:
    keys[pair(row)] = {row['a']:(row['old_a'],row['new']), row['b']:(row['old_b'],row['new'])}
for row in data['retained']:
    keys[pair(row)] = {row['a']:(row['old'],row['new']), row['b']:(row['old'],row['new'])}

counts = []
for side in (0,1):
    count = collections.Counter()
    for row in raw:
        if not set(row['strata']).intersection(groups):
            continue
        truth = rows[pair(row)]['label']
        if truth == 'uncertain':
            count['uncertain'] += 1
            continue
        a,b = (keys[pair(row)][row[k]][side] for k in ('a','b'))
        together = bool(a) and a == b
        count['tp' if truth == 'same' and together else 'fn' if truth == 'same' else 'fp' if together else 'tn'] += 1
    counts.append(dict(count))
for name,count in zip(('baseline','candidate'),counts):
    assert all(quality['random_sample'][name][k] == count.get(k,0) for k in ('tp','fn','fp','tn','uncertain'))

undecided = [{**row,'new_label':rows[pair(row)]['label']} for row in reference['pairs'] if row.get('same') is None]
assert len(undecided) == 13 and all(row['new_label']=='different' for row in undecided)

# Manufacturer-backed review only. Do not overwrite submitted labels or certify the rest of the set.
corrections = []
for index, sources in [
    (8, ['https://www.hp.com/us-en/shop/pdp/hyperx-cloud-iii-s-wireless-gaming-headset-%28black-red%29',
         'https://www.hp.com/ca-en/shop/products/accessories/hyperx-cloud-iii-s-wireless-gaming-headset-white-pink-dg0r2aa']),
    (87, ['https://www.razer.com/console-headsets/razer-kaira-x-for-xbox']),
]:
    row = rows[pair(raw[index])]
    assert row['label']=='different'
    corrections.append({'sample_index':index, **row, 'review_label':'same', 'sources':sources})

summary = {
    'sha256': {name:hashlib.sha256((SUBMITTED/name).read_bytes()).hexdigest()
               for name in ('stand-data.json','labeled_sample.json','unlabeled_sample.json')},
    'live_core_export_equal':True, 'quality_equal':True, 'registry_equal':True,
    'seed_selection_and_shuffle_equal':True, 'frame':frame,
    'new_merges_pool_after_main_sample':len(merge_pool),
    'independent_confusion':dict(zip(('baseline','candidate'),counts)),
    'undecided_converted_to_different':undecided,
    'manufacturer_backed_label_errors':corrections,
    'live_checks':read(BASE/'stand-extra.json'),
    'limits':'Full semantic review of all 530 labels was not performed; no corrected labels or rules written.',
}
(BASE/'independent-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
print('PASS core export, integrity, invariants, exact seeded selection/shuffle, 280-pair confusion, quality and registry reproduction')
print('FINDINGS: 2 manufacturer-backed label errors; 13 undecided labels converted to different; excluded-frame weights applied to full populations')
print(json.dumps(frame,ensure_ascii=False,indent=1))
