import json, sqlite3, tempfile
from domain.scale_bench import run_scale_benchmark,evaluate_storage_decision
with tempfile.TemporaryDirectory() as t:
 p=t+'/bench.db'
 cp=run_scale_benchmark(100000,db_path=p)
 c=sqlite3.connect(p)
 bad=c.execute('SELECT COUNT(*) FROM offers o JOIN channels c ON c.id=o.channel_id WHERE c.seller_id<>o.seller_id').fetchone()[0]
 try:
  sizes=dict(c.execute("SELECT name,SUM(pgsize) FROM dbstat GROUP BY name"))
 except sqlite3.Error as e:sizes={'unavailable':str(e)}
 print(json.dumps(dict(metrics=cp.to_dict(),decision=evaluate_storage_decision(cp).to_dict(),mismatched_seller_channels=bad,actual_table_bytes=sizes),indent=2,ensure_ascii=False))
