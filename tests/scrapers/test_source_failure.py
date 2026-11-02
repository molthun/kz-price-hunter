from tests import test_support  # noqa: F401
import asyncio
import json
import unittest
from unittest.mock import Mock, patch

import source_failure as sf
from scrapers.base import PagedScraper, ScanResult, UnconfirmedEnd
from scrapers import http

SECRET='fixture-private-secret'

class SourceFailureTests(unittest.TestCase):
    def test_network_http_parse_and_unknown_allowlisted_no_raw_exception(self):
        cases=[(type('DNSError',(RuntimeError,),{}),'dns'),(TimeoutError,'timeout'),(type('SSLError',(RuntimeError,),{}),'tls'),(ConnectionError,'connection'),(ValueError,'parse'),(RuntimeError,'unknown')]
        for cls,code in cases:
            with self.subTest(code=code):
                info=sf.classify(cls(SECRET+' https://example.com/?token='+SECRET),page=2)
                self.assertEqual(info['error_code'],code);self.assertEqual(info['page'],2)
                self.assertNotIn(SECRET,json.dumps([info,sf.describe(info),sf.public(info)]))
        for status,code in [(403,'http_403'),(404,'http_404'),(429,'http_429'),(503,'http_5xx'),(400,'http_other')]:
            with self.subTest(status=status):
                info=sf.classify(RuntimeError(f'HTTP {status} '+SECRET),page=1)
                self.assertEqual(info['error_code'],code);self.assertEqual(info['http_status'],status)
                self.assertNotIn(SECRET,sf.describe(info))
    def test_chained_and_browser_network_causes_survive_wrapping(self):
        cause=TimeoutError(SECRET);wrapped=RuntimeError(SECRET);wrapped.__cause__=cause
        self.assertEqual(sf.classify(wrapped)['error_code'],'timeout')
        self.assertEqual(sf.classify(RuntimeError('Page.goto: net::ERR_NAME_NOT_RESOLVED '+SECRET))['error_code'],'dns')
        self.assertEqual(sf.classify(RuntimeError('net::ERR_CERT_AUTHORITY_INVALID '+SECRET))['error_code'],'tls')
        wrapped.__cause__=wrapped
        self.assertEqual(sf.classify(wrapped)['error_code'],'unknown')
    def test_metadata_validation_and_roundtrip_do_not_import_extra_fields(self):
        info=sf.clean({'error_code':['bad'],'phase':{},'page':True,'http_status':'403','token':SECRET})
        self.assertEqual(info,sf.make())
        good=sf.make('http_429',phase='fetch',page=3,http_status=429)
        self.assertEqual(sf.from_legacy('category: '+sf.describe(good)),good)
        self.assertIsNone(sf.from_legacy('RuntimeError '+SECRET))
        self.assertIsNone(sf.from_legacy('SC02[code=invalid;phase=fetch;page=1;http=403] '+SECRET))
        self.assertIsNone(sf.from_legacy('SC02[code=dns;phase=fetch;page=999999999;http=-]'))
    def test_capture_observes_real_transport_without_changing_exception_or_retry_policy(self):
        http.reset_limiter()
        response=Mock(status_code=429,headers={'Retry-After':'120'},content=b'')
        with sf.capture() as state,patch.object(http._curl,'get',return_value=response) as network:
            self.assertIs(http.get('https://failure-fixture.example/'),response)
            self.assertEqual(state['http_status'],429)
            with self.assertRaises(http.HostCooldown):http.get('https://failure-fixture.example/')
            self.assertEqual(state['failure']['error_code'],'cooldown');self.assertEqual(network.call_count,1)
        http.reset_limiter()
        error=TimeoutError(SECRET)
        with sf.capture() as state,patch.object(http._curl,'get',side_effect=error):
            with self.assertRaises(TimeoutError) as caught:http.get('https://failure-fixture.example/')
            self.assertIs(caught.exception,error);self.assertEqual(state['failure']['error_code'],'timeout')
            sf.observe_http(200);self.assertIsNone(state['failure'])
    def test_legacy_results_are_not_raw_evidence_and_partial_remains_partial(self):
        result=ScanResult([{'id':'1'}],error=SECRET)
        self.assertEqual(sf.result_failure(result)['error_code'],'unknown')
        self.assertEqual(sf.result_failure(ScanResult(error=SECRET),{'http_status':429})['error_code'],'http_429')
        self.assertEqual(sf.result_failure(ScanResult(),{'failure':sf.make('dns')})['error_code'],'dns')
        limited=ScanResult([{'id':'1'}],limited=True,error_info=sf.make('end_unconfirmed',page=2))
        self.assertIsNone(limited.error);self.assertFalse(limited.complete)
    def test_paged_failure_preserves_successful_first_page_and_never_completes(self):
        class Fixture(PagedScraper):
            PAGE_DELAY_SECONDS=0;RETRY_ATTEMPTS=1
            def _fetch_page(self,*args):
                if args[-1]==1:return [{'id':'1','title':'Fixture','price':1000,'url':'https://example.com/1'}]
                raise RuntimeError('HTTP 429 '+SECRET)
        with patch.object(Fixture,'_report_scrape'):
            result=Fixture()._scrape_sync('fixture','https://example.com/',max_pages=2)
        self.assertEqual(len(result),1);self.assertFalse(result.complete)
        self.assertEqual(result.error_info,sf.make('http_429',phase='fetch',page=2,http_status=429))
        self.assertNotIn(SECRET,json.dumps(result.error_info))
    def test_unconfirmed_end_is_limited_after_data_and_failure_on_first_page(self):
        class Fixture(PagedScraper):
            PAGE_DELAY_SECONDS=0
            def _fetch_page(self,*args):
                if args[-1]==1:return [{'id':'1'}]
                raise UnconfirmedEnd(SECRET)
        with patch.object(Fixture,'_report_scrape'):
            result=Fixture()._scrape_sync('fixture','https://example.com/',max_pages=2)
        self.assertIsNone(result.error);self.assertTrue(result.limited);self.assertFalse(result.complete)
        self.assertEqual(result.error_info['error_code'],'end_unconfirmed')

class FailureContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_and_thread_contexts_do_not_mix_categories(self):
        async def task(status):
            with sf.capture() as state:
                await asyncio.to_thread(sf.observe_http,status)
                await asyncio.sleep(0)
                return state['http_status']
        self.assertEqual(await asyncio.gather(task(403),task(503)),[403,503])
