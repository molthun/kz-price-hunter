"""Мелкий долг аудита: L03 (настройки), M07 (устойчивые id), L01 (тексты без устаревших чисел)."""
import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import json
import os
import re
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

_TMP = tempfile.TemporaryDirectory()
os.environ["DATA_DIR"] = _TMP.name

import config  # noqa: E402

ROOT = Path(__file__).resolve().parent


class SettingsFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "settings.json"
        patcher = patch.object(config, "SETTINGS_FILE", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_atomic_write_private_mode_and_no_temp_leftovers(self):
        config.save_settings({"check_interval_seconds": 3600})
        self.assertEqual(json.loads(self.path.read_text())["check_interval_seconds"], 3600)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["settings.json"])

    def test_failed_write_keeps_previous_file(self):
        config.save_settings({"check_interval_seconds": 3600})
        before = self.path.read_text()
        with patch.object(config.json, "dump", side_effect=RuntimeError("disk full")):
            with self.assertRaises(RuntimeError):
                config.save_settings({"check_interval_seconds": 7200})
        self.assertEqual(self.path.read_text(), before)
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["settings.json"])

    def test_concurrent_saves_do_not_lose_keys(self):
        self.path.write_text(json.dumps({"legacy_a": 1}))

        def save(i):
            config.save_settings({"check_interval_seconds": 3600 + i})

        threads = [threading.Thread(target=save, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        data = json.loads(self.path.read_text())
        self.assertEqual(data["legacy_a"], 1)
        self.assertIn(data["check_interval_seconds"], range(3600, 3610))


class OpenAiBaseTest(unittest.TestCase):
    def test_settings_base_used_when_env_empty(self):
        with patch.object(config, "OPENAI_API_BASE", ""), \
             patch.object(config, "load_settings", return_value={**config.load_settings(), "openai_api_base": "https://gw.example/v1"}):
            self.assertEqual(config.get_ai_config()["openai_api_base"], "https://gw.example/v1")

    def test_explicit_env_overrides_settings(self):
        with patch.object(config, "OPENAI_API_BASE", "https://env.example/v1"), \
             patch.object(config, "load_settings", return_value={**config.load_settings(), "openai_api_base": "https://gw.example/v1"}):
            self.assertEqual(config.get_ai_config()["openai_api_base"], "https://env.example/v1")

    def test_default_env_is_empty(self):
        if "OPENAI_API_BASE" in os.environ:
            self.skipTest("OPENAI_API_BASE задан в окружении разработчика")
        self.assertEqual(config.OPENAI_API_BASE, "")


class StableIdTest(unittest.TestCase):
    def test_slug_id(self):
        from scrapers.base import slug_id
        self.assertEqual(slug_id("alser", "/smartfon-vivo-y21d/"), "alser_smartfon-vivo-y21d")
        common = "noutbuk-apple-macbook-air-13-m3-2024-16gb-512gb-"
        self.assertNotEqual(slug_id("alser", common + "midnight-mxcv3"), slug_id("alser", common + "starlight-mxcu3"))
        long_id = slug_id("alser", "x" * 300)
        self.assertLessEqual(len(long_id), len("alser_") + 80)
        self.assertEqual(long_id, slug_id("alser", "x" * 300))  # детерминирован


class NoHardcodedShopCountsTest(unittest.TestCase):
    """Число магазинов меняется: в интерфейсе и боте оно вычисляется, а не пишется текстом."""

    def test_ui_and_bot_texts(self):
        pattern = re.compile(r"\b\d{2}\s+(?:магазин|сет|торгов)")
        for path in (ROOT / "web/templates/index.html", ROOT / "telegram_bot.py", ROOT / "ai_service.py"):
            hits = [m.group(0) for m in pattern.finditer(path.read_text(encoding="utf-8"))]
            self.assertEqual(hits, [], path.name)


class TestDataIsolationTest(unittest.TestCase):
    """Тесты не должны писать в рабочую prices.db: DATA_DIR фиксируется первым импортом config."""

    def test_every_test_module_imports_test_support_first(self):
        import ast
        for path in sorted(ROOT.glob("test_*.py")):
            if path.name == "test_support.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            first = next(n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom)))
            names = [a.name for a in first.names] if isinstance(first, ast.Import) else [first.module]
            self.assertEqual(names, ["test_support"], f"{path.name}: первым импортом должен быть test_support")

    def test_db_path_is_outside_repo(self):
        import database
        for db_path in (config.DB_PATH, database.DB_PATH, config.SETTINGS_FILE):
            self.assertNotEqual(Path(db_path).resolve().parent, ROOT, db_path)


if __name__ == "__main__":
    unittest.main()
