import socket,time,json,concurrent.futures,datetime
from urllib.parse import urlsplit,urlunsplit
from web.server import SHOP_REGISTRY

def dns(item):
 key,(cls,cats,name)=item;host=urlsplit(cats[0]['url']).hostname
 if not host: host=urlsplit(getattr(cls(),'api_base',None) or getattr(cls(),'base_url','')).hostname
 start=time.monotonic()
 try:
  if not host:return dict(shop_key=key,host=None,ok=None,reason='category_is_not_url')
  addresses=socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
  return dict(shop_key=key,host=host,ok=bool(addresses),latency_ms=round((time.monotonic()-start)*1000))
 except Exception as e:
  return dict(shop_key=key,host=host,ok=False,error_type=type(e).__name__,latency_ms=round((time.monotonic()-start)*1000))

result={'at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:result['dns']=list(pool.map(dns,SHOP_REGISTRY.items()))
print(json.dumps(result,ensure_ascii=False))
