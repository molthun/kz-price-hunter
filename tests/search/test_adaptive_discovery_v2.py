"""
Tests for Adaptive Discovery & Query Demand Generator (S2-E14, KZ Price Hunter 2.0).
Verifies:
1. SearchPatternGenerator for multi-channel discovery queries (Google, Kaspi, 2GIS, Instagram, Telegram).
2. Priority calculation based on demand volume and success rate deficit.
3. DiscoveryTaskRepository CRUD, query deduplication, status updates, and candidate linking.
4. End-to-end integration: SearchEvents -> Demand Signals -> Discovery Tasks.
5. Admin API endpoints: GET /api/v2/discovery/tasks, POST /api/v2/discovery/generate, POST /api/v2/discovery/tasks/{id}/resolve.
"""

from tests import test_support  # noqa: F401  изолирует DATA_DIR
import datetime
import json
import unittest
from unittest.mock import patch
from aiohttp.test_utils import TestClient, TestServer

import auth
from database import create_session, get_connection, init_db, upsert_telegram_user
from domain.adaptive_discovery import (
    AdaptiveDiscoveryEngine,
    DiscoveryTask,
    DiscoveryTaskStatus,
    SearchPatternGenerator,
    TargetSearchChannel,
)
from domain.discovery import CandidateStatus, DiscoverySource, SellerCandidate
from domain.search_analytics import QueryDemandSignal, SearchEvent
from domain.search_core import SearchOutcome
from repositories.candidate_repo import CandidateRepository
from repositories.discovery_task_repo import DiscoveryTaskRepository
from repositories.schema_v2 import init_schema_v2
from repositories.search_analytics_repo import SearchAnalyticsRepository
import web.server as server

NOW_ISO = datetime.datetime.now(datetime.timezone.utc).isoformat()


class TestSearchPatternGenerator(unittest.TestCase):
    """Tests search query generation across channels."""

    def test_pattern_generator_with_city(self):
        queries = SearchPatternGenerator.generate_queries("суши сет", city="Астана")
        channels = {q.channel for q in queries}
        self.assertIn(TargetSearchChannel.GOOGLE, channels)
        self.assertIn(TargetSearchChannel.KASPI, channels)
        self.assertIn(TargetSearchChannel.TWOGIS, channels)
        self.assertIn(TargetSearchChannel.INSTAGRAM, channels)
        self.assertIn(TargetSearchChannel.TELEGRAM, channels)

        # Check city presence in Google queries
        google_qs = [q.query for q in queries if q.channel == TargetSearchChannel.GOOGLE]
        self.assertTrue(any("Астана" in gq for gq in google_qs))

    def test_pattern_generator_without_city(self):
        queries = SearchPatternGenerator.generate_queries("iphone 16 pro", city=None)
        channels = {q.channel for q in queries}
        self.assertIn(TargetSearchChannel.GOOGLE, channels)
        self.assertIn(TargetSearchChannel.KASPI, channels)
        # 2GIS should be omitted when city is not specified
        self.assertNotIn(TargetSearchChannel.TWOGIS, channels)

    def test_pattern_generator_empty_query(self):
        self.assertEqual(SearchPatternGenerator.generate_queries(""), [])
        self.assertEqual(SearchPatternGenerator.generate_queries("   "), [])


class TestAdaptiveDiscoveryEngine(unittest.TestCase):
    """Tests priority scoring and task generation logic."""

    def test_priority_calculation(self):
        # 100 searches with 0% success -> priority = 100.0
        self.assertEqual(AdaptiveDiscoveryEngine.calculate_priority(100, 0.0), 100.0)

        # 100 searches with 50% success -> priority = 50.0
        self.assertEqual(AdaptiveDiscoveryEngine.calculate_priority(100, 50.0), 50.0)

        # 100 searches with 100% success -> priority = 0.0 (no deficit)
        self.assertEqual(AdaptiveDiscoveryEngine.calculate_priority(100, 100.0), 0.0)

        # 10 searches with 20% success -> priority = 10 * 0.8 = 8.0
        self.assertEqual(AdaptiveDiscoveryEngine.calculate_priority(10, 20.0), 8.0)

    def test_task_from_signal(self):
        sig = QueryDemandSignal(
            normalized_query="суши сет",
            sample_query="суши сет астана",
            search_count=50,
            success_rate=20.0,
            city="Астана",
            needs_discovery=True,
        )
        task = AdaptiveDiscoveryEngine.task_from_signal(sig)
        self.assertEqual(task.normalized_query, "суши сет")
        self.assertEqual(task.signal_query, "суши сет астана")
        self.assertEqual(task.city, "Астана")
        self.assertEqual(task.search_count, 50)
        self.assertEqual(task.priority_score, 40.0)  # 50 * 0.8
        self.assertEqual(task.status, DiscoveryTaskStatus.PENDING)
        self.assertTrue(len(task.search_queries) > 0)


class TestDiscoveryTaskRepository(unittest.TestCase):
    """Tests persistence, query deduplication, status transitions, and candidate linking."""

    def setUp(self):
        self.conn = get_connection()
        init_schema_v2(self.conn)
        self.conn.execute("DELETE FROM discovery_tasks")
        self.conn.execute("DELETE FROM seller_candidates")
        self.conn.commit()
        self.repo = DiscoveryTaskRepository(self.conn)
        self.cand_repo = CandidateRepository(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_crud_discovery_task(self):
        task = DiscoveryTask(
            id="dtask_test_1",
            signal_query="кофемашина de longhi",
            normalized_query="de delonghi кофемашина longhi",
            city="Алматы",
            search_count=25,
            success_rate=10.0,
            priority_score=22.5,
            search_queries=[{"channel": "google", "query": "купить кофемашину de longhi Алматы", "intent": "commercial"}],
        )
        saved = self.repo.save_task(task)
        self.assertEqual(saved.id, "dtask_test_1")

        fetched = self.repo.get_task_by_id("dtask_test_1")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.city, "Алматы")
        self.assertEqual(fetched.priority_score, 22.5)
        self.assertEqual(len(fetched.search_queries), 1)

    def test_deduplication_and_priority_update(self):
        sig1 = QueryDemandSignal(
            normalized_query="робот пылесос",
            sample_query="робот пылесос xiaomi",
            search_count=10,
            success_rate=30.0,
            city="Астана",
            needs_discovery=True,
        )
        tasks = self.repo.generate_tasks_from_signals([sig1])
        self.assertEqual(len(tasks), 1)
        orig_id = tasks[0].id

        # Second generation with higher demand should update existing task, not create duplicate
        sig2 = QueryDemandSignal(
            normalized_query="робот пылесос",
            sample_query="робот пылесос xiaomi",
            search_count=30,
            success_rate=20.0,
            city="Астана",
            needs_discovery=True,
        )
        tasks_second = self.repo.generate_tasks_from_signals([sig2])
        self.assertEqual(len(tasks_second), 0)  # No new tasks created

        updated = self.repo.get_task_by_id(orig_id)
        self.assertEqual(updated.search_count, 30)
        self.assertEqual(updated.priority_score, 24.0)  # 30 * 0.8

    def test_deduplication_city_all_and_none_equivalence(self):
        """C-E14-01: Verifies that city='Все' and city=None are treated as duplicate scopes."""
        sig1 = QueryDemandSignal(
            normalized_query="наушники sony",
            sample_query="наушники sony wh-1000xm5",
            search_count=10,
            success_rate=40.0,
            city=None,
            needs_discovery=True,
        )
        tasks = self.repo.generate_tasks_from_signals([sig1])
        self.assertEqual(len(tasks), 1)

        sig2 = QueryDemandSignal(
            normalized_query="наушники sony",
            sample_query="наушники sony wh-1000xm5",
            search_count=20,
            success_rate=30.0,
            city="Все",
            needs_discovery=True,
        )
        tasks_vse = self.repo.generate_tasks_from_signals([sig2])
        self.assertEqual(len(tasks_vse), 0)  # Deduplicated against city=None task

    def test_update_status(self):
        task = DiscoveryTask(
            id="dtask_status_test",
            signal_query="велосипед trek",
            normalized_query="trek велосипед",
            city="Алматы",
            search_count=15,
            priority_score=12.0,
        )
        self.repo.save_task(task)

        # Move to RUNNING
        ok = self.repo.update_status("dtask_status_test", DiscoveryTaskStatus.RUNNING, notes="Crawler started")
        self.assertTrue(ok)
        t = self.repo.get_task_by_id("dtask_status_test")
        self.assertEqual(t.status, DiscoveryTaskStatus.RUNNING)
        self.assertEqual(t.notes, "Crawler started")

        # Invalid status raises ValueError
        with self.assertRaises(ValueError):
            self.repo.update_status("dtask_status_test", "UNKNOWN_STATUS")

    def test_link_candidate_increments_counter(self):
        task = DiscoveryTask(
            id="dtask_link_test",
            signal_query="корма для кошек",
            normalized_query="для кошек корма",
            city="Алматы",
            search_count=20,
            priority_score=18.0,
        )
        self.repo.save_task(task)

        cand = SellerCandidate(
            id="cand_zoo_1",
            name="Зоомагазин Мяу",
            source_type=DiscoverySource.WEBSITE,
            source_url="https://myau.kz",
            status=CandidateStatus.DISCOVERED,
            city="Алматы",
        )
        self.cand_repo.save_candidate(cand)

        linked = self.repo.link_candidate("dtask_link_test", "cand_zoo_1")
        self.assertTrue(linked)

        # Verify candidate updated
        cand_db = self.cand_repo.get_candidate_by_id("cand_zoo_1")
        self.assertEqual(cand_db.discovery_task_id, "dtask_link_test")

        # Verify task counter incremented
        task_db = self.repo.get_task_by_id("dtask_link_test")
        self.assertEqual(task_db.discovered_candidates_count, 1)

    def test_tasks_summary(self):
        self.repo.save_task(DiscoveryTask(id="s_1", signal_query="q1", normalized_query="q1", priority_score=10.0, status=DiscoveryTaskStatus.PENDING))
        self.repo.save_task(DiscoveryTask(id="s_2", signal_query="q2", normalized_query="q2", priority_score=20.0, status=DiscoveryTaskStatus.RUNNING))
        self.repo.save_task(DiscoveryTask(id="s_3", signal_query="q3", normalized_query="q3", priority_score=30.0, status=DiscoveryTaskStatus.COMPLETED, discovered_candidates_count=3))

        summary = self.repo.get_tasks_summary()
        self.assertEqual(summary["total_tasks"], 3)
        self.assertEqual(summary["pending_tasks"], 1)
        self.assertEqual(summary["running_tasks"], 1)
        self.assertEqual(summary["completed_tasks"], 1)
        self.assertEqual(summary["total_candidates_found"], 3)
        self.assertAlmostEqual(summary["avg_priority"], 20.0, places=1)


class TestAdaptiveDiscoveryIntegration(unittest.TestCase):
    """End-to-end integration: SearchEvents -> Demand Signals -> Discovery Tasks."""

    def setUp(self):
        self.conn = get_connection()
        init_schema_v2(self.conn)
        self.analytics_repo = SearchAnalyticsRepository(self.conn)
        self.discovery_repo = DiscoveryTaskRepository(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_pipeline_unsatisfied_searches_trigger_tasks(self):
        # 1. Record 8 searches for a query with NOT_FOUND outcomes
        # (first 2 satisfy privacy threshold MIN_OCCURRENCES_TO_STORE_TEXT, next 6 store clean text)
        for i in range(8):
            self.analytics_repo.record_event(SearchEvent(
                query="глюкометр contour plus",
                outcome=SearchOutcome.NOT_FOUND,
                city="Астана",
                created_at=NOW_ISO,
            ))

        # 2. Extract discovery signals
        signals = self.analytics_repo.get_discovery_signals(min_searches=5, max_success_rate=50.0)
        self.assertTrue(len(signals) >= 1)
        glu_signal = next((s for s in signals if "contour" in s.normalized_query), None)
        self.assertIsNotNone(glu_signal)
        self.assertEqual(glu_signal.search_count, 6)
        self.assertEqual(glu_signal.success_rate, 0.0)

        # 3. Generate discovery tasks from signals
        tasks = self.discovery_repo.generate_tasks_from_signals([glu_signal])
        self.assertEqual(len(tasks), 1)
        d_task = tasks[0]
        self.assertEqual(d_task.priority_score, 6.0)
        self.assertEqual(d_task.status, DiscoveryTaskStatus.PENDING)
        self.assertTrue(any("contour" in q["query"] for q in d_task.search_queries))


class TestDiscoveryAPIEndpoints(unittest.IsolatedAsyncioTestCase):
    """Tests REST API endpoints for discovery tasks."""

    async def asyncSetUp(self):
        init_db()
        self.admin_id = 999901
        conn = get_connection()
        try:
            init_schema_v2(conn)
            upsert_telegram_user({"id": self.admin_id, "username": "test_admin", "first_name": "Admin"})
            self.admin_session = create_session(self.admin_id)

            # Seed sample discovery task
            repo = DiscoveryTaskRepository(conn)
            repo.save_task(DiscoveryTask(
                id="api_task_1",
                signal_query="перфоратор makita",
                normalized_query="makita перфоратор",
                city="Алматы",
                search_count=40,
                success_rate=25.0,
                priority_score=30.0,
                status=DiscoveryTaskStatus.PENDING,
            ))

            # Seed search events for generate endpoint (2 rare + 5 text = 7 events)
            analytics_repo = SearchAnalyticsRepository(conn)
            for _ in range(7):
                analytics_repo.record_event(SearchEvent(
                    query="сварочный аппарат resanta",
                    outcome=SearchOutcome.NOT_FOUND,
                    city="Астана",
                    created_at=NOW_ISO,
                ))
            conn.commit()
        finally:
            conn.close()

        app = server.create_app()
        app.cleanup_ctx.clear()
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def test_get_tasks_requires_admin(self):
        # Guest request should be rejected with 401
        resp = await self.client.get("/api/v2/discovery/tasks")
        self.assertEqual(resp.status, 401)

    async def test_get_tasks_as_admin(self):
        self.client.session.cookie_jar.update_cookies({auth.SESSION_COOKIE: self.admin_session})
        with patch.object(auth, "ADMIN_TELEGRAM_IDS", {self.admin_id}):
            resp = await self.client.get("/api/v2/discovery/tasks")
            self.assertEqual(resp.status, 200)
            data = await resp.json()
            self.assertIn("tasks", data)
            self.assertIn("summary", data)
            self.assertTrue(len(data["tasks"]) >= 1)
            self.assertEqual(data["tasks"][0]["id"], "api_task_1")

    async def test_generate_tasks_as_admin(self):
        self.client.session.cookie_jar.update_cookies({auth.SESSION_COOKIE: self.admin_session})
        headers = {"Origin": str(self.client.make_url("/")).rstrip("/")}
        with patch.object(auth, "ADMIN_TELEGRAM_IDS", {self.admin_id}):
            resp = await self.client.post(
                "/api/v2/discovery/generate",
                json={"min_searches": 3, "max_success_rate": 70.0, "city": "Астана"},
                headers=headers,
            )
            self.assertEqual(resp.status, 200)
            data = await resp.json()
            self.assertEqual(data["status"], "ok")
            self.assertTrue(data["tasks_created"] >= 1)

    async def test_resolve_task_as_admin(self):
        self.client.session.cookie_jar.update_cookies({auth.SESSION_COOKIE: self.admin_session})
        headers = {"Origin": str(self.client.make_url("/")).rstrip("/")}
        with patch.object(auth, "ADMIN_TELEGRAM_IDS", {self.admin_id}):
            resp = await self.client.post(
                "/api/v2/discovery/tasks/api_task_1/resolve",
                json={"status": "COMPLETED", "notes": "Catalog found in Kaspi", "candidates_delta": 2},
                headers=headers,
            )
            self.assertEqual(resp.status, 200)
            data = await resp.json()
            self.assertEqual(data["status"], "ok")
            self.assertEqual(data["task"]["status"], "COMPLETED")
            self.assertEqual(data["task"]["discovered_candidates_count"], 2)


if __name__ == "__main__":
    unittest.main()
