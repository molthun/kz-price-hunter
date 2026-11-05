"""Independent parser/circuit probes; synthetic catalog, no network/database."""
import asyncio, json, subprocess, types
from unittest.mock import AsyncMock, patch
import local_search
from bounded_cache import BoundedTTLCache

old=types.ModuleType('old_search')
exec(compile(subprocess.check_output(['git','show','35ac871:local_search.py'],text=True),'old_search.py','exec'),old.__dict__)
cats=[{'id':'electronics','name':'Электроника','parent_id':None,'fields':[]},
      {'id':'gpus','name':'Видеокарты','parent_id':'electronics','fields':[
          {'key':'vram','label':'Видеопамять'},{'key':'chipset','label':'Чипсет'},{'key':'bus','label':'Шина'}]},
      {'id':'phones','name':'Смартфоны','parent_id':'electronics','fields':[]}]
rows=[]
for raw,want in [
    ({'id':'gpus','name':'Видеокарты'},'gpus'),
    ({'id':'graphics_cards','name':'Видеокарты'},'gpus'),
    ({'category':'electronics','subcategory':'gpus'},'gpus'),
    ({'category':'electronics','subcategory':'does_not_exist'},'electronics'),
    ({'category':'no_such','subcategory':'gpus'},'gpus'),
    ({'category':'new_stuff','subcategory':'no_such'},None),
    ({'category_id':'phones','category_name':'Смартфоны','id':'gpus','name':'Видеокарты'},'phones'),
    ({'id':'invalid id','name':'<script>'},None),
    ({'unexpected':True},None),
    ([],None),
    (None,None)]:
    result=local_search.clean_plan(raw,'RX 9070 GRE',cats)
    actual=result['category_id'] if result else None
    assert actual==want,(raw,actual,want)
    rows.append({'input':raw,'expected':want,'actual':actual,'old_accepts':old.clean_plan(raw,'RX 9070 GRE',cats) is not None})
# Compatibility with the canonical form, including explicit constraints and review proposals.
for raw in [
    {'category_id':'gpus','category_name':'Видеокарты','search_query':'RX 9070 GRE','attributes':{'vram':12},'confidence':.9},
    {'category_id':'new','category_name':'Геодезические приборы','parent_id':'electronics','fields':[{'key':'accuracy','label':'Точность'}]},
    {'category_id':'phones','category_name':'Смартфоны','search_query':'iphone','confidence':.6}]:
    assert local_search.clean_plan(raw,'iphone 15 pro 256',cats)==old.clean_plan(raw,'iphone 15 pro 256',cats)
async def circuit():
    cfg={'ai_search_enabled':True,'local_api_base':'http://audit.invalid/v1','local_model':'audit'}
    results=[]
    for first_raw,want_second,calls in [({'unexpected':True},'ok',2),(None,'unavailable',1)]:
        with patch.object(local_search,'enabled',return_value=True),patch.object(local_search,'_plans',BoundedTTLCache(10,60)),patch.object(local_search,'_failures',BoundedTTLCache(8,15)),patch.object(local_search,'_pending',{}),patch('config.get_ai_config',return_value=cfg),patch('local_ai.pause_reason',return_value=None):
            mock=AsyncMock(side_effect=[first_raw,{'id':'gpus','name':'Видеокарты'}])
            with patch('ai_service.call_local_api',new=mock):
                first=await local_search.understand('первый',cats)
                second=await local_search.understand('RX 9070 GRE',cats)
            assert first['status']=='unavailable'
            assert second['status']==want_second,(second,want_second)
            assert mock.await_count==calls
            results.append({'first_raw':first_raw,'first_status':first['status'],'second_status':second['status'],'provider_calls':calls})
    return results
print(json.dumps({'parser_cases':rows,'canonical_compatibility_cases':3,'circuit_cases':asyncio.run(circuit()),'result':'PASS'},ensure_ascii=False,indent=2))
