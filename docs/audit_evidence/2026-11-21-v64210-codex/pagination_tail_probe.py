from tests import test_support
import json
from unittest.mock import Mock, patch
from scrapers.marwin import MarwinScraper
from tests.scrapers.test_marwin_v1_api import _raw_item
from tests.scrapers.test_city_confirmation_r1a import _retired_after
url='https://www.marwin.kz/knigi-32627'
s=MarwinScraper();s._slug_to_id['knigi-32627']=33
responses=[Mock(status_code=200,json=Mock(return_value={'items':[_raw_item(i) for i in range(1,21)],'pagination':{'page':1,'pages':2,'total':40,'limit':20}})),
           Mock(status_code=200,json=Mock(return_value={'items':[_raw_item(21)],'pagination':{'page':2,'pages':2,'total':40,'limit':20}}))]
s.session=Mock(get=Mock(side_effect=responses))
with patch('time.sleep'):
    result=s._scrape_sync('Synthetic',url,5)
old_ids=[f'marwin_v2_{i}@unknown' for i in range(1,41)]
retired,active,assessment=_retired_after(result,old_ids,'marwin',url)
assert len(result)==21 and result.complete and len(retired)==19
print(json.dumps({'received':len(result),'reported_total':40,'complete':result.complete,'unparsed':result.unparsed,
                 'quality':assessment['quality'],'may_retire':assessment['may_retire'],'retired_count':len(retired)},ensure_ascii=False))
