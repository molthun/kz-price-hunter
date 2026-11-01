from pathlib import Path
import json
import sqlite3
from content_retention import sanitized_backup, clear_descriptions, description_counts
from scripts.scrub_product_descriptions import protected_fingerprint
from scripts.scrub_social_content import scrub, _finish_physically

root=Path('/app/data').resolve()
main=root/'prices.db'
backup=root/'backups'/'before-lsa-3d54340-sanitized.db'
assert main.is_file() and not backup.exists()
with sqlite3.connect(main.as_uri()+'?mode=ro',uri=True) as source,sqlite3.connect(backup) as dest:
    sanitized_backup(source,dest)
    assert dest.execute('PRAGMA quick_check').fetchone()[0]=='ok'
    assert protected_fingerprint(source)==protected_fingerprint(dest)
    assert not any(description_counts(dest).values())
print(json.dumps({'sanitized_recovery_copy':backup.name,'quick_check':'ok','protected_data_unchanged':True}),flush=True)
files=sorted({*root.rglob('*.db'),*root.rglob('*.sqlite'),*root.rglob('*.sqlite3')})
for path in files:
    path=path.resolve(strict=True)
    assert path.is_relative_to(root)
    with sqlite3.connect(path.as_uri()+'?mode=rw',uri=True) as conn:
        before=description_counts(conn)
        protected=protected_fingerprint(conn)
        clear_descriptions(conn)
        social=0
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='content_observations'").fetchone():
            result=scrub(conn,apply=True)
            assert result.physical_complete,result.reason
            social=int(result)
        else:
            assert _finish_physically(conn)==''
        after=description_counts(conn)
        assert not any(after.values())
        assert protected_fingerprint(conn)==protected
        assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    print(json.dumps({'file':str(path.relative_to(root)),'before':before,'after':after,'social_rows_cleared':social,'protected_data_unchanged':True,'physical_cleanup':'complete'}),flush=True)
print(json.dumps({'complete':True,'databases':len(files)}),flush=True)
