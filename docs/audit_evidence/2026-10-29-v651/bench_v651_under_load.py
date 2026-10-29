"""6.5.1: запись порции 100 товаров кодом образа при 6 счётных потоках (копия базы во /tmp/bench.db)."""
import datetime, os, sys, threading, time
from pathlib import Path
import database as d
d.DB_PATH = Path("/tmp/bench.db")
c = d.get_connection()
rows = [dict(r) for r in c.execute("select id,shop,city,title,category,url,image_url,current_price price,"
        "old_price_on_site from products where is_active=1 limit 1200")]
c.close()
now = datetime.datetime.now(datetime.timezone.utc).isoformat()

def run(label, chunk):
    for p in chunk:
        p["price"] = int(p["price"]) + 1
    prepared_at = time.time()
    prepared = d._prepare_products_chunk(chunk)
    with d.get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE"); t = time.time()
        d._save_products_chunk(conn, chunk, now, time.time(), prepared=prepared); t1 = time.time()
        conn.commit(); t2 = time.time()
    print(f"{label}: prepare (вне блокировки) {t - prepared_at:.3f} с, под блокировкой {t1 - t:.3f} с, "
          f"commit {t2 - t1:.3f} с", flush=True)

i = 0
for _ in range(2):
    run("без нагрузки", rows[i:i + 100]); i += 100
stop = False
def burn():
    x = 0
    while not stop:
        x += 1
ts = [threading.Thread(target=burn) for _ in range(6)]
[t.start() for t in ts]
for sw in (0.005, 0.0005):
    sys.setswitchinterval(sw)
    for _ in range(2):
        run(f"6 потоков, интервал {sw * 1000:g} мс", rows[i:i + 100]); i += 100
stop = True
[t.join() for t in ts]
