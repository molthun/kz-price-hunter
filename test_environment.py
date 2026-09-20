"""P14 Состояние окружения: пульс работников, выключенное против сломавшегося, версии без секретов."""
import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import datetime
import os
import tempfile
import unittest
from unittest.mock import patch

import database
import environment as env
import monitoring
from config import DB_PATH

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


class ComponentStateTest(unittest.TestCase):
    def last(self, minutes_ago, note="цикл"):
        return {"last_seen": (NOW - datetime.timedelta(minutes=minutes_ago)).isoformat(), "note": note}

    def test_recent_heartbeat_is_alive(self):
        state = env.component_state(env.TELEGRAM, self.last(1), enabled=True, now=NOW)
        self.assertEqual(state["state"], "alive")
        self.assertIn("назад", state["reason"])

    def test_missing_heartbeat_is_not_green(self):
        """Умерший работник при живом сайте не должен выглядеть «в норме»."""
        state = env.component_state(env.TELEGRAM, self.last(60), enabled=True, now=NOW)
        self.assertEqual(state["state"], "stale")
        self.assertIn("нет пульса", state["reason"])

    def test_never_seen_is_unknown_not_broken(self):
        state = env.component_state(env.SCHEDULER, None, enabled=True, now=NOW)
        self.assertEqual(state["state"], "unknown")
        self.assertIn("пульса ещё не было", state["reason"])

    def test_disabled_component_differs_from_failed(self):
        disabled = env.component_state(env.TELEGRAM, None, enabled=False, now=NOW)
        failed = env.component_state(env.TELEGRAM, self.last(600), enabled=True, now=NOW)
        self.assertEqual(disabled["state"], "disabled")
        self.assertIn("выключено", disabled["reason"])
        self.assertNotEqual(disabled["state"], failed["state"])

    def test_thresholds_match_the_worker_rhythm(self):
        """У каждого работника свой ритм: очередь Telegram молчит секунды, бэкапы — сутки."""
        self.assertLess(env.STALE_AFTER_SECONDS[env.TELEGRAM], env.STALE_AFTER_SECONDS[env.SCHEDULER])
        self.assertLess(env.STALE_AFTER_SECONDS[env.SCHEDULER], env.STALE_AFTER_SECONDS[env.BACKUP])
        # Бэкап раз в сутки не считается молчащим
        self.assertEqual(env.component_state(env.BACKUP, self.last(60 * 25), enabled=True, now=NOW)["state"],
                         "alive")


class VersionsTest(unittest.TestCase):
    def test_versions_are_actual_not_declared(self):
        import sys
        data = env.versions()
        self.assertEqual(data["python"], sys.version.split()[0])
        self.assertTrue(data["app"])
        self.assertTrue(data["sqlite"])
        self.assertIn("aiohttp", data)

    def test_git_sha_is_reported_when_known(self):
        with patch.dict(os.environ, {"GIT_SHA": "abc123def456"}):
            self.assertEqual(env.git_sha(), "abc123def456")

    def test_uptime_grows_from_process_start(self):
        self.assertGreaterEqual(env.uptime_seconds(), 0.0)

    def test_secrets_are_never_shown(self):
        """Видно только, настроено ли: сами ключи и токены не показываются никогда."""
        with patch("config.get_ai_config", return_value={
                "gemini_api_key": "AIzaSyD-секретный-ключ-который-нельзя-показывать",
                "openai_api_key": "sk-proj-секретный-ключ", "ai_provider": "auto"}), \
             patch("config.get_bot_token", return_value="123456:секретный-токен-бота"):
            view = env.safe_config_view()
        for field in ("gemini", "openai", "telegram_bot"):
            with self.subTest(field=field):
                self.assertNotRegex(str(view[field]), r"[A-Za-z0-9_-]{20,}")
        dump = " ".join(str(v) for v in view.values())
        self.assertNotIn("AIza", dump)
        self.assertNotIn("sk-proj", dump)
        self.assertNotIn("секретный", dump)
        self.assertEqual(view["gemini"], "ключ задан")
        self.assertEqual(view["telegram_bot"], "настроен")


class HeartbeatStorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [patch.object(database, "DB_PATH", type(DB_PATH)(os.path.join(self.tmp.name, "prices.db"))),
                        patch("config.DATA_DIR", type(DB_PATH)(self.tmp.name))]
        for p in self.patches:
            p.start()
        database.init_db()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def test_heartbeat_is_recorded_and_counted(self):
        env.heartbeat(env.SCHEDULER, "цикл 1", now=NOW)
        env.heartbeat(env.SCHEDULER, "цикл 2", now=NOW + datetime.timedelta(minutes=1))
        beat = database.heartbeats()[env.SCHEDULER]
        self.assertEqual(beat["beats"], 2)
        self.assertEqual(beat["note"], "цикл 2")

    def test_heartbeat_failure_never_breaks_the_worker(self):
        with patch.object(database, "get_connection", side_effect=RuntimeError("база занята")):
            env.heartbeat(env.SCHEDULER, "цикл")     # не должно бросить

    def test_section_marks_a_silent_worker(self):
        env.heartbeat(env.SCHEDULER, "цикл", now=NOW - datetime.timedelta(hours=3))
        env.heartbeat(env.TELEGRAM, "цикл", now=NOW)
        with patch.object(env, "enabled_components", return_value={c: True for c in env.COMPONENTS}):
            section = monitoring.environment_section(now=NOW)
        states = {c["component"]: c for c in section["components"]}
        self.assertEqual(states[env.SCHEDULER]["state"], "stale")
        self.assertEqual(states[env.SCHEDULER]["status"], monitoring.DEGRADED)
        self.assertEqual(states[env.TELEGRAM]["state"], "alive")
        self.assertEqual(section["status"], monitoring.DEGRADED)
        self.assertIn("планировщик", section["reason"])

    def test_disabled_component_does_not_spoil_the_status(self):
        for name in env.COMPONENTS:
            env.heartbeat(name, "цикл", now=NOW)
        enabled = {c: True for c in env.COMPONENTS}
        enabled[env.TELEGRAM] = False
        with patch.object(env, "enabled_components", return_value=enabled):
            section = monitoring.environment_section(now=NOW)
        states = {c["component"]: c for c in section["components"]}
        self.assertEqual(states[env.TELEGRAM]["state"], "disabled")
        self.assertEqual(states[env.TELEGRAM]["status"], monitoring.DISABLED)
        self.assertEqual(section["status"], monitoring.HEALTHY)

    def test_live_site_does_not_make_a_dead_worker_green(self):
        """Ни одного пульса при работающем сайте — это «неизвестно», а не «в норме»."""
        with patch.object(env, "enabled_components", return_value={c: True for c in env.COMPONENTS}):
            section = monitoring.environment_section(now=NOW)
        self.assertEqual(section["status"], monitoring.UNKNOWN)
        self.assertIn("Нет пульса", section["reason"])

    def test_restart_keeps_heartbeats_but_resets_uptime(self):
        env.heartbeat(env.SCHEDULER, "до перезапуска", now=NOW)
        with patch.object(env, "_STARTED_AT", __import__("time").time()):
            self.assertLess(env.uptime_seconds(), 5)
        self.assertIn(env.SCHEDULER, database.heartbeats())

    def test_section_hides_secrets(self):
        with patch.object(env, "enabled_components", return_value={c: True for c in env.COMPONENTS}):
            section = monitoring.environment_section(now=NOW)
        import json
        dump = json.dumps(section, ensure_ascii=False, default=str)
        self.assertNotIn("api_key", dump.lower())
        self.assertIn("versions", section)
        self.assertIn("не показываются", section["note"])


if __name__ == "__main__":
    unittest.main()
