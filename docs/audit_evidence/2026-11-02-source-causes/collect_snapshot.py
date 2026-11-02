import datetime,json,re,hashlib,collections,subprocess
from pathlib import Path
import config,database,monitoring
from web.server import SHOP_REGISTRY,_monitoring_registry,enabled_shop_keys

def signals(reason):
    s=str(reason or '').lower();codes=[]
    patterns={
      'http_403':r'\b403\b','http_404':r'\b404\b','http_429':r'\b429\b','http_5xx':r'\b50[0-9]\b',
      'timeout':r'timeout|timed out|таймаут|превышен.*времен',
      'dns':r'dnserror|could not resolve|name.*resolution',
      'cooldown':r'hostcooldown|cooldown',
      'empty_cards':r'карточки не найдены|не найдены карточки',
      'repeated_ids':r'повторов|дубл',
      'transport':r'connection|curl|ssl|resolve|соединен|соединён',
      'pagination_unconfirmed':r'unconfirmed|не подтвержд|неподтвержд|конец.*не|empty.*page|пуст.*страниц|стран.*пуст',
      'page_limit':r'max_pages|лимит.*страниц|ограничен.*страниц|достигнут.*лимит|page.?limit',
      'volume_drop':r'при норме|baseline|объ[её]м|паден.*товар',
      'rejected_cards':r'отброшено|rejected|невалид|некоррект.*цен',
      'image_drop':r'фото|изображен|image',
      'queue_lease':r'lease|аренд|очеред|spool|fencing|барьер',
      'cancelled':r'cancel|отмен|останов',
      'parse_schema':r'keyerror|jsondecode|attributeerror|неизвест.*формат|schema|не.*json|json.*ошиб',
      'bot_guard':r'captcha|капч|cloudflare|forbidden|access denied|защит',
      'empty':r'нет товаров|не вернул товаров|0 товар|no products|no items',
    }
    for code,pattern in patterns.items():
        if re.search(pattern,s):codes.append(code)
    return codes or (['other'] if s else [])

settings=config.load_settings();now=monitoring._now();rows=monitoring.shops_overview(_monitoring_registry(),enabled_shop_keys(settings),now)
with monitoring._conn() as c:
    cats=monitoring._latest_categories(c,list(SHOP_REGISTRY))
    out=[]
    for row in rows:
        k=row['shop_key'];detail=[]
        for cat in cats.get(k,[]):
            detail.append({key:cat.get(key) for key in ('category','source_url','kind','quality','received','valid','rejected','duplicates','baseline','baseline_basis','accepted','finished_at')} | {'reason_codes':signals(cat.get('reason'))})
        out.append({key:row.get(key) for key in ('shop_key','name','status','scan_status','last_attempt_at','last_success_at','last_items','failure_count','freshness','categories_total','categories_observed','categories_problem','baseline_items','http_requests_24h','http_errors_24h')} | {'reason_codes':signals(row.get('reason')),'last_error_codes':signals(row.get('last_error')),'categories':detail})
result={'at':now.isoformat(),'stand_sha':subprocess.check_output(['git','-C','/app','rev-parse','HEAD'],text=True).strip() if Path('/app/.git').exists() else None,'settings_sha256':hashlib.sha256(json.dumps(settings,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest(),'counts':dict(collections.Counter(r['status'] for r in out)),'shops':out}
print(json.dumps(result,ensure_ascii=False))
