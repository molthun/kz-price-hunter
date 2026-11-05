"""Independent negative probes; all database files are disposable synthetic fixtures."""
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

from catalog_classifier import canonical_gpu_model
from domain.attribute_indexing import normalize_attribute_value
from domain.catalog_enrichment_ai import DDL_ATTRIBUTES
from scripts.recompute_gpu_models import plan_changes, apply_changes

SCRIPT = str(Path('scripts/recompute_gpu_models.py').resolve())


def fixture(path):
    c = sqlite3.connect(path)
    c.executescript('CREATE TABLE canonical_products(id TEXT PRIMARY KEY,title TEXT,model TEXT,category TEXT,updated_at TEXT);')
    c.executescript(DDL_ATTRIBUTES)
    for pid,title in [('base','Видеокарта RX 9070'),('gre','Видеокарта RX 9070 GRE')]:
        c.execute('INSERT INTO canonical_products VALUES(?,?,?,?,?)',(pid,title,'Radeon RX 9070','gpus','now'))
        c.execute('INSERT INTO product_search_attributes VALUES(?,?,?,?,?,?)',
                  (pid,'chipset','Radeon RX 9070','Название товара',title,'now'))
    c.commit()
    return c


def cli(db,report,apply=False):
    args = [sys.executable,SCRIPT,'--db',str(db),'--report',str(report)]
    if apply:
        args.append('--apply')
    return subprocess.run(args,capture_output=True,text=True)


assert canonical_gpu_model('Видеокарта RX 9070 GRE') == 'Radeon RX 9070 GRE'
assert normalize_attribute_value('chipset','RX 9070 GRE') == 'Radeon RX 9070 GRE'
print('POSITIVE_GRE_PARSE_FIXED')

# F1a: valid dry run is read-only; a DB/report path collision silently destroys SQLite.
with tempfile.TemporaryDirectory(prefix='kzph-v6287-dry-') as td:
    db = Path(td)/'copy.db'
    fixture(db).close()
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    safe = cli(db,Path(td)/'plan.json')
    assert safe.returncode == 0
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    unsafe = cli(db,db)
    assert unsafe.returncode == 0
    assert json.loads(db.read_text())['applied'] is False
    c = sqlite3.connect(db)
    try:
        c.execute('PRAGMA quick_check').fetchone()
    except sqlite3.DatabaseError as ex:
        assert 'not a database' in str(ex)
        print('F1_DRY_RUN_OVERWRITES_DB',unsafe.returncode,str(ex))
    else:
        raise AssertionError('Expected destroyed SQLite fixture')
    finally:
        c.close()

# F1b: an invalid report destination is detected after the changes have committed.
with tempfile.TemporaryDirectory(prefix='kzph-v6287-report-') as td:
    db = Path(td)/'copy.db'
    fixture(db).close()
    report = Path(td)/'missing'/'undo.json'
    failed = cli(db,report,apply=True)
    c = sqlite3.connect(db)
    model = c.execute("SELECT model FROM canonical_products WHERE id='gre'").fetchone()[0]
    c.close()
    assert failed.returncode != 0 and not report.exists() and model == 'Radeon RX 9070 GRE'
    print('F1_REPORT_FAILURE_AFTER_COMMIT',failed.returncode,model,'report_exists',report.exists())

# F2: the regex accepts compact TiSuper while its dictionary does not.
for title in ['RX 9070 TiSuper','RTX 4060 TiSuper','GTX 1650 TiSuper']:
    try:
        canonical_gpu_model(title)
    except KeyError as ex:
        assert ex.args == ('tisuper',)
        print('F2_SUFFIX_KEYERROR',title,str(ex))
    else:
        raise AssertionError(title)

# F3: valid source-bound chipset derived from the old title parser is left inconsistent.
with tempfile.TemporaryDirectory(prefix='kzph-v6287-chipset-') as td:
    c = fixture(Path(td)/'copy.db')
    plan = plan_changes(c)
    assert [x['id'] for x in plan['models']] == ['gre'] and plan['chipsets'] == []
    apply_changes(c,plan)
    actual = c.execute("SELECT p.id FROM canonical_products p JOIN product_search_attributes pa ON pa.product_id=p.id WHERE pa.key='chipset' AND pa.value='Radeon RX 9070' AND pa.source_title=p.title ORDER BY p.id").fetchall()
    assert [r[0] for r in actual] == ['base','gre']
    assert c.execute("SELECT model FROM canonical_products WHERE id='gre'").fetchone()[0] == 'Radeon RX 9070 GRE'
    print('F3_OLD_CHIPSET_FILTER_MATCHES_GRE',[r[0] for r in actual])
    c.close()
