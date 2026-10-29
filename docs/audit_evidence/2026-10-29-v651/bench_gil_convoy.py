import os, time, datetime, threading
from pathlib import Path
import database as d
d.DB_PATH = Path("/tmp/bench.db")
c = d.get_connection()
rows = [dict(r) for r in c.execute("select id,shop,city,title,category,url,image_url,current_price price,"
        "old_price_on_site from products where is_active=1 order by random() limit 1200")]
c.close()
now = datetime.datetime.now(datetime.timezone.utc).isoformat()
def run(label, chunk):
    for p in chunk: p["price"] = int(p["price"]) + 1
    with d.get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE"); t = time.time()
        d._save_products_chunk(conn, chunk, now, time.time()); t1 = time.time()
        conn.commit(); t2 = time.time()
    print(label, "work", round(t1 - t, 3), "commit", round(t2 - t1, 3), flush=True)
i = 0
for mode in ("0", "1"):
    os.environ["DUAL_WRITE_V2"] = mode
    for _ in range(2):
        run(f"dual={mode}", rows[i:i+100]); i += 100
# GIL-нагрузка: 6 потоков чистого Python, как разбор HTML в обходах
stop = False
def burn():
    x = 0
    while not stop: x += 1
ts = [threading.Thread(target=burn) for _ in range(6)]
[t.start() for t in ts]
for _ in range(2):
    run("dual=1 +6 CPU threads", rows[i:i+100]); i += 100
stop = True
[t.join() for t in ts]
