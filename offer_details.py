"""A single seller/channel/city offer. Never borrow another offer's media or description."""
from __future__ import annotations
import asyncio,hashlib,json,time
from collections import OrderedDict
from urllib.parse import urlsplit

_cache=OrderedDict()
_inflight={}


def read_offer(conn,ident):
    columns={r[1] for r in conn.execute('PRAGMA table_info(products)')}
    legacy={'id','title'}<=columns
    join="LEFT JOIN products legacy ON legacy.id=substr(o.id,5) AND substr(o.id,1,4)='off_'" if legacy else ''
    title="coalesce(nullif(legacy.title,''),p.title)" if legacy else 'p.title'
    cursor=conn.execute(f'''SELECT o.*,s.name AS seller_name,c.name AS channel_name,{title} AS source_title
      FROM offers o JOIN canonical_products p ON p.id=o.product_id
      JOIN sellers s ON s.id=o.seller_id JOIN channels c ON c.id=o.channel_id {join}
      WHERE o.id=? AND o.is_active=1 AND s.is_active=1 AND c.is_active=1''',(ident,))
    row=cursor.fetchone()
    return dict(zip([d[0] for d in cursor.description],row)) if row else None


def signature(offer):
    return hashlib.sha256(json.dumps([offer[k] for k in ('id','product_id','seller_id','channel_id','url','external_sku','source_title','image_url')],ensure_ascii=False).encode()).hexdigest()


def context(conn,ident):
    from domain.search_core import SearchCore
    from repositories.search_repo import SearchRepository
    if not conn.in_transaction:conn.execute('BEGIN')
    source=read_offer(conn,ident)
    if not source:return None
    data=SearchCore(SearchRepository(conn)).get_canonical_product(source['product_id'])
    if not data:return None
    offer=next((o for o in data['offers'] if o['id']==ident),None)
    if not offer:return None
    product=dict(data['product']);product.update(title=source['source_title'],image_url=source['image_url'] or '',description='',attributes={})
    data.update(product=product,offers=[offer],selected_offer=offer,options=[],sellers_count=1,offers_count=1,
                source_signature=signature(source),min_price=offer['price'] if offer['is_purchasable'] else None,
                max_price=offer['price'] if offer['is_purchasable'] else None,
                best_offer=offer if offer['is_purchasable'] else None,has_purchasable_offers=offer['is_purchasable'],
                best_new_price=offer['price'] if offer['is_purchasable'] and offer['condition']=='NEW' else None,
                best_used_price=offer['price'] if offer['is_purchasable'] and offer['condition'] in ('USED','REFURBISHED','OPEN_BOX') else None,
                has_gift=offer.get('gift') is not None)
    # Rebuild seller summary: same seller can have other cities, conditions and channels.
    data['sellers']=[dict(seller_id=offer['seller_id'],seller_name=offer['seller_name'],offers=[offer],
                          min_price=data['min_price'],max_price=data['max_price'],offers_count=1,channels_count=1,
                          best_offer=data['best_offer'])]
    series=[s for s in (data.get('history') or {}).get('series',[]) if s.get('offer_id')==ident]
    data['history']=dict(series=series,total_series=len(series),total_observations=sum(len(s.get('points',[])) for s in series))
    data['condition_breakdown']=None
    return data


def forte(source):
    from scrapers.fortemarket import API_SEARCH_URL,build_description_from_params
    from scrapers import http
    from product_evidence import safe_url
    sku=str(source['external_sku']).split('@',1)[0]
    if not sku.startswith('forte_'):return None
    wanted=sku[len('forte_'):]
    response=http.post(API_SEARCH_URL,json={'query':source['source_title'][:300],'hitsPerPage':10},timeout=10,headers={'Origin':'https://market.forte.kz','Referer':'https://market.forte.kz/'})
    if response.status_code not in (200,201):return None
    if len(response.content)>4*1024*1024:return None
    hit=next((h for h in (response.json() or {}).get('hits',[]) if str(h.get('objectID') or h.get('ID') or '')==wanted),None)
    if not hit:return None
    fields=hit.get('ParamMap') or {};facts=[]
    if isinstance(fields,dict):
        for key,value in fields.items():
            name=str(key).replace('_',' ').strip()[:80]
            if isinstance(value,(str,int,float)) and name and len(str(value))<=300 and not any(w in name.casefold() for w in ('описан','description','merchant','мерчант','внимание','примечание')):
                facts.append(dict(name=name,value=str(value),source_url=source['url']))
    images=[]
    def add(value):
        if isinstance(value,list):
            for x in value:add(x)
        elif isinstance(value,dict):add(value.get('url') or value.get('URL') or value.get('src'))
        elif isinstance(value,str):
            url=safe_url(value,source['url'])
            if url and url not in images and len(images)<12:images.append(url)
    for key in ('Picture','Pictures','Images','Gallery'):add(hit.get(key))
    from html_soup import make_soup
    prose=hit.get('Description') or hit.get('description')
    text=make_soup(prose).get_text(' ',strip=True) if isinstance(prose,str) and prose.strip() else build_description_from_params(hit) or ''
    from product_evidence import sanitize_facts
    clean_facts=sanitize_facts(facts)
    return dict(facts=clean_facts,images=images,description=text[:20000],status='ready' if clean_facts or images or text else 'empty')


def fetch_source(source):
    from product_details import fetch_html,store_host_allowed,STORE_DOMAINS,UnsafeUrl,_resolve_public
    from product_evidence import extract,safe_url
    if not safe_url(source['url']):return None
    host=urlsplit(source['url']).hostname or ''
    if not store_host_allowed(host):return None
    if host=='market.forte.kz' or host.endswith('.market.forte.kz'):
        return forte(source)
    domain=max((d for d in STORE_DOMAINS if host==d or host.endswith('.'+d)),key=len)
    def own_store_dns(redirect_host,port):
        if redirect_host!=domain and not redirect_host.endswith('.'+domain):
            raise UnsafeUrl('переход к другому магазину')
        return _resolve_public(redirect_host,port)
    html=fetch_html(source['url'],resolve=own_store_dns)
    return extract(html,source['url'],source['source_title'],source['seller_name']) if html else None


async def evidence(get_conn,ident):
    from product_classification import db
    from product_evidence import summary,safe_url,image_key,sanitize_facts
    from product_details import _fetch_rate,_fetch_semaphore,FETCH_WAIT_SECONDS
    source=await db(get_conn,lambda c:read_offer(c,ident))
    if not source:return None
    sig=signature(source);key=(ident,sig)
    cached=_cache.get(key)
    if cached and cached[0]>time.monotonic():return cached[1]
    if key in _inflight:return await asyncio.shield(_inflight[key])
    future=asyncio.get_running_loop().create_future();_inflight[key]=future
    result=dict(status='unavailable',facts=[],images=[],description='',summary='',sources=[],offer_id=ident,source_signature=sig)
    own_image=safe_url(source['image_url'])
    if own_image:result['images']=[own_image]
    own_url=safe_url(source['url'])
    if own_url:result['sources']=[dict(url=own_url,name=source['seller_name'])]
    try:
        parsed=None
        if not _fetch_rate.retry_after('details'):
            semaphore=_fetch_semaphore()
            try:
                await asyncio.wait_for(semaphore.acquire(),FETCH_WAIT_SECONDS)
                try:parsed=await asyncio.to_thread(fetch_source,source)
                finally:semaphore.release()
            except Exception:pass
        if parsed and parsed['status']=='ready':
            result.update(status='ready',facts=sanitize_facts(parsed['facts']),description=parsed['description'])
            result['images']=list({image_key(u):u for u in parsed['images']}.values()) or result['images']
            result['summary']=summary(result['facts'])
        current=await db(get_conn,lambda c:read_offer(c,ident))
        if not current or signature(current)!=sig:
            future.set_result(None);return None
        _cache[key]=(time.monotonic()+(600 if result['status']=='ready' else 30),result);_cache.move_to_end(key)
        while len(_cache)>300:_cache.popitem(last=False)
        future.set_result(result);return result
    except BaseException as error:
        future.set_result(None)
        if isinstance(error,Exception):return None
        raise
    finally:_inflight.pop(key,None)
