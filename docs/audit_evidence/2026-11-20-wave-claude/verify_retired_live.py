import json, sys, config, offer_identity
from collections import Counter
from scrapers.sulpak import SulpakScraper
from scrapers.technodom import TechnodomScraper
gone=json.load(open(sys.argv[1]))
live={}; incomplete=[]
for shop,cls,cats in (("Sulpak",SulpakScraper,config.SULPAK_CATEGORIES),("Технодом",TechnodomScraper,config.TECHNODOM_CATEGORIES)):
    ids=set()
    for cat in cats:
        r=cls()._scrape_sync(cat["name"],cat["url"],cat.get("max_pages"))
        if not r.complete: incomplete.append((shop,cat["name"],r.error))
        offer_identity.assign_offer_ids(r)
        ids.update(x["id"] for x in r)
    live[shop]=ids
for shop in ("Sulpak","Технодом"):
    g=gone.get(shop,[]); present=[x for x in g if x[0] in live[shop]]
    print(shop, "retired", len(g), "present_in_live_now", len(present), Counter(x[1] for x in g), "present_by_city", Counter(x[1] for x in present))
    print("  sample present", present[:5])
print("incomplete live categories", incomplete)
json.dump({k:sorted(v) for k,v in live.items()}, open(sys.argv[2],"w"))
