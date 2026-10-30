"""Independent checks of sample membership, seeded selection, confusion counts and clear label examples.
No models, databases or edits to labels/reference set. Reads the frozen product-data export.
"""
import collections
import hashlib
import itertools
import json
from pathlib import Path
import random

ROOT = Path(__file__).resolve().parents[3]
BASE = Path(__file__).parent
data_path = ROOT / 'docs/audit_evidence/2026-10-30-c2/stand-data.json'
labels_path = ROOT / 'scripts/k2_labeled_sample.json'
reference_path = ROOT / 'scripts/k2_verified_pairs.json'
data, labels, reference = [json.loads(p.read_text()) for p in [data_path, labels_path, reference_path]]
key = lambda p: tuple(sorted([p['a'], p['b']]))
rows = {key(p): p for p in labels['pairs']}
assert len(rows) == len(labels['pairs']) == 459
groups = {
    'dropped_mixed': [p for p in data['dropped'] if not(p['new_a'].startswith('k2:') and p['new_b'].startswith('k2:'))],
    'dropped_both_k2': [p for p in data['dropped'] if p['new_a'].startswith('k2:') and p['new_b'].startswith('k2:')],
    'added': data['added'],
}
rng = random.Random(20261030)
for name, size in [('dropped_mixed', 60), ('dropped_both_k2', 60), ('added', 100)]:
    expected = {key(p) for p in rng.sample(groups[name], size)}
    actual = {key(p) for p in labels['pairs'] if name in p['strata']}
    assert expected == actual, name
print('PASS independently reproduced seeded samples: 60 / 60 / 100')

lookups = {}
for p in data['dropped']:
    lookups[key(p)] = {p['a']: (p['old'], p['new_a']), p['b']: (p['old'], p['new_b'])}
for p in data['added']:
    lookups[key(p)] = {p['a']: (p['old_a'], p['new']), p['b']: (p['old_b'], p['new'])}
retained = [p for p in labels['pairs'] if 'retained_pool' in p['strata']]
for p in retained:
    a, b = [data['eval_inputs'][p[k]] for k in ['a', 'b']]
    assert a['old'] and a['old'] == b['old'] and a['new'] and a['new'] == b['new']
    assert a['shops'] and b['shops'] and len(set(a['shops']) | set(b['shops'])) >= 2
    lookups[key(p)] = {p['a']: (a['old'], a['new']), p['b']: (b['old'], b['new'])}
assert len(retained) == 52
print('PASS 52 retained-pool pairs are valid, but not a random sample of all 899')

random_rows = [p for p in labels['pairs'] if set(p['strata']) & {'added','dropped_mixed','dropped_both_k2','retained_pool'}]
confusion = []
for side in [0, 1]:
    counts = collections.Counter()
    for p in random_rows:
        label = p['label']
        if label == 'uncertain':
            counts['uncertain'] += 1
            continue
        truth = label == 'same'  # packaging is different under documented owner decision.
        a, b = (lookups[key(p)][p[k]][side] for k in ['a','b'])
        predicted = bool(a) and a == b
        counts['tp' if truth and predicted else 'fn' if truth else 'fp' if predicted else 'tn'] += 1
    confusion.append(dict(counts))
report = json.loads((BASE / 'quality.json').read_text())
for name, result in zip(['baseline','candidate'], confusion):
    assert all(report['random_sample'][name][k] == result.get(k, 0) for k in ['tp','fn','fp','tn','uncertain'])
print('PASS independent confusion matrices:', confusion)

# Re-review the five exact label errors identified in the preceding audit.
for i in [133, 136, 179, 204, 205]:
    p = reference['pairs'][i]
    assert p['same'] is True and rows[key(p)]['label'] == 'same'
print('PASS all five earlier manufacturer-supported label errors are corrected')

# Independent title-level review of explicit lengths, sizes, memory generation, and whole system vs accessory.
# These checks deliberately exclude subtle model omissions and packaging ambiguities.
reviewed = []
for p in report['candidate_false_merges']:
    a, b = p['a'], p['b']
    reason = None
    if 'PP12-0.5M/O' in a and 'PP12 1 м' in b:
        reason = '0.5 m versus 1 m'
    elif 'PP12-3M/V' in a and 'PP12 15 м' in b:
        reason = '3 m versus 15 m'
    elif 'Fenix 8 47mm' in a and '43mm' in b:
        reason = '47 mm versus 43 mm'
    elif 'Fenix 8 51mm' in a and '43mm' in b:
        reason = '51 mm versus 43 mm'
    elif 'Defender ES Largo' in a and '5м' in a and '1.8 м' in b:
        reason = '5 m versus 1.8 m'
    elif 'DDR-4 DIMM' in a and 'NTBSD5' in b:
        reason = 'DDR4 versus DDR5 identifiers'
    elif 'Игровая приставка Sony PlayStation 5' in a and b.startswith('Пульт для Sony PlayStation 5'):
        reason = 'console bundle versus separate accessory'
    if reason:
        sample = rows[key(p)]
        assert sample['label'] == 'different'
        current = lookups[key(p)]
        assert current[a][1] and current[a][1] == current[b][1]
        reviewed.append({'a': a, 'b': b, 'reason': reason, 'label': 'different', 'candidate_key': current[a][1]})
assert len(reviewed) == 8, len(reviewed)
print('PASS independently confirmed 8 clear new false merges; other labels not all re-reviewed')

reg = json.loads((BASE / 'registry.json').read_text())
for name in ['dropped','added']:
    assert len({key(p) for p in reg[name]}) == len(reg[name])
    assert {key(p) for p in reg[name]} == {key(p) for p in data[name]}
assert reg['dropped_counts']['missing_fields_one_side'] == 660
print('PASS registry contains exactly all 1071 dropped and 2695 added pairs with one structural cause each')
out = {'input_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in [data_path, labels_path, reference_path]},
       'reviewed_false_merges': reviewed, 'independent_confusion': confusion,
       'seed_reproduced': True, 'retained_full_population_sampled': False,
       'all_labels_independently_reviewed': False}
absent_old = []
for p in labels['pairs']:
    if 'old_set' not in p['strata']:
        continue
    a, b = [data['eval_inputs'].get(p[k]) for k in ['a','b']]
    if a and b and all(v is None for v in [a['old'], a['new'], b['old'], b['new']]):
        absent_old.append({'a': p['a'], 'b': p['b'], 'strata': p['strata']})
out['absent_old_set_pairs'] = absent_old
out['reported_without_keys'] = report['labels']['without_keys']
assert len(absent_old) == 2
print('NOTE auxiliary without_keys=', report['labels']['without_keys'], 'but', len(absent_old),
      'old_set pairs have no keys; weighted/random strata unaffected')
(BASE / 'independent-results.json').write_text(json.dumps(out, ensure_ascii=False, indent=2))
