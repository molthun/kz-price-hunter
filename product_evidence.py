"""Source-bound structured facts and remote image links; source prose stays in RAM only."""
from __future__ import annotations
import asyncio
import hashlib
import html
import json
import os
import re
import time
from collections import OrderedDict
from urllib.parse import urljoin,urlsplit,urlunsplit,parse_qsl,urlencode

EXCLUDED_FACT_NAMES = {
    'название в магазине', 'название товара', 'наименование товара',
    'артикул магазина', 'код товара в магазине',
}


def clean_fact_str(val: str) -> str:
    if not val:
        return ''
    s = html.unescape(str(val))
    return ' '.join(s.split())


def sanitize_facts(facts: list) -> list:
    if not isinstance(facts, list):
        return []
    res = []
    seen = set()
    for item in facts:
        if not isinstance(item, dict):
            continue
        raw_name = clean_fact_str(item.get('name'))
        raw_value = clean_fact_str(item.get('value'))
        if not raw_name or not raw_value or len(raw_value) > 300:
            continue
        low_name = raw_name.casefold()
        if low_name in EXCLUDED_FACT_NAMES:
            continue
        if any(x in low_name for x in ('описание', 'description', 'внимание', 'примечание', 'срок поставки')) or low_name in ('цена', 'price'):
            continue
        pair = (low_name, raw_value.casefold())
        if pair in seen:
            continue
        seen.add(pair)
        clean_item = dict(item)
        clean_item['name'] = raw_name[:80]
        clean_item['value'] = raw_value
        res.append(clean_item)
    return res[:60]

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
        name=clean_fact_str(name)[:80];value=clean_fact_str(value)
        if not name or not value or len(value)>300:return
        low_name=name.casefold()
        if low_name in EXCLUDED_FACT_NAMES:return
        if any(x in low_name for x in ('описание','description','внимание','примечание','срок поставки')) or low_name in ('цена','price'):return
        if (low_name,value.casefold()) in seen:return
        seen.add((low_name,value.casefold()))
        if len(facts)<60:facts.append(dict(name=name,value=value,source_url=url))
    images=[];image_seen=set()
    def image(value):
        if isinstance(value,list):
            for x in value:image(x)
        elif isinstance(value,dict):image(value.get('contentUrl') or value.get('url'))
        elif value:
            link=safe_url(value,url)
            if link:
                from domain.image_quality import upgrade_image_url
                link = upgrade_image_url(link)
                if image_key(link) not in image_seen and len(images)<12:
                    image_seen.add(image_key(link));images.append(link)
    if node:
        brand=node.get('brand');fact('Бренд',brand.get('name','') if isinstance(brand,dict) else brand)
        fact('Модель',node.get('model'))
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
    for el in soup.select('.product-gallery img, .product-images img, .product-slider img, .bx_bigimages img, .detail-gallery-big__link[href], .swiper-slide img, .gallery img'):
        img_val = (
            el.get('data-zoom-image') or el.get('data-large') or el.get('data-original') or
            el.get('data-highres') or el.get('data-full-src') or el.get('data-large-src') or
            el.get('data-big') or el.get('data-zoom') or el.get('data-src') or
            el.get('src') or el.get('href') or el.get('content')
        )
        image(img_val)
    if not images:
        meta=soup.select_one('meta[property="og:image"]')
        if meta:image(meta.get('content'))
    text=extract_description(html,url,shop)
    if node and isinstance(node.get('description'),str):
        text=make_soup(node['description']).get_text(' ',strip=True)[:20000]
    clean_list=sanitize_facts(facts)
    has_payload = bool(clean_list or images or text or (node or heading))
    return dict(status='ready' if has_payload else 'empty',facts=clean_list,images=images,description=text,source_url=url,source_title=str(node.get('name','') if node else heading)[:300])


def enrich_facts_with_canonical_attributes(conn, pid: str, facts: List[Dict[str, Any]], title: str) -> List[Dict[str, Any]]:
    existing_names = {f['name'].casefold() for f in facts if isinstance(f, dict) and f.get('name')}
    res = list(facts)
    try:
        row = conn.execute("SELECT brand, model, category FROM canonical_products WHERE id = ?", (pid,)).fetchone()
        if row:
            brand, model, cat = row[0], row[1], row[2]
            if brand and 'бренд' not in existing_names:
                res.insert(0, {'name': 'Бренд', 'value': brand, 'source_url': ''})
                existing_names.add('бренд')
            if model and 'модель' not in existing_names and model != title:
                res.append({'name': 'Модель', 'value': model, 'source_url': ''})
                existing_names.add('модель')
    except Exception:
        pass

    try:
        from domain.attribute_indexing import ATTRIBUTE_LABELS
        attr_rows = conn.execute(
            "SELECT key, value FROM product_search_attributes WHERE product_id = ?",
            (pid,)
        ).fetchall()
        for k, v in attr_rows:
            label = ATTRIBUTE_LABELS.get(k, k)
            if label.casefold() not in existing_names and v:
                res.append({'name': label, 'value': v, 'source_url': ''})
                existing_names.add(label.casefold())
    except Exception:
        pass

    if len(res) < 3 and title:
        try:
            from domain.attribute_indexing import extract_title_attributes, ATTRIBUTE_LABELS
            title_attrs = extract_title_attributes(title)
            for k, v in title_attrs.items():
                label = ATTRIBUTE_LABELS.get(k, k)
                if label.casefold() not in existing_names and v:
                    res.append({'name': label, 'value': v, 'source_url': ''})
                    existing_names.add(label.casefold())
        except Exception:
            pass

    return res


def stored(conn,pid):
    row=conn.execute('SELECT signature,facts_json,images_json,sources_json,status,checked_at,next_attempt FROM product_source_evidence WHERE product_id=?',(pid,)).fetchone()
    current=conn.execute(f'SELECT {SOURCE_SIGNATURE}, title FROM canonical_products p WHERE id=?',(pid,)).fetchone()
    if not row or not current or row[0]!=current[0]:
        if current and current[1]:
            synth_facts = enrich_facts_with_canonical_attributes(conn, pid, [], current[1])
            if synth_facts:
                return dict(facts=sanitize_facts(synth_facts),images=[],sources=[],status='ready',checked_at=time.time(),next_attempt=time.time()+86400)
        return None
    raw_facts=json.loads(row[1]) if row[1] else []
    clean_facts=enrich_facts_with_canonical_attributes(conn, pid, raw_facts, current[1] if current else '')
    return dict(facts=sanitize_facts(clean_facts),images=json.loads(row[2]),sources=json.loads(row[3]),status=row[4],checked_at=row[5],next_attempt=row[6])


def summary(facts,limit=400):
    clean=sanitize_facts(facts)
    return '; '.join(f["name"]+': '+f["value"] for f in clean[:6])[:limit]


def save(conn,pid,signature,data):
    current=conn.execute(f'SELECT {SOURCE_SIGNATURE} FROM canonical_products p WHERE id=?',(pid,)).fetchone()
    if not current or current[0]!=signature:return False
    now=time.time();delay=7*86400 if data['status']=='ready' else 6*3600
    clean_facts=sanitize_facts(data.get('facts',[]))
    facts_json=json.dumps(clean_facts,ensure_ascii=False,sort_keys=True)
    facts_hash=hashlib.sha256(facts_json.encode()).hexdigest()
    conn.execute('''INSERT INTO product_source_evidence VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(product_id)
      DO UPDATE SET signature=excluded.signature,facts_hash=excluded.facts_hash,facts_json=excluded.facts_json,images_json=excluded.images_json,
      sources_json=excluded.sources_json,status=excluded.status,checked_at=excluded.checked_at,next_attempt=excluded.next_attempt''',
      (pid,signature,facts_hash,facts_json,json.dumps(data['images']),json.dumps(data['sources'],ensure_ascii=False),data['status'],now,now+delay))
    try:
        from domain.attribute_indexing import index_product_facts
        index_product_facts(conn, pid, clean_facts)
    except Exception:
        pass
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
            clean_parsed = sanitize_facts(parsed.get('facts', []))
            for f in clean_parsed:
                pair = (f['name'].casefold(), f['value'].casefold())
                if pair not in seen and len(result['facts'])<60:
                    result['facts'].append(f);seen.add(pair)
            result['facts'] = sanitize_facts(result['facts'])
            from domain.image_quality import upgrade_image_url
            for image in parsed['images']:
                up_img = upgrade_image_url(image)
                if image_key(up_img) not in {image_key(x) for x in result['images']} and len(result['images'])<12:result['images'].append(up_img)
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
