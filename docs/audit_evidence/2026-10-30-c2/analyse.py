"""Recompute reported C2 scores from exported immutable keys; no database writes.
The five reviewed label corrections are audit evidence, not changes to the project's reference set.
"""
import collections
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from scripts.migrate_to_k2 import evaluate_verified_pairs

ROOT = Path(__file__).resolve().parents[3]
BASE = Path(__file__).parent
reference_path = ROOT / 'scripts/k2_verified_pairs.json'
reference = json.loads(reference_path.read_text())
assert hashlib.sha256(reference_path.read_bytes()).hexdigest() == '1926172145a9705435a93852a8d294b62f6a4d8fe0ea65e443a59b55c354140f', 'Reference set changed since this audit'
data = json.loads((BASE / 'stand-data.json').read_text())

def evaluate(dataset, side):
    keys = {t: r[side] for t, r in data['eval_inputs'].items() if r[side] is not None}
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'pairs.json'
        path.write_text(json.dumps(dataset, ensure_ascii=False))
        result = evaluate_verified_pairs(str(path), keys)
    return {**{k: v for k, v in result.items() if k != 'false_merges'},
            'false_merges_count': len(result['false_merges'])}

sources = {
    133: 'https://ro.hisense.com/produse/televizoare/hi-qled-tv/TV-SET-50E7Q-HSN/p/000000000020015893',
    136: 'https://cdrdv2-public.intel.com/839617/core-ultra-desktop-processors-series-2-product-brief.pdf',
    179: 'https://images.teamgroupinc.com/products/ssd/m2/ms30/spec-sheet/ms30-en.pdf',
    204: 'https://www.razer.com/eu-en/gaming-headsets/razer-barracuda/RZ04-03790300-R3M1',
    205: 'https://www.razer.com/eu-en/gaming-headsets/razer-barracuda/RZ04-03790300-R3M1',
}
corrected = copy.deepcopy(reference)
reviewed = []
for index, source in sources.items():
    pair = corrected['pairs'][index]
    assert pair['same'] is False
    pair['same'] = True
    reviewed.append({'index_zero_based': index, 'a': pair['a'], 'b': pair['b'],
                     'recorded_same': False, 'reviewed_same': True, 'source': source,
                     'old_a': data['eval_inputs'][pair['a']]['old'], 'old_b': data['eval_inputs'][pair['b']]['old'],
                     'new_a': data['eval_inputs'][pair['a']]['new'], 'new_b': data['eval_inputs'][pair['b']]['new']})
partition = collections.Counter()
for pair in data['dropped']:
    a, b = pair['new_a'], pair['new_b']
    if not a or not b:
        partition['empty_identity'] += 1
    elif a.startswith('k2:') and b.startswith('k2:'):
        partition['both_k2'] += 1
    else:
        partition['mixed_legacy_k2'] += 1
mixed = [p for p in data['dropped'] if p['new_a'] and p['new_b']
         and not (p['new_a'].startswith('k2:') and p['new_b'].startswith('k2:'))]
summary = {
    'reference_sha256': hashlib.sha256(reference_path.read_bytes()).hexdigest(),
    'pair_count': len(reference['pairs']),
    'unique_titles': len(data['eval_inputs']),
    'positive_pairs': sum(p['same'] for p in reference['pairs']),
    'negative_pairs': sum(not p['same'] for p in reference['pairs']),
    'positive_note_added': sum(p.get('note', '').startswith('stand added verified same') for p in reference['pairs']),
    'negative_note_dropped': sum(p.get('note', '').startswith('stand dropped verified diff') for p in reference['pairs']),
    'baseline_original_labels': evaluate(reference, 'old'),
    'candidate_original_labels': evaluate(reference, 'new'),
    'reviewed_label_errors': reviewed,
    'baseline_five_corrections': evaluate(corrected, 'old'),
    'candidate_five_corrections': evaluate(corrected, 'new'),
    'correction_scope': 'Illustration only; five audited labels corrected in memory, all other labels not fully reviewed.',
    'dropped_partition': {k: partition[k] for k in ['empty_identity','mixed_legacy_k2','both_k2']},
    'mixed_pairs_with_missing_fields': sum(p['fields_a'] is None or p['fields_b'] is None for p in mixed),
    'mixed_pairs_with_unbranded_true': sum((p['fields_a'] or {}).get('unbranded') is True or
                                         (p['fields_b'] or {}).get('unbranded') is True for p in mixed),
    'dataset_changed': False,
}
(BASE / 'analysis.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
print(json.dumps({k: v for k, v in summary.items() if k not in ['reviewed_label_errors']}, ensure_ascii=False, indent=2))
