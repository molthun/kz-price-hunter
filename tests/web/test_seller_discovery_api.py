from tests import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import asyncio
import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import auth
from domain.adaptive_discovery import DiscoveryTaskStatus
from domain.discovery import CandidateStatus, DiscoverySource, SellerCandidate
from domain.source_profiler import CatalogFormat, ExtractionTier, SourceProfile
import repositories.schema_v2 as schema_v2
from repositories.candidate_repo import CandidateRepository
from repositories.discovery_task_repo import DiscoveryTaskRepository
import web.server as srv


class TestSellerDiscoveryApi(unittest.IsolatedAsyncioTestCase):
    """
    Tests for Seller Discovery & Source Profiler administrative endpoints:
    - GET /api/v2/admin/discovery/stats
    - GET /api/v2/admin/discovery/candidates (filters, search, pagination, profiles)
    - POST /api/v2/admin/discovery/candidates/{id}/profile
    - POST /api/v2/discovery/tasks/create
    """

    async def asyncSetUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test_discovery.db")

        # Initialize schema in the temporary file DB
        init_conn = sqlite3.connect(self.db_path)
        schema_v2.init_schema_v2(init_conn)
        init_conn.close()

        def _get_test_conn():
            c = sqlite3.connect(self.db_path, check_same_thread=False)
            c.row_factory = sqlite3.Row
            return c

        self.conn_patcher = patch("database.get_connection", side_effect=_get_test_conn)
        self.conn_patcher.start()

        # Seed initial data
        conn = _get_test_conn()
        try:
            self.cand_repo = CandidateRepository(conn)
            self.task_repo = DiscoveryTaskRepository(conn)

            c1 = SellerCandidate(
                id="cand_apple_city",
                name="Apple City Shop",
                source_type=DiscoverySource.WEBSITE,
                source_url="https://applecity.kz/catalog/smartphones",
                city="Алматы",
                phone="+77011112233",
                domain="applecity.kz",
                status=CandidateStatus.DISCOVERED,
            )
            self.cand_repo.save_candidate(c1)

            c2 = SellerCandidate(
                id="cand_insta_boutique",
                name="Almaty Gadgets Insta",
                source_type=DiscoverySource.INSTAGRAM,
                source_url="https://instagram.com/almaty_gadgets",
                city="Алматы",
                phone="+77023334455",
                status=CandidateStatus.PROFILED,
            )
            self.cand_repo.save_candidate(c2)

            # Profile for c2
            p2 = SourceProfile(
                id="prof_cand_insta_boutique",
                candidate_id="cand_insta_boutique",
                source_url="https://instagram.com/almaty_gadgets",
                catalog_format=CatalogFormat.INSTAGRAM_TEXT,
                extraction_tier=ExtractionTier.SOCIAL_TEXT,
                observed=True,
            )
            self.cand_repo.save_source_profile(p2)
            conn.commit()
        finally:
            conn.close()

        # Build anonymous and admin test clients
        self.anon_app = web.Application()
        self.anon_app.add_routes(srv.routes)
        self.anon_server = TestServer(self.anon_app)
        self.anon_client = TestClient(self.anon_server)
        await self.anon_client.start_server()

        @web.middleware
        async def fake_admin_mw(request, handler):
            request["user"] = {"id": 1, "username": "admin_test"}
            return await handler(request)

        self.admin_app = web.Application(middlewares=[fake_admin_mw])
        self.admin_app.add_routes(srv.routes)
        self.admin_server = TestServer(self.admin_app)
        self.admin_client = TestClient(self.admin_server)
        await self.admin_client.start_server()

        self.admin_auth_patcher = patch.object(auth, "ADMIN_TELEGRAM_IDS", {1})
        self.admin_auth_patcher.start()

    async def asyncTearDown(self):
        self.admin_auth_patcher.stop()
        await self.admin_client.close()
        await self.admin_server.close()
        await self.anon_client.close()
        await self.anon_server.close()
        self.conn_patcher.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    async def test_admin_auth_protection(self):
        """Unauthenticated requests must be rejected with 401/403."""
        endpoints = [
            ("GET", "/api/v2/admin/discovery/stats"),
            ("GET", "/api/v2/admin/discovery/candidates"),
            ("POST", "/api/v2/admin/discovery/candidates/cand_apple_city/profile"),
            ("POST", "/api/v2/discovery/tasks/create"),
        ]
        for method, url in endpoints:
            with self.subTest(method=method, url=url):
                if method == "GET":
                    resp = await self.anon_client.get(url)
                else:
                    resp = await self.anon_client.post(url, json={})
                self.assertIn(resp.status, (401, 403))

    async def test_get_discovery_stats(self):
        """Admin stats endpoint returns comprehensive discovery funnel metrics."""
        resp = await self.admin_client.get("/api/v2/admin/discovery/stats")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data.get("ok"))
        self.assertIn("candidates", data)
        self.assertIn("tasks", data)

        c_stats = data["candidates"]
        self.assertEqual(c_stats["total_candidates"], 2)
        self.assertEqual(c_stats["by_status"][CandidateStatus.DISCOVERED], 1)
        self.assertEqual(c_stats["by_status"][CandidateStatus.PROFILED], 1)
        self.assertEqual(c_stats["by_source"][DiscoverySource.WEBSITE], 1)
        self.assertEqual(c_stats["by_source"][DiscoverySource.INSTAGRAM], 1)
        self.assertEqual(c_stats["total_profiles"], 1)
        self.assertEqual(c_stats["observed_profiles"], 1)

    async def test_list_candidates_filters_and_search(self):
        """GET /api/v2/admin/discovery/candidates supports filters, q search, and profiles embedding."""
        # 1. All candidates
        resp = await self.admin_client.get("/api/v2/admin/discovery/candidates")
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["total"], 2)
        self.assertEqual(len(data["candidates"]), 2)

        # 2. Filter by status
        resp = await self.admin_client.get("/api/v2/admin/discovery/candidates?status=DISCOVERED")
        data = await resp.json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["candidates"][0]["id"], "cand_apple_city")

        # 3. Filter by source_type
        resp = await self.admin_client.get(f"/api/v2/admin/discovery/candidates?source_type={DiscoverySource.INSTAGRAM}")
        data = await resp.json()
        self.assertEqual(data["total"], 1)
        cand = data["candidates"][0]
        self.assertEqual(cand["id"], "cand_insta_boutique")
        self.assertEqual(len(cand["profiles"]), 1)
        self.assertEqual(cand["profiles"][0]["catalog_format"], CatalogFormat.INSTAGRAM_TEXT)

        # 4. Search by query 'q'
        resp = await self.admin_client.get("/api/v2/admin/discovery/candidates?q=applecity")
        data = await resp.json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["candidates"][0]["id"], "cand_apple_city")

        # 5. Invalid status/source returns 400
        resp = await self.admin_client.get("/api/v2/admin/discovery/candidates?status=INVALID_STATUS")
        self.assertEqual(resp.status, 400)

    async def test_profile_candidate_endpoint(self):
        """POST /api/v2/admin/discovery/candidates/{id}/profile triggers profiling and updates status."""
        resp = await self.admin_client.post(
            "/api/v2/admin/discovery/candidates/cand_apple_city/profile",
            json={"hints": {"status_code": 200, "content_type": "text/html"}},
        )
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["ok"])
        cand = data["candidate"]
        # Since hints simulate an observed website with HTML, status transitions to CATALOG_FOUND
        self.assertEqual(cand["status"], CandidateStatus.CATALOG_FOUND)
        self.assertTrue(len(data["profiles"]) >= 1)
        self.assertEqual(data["profiles"][0]["domain"], "applecity.kz")

        # 404 for non-existent candidate
        resp_404 = await self.admin_client.post(
            "/api/v2/admin/discovery/candidates/non_existent_cand/profile",
            json={},
        )
        self.assertEqual(resp_404.status, 404)

    async def test_create_discovery_task(self):
        """POST /api/v2/discovery/tasks/create generates targeted search queries and persists task."""
        # 1. Validation error when query is empty
        resp = await self.admin_client.post("/api/v2/discovery/tasks/create", json={"query": ""})
        self.assertEqual(resp.status, 400)

        # 2. Valid creation
        resp = await self.admin_client.post(
            "/api/v2/discovery/tasks/create",
            json={
                "query": "iPhone 16 Pro Max",
                "city": "Астана",
                "priority_score": 8.5,
                "notes": "Высокий спрос в Астане",
            },
        )
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertTrue(data["ok"])
        task = data["task"]
        self.assertEqual(task["signal_query"], "iPhone 16 Pro Max")
        self.assertEqual(task["city"], "Астана")
        self.assertEqual(task["priority_score"], 8.5)
        self.assertEqual(task["status"], DiscoveryTaskStatus.PENDING)
        self.assertTrue(len(task["search_queries"]) >= 1)
        # Check generated query targets Google / 2GIS / Instagram / Kaspi
        channels = [q["channel"] for q in task["search_queries"]]
        self.assertIn("google", channels)

    async def test_repo_advanced_filters_and_count(self):
        """Unit level verification for CandidateRepository query & filter methods."""
        conn = sqlite3.connect(self.db_path)
        try:
            repo = CandidateRepository(conn)

            # Query by phone
            by_phone = repo.find_candidates(phone="1112233")
            self.assertEqual(len(by_phone), 1)
            self.assertEqual(by_phone[0].id, "cand_apple_city")

            # Query by city
            by_city = repo.find_candidates(city="Алматы")
            self.assertEqual(len(by_city), 2)

            # Query by domain
            by_dom = repo.find_candidates(domain="applecity")
            self.assertEqual(len(by_dom), 1)

            # Pagination offset & limit
            p1 = repo.find_candidates(offset=0, limit=1)
            self.assertEqual(len(p1), 1)
            p2 = repo.find_candidates(offset=1, limit=1)
            self.assertEqual(len(p2), 1)
            self.assertNotEqual(p1[0].id, p2[0].id)

            # Count candidates with filters
            c_all = repo.count_candidates()
            self.assertEqual(c_all, 2)
            c_disc = repo.count_candidates(status=CandidateStatus.DISCOVERED)
            self.assertEqual(c_disc, 1)
            c_query = repo.count_candidates(query="Gadgets")
            self.assertEqual(c_query, 1)
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
