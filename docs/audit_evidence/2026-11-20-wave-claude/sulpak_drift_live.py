import time
from scrapers.sulpak import SulpakScraper
from scrapers.base import UnconfirmedEnd
URL="https://www.sulpak.kz/f/smartfoniy/"
def walk():
    s=SulpakScraper(cities=("Алматы",)); s._active_city="Алматы"; s.cookies={"city_id":"1","language_id":"3"}
    pages=[]; seen=[]
    for pg in range(1,80):
        try: r=s._fetch_page("C",URL,pg)
        except UnconfirmedEnd: break
        ids=[x["id"] for x in r]
        if not ids: break
        pages.append(ids)
        if r.complete: break
        time.sleep(0.5)
    allids=[i for p in pages for i in p]
    return pages, allids
p1,a1=walk(); p2,a2=walk()
for n,(p,a) in enumerate(((p1,a1),(p2,a2)),1):
    print(f"pass{n}: pages {len(p)} cards {len(a)} unique {len(set(a))} repeated {len(a)-len(set(a))}")
print("only in pass1", len(set(a1)-set(a2)), "only in pass2", len(set(a2)-set(a1)))
