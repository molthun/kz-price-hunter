"""Configured categories affect current quality; archived sources remain historical."""
from tests import test_support  # noqa: F401
import datetime
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import config
import database
import data_quality as dq
import monitoring
import service_monitor
import source_failure as sf

class ActiveSourceScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();root=Path(self.tmp.name)
        self.patches=[patch.object(database,'DB_PATH',root/'prices.db'),patch.object(config,'DATA_DIR',root),patch.object(config,'SETTINGS_FILE',root/'settings.json')]
        for p in self.patches:p.start()
        database.init_db();self.now=datetime.datetime.now(datetime.timezone.utc);self.active='https://example.com/active';self.old='https://example.com/removed'
        self.registry={'fixture':('Fixture',1)};self.scope={'fixture':{self.active}}
    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()
    def row(self,url,quality='ok',valid=100,baseline=100,reason='',kind='complete'):
        with database.get_connection() as c:
            c.execute('''INSERT INTO source_scans(shop_key,source_url,category,scan_id,started_at,finished_at,kind,quality,reason,received,valid,baseline,accepted) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',('fixture',url,'Fixture',url,self.now.isoformat(),self.now.isoformat(),kind,quality,reason,valid,valid,baseline,1 if quality=='ok' else 0));c.commit()
    def scan(self,error=None,status='complete'):
        database.record_shop_scan_result('fixture',100,1,error,status)
    def overview(self):return monitoring.shops_overview(self.registry,['fixture'],now=self.now,active_sources=self.scope)[0]
    def test_removed_failures_do_not_inflate_current_quality_or_leak_cause(self):
        self.row(self.active)
        for i in range(4):self.row(self.old+str(i),'failed',0,100,sf.describe(sf.make('http_429',phase='fetch',page=1,http_status=429)))
        self.scan()
        legacy=monitoring.shops_overview(self.registry,['fixture'],now=self.now)[0]
        self.assertEqual(legacy['categories_observed'],5);self.assertEqual(legacy['status'],'degraded')
        current=self.overview();self.assertEqual(current['categories_observed'],1);self.assertEqual(current['categories_problem'],0)
        self.assertEqual(current['historical_categories_observed'],4);self.assertEqual(current['baseline_items'],100);self.assertEqual(current['status'],'healthy')
        self.assertIsNone(current['failure'])
        with database.get_connection() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM source_scans').fetchone()[0],5)
    def test_active_failure_is_not_hidden(self):
        failure=sf.make('timeout',phase='fetch',page=2)
        self.row(self.active,'failed',0,100,sf.describe(failure),'limited');self.row(self.old,'ok',100,100)
        self.scan('real failure','partial');current=self.overview()
        self.assertEqual(current['status'],'limited');self.assertEqual(current['categories_problem'],1)
        self.assertEqual(current['failure']['error_code'],'timeout')
    def test_replaced_or_unobserved_category_is_unknown_never_healthy(self):
        self.row(self.old);self.scan()
        current=self.overview();self.assertEqual(current['status'],'unknown');self.assertEqual(current['categories_observed'],0)
        self.assertEqual(current['quality'],'unknown');self.assertEqual(current['historical_categories_observed'],1)
        self.scope['fixture'].add('https://example.com/not-observed');self.row(self.active)
        self.assertEqual(self.overview()['status'],'unknown')
    def test_disabled_shop_and_empty_explicit_scope_stay_honest(self):
        self.row(self.old);self.scan()
        self.assertEqual(monitoring.shops_overview(self.registry,[],active_sources=self.scope)[0]['status'],'disabled')
        self.assertEqual(monitoring.shops_overview(self.registry,['fixture'],active_sources={})[0]['status'],'unknown')
    def test_last_failed_shop_scan_is_not_rewritten_into_success(self):
        self.row(self.active);self.row(self.old,'failed',0,100);self.scan('old aggregate','failed')
        current=self.overview();self.assertNotEqual(current['status'],'healthy')
        self.assertEqual(database.get_shop_scans()['fixture']['status'],'failed')
    def test_detail_excludes_removed_categories_but_keeps_history(self):
        self.row(self.active);self.row(self.old,'failed',0,100);self.scan()
        detail=monitoring.shop_detail('fixture','Fixture',[{'name':'Active','url':self.active}],True,now=self.now)
        self.assertEqual([r['source_url'] for r in detail['categories']],[self.active])
        self.assertEqual(detail['historical_categories_observed'],1);self.assertEqual(len(detail['history']),2)
        self.assertEqual(detail['last_quality']['quality'],'ok')
    def test_new_service_view_and_assistant_use_configured_scope(self):
        self.row(self.active);self.row(self.old,'failed',0,100);self.scan()
        current=service_monitor.shops(self.registry,['fixture'],self.scope)['items'][0]
        self.assertEqual(current['historical_categories_observed'],1)
        from web import server
        from admin_assistant import _shops
        fake_registry={'fixture':(object,[{'name':'Active','url':self.active}],'Fixture')}
        with patch.object(server,'SHOP_REGISTRY',fake_registry),patch.object(server,'enabled_shop_keys',return_value=['fixture']):
            data=_shops(now=self.now)
        self.assertEqual(data['shops'][0]['status'],'healthy')
