"""Concurrent admission with temporary data; no network."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
os.environ['DATA_DIR']=tempfile.mkdtemp(prefix='kzph-capacity-')
import database
from queue_service import ProductQueueManager
database.init_db()
original=database.spool_stats
barrier=threading.Barrier(2)
def concurrent_stats(*args):
    result=original(*args)
    barrier.wait(timeout=5)
    return result
async def main():
    qm=ProductQueueManager(maxsize=1)
    with patch.object(database,'spool_stats',side_effect=concurrent_stats):
        accepted=await asyncio.gather(*[qm.enqueue([{'id':str(n)}],'Synthetic',{},shop_key='same') for n in range(2)])
    return {'maxsize':1,'accepted':accepted,'pending':database.spool_stats('same')['pending']}
result=asyncio.run(main())
Path(__file__).with_name('capacity.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
