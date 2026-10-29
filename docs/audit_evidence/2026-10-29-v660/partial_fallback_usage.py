"""Partial normalization paid retry must retain fallback accounting; all providers mocked."""
import sys,json,asyncio
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path.cwd()))
from tests.ai import test_local_ai as fixtures
import ai_service,database
case=fixtures._DbCase();case.setUp()
a,b='Synthetic catalogue item A','Synthetic catalogue item B'
cfg={'enabled':True,'has_ai':True,'ai_search_enabled':True,'has_local':True,'ai_provider':'auto','local_api_base':'http://local.invalid/v1','local_model':'fake','local_normalize_enabled':True,'gemini_api_key':'dummy','openai_api_key':'','gemini_model_mode':'manual','gemini_model':'fake-paid'}
async def local(*args,**kw):return {'items':[{'title':a,'canonical_key':'brand:modela:null'},{'title':b,'canonical_key':'0:0:0'}]}
async def paid(*args,**kw):return {'items':[{'title':b,'canonical_key':'brand:modelb:null'}]}
try:
 with patch.object(ai_service,'_get_api_credentials',return_value=cfg),patch.object(ai_service,'call_local_api',local),patch.object(ai_service,'call_gemini_api',paid),patch('model_matching.extract_canonical_key',return_value=None):
  result=asyncio.run(ai_service.normalize_product_titles_batch([a,b],for_scan=True))
 with database.get_connection() as c:rows=[dict(r) for r in c.execute("SELECT provider,outcome,requests,fallbacks FROM ai_usage WHERE task='normalize' ORDER BY provider")]
 print(json.dumps({'normalized':result,'usage':rows,'total_fallbacks':sum(r['fallbacks'] for r in rows)},ensure_ascii=False,indent=2))
 assert len(result)==2 and sum(r['fallbacks'] for r in rows)==1,'Successful paid retry after partial local answer is not recorded as fallback'
finally:case.tearDown()
