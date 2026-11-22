"""Read-only: GET prod /api/products per shop; recompute offer_id with current code; compare."""
import json, urllib.request, urllib.parse, sys
from collections import Counter
from config import SHOP_KEYS
from offer_identity import offer_id
tot=Counter(); bad=Counter(); examples={}
for key,name in SHOP_KEYS.items():
    for off in (0, 200, 1000, 5000, 20000):
        q=urllib.parse.urlencode({"shop":name,"limit":200,"offset":off})
        try: d=json.load(urllib.request.urlopen("https://shop.molthun.ru/api/products?"+q, timeout=60))
        except Exception as e: print("ERR",name,type(e).__name__); break
        if not d["products"]: break
        for p in d["products"]:
            base=p["id"].split("@")[0]
            new=offer_id({"id":base,"shop":p["shop"],"city":p["city"]})
            tot[name]+=1
            if new!=p["id"]:
                bad[(name,p["city"],p["id"].split("@")[-1],new.split("@")[-1])]+=1
                examples.setdefault(name,(p["id"],new,p["city"]))
print("checked",sum(tot.values()),"shops",len(tot))
print("mismatches",sum(bad.values()))
for k,v in bad.most_common(20): print(" ",v,k)
print(examples)
