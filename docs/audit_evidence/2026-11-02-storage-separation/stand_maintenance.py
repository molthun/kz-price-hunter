from pathlib import Path
import collections
import hashlib
import json
import sqlite3
from content_retention import sanitized_backup,description_counts
from repositories.category_repo import CategoryRepository
from domain.feed_categories import apply_feed_categories,repair_storage_categories

root=Path('/app/data');db=root/'prices.db';dest=root/'backups'/'before-storage-split-6158-sanitized.db'
assert db.is_file() and not dest.exists()
tables=('products','canonical_products','offers','offer_price_history','price_observations','price_history','watches','watch_state','watch_events','users','favorites','local_search_intents','catalog_expansions','catalog_expansion_matches','product_search_attributes')
def fingerprint(conn):
    data={}
    for table in tables:
        cols=[r[1] for r in conn.execute(f'PRAGMA table_info({table})') if not(table=='canonical_products' and r[1]=='category')]
        if not cols:continue
        projection=','.join('"'+col+'"' for col in cols)
        digest=hashlib.sha256();count=0
        for row in conn.execute(f'SELECT {projection} FROM {table} ORDER BY rowid'):
            digest.update(json.dumps(list(row),ensure_ascii=False,default=str).encode());digest.update(b'\n');count+=1
        data[table]=(count,digest.hexdigest())
    return data
with sqlite3.connect(db) as conn:
    before=fingerprint(conn)
    with sqlite3.connect(dest) as backup:
        sanitized_backup(conn,backup)
        assert backup.execute('PRAGMA quick_check').fetchone()[0]=='ok'
        assert fingerprint(backup)==before
    print(json.dumps({'recovery_copy':dest.name,'quick_check':'ok','minimal_data_unchanged':True}),flush=True)
    synced=CategoryRepository(conn).sync_master_taxonomy();conn.commit()
    feed_moves=apply_feed_categories(conn)
    preview=repair_storage_categories(conn,apply=False)
    moves=repair_storage_categories(conn)
    assert preview==moves
    apply_feed_categories(conn)
    assert repair_storage_categories(conn,apply=False)==[]
    assert fingerprint(conn)==before
    assert conn.execute('PRAGMA quick_check').fetchone()[0]=='ok'
    assert not any(description_counts(conn).values())
    target=conn.execute("SELECT category FROM canonical_products WHERE id='prod_0b8ba922f60894c3'").fetchone()[0]
    assert target=='Оперативная память'
    print(json.dumps({'taxonomy_synced':synced,'feed_moves':feed_moves,'repaired':len(moves),'by_target':dict(collections.Counter(row[2] for row in moves)),'all_minimal_data_preserved':True,'repeat_stable':True,'AMD_Radeon_category':target,'categories':[list(r) for r in conn.execute("SELECT category,count(*) FROM canonical_products WHERE category IN ('SSD накопители','Жесткие диски HDD','Оперативная память','Флешки и карты памяти') GROUP BY category")]},ensure_ascii=False),flush=True)
