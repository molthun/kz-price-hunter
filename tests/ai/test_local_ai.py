"""P17-A: локальная модель (Ollama) рядом с платными — порядок, запасной путь, паузы, учёт и вызов.

Решения владельца 29.10.2026 (docs/AI_LOCAL_POLICY.md): локальная первая для всех задач; не ответила, не поняла
или выключена — платная в рамках бюджета; дневной лимит локальная не расходует; машина семейная — кнопки
«AI вкл/выкл» на рабочем столе, автоматическое выключение при игре и включение через час (ai_mode.json).
"""
from tests import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import asyncio
import datetime
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestServer

import ai_router
import ai_service
import config
import database
import local_ai
from config import DB_PATH

DEPLOY = Path(__file__).resolve().parents[2] / "deploy" / "prod-host"


class _DbCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.patches = [patch.object(database, "DB_PATH", type(DB_PATH)(self.data / "prices.db")),
                        patch.object(config, "DATA_DIR", self.data),
                        patch.object(config, "SETTINGS_FILE", self.data / "settings.json")]
        for p in self.patches:
            p.start()
        database.init_db()
        ai_router._CACHE.clear()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()
        ai_router._CACHE.clear()

    def write_gpu(self, busy, age_seconds=10, reason="игра"):
        moment = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=age_seconds)
        (self.data / "gpu_state.json").write_text(json.dumps(
            {"at": moment.strftime("%Y-%m-%dT%H:%M:%SZ"), "busy": busy, "reason": reason}))


class ModeTest(_DbCase):
    def write_mode(self, mode, by="button"):
        (self.data / "ai_mode.json").write_text(json.dumps({"mode": mode, "since": "2026-10-29T12:00:00Z", "by": by}))

    def test_mode_file_drives_pause(self):
        self.assertTrue(local_ai.mode()["on"], "нет файла — включено")
        self.assertIsNone(local_ai.pause_reason())
        self.write_mode("off", "game")
        self.assertIn("видеокарту заняла игра", local_ai.pause_reason())
        self.write_mode("on", "auto")
        self.assertIsNone(local_ai.pause_reason())
        (self.data / "ai_mode.json").write_text("испорчен")
        self.assertTrue(local_ai.mode()["on"])

    def test_gpu_state_freshness(self):
        self.write_gpu(True, age_seconds=10)
        self.assertTrue(local_ai.gpu_state()["fresh"])
        self.write_gpu(True, age_seconds=600)
        st = local_ai.status()
        self.assertFalse(st["gpu"]["fresh"])
        self.assertIsNone(st["paused"], "занятость видеокарты сама не выключает — выключает режим")
        (self.data / "gpu_state.json").unlink()
        self.assertIn("не работает", local_ai.gpu_state()["reason"])


class ConfigTest(_DbCase):
    def test_local_only_counts_as_ai_and_validation(self):
        config.save_settings({"local_api_base": "http://192.168.28.25:11434/v1", "local_model": "qwen3-vl:8b-instruct"})
        with patch.object(config, "GEMINI_API_KEY", ""), patch.object(config, "OPENAI_API_KEY", ""):
            cfg = config.get_ai_config()
        self.assertTrue(cfg["has_local"])
        self.assertTrue(cfg["has_ai"])
        self.assertEqual(cfg["provider"], "local")
        for bad in ({"local_api_base": "ftp://x/v1"}, {"local_api_base": "http://u:p@host:1/v1"},
                    {"local_api_base": "http://h:1/v1", "local_model": "плохое имя"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                config.save_settings(bad)
        config.save_settings({"local_api_base": "", "ai_provider": "auto"})
        with self.assertRaises(ValueError):
            config.save_settings({"ai_provider": "local"})


class RouterLocalTest(_DbCase):
    def setUp(self):
        super().setUp()
        self.config = {"enabled": True, "has_ai": True, "ai_search_enabled": True, "ai_provider": "auto",
                       "gemini_api_key": "g-key", "openai_api_key": "o-key",
                       "openai_api_base": "https://api.openai.com/v1",
                       "gemini_model": "gemini-2.5-flash", "openai_model": "gpt-4o-mini",
                       "local_api_base": "http://ollama:11434/v1", "local_model": "qwen3-vl:8b-instruct",
                       "local_api_key": "", "has_local": True}
        self.calls = []

    def run_task(self, task="normalize", local=None, gemini=None, paused=None):
        def deliver(provider, value, model):
            self.calls.append(provider)
            sink = ai_service.usage_sink.get()
            if sink is not None:
                sink.append({"provider": provider, "outcome": "ok" if value else "empty", "model": model,
                             "input_tokens": 50, "output_tokens": 10})
            return value

        async def fake_local(prompt, base, model, key="", *, timeout=30, scan=False):
            return deliver("local", local, model)

        async def fake_gemini(prompt, key, *, timeout=30, scan=False, model=None):
            return deliver("gemini", gemini, "gemini-2.5-flash")

        async def fake_openai(prompt, key, base, *, timeout=30, scan=False, model=None):
            return deliver("openai", None, "gpt-4o-mini")

        with patch.object(ai_service, "call_local_api", fake_local), \
             patch.object(ai_service, "call_gemini_api", fake_gemini), \
             patch.object(ai_service, "call_openai_api", fake_openai), \
             patch.object(local_ai, "pause_reason", return_value=paused):
            return asyncio.run(ai_router.run(task, "текст", config=self.config))

    def rows(self):
        with database.get_connection() as conn:
            return [dict(r) for r in conn.execute("SELECT provider, outcome, requests, cost_usd, fallbacks FROM ai_usage")]

    def test_local_first_for_all_tasks_and_cost_zero(self):
        for task in ("normalize", "query_parse", "consultant", "category_classify"):
            with self.subTest(task=task):
                self.calls.clear()
                r = self.run_task(task, local={"ok": True})
                self.assertEqual((r["provider"], r["fallback_used"]), ("local", False))
                self.assertEqual(self.calls, ["local"])
        local_rows = [r for r in self.rows() if r["provider"] == "local"]
        self.assertTrue(local_rows)
        self.assertTrue(all(r["cost_usd"] == 0.0 for r in local_rows))

    def test_local_failure_falls_back_to_paid_even_for_internal_tasks(self):
        r = self.run_task("normalize", local=None, gemini={"ok": 1})
        self.assertEqual((r["provider"], r["fallback_used"]), ("gemini", True))
        self.assertEqual(self.calls, ["local", "gemini"])
        # Второго платного у фоновой задачи по-прежнему нет
        self.calls.clear()
        with self.assertRaises(ai_router.AIUnavailable):
            self.run_task("normalize", local=None, gemini=None)
        self.assertEqual(self.calls, ["local", "gemini"])

    def test_local_misunderstood_answer_goes_to_paid(self):
        """«Не поняла»: ответ есть, но не прошёл проверку задачи — платная берёт задачу."""
        def validate(data):
            return data if data.get("brand") else None

        async def fake_local(prompt, base, model, key="", *, timeout=30, scan=False):
            self.calls.append("local")
            return {"что-то": "не то"}

        async def fake_gemini(prompt, key, *, timeout=30, scan=False, model=None):
            self.calls.append("gemini")
            return {"brand": "Apple"}

        with patch.object(ai_service, "call_local_api", fake_local), \
             patch.object(ai_service, "call_gemini_api", fake_gemini), \
             patch.object(local_ai, "pause_reason", return_value=None):
            r = asyncio.run(ai_router.run("normalize", "текст", config=self.config, validate=validate))
        self.assertEqual((r["provider"], r["result"], r["fallback_used"]), ("gemini", {"brand": "Apple"}, True))
        self.assertIn(("local", "bad_response"), {(x["provider"], x["outcome"]) for x in self.rows()})

    def test_pause_skips_local_without_calling_it(self):
        r = self.run_task("normalize", local={"ok": 1}, gemini={"ok": 2}, paused="видеокарта занята: игра")
        self.assertEqual(r["provider"], "gemini")
        self.assertEqual(self.calls, ["gemini"])
        rows = self.rows()
        self.assertIn(("local", "local_paused"), {(x["provider"], x["outcome"]) for x in rows})
        usage = database.ai_usage(days=1)
        self.assertEqual(usage["totals"]["provider_calls"], 1, "пауза — не обращение к модели")
        self.assertEqual(usage["totals"]["errors"], 0)

    def test_paid_budget_exhausted_keeps_local(self):
        with patch.object(ai_router, "budget_allows", return_value=False):
            r = self.run_task("normalize", local={"ok": 1})
            self.assertEqual(r["provider"], "local")
            with self.assertRaises(ai_router.AIUnavailable):
                self.run_task("normalize", local=None, gemini={"ok": 1})
        self.assertNotIn("gemini", self.calls)

    def test_explicit_choice_has_no_silent_fallback(self):
        self.config["ai_provider"] = "local"
        with self.assertRaises(ai_router.AIUnavailable):
            self.run_task("query_parse", local=None, gemini={"ok": 1})
        self.assertEqual(self.calls, ["local"])
        self.calls.clear()
        self.config["ai_provider"] = "gemini"
        self.assertEqual(self.run_task("query_parse", local={"ok": 1}, gemini={"ok": 2})["provider"], "gemini")
        self.assertEqual(self.calls, ["gemini"])

    def test_without_local_nothing_changes(self):
        self.config.update(local_api_base="", local_model="", has_local=False)
        self.assertEqual(ai_router.providers_for("query_parse", self.config), ["gemini", "openai"])
        self.assertEqual(ai_router.providers_for("normalize", self.config), ["gemini"])


class LocalCallTest(_DbCase):
    """Настоящий HTTP-вызов к поддельному OpenAI-совместимому серверу."""

    def serve(self, handler, coro):
        async def main():
            app = web.Application()
            app.router.add_post("/v1/chat/completions", handler)
            app.router.add_get("/v1/models", lambda r: web.json_response({"data": [{"id": "qwen3-vl:8b-instruct"}]}))
            server = TestServer(app)
            await server.start_server()
            try:
                return await coro(str(server.make_url("/v1")))
            finally:
                await server.close()
        return asyncio.run(main())

    def test_payload_headers_budget_and_errors(self):
        seen = []

        async def ok(request):
            seen.append((await request.json(), request.headers.get("Authorization")))
            return web.json_response({"choices": [{"message": {"content": '{"brand": "Apple"}'}}],
                                      "usage": {"prompt_tokens": 40, "completion_tokens": 8}})

        before = ai_service.ai_calls_today()
        result = self.serve(ok, lambda base: ai_service.call_local_api("p", base, "qwen3-vl:8b-instruct", "", scan=True))
        self.assertEqual(result, {"brand": "Apple"})
        payload, auth = seen[0]
        self.assertEqual(payload["reasoning_effort"], "none")
        self.assertEqual(payload["model"], "qwen3-vl:8b-instruct")
        self.assertIsNone(auth, "ключ не задан — заголовка нет")
        self.serve(ok, lambda base: ai_service.call_local_api("p", base, "m", "secret"))
        self.assertEqual(seen[1][1], "Bearer secret")
        self.assertEqual(ai_service.ai_calls_today(), before, "локальная модель не расходует дневной лимит")

        async def boom(request):
            return web.Response(status=500, text="fail")

        async def not_json(request):
            return web.json_response({"choices": [{"message": {"content": "не JSON"}}]})

        for handler in (boom, not_json):
            with self.subTest(handler=handler.__name__):
                self.assertIsNone(self.serve(handler, lambda base: ai_service.call_local_api("p", base, "m")))
        self.assertIsNone(asyncio.run(ai_service.call_local_api("p", "http://127.0.0.1:9/v1", "m", timeout=1)))

    def test_one_request_at_a_time(self):
        active = {"now": 0, "max": 0}

        async def slow(request):
            active["now"] += 1
            active["max"] = max(active["max"], active["now"])
            await asyncio.sleep(0.2)
            active["now"] -= 1
            return web.json_response({"choices": [{"message": {"content": '{"ok": 1}'}}]})

        async def three(base):
            return await asyncio.gather(*(ai_service.call_local_api("p", base, "m") for _ in range(3)))

        results = self.serve(slow, three)
        self.assertEqual(results, [{"ok": 1}] * 3)
        self.assertEqual(active["max"], 1)

    def test_check_local_model(self):
        async def ok(request):
            return web.json_response({"choices": [{"message": {"content": '{"ok": true}'}}]})

        check = self.serve(ok, lambda base: ai_service.check_local_model(base, "qwen3-vl:8b-instruct"))
        self.assertTrue(check["model_present"] and check["answered"])
        missing = self.serve(ok, lambda base: ai_service.check_local_model(base, "другая"))
        self.assertFalse(missing["model_present"])


STUB = r'''#!/usr/bin/env python3
import json, os, sys, pathlib
name = pathlib.Path(sys.argv[0]).name
root = pathlib.Path(os.environ["CASE_ROOT"])
with open(root / "calls.jsonl", "a") as f:
    f.write(json.dumps([name] + sys.argv[1:]) + "\n")
if name == "nvidia-smi":
    used, ollama, util = (int(x) for x in os.environ["GPU"].split(","))
    if "--query-gpu=utilization.gpu,memory.used" in sys.argv:
        print(f"{util}, {used}")
    else:
        print("/usr/bin/gnome-shell, 138")
        if ollama:
            print(f"/usr/lib/ollama/llama-server, {ollama}")
elif name == "curl":
    url = next(a for a in sys.argv if a.startswith("http"))
    if url.endswith("/api/ps"):
        print(json.dumps({"models": [{"name": "qwen3-vl:8b-instruct"}]}))
'''


@unittest.skipUnless(shutil.which("bash"), "нужен bash")
class HostScriptsTest(unittest.TestCase):
    """ai_switch.sh (кнопки) и gpu_probe.sh (cron): выключение при игре, включение через час, загрузка модели."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "bin").mkdir()
        for cmd in ("nvidia-smi", "curl", "notify-send"):
            p = self.root / "bin" / cmd
            p.write_text(STUB)
            p.chmod(0o755)
        self.prod = self.root / "prod"
        self.stand = self.root / "stand"
        self.prod.mkdir()
        self.stand.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def env(self, gpu="500,0,3"):
        return dict(os.environ, CASE_ROOT=str(self.root), GPU=gpu,
                    PATH=str(self.root / "bin") + os.pathsep + os.environ["PATH"],
                    TARGETS=f"{self.prod} {self.stand}", OLLAMA_URL="http://ollama:11434")

    def run_sh(self, script, *args, gpu="500,0,3"):
        r = subprocess.run(["bash", str(DEPLOY / script), *args], env=self.env(gpu), capture_output=True,
                           text=True, errors="replace", timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def mode(self):
        return json.loads((self.prod / "ai_mode.json").read_text())

    def curl_payloads(self):
        path = self.root / "calls.jsonl"
        calls = [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []
        path.unlink(missing_ok=True)
        return [c[c.index("-d") + 1] for c in calls if c[0] == "curl" and "-d" in c]

    def backdate(self, minutes, last_busy_minutes=None):
        old = lambda m: (datetime.datetime.now(datetime.timezone.utc)
                         - datetime.timedelta(minutes=m)).strftime("%Y-%m-%dT%H:%M:%SZ")
        m = self.mode()
        m["since"] = old(minutes)
        (self.prod / "ai_mode.json").write_text(json.dumps(m))
        g = json.loads((self.prod / "gpu_state.json").read_text())
        g["last_busy_at"] = old(last_busy_minutes) if last_busy_minutes is not None else None
        (self.prod / "gpu_state.json").write_text(json.dumps(g))

    def test_buttons_switch_mode_and_model(self):
        out = self.run_sh("ai_switch.sh", "off")
        self.assertEqual(self.mode()["mode"], "off")
        self.assertEqual(self.mode(), json.loads((self.stand / "ai_mode.json").read_text()))
        self.assertIn('"keep_alive": 0', " ".join(self.curl_payloads()))
        self.assertIn("выключен", out)
        out = self.run_sh("ai_switch.sh", "on")
        self.assertEqual((self.mode()["mode"], self.mode()["by"]), ("on", "button"))
        self.assertIn('"keep_alive": -1', " ".join(self.curl_payloads()))

    def test_game_switches_off_and_resumes_after_an_hour_of_freedom(self):
        self.run_sh("ai_switch.sh", "on")
        self.curl_payloads()
        # Модель Ollama в памяти — не игра
        self.run_sh("gpu_probe.sh", gpu="6400,5900,95")
        self.assertEqual(self.mode()["mode"], "on")
        # Игра рядом с моделью: выключено автоматически, модель выгружена
        out = self.run_sh("gpu_probe.sh", gpu="9400,5900,99")
        self.assertEqual((self.mode()["mode"], self.mode()["by"]), ("off", "game"))
        self.assertIn('"keep_alive": 0', " ".join(self.curl_payloads()))
        self.assertIn("выключен", out)
        # Игру закрыли, но часа ещё нет — остаётся выключено
        self.run_sh("gpu_probe.sh", gpu="500,0,2")
        self.assertEqual(self.mode()["mode"], "off")
        # Час прошёл, но видеокарту занимали 3 минуты назад — ждём 10 минут свободы
        self.backdate(61, last_busy_minutes=3)
        self.run_sh("gpu_probe.sh", gpu="500,0,2")
        self.assertEqual(self.mode()["mode"], "off")
        # Час прошёл и 10 минут свободно — включается сам и загружает модель
        self.backdate(61, last_busy_minutes=11)
        self.run_sh("gpu_probe.sh", gpu="500,0,2")
        self.assertEqual((self.mode()["mode"], self.mode()["by"]), ("on", "auto"))
        self.assertIn('"keep_alive": -1', " ".join(self.curl_payloads()))

    def test_button_off_waits_an_hour_even_without_games(self):
        self.run_sh("ai_switch.sh", "off")
        self.run_sh("gpu_probe.sh", gpu="500,0,1")
        self.assertEqual(self.mode()["mode"], "off")
        self.backdate(61)
        self.run_sh("gpu_probe.sh", gpu="500,0,1")
        self.assertEqual(self.mode()["mode"], "on")

    def test_on_without_loaded_model_reloads_it(self):
        self.run_sh("ai_switch.sh", "on")
        self.curl_payloads()
        out = self.run_sh("gpu_probe.sh", gpu="500,0,1")
        self.assertIn('"keep_alive": -1', " ".join(self.curl_payloads()))
        self.assertIn("загружена заново", out)
        state = json.loads((self.prod / "gpu_state.json").read_text())
        self.assertFalse(state["busy"])

    def test_load_without_ollama_model_is_foreign(self):
        self.run_sh("ai_switch.sh", "on")
        self.run_sh("gpu_probe.sh", gpu="900,0,80")
        self.assertEqual(self.mode()["mode"], "off")


class AdminEndpointsTest(_DbCase):
    """Ключ локальной модели не уходит наружу; проверка подключения и состояние — только администратору."""

    def test_admin_endpoints(self):
        from aiohttp.test_utils import TestServer as Srv
        from tests.test_support import BrowserTestClient
        from database import upsert_telegram_user, create_session
        import web.server as server

        config.save_settings({"local_api_base": "http://127.0.0.1:9/v1", "local_model": "qwen3-vl:8b-instruct",
                              "local_api_key": "fake-local-secret"})
        upsert_telegram_user({"id": 987, "first_name": "Admin"})

        async def fake_check(base, model, key=""):
            fake_check.args = (base, model, key)
            return {"models": [model], "error": None, "model_present": True, "answered": True, "latency_ms": 5}

        async def main():
            app = server.create_app()
            app.cleanup_ctx.clear()
            with patch("auth.ADMIN_TELEGRAM_IDS", {987}), patch.object(server, "get_bot_username", return_value=None), \
                 patch.object(ai_service, "check_local_model", fake_check):
                async with BrowserTestClient(Srv(app)) as client:
                    anon = await client.get("/api/admin/ai/local/status")
                    client.session.cookie_jar.update_cookies({"kzph_session": create_session(987)})
                    cfg = await client.get("/api/admin/config")
                    body = await cfg.text()
                    status = await (await client.get("/api/admin/ai/local/status")).json()
                    check = await client.post("/api/admin/ai/local/check",
                                              json={"api_base": "http://192.168.28.25:11434/v1",
                                                    "model": "qwen3-vl:8b-instruct", "api_key": "***"})
                    bad = await client.post("/api/admin/ai/local/check", json={"api_base": "file:///etc", "model": "m"})
                    return anon.status, cfg.status, body, status, check.status, await check.json(), bad.status

        anon, cfg_status, body, status, check_status, check, bad = asyncio.run(main())
        self.assertIn(anon, (401, 403))
        self.assertEqual(cfg_status, 200)
        self.assertNotIn("fake-local-secret", body)
        self.assertIn("paused", status)
        self.assertEqual(check_status, 200)
        self.assertNotIn("fake-local-secret", json.dumps(check))
        self.assertTrue(check["check"]["answered"])
        # Маска из формы заменяется сохранённым ключом, адрес и модель — из формы
        self.assertEqual(fake_check.args, ("http://192.168.28.25:11434/v1", "qwen3-vl:8b-instruct", "fake-local-secret"))
        self.assertEqual(bad, 400)


if __name__ == "__main__":
    unittest.main()
