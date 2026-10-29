"""Запись одних и тех же порций кодом из PYTHONPATH; дамп затронутых строк в JSON."""
import json, random, sqlite3, sys, time
from pathlib import Path
src, dst, out, mode = sys.argv[1:5]
import shutil; shutil.copy(src, dst)
import database as d
d.DB_PATH = Path(dst)
if mode == "new":
    from repositories.schema_v2 import init_schema_v2
    with d.get_connection() as c:
        init_schema_v2(c); c.commit()
random.seed(651)
with d.get_connection() as c:
    base = [dict(r) for r in c.execute(
        "select id, shop, city, title, category, url, image_url, description, current_price price, "
        "old_price_on_site from products where is_active=1 order by id limit 4000 offset 20000")]
random.shuffle(base)
chunks = []
for k in range(12):
    ch = []
    for p in base[k*100:(k+1)*100]:
        p = dict(p); r = random.random()
        if r < .3: p["price"] = int(p["price"]) + random.choice([-500, 700])
        elif r < .4: p["title"] = p["title"] + " NEW"
        elif r < .45: p["price"] = 0
        elif r < .5: p["id"] = p["id"] + "_eqnew"
        elif r < .55: p["description"] = "описание " + p["id"]
        elif r < .6: p["old_price_on_site"] = int(p["price"]) * 2
        ch.append(p)
        if random.random() < .05: ch.append(dict(p, price=int(p["price"] or 1000) + 1))
    chunks.append(ch)
touched = sorted({p["id"] for ch in chunks for p in ch})
t = time.time()
for k, ch in enumerate(chunks):
    with d.get_connection() as c:
        c.execute("BEGIN IMMEDIATE")
        d._save_products_chunk(c, ch, f"2026-10-29T1{k%10}:00:00+00:00", 3e9 + k)
        c.commit()
el = time.time() - t
marks = json.dumps(touched)
offers = json.dumps(["off_" + i for i in touched])
c = sqlite3.connect(dst)
c.execute("INSERT INTO products_fts(products_fts) VALUES('integrity-check')")
c.execute("INSERT INTO canonical_products_fts(canonical_products_fts) VALUES('integrity-check')")
q = lambda s, *a: [list(r) for r in c.execute(s, a)]
dump = {
 "products": q("select id,shop,city,title,category,url,image_url,description,current_price,old_price_on_site,"
               "first_seen_price,min_price,max_price,canonical_key,is_active,created_at,updated_at,observed_seq "
               "from products where id in (select value from json_each(?)) order by id", marks),
 "observations": q("select product_id,price,old_price_on_site,observed_at from price_observations "
                   "where product_id in (select value from json_each(?)) order by product_id, rowid", marks),
 "offers": q("select id,product_id,seller_id,channel_id,external_sku,url,image_url,price,old_price,condition,"
             "availability,city,observed_at,is_active,created_at,updated_at from offers "
             "where id in (select value from json_each(?)) order by id", offers),
 "history": q("select offer_id,price,old_price,observed_at,product_id,seller_id,channel_id,condition,city "
              "from offer_price_history where offer_id in (select value from json_each(?)) "
              "order by offer_id, observed_at", offers),
 "canonical": q("select id,canonical_key,description,image_url,created_at,updated_at from canonical_products "
                "where id in (select product_id from offers where id in (select value from json_each(?))) "
                "order by id", offers),
 "sellers": q("select id,slug,name,domain,is_active,created_at,updated_at from sellers order by id"),
 "identities": q("select id,seller_id,identity_type,identity_value from seller_identities order by id"),
 "channels": q("select id,seller_id,channel_type,name from channels order by id"),
}
json.dump({"elapsed": el, "items": sum(map(len, chunks)), "dump": dump}, open(out, "w"), ensure_ascii=False)
print(mode, "chunks", len(chunks), "items", sum(map(len, chunks)), "elapsed", round(el, 2))
