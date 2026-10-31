"""Targeted C2 source corrections. Does not append/resample pairs or access a DB."""
import json,subprocess,collections
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).parent
SRC=HERE.parent/'2026-10-31-c2-loss-ext'
read=lambda p:json.loads(p.read_text())
labels_path=SRC/'labels.json';sample_path=ROOT/'scripts/k2_labeled_sample.json'
labels=read(labels_path);sample=read(sample_path);chosen=read(SRC/'strata_sealed.json')
updates={
 '22':{'label':'same','note':'Карточка AplTech прямо связывает название Philips 27E2N1110 с партномером 27E2N1110/01 и характеристиками 27 дюймов, IPS, FHD, 120 Гц. Это подтверждение конкретной карточки продавца; общее правило о региональных суффиксах не утверждается.','sources':['https://www.apltech.kz/tovar/438203/philips-27e2n1110-27e2n111001']},
 '113':{'label':'uncertain','note':'Карточка HSW подтверждает Heatsink White. Карточка базового NMD5600D5U46-16GB указывает Bulk Pack, но прямо не подтверждает отсутствие радиатора. Недостаточно данных для уверенного different: не приравниваем различие артикула или упаковки к доказанному различию охлаждения.','sources':['https://iplaza.kz/operativnaya-pamyat-16gb-ddr5-5600mhz-nomad-udimm-cl46-nmd5600d5u46-16gbhsw-heatsink-white','https://www.apltech.kz/tovar/399655/nomad-nmd5600d5u46-nmd5600d5u46-16gb']},
 '171':{'label':'different','note':'Официальный даташит Jabra RevC, таблица Productos: 26699-999-889 — USB-C MS Stereo с Wireless Charging, 26699-999-899 — USB-C MS Stereo без этой комплектации. WLC отличается комплектом зарядки.','sources':['https://www.jabra.com/_/media/Jabra_VXi_Product-Documentation/Jabra-Evolve2-65-Flex/Technical-specifications/RevC/ES-Evolve2-65-Flex-Tech-Specs-301024.pdf']}}
key=lambda r:frozenset((r['a'],r['b']))
positions={key(r):i for i,r in enumerate(sample['pairs'])}
for index,item in updates.items():
 labels[index]=item
 pair=chosen[int(index)];row=sample['pairs'][positions[key(pair)]]
 assert row['strata']==pair['strata'] and row.get('phase')=='ext'
 row.update(item)
original=json.loads(subprocess.check_output(['git','show','614255e:scripts/k2_labeled_sample.json'],cwd=ROOT))
changed=[i for i,(a,b) in enumerate(zip(original['pairs'],sample['pairs'])) if a!=b]
assert changed==[1270+22,1270+113,1270+171] and len(sample['pairs'])==len(original['pairs'])==1550
assert sample['known_pairs']==original['known_pairs']
# Keep dataset version 8: correction of the existing draw, no new population/sample.
assert sample['version']==8
labels_path.write_text(json.dumps(labels,ensure_ascii=False,indent=2)+'\n')
sample_path.write_text(json.dumps(sample,ensure_ascii=False,indent=2)+'\n')
summary={'changed_draw_indices':[22,113,171],'only_label_change':'113: different -> uncertain','changed_sample_indices':changed,'counts':dict(collections.Counter(r['label'] for r in labels.values())),'sample_version':sample['version'],'sample_size':len(sample['pairs'])}
(HERE/'changes.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n');print(json.dumps(summary,ensure_ascii=False,indent=1))
