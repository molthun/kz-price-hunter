import sqlite3, sys, time, os
src, dst = sys.argv[1], sys.argv[2]
t = time.time()
s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
if os.path.exists(dst): os.remove(dst)
d = sqlite3.connect(dst)
s.backup(d)  # один снимок WAL, писателей не блокирует
d.execute("PRAGMA journal_mode=DELETE")
qc = d.execute("PRAGMA quick_check").fetchone()[0]
n = {t_: d.execute(f"select count(*) from {t_}").fetchone()[0] for t_ in ("users","offers","products","offer_price_history")}
d.close(); s.close()
print(os.path.basename(dst), "secs", round(time.time()-t), "MB", os.path.getsize(dst)//1000000, "quick_check", qc, n, flush=True)
