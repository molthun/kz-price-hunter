"""Safe failure metadata through real scan orchestration and temporary SQLite."""
from tests import test_support  # noqa: F401
import json
from unittest.mock import patch
from tests.monitoring.test_data_quality import PipelineCase
import database
import monitoring
import service_monitor
import source_failure as sf


class SourceFailurePipelineTests(PipelineCase):
    def test_failure_is_persisted_and_exposed_without_legacy_secret(self):
        result=self.result(1,complete=False,error='fixture-private-secret')
        result.error_info=sf.make('http_429',phase='fetch',page=2,http_status=429)
        from telemetry import telemetry
        with patch.object(telemetry,'record_event') as events:
            self.scan(result)
        row=self.sql('SELECT reason,quality FROM source_scans ORDER BY id DESC')[0]
        self.assertEqual(row[1],'failed');self.assertEqual(sf.from_legacy(row[0]),result.error_info)
        shop=database.get_shop_scans()['syn'];self.assertEqual(shop['status'],'partial')
        self.assertNotIn('fixture-private-secret',shop['last_error'])
        self.assertNotIn('fixture-private-secret',str(events.call_args_list))
        view=monitoring.shops_overview({'syn':('Синтетика',1)},['syn'])
        self.assertEqual(view[0]['failure']['http_status'],429)
        self.assertEqual(service_monitor.shops({'syn':('Синтетика',1)},['syn'])['items'][0]['failure']['page'],2)
        # First valid item is retained; error never grants retirement or baseline learning.
        self.assertEqual(self.active(),1)
        self.assertEqual(self.sql('SELECT accepted FROM source_scans ORDER BY id DESC')[0][0],0)

    def test_legacy_shop_error_is_unknown_and_never_reconstructed(self):
        database.record_shop_scan_result('syn',0,1,'RuntimeError fixture-private-secret','failed')
        view=service_monitor.shops({'syn':('Синтетика',1)},['syn'])
        self.assertIsNone(view['items'][0]['failure'])
        self.assertNotIn('fixture-private-secret',json.dumps(view))
