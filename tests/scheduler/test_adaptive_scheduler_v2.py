"""Adaptive Scheduler 2.0 (S2-E15/E17): план по спросу и discovery-задачам.

Проверяем поведение: детерминированное сопоставление запросов с категориями, лимит и
фильтр включённых категорий, shadow-режим не меняет волну, включённый добавляет не больше
N категорий, ошибка планировщика не ломает обход, план сохраняется для наблюдения.
"""
from tests import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import os
import sqlite3
import unittest
from unittest.mock import Mock, patch

from aiohttp import web
from aiohttp.test_utils import make_mocked_request

import adaptive_scheduler_v2 as sched
from config import MASTER_CATEGORIES
from domain.adaptive_discovery import DiscoveryTask, DiscoveryTaskStatus
from domain.search_analytics import QueryDemandSignal
from repositories import DiscoveryTaskRepository, init_schema_v2
import web.server as server


def signal(query: str, count: int = 5, success: float = 20.0) -> QueryDemandSignal:
    return QueryDemandSignal(normalized_query=query, sample_query=query,
                             search_count=count, success_rate=success, city="Астана",
                             needs_discovery=True)


def task(task_id: str, query: str, priority: float = 5.0,
         status: str = DiscoveryTaskStatus.PENDING) -> DiscoveryTask:
    return DiscoveryTask(id=task_id, signal_query=query, normalized_query=query.lower(),
                         city="Астана", priority_score=priority, status=status)


class CategoryMappingTest(unittest.TestCase):
    def test_queries_map_to_master_categories(self):
        self.assertIn("smartphones", sched.categories_for_query("айфон 15 про"))
        self.assertIn("laptops", sched.categories_for_query("Игровой ноутбук ASUS"))
        self.assertIn("pc_components", sched.categories_for_query("видеокарта RTX 5090"))
        self.assertIn("pets", sched.categories_for_query("корм для кошек"))
        self.assertEqual(sched.categories_for_query("неизвестный запрос"), [])

    def test_matching_is_case_and_yo_insensitive(self):
        self.assertEqual(sched.categories_for_query("ПЛЁНКА ДЛЯ ТЕЛЕФОНА"),
                         sched.categories_for_query("пленка для телефона"))

    def test_multiple_categories_keep_master_order(self):
        categories = sched.categories_for_query("ноутбук для игр и монитор")
        self.assertEqual(categories, ["laptops", "monitors"])


class ModeTest(unittest.TestCase):
    def test_default_is_shadow(self):
        self.assertEqual(sched.mode({}), sched.MODE_SHADOW)

    def test_known_modes_are_accepted(self):
        self.assertEqual(sched.mode({sched.ENV_VAR: "on"}), sched.MODE_ON)
        self.assertEqual(sched.mode({sched.ENV_VAR: "OFF"}), sched.MODE_OFF)
        self.assertEqual(sched.mode({sched.ENV_VAR: " Shadow "}), sched.MODE_SHADOW)

    def test_unknown_mode_falls_back_to_shadow(self):
        self.assertEqual(sched.mode({sched.ENV_VAR: "включить-всё"}), sched.MODE_SHADOW)


class BuildPlanTest(unittest.TestCase):
    def test_signals_and_tasks_aggregate_scores_by_category(self):
        plan = sched.build_plan(
            [signal("видеокарта rtx 5090", count=5, success=10.0)],  # 4.5
            [task("t1", "игровой ноутбук", priority=8.0)],           # 8.0
        )
        self.assertEqual(plan["extra_categories"][:2], ["laptops", "pc_components"])
        self.assertEqual(plan["reasons"]["demand_signals"], 1)
        self.assertEqual(plan["reasons"]["discovery_tasks"], 1)
        self.assertGreater(plan["category_scores"]["laptops"], 0)

    def test_limit_caps_extra_categories(self):
        signals = [signal("смартфон"), signal("ноутбук"), signal("монитор"), signal("корм для кошек")]
        plan = sched.build_plan(signals, [], limit=2)
        self.assertEqual(len(plan["extra_categories"]), 2)

    def test_disabled_categories_are_excluded(self):
        enabled = {key: key != "laptops" for key in MASTER_CATEGORIES}
        plan = sched.build_plan([], [task("t1", "игровой ноутбук")], enabled_categories=enabled)
        self.assertNotIn("laptops", plan["extra_categories"])

    def test_non_pending_tasks_are_ignored(self):
        plan = sched.build_plan([], [task("t1", "игровой ноутбук",
                                          status=DiscoveryTaskStatus.COMPLETED)])
        self.assertEqual(plan["extra_categories"], [])

    def test_queries_without_categories_produce_empty_plan(self):
        plan = sched.build_plan([signal("новый уникальный запрос")],
                                [task("t1", "ещё один непонятный")])
        self.assertEqual(plan["extra_categories"], [])
        self.assertEqual(plan["reasons"]["demand_signals"], 0)

    def test_plan_is_reproducible_for_same_input(self):
        args = ([signal("айфон 15")], [task("t1", "игровой ноутбук", 3.0)])
        first = sched.build_plan(*args)
        second = sched.build_plan(*args)
        self.assertEqual(first["extra_categories"], second["extra_categories"])
        self.assertEqual(first["category_scores"], second["category_scores"])


class PersistenceTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_schema_v2(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_record_and_load_latest_plan(self):
        plan = sched.build_plan([signal("корм для кошек")], [task("t1", "игровой ноутбук")])
        sched.record_plan(self.conn, plan)
        loaded = sched.latest_plan(self.conn)
        self.assertEqual(loaded["id"], plan["id"])
        self.assertEqual(loaded["extra_categories"], plan["extra_categories"])
        self.assertTrue(loaded["discovery_tasks"])

    def test_history_is_trimmed(self):
        with patch.object(sched, "PLANS_KEEP", 2):
            for index in range(3):
                plan = sched.build_plan([], [])
                plan["id"] = f"plan_{index}"
                plan["generated_at"] = f"2026-10-22T10:0{index}:00+00:00"
                sched.record_plan(self.conn, plan)
        count = self.conn.execute("SELECT COUNT(*) FROM scheduler_plans").fetchone()[0]
        self.assertEqual(count, 2)


class GetPlanTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_schema_v2(self.conn)
        DiscoveryTaskRepository(self.conn).save_task(
            task("t1", "игровой ноутбук", priority=8.0))

    def tearDown(self):
        self.conn.close()

    def test_off_mode_does_not_write_plan(self):
        with patch.dict(os.environ, {sched.ENV_VAR: "off"}):
            plan = sched.get_plan(conn=self.conn)
            self.assertEqual(plan["mode"], "off")
            self.assertEqual(plan["extra_categories"], [])
            self.assertIsNone(sched.latest_plan(self.conn))

    def test_shadow_mode_records_plan(self):
        with patch.dict(os.environ, {sched.ENV_VAR: "shadow"}):
            plan = sched.get_plan(conn=self.conn)
        self.assertEqual(plan["mode"], "shadow")
        self.assertEqual(sched.latest_plan(self.conn)["extra_categories"], ["laptops"])

    def test_real_demand_signal_from_database(self):
        for i in range(5):
            self.conn.execute(
                "INSERT INTO search_events (id, query, normalized_query, city, outcome,"
                " total_products, total_offers, latency_ms, created_at)"
                " VALUES (?, ?, ?, ?, 'NOT_FOUND', 0, 0, 1.0, '2026-10-22T10:00:00+00:00')",
                (f"e{i}", "видеокарта rtx 5090", "видеокарта rtx 5090", "Астана"))
        self.conn.commit()
        with patch.dict(os.environ, {sched.ENV_VAR: "shadow"}):
            plan = sched.get_plan(conn=self.conn, limit=2)
        self.assertIn("pc_components", plan["extra_categories"])
        self.assertEqual(plan["reasons"]["demand_signals"], 1)


class AdaptWavePlanTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_schema_v2(self.conn)
        repo = DiscoveryTaskRepository(self.conn)
        repo.save_task(task("t1", "игровой ноутбук", priority=8.0))
        repo.save_task(task("t2", "смартфон до 200000", priority=5.0))

    def tearDown(self):
        self.conn.close()

    def test_shadow_does_not_change_wave(self):
        with patch.dict(os.environ, {sched.ENV_VAR: "shadow"}):
            result = sched.adapt_wave_plan(["smartphones"], conn=self.conn)
        self.assertEqual(result["extra_categories"], [])
        self.assertIn("laptops", result["summary"]["extra_categories"])

    def test_on_mode_adds_only_new_categories(self):
        with patch.dict(os.environ, {sched.ENV_VAR: "on"}):
            result = sched.adapt_wave_plan(["laptops"], conn=self.conn)
        self.assertNotIn("laptops", result["extra_categories"])
        self.assertIn("smartphones", result["extra_categories"])

    def test_off_mode_is_noop_without_db(self):
        with patch.dict(os.environ, {sched.ENV_VAR: "off"}):
            result = sched.adapt_wave_plan(["smartphones"], conn=None)
        self.assertEqual(result["extra_categories"], [])
        self.assertEqual(result["mode"], "off")

    def test_planner_error_does_not_break_the_wave(self):
        with patch.object(sched, "get_plan", side_effect=RuntimeError("boom")):
            result = sched.adapt_wave_plan(["smartphones"], conn=self.conn)
        self.assertEqual(result["extra_categories"], [])
        self.assertEqual(result["summary"]["error"], "RuntimeError")


class SchedulerPlanEndpointTest(unittest.IsolatedAsyncioTestCase):
    async def test_guest_gets_401(self):
        request = make_mocked_request("GET", "/api/v2/scheduler/plan")
        response = await server.scheduler_plan_handler(request)
        self.assertEqual(response.status, 401)

    async def test_admin_gets_latest_plan(self):
        from auth import require_admin  # noqa: F401 — проверяем, что маршрут защищён
        request = make_mocked_request("GET", "/api/v2/scheduler/plan")
        request["user"] = {"id": 1, "is_admin": True}
        fake_conn = Mock()
        with patch("auth.is_admin", return_value=True), \
             patch("database.get_connection", return_value=fake_conn), \
             patch.object(sched, "latest_plan", return_value={"id": "plan_1"}):
            response = await server.scheduler_plan_handler(request)
        self.assertEqual(response.status, 200)
        self.assertIn('"plan_1"', response.text)


if __name__ == "__main__":
    unittest.main()
