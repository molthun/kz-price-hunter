"""Фактическое состояние окружения и пульс фоновых работников (этап P14).

Здесь отвечают на вопрос «что на самом деле сейчас работает»:
- версии того, что реально установлено, а не того, что записано в требованиях;
- пульс каждого фонового работника: планировщика обходов, очереди Telegram, фоновой AI-нормализации,
  телеметрии и бэкапов;
- **выключенное отличается от сломавшегося**: компонент, который владелец сам отключил, не показывается
  аварией, а умерший работник не показывается «в норме» только потому, что сайт продолжает отвечать.

Пульс пишут сами работники — коротко и не мешая работе; если запись не удалась, работа продолжается.
"""
from __future__ import annotations

import datetime
import os
import platform
import sys
import time
from typing import Any, Dict, Optional

# Компоненты, за которыми следим, и понятные названия
SCHEDULER = "scheduler"
TELEGRAM = "telegram"
AI_NORMALIZE = "ai_normalize"
TELEMETRY = "telemetry"
BACKUP = "backup"
COMPONENTS = (SCHEDULER, TELEGRAM, AI_NORMALIZE, TELEMETRY, BACKUP)

LABELS = {
    SCHEDULER: "планировщик обходов",
    TELEGRAM: "очередь Telegram",
    AI_NORMALIZE: "фоновая AI-нормализация",
    TELEMETRY: "запись телеметрии",
    BACKUP: "резервные копии",
}

# Сколько ждать пульса, прежде чем считать работника молчащим. Значения с запасом относительно
# собственных интервалов работников: очередь Telegram просыпается раз в 10 с, обходы — раз в минуты.
STALE_AFTER_SECONDS = {
    SCHEDULER: 20 * 60,
    TELEGRAM: 5 * 60,
    AI_NORMALIZE: 60 * 60,
    TELEMETRY: 10 * 60,
    BACKUP: 48 * 3600,
}

_STARTED_AT = time.time()


def uptime_seconds() -> float:
    """Сколько работает этот процесс. Перезапуск обнуляет — это и есть признак перезапуска."""
    return max(0.0, time.time() - _STARTED_AT)


def _package_version(name: str) -> Optional[str]:
    try:
        from importlib.metadata import PackageNotFoundError, version as _version
        try:
            return _version(name)
        except PackageNotFoundError:
            return None
    except Exception:
        return None


def _chromium_version() -> Optional[str]:
    """Версия браузера, которым пользуется Playwright, если он установлен."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            path = p.chromium.executable_path
        return os.path.basename(os.path.dirname(os.path.dirname(path))) or None
    except Exception:
        return None


def versions(include_browser: bool = False) -> Dict[str, Optional[str]]:
    """Версии того, что установлено на самом деле."""
    import sqlite3
    import version as app_version
    data = {
        "app": app_version.__version__,
        "git_sha": git_sha(),
        "python": sys.version.split()[0],
        "platform": platform.platform(terse=True),
        "sqlite": sqlite3.sqlite_version,
        "aiohttp": _package_version("aiohttp"),
        "playwright": _package_version("playwright"),
        "curl_cffi": _package_version("curl_cffi"),
        "requests": _package_version("requests"),
    }
    if include_browser:
        data["chromium"] = _chromium_version()
    return data


def git_sha() -> Optional[str]:
    """Коммит, из которого собран образ: из переменной окружения или из .git рядом."""
    for key in ("GIT_SHA", "GIT_COMMIT", "SOURCE_COMMIT"):
        value = os.getenv(key)
        if value:
            return value.strip()[:40]
    head = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".git", "HEAD")
    try:
        with open(head, encoding="utf-8") as f:
            ref = f.read().strip()
        if ref.startswith("ref: "):
            path = os.path.join(os.path.dirname(head), ref[5:])
            with open(path, encoding="utf-8") as f:
                return f.read().strip()[:40]
        return ref[:40]
    except OSError:
        return None


def heartbeat(component: str, note: str = "", now: Optional[datetime.datetime] = None) -> None:
    """Отметка «я жив» от работника. Fail-open: сбой записи не должен мешать работе."""
    try:
        import database
        database.record_heartbeat(component, note=note, now=now)
    except Exception:
        pass


def component_state(component: str, last: Optional[Dict[str, Any]], enabled: bool,
                    now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Состояние одного компонента: работает, молчит, выключен или никогда не отмечался.

    Выключенный владельцем компонент — это не авария, но и не «в норме»: он именно выключен.
    Молчание живого сайта ничего не говорит о работнике, поэтому «нет пульса» — отдельное состояние.
    """
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    label = LABELS.get(component, component)
    if not enabled:
        return {"component": component, "label": label, "state": "disabled",
                "reason": ("автоматические обходы выключены, поэтому самопроверка копий не запускается; "
                           "возраст копий показан отдельно"
                           if component == BACKUP else "выключено настройками"),
                "age_seconds": None, "last_seen": None}
    if not last or not last.get("last_seen"):
        return {"component": component, "label": label, "state": "unknown",
                "reason": "пульса ещё не было", "age_seconds": None, "last_seen": None}
    seen = last["last_seen"]
    if isinstance(seen, str):
        seen = datetime.datetime.fromisoformat(seen)
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=datetime.timezone.utc)
    age = max(0.0, (moment - seen).total_seconds())
    limit = STALE_AFTER_SECONDS.get(component, 15 * 60)
    if age > limit:
        return {"component": component, "label": label, "state": "stale",
                "reason": f"нет пульса {_human_age(age)} (ждём не реже чем раз в {_human_age(limit)})",
                "age_seconds": round(age), "last_seen": seen.isoformat(), "note": last.get("note")}
    return {"component": component, "label": label, "state": "alive",
            "reason": f"пульс {_human_age(age)} назад", "age_seconds": round(age),
            "last_seen": seen.isoformat(), "note": last.get("note")}


def _human_age(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} с"
    if seconds < 3600:
        return f"{seconds // 60} мин"
    if seconds < 86400:
        return f"{seconds // 3600} ч"
    return f"{seconds // 86400} дн"


def enabled_components(settings: Optional[Dict[str, Any]] = None) -> Dict[str, bool]:
    """Что владелец включил: выключенный компонент не должен выглядеть сломавшимся."""
    import config
    settings = settings if settings is not None else config.load_settings()
    ai = config.get_ai_config()
    from telemetry import telemetry_enabled
    return {
        SCHEDULER: bool(settings.get("auto_scan_enabled", True)),
        TELEGRAM: bool(config.get_bot_token()),
        AI_NORMALIZE: bool(ai.get("has_ai") and ai.get("enabled")),
        TELEMETRY: telemetry_enabled(),
        # Сами копии по-прежнему делаются по событию (миграции), поэтому их возраст — отдельная метрика,
        # а не признак живого работника. Но самопроверка копий (P15) идёт в обслуживании после обходов,
        # поэтому пульс здесь ожидается ровно тогда, когда включены автоматические обходы.
        BACKUP: bool(settings.get("auto_scan_enabled", True)),
    }


def safe_config_view() -> Dict[str, Any]:
    """Конфигурация без секретов: видно, настроено ли, но не сами ключи и токены."""
    import config
    ai = config.get_ai_config()
    return {
        "telegram_bot": "настроен" if config.get_bot_token() else "не настроен",
        "gemini": "ключ задан" if ai.get("gemini_api_key") else "ключа нет",
        "openai": "ключ задан" if ai.get("openai_api_key") else "ключа нет",
        "ai_provider": ai.get("ai_provider"),
        "data_dir": str(config.DATA_DIR),
        "dev_login": "разрешён" if config.ALLOW_DEV_LOGIN else "запрещён",
    }
