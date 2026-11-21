from tests import test_support
import json
from unittest.mock import Mock,patch
from scrapers.marwin import MarwinScraper
from tests.scrapers.test_marwin_v1_api import _raw_item
from tests.scrapers.test_city_confirmation_r1a import _retired_after
URL='https://www.marwin.kz/knigi-32627'

def run(label, ids1, ids2, total1=40, total2=40, should_complete=False):
    s=MarwinScraper();s._slug_to_id['knigi-32627']=33
    replies=[]
    for page,ids,total in ((1,ids1,total1),(2,ids2,total2),(3,[],total2)):
        replies.append(Mock(status_code=200,json=Mock(return_value={'items':[_raw_item(i) for i in ids],
            'pagination':{'page':page,'pages':2,'total':total,'limit':20}})))
    s.session=Mock(get=Mock(side_effect=replies))
    with patch('time.sleep'):
        result=s._scrape_sync('Synthetic',URL,3)
    retired,_,assessment=_retired_after(result,[f'marwin_v2_{i}@unknown' for i in range(1,42)],'marwin',URL)
    return {'case':label,'expected_complete':should_complete,'actual_complete':result.complete,
        'received':len(result),'may_retire':assessment['may_retire'],'retired':retired,
        'pass':result.complete==should_complete and (should_complete or not retired)}

out=[run('valid_20_20',range(1,21),range(21,41),should_complete=True),
     run('valid_20_1_total21',range(1,21),[21],21,21,True),
     run('truncated_tail_total40',range(1,21),[21]),
     run('truncated_middle',range(1,16),range(21,41)),
     run('total_changes_40_to21',range(1,21),[21],40,21),
     run('overlap_between_pages',range(1,21),range(20,40))]
print('MATRIX='+json.dumps(out,ensure_ascii=False))
assert all(x['pass'] for x in out), 'acceptance matrix failed'
