"""Independent CLI acceptance and remaining failure probes on disposable SQLite files."""
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

# G1: a symlink DB name causes side-file validation to inspect the wrong WAL name.
with tempfile.TemporaryDirectory(prefix='kzph-v6288-wal-') as td:
    db = Path(td)/'actual.db'
    alias = Path(td)/'alias.db'
    alias.symlink_to(db)
    c = fixture(db,wal=True)
    wal = Path(str(db)+'-wal')
    before = wal.read_bytes()
    assert before[:4] in (bytes.fromhex('377f0682'),bytes.fromhex('377f0683'))
    result = cli(alias,wal)
    after = wal.read_bytes()
    assert result.returncode == 0 and after.startswith(b'{')
    assert json.loads(after)['status'] == 'planned'
    print('G1_ACTIVE_WAL_REPLACED_BY_DRY_RUN',result.returncode,'before_magic',before[:4].hex(),'after_magic',after[:4].hex())
    c.close()

# G2: resume finishes the data but replaces, rather than retains, first-batch undo history.
with tempfile.TemporaryDirectory(prefix='kzph-v6288-resume-') as td:
    db = Path(td)/'copy.db'
    report = Path(td)/'undo.json'
    c = fixture(db,n=450)
    c.execute("CREATE TRIGGER stop_second BEFORE UPDATE ON canonical_products WHEN OLD.id='p200' BEGIN SELECT RAISE(ABORT,'synthetic stop'); END")
    c.commit()
    c.close()
    first = cli(db,report,True)
    partial = json.loads(report.read_text())
    assert first.returncode == 4 and partial['status'] == 'partial'
    assert len(partial['models']) == 450 and len(partial['applied']['models']) == 200
    c = sqlite3.connect(db)
    c.execute('DROP TRIGGER stop_second')
    c.commit()
    c.close()
    second = cli(db,report,True)
    final = json.loads(report.read_text())
    assert second.returncode == 0 and final['status'] == 'applied'
    assert len(final['models']) == 250 and len(final['applied']['models']) == 250
    missing = set(partial['applied']['models']) - {x['id'] for x in final['models']}
    assert len(missing) == 200 and len(list(Path(td).glob('*.json'))) == 1
    c = sqlite3.connect(db)
    assert c.execute("SELECT count(*) FROM canonical_products WHERE model='Radeon RX 9070 GRE'").fetchone()[0] == 450
    c.close()
    print('G2_FIRST_BATCH_UNDO_LOST',len(missing),'final_plan',len(final['models']),'total_updated',450)
