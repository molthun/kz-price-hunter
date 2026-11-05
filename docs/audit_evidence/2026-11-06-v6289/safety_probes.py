"""Independent CLI acceptance and fixed failure probes on disposable SQLite files."""
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

from catalog_classifier import canonical_gpu_model
from domain.catalog_enrichment_ai import DDL_ATTRIBUTES
from scripts.recompute_gpu_models import plan_changes, apply_changes

SCRIPT = str(Path('scripts/recompute_gpu_models.py').resolve())


def fixture(path,n=1,wal=False):
    c = sqlite3.connect(path)
    if wal:
        c.execute('PRAGMA journal_mode=WAL')
        c.execute('PRAGMA wal_autocheckpoint=0')
    c.executescript('CREATE TABLE canonical_products(id TEXT PRIMARY KEY,title TEXT,model TEXT,category TEXT,updated_at TEXT);')
    c.executescript(DDL_ATTRIBUTES)
    c.executemany('INSERT INTO canonical_products VALUES(?,?,?,?,?)',
                  [(f'p{i:03}','Видеокарта RX 9070 GRE','Radeon RX 9070','gpus','now') for i in range(n)])
    c.commit()
    return c


def cli(db,report,apply=False):
    args = [sys.executable,SCRIPT,'--db',str(db),'--report',str(report)]
    if apply:
        args.append('--apply')
    return subprocess.run(args,capture_output=True,text=True)


# The original F1 collisions and report write failure are fixed for a direct DB path.
with tempfile.TemporaryDirectory(prefix='kzph-v6288-positive-') as td:
    db = Path(td)/'copy.db'
    fixture(db).close()
    symlink = Path(td)/'report-link.db'
    symlink.symlink_to(db)
    hardlink = Path(td)/'report-hard.db'
    hardlink.hardlink_to(db)
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    for report in [db,symlink,hardlink,*[Path(str(db)+s) for s in ('-wal','-shm','-journal')],Path(td)]:
        for apply in (False,True):
            result = cli(db,report,apply)
            assert result.returncode == 2,(report,apply,result.stderr)
    assert cli(db,Path(td)/'missing'/'undo.json',True).returncode == 3
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    assert cli(db,Path(td)/'plan.json').returncode == 0
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    print('ORIGINAL_F1_DIRECT_PATHS_FIXED')

# Original F2 no longer raises; conditional F3 title-derived chipset is recalculated.
for title in ['RX 9070 TiSuper','RTX 4060 TiSuper','GTX 1650 TiSuper','RTX 4060 Ti\tSuper']:
    assert canonical_gpu_model(title).endswith('Ti Super'),title
print('ORIGINAL_F2_FIXED')
with tempfile.TemporaryDirectory(prefix='kzph-v6288-chipset-') as td:
    c = fixture(Path(td)/'copy.db')
    c.execute("INSERT INTO product_search_attributes VALUES('p000','chipset','Radeon RX 9070','Название товара','Видеокарта RX 9070 GRE','now')")
    c.commit()
    plan = plan_changes(c)
    assert plan['chipsets'][0]['new'] == 'Radeon RX 9070 GRE'
    apply_changes(c,plan)
    assert c.execute("SELECT value FROM product_search_attributes WHERE product_id='p000'").fetchone()[0] == 'Radeon RX 9070 GRE'
    c.close()
print('DERIVED_CHIPSET_RECOMPUTED')


# G1: symlink in the database file or its parent directory protects the real side files.
with tempfile.TemporaryDirectory(prefix='kzph-v6289-wal-') as td:
    root = Path(td)
    real = root/'real'
    real.mkdir()
    db = real/'actual.db'
    alias = root/'alias.db'
    alias.symlink_to(db)
    linkdir = root/'linkdir'
    linkdir.symlink_to(real,target_is_directory=True)
    c = fixture(db,wal=True)
    wal = Path(str(db)+'-wal')
    protected = [db,wal,Path(str(db)+'-shm')]
    before = {str(p):p.read_bytes() for p in protected}
    targets = [db,wal,Path(str(db)+'-shm'),Path(str(db)+'-journal'),linkdir/'actual.db-wal']
    checks = 0
    for db_arg in (alias,linkdir/'actual.db'):
        for report in targets:
            for apply in (False,True):
                result = cli(db_arg,report,apply)
                assert result.returncode == 2,(db_arg,report,apply,result.stderr)
                checks += 1
    assert all(Path(p).read_bytes() == raw for p,raw in before.items())
    assert not Path(str(db)+'-journal').exists()
    assert c.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
    c.close()
    print('G1_FIXED',checks,'CLI refusals; DB/WAL/SHM bytes preserved')

# G2: an existing partial report survives; a new report completes the remaining work.
with tempfile.TemporaryDirectory(prefix='kzph-v6289-resume-') as td:
    db = Path(td)/'copy.db'
    report = Path(td)/'undo-first.json'
    report2 = Path(td)/'undo-continued.json'
    c = fixture(db,n=450)
    c.execute("CREATE TRIGGER stop_second BEFORE UPDATE ON canonical_products WHEN OLD.id='p200' BEGIN SELECT RAISE(ABORT,'synthetic stop'); END")
    c.commit()
    c.close()
    first = cli(db,report,True)
    first_bytes = report.read_bytes()
    partial = json.loads(first_bytes)
    assert first.returncode == 4 and partial['status'] == 'partial'
    assert len(partial['models']) == 450 and len(partial['applied']['models']) == 200
    c = sqlite3.connect(db)
    c.execute('DROP TRIGGER stop_second')
    c.commit()
    c.close()
    refused = cli(db,report,True)
    assert refused.returncode == 2 and report.read_bytes() == first_bytes
    second = cli(db,report2,True)
    final = json.loads(report2.read_text())
    assert second.returncode == 0 and final['status'] == 'applied'
    assert len(final['applied']['models']) == 250
    assert report.read_bytes() == first_bytes
    old_values = {x['id']:x['old'] for x in partial['models']}
    old_values.update({x['id']:x['old'] for x in final['models']})
    applied = set(partial['applied']['models']) | set(final['applied']['models'])
    assert len(old_values) == 450 and len(applied) == 450
    c = sqlite3.connect(db)
    actual = {r[0] for r in c.execute("SELECT id FROM canonical_products WHERE model='Radeon RX 9070 GRE'")}
    assert actual == applied == set(old_values)
    # Independent rollback of model values, using only the preserved reports.
    c.executemany('UPDATE canonical_products SET model=? WHERE id=?',[(old_values[pid],pid) for pid in applied])
    c.commit()
    assert c.execute("SELECT count(*) FROM canonical_products WHERE model='Radeon RX 9070'").fetchone()[0] == 450
    c.close()
    print('G2_FIXED','partial 200 + continuation 250; first report unchanged; old values and applied IDs 450/450; model rollback verified')
