"""P16 Шлюз выката: секреты, уязвимости зависимостей, обновление базы, smoke по образу."""
import test_support  # noqa: F401  isolates DATA_DIR; must precede project imports
import asyncio
import datetime
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import database
import release_gate as gate
from config import DB_PATH

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 9, 23, 12, 0, tzinfo=UTC)


class SecretScanTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, name, text):
        path = os.path.join(self.tmp.name, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_real_looking_key_blocks_release(self):
        self.write("config_local.py", 'GEMINI = "AIza' + "b" * 35 + '"\n')
        report = gate.check_secrets(self.tmp.name)
        self.assertFalse(report["ok"])
        self.assertEqual(report["findings"][0]["kind"], "Google/Gemini API key")

    def test_secret_value_is_not_printed_in_the_report(self):
        """Отчёты пересылают: сам ключ в них попадать не должен."""
        secret = "AIza" + "c" * 35
        self.write("leak.py", f'KEY = "{secret}"\n')
        report = gate.check_secrets(self.tmp.name)
        self.assertNotIn(secret, json.dumps(report))
        self.assertIn("символов", report["findings"][0]["preview"])

    def test_bot_token_and_private_key_are_found(self):
        self.write("notes.md", "токен 123456789:" + "A" * 35 + "\n")
        self.write("key.pem", "-----BEGIN " + "PRIVATE KEY" + "-----\nabc\n")
        kinds = {f["kind"] for f in gate.check_secrets(self.tmp.name)["findings"]}
        self.assertEqual(kinds, {"Telegram bot token", "Приватный ключ"})

    def test_placeholder_values_do_not_block(self):
        """M08: заглушкой считается само значение, а не слово «example» где-то в строке."""
        self.write("README.md", 'Пример: GEMINI_API_KEY="AIzaSy' + "x" * 33 + '"\n')
        self.write("env.sample", 'TELEGRAM_BOT_TOKEN=<your_token_here>\n')
        self.assertTrue(gate.check_secrets(self.tmp.name)["ok"])

    def test_word_example_next_to_a_real_key_does_not_excuse_it(self):
        self.write("README.md", 'Пример: GEMINI_API_KEY="AIza' + "d" * 35 + '" (example)\n')
        report = gate.check_secrets(self.tmp.name)
        self.assertFalse(report["ok"], "настоящий ключ остаётся ключом рядом со словом «пример»")

    def test_dependencies_and_binaries_are_skipped(self):
        self.write("node_modules/pkg/index.js", 'const k = "AIza' + "e" * 35 + '";\n')
        self.write("venv/lib/x.py", 'K = "AIza' + "f" * 35 + '"\n')
        self.assertTrue(gate.check_secrets(self.tmp.name)["ok"])

    def test_workflow_files_are_scanned(self):
        """M08: .github не должен выпадать из проверки — он попадает в репозиторий."""
        import subprocess
        subprocess.run(["git", "init", "-q", self.tmp.name], check=True, capture_output=True)
        self.write(".github/workflows/publish.yml", "env:\n  TOKEN: ghp_" + "b" * 36 + "\n")
        subprocess.run(["git", "-C", self.tmp.name, "add", "-A"], check=True, capture_output=True)
        report = gate.check_secrets(self.tmp.name)
        self.assertFalse(report["ok"])
        self.assertEqual(report["findings"][0]["file"], ".github/workflows/publish.yml")

    def test_this_repository_has_no_secrets(self):
        """Настоящая проверка: в самом репозитории ключей быть не должно."""
        report = gate.check_secrets(os.path.dirname(os.path.abspath(__file__)))
        self.assertTrue(report["ok"], f"найдено: {report['findings']}")


class DependencyAuditTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def report(self, vulns):
        path = os.path.join(self.tmp.name, "audit.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"dependencies": [{"name": "aiohttp", "version": "3.9.0", "vulns": vulns}]}, fh)
        return path

    def allowlist(self, entries):
        path = os.path.join(self.tmp.name, "allow.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"allow": entries}, fh)
        return path

    def test_vulnerability_blocks_publication(self):
        result = gate.check_dependencies(self.report([{"id": "GHSA-1111", "fix_versions": ["3.9.2"]}]),
                                         self.allowlist([]), now=NOW)
        self.assertFalse(result["ok"])
        self.assertEqual(result["blocking"][0]["package"], "aiohttp")

    def test_clean_report_passes(self):
        self.assertTrue(gate.check_dependencies(self.report([]), self.allowlist([]), now=NOW)["ok"])

    def test_allowlisted_with_reason_and_date_passes(self):
        allow = self.allowlist([{"id": "GHSA-1111", "reason": "не затрагивает наш путь", "until": "2026-12-01"}])
        result = gate.check_dependencies(self.report([{"id": "GHSA-1111"}]), allow, now=NOW)
        self.assertTrue(result["ok"])
        self.assertEqual(result["allowed"][0]["reason"], "не затрагивает наш путь")

    def test_expired_allowance_blocks_again(self):
        """Пропуск не превращается в вечное молчание."""
        allow = self.allowlist([{"id": "GHSA-1111", "reason": "ждём релиз", "until": "2026-01-01"}])
        result = gate.check_dependencies(self.report([{"id": "GHSA-1111"}]), allow, now=NOW)
        self.assertFalse(result["ok"])
        self.assertTrue(result["expired"])

    def test_allowance_without_reason_or_date_blocks(self):
        for entry in ({"id": "GHSA-1111"}, {"id": "GHSA-1111", "reason": "просто так"}):
            with self.subTest(entry=entry):
                result = gate.check_dependencies(self.report([{"id": "GHSA-1111"}]),
                                                 self.allowlist([entry]), now=NOW)
                self.assertFalse(result["ok"])

    def test_skipped_package_is_not_a_clean_result(self):
        """M09: «не смогли проверить» — это не «проверено и чисто»."""
        path = os.path.join(self.tmp.name, "audit.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"dependencies": [{"name": "demo", "version": "1.0",
                                         "skip_reason": "Could not audit package"}]}, fh)
        result = gate.check_dependencies(path, self.allowlist([]), now=NOW)
        self.assertFalse(result["ok"])
        self.assertEqual(result["skipped"][0]["package"], "demo")

    def test_package_without_a_vulnerability_list_is_treated_as_unchecked(self):
        path = os.path.join(self.tmp.name, "audit.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"dependencies": [{"name": "demo", "version": "1.0"}]}, fh)
        self.assertFalse(gate.check_dependencies(path, self.allowlist([]), now=NOW)["ok"])

    def test_empty_or_foreign_report_does_not_pass(self):
        for payload in ({}, {"dependencies": []}, [], {"результат": "ок"},
                        {"dependencies": ["aiohttp"]}):
            with self.subTest(payload=payload):
                path = os.path.join(self.tmp.name, "audit.json")
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh)
                self.assertFalse(gate.check_dependencies(path, self.allowlist([]), now=NOW)["ok"])

    def test_unreadable_report_blocks_instead_of_passing(self):
        result = gate.check_dependencies(os.path.join(self.tmp.name, "нет.json"), self.allowlist([]), now=NOW)
        self.assertFalse(result["ok"])
        self.assertIn("не прочитан", result["error"])

    def test_repository_allowlist_is_valid_and_bounded(self):
        for entry in gate.load_allowlist(gate.ALLOWLIST_FILE):
            with self.subTest(entry=entry.get("id")):
                self.assertTrue(entry.get("reason"))
                datetime.date.fromisoformat(str(entry.get("until")))


class UpgradeRehearsalTest(unittest.TestCase):
    """База предыдущего релиза должна подниматься новым кодом — или потеря данных названа вслух."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = type(DB_PATH)(self.tmp.name)
        self.patches = [patch.object(database, "DB_PATH", self.data_dir / "prices.db"),
                        patch("config.DATA_DIR", self.data_dir),
                        patch("config.SETTINGS_FILE", self.data_dir / "settings.json")]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        database.init_db()
        self.db = str(self.data_dir / "prices.db")

    def test_previous_release_database_upgrades_and_works(self):
        result = gate.upgrade_rehearsal(self.db)
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(result["integrity"], "ok")
        self.assertTrue(all(s["ok"] for s in result["smoke"]))
        self.assertEqual(result["schema_after"], database.SCHEMA_VERSION)

    def test_unchanged_schema_is_stated_as_necessary_not_sufficient(self):
        """M07: равенство схемы не выдаётся за доказательство работоспособности прежнего кода."""
        result = gate.upgrade_rehearsal(self.db)
        self.assertTrue(result["rollback_compatible"])
        self.assertIn("не доказательство", result["rollback_note"])
        self.assertIn("rollback_check", result["rollback_note"])

    def test_grown_schema_names_the_data_loss_plainly(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE schema_metadata SET value = ? WHERE name = 'schema_version'",
                         (str(database.SCHEMA_VERSION - 1),))
            conn.commit()
        result = gate.upgrade_rehearsal(self.db)
        self.assertFalse(result["rollback_compatible"])
        self.assertIn("теряются все данные", result["rollback_note"])

    def test_broken_database_does_not_pass_as_upgraded(self):
        broken = os.path.join(self.tmp.name, "broken.db")
        with open(broken, "wb") as fh:
            fh.write("это не база данных".encode("utf-8"))
        result = gate.upgrade_rehearsal(broken)
        self.assertFalse(result["ok"])

    def test_missing_database_is_reported(self):
        result = gate.upgrade_rehearsal(os.path.join(self.tmp.name, "нет.db"))
        self.assertFalse(result["ok"])
        self.assertIn("не найдена", result["error"])

    def test_rehearsal_does_not_touch_the_original_file(self):
        before = os.stat(self.db)
        gate.upgrade_rehearsal(self.db)
        after = os.stat(self.db)
        self.assertEqual((before.st_size, round(before.st_mtime, 3)),
                         (after.st_size, round(after.st_mtime, 3)))


class WalSnapshotTest(unittest.TestCase):
    """M07: подтверждённая транзакция в журнале WAL обязана попасть в копию."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = type(DB_PATH)(self.tmp.name)
        for p in [patch.object(database, "DB_PATH", self.data_dir / "prices.db"),
                  patch("config.DATA_DIR", self.data_dir),
                  patch("config.SETTINGS_FILE", self.data_dir / "settings.json")]:
            p.start()
            self.addCleanup(p.stop)
        database.init_db()
        self.db = str(self.data_dir / "prices.db")

    def write_into_wal(self):
        """Запись, подтверждённая, но оставленная в журнале: checkpoint отключён, соединение открыто."""
        conn = sqlite3.connect(self.db)
        self.addCleanup(conn.close)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("INSERT OR REPLACE INTO schema_metadata (name, value) VALUES ('wal_sentinel', '1')")
        conn.commit()
        return conn

    def test_committed_wal_record_is_in_the_rehearsed_copy(self):
        self.write_into_wal()
        seen = {}
        original = gate._snapshot

        def spy(source, target):
            original(source, target)
            with sqlite3.connect(target) as copy:
                row = copy.execute("SELECT value FROM schema_metadata WHERE name = 'wal_sentinel'").fetchone()
            seen["sentinel"] = row[0] if row else None

        with patch.object(gate, "_snapshot", spy):
            result = gate.upgrade_rehearsal(self.db)
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(seen["sentinel"], "1", "запись из журнала WAL должна быть в копии")

    def test_source_database_is_not_modified_by_the_snapshot(self):
        self.write_into_wal()
        before = os.stat(self.db)
        target = os.path.join(self.tmp.name, "copy.db")
        gate._snapshot(self.db, target)
        after = os.stat(self.db)
        self.assertEqual(before.st_size, after.st_size)
        with sqlite3.connect(target) as copy:
            self.assertEqual(copy.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_previous_code_is_checked_on_the_upgraded_copy(self):
        """M07: обещание отката подтверждается запуском прежнего кода, а не равенством номера схемы."""
        repo = os.path.dirname(os.path.abspath(__file__))
        result = gate.rollback_check(self.db, repo)
        self.assertTrue(result["ok"], result.get("error"))
        self.assertTrue(all(c["ok"] for c in result["checks"]))

    def test_missing_previous_code_is_reported_not_assumed_fine(self):
        result = gate.rollback_check(self.db, os.path.join(self.tmp.name, "нет-кода"))
        self.assertFalse(result["ok"])
        self.assertIn("не найден", result["error"])


class ImageSmokeTest(unittest.IsolatedAsyncioTestCase):
    """Отвечающий контейнер — ещё не работающий сервис."""

    async def asyncSetUp(self):
        from aiohttp import web as aioweb
        self.responses = {
            "/api/version": (200, json.dumps({"version": "5.12.0"})),
            "/api/stats": (200, "{}"),
            "/": (200, "<html></html>"),
            "/api/admin/monitoring": (401, "{}"),
            "/api/admin/assistant": (403, "{}"),      # отказ из-за проверки источника запроса — тоже отказ
            "/api/admin/monitoring/daily": (401, "{}"),
        }

        async def handler(request):
            status, body = self.responses.get(request.path, (404, "{}"))
            return aioweb.Response(status=status, text=body, content_type="application/json")

        app = aioweb.Application()
        app.router.add_route("*", "/{tail:.*}", handler)
        self.runner = aioweb.AppRunner(app)
        await self.runner.setup()
        self.site = aioweb.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        port = self.runner.addresses[0][1]
        self.url = f"http://127.0.0.1:{port}"
        self.addAsyncCleanup(self.runner.cleanup)

    async def test_healthy_image_passes(self):
        result = await gate.smoke(self.url)
        self.assertTrue(result["ok"], result["checks"])

    async def test_any_refusal_for_closed_sections_is_accepted(self):
        self.responses["/api/admin/monitoring"] = (403, "{}")
        self.assertTrue((await gate.smoke(self.url))["ok"])

    async def test_open_admin_endpoint_fails_the_smoke(self):
        self.responses["/api/admin/monitoring"] = (200, json.dumps({"secret": "данные"}))
        result = await gate.smoke(self.url)
        self.assertFalse(result["ok"])

    async def test_leaked_secret_in_response_fails_the_smoke(self):
        self.responses["/api/version"] = (200, json.dumps({"version": "5.12.0",
                                                           "api_key": "AIzaSyD-xxxxxxxxxxxxxxxxxxxx"}))
        result = await gate.smoke(self.url)
        self.assertFalse(result["ok"])
        self.assertIn("секрет", result["checks"][0]["error"])

    async def test_missing_version_field_fails_the_smoke(self):
        self.responses["/api/version"] = (200, "{}")
        result = await gate.smoke(self.url)
        self.assertFalse(result["ok"])

    async def test_dead_image_fails_instead_of_hanging(self):
        await self.runner.cleanup()
        result = await gate.smoke(self.url, timeout=2.0)
        self.assertFalse(result["ok"])


class CommandLineTest(unittest.TestCase):
    """Провал проверки должен останавливать конвейер, а не печатать предупреждение."""

    def test_exit_code_is_nonzero_on_failure(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with open(os.path.join(tmp.name, "leak.py"), "w", encoding="utf-8") as fh:
            fh.write('K = "AIza' + "g" * 35 + '"\n')
        with patch("sys.stdout"):
            self.assertEqual(gate.main(["secrets", "--root", tmp.name]), 1)
            self.assertEqual(gate.main(["upgrade", os.path.join(tmp.name, "нет.db")]), 1)

    def test_exit_code_is_zero_when_clean(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with patch("sys.stdout"):
            self.assertEqual(gate.main(["secrets", "--root", tmp.name]), 0)


class WorkflowTest(unittest.TestCase):
    """Публикация образа должна зависеть от обязательных проверок — это видно в самом workflow."""

    def workflow(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            ".github", "workflows", "docker-publish.yml")
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()

    def test_publication_depends_on_every_required_job(self):
        text = self.workflow()
        needs = [line for line in text.splitlines() if "needs:" in line]
        self.assertTrue(needs, "у публикации должны быть обязательные проверки")
        required = ("test", "frontend", "secret-scan", "dependency-audit", "upgrade-rehearsal",
                    "image-smoke")
        joined = " ".join(needs)
        for job in required:
            with self.subTest(job=job):
                self.assertIn(job, joined)

    def test_every_required_job_is_defined(self):
        text = self.workflow()
        for job in ("secret-scan:", "dependency-audit:", "upgrade-rehearsal:", "image-smoke:"):
            with self.subTest(job=job):
                self.assertIn(f"  {job}", text)

    def test_rollback_is_rehearsed_with_the_previous_release_code(self):
        self.assertIn("release_gate.py rollback", self.workflow())

    def test_image_is_smoke_tested_before_it_is_pushed(self):
        text = self.workflow()
        self.assertIn("release_gate.py smoke", text)
        self.assertLess(text.index("release_gate.py smoke"), text.index("push: true"),
                        "smoke должен идти до публикации")


if __name__ == "__main__":
    unittest.main()


class UpgradeThenRollbackTest(unittest.TestCase):
    """M07: откат проверяется на ОБНОВЛЁННОЙ базе, иначе он ничего про обновление не доказывает."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = type(DB_PATH)(self.tmp.name)
        for p in [patch.object(database, "DB_PATH", self.data_dir / "prices.db"),
                  patch("config.DATA_DIR", self.data_dir),
                  patch("config.SETTINGS_FILE", self.data_dir / "settings.json")]:
            p.start()
            self.addCleanup(p.stop)
        database.init_db()
        self.db = str(self.data_dir / "prices.db")
        self.upgraded = os.path.join(self.tmp.name, "upgraded.db")

    def old_code(self, query):
        """Каталог «прежнего релиза»: свои SMOKE_QUERIES и свой доступ к базе."""
        code = os.path.join(self.tmp.name, "prev-release")
        os.makedirs(code, exist_ok=True)
        with open(os.path.join(code, "backup_health.py"), "w", encoding="utf-8") as fh:
            fh.write(f"SMOKE_QUERIES = ((\"запрос прежнего кода\", {query!r}),)\n")
        with open(os.path.join(code, "database.py"), "w", encoding="utf-8") as fh:
            fh.write("import pathlib, sqlite3\n"
                     "DB_PATH = pathlib.Path('prices.db')\n"
                     "def get_connection():\n"
                     "    conn = sqlite3.connect(str(DB_PATH))\n"
                     "    conn.row_factory = sqlite3.Row\n"
                     "    return conn\n")
        return code

    def test_upgrade_keeps_the_upgraded_copy_for_the_rollback_check(self):
        result = gate.upgrade_rehearsal(self.db, keep_path=self.upgraded)
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(result["upgraded_copy"], self.upgraded)
        self.assertTrue(os.path.exists(self.upgraded), "обновлённая база должна остаться для проверки")
        with sqlite3.connect(self.upgraded) as conn:
            version = conn.execute("SELECT value FROM schema_metadata WHERE name='schema_version'").fetchone()
        self.assertEqual(int(version[0]), database.SCHEMA_VERSION)

    def test_source_database_is_left_alone(self):
        before = os.stat(self.db)
        gate.upgrade_rehearsal(self.db, keep_path=self.upgraded)
        after = os.stat(self.db)
        self.assertEqual((before.st_size, round(before.st_mtime, 3)),
                         (after.st_size, round(after.st_mtime, 3)))

    def test_previous_code_passes_on_the_upgraded_database(self):
        gate.upgrade_rehearsal(self.db, keep_path=self.upgraded)
        result = gate.rollback_check(self.upgraded, self.old_code("SELECT id FROM products LIMIT 1"))
        self.assertTrue(result["ok"], result.get("error"))

    def test_a_change_the_old_code_cannot_read_fails_this_sequence(self):
        """Приёмка Codex: несовместимость обязана валить именно последовательность upgrade → rollback."""
        gate.upgrade_rehearsal(self.db, keep_path=self.upgraded)
        broken = self.old_code("SELECT колонка_которой_нет FROM products LIMIT 1")
        result = gate.rollback_check(self.upgraded, broken)
        self.assertFalse(result["ok"], "запрос прежнего кода не работает — это и есть провал отката")

    def test_workflow_runs_the_rollback_on_the_upgraded_database(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            ".github", "workflows", "docker-publish.yml")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("upgrade prev-data/prices.db --keep upgraded.db", text)
        self.assertIn("rollback upgraded.db prev-release", text)
