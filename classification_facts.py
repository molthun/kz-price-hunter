"""Bounded structured attributes for classification; prose and credentials stay out."""
import hashlib
import json
import re

_METADATA = {'название в магазине','артикул','модель','brand','бренд','manufacturer','производитель'}
_SKIP = {'url','source_url','image','images','image_url','photos','photo','shop','seller','merchant','city','price','old_price','email','phone','address','telegram','whatsapp','user_id','token','api_key','password','secret','cookie','authorization'}
_TYPE = {'тип товара','вид товара','тип устройства','product type','device type','тип накопителя','тип памяти','форм-фактор','форм-фактор памяти','form factor','memory type'}


def priority(name):
    text=name.casefold()
    if text in _TYPE:return 0
    if any(x in text for x in ('процессор','диагональ','чипсет','сокет','cpu','gpu','processor','screen','chipset','socket','graphics','назначение','интерфейс','тип ','вид ')):return 1
    if text in _METADATA:return 3
    return 2


def _fact(name,value):
    if not isinstance(name,str) or not isinstance(value,(str,int,float,bool)):return None
    name=' '.join(name.split())[:80];value=' '.join(str(value).split())
    if not name or not value or len(value)>300:return None
    lowered=name.casefold()
    if any(x in lowered for x in ('описание','description','примечание','внимание')) or any(part in _SKIP for part in lowered.split(' / ')):return None
    if re.search(r'(?i)(?:https?://|www\.|\bBearer\s|\bsk-(?:proj-|svcacct-))',value):return None
    if re.search(r'(?i)(?:token|password|secret|api_key|authorization|cookie)',lowered):return None
    return dict(name=name,value=value)


def catalog_facts(attributes):
    if isinstance(attributes,str):
        if len(attributes)>64000:return []
        try:attributes=json.loads(attributes)
        except (ValueError,TypeError):return []
    if not isinstance(attributes,dict):return []
    facts=[]
    def visit(data,path='',depth=0):
        for key,value in sorted(data.items(),key=lambda item:(priority(str(item[0])),str(item[0])))[:100]:
            name=(path+' / ' if path else '')+str(key)
            if isinstance(value,dict) and depth<2:visit(value,name,depth+1)
            elif isinstance(value,list) and all(isinstance(v,(str,int,float,bool)) for v in value[:10]):
                fact=_fact(name,'; '.join(str(v) for v in value[:10]))
                if fact:facts.append(fact)
            else:
                fact=_fact(name,value)
                if fact:facts.append(fact)
    visit(attributes)
    return sorted(facts,key=lambda f:(priority(f['name']),f['name'],f['value']))[:60]


def attributes_hash(attributes):
    facts=catalog_facts(attributes)
    return hashlib.sha256(json.dumps(facts,ensure_ascii=False,sort_keys=True).encode()).hexdigest() if facts else ''


def selected_source_facts(facts,limit=40):
    result=[];groups={}
    for row in facts if isinstance(facts,list) else []:
        if not isinstance(row,dict):continue
        fact=_fact(row.get('name'),row.get('value'))
        if not fact:continue
        source=str(row.get('source_url') or '')
        if source not in groups:groups[source]=len(groups)+1
        result.append({**fact,'source_group':groups[source]})
    return sorted(result,key=lambda f:priority(f['name']))[:limit]


def conflicts(facts):
    values={}
    for row in facts if isinstance(facts,list) else []:
        if not isinstance(row,dict):continue
        name=str(row.get('name') or '').strip().casefold()
        aliases={'memory type':'тип памяти','form factor':'форм-фактор','форм-фактор памяти':'форм-фактор','product type':'тип товара','вид товара':'тип товара','device type':'тип устройства'}
        name=aliases.get(name,name)
        if name in _TYPE:values.setdefault(name,set()).add(str(row.get('value') or '').strip().casefold())
    return sorted(name for name,items in values.items() if len(items)>1)


def explicit_type_evidence(facts):
    for row in facts if isinstance(facts,list) else []:
        if not isinstance(row,dict):continue
        name=str(row.get('name') or '').strip().casefold()
        if name in {'тип товара','вид товара','тип устройства','product type','device type'}:return True
        if name=='название в магазине' and len(re.findall(r'[а-яёa-z]{3,}',str(row.get('value') or '').casefold()))>=2:return True
    return False
