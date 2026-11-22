import sqlite3, json
P, S = "prod_20261122T1732Z.db", "stand_20261122T1732Z.db"
c = sqlite3.connect(S); c.execute(f"ATTACH \"file:{P}?mode=ro\" AS p")
def tabs(db): return {r[0] for r in c.execute(f"select name from {db}.sqlite_master where type=\"table\" and name not like \"sqlite_%\" and name not like \"%fts%\"")}
tp, ts = tabs("p"), tabs("main")
rows = []
for t in sorted(tp | ts):
    a = c.execute(f"select count(*) from p.\"{t}\"").fetchone()[0] if t in tp else None
    b = c.execute(f"select count(*) from main.\"{t}\"").fetchone()[0] if t in ts else None
    if a or b: rows.append((t, a, b))
for r in rows: print("%-34s prod=%-9s stand=%s" % r)
print("--- prod-only offers")
q = """select s.name, o.is_active, count(*), max(o.updated_at) from p.offers o join p.sellers s on s.id=o.seller_id
 where o.id not in (select id from main.offers) group by 1,2 order by 3 desc limit 25"""
for r in c.execute(q): print(r)
print("--- common offers: id mismatches")
print(c.execute("""select sum(po.product_id<>so.product_id), sum(po.seller_id<>so.seller_id), sum(po.channel_id<>so.channel_id), count(*)
 from p.offers po join main.offers so on so.id=po.id""").fetchone())
print("--- history overlap for common offers")
print(c.execute("""select count(*) from p.offer_price_history h where h.offer_id in (select id from main.offers)""").fetchone(),
      c.execute("""select count(*) from p.offer_price_history h join main.offer_price_history s on s.offer_id=h.offer_id and s.observed_at=h.observed_at""").fetchone())
print("--- sellers in prod not in stand", [r for r in c.execute("select id,name from p.sellers where id not in (select id from main.sellers)")])
