from tests import test_support
import json
from unittest.mock import patch
from tests.scheduler.test_catchup_plan import CatchupScanDoesNotShiftRotationTest
import web.server as server

original=server._scan_task_body
case=CatchupScanDoesNotShiftRotationTest()
async def manual(keys,cats,scan_type,catchup_sources=None):
    return await original(keys,cats,'manual',catchup_sources)
with patch.object(server,'_scan_task_body',manual):
    result=case._run_with_tail_spies(None)
assert result=={'tracked_queries':[('iphone','Астана')],'demand_polls':1},result
print('MANUAL_POSITIVE_CONTROL='+json.dumps(result,ensure_ascii=False))
