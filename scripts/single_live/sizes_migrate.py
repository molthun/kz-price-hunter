"""Дополнение переноса 22.11.2026: размеры прода (offer_sizes + raw_payload.available_sizes) для
предложений живого сервера, у которых размеров нет совсем. Существующие размеры живого сервера не
трогаются. Причина: Kari получил размеры в 6.44.0 после последнего обхода Kari на стенде, а прод
успел обойти его с размерами. Следующий обход живого сервера перезапишет эти размеры свежими.
Запуск: python sizes_migrate.py SOURCE_PROD.db TARGET.db [--dry-run]"""
import json
import sqlite3
import sys
import time

src, dst = sys.argv[1], sys.argv[2]
dry = "--dry-run" in sys.argv
t0 = time.time()
c = sqlite3.connect(dst, isolation_level=None, timeout=30)
c.execute("PRAGMA busy_timeout=30000")
c.execute(f"ATTACH 'file:{src}?mode=ro' AS p")
rep = {}
c.execute("BEGIN IMMEDIATE")
try:
    c.execute("""CREATE TEMP TABLE need AS
        SELECT DISTINCT ps.offer_id FROM p.offer_sizes ps
        JOIN main.offers o ON o.id = ps.offer_id
        WHERE NOT EXISTS (SELECT 1 FROM main.offer_sizes s WHERE s.offer_id = ps.offer_id)
          AND (o.raw_payload_json IS NULL OR o.raw_payload_json = '' OR NOT json_valid(o.raw_payload_json)
               OR json_extract(o.raw_payload_json, '$.available_sizes') IS NULL)""")
    rep["offers"] = c.execute("SELECT count(*) FROM need").fetchone()[0]
    rep["by_seller"] = c.execute("""SELECT s.name, count(*) FROM need n JOIN main.offers o ON o.id = n.offer_id
                                    JOIN main.sellers s ON s.id = o.seller_id GROUP BY 1 ORDER BY 2 DESC""").fetchall()
    cur = c.execute("""INSERT OR IGNORE INTO main.offer_sizes (offer_id, product_id, size, updated_at)
        SELECT ps.offer_id, o.product_id, ps.size, ps.updated_at
        FROM p.offer_sizes ps JOIN need n ON n.offer_id = ps.offer_id JOIN main.offers o ON o.id = ps.offer_id""")
    rep["size_rows_added"] = cur.rowcount
    cur = c.execute("""UPDATE main.offers SET raw_payload_json = json_set(
            CASE WHEN raw_payload_json IS NULL OR raw_payload_json = '' OR NOT json_valid(raw_payload_json)
                 THEN '{}' ELSE raw_payload_json END,
            '$.available_sizes',
            json((SELECT json_extract(po.raw_payload_json, '$.available_sizes') FROM p.offers po WHERE po.id = main.offers.id)))
        WHERE id IN (SELECT offer_id FROM need)
          AND (SELECT json_extract(po.raw_payload_json, '$.available_sizes') FROM p.offers po WHERE po.id = main.offers.id) IS NOT NULL""")
    rep["payloads_updated"] = cur.rowcount
    rep["offer_sizes_after"] = c.execute("SELECT count(*), count(DISTINCT offer_id) FROM main.offer_sizes").fetchone()
    c.execute("ROLLBACK" if dry else "COMMIT")
except Exception:
    c.execute("ROLLBACK")
    raise
rep["dry_run"] = dry
rep["secs"] = round(time.time() - t0, 1)
print(json.dumps(rep, ensure_ascii=False))
