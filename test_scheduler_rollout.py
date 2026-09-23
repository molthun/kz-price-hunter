"""P11 Контролируемое включение планировщика: шаги, откат, отсутствие роста агрессивности."""
import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import datetime
import os
import tempfile
import unittest
from unittest.mock import patch

import database
import scheduler_rollout as rollout
import scheduler_shadow as sched
from config import DB_PATH

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 9, 23, 12, 0, tzinfo=UTC)


def candidate(shop, profile=sched.NORMAL, category="Смартфоны", age_hours=40.0, **kw):
    base = {"shop": shop, "category": category, "profile": profile, "age_hours": age_hours,
            "last_quality": "ok", "searches": 0, "unmet_searches": 0, "watches": 0,
            "price_changes_per_day": 0.0, "target_hours": 6.0}
    base.update(kw)
    return base


class StagesTest(unittest.TestCase):
    def test_default_is_off(self):
        self.assertEqual(rollout.stage_of({}), rollout.OFF)
        self.assertEqual(rollout.stage_of({"adaptive_scheduler_stage": "мимо"}), rollout.OFF)

    def test_forward_only_one_step_at_a_time(self):
        self.assertTrue(rollout.can_switch(rollout.OFF, rollout.CANARY_ONE)[0])
        ok, why = rollout.can_switch(rollout.OFF, rollout.ALL)
        self.assertFalse(ok)
        self.assertIn("нельзя перепрыгнуть", why)
        self.assertTrue(rollout.can_switch(rollout.CANARY_ONE, rollout.CANARY_FEW)[0])
        self.assertTrue(rollout.can_switch(rollout.CANARY_FEW, rollout.ALL)[0])

    def test_switching_back_is_always_allowed(self):
        for stage in (rollout.CANARY_ONE, rollout.CANARY_FEW, rollout.ALL):
            with self.subTest(stage=stage):
                self.assertTrue(rollout.can_switch(stage, rollout.OFF)[0])
        self.assertTrue(rollout.can_switch(rollout.ALL, rollout.CANARY_ONE)[0])

    def test_first_canary_is_the_friendliest_shop(self):
        candidates = [candidate("dns", sched.DEGRADED), candidate("kaspi", sched.FRIENDLY),
                      candidate("alser", sched.NORMAL), candidate("mechta", sched.EXPENSIVE)]
        self.assertEqual(rollout.canary_shops(rollout.CANARY_ONE, candidates), ["kaspi"])
        few = rollout.canary_shops(rollout.CANARY_FEW, candidates)
        self.assertEqual(few[:2], ["kaspi", "alser"])
        self.assertLessEqual(len(few), rollout.CANARY_FEW_LIMIT)
        self.assertEqual(len(rollout.canary_shops(rollout.ALL, candidates)), 4)
        self.assertEqual(rollout.canary_shops(rollout.OFF, candidates), [])

    def test_canary_choice_is_reproducible(self):
        candidates = [candidate(f"shop{i}", sched.NORMAL) for i in range(8)]
        allowed = [f"shop{i}" for i in range(8)]
        first = rollout.canary_shops(rollout.CANARY_FEW, candidates, allowed=allowed)
        self.assertEqual(first, rollout.canary_shops(rollout.CANARY_FEW, list(reversed(candidates)),
                                                     allowed=allowed))


class SelectionTest(unittest.TestCase):
    def setUp(self):
        self.candidates = [candidate("kaspi", sched.FRIENDLY, age_hours=50.0, unmet_searches=100),
                           candidate("dns", age_hours=45.0),
                           candidate("alser", age_hours=8.0)]
        self.baseline = ["dns", "alser", "kaspi"]

    def test_off_keeps_the_old_order_exactly(self):
        result = rollout.select_targets(rollout.OFF, self.baseline, self.candidates, now=NOW)
        self.assertEqual(result["targets"], self.baseline)
        self.assertFalse(result["changed"])

    def test_adaptive_order_never_adds_a_shop(self):
        """Агрессивность не растёт: новый порядок — подмножество прежней волны."""
        for stage in (rollout.CANARY_ONE, rollout.CANARY_FEW, rollout.ALL):
            with self.subTest(stage=stage):
                result = rollout.select_targets(stage, self.baseline, self.candidates, now=NOW)
                self.assertTrue(set(result["targets"]) <= set(self.baseline))
                self.assertEqual(len(result["targets"]), len(set(result["targets"])))

    def test_canary_shop_goes_first_when_it_is_needed_most(self):
        result = rollout.select_targets(rollout.CANARY_ONE, self.baseline, self.candidates, now=NOW)
        self.assertEqual(result["canary"], ["kaspi"])
        self.assertEqual(result["targets"][0], "kaspi")
        self.assertEqual(sorted(result["targets"]), sorted(self.baseline))

    def test_canary_shop_too_fresh_is_skipped_not_hurried(self):
        candidates = [candidate("kaspi", sched.FRIENDLY, age_hours=1.0)]
        result = rollout.select_targets(rollout.CANARY_ONE, ["kaspi", "dns"], candidates, now=NOW)
        self.assertNotIn("kaspi", result["targets"])
        self.assertIn("kaspi", result["skipped"])
        self.assertIn("dns", result["targets"])

    def test_shops_outside_the_canary_keep_the_old_behaviour(self):
        result = rollout.select_targets(rollout.CANARY_ONE, self.baseline, self.candidates, now=NOW)
        others = [s for s in result["targets"] if s != "kaspi"]
        self.assertEqual(others, ["dns", "alser"])

    def test_shop_missing_from_this_wave_is_not_dragged_in(self):
        result = rollout.select_targets(rollout.CANARY_ONE, ["dns"], self.candidates, now=NOW)
        self.assertEqual(result["targets"], ["dns"])
        self.assertFalse(result["changed"])


class RollbackRulesTest(unittest.TestCase):
    def before(self, **kw):
        base = {"error_share": 0.10, "block_share": 0.02, "completeness": 0.90,
                "requests": 500, "scans": 50, "enough_data": True}
        base.update(kw)
        return base

    def after(self, **kw):
        return self.before(**kw)

    def test_thresholds_are_the_agreed_ones(self):
        self.assertEqual(rollout.ERROR_GROWTH_LIMIT, 1.33)
        self.assertEqual(rollout.BLOCK_GROWTH_LIMIT, 1.33)
        self.assertEqual(rollout.COMPLETENESS_DROP_LIMIT, 0.10)

    def test_errors_growing_by_a_third_trigger_rollback(self):
        needed, why = rollout.should_rollback(self.before(), self.after(error_share=0.20))
        self.assertTrue(needed)
        self.assertIn("ошибок", why)

    def test_small_growth_does_not_trigger_rollback(self):
        needed, _ = rollout.should_rollback(self.before(), self.after(error_share=0.12))
        self.assertFalse(needed)

    def test_blocks_growing_trigger_rollback(self):
        needed, why = rollout.should_rollback(self.before(), self.after(block_share=0.05))
        self.assertTrue(needed)
        self.assertIn("лимиту", why)

    def test_completeness_falling_triggers_rollback(self):
        needed, why = rollout.should_rollback(self.before(), self.after(completeness=0.75))
        self.assertTrue(needed)
        self.assertIn("полнота", why)

    def test_completeness_falling_slightly_does_not(self):
        needed, _ = rollout.should_rollback(self.before(), self.after(completeness=0.85))
        self.assertFalse(needed)

    def test_too_little_data_is_not_a_verdict(self):
        needed, why = rollout.should_rollback(self.before(), self.after(enough_data=False))
        self.assertFalse(needed)
        self.assertIn("мало", why)

    def test_errors_appearing_from_zero_are_caught(self):
        needed, why = rollout.should_rollback(self.before(error_share=0.0), self.after(error_share=0.05))
        self.assertTrue(needed)

    def test_next_step_needs_observation_time_and_clean_metrics(self):
        ready, why = rollout.ready_for_next_step(rollout.CANARY_ONE, self.before(), self.after(),
                                                 NOW - datetime.timedelta(hours=2), NOW)
        self.assertFalse(ready)
        self.assertIn("из 24", why)
        ready, why = rollout.ready_for_next_step(rollout.CANARY_ONE, self.before(), self.after(),
                                                 NOW - datetime.timedelta(hours=30), NOW)
        self.assertTrue(ready)
        ready, why = rollout.ready_for_next_step(rollout.CANARY_ONE, self.before(),
                                                 self.after(error_share=0.30),
                                                 NOW - datetime.timedelta(hours=30), NOW)
        self.assertFalse(ready)
        self.assertIn("ухудшения", why)

    def test_last_stage_has_no_next(self):
        ready, why = rollout.ready_for_next_step(rollout.ALL, self.before(), self.after(),
                                                 NOW - datetime.timedelta(hours=48), NOW)
        self.assertFalse(ready)
        self.assertIn("последний шаг", why)


class RolloutStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = type(DB_PATH)(self.tmp.name)
        self.patches = [patch.object(database, "DB_PATH", self.data_dir / "prices.db"),
                        patch("config.DATA_DIR", self.data_dir),
                        patch("config.SETTINGS_FILE", self.data_dir / "settings.json")]
        for p in self.patches:
            p.start()
        database.init_db()
        self.candidates = [candidate("kaspi", sched.FRIENDLY), candidate("dns")]

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def http(self, shop, requests, errors, blocked, at=None):
        """Часовой агрегат: периоды «до» и «после» должны различаться с точностью до часа (M02)."""
        moment = at or (NOW - datetime.timedelta(hours=1))
        bucket = moment.strftime("%Y-%m-%dT%H:00:00Z")
        with database.get_connection() as conn:
            conn.execute("""INSERT INTO telemetry_http_aggregates
                            (bucket_type, bucket_start, host, shop, total_requests, errors, status_429,
                             status_4xx, status_5xx)
                            VALUES ('hour', ?, ?, ?, ?, ?, ?, 0, 0)
                            ON CONFLICT(bucket_type, bucket_start, host, shop) DO UPDATE SET
                                total_requests = excluded.total_requests, errors = excluded.errors,
                                status_429 = excluded.status_429""",
                         (bucket, f"{shop}.kz", shop, requests, errors, blocked))
            conn.commit()

    def scans(self, shop, total, good, day=None):
        moment = day or (NOW - datetime.timedelta(hours=1))
        with database.get_connection() as conn:
            for i in range(total):
                conn.execute("""INSERT INTO source_scans (shop_key, source_url, category, scan_id,
                                started_at, finished_at, kind, quality, received, valid, rejected,
                                with_image, accepted)
                                VALUES (?, ?, ?, ?, ?, ?, 'category', ?, 10, 10, 0, 10, 1)""",
                             (shop, f"https://{shop}.kz/c", "Смартфоны", f"s{i}",
                              moment.isoformat(), moment.isoformat(),
                              "ok" if i < good else "degraded"))
            conn.commit()

    def test_starting_a_stage_remembers_the_before_picture(self):
        self.http("kaspi", 200, 10, 2)
        self.scans("kaspi", 10, 9)
        started = rollout.start_stage(rollout.CANARY_ONE, self.candidates, now=NOW)
        self.assertEqual(started["canary"], ["kaspi"])
        before = rollout.baseline_metrics()
        self.assertEqual(before["requests"], 200)
        self.assertAlmostEqual(before["error_share"], 0.05)
        self.assertAlmostEqual(before["completeness"], 0.9)
        self.assertIsNotNone(rollout.started_at(NOW))

    def test_metrics_cover_only_the_canary_shops(self):
        self.http("kaspi", 100, 5, 0)
        self.http("dns", 100, 50, 0)
        self.scans("kaspi", 10, 10)
        data = rollout.metrics(["kaspi"], NOW - datetime.timedelta(days=1), NOW)
        self.assertEqual(data["requests"], 100)
        self.assertAlmostEqual(data["error_share"], 0.05)

    def test_rollback_returns_the_old_order_and_records_why(self):
        import config
        config.save_settings({**config.load_settings(), rollout.SETTING_STAGE: rollout.CANARY_ONE})
        self.assertEqual(rollout.stage_of(), rollout.CANARY_ONE)
        record = rollout.rollback("выросли отказы по лимиту", now=NOW)
        self.assertEqual(rollout.stage_of(), rollout.OFF)
        self.assertEqual(record["from_stage"], rollout.CANARY_ONE)
        self.assertEqual(rollout.last_rollback()["reason"], "выросли отказы по лимиту")

    def test_automatic_rollback_on_worsening_metrics(self):
        import config
        self.http("kaspi", 200, 4, 0)
        self.scans("kaspi", 10, 10)
        config.save_settings({**config.load_settings(), rollout.SETTING_STAGE: rollout.CANARY_ONE})
        rollout.start_stage(rollout.CANARY_ONE, self.candidates, now=NOW)
        # Всё стало хуже уже ПОСЛЕ включения: ошибок втрое больше
        later = NOW + datetime.timedelta(hours=1)
        self.http("kaspi", 200, 60, 0, at=later)
        self.scans("kaspi", 10, 10, day=later)
        record = rollout.check_and_rollback(self.candidates, now=NOW + datetime.timedelta(hours=2))
        self.assertIsNotNone(record)
        self.assertEqual(rollout.stage_of(), rollout.OFF)
        self.assertIn("ошибок", record["reason"])

    def test_no_rollback_while_metrics_hold(self):
        import config
        self.http("kaspi", 200, 10, 2)
        self.scans("kaspi", 10, 9)
        config.save_settings({**config.load_settings(), rollout.SETTING_STAGE: rollout.CANARY_ONE})
        rollout.start_stage(rollout.CANARY_ONE, self.candidates, now=NOW)
        later = NOW + datetime.timedelta(hours=1)
        self.http("kaspi", 200, 10, 2, at=later)
        self.scans("kaspi", 10, 9, day=later)
        self.assertIsNone(rollout.check_and_rollback(self.candidates,
                                                     now=NOW + datetime.timedelta(hours=2)))
        self.assertEqual(rollout.stage_of(), rollout.CANARY_ONE)

    def test_stage_survives_a_restart(self):
        import config
        import importlib
        config.save_settings({**config.load_settings(), rollout.SETTING_STAGE: rollout.CANARY_FEW})
        rollout.start_stage(rollout.CANARY_FEW, self.candidates, now=NOW)
        importlib.reload(rollout)                    # «перезапуск» процесса
        self.assertEqual(rollout.stage_of(), rollout.CANARY_FEW)
        self.assertIsNotNone(rollout.started_at(NOW))
        self.assertIsNotNone(rollout.baseline_metrics())

    def test_stage_without_candidates_says_so(self):
        """Шаг включён, а истории обходов ещё нет — это состояние названо словами, а не прочерком."""
        import config
        config.save_settings({**config.load_settings(), rollout.SETTING_STAGE: rollout.CANARY_ONE})
        state = rollout.status([], now=NOW)
        self.assertEqual(state["canary"], [])
        self.assertFalse(state["ready_for_next"])
        self.assertIn("ещё не определены", state["ready_reason"])

    def test_status_explains_itself(self):
        import config
        self.http("kaspi", 200, 10, 2)
        self.scans("kaspi", 10, 9)
        config.save_settings({**config.load_settings(), rollout.SETTING_STAGE: rollout.CANARY_ONE})
        rollout.start_stage(rollout.CANARY_ONE, self.candidates, now=NOW)
        state = rollout.status(self.candidates, now=NOW + datetime.timedelta(hours=1))
        self.assertEqual(state["stage"], rollout.CANARY_ONE)
        self.assertEqual(state["canary"], ["kaspi"])
        self.assertEqual(state["next_stage"], rollout.CANARY_FEW)
        self.assertFalse(state["ready_for_next"])
        self.assertIn("наблюдений пока мало", state["ready_reason"])
        self.assertIn("после включения", state["note"])
        self.assertEqual(state["thresholds"]["error_growth"], rollout.ERROR_GROWTH_LIMIT)
        self.assertIn("вручную", state["note"])


class _FrozenNow(datetime.datetime):
    """«Сейчас» для кода, который берёт время сам: проверка отката вызывается воркером без параметра."""

    _moment = None

    def __class_getitem__(cls, item):      # pragma: no cover — совместимость с typing
        return cls

    def __new__(cls, moment):
        frozen = type("Frozen", (datetime.datetime,), {})
        frozen.now = classmethod(lambda c, tz=None: moment if tz is None else moment.astimezone(tz))
        frozen.fromisoformat = datetime.datetime.fromisoformat
        return frozen


class WorkerIntegrationTest(RolloutStateTest):
    """Настоящий путь волны: порядок меняется только при включённом шаге и никогда не добавляет обходы."""

    def test_wave_order_is_untouched_while_off(self):
        import web.server as server
        targets, note = server.apply_adaptive_order(["dns", "kaspi"])
        self.assertEqual(targets, ["dns", "kaspi"])
        self.assertEqual(note, "")

    def test_wave_order_changes_only_for_the_canary(self):
        import config
        import web.server as server
        config.save_settings({**config.load_settings(), rollout.SETTING_STAGE: rollout.CANARY_ONE})
        with patch.object(database, "scheduler_candidates", return_value=self.candidates):
            targets, note = server.apply_adaptive_order(["dns", "kaspi"])
        self.assertEqual(sorted(targets), ["dns", "kaspi"])
        self.assertEqual(targets[0], "kaspi")
        self.assertIn("пробных магазинов", note)

    def test_failure_of_the_new_order_never_breaks_the_wave(self):
        import config
        import web.server as server
        config.save_settings({**config.load_settings(), rollout.SETTING_STAGE: rollout.CANARY_ONE})
        with patch.object(database, "scheduler_candidates", side_effect=RuntimeError("база занята")):
            targets, note = server.apply_adaptive_order(["dns", "kaspi"])
        self.assertEqual(targets, ["dns", "kaspi"])
        self.assertEqual(note, "")

    def test_worker_rollback_hook_switches_off(self):
        import config
        import web.server as server
        self.http("kaspi", 200, 4, 0)
        self.scans("kaspi", 10, 10)
        config.save_settings({**config.load_settings(), rollout.SETTING_STAGE: rollout.CANARY_ONE})
        rollout.start_stage(rollout.CANARY_ONE, self.candidates, now=NOW)
        later = NOW + datetime.timedelta(hours=1)
        self.http("kaspi", 200, 60, 0, at=later)
        self.scans("kaspi", 10, 10, day=later)
        with patch.object(database, "scheduler_candidates", return_value=self.candidates), \
             patch.object(rollout.datetime, "datetime", _FrozenNow(NOW + datetime.timedelta(hours=2))):
            record = server.check_adaptive_rollback()
        self.assertIsNotNone(record)
        self.assertEqual(rollout.stage_of(), rollout.OFF)


if __name__ == "__main__":
    unittest.main()


class AuditFixesTest(RolloutStateTest):
    """Регрессии по замечаниям аудита M01, M02 и M03."""

    # --- M01: состав шага не переизбирается ---

    def test_membership_is_fixed_at_start_and_does_not_follow_the_ranking(self):
        import config
        config.save_settings({**config.load_settings(), rollout.SETTING_STAGE: rollout.CANARY_ONE})
        rollout.start_stage(rollout.CANARY_ONE, self.candidates, now=NOW)
        self.assertEqual(rollout.canary_members(), ["kaspi"])
        # Профили поменялись местами: kaspi ухудшился, dns стал дружелюбным
        swapped = [candidate("kaspi", sched.DEGRADED), candidate("dns", sched.FRIENDLY)]
        state = rollout.status(swapped, now=NOW + datetime.timedelta(hours=1))
        self.assertEqual(state["canary"], ["kaspi"], "участник шага не должен подменяться сам")
        selection = rollout.select_targets(rollout.CANARY_ONE, ["dns", "kaspi"], swapped, now=NOW)
        self.assertEqual(selection["canary"], ["kaspi"])

    def test_worsened_member_stays_under_observation_and_is_rolled_back(self):
        import config
        self.http("kaspi", 200, 4, 0)
        self.scans("kaspi", 10, 10)
        config.save_settings({**config.load_settings(), rollout.SETTING_STAGE: rollout.CANARY_ONE})
        rollout.start_stage(rollout.CANARY_ONE, self.candidates, now=NOW)
        later = NOW + datetime.timedelta(hours=1)
        self.http("kaspi", 200, 60, 0, at=later)
        self.scans("kaspi", 10, 10, day=later)
        swapped = [candidate("kaspi", sched.DEGRADED), candidate("dns", sched.FRIENDLY)]
        record = rollout.check_and_rollback(swapped, now=NOW + datetime.timedelta(hours=2))
        self.assertIsNotNone(record, "ухудшившийся участник обязан привести к откату, а не исчезнуть")
        self.assertEqual(rollout.stage_of(), rollout.OFF)

    def test_disappeared_member_is_named_not_replaced(self):
        import config
        config.save_settings({**config.load_settings(), rollout.SETTING_STAGE: rollout.CANARY_ONE})
        rollout.start_stage(rollout.CANARY_ONE, self.candidates, now=NOW)
        state = rollout.status([candidate("dns", sched.FRIENDLY)], now=NOW)
        self.assertEqual(state["missing_members"], ["kaspi"])
        self.assertFalse(state["ready_for_next"])
        self.assertIn("замена не подбирается", state["ready_reason"])

    def test_membership_survives_a_restart(self):
        import config
        import importlib
        config.save_settings({**config.load_settings(), rollout.SETTING_STAGE: rollout.CANARY_ONE})
        rollout.start_stage(rollout.CANARY_ONE, self.candidates, now=NOW)
        importlib.reload(rollout)
        self.assertEqual(rollout.canary_members(), ["kaspi"])

    def test_rollback_clears_the_membership(self):
        import config
        config.save_settings({**config.load_settings(), rollout.SETTING_STAGE: rollout.CANARY_ONE})
        rollout.start_stage(rollout.CANARY_ONE, self.candidates, now=NOW)
        record = rollout.rollback("проверка", now=NOW)
        self.assertEqual(record["canary"], ["kaspi"], "в записи отката видно, кто участвовал")
        self.assertEqual(rollout.canary_members(), [])

    # --- M02: периоды до и после не смешиваются ---

    def test_history_before_the_stage_does_not_mask_a_failing_canary(self):
        """Сценарий аудита: 10 000 хороших запросов до включения и 100 провальных после."""
        import config
        self.http("kaspi", 10000, 1000, 0)
        self.scans("kaspi", 100, 100)
        config.save_settings({**config.load_settings(), rollout.SETTING_STAGE: rollout.CANARY_ONE})
        rollout.start_stage(rollout.CANARY_ONE, self.candidates, now=NOW)
        later = NOW + datetime.timedelta(hours=1)
        self.http("kaspi", 100, 100, 0, at=later)
        self.scans("kaspi", 5, 0, day=later)
        moment = NOW + datetime.timedelta(hours=2)
        after = rollout.metrics(["kaspi"], NOW, moment)
        self.assertEqual(after["requests"], 100, "прежние запросы не относятся к этому шагу")
        self.assertEqual(after["error_share"], 1.0)
        self.assertEqual(after["completeness"], 0.0)
        needed, why = rollout.should_rollback(rollout.baseline_metrics(), after)
        self.assertTrue(needed, why)
        self.assertIsNotNone(rollout.check_and_rollback(self.candidates, now=moment))

    def test_old_observations_do_not_fill_the_sufficiency_threshold(self):
        import config
        self.http("kaspi", 10000, 10, 0)
        self.scans("kaspi", 100, 100)
        config.save_settings({**config.load_settings(), rollout.SETTING_STAGE: rollout.CANARY_ONE})
        rollout.start_stage(rollout.CANARY_ONE, self.candidates, now=NOW)
        after = rollout.metrics(["kaspi"], NOW, NOW + datetime.timedelta(hours=1))
        self.assertEqual(after["requests"], 0)
        self.assertFalse(after["enough_data"], "судить по истории до включения нельзя")

    def test_metrics_report_their_own_period(self):
        data = rollout.metrics(["kaspi"], NOW, NOW + datetime.timedelta(hours=3))
        self.assertEqual(data["hours"], 3.0)
        self.assertTrue(data["since"].startswith("2026-"))


class GuardedSettingsTest(unittest.IsolatedAsyncioTestCase):
    """M03: шаг включения нельзя выставить общим сохранением настроек в обход подготовки."""

    async def test_general_config_endpoint_refuses_the_staged_settings(self):
        import auth
        import config
        import web.server as server
        from aiohttp.test_utils import TestServer
        from test_support import BrowserTestClient as TestClient
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patches = [patch.object(database, "DB_PATH", type(DB_PATH)(os.path.join(tmp.name, "prices.db"))),
                   patch("config.DATA_DIR", type(DB_PATH)(tmp.name)),
                   patch("config.SETTINGS_FILE", type(DB_PATH)(os.path.join(tmp.name, "settings.json")))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        database.init_db()
        database.upsert_telegram_user({"id": 9601, "first_name": "A"})
        token = database.create_session(9601)
        app = server.create_app()
        app.cleanup_ctx.clear()
        async with TestClient(TestServer(app)) as client:
            client.session.cookie_jar.update_cookies({auth.SESSION_COOKIE: token})
            with patch.object(auth, "ADMIN_TELEGRAM_IDS", {9601}):
                res = await client.post("/api/admin/config",
                                        json={"adaptive_scheduler_stage": "all"})
                self.assertEqual(res.status, 400)
                self.assertIn("своим переключателем", (await res.json())["message"])
                self.assertEqual(config.load_settings()["adaptive_scheduler_stage"], "off")
                # Тот же запрет и для режима AI-сопоставления
                self.assertEqual((await client.post("/api/admin/config",
                                                    json={"ai_matching_mode": "on"})).status, 400)
                # Обычные настройки по-прежнему сохраняются
                ok = await client.post("/api/admin/config", json={"candidate_drop_pct": 33})
                self.assertEqual(ok.status, 200)
                self.assertEqual(config.load_settings()["candidate_drop_pct"], 33)

    async def test_dedicated_endpoint_still_refuses_a_jump(self):
        self.assertFalse(rollout.can_switch(rollout.OFF, rollout.ALL)[0])
        self.assertTrue(rollout.can_switch(rollout.OFF, rollout.CANARY_ONE)[0])


class PartialHourTest(RolloutStateTest):
    """M02: шаг, начатый в середине часа, не должен терять первые же ошибки."""

    START = datetime.datetime(2026, 9, 23, 12, 10, tzinfo=datetime.timezone.utc)

    def setUp(self):
        super().setUp()
        import config
        config.save_settings({**config.load_settings(), rollout.SETTING_STAGE: rollout.CANARY_ONE})

    def test_traffic_of_the_starting_hour_is_measured_as_a_delta(self):
        """Воспроизведение аудита: старт 12:10, до него 10 000 запросов, после — 100 провальных."""
        # 12:00–12:10 — прежний трафик того же часа
        self.http("kaspi", 10000, 1000, 0, at=self.START.replace(minute=0))
        self.scans("kaspi", 100, 100, day=self.START - datetime.timedelta(hours=2))
        rollout.start_stage(rollout.CANARY_ONE, self.candidates, now=self.START)
        # 12:20 — тот же час, но уже после включения: счётчик часа вырос
        self.http("kaspi", 10100, 1100, 0, at=self.START.replace(minute=0))
        self.scans("kaspi", 5, 0, day=self.START + datetime.timedelta(minutes=10))
        moment = self.START.replace(minute=30)
        after = rollout.metrics(["kaspi"], self.START, moment, rollout.http_start_counters())
        self.assertEqual(after["requests"], 100, "учтён только трафик после включения")
        self.assertEqual(after["errors"], 100)
        self.assertEqual(after["http_coverage"]["partial_hour"], "delta")
        self.assertTrue(after["enough_data"], "реальных наблюдений достаточно, чтобы судить")

    def test_failure_in_the_first_hour_causes_a_rollback(self):
        self.http("kaspi", 10000, 1000, 0, at=self.START.replace(minute=0))
        self.scans("kaspi", 100, 100, day=self.START - datetime.timedelta(hours=2))
        rollout.start_stage(rollout.CANARY_ONE, self.candidates, now=self.START)
        self.http("kaspi", 10100, 1100, 0, at=self.START.replace(minute=0))
        self.scans("kaspi", 5, 0, day=self.START + datetime.timedelta(minutes=10))
        record = rollout.check_and_rollback(self.candidates, now=self.START.replace(minute=30))
        self.assertIsNotNone(record, "провал первого часа обязан приводить к откату")
        self.assertEqual(rollout.stage_of(), rollout.OFF)

    def test_coverage_is_reported_honestly_without_a_snapshot(self):
        """Без снимка счётчиков час начала не засчитывается — и отчёт об этом говорит."""
        self.http("kaspi", 500, 10, 0, at=self.START.replace(minute=0))
        after = rollout.metrics(["kaspi"], self.START, self.START.replace(minute=50))
        self.assertEqual(after["requests"], 0)
        self.assertEqual(after["http_coverage"]["partial_hour"], "excluded")

    def test_broken_scans_alone_are_enough_to_roll_back(self):
        """Мало запросов, но обходы провалились — это не «мало данных», а ухудшение."""
        self.http("kaspi", 200, 4, 0, at=self.START - datetime.timedelta(hours=2))
        self.scans("kaspi", 10, 10, day=self.START - datetime.timedelta(hours=2))
        rollout.start_stage(rollout.CANARY_ONE, self.candidates, now=self.START)
        self.scans("kaspi", 5, 0, day=self.START + datetime.timedelta(minutes=20))
        after = rollout.metrics(["kaspi"], self.START, self.START.replace(minute=50),
                                rollout.http_start_counters())
        self.assertFalse(after["enough_data"], "запросов действительно мало")
        needed, why = rollout.should_rollback(rollout.baseline_metrics(), after)
        self.assertTrue(needed, why)
        self.assertIn("полнота", why)

    def test_a_step_started_on_the_hour_counts_the_whole_hour(self):
        exact = self.START.replace(minute=0)
        rollout.start_stage(rollout.CANARY_ONE, self.candidates, now=exact)
        self.http("kaspi", 60, 1, 0, at=exact)
        after = rollout.metrics(["kaspi"], exact, exact.replace(minute=40),
                                rollout.http_start_counters())
        self.assertEqual(after["requests"], 60)
        self.assertEqual(after["http_coverage"]["partial_hour"], "full")


class CanaryMembershipTest(RolloutStateTest):
    """M01: участником может быть только включённый магазин, а первый шаг — только дружелюбный."""

    def test_first_step_needs_a_friendly_source(self):
        with self.assertRaises(rollout.StageRefused) as refused:
            rollout.canary_shops(rollout.CANARY_ONE, [candidate("dns", sched.DEGRADED)],
                                 allowed=["dns"])
        self.assertIn("дружелюбном", str(refused.exception))

    def test_disabled_shop_is_not_chosen_even_if_it_is_the_friendliest(self):
        candidates = [candidate("dns", sched.FRIENDLY), candidate("kaspi", sched.FRIENDLY)]
        self.assertEqual(rollout.canary_shops(rollout.CANARY_ONE, candidates, allowed=["kaspi"]),
                         ["kaspi"])

    def test_no_enabled_candidates_refuses_the_step(self):
        with self.assertRaises(rollout.StageRefused):
            rollout.canary_shops(rollout.CANARY_ONE, [candidate("dns", sched.FRIENDLY)], allowed=["kaspi"])

    def test_wider_steps_take_only_enabled_shops(self):
        candidates = [candidate("kaspi", sched.FRIENDLY), candidate("dns"), candidate("alser")]
        self.assertEqual(rollout.canary_shops(rollout.CANARY_FEW, candidates, allowed=["kaspi", "alser"]),
                         ["kaspi", "alser"])

    def test_member_disabled_after_the_start_is_named_not_replaced(self):
        import config
        config.save_settings({**config.load_settings(), rollout.SETTING_STAGE: rollout.CANARY_ONE})
        with patch.object(rollout, "allowed_shops", return_value=["kaspi", "dns"]):
            rollout.start_stage(rollout.CANARY_ONE, self.candidates, now=NOW)
            self.assertEqual(rollout.canary_members(), ["kaspi"])
        # Владелец выключил kaspi уже после начала шага
        with patch.object(rollout, "allowed_shops", return_value=["dns"]):
            state = rollout.status(self.candidates, now=NOW + datetime.timedelta(hours=1))
        self.assertEqual(state["missing_members"], ["kaspi"])
        self.assertEqual(state["canary"], ["kaspi"], "замена не подбирается")
        self.assertFalse(state["ready_for_next"])
        self.assertIn("выключены", state["ready_reason"])

    def test_status_names_the_refusal_instead_of_showing_an_empty_dash(self):
        import config
        config.save_settings({**config.load_settings(), rollout.SETTING_STAGE: rollout.CANARY_ONE})
        with patch.object(rollout, "allowed_shops", return_value=["dns"]):
            state = rollout.status([candidate("dns", sched.DEGRADED)], now=NOW)
        self.assertEqual(state["canary"], [])
        self.assertFalse(state["ready_for_next"])
        self.assertIn("дружелюбном", state["ready_reason"])

    def test_wave_is_not_broken_when_the_step_cannot_be_formed(self):
        selection = rollout.select_targets(rollout.CANARY_ONE, ["dns"],
                                           [candidate("dns", sched.DEGRADED)], now=NOW, members=None)
        self.assertEqual(selection["targets"], ["dns"])
        self.assertFalse(selection["changed"])


class AtomicSwitchTest(RolloutStateTest):
    """M03: включённого шага без подготовленного состояния возникнуть не может."""

    def test_failed_preparation_leaves_the_previous_stage(self):
        with patch.object(rollout, "metrics", side_effect=RuntimeError("база занята")):
            with self.assertRaises(RuntimeError):
                rollout.switch_stage(rollout.CANARY_ONE, self.candidates, now=NOW)
        self.assertEqual(rollout.stage_of(), rollout.OFF, "шаг не должен включиться")
        self.assertEqual(rollout.canary_members(), [])
        self.assertIsNone(rollout.baseline_metrics())

    def test_refused_step_does_not_switch_anything(self):
        with patch.object(rollout, "allowed_shops", return_value=["dns"]):
            with self.assertRaises(rollout.StageRefused):
                rollout.switch_stage(rollout.CANARY_ONE, [candidate("dns", sched.DEGRADED)], now=NOW)
        self.assertEqual(rollout.stage_of(), rollout.OFF)

    def test_jump_over_a_step_is_refused_by_the_only_path(self):
        with self.assertRaises(rollout.StageRefused):
            rollout.switch_stage(rollout.ALL, self.candidates, now=NOW)
        self.assertEqual(rollout.stage_of(), rollout.OFF)

    def test_successful_switch_stores_stage_members_and_baseline_together(self):
        self.http("kaspi", 200, 10, 2)
        self.scans("kaspi", 10, 9)
        rollout.switch_stage(rollout.CANARY_ONE, self.candidates, now=NOW)
        self.assertEqual(rollout.stage_of(), rollout.CANARY_ONE)
        self.assertEqual(rollout.canary_members(), ["kaspi"])
        self.assertIsNotNone(rollout.baseline_metrics())
        self.assertIsNotNone(rollout.started_at(NOW))
        self.assertIsNotNone(rollout.http_start_counters())

    def test_previous_preparation_is_restored_on_failure(self):
        """Неудачная попытка расширения не должна портить состояние уже идущего шага."""
        rollout.switch_stage(rollout.CANARY_ONE, self.candidates, now=NOW)
        members, started = rollout.canary_members(), rollout.started_at(NOW)
        with patch.object(rollout, "metrics", side_effect=RuntimeError("база занята")):
            with self.assertRaises(RuntimeError):
                rollout.switch_stage(rollout.CANARY_FEW, self.candidates,
                                     now=NOW + datetime.timedelta(hours=1))
        self.assertEqual(rollout.stage_of(), rollout.CANARY_ONE)
        self.assertEqual(rollout.canary_members(), members)
        self.assertEqual(rollout.started_at(NOW), started)

    def test_switching_off_clears_the_step_state(self):
        rollout.switch_stage(rollout.CANARY_ONE, self.candidates, now=NOW)
        rollout.switch_stage(rollout.OFF, [], now=NOW + datetime.timedelta(hours=1))
        self.assertEqual(rollout.stage_of(), rollout.OFF)
        self.assertEqual(rollout.canary_members(), [])


class SwitchApiTest(unittest.IsolatedAsyncioTestCase):
    """Тот же путь через HTTP: отказ подготовки не оставляет включённый шаг."""

    async def test_endpoint_refuses_and_keeps_the_stage_off(self):
        import auth
        import config
        import scheduler_rollout as rollout_module
        import web.server as server
        from aiohttp.test_utils import TestServer
        from test_support import BrowserTestClient as TestClient
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patches = [patch.object(database, "DB_PATH", type(DB_PATH)(os.path.join(tmp.name, "prices.db"))),
                   patch("config.DATA_DIR", type(DB_PATH)(tmp.name)),
                   patch("config.SETTINGS_FILE", type(DB_PATH)(os.path.join(tmp.name, "settings.json")))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        database.init_db()
        database.upsert_telegram_user({"id": 9701, "first_name": "A"})
        token = database.create_session(9701)
        app = server.create_app()
        app.cleanup_ctx.clear()
        async with TestClient(TestServer(app)) as client:
            client.session.cookie_jar.update_cookies({auth.SESSION_COOKIE: token})
            with patch.object(auth, "ADMIN_TELEGRAM_IDS", {9701}):
                # Сбой подготовки: раньше шаг оставался включённым без снимка «до»
                with patch.object(database, "scheduler_candidates", side_effect=RuntimeError("база занята")):
                    res = await client.post("/api/admin/scheduler/rollout", json={"stage": "canary_one"})
                self.assertEqual(res.status, 500)
                self.assertEqual(config.load_settings()[rollout_module.SETTING_STAGE], "off")
                self.assertEqual(rollout_module.canary_members(), [])
                self.assertIsNone(rollout_module.baseline_metrics())
                # Прыжок через шаг по-прежнему отклоняется
                jump = await client.post("/api/admin/scheduler/rollout", json={"stage": "all"})
                self.assertEqual(jump.status, 400)
                self.assertEqual(config.load_settings()[rollout_module.SETTING_STAGE], "off")


class PermissionEdgeTest(RolloutStateTest):
    """M01: «никто не разрешён» и «неизвестно» — это не «разрешены все»."""

    def test_empty_allowed_list_means_nobody(self):
        with self.assertRaises(rollout.StageRefused) as refused:
            rollout.canary_shops(rollout.CANARY_ONE,
                                 [candidate("kaspi", sched.FRIENDLY), candidate("dns")], allowed=[])
        self.assertIn("ни один магазин", str(refused.exception))

    def test_all_shops_disabled_stops_a_new_step(self):
        with patch.object(rollout, "allowed_shops", return_value=[]):
            with self.assertRaises(rollout.StageRefused):
                rollout.switch_stage(rollout.CANARY_ONE, self.candidates, now=NOW)
        self.assertEqual(rollout.stage_of(), rollout.OFF)

    def test_disabling_the_last_member_is_visible(self):
        import config
        config.save_settings({**config.load_settings(), rollout.SETTING_STAGE: rollout.CANARY_ONE})
        with patch.object(rollout, "allowed_shops", return_value=["kaspi", "dns"]):
            rollout.start_stage(rollout.CANARY_ONE, self.candidates, now=NOW)
        with patch.object(rollout, "allowed_shops", return_value=[]):
            self.assertEqual(rollout.missing_members(self.candidates), ["kaspi"])
            state = rollout.status(self.candidates, now=NOW + datetime.timedelta(hours=1))
        self.assertEqual(state["missing_members"], ["kaspi"])
        self.assertFalse(state["ready_for_next"])

    def test_unknown_permissions_do_not_allow_everyone(self):
        with patch.object(rollout, "allowed_shops", return_value=None):
            with self.assertRaises(rollout.StageRefused) as refused:
                rollout.canary_shops(rollout.CANARY_ONE, self.candidates)
            self.assertIn("не удалось выяснить", str(refused.exception))

    def test_unknown_permissions_make_members_unobservable(self):
        import config
        config.save_settings({**config.load_settings(), rollout.SETTING_STAGE: rollout.CANARY_ONE})
        with patch.object(rollout, "allowed_shops", return_value=["kaspi", "dns"]):
            rollout.start_stage(rollout.CANARY_ONE, self.candidates, now=NOW)
        with patch.object(rollout, "allowed_shops", return_value=None):
            self.assertEqual(rollout.missing_members(self.candidates), ["kaspi"])

    def test_failure_of_the_lookup_is_reported_as_unknown(self):
        import web.server as server
        with patch.object(server, "enabled_shop_keys", side_effect=RuntimeError("настройки недоступны")):
            self.assertIsNone(rollout.allowed_shops())


class ConcurrentSwitchTest(RolloutStateTest):
    """M03: отказ одного перехода не должен стирать состояние другого, успевшего пройти."""

    def test_failed_switch_does_not_erase_a_parallel_successful_one(self):
        import threading
        self.http("kaspi", 200, 10, 2)
        self.scans("kaspi", 10, 9)
        reached, release = threading.Event(), threading.Event()
        original = rollout.prepare_stage

        def slow_prepare(stage, candidates, now=None):
            prepared = original(stage, candidates, now=now)
            reached.set()
            release.wait(5)
            raise RuntimeError("подготовка сорвалась")

        failure = {}

        def first():
            try:
                with patch.object(rollout, "prepare_stage", slow_prepare):
                    rollout.switch_stage(rollout.CANARY_ONE, self.candidates, now=NOW)
            except Exception as e:                      # отказ ожидаем
                failure["error"] = str(e)

        thread = threading.Thread(target=first)
        thread.start()
        self.assertTrue(reached.wait(5), "первый переход должен дойти до подготовки")
        # Второй переход проходит целиком, пока первый ещё не завершился
        second = threading.Thread(target=lambda: rollout.switch_stage(
            rollout.CANARY_ONE, self.candidates, now=NOW + datetime.timedelta(minutes=1)))
        second.start()
        second.join(10)
        release.set()
        thread.join(10)

        self.assertEqual(rollout.stage_of(), rollout.CANARY_ONE)
        self.assertEqual(rollout.canary_members(), ["kaspi"], "успешный переход не должен быть стёрт")
        self.assertIsNotNone(rollout.baseline_metrics())
        self.assertIsNotNone(rollout.started_at(NOW))

    def test_stale_preparation_cannot_overwrite_a_newer_state(self):
        rollout.switch_stage(rollout.CANARY_ONE, self.candidates, now=NOW)
        stale_version = rollout.state_version()
        rollout.switch_stage(rollout.OFF, [], now=NOW + datetime.timedelta(minutes=1))
        rollout.switch_stage(rollout.CANARY_ONE, self.candidates, now=NOW + datetime.timedelta(minutes=2))
        fresh_members = rollout.canary_members()
        # Запоздалая попытка записать состояние по старой версии отклоняется
        with self.assertRaises(rollout.StageRefused):
            rollout.start_stage(rollout.CANARY_ONE, self.candidates, now=NOW,
                                expected_version=stale_version)
        self.assertEqual(rollout.canary_members(), fresh_members)

    def test_automatic_rollback_skips_a_state_changed_in_parallel(self):
        self.http("kaspi", 200, 4, 0)
        self.scans("kaspi", 10, 10)
        rollout.switch_stage(rollout.CANARY_ONE, self.candidates, now=NOW)
        record = rollout.rollback("проверка", now=NOW, expected_version="999")
        self.assertIn("skipped", record)
        self.assertEqual(rollout.stage_of(), rollout.CANARY_ONE, "чужое состояние не тронуто")

    def test_version_grows_with_every_stored_transition(self):
        first = rollout.switch_stage(rollout.CANARY_ONE, self.candidates, now=NOW)["version"]
        rollout.switch_stage(rollout.OFF, [], now=NOW)
        second = rollout.switch_stage(rollout.CANARY_ONE, self.candidates, now=NOW)["version"]
        self.assertGreater(int(second), int(first))
