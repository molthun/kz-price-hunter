"""
Tests for Search Quality & Search Analytics (S2-E13, KZ Price Hunter 2.0).
Verifies:
1. Query normalization and permutation invariance.
2. Anonymized SearchEvent persistence in SQLite search_events table.
3. Search Success Rate (SSR) KPI computation and outcome breakdowns (FOUND, WEAK_MATCH, NOT_FOUND, ERROR).
4. Query demand signals generation for Adaptive Discovery (S2-E14).
5. End-to-end telemetry recording in /api/v2/search and /api/v2/analytics/search-quality endpoint.
"""

from tests import test_support  # must be first
import datetime
import json
import sqlite3
import unittest
from aiohttp.test_utils import make_mocked_request

from domain.search_analytics import (
    QueryDemandSignal,
    SearchEvent,
    SearchQualitySummary,
    normalize_search_query,
)
from domain.search_core import SearchOutcome
from repositories import (
    SearchAnalyticsRepository,
    init_schema_v2,
)


class TestSearchAnalyticsDomain(unittest.TestCase):
    def test_query_normalization(self):
        # Permutation invariance & punctuation removal
        self.assertEqual(
            normalize_search_query("iPhone 16 Pro!"),
            normalize_search_query("pro 16 iphone")
        )
        self.assertEqual(
            normalize_search_query("  ноутбук, asus   rog "),
            "asus rog ноутбук"
        )
        self.assertEqual(normalize_search_query(""), "")
        self.assertEqual(normalize_search_query("   "), "")

    def test_search_event_post_init(self):
        event = SearchEvent(query="RTX 5090 Астана", outcome=SearchOutcome.FOUND)
        self.assertIsNotNone(event.id)
        self.assertIsNotNone(event.created_at)
        self.assertEqual(event.normalized_query, "5090 rtx астана")
        d = event.to_dict()
        self.assertEqual(d["query"], "RTX 5090 Астана")
        self.assertEqual(d["outcome"], SearchOutcome.FOUND)

    def test_sensitive_data_redaction(self):
        # Privacy protection (C-E13-01)
        ev_phone = SearchEvent(query="+7 777 123 45 67")
        self.assertEqual(ev_phone.query, "[redacted]")
        self.assertEqual(ev_phone.normalized_query, "[redacted]")

        ev_email = SearchEvent(query="customer@example.kz")
        self.assertEqual(ev_email.query, "[redacted]")

        ev_num = SearchEvent(query="990101123456")
        self.assertEqual(ev_num.query, "[redacted]")


class TestSearchAnalyticsRepository(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_schema_v2(self.conn)
        self.repo = SearchAnalyticsRepository(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_record_and_count_events(self):
        self.assertEqual(self.repo.count_events(), 0)

        ev1 = SearchEvent(query="iphone 16", outcome=SearchOutcome.FOUND, total_products=3, latency_ms=12.5)
        ev2 = SearchEvent(query="космический корабль", outcome=SearchOutcome.NOT_FOUND, total_products=0, latency_ms=4.1)

        self.repo.record_event(ev1)
        self.repo.record_event(ev2)

        self.assertEqual(self.repo.count_events(), 2)

    def test_quality_summary_and_ssr_kpi(self):
        # 10 searches: 6 FOUND, 2 WEAK_MATCH, 1 NOT_FOUND, 1 ERROR
        # Harmonized P06 formula: answered = 6 + 2 + 1 = 9. SSR = 6 / 9 * 100 = 66.67%, error_rate = 1 / 10 * 100 = 10.0%
        for _ in range(6):
            self.repo.record_event(SearchEvent(query="iphone", outcome=SearchOutcome.FOUND, latency_ms=10.0))
        for _ in range(2):
            self.repo.record_event(SearchEvent(query="чехол", outcome=SearchOutcome.WEAK_MATCH, latency_ms=15.0))
        self.repo.record_event(SearchEvent(query="unknown", outcome=SearchOutcome.NOT_FOUND, latency_ms=5.0))
        self.repo.record_event(SearchEvent(query="crash", outcome=SearchOutcome.ERROR, latency_ms=2.0))

        summary: SearchQualitySummary = self.repo.get_quality_summary()
        self.assertEqual(summary.total_searches, 10)
        self.assertEqual(summary.found_count, 6)
        self.assertEqual(summary.weak_match_count, 2)
        self.assertEqual(summary.not_found_count, 1)
        self.assertEqual(summary.error_count, 1)
        self.assertEqual(summary.search_success_rate, 66.67)
        self.assertEqual(summary.error_rate, 10.0)
        self.assertGreater(summary.avg_latency_ms, 0.0)

    def test_summary_city_and_date_filtering(self):
        self.repo.record_event(SearchEvent(query="q1", city="Астана", outcome=SearchOutcome.FOUND, created_at="2026-09-25T10:00:00"))
        self.repo.record_event(SearchEvent(query="q2", city="Алматы", outcome=SearchOutcome.NOT_FOUND, created_at="2026-09-25T11:00:00"))
        self.repo.record_event(SearchEvent(query="q3", city="Астана", outcome=SearchOutcome.FOUND, created_at="2026-09-26T12:00:00"))

        # City filter
        sum_astana = self.repo.get_quality_summary(city="Астана")
        self.assertEqual(sum_astana.total_searches, 2)
        self.assertEqual(sum_astana.search_success_rate, 100.0)

        sum_almaty = self.repo.get_quality_summary(city="Алматы")
        self.assertEqual(sum_almaty.total_searches, 1)
        self.assertEqual(sum_almaty.search_success_rate, 0.0)

        # Date filter
        sum_since = self.repo.get_quality_summary(since_iso="2026-09-26T00:00:00")
        self.assertEqual(sum_since.total_searches, 1)
        self.assertEqual(sum_since.found_count, 1)

    # Правило приватности P06: текст запроса не хранится до третьего обращения, поэтому первые
    # два события любого запроса записываются обезличенно и в агрегаты по тексту не попадают.
    # «Прогрев» ниже доводит каждый запрос до порога, чтобы проверялась именно арифметика
    # агрегации, а не момент включения текста (для самого правила есть отдельный тест).
    def _warm_up(self, query, city=None):
        from domain.search_analytics import SearchEvent as _E
        for _ in range(2):
            self.repo.record_event(_E(query=query, city=city, outcome=SearchOutcome.FOUND))

    def test_top_queries_aggregation(self):
        # 3 searches for 'iphone 16' (permutations), 2 for 'macbook air'
        self._warm_up("iphone 16")
        self._warm_up("macbook air")
        self.repo.record_event(SearchEvent(query="iphone 16", outcome=SearchOutcome.FOUND))
        self.repo.record_event(SearchEvent(query="16 iphone", outcome=SearchOutcome.FOUND))
        self.repo.record_event(SearchEvent(query="IPHONE 16", outcome=SearchOutcome.NOT_FOUND))

        self.repo.record_event(SearchEvent(query="macbook air", outcome=SearchOutcome.FOUND))
        self.repo.record_event(SearchEvent(query="air macbook", outcome=SearchOutcome.FOUND))

        top = self.repo.get_top_queries(limit=10)
        self.assertEqual(len(top), 2)

        # iphone 16 has 3 searches, SSR = 66.7%
        self.assertEqual(top[0]["normalized_query"], "16 iphone")
        self.assertEqual(top[0]["search_count"], 3)
        self.assertEqual(top[0]["found_count"], 2)
        self.assertEqual(top[0]["success_rate"], 66.7)

        # macbook air has 2 searches, SSR = 100%
        self.assertEqual(top[1]["normalized_query"], "air macbook")
        self.assertEqual(top[1]["search_count"], 2)
        self.assertEqual(top[1]["success_rate"], 100.0)

    def test_discovery_signals_for_adaptive_discovery(self):
        """High demand (>= 5 searches) with low success rate (<= 70%) generates Discovery Signals."""
        # Query 1: 'суши сет астана' - 6 searches, 1 FOUND, 5 NOT_FOUND -> SSR = 16.7% -> Signal!
        self._warm_up("суши сет астана", "Астана")
        self._warm_up("iphone 16 pro", "Астана")
        self._warm_up("редкая деталь", "Астана")
        for _ in range(5):
            self.repo.record_event(SearchEvent(query="суши сет астана", city="Астана", outcome=SearchOutcome.NOT_FOUND))
        self.repo.record_event(SearchEvent(query="суши сет астана", city="Астана", outcome=SearchOutcome.FOUND))

        # Query 2: 'iphone 16 pro' - 10 searches, 10 FOUND -> SSR = 100% -> No signal needed
        for _ in range(10):
            self.repo.record_event(SearchEvent(query="iphone 16 pro", city="Астана", outcome=SearchOutcome.FOUND))

        # Query 3: 'редкая деталь' - 2 searches, 0 FOUND -> below min_searches threshold (5) -> No signal yet
        for _ in range(2):
            self.repo.record_event(SearchEvent(query="редкая деталь", city="Астана", outcome=SearchOutcome.NOT_FOUND))

        signals = self.repo.get_discovery_signals(min_searches=5, max_success_rate=70.0)
        self.assertEqual(len(signals), 1)
        sig: QueryDemandSignal = signals[0]
        self.assertIn("суши", sig.normalized_query)
        self.assertEqual(sig.search_count, 6)
        self.assertEqual(sig.success_rate, 16.7)
        self.assertTrue(sig.needs_discovery)

    def test_purge_old_events(self):
        # Insert old event (120 days ago) and fresh event
        old_date = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=120)).isoformat()
        self.repo.record_event(SearchEvent(query="old", outcome=SearchOutcome.FOUND, created_at=old_date))
        self.repo.record_event(SearchEvent(query="fresh", outcome=SearchOutcome.FOUND))

        self.assertEqual(self.repo.count_events(), 2)
        purged = self.repo.purge_old_events(days=90)
        self.assertEqual(purged, 1)
        self.assertEqual(self.repo.count_events(), 1)


class TestSearchQualityHttpEndpoints(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from database import init_db, get_connection
        import web.server as server
        self.server = server
        init_db()
        conn = get_connection()
        try:
            conn.execute("DELETE FROM search_events")
            conn.commit()
        finally:
            conn.close()

    async def test_search_v2_records_telemetry_event(self):
        from database import get_connection
        conn = get_connection()
        try:
            repo = SearchAnalyticsRepository(conn)
            initial_count = repo.count_events()

            req = make_mocked_request(
                "GET",
                "/api/v2/search?q=rtx+5090&city=Астана",
                headers={"Host": "localhost:8080"}
            )
            resp = await self.server.search_v2_handler(req)
            self.assertEqual(resp.status, 200)

            # Telemetry record must be written
            new_count = repo.count_events()
            self.assertEqual(new_count, initial_count + 1)
        finally:
            conn.close()

    async def test_analytics_search_quality_endpoint(self):
        from database import get_connection
        conn = get_connection()
        try:
            repo = SearchAnalyticsRepository(conn)
            # По правилу P06 первые два обращения к запросу хранятся обезличенно, поэтому в
            # агрегат по тексту попадают только начиная с третьего. Чтобы проверялся сам сигнал
            # спроса (порог 5 поисков), а не момент включения текста, обращений здесь семь:
            # пять из них несут текст. Само правило проверяется отдельным тестом.
            for _ in range(7):
                repo.record_event(SearchEvent(query="роллы филадельфия", city="Астана", outcome=SearchOutcome.NOT_FOUND))

            # 1. Unauthenticated request -> 401
            req_unauth = make_mocked_request(
                "GET",
                "/api/v2/analytics/search-quality?city=Астана",
                headers={"Host": "localhost:8080"}
            )
            resp_unauth = await self.server.search_quality_analytics_handler(req_unauth)
            self.assertEqual(resp_unauth.status, 401)

            # 2. Non-admin user -> 403
            req_user = make_mocked_request(
                "GET",
                "/api/v2/analytics/search-quality?city=Астана",
                headers={"Host": "localhost:8080"}
            )
            req_user["user"] = {"id": 999999, "username": "regular_user"}
            from unittest.mock import patch
            with patch("auth.is_admin", return_value=False):
                resp_forbidden = await self.server.search_quality_analytics_handler(req_user)
                self.assertEqual(resp_forbidden.status, 403)

            # 3. Admin user -> 200
            req_admin = make_mocked_request(
                "GET",
                "/api/v2/analytics/search-quality?city=Астана",
                headers={"Host": "localhost:8080"}
            )
            req_admin["user"] = {"id": 1, "username": "admin"}
            with patch("auth.is_admin", return_value=True):
                resp = await self.server.search_quality_analytics_handler(req_admin)
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.text)

            self.assertIn("summary", data)
            self.assertIn("top_queries", data)
            self.assertIn("discovery_signals", data)

            summary = data["summary"]
            self.assertEqual(summary["total_searches"], 7)
            self.assertEqual(summary["search_success_rate"], 0.0)

            signals = data["discovery_signals"]
            self.assertEqual(len(signals), 1)
            self.assertTrue(signals[0]["needs_discovery"])
            self.assertIn("филадельфия", signals[0]["normalized_query"])
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
