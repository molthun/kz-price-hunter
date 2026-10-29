from tests import test_support  # noqa: F401
"""Регрессии повторной приёмки P17-E: скрипты прода на машине стенда и учёт доставки копий.

P17E-02 — сбой доставки копии на другой диск виден приложению и мониторингу, а старые копии не удаляются
до проверки новой. P17E-03 — verify_public.sh не сообщает успех при неработающем поиске и неверных
именах контейнеров. P17E-01 — restore_prod.sh разворачивает сервис из набора «база + конфигурация»,
репетиция изолирована, а настоящий запуск не начинается, пока отвечает старый прод.

Скрипты запускаются настоящим bash; docker, rsync, curl, stat, df и sleep подменены заглушками.
"""
import datetime
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
import database

DEPLOY = Path(__file__).resolve().parents[2] / "deploy" / "prod-host"
BASH = shutil.which("bash")

STUB = r'''#!/usr/bin/env python3
import json, os, pathlib, shutil, sqlite3, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
root = pathlib.Path(os.environ["CASE_ROOT"])
fault = os.environ.get("FAULT", "")
log = root / "calls.jsonl"
with open(log, "a") as f:
    f.write(json.dumps([name] + args) + "\n")

def arg_after(flag):
    return args[args.index(flag) + 1] if flag in args else None

if name == "sleep":
    sys.exit(0)

if name == "stat":
    fmt, path = args[1], pathlib.Path(args[-1])
    if fmt == "%d":
        # Разные устройства для данных и цели, кроме случая «диск не смонтирован»
        print(1 if fault == "same_device" or "data" in path.parts else 2)
    else:
        if not path.exists():
            sys.exit(1)
        print(path.stat().st_size)
    sys.exit(0)

if name == "df":
    print("Avail")
    print(0 if fault == "no_space" else 10 ** 12)
    sys.exit(0)

if name == "rsync":
    if fault == "rsync_fail":
        sys.exit(23)
    src, dst = pathlib.Path(args[-2]), pathlib.Path(args[-1].rstrip("/"))
    target = dst / src.name
    shutil.copyfile(src, target)
    if fault == "corrupt" and src.suffix == ".db":
        data = bytearray(target.read_bytes())
        data[100:4096] = b"\xff" * (4096 - 100)   # тот же размер, испорченные страницы
        target.write_bytes(bytes(data))
    sys.exit(0)

if name == "curl":
    url = next(a for a in reversed(args) if a.startswith("http"))
    if "/api/live" in url:
        sys.exit(0 if fault == "old_alive" else 7)
    if "/api/products" in url:
        if fault == "search_timeout":
            sys.exit(28)
        code, body = {"search500": (500, "Internal Server Error"),
                      "search_bad_json": (200, "<html>not json</html>")}.get(
            fault, (200, json.dumps({"products": [{"id": "p1"}], "total": 1})))
        out = arg_after("-o")
        if out:
            pathlib.Path(out).write_text(body)
        if "-w" in args:
            print(f"{code} 0.05", end="")
        sys.exit(0)
    if "/api/version" in url:
        print(json.dumps({"version": "6.5.2" if fault != "version_mismatch" or "127.0.0.1" in url else "6.5.1"}))
    elif "/api/health" in url:
        print(json.dumps({"status": "ok"}))
    sys.exit(0)

if name == "docker":
    known = os.environ.get("KNOWN", "kz-price-hunter kz-price-watchtower").split()
    if fault == "container_down" and args[0] == "exec":
        sys.exit(1)
    if args[0] == "ps":
        flt = arg_after("--filter") or ""
        wanted = flt.split("=", 1)[1].strip("^$") if "=" in flt else ""
        if wanted in known:
            print("Up 1 hour")
        sys.exit(0)
    if args[0] == "logs":
        sys.exit(0)
    if args[0] == "inspect":
        print("ghcr.io/molthun/kz-price-hunter:latest")
        sys.exit(0)
    if args[0] == "exec":
        rest = args[2:]
        if rest[:1] == ["cat"]:
            print(json.dumps({"enabled_shops": ["kaspi"], "gemini_api_key": "secret"}))
            sys.exit(0)
        if rest[:2] == ["sh", "-c"]:
            sys.exit(0)
        code = rest[2] if len(rest) > 2 else ""
        if "backup_database" in code:
            backups = root / "kzph-prod" / "data" / "backups"
            backups.mkdir(parents=True, exist_ok=True)
            p = backups / "prices-nightly-20991231T000000000000Z-abc123.db"
            p.unlink(missing_ok=True)
            c = sqlite3.connect(p)
            c.execute("CREATE TABLE products (id TEXT)")
            c.executemany("INSERT INTO products VALUES (?)", [(str(i),) for i in range(500)])
            c.commit()
            c.close()
            print("/app/data/backups/" + p.name)
            sys.exit(0)
        if "record_backup_delivery" in code:
            with open(root / "deliveries.jsonl", "a") as f:
                f.write(json.dumps(rest[3:], ensure_ascii=False) + "\n")
            sys.exit(0)
        if "/proc/net/tcp" in code:
            print("192.168.28.10")
            sys.exit(0)
        sys.exit(0)
    if args[0] == "run":
        vol = arg_after("-v")
        host = vol.split(":/b")[0]
        if "quick_check" in args[-2] and "count(*)" in args[-2]:
            print("ok 3 5 0")
            sys.exit(0)
        try:
            c = sqlite3.connect("file:" + host + "/" + args[-1] + "?immutable=1", uri=True)
            print(c.execute("PRAGMA quick_check").fetchone()[0])
        except sqlite3.DatabaseError as e:
            print("database disk image is malformed")
        sys.exit(0)
    sys.exit(0)
'''


@unittest.skipUnless(BASH, "нужен bash")
class _ScriptCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for cmd in ("docker", "rsync", "stat", "df", "curl", "sleep"):
            p = self.bin / cmd
            p.write_text(STUB)
            p.chmod(0o755)

    def tearDown(self):
        self.tmp.cleanup()

    def run_script(self, script, *args, fault="", env=None, cwd=None):
        e = dict(os.environ, CASE_ROOT=str(self.root), FAULT=fault,
                 PATH=str(self.bin) + os.pathsep + os.environ["PATH"], LC_ALL="C.UTF-8")
        e.update(env or {})
        return subprocess.run([BASH, str(script), *args], env=e, capture_output=True, text=True, errors="replace",
                              cwd=cwd or self.root, timeout=120)

    def deliveries(self):
        path = self.root / "deliveries.jsonl"
        return [json.loads(l) for l in path.read_text().splitlines()] if path.exists() else []


class BackupDeliveryScriptTest(_ScriptCase):
    def setUp(self):
        super().setUp()
        self.prod = self.root / "kzph-prod"
        (self.prod / "data" / "backups").mkdir(parents=True)
        self.target = self.root / "target"
        self.target.mkdir()
        shutil.copy(DEPLOY / "backup_to_nas.sh", self.prod / "backup_to_nas.sh")
        (self.prod / "backup.conf").write_text(
            f"CONTAINER=kz-price-hunter\nBACKUP_TARGET={self.target}\nKEEP_LOCAL=2\nKEEP_REMOTE=14\n")
        (self.prod / "compose.yaml").write_text("services: {}\n")
        (self.prod / ".env").write_text("TELEGRAM_BOT_TOKEN=x\n")
        c = sqlite3.connect(self.prod / "data" / "prices.db")
        c.execute("CREATE TABLE t (x)")
        c.commit()
        c.close()
        # 16 прежних ночных копий: при сбое ни одна не должна пропасть
        for i in range(16):
            old = self.target / f"prices-nightly-2026010{i % 10}T00{i:02d}00Z-old{i:02d}.db"
            old.write_bytes(b"old")
            os.utime(old, (1_700_000_000 + i, 1_700_000_000 + i))

    def run_backup(self, fault=""):
        return self.run_script(self.prod / "backup_to_nas.sh", fault=fault)

    def old_copies(self):
        return len(list(self.target.glob("prices-nightly-*old*.db")))

    def test_healthy_delivery_verifies_target_and_records_ok(self):
        r = self.run_backup()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        new = self.target / "prices-nightly-20991231T000000000000Z-abc123.db"
        self.assertTrue(new.exists())
        conf = self.target / "kzph-config-20991231T000000000000Z-abc123.tar"
        self.assertTrue(conf.exists())
        self.assertEqual(stat.S_IMODE(conf.stat().st_mode), 0o600)
        with tarfile.open(conf) as t:
            self.assertEqual(sorted(t.getnames()), [".env", "compose.yaml", "settings.json"])
        self.assertEqual(len(list(self.target.glob("prices-nightly-*.db"))), 14)  # хранение после проверки
        (last,) = self.deliveries()[-1:]
        self.assertEqual(last[0], "ok")
        self.assertIn("quick_check", last[4])

    def test_failures_record_failed_and_keep_old_copies(self):
        cases = {
            "rsync_fail": "перенос",
            "corrupt": "проверка конечного файла",
            "same_device": "проверка цели",
            "no_space": "мало места",
        }
        for fault, marker in cases.items():
            with self.subTest(fault=fault):
                for f in self.target.glob("*abc123*"):
                    f.unlink()
                (self.root / "deliveries.jsonl").unlink(missing_ok=True)
                r = self.run_backup(fault)
                self.assertNotEqual(r.returncode, 0, fault)
                self.assertNotIn("готово", r.stdout)
                (last,) = self.deliveries()[-1:]
                self.assertEqual(last[0], "failed", fault)
                self.assertIn(marker, last[4])
                self.assertEqual(self.old_copies(), 16, "старые копии удалены до проверки новой")
                self.assertFalse(any(self.target.glob("kzph-config-*abc123*")))

    def test_same_device_stops_before_making_a_copy(self):
        self.run_backup("same_device")
        self.assertFalse(any((self.prod / "data" / "backups").glob("prices-nightly-*")))

    def test_container_down_fails_and_says_result_not_recorded(self):
        r = self.run_backup("container_down")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("не удалось записать итог доставки", r.stdout)
        self.assertEqual(self.old_copies(), 16)


class VerifyPublicScriptTest(_ScriptCase):
    def run_verify(self, fault="", **env):
        return self.run_script(DEPLOY / "verify_public.sh", fault=fault, env=env)

    def test_healthy_actual_names_pass(self):
        r = self.run_verify()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ИТОГ: прод работает", r.stdout)

    def test_any_failure_gives_nonzero_exit(self):
        for fault in ("search500", "search_bad_json", "search_timeout", "version_mismatch"):
            with self.subTest(fault=fault):
                r = self.run_verify(fault)
                self.assertEqual(r.returncode, 1, r.stdout)
                self.assertNotIn("ИТОГ: прод работает", r.stdout)

    def test_wrong_container_names_fail(self):
        r = self.run_verify(CONTAINER="kzph-prod", WATCHTOWER="kzph-prod-watchtower")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("kzph-prod", r.stdout)


class RestoreScriptTest(_ScriptCase):
    def setUp(self):
        super().setUp()
        self.backups = self.root / "backups"
        self.backups.mkdir()
        self.db = self.backups / "prices-nightly-20991231T000000000000Z-abc123.db"
        c = sqlite3.connect(self.db)
        c.execute("CREATE TABLE products (id TEXT)")
        c.commit()
        c.close()
        bundle = self.root / "bundle"
        bundle.mkdir()
        (bundle / "compose.yaml").write_text("services:\n  app:\n    ports: ['35540:35539']\n")
        (bundle / ".env").write_text("TELEGRAM_BOT_TOKEN=prod-token\nDIAG_TOKEN=prod-diag\nPUBLIC_ORIGIN=https://x\n")
        (bundle / "settings.json").write_text(json.dumps(
            {"enabled_shops": ["kaspi"], "gemini_api_key": "g", "openai_api_key": "o", "telegram_bot_token": "t",
             "daily_digest_telegram_enabled": True}))
        self.config = self.backups / "kzph-config-20991231T000000000000Z-abc123.tar"
        with tarfile.open(self.config, "w") as t:
            for n in ("compose.yaml", ".env", "settings.json"):
                t.add(bundle / n, arcname=n)

    def restore(self, *args, fault=""):
        return self.run_script(DEPLOY / "restore_prod.sh", "--from", str(self.backups), *args, fault=fault)

    def test_rehearsal_prepares_isolated_copy_without_secrets(self):
        to = self.root / "restore"
        r = self.restore("--to", str(to), "--rehearsal")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((to / "data" / "prices.db").exists())
        self.assertTrue((to / "compose.yaml.orig").exists())
        compose = (to / "compose.yaml").read_text()
        self.assertIn("internal: true", compose)
        self.assertNotIn("ports", compose)
        env = (to / ".env").read_text()
        self.assertNotIn("prod-token", env)
        self.assertNotIn("prod-diag", env)
        self.assertIn("PUBLIC_ORIGIN=https://x", env)
        s = json.loads((to / "data" / "settings.json").read_text())
        self.assertEqual((s["gemini_api_key"], s["openai_api_key"], s["telegram_bot_token"]), ("", "", ""))
        self.assertFalse(s["daily_digest_telegram_enabled"])
        self.assertEqual(s["enabled_shops"], ["kaspi"])
        self.assertEqual(stat.S_IMODE((to / ".env").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((to / "data" / "settings.json").stat().st_mode), 0o600)
        self.assertNotIn("prod-token", r.stdout + r.stderr)

    def test_real_restore_keeps_config_and_refuses_start_while_old_prod_answers(self):
        to = self.root / "restore"
        r = self.restore("--to", str(to), "--start", "--old-url", "http://127.0.0.1:35540", fault="old_alive")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("старый прод", r.stderr)
        self.assertIn("prod-token", (to / ".env").read_text())  # настоящий прод — со своим ботом
        calls = (self.root / "calls.jsonl").read_text()
        self.assertNotIn('"compose"', calls, "контейнер запущен при живом старом проде")

    def test_refuses_existing_data_and_incomplete_bundle(self):
        to = self.root / "restore"
        (to / "data").mkdir(parents=True)
        (to / "data" / "prices.db").write_bytes(b"live")
        r = self.restore("--to", str(to))
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual((to / "data" / "prices.db").read_bytes(), b"live")
        with tarfile.open(self.config, "w") as t:
            t.add(self.db, arcname="compose.yaml")
        r = self.restore("--to", str(self.root / "other"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("неполный", r.stderr)


class DeliveryMonitoringTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        self.patches = [patch.object(config, "DATA_DIR", self.data_dir),
                        patch.object(database, "DB_PATH", self.data_dir / "prices.db")]
        for p in self.patches:
            p.start()
        database.init_db()
        self.now = datetime.datetime(2026, 10, 30, 12, 0, tzinfo=datetime.timezone.utc)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def section(self):
        import monitoring
        return monitoring.backup_section(now=self.now)

    def test_not_configured_does_not_change_status(self):
        import monitoring
        s = self.section()
        self.assertFalse(s["delivery"]["configured"])
        self.assertNotIn("Доставка", s["reason"])
        self.assertEqual(monitoring.delivery_state(self.now)["status"], monitoring.UNKNOWN)

    def test_failed_or_stale_delivery_degrades_even_with_fresh_local_copy(self):
        import monitoring
        database.backup_database("nightly")  # свежая копия рядом с базой
        database.record_backup_delivery("ok", "/srv/storage/kzph-backups", "a.db", 100, "ok",
                                        now=self.now - datetime.timedelta(hours=2))
        self.assertEqual(monitoring.delivery_state(self.now)["status"], monitoring.HEALTHY)
        database.record_backup_delivery("failed", "/srv/storage/kzph-backups", "b.db", None, "перенос: rsync",
                                        now=self.now - datetime.timedelta(hours=1))
        s = self.section()
        self.assertEqual(s["status"], monitoring.DEGRADED)
        self.assertIn("rsync", s["reason"])
        # Пропущенный запуск: последняя успешная доставка старше суток, новых записей нет
        database.record_backup_delivery("ok", "/srv/storage/kzph-backups", "c.db", 100, "ok",
                                        now=self.now - datetime.timedelta(hours=30))
        later = self.now + datetime.timedelta(hours=1)
        with database.get_connection() as conn:
            conn.execute("DELETE FROM backup_deliveries WHERE outcome = 'failed'")
            conn.execute("DELETE FROM backup_deliveries WHERE file = 'a.db'")
            conn.commit()
        state = monitoring.delivery_state(later)
        self.assertEqual(state["status"], monitoring.DEGRADED)
        self.assertIn("ч", state["reason"])


if __name__ == "__main__":
    unittest.main()
