"""P16. Шлюз выката: проверки, без которых образ не публикуется.

Здесь собраны те проверки цепочки, которых не было в тестах: поиск секретов в исходниках, разбор отчёта
о уязвимостях зависимостей, репетиция обновления базы предыдущего релиза и smoke по работающему контейнеру.

Принцип тот же, что и во всём проекте: провал проверки — это провал, а не предупреждение. Каждая функция
возвращает отчёт с полем `ok`, а командная строка — ненулевой код возврата, чтобы конвейер остановился.

Исключения не прячутся молча: пропуск известной уязвимости оформляется списком с причиной и датой, после
которой пропуск перестаёт действовать. Бессрочных исключений здесь нет.
"""
from __future__ import annotations

import argparse
import datetime
import fnmatch
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from typing import Any, Dict, List, Optional, Sequence

# ---------------------------------------------------------------------------
# Поиск секретов
# ---------------------------------------------------------------------------

SKIP_DIRS = {".git", "venv", ".venv", "node_modules", "__pycache__", "backups", "certs",
             "data", "dist", "build", ".mypy_cache", ".pytest_cache"}
SKIP_SUFFIXES = (".db", ".db-wal", ".db-shm", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf",
                 ".zip", ".gz", ".woff", ".woff2", ".pyc", ".lock")
MAX_SCAN_BYTES = 2_000_000

# Узкие шаблоны настоящих ключей: широкое «password» дало бы шум, на который перестают смотреть.
SECRET_PATTERNS = (
    ("Google/Gemini API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("OpenAI API key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("Telegram bot token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_\-]{35}\b")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Приватный ключ", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
)

# Заглушка распознаётся по самому значению, а не по строке вокруг него: слово «example» в комментарии
# рядом не должно оправдывать настоящий ключ (M08).
PLACEHOLDER = re.compile(r"(?i)(example|placeholder|dummy|fake|your[_-]?key|x{4,}|012345|abcdef0123)")


def _git_tracked(root: str) -> Optional[List[str]]:
    """Файлы, которые хранит git. Именно они попадают в репозиторий и в образ.

    Обход каталогов пропускал всё, что начинается с точки, а значит и `.github` — токен в workflow
    вообще не проверялся (M08). Список от самого git такой дыры не оставляет.
    """
    import subprocess
    try:
        res = subprocess.run(["git", "-C", root, "ls-files", "-z"],
                             capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if res.returncode != 0:
        return None
    return [os.path.join(root, name) for name in res.stdout.split("\0") if name]


def _scan_files(root: str) -> List[str]:
    """Что проверять: отслеживаемые git-файлы, а вне репозитория — обход каталогов.

    Скрытые каталоги больше не пропускаются скопом: исключаются только служебные, названные поимённо.
    """
    candidates = _git_tracked(root)
    if candidates is None:
        candidates = []
        for base, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and d != ".git"]
            candidates.extend(os.path.join(base, name) for name in files)
    found: List[str] = []
    for path in candidates:
        parts = set(os.path.relpath(path, root).split(os.sep))
        if parts & SKIP_DIRS or ".git" in parts:
            continue
        if path.endswith(SKIP_SUFFIXES):
            continue
        try:
            if os.path.islink(path) or os.path.getsize(path) > MAX_SCAN_BYTES:
                continue
        except OSError:
            continue
        found.append(path)
    return sorted(found)


def _git_ignored(root: str, paths: List[str]) -> set:
    """Файлы, которые git не хранит (например, локальный settings.json с ключом владельца).

    Шлюз проверяет то, что попадает в репозиторий и в образ. Локальные файлы вне репозитория — забота
    владельца, а не повод останавливать выкат; если это не git-репозиторий, не пропускается ничего.
    """
    import subprocess
    if not paths:
        return set()
    try:
        res = subprocess.run(["git", "-C", root, "check-ignore", "--stdin"],
                             input="\n".join(paths), capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return set()
    if res.returncode not in (0, 1):
        return set()
    return {line.strip() for line in res.stdout.splitlines() if line.strip()}


def secret_findings(root: str = ".") -> List[Dict[str, Any]]:
    """Ищет в исходниках то, что выглядит как настоящий ключ. Примеры и заглушки не считаются."""
    findings: List[Dict[str, Any]] = []
    files = _scan_files(root)
    ignored = _git_ignored(root, files)
    for path in files:
        if path in ignored:
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                for number, line in enumerate(fh, start=1):
                    if len(line) > 4000:
                        continue
                    for label, pattern in SECRET_PATTERNS:
                        match = pattern.search(line)
                        if not match or PLACEHOLDER.search(match.group(0)):
                            continue
                        findings.append({
                            "file": os.path.relpath(path, root), "line": number, "kind": label,
                            # Сам секрет в отчёт не попадает: отчёты читают и пересылают
                            "preview": match.group(0)[:4] + "…" + str(len(match.group(0))) + " символов",
                        })
        except OSError:
            continue
    return findings


def check_secrets(root: str = ".") -> Dict[str, Any]:
    findings = secret_findings(root)
    return {"ok": not findings, "findings": findings,
            "note": "Ключи и токены не должны попадать в репозиторий даже в комментариях"}


# ---------------------------------------------------------------------------
# Уязвимости зависимостей
# ---------------------------------------------------------------------------

ALLOWLIST_FILE = os.path.join("docs", "dependency_allowlist.json")


def load_allowlist(path: str = ALLOWLIST_FILE) -> List[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    entries = data.get("allow") if isinstance(data, dict) else data
    return [e for e in (entries or []) if isinstance(e, dict)]


def check_dependencies(report_path: str, allowlist_path: str = ALLOWLIST_FILE,
                       now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Разбирает отчёт pip-audit (JSON) и решает, можно ли публиковать.

    Пропуск действует, только если у него есть причина и дата окончания, и она ещё не наступила:
    «известная уязвимость» не должна превращаться в вечное молчание.
    """
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    try:
        with open(report_path, "r", encoding="utf-8") as fh:
            report = json.load(fh)
    except (OSError, ValueError) as e:
        return {"ok": False, "blocking": [], "allowed": [], "expired": [],
                "error": f"Отчёт о зависимостях не прочитан: {type(e).__name__}"}

    packages = report.get("dependencies") if isinstance(report, dict) else report
    # Отчёт обязан быть понятной структурой: пустой или неожиданный JSON — это отсутствие проверки,
    # а не её успех. В конвейере код выхода pip-audit подавляется, поэтому судит только этот разбор (M09).
    if not isinstance(packages, list) or not packages:
        return {"ok": False, "blocking": [], "allowed": [], "expired": [], "skipped": [],
                "error": "Отчёт о зависимостях пуст или не той формы: проверка не выполнена"}

    allow = {str(e.get("id") or "").upper(): e for e in load_allowlist(allowlist_path)}
    blocking, allowed, expired, skipped = [], [], [], []
    for pkg in packages or []:
        if not isinstance(pkg, dict):
            return {"ok": False, "blocking": [], "allowed": [], "expired": [], "skipped": [],
                    "error": "Отчёт о зависимостях повреждён: элемент списка не является пакетом"}
        name = pkg.get("name") or pkg.get("package")
        version = pkg.get("version")
        # Пакет, который не смогли проверить, — не «чистый»: о нём просто ничего не известно
        reason = pkg.get("skip_reason") or pkg.get("skipped") or pkg.get("error")
        if reason:
            skipped.append({"package": name, "version": version, "why": str(reason)[:200]})
            continue
        if "vulns" not in pkg and "vulnerabilities" not in pkg:
            skipped.append({"package": name, "version": version,
                            "why": "в отчёте нет списка уязвимостей для этого пакета"})
            continue
        for vuln in pkg.get("vulns") or pkg.get("vulnerabilities") or []:
            vid = str(vuln.get("id") or "").upper()
            item = {"id": vid, "package": name, "version": version,
                    "fix_versions": vuln.get("fix_versions") or []}
            rule = allow.get(vid)
            if not rule:
                blocking.append(item)
                continue
            until = str(rule.get("until") or "")
            reason = str(rule.get("reason") or "").strip()
            try:
                deadline = datetime.datetime.fromisoformat(until).replace(tzinfo=datetime.timezone.utc)
            except ValueError:
                blocking.append({**item, "why": "у пропуска нет годной даты окончания"})
                continue
            if not reason:
                blocking.append({**item, "why": "у пропуска не указана причина"})
            elif deadline < moment:
                expired.append({**item, "until": until, "reason": reason})
            else:
                allowed.append({**item, "until": until, "reason": reason})
    return {"ok": not blocking and not expired and not skipped,
            "blocking": blocking, "allowed": allowed, "expired": expired, "skipped": skipped,
            "note": "Пропуск уязвимости действует только с причиной и до указанной даты; "
                    "непроверенная зависимость считается непроверенной, а не безопасной"}


# ---------------------------------------------------------------------------
# Репетиция обновления базы предыдущего релиза
# ---------------------------------------------------------------------------

def _schema_version(path: str) -> Optional[int]:
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            row = conn.execute("SELECT value FROM schema_metadata WHERE name = 'schema_version'").fetchone()
        return int(row[0]) if row and row[0] is not None else None
    except (sqlite3.Error, TypeError, ValueError):
        return None


def _snapshot(source_path: str, target_path: str) -> None:
    """Согласованная копия базы через SQLite Backup API.

    Обычное копирование файла берёт только основной файл: подтверждённые транзакции, которые ещё лежат
    в журнале WAL, в копию не попадают, а проверка целостности такой потери не замечает (M07).
    Backup API копирует базу целиком, вместе с журналом, и не трогает исходный файл.
    """
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    try:
        target = sqlite3.connect(target_path)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


def rollback_check(db_path: str, code_dir: str, python_exe: Optional[str] = None,
                   timeout: float = 120.0) -> Dict[str, Any]:
    """Проверяет, что ПРЕЖНИЙ код действительно работает на обновлённой базе.

    Равенство версии схемы — условие необходимое, но не достаточное: старое приложение могло опираться
    на что-то ещё (M07). Поэтому запросы приложения выполняются кодом из указанного каталога, то есть
    тем самым, на который предлагается откатиться. Проверка идёт на копии; исходный файл не трогается.
    """
    import subprocess
    if not os.path.isdir(code_dir):
        return {"ok": False, "error": f"Каталог прежнего кода не найден: {code_dir}"}
    workdir = tempfile.mkdtemp(prefix="rollback-check-")
    target = os.path.join(workdir, "prices.db")
    script = (
        "import json, os, sys\n"
        "sys.path.insert(0, os.getcwd())\n"
        "import backup_health, database\n"
        "database.DB_PATH = type(database.DB_PATH)(os.environ['ROLLBACK_DB'])\n"
        "checks = []\n"
        "with database.get_connection() as conn:\n"
        "    for label, query in backup_health.SMOKE_QUERIES:\n"
        "        try:\n"
        "            conn.execute(query).fetchall()\n"
        "            checks.append({'check': label, 'ok': True})\n"
        "        except Exception as e:\n"
        "            checks.append({'check': label, 'ok': False, 'error': f'{type(e).__name__}: {e}'})\n"
        "print(json.dumps(checks, ensure_ascii=False))\n")
    try:
        _snapshot(db_path, target)
        env = dict(os.environ, ROLLBACK_DB=target, DATA_DIR=workdir)
        res = subprocess.run([python_exe or sys.executable, "-c", script], cwd=code_dir, env=env,
                             capture_output=True, text=True, timeout=timeout)
        if res.returncode != 0:
            return {"ok": False, "error": f"Прежний код не смог открыть базу: {res.stderr.strip()[-400:]}"}
        line = [l for l in res.stdout.splitlines() if l.startswith("[")]
        checks = json.loads(line[-1]) if line else []
        return {"ok": bool(checks) and all(c["ok"] for c in checks), "checks": checks,
                "note": "Запросы приложения выполнены кодом прежнего релиза на обновлённой копии"}
    except Exception as e:                       # noqa: BLE001 — отчёт важнее трассировки
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def upgrade_rehearsal(db_path: str) -> Dict[str, Any]:
    """Берёт базу предыдущего релиза, обновляет её новым кодом и проверяет, что с ней можно работать.

    Ответ на два вопроса владельца: поднимется ли новый образ на старой базе и можно ли будет вернуться
    на старый образ. Возврат возможен, только если версия схемы не выросла; иначе честно описывается,
    что придётся восстанавливать копию и какие данные будут потеряны.
    """
    import backup_health
    if not os.path.exists(db_path):
        return {"ok": False, "error": f"База не найдена: {db_path}"}

    before = _schema_version(db_path)
    workdir = tempfile.mkdtemp(prefix="upgrade-rehearsal-")
    target = os.path.join(workdir, "prices.db")
    result: Dict[str, Any] = {"ok": False, "schema_before": before, "schema_after": None,
                              "smoke": [], "integrity": None, "rollback_compatible": None}
    try:
        _snapshot(db_path, target)
        import config
        import database
        saved = (database.DB_PATH, config.DATA_DIR)
        database.DB_PATH = type(config.DATA_DIR)(target)
        config.DATA_DIR = type(config.DATA_DIR)(workdir)
        try:
            database.init_db()
            after = _schema_version(target)
        finally:
            database.DB_PATH, config.DATA_DIR = saved

        result["schema_after"] = after
        with sqlite3.connect(target) as conn:
            conn.row_factory = sqlite3.Row
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            result["integrity"] = integrity
            for label, query in backup_health.SMOKE_QUERIES:
                try:
                    conn.execute(query).fetchall()
                    result["smoke"].append({"check": label, "ok": True})
                except sqlite3.Error as e:
                    result["smoke"].append({"check": label, "ok": False, "error": str(e)})

        expected = int(database.SCHEMA_VERSION)
        result["schema_expected"] = expected
        smoke_ok = all(s["ok"] for s in result["smoke"])
        result["rollback_compatible"] = (before is not None and after == before)
        result["rollback_note"] = (
            "Схема не изменилась, поэтому прежний образ такую базу откроет. Это необходимое условие, "
            "но не доказательство: работу прежнего кода подтверждает отдельная проверка rollback_check."
            if result["rollback_compatible"] else
            f"Схема выросла {before} → {after}: прежний образ такую базу не поднимет. Возврат — только "
            "восстановлением согласованной копии, при этом теряются все данные, накопленные после неё.")
        result["ok"] = (integrity == "ok" and smoke_ok and after == expected)
        if not result["ok"] and not result.get("error"):
            result["error"] = ("Обновление не прошло: "
                               + ("целостность нарушена; " if integrity != "ok" else "")
                               + ("запросы приложения не работают; " if not smoke_ok else "")
                               + (f"версия схемы {after}, ожидалась {expected}" if after != expected else ""))
        return result
    except Exception as e:                       # noqa: BLE001 — отчёт важнее трассировки
        result["error"] = f"{type(e).__name__}: {e}"
        return result
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Smoke по работающему образу
# ---------------------------------------------------------------------------

# Закрытым разделам годится любой отказ (401 без входа, 403 при проверке источника запроса) —
# важно, что данные не отдаются. Открытые разделы должны отвечать именно 200.
SMOKE_ENDPOINTS = (
    {"path": "/api/version", "expect": [200], "must_contain": ["version"]},
    {"path": "/api/stats", "expect": [200]},
    {"path": "/", "expect": [200]},
    {"path": "/api/admin/monitoring", "expect": [401, 403]},
    {"path": "/api/admin/assistant", "expect": [401, 403], "method": "POST"},
    {"path": "/api/admin/monitoring/daily", "expect": [401, 403]},
)

SECRET_LEAK = re.compile(r"(?i)(api[_-]?key|bot[_-]?token|secret)\"?\s*[:=]\s*\"[^\"]{8,}")


async def smoke(base_url: str, timeout: float = 10.0) -> Dict[str, Any]:
    """Проверяет поднятый образ: отвечает ли он, закрыты ли админские данные, не светит ли секреты."""
    import aiohttp
    checks: List[Dict[str, Any]] = []
    base = base_url.rstrip("/")
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
        for item in SMOKE_ENDPOINTS:
            url = base + item["path"]
            allowed = item["expect"]
            entry: Dict[str, Any] = {"check": item["path"], "expected": allowed}
            try:
                method = item.get("method", "GET")
                async with session.request(method, url, allow_redirects=False,
                                           json={} if method == "POST" else None) as res:
                    body = await res.text()
                    entry["status"] = res.status
                    entry["ok"] = res.status in allowed
                    for word in item.get("must_contain", []):
                        if word not in body:
                            entry["ok"] = False
                            entry["error"] = f"в ответе нет поля {word}"
                    if SECRET_LEAK.search(body):
                        entry["ok"] = False
                        entry["error"] = "в ответе видно похожее на секрет значение"
            except Exception as e:                # noqa: BLE001 — недоступность это тоже результат
                entry["ok"] = False
                entry["error"] = f"{type(e).__name__}: {e}"
            checks.append(entry)
    return {"ok": all(c["ok"] for c in checks), "checks": checks,
            "note": "Отвечающий контейнер — ещё не работающий сервис: проверяются и закрытые разделы"}


# ---------------------------------------------------------------------------
# Командная строка
# ---------------------------------------------------------------------------

def _print(report: Dict[str, Any]) -> int:
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report.get("ok") else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Проверки перед публикацией образа (P16)")
    sub = parser.add_subparsers(dest="command", required=True)
    p_secrets = sub.add_parser("secrets", help="поиск ключей и токенов в исходниках")
    p_secrets.add_argument("--root", default=".")
    p_deps = sub.add_parser("deps", help="разбор отчёта pip-audit с учётом списка пропусков")
    p_deps.add_argument("report")
    p_deps.add_argument("--allowlist", default=ALLOWLIST_FILE)
    p_upgrade = sub.add_parser("upgrade", help="репетиция обновления базы предыдущего релиза")
    p_upgrade.add_argument("db")
    p_rollback = sub.add_parser("rollback", help="проверка прежнего кода на обновлённой базе")
    p_rollback.add_argument("db")
    p_rollback.add_argument("code_dir")
    p_rollback.add_argument("--python", default=None)
    p_smoke = sub.add_parser("smoke", help="smoke по работающему образу")
    p_smoke.add_argument("url")
    args = parser.parse_args(argv)

    if args.command == "secrets":
        return _print(check_secrets(args.root))
    if args.command == "deps":
        return _print(check_dependencies(args.report, args.allowlist))
    if args.command == "upgrade":
        return _print(upgrade_rehearsal(args.db))
    if args.command == "rollback":
        return _print(rollback_check(args.db, args.code_dir, args.python))
    import asyncio
    return _print(asyncio.run(smoke(args.url)))


if __name__ == "__main__":
    sys.exit(main())
