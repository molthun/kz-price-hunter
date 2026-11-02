"""Source-bound structured facts and remote image links; source prose stays in RAM only."""
from __future__ import annotations
import asyncio
import hashlib
import json
import os
import re
import time
from collections import OrderedDict
from urllib.parse import urljoin,urlsplit,urlunsplit,parse_qsl,urlencode

DDL='''
CREATE TABLE IF NOT EXISTS product_source_evidence(
 product_id TEXT PRIMARY KEY REFERENCES canonical_products(id) ON DELETE CASCADE,
 signature TEXT NOT NULL,facts_hash TEXT NOT NULL,facts_json TEXT NOT NULL DEFAULT '[]',images_json TEXT NOT NULL DEFAULT '[]',
 sources_json TEXT NOT NULL DEFAULT '[]',status TEXT NOT NULL,checked_at REAL NOT NULL,next_attempt REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evidence_due ON product_source_evidence(next_attempt);
'''
SOURCE_SIGNATURE="json_array(p.title,p.brand,p.model)"
_cache=OrderedDict()
_inflight={}


def enabled():return os.environ.get('PRODUCT_ENRICHMENT_ENABLED')=='1'


def safe_url(value,base=''):
    try:
        if len(str(value or ''))>2048:return ''
        u=urlsplit(urljoin(base,str(value or '').strip()))
        if u.scheme not in ('https','http') or not u.hostname or u.username or u.password:return ''
        if u.port not in (None,80,443):return ''
        return urlunsplit((u.scheme,u.netloc,u.path,u.query,''))
    except (ValueError,TypeError):return ''


def image_key(url):
    u=urlsplit(url)
    query=[(k,v) for k,v in parse_qsl(u.query) if k.lower() not in ('width','height','w','h','quality','q','resize')]
    return urlunsplit((u.scheme,u.netloc,re.sub(r'/(?:small|medium|large)/','/size/',u.path),urlencode(query),''))


def words(text):return re.findall(r'[a-zа-яё0-9]+',str(text or '').casefold())


def identity(expected,node_name,sku='',model=''):
    """Require model/SKU or substantial title overlap, not an incidental category word."""
    from domain.product_options import title_color,_gigabytes
    colors=(title_color(expected),title_color(node_name))
    if all(colors) and colors[0]!=colors[1]:return False
    sizes=(_gigabytes(expected),_gigabytes(node_name))
    if all(sizes) and sorted(sizes[0])!=sorted(sizes[1]):return False
    a=set(words(expected));b=set(words(node_name))
    for token in (sku,model):
        compact=re.sub(r'[^\w]','',str(token).casefold())
        if len(compact)>=5 and any(c.isdigit() for c in compact) and compact in re.sub(r'[^\w]','',expected.casefold()):return True
    identifiers={x for x in a if len(x)>=4 and any(c.isdigit() for c in x) and any(c.isalpha() for c in x)}
    if identifiers and not identifiers<=b:return False
    if identifiers and identifiers<=b and any(len(x)>=8 for x in identifiers):return True
    common={x for x in a&b if len(x)>=3}
    return len(common)>=2 and len(common)/max(1,len({x for x in a if len(x)>=3}))>=0.65


def extract(html,url,title,shop=''):
    from html_soup import make_soup
    from product_details import extract_description
    soup=make_soup(html);nodes=[]
    def walk(value):
        if isinstance(value,list):
            for x in value:walk(x)
        elif isinstance(value,dict):
            kinds=value.get('@type',[]);kinds=[kinds] if isinstance(kinds,str) else kinds
            if not isinstance(kinds,list):kinds=[]
            if 'Product' in kinds:nodes.append(value)
            # Only top-level products/@graph: never recommendations or ItemList neighbours.
            walk(value.get('@graph',[]))
    for s in soup.select('script[type="application/ld+json"]'):
        try:walk(json.loads(s.string or s.get_text()))
        except (ValueError,TypeError):pass
    node=next((n for n in nodes if identity(title,n.get('name',''),n.get('sku',''),n.get('mpn',''))),None)
    h1=soup.find('h1');heading=h1.get_text(' ',strip=True) if h1 else ''
    if node and heading and not (identity(title,heading) or words(heading)==words(node.get('name','')) or identity(heading,node.get('name',''))):node=None
    if node is None and not identity(title,heading):return dict(status='identity_unconfirmed',facts=[],images=[],description='',source_url=url)
    facts=[];seen=set()
    def fact(name,value):
        if isinstance(value,(dict,list,tuple)):return
        name=' '.join(str(name or '').split())[:80];value=' '.join(str(value or '').split())
        if not name or not value or len(value)>300 or any(x in name.casefold() for x in ('описание','description','внимание','примечание','срок поставки')) or name.casefold() in ('цена','price'):return
        if (name.casefold(),value.casefold()) in seen:return
        seen.add((name.casefold(),value.casefold()))
        if len(facts)<60:facts.append(dict(name=name,value=value,source_url=url))
    images=[];image_seen=set()
    def image(value):
        if isinstance(value,list):
            for x in value:image(x)
        elif isinstance(value,dict):image(value.get('contentUrl') or value.get('url'))
        elif value:
            link=safe_url(value,url)
            if link and image_key(link) not in image_seen and len(images)<12:
                image_seen.add(image_key(link));images.append(link)
    fact('Название в магазине',node.get('name','') if node else heading)
    if node:
        brand=node.get('brand');fact('Бренд',brand.get('name','') if isinstance(brand,dict) else brand)
        fact('Артикул',node.get('mpn') or node.get('sku'));fact('Модель',node.get('model'))
        props=node.get('additionalProperty') or []
        for prop in props if isinstance(props,list) else [props]:
            if isinstance(prop,dict):fact(prop.get('name'),prop.get('value'))
        image(node.get('image'))
    # Known product specification containers only; no page-wide table or arbitrary images.
    for item in soup.select('.dotted-item, .specifications-list__spec, [itemprop="additionalProperty"]'):
        n=item.select_one('.dotted-item__name, .specifications-list__spec-term-translation, .specifications-list__spec-term, [itemprop="name"]')
        v=item.select_one('.dotted-item__value, .specifications-list__spec-definition-translation, .specifications-list__spec-definition, [itemprop="value"]')
        if n and v:fact(n.get_text(' ',strip=True),v.get_text(' ',strip=True))
    for row in soup.select('.product-specifications tr, .product-characteristics tr, .product-params tr, .properties tr'):
        cells=row.select('th,td')
        if len(cells)==2:fact(cells[0].get_text(' ',strip=True),cells[1].get_text(' ',strip=True))
    for row in soup.select('.product-characteristics dl, .product-specifications dl'):
        for n in row.select('dt'):
            v=n.find_next_sibling('dd')
            if v:fact(n.get_text(' ',strip=True),v.get_text(' ',strip=True))
    for el in soup.select('.product-gallery img, .product-images img, .product-slider img, .bx_bigimages img, .detail-gallery-big__link[href]'):
        image(el.get('data-src') or el.get('src') or el.get('href') or el.get('content'))
    if not images:
        meta=soup.select_one('meta[property="og:image"]')
        if meta:image(meta.get('content'))
    text=extract_description(html,url,shop)
    if node and isinstance(node.get('description'),str):
        text=make_soup(node['description']).get_text(' ',strip=True)[:20000]
    return dict(status='ready' if facts or images or text else 'empty',facts=facts,images=images,description=text,source_url=url,source_title=str(node.get('name','') if node else heading)[:300])


def stored(conn,pid):
    row=conn.execute('SELECT signature,facts_json,images_json,sources_json,status,checked_at,next_attempt FROM product_source_evidence WHERE product_id=?',(pid,)).fetchone()
    current=conn.execute(f'SELECT {SOURCE_SIGNATURE} FROM canonical_products p WHERE id=?',(pid,)).fetchone()
    if not row or not current or row[0]!=current[0]:return None
    return dict(facts=json.loads(row[1]),images=json.loads(row[2]),sources=json.loads(row[3]),status=row[4],checked_at=row[5],next_attempt=row[6])


def summary(facts,limit=400):
    return '; '.join(f["name"]+': '+f["value"] for f in facts[:6])[:limit]


def save(conn,pid,signature,data):
    current=conn.execute(f'SELECT {SOURCE_SIGNATURE} FROM canonical_products p WHERE id=?',(pid,)).fetchone()
    if not current or current[0]!=signature:return False
    now=time.time();delay=7*86400 if data['status']=='ready' else 6*3600
    facts_json=json.dumps(data['facts'],ensure_ascii=False,sort_keys=True)
    facts_hash=hashlib.sha256(facts_json.encode()).hexdigest()
    conn.execute('''INSERT INTO product_source_evidence VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(product_id)
      DO UPDATE SET signature=excluded.signature,facts_hash=excluded.facts_hash,facts_json=excluded.facts_json,images_json=excluded.images_json,
      sources_json=excluded.sources_json,status=excluded.status,checked_at=excluded.checked_at,next_attempt=excluded.next_attempt''',
      (pid,signature,facts_hash,facts_json,json.dumps(data['images']),json.dumps(data['sources'],ensure_ascii=False),data['status'],now,now+delay))
    from repositories.classification_repo import ClassificationRepository
    ClassificationRepository(conn).touch([pid])
    return True


async def enrich(get_conn,pid,live_description=False):
    from product_classification import db
    def load(c):
        from repositories.classification_repo import ClassificationRepository
        p=c.execute(f'SELECT title,{SOURCE_SIGNATURE} FROM canonical_products p WHERE id=?',(pid,)).fetchone()
        return (p,stored(c,pid),ClassificationRepository(c).sources(pid)) if p else (None,None,[])
    product,existing,sources=await db(get_conn,load)
    if not product:return None
    signature=product[1];key=(pid,signature)
    cached=_cache.get(key)
    if cached and cached[0]>time.monotonic():return {**cached[1],**({'checked_at':existing['checked_at']} if existing else {})}
    if existing and existing['next_attempt']>time.time() and (not live_description or existing['status']!='ready'):
        return {**existing, 'description':'','summary':summary(existing['facts'])}
    if key in _inflight:return await asyncio.shield(_inflight[key])
    future=asyncio.get_running_loop().create_future();_inflight[key]=future
    result=dict(status='unavailable',facts=[],images=[],sources=[],description='')
    attempted=False
    try:
        from product_details import _fetch_rate,_fetch_semaphore,FETCH_WAIT_SECONDS,fetch_html,store_host_allowed
        seen=set()
        allowed=[s for s in sources if store_host_allowed(urlsplit(s['url']).hostname)]
        for source in allowed[:2]:
            if _fetch_rate.retry_after('details'):break
            semaphore=_fetch_semaphore()
            try:await asyncio.wait_for(semaphore.acquire(),FETCH_WAIT_SECONDS)
            except asyncio.TimeoutError:break
            try:
                attempted=True
                html=await asyncio.to_thread(fetch_html,source['url'])
                parsed=await asyncio.to_thread(extract,html,source['url'],product[0],source['name']) if html else None
            except Exception:parsed=None
            finally:semaphore.release()
            if not parsed or parsed['status'] not in ('ready','empty'):continue
            result['sources'].append(dict(url=source['url'],name=source['name'],title=parsed.get('source_title','')))
            if not result['description']:result['description']=parsed['description']
            # Keep conflicting values with provenance; never silently replace one store by another.
            for f in parsed['facts']:
                if (f['name'],f['value']) not in seen and len(result['facts'])<60:
                    result['facts'].append(f);seen.add((f['name'],f['value']))
            for image in parsed['images']:
                if image_key(image) not in {image_key(x) for x in result['images']} and len(result['images'])<12:result['images'].append(image)
            result['status']='ready' if result['facts'] or result['images'] or result['description'] else 'empty'
            if result['facts'] and len(result['images'])>=2:break
        if allowed and not attempted:
            result={**(existing or result),'description':'','status':(existing or {}).get('status','deferred')}
            future.set_result(result);return result
        # A temporary failure does not erase facts or pretend they were reverified now.
        if not result['sources'] and existing:
            await db(get_conn,lambda c:c.execute('UPDATE product_source_evidence SET next_attempt=? WHERE product_id=?',(time.time()+21600,pid)))
            result={**existing,'description':'','summary':summary(existing['facts'])}
            future.set_result(result);return result
        saved=await db(get_conn,lambda c:save(c,pid,signature,result))
        if not saved:return None
        result['summary']=summary(result['facts']);result['checked_at']=time.time()
        _cache[key]=(time.monotonic()+600,result);_cache.move_to_end(key)
        while len(_cache)>300:_cache.popitem(last=False)
        future.set_result(result);return result
    except BaseException as e:
        future.set_result(None)
        if isinstance(e,Exception):return None
        raise
    finally:_inflight.pop(key,None)


async def worker(get_conn):
    from product_classification import db
    while enabled():
        try:
            def choose(c):
                return c.execute(f'''SELECT p.id FROM canonical_products p
                  LEFT JOIN product_source_evidence e ON e.product_id=p.id
                  LEFT JOIN classification_jobs j ON j.product_id=p.id
                  WHERE (e.product_id IS NULL OR e.signature!={SOURCE_SIGNATURE} OR e.next_attempt<=?)
                  AND EXISTS(SELECT 1 FROM offers o WHERE o.product_id=p.id AND (o.is_active=1 OR j.status='review' OR j.forced=1) AND o.url!='')
                  ORDER BY CASE WHEN j.status='review' OR j.forced=1 THEN 0 ELSE 1 END,coalesce(e.checked_at,0),p.rowid LIMIT 1''',(time.time(),)).fetchone()
            row=await db(get_conn,choose)
            if row:await enrich(get_conn,row[0])
            await asyncio.sleep(8 if row else 30)
        except asyncio.CancelledError:raise
        except Exception:
            await asyncio.sleep(30)
