"""Independent acceptance probes. Temporary DATA_DIR; no network or running services."""
from tests import test_support  # noqa: F401
import asyncio
import datetime
import json
import time
import unittest
from unittest.mock import patch

import config
import database
import web.server as server


class MonitorDecisionProbe(unittest.IsolatedAsyncioTestCase):
    async def check_tick(self, *, due, busy=False):
        calls = []
        ticks = 0
        now = 1800000000.0
        interval = server.get_wave_interval_seconds({}, server.get_wave_plan()['total_waves'])
        last = now - interval - 1 if due else now - 30

        async def one_tick(seconds):
            nonlocal ticks
            ticks += 1
            if ticks > 1:
                raise asyncio.CancelledError

        async def db_call(fn, *args):
            return fn(*args)

        def catchup(*args):
            calls.append(('plan_catchup',))
            return {'kari': ['source-only']}

        def spawn(*args, **kwargs):
            calls.append(('spawn', args, kwargs))

        import environment
        with patch.object(server.asyncio, 'sleep', one_tick), \
             patch.object(server, 'run_db', db_call), \
             patch.object(server, 'load_settings', return_value={}), \
             patch.object(server, 'enabled_shop_keys', return_value=['kari']), \
             patch.object(server, 'get_metadata', return_value=str(last)), \
             patch.object(server, '_catchup_sources', side_effect=catchup), \
             patch.object(server, 'apply_adaptive_order', side_effect=lambda k: (k, '')), \
             patch.object(server, 'spawn_scan', side_effect=spawn), \
             patch.object(environment, 'heartbeat'), \
             patch.object(server.time, 'time', return_value=now), \
             patch.dict(server.scan_state, {'is_running': busy}):
            await server.auto_scan_background_worker(None)
        return calls

    async def test_due_wave_has_priority_over_catchup(self):
        calls = await self.check_tick(due=True)
        self.assertEqual(calls, [('spawn', (['kari'],), {'scan_type': 'auto'})])

    async def test_between_waves_only_source_plan_is_forwarded(self):
        calls = await self.check_tick(due=False)
        self.assertEqual(calls, [('plan_catchup',), ('spawn', (['kari'],),
                         {'scan_type': 'auto', 'catchup_sources': {'kari': ['source-only']}})])

    async def test_busy_scan_prevents_both_launch_paths(self):
        self.assertEqual(await self.check_tick(due=True, busy=True), [])


class BackoffIntegrationProbe(unittest.TestCase):
    def test_real_shop_backoff_and_latest_source_are_respected(self):
        database.init_db()
        key = 'kari'
        url = server.SHOP_REGISTRY[key][1][0]['url']
        now = 1800000000.0
        intervals = []
        settings = {'enabled_categories': None}
        for expected_wait in [600, 600, 1200, 2400, 3600]:
            stamp = datetime.datetime.fromtimestamp(now, datetime.timezone.utc).isoformat()
            with database.get_connection() as conn:
                conn.execute('INSERT INTO source_scans (shop_key, source_url, started_at, finished_at, kind, quality) VALUES (?,?,?,?,?,?)',
                             (key, url, stamp, stamp, 'limited', 'failed'))
                conn.commit()
            with patch('time.time', return_value=now):
                database.record_shop_scan_result(key, 0, 1, 'synthetic failure', 'failed')
            # Isolate one configured source; use actual DB readers and actual catch-up selector.
            registry = {key: (object, [{'url': url, 'master': 'household'}], 'Kari')}
            with patch.dict(server.SHOP_REGISTRY, registry, clear=True):
                with patch('time.time', return_value=now + expected_wait - 1):
                    self.assertEqual(server._catchup_sources([key], settings, 9600), {})
                with patch('time.time', return_value=now + expected_wait):
                    self.assertEqual(server._catchup_sources([key], settings, 9600), {key: [url]})
            intervals.append(expected_wait)
            now += expected_wait
        print('INDEPENDENT_BACKOFF_SECONDS=' + json.dumps(intervals))


class FortePaginationProbe(unittest.TestCase):
    def test_two_pages_keep_both_cities_and_late_parse_failure_blocks_complete(self):
        from scrapers.fortemarket import ForteMarketScraper
        from scrapers.base import ScanResult
        from offer_identity import assign_offer_ids
        from data_quality import dedupe
        hits = [{'objectID': str(i), 'Name': 'Synthetic phone ' + str(i), 'Price': 120000,
                 'Locations': {'Location': [{'ID': 'KZ-AST', 'Price': 100000},
                                            {'ID': 'KZ-ALA', 'Price': 90000}]}}
                for i in range(1, 102)]
        for bad_tail in (False, True):
            pages = {1: {'hits': hits[:100], 'nbHits': 101},
                     2: {'hits': hits[100:] + ([None] if bad_tail else []), 'nbHits': 101}}
            requested = []
            def fetch(_self, category, url, page):
                requested.append(page)
                return ForteMarketScraper.parse_response(pages[page], category, page,
                                                          cities=('Астана', 'Алматы'))
            with patch.object(ForteMarketScraper, '_fetch_page_with_retry', fetch), patch('time.sleep'):
                result = ForteMarketScraper()._scrape_sync('Synthetic', 'https://example.com/catalog', 5)
            assign_offer_ids(result)
            self.assertEqual(requested, [1, 2])
            self.assertEqual(len(dedupe(result)), 202)
            self.assertEqual(sum(p['city'] == 'Астана' for p in result), 101)
            self.assertEqual(sum(p['city'] == 'Алматы' for p in result), 101)
            self.assertEqual(result.complete, not bad_tail)
            self.assertEqual(result.limited, bad_tail)
            self.assertEqual(result.unparsed, int(bad_tail))


if __name__ == '__main__':
    unittest.main()
