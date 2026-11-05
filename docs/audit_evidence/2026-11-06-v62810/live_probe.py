"""Separate diagnostic process; readonly catalog; no deployment or catalog writes."""
import asyncio, hashlib, json, sqlite3, time, types
from pathlib import Path
import ai_service, config, local_search, version

async def verify(candidate_source):
    candidate=types.ModuleType('audit_candidate')
    exec(compile(candidate_source,'audit_candidate.py','exec'),candidate.__dict__)
    settings=Path(config.SETTINGS_FILE)
    before=hashlib.sha256(settings.read_bytes()).hexdigest()
    c=sqlite3.connect('file:/app/data/prices.db?mode=ro',uri=True)
    c.execute('PRAGMA query_only=ON')
    cats=local_search.catalog_context(c)
    c.close()
    real_call=ai_service.call_local_api
    # Suppress only event persistence in this diagnostic process, never provider execution.
    ai_service._record_ai=lambda *a,**k:None
    out={'stand_version':version.__version__,'candidate_sha256':hashlib.sha256(candidate_source.encode()).hexdigest(),
         'model':config.get_ai_config().get('local_model'),'category_count':len(cats),'results':[]}
    for query in ['RX 9070 GRE','зимние шины 205/55 R16','iphone 15 pro 256','ноутбук rtx 4060','подгузники 4','кофемашина','тахеометр','робот пылесос']:
        calls=[]
        async def capture(*args,**kwargs):
            raw=await real_call(*args,**kwargs)
            entry={'raw_type':type(raw).__name__}
            if isinstance(raw,dict):
                entry['category_keys']={k:raw[k] for k in ['category_id','category_name','id','name','category','subcategory'] if k in raw}
            try:
                entry['old_parser_accepts_same_raw']=local_search.clean_plan(raw,query,cats) is not None
            except Exception as e:
                entry['old_parser_error']=type(e).__name__
            calls.append(entry)
            return raw
        ai_service.call_local_api=capture
        started=time.monotonic()
        result=await candidate.understand(query,cats)
        plan=result.get('plan') or {}
        out['results'].append({'query':query,'status':result['status'],'seconds':round(time.monotonic()-started,3),
            'category_id':plan.get('category_id'),'category_name':plan.get('category_name'),
            'search_query':plan.get('search_query'),'attributes':plan.get('attributes'),'calls':calls})
    ai_service.call_local_api=real_call
    out['settings_sha256_unchanged']=before==hashlib.sha256(settings.read_bytes()).hexdigest()
    print(json.dumps(out,ensure_ascii=False,indent=2))
