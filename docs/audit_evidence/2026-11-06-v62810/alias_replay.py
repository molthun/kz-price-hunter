"""Deterministic safety replay: real observed primary shape; synthetic secondary refinement.
The secondary payload reconstructs the observed final category, not an exact raw capture.
No network, no live DB or app writes.
"""
import asyncio,json,subprocess,types
from unittest.mock import AsyncMock,patch
from bounded_cache import BoundedTTLCache
import local_search
old=types.ModuleType('old_search')
exec(compile(subprocess.check_output(['git','show','35ac871:local_search.py'],text=True),'old_search.py','exec'),old.__dict__)
cats=[{'id':'gpus','name':'Видеокарты','parent_id':'electronics','fields':[]},
      {'id':'electronics','name':'Электроника','parent_id':None,'fields':[]},
      {'id':'ai_706f8863ee306bd0','name':'Геодезическое оборудование','parent_id':'electronics','fields':[]}]
primary={'id':'rx_9070_gre','name':'RX 9070 GRE'}
secondary={'new_category_name':'Геодезическое оборудование','parent_id':'electronics','fields':[]}
async def run(module):
    with patch.object(module,'enabled',return_value=True),patch.object(module,'_plans',BoundedTTLCache(10,60)),patch.object(module,'_failures',BoundedTTLCache(8,15)),patch.object(module,'_pending',{}),patch('config.get_ai_config',return_value={'local_api_base':'http://audit.invalid/v1','local_model':'m','ai_search_enabled':True}),patch('local_ai.pause_reason',return_value=None),patch('ai_service.call_local_api',new=AsyncMock(side_effect=[primary,secondary])):
        return await module.understand('RX 9070 GRE',cats)
current=asyncio.run(run(local_search))
previous=asyncio.run(run(old))
assert previous['status']=='unavailable'
assert current['status']=='ok'
assert current['plan']['category_id']=='ai_706f8863ee306bd0'
assert current['plan']['confidence']>=.85
print(json.dumps({'primary':primary,'secondary_synthetic':secondary,'old':previous,'candidate':current,'alias_only_primary_plan':local_search.clean_plan(primary,'RX 9070 GRE',cats)},ensure_ascii=False,indent=2))
