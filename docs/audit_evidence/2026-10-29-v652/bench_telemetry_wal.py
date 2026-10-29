"""Сколько пишет сброс телеметрии: прирост WAL за 10 сбросов, 20 хостов, выборки заполнены (500)."""
import os, sqlite3, sys, tempfile
from pathlib import Path
sys.path.insert(0, os.getcwd())
os.environ["DATA_DIR"] = tempfile.mkdtemp()
import database, telemetry as tm
db = Path(tempfile.mkdtemp()) / "prices.db"
database.DB_PATH = db
database.ensure_journal_mode()
with database.get_connection() as c:
    database._create_schema(c.cursor()); c.commit()
hold = sqlite3.connect(db); hold.execute("PRAGMA wal_autocheckpoint=0")
t = tm.TelemetryService()
def wave(n):
    for h in range(20):
        for i in range(n):
            t.record_http_metric(f"shop{h}.example", "GET", 200, 50.0 + (i * 7) % 900, shop=f"s{h}")
    t.flush()
for _ in range(6):
    wave(200)  # заполнить выборки minute/hour/day
hold.execute("PRAGMA wal_checkpoint(TRUNCATE)")
before = os.path.getsize(str(db) + "-wal") if os.path.exists(str(db) + "-wal") else 0
for _ in range(10):
    wave(50)
after = os.path.getsize(str(db) + "-wal")
rows = hold.execute("SELECT (SELECT count(*) FROM telemetry_http_samples), (SELECT max(rowid) FROM telemetry_http_samples)").fetchone()
print(f"WAL за 10 сбросов: {(after - before) / 1048576:.1f} МБ; строк выборок {rows[0]}, max(rowid) {rows[1]}")
