import sqlite3
c=sqlite3.connect('file:/app/data/prices.db?mode=ro', uri=True); c.execute('PRAGMA query_only=ON')
T='2026-11-20T19:00:35'
print('cols', [r[1] for r in c.execute("pragma table_info(source_scans)")])
srcs=c.execute("select source_url, sum(substr(product_id,instr(product_id,'@')+1)='kz' and active=1), sum(substr(product_id,instr(product_id,'@')+1)='unknown' and active=1) from product_sources where shop_key='kari' group by 1").fetchall()
scanned={r[0]:r[1:] for r in c.execute("select source_url, count(*), max(started_at), group_concat(distinct quality) from source_scans where shop_key='kari' and julianday(started_at)>=julianday(?) group by 1",(T,))}
a=[s for s in srcs if s[0] in scanned]; b=[s for s in srcs if s[0] not in scanned]
print('kari sources total', len(srcs), 'scanned since restart', len(a), 'not scanned', len(b))
print('scanned: active kz', sum(s[1] or 0 for s in a), 'active unknown', sum(s[2] or 0 for s in a))
print('not scanned: active kz', sum(s[1] or 0 for s in b), 'active unknown', sum(s[2] or 0 for s in b))
print('unknown-quality scans', [(u[:70],)+tuple(scanned[u]) for u in scanned if 'unknown' in (scanned[u][2] or '')])
print('last kari scan any', c.execute("select max(started_at) from source_scans where shop_key='kari'").fetchone())
print('kari scans per day', c.execute("select substr(started_at,1,13), count(*) from source_scans where shop_key='kari' and julianday(started_at)>=julianday('2026-11-20') group by 1").fetchall())
# @kz retired since restart: does an @unknown twin exist active?
print('kz retired since restart with active unknown twin', c.execute("""select count(*), sum(u.is_active=1) from offers o join products p on o.id='off_'||p.id
 left join products u on u.id=substr(p.id,1,instr(p.id,'@'))||'unknown'
 where p.shop='Kari' and o.is_active=0 and julianday(o.updated_at)>=julianday(?) and p.id like '%@kz'""",(T,)).fetchone())
print('shopkz astana retired since restart, kz twin active', c.execute("""select count(*), sum(k.is_active=1) from offers o join products p on o.id='off_'||p.id
 left join products k on k.id=substr(p.id,1,instr(p.id,'@'))||'kz'
 where p.shop='Белый Ветер' and o.is_active=0 and julianday(o.updated_at)>=julianday(?) and p.id like '%@astana'""",(T,)).fetchone())
print('shopkz kz retired since restart with active astana twin', c.execute("""select count(*), sum(a.is_active=1) from offers o join products p on o.id='off_'||p.id
 left join products a on a.id=substr(p.id,1,instr(p.id,'@'))||'astana'
 where p.shop='Белый Ветер' and o.is_active=0 and julianday(o.updated_at)>=julianday(?) and p.id like '%@kz'""",(T,)).fetchone())
