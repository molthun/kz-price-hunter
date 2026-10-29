"""Включена ли сейчас локальная модель (P17-A, docs/AI_LOCAL_POLICY.md).

Машина прода — семейный компьютер: когда на нём играют, видеокарта нужна игре. Режим локальной модели —
«вкл» или «выкл» — хранится в `DATA_DIR/ai_mode.json`. Его пишут скрипты хоста:

- `deploy/prod-host/ai_switch.sh on|off` — кнопки «AI вкл» и «AI выкл» на рабочем столе сервера;
- `deploy/prod-host/gpu_probe.sh` (cron раз в минуту) — выключает сам, если видеокарту заняла игра, а «вкл»
  нажать забыли, и включает обратно через час после выключения, если видеокарта не занята.

Пока режим «выкл», маршрутизатор не вызывает локальную модель и отдаёт задачи платным. Нет файла — «вкл».
Модуль только читает: видеокарту и Ollama он не трогает.
"""
from __future__ import annotations

import datetime
import json
from typing import Any, Dict, Optional

MODE_FILE = "ai_mode.json"
GPU_STATE_FILE = "gpu_state.json"
# Отметка проверки видеокарты считается живой столько времени: скрипт пишет её раз в минуту
GPU_STATE_STALE_SECONDS = 5 * 60

BY_LABELS = {"button": "кнопкой", "game": "автоматически: видеокарту заняла игра",
             "auto": "автоматически", "admin": "из админки"}


def _read(name: str) -> Optional[Dict[str, Any]]:
    import config
    try:
        data = json.loads((config.DATA_DIR / name).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def mode() -> Dict[str, Any]:
    """Режим: {"on": bool, "since", "by", "label"}. Нет файла или он испорчен — «вкл» (так было до кнопок)."""
    data = _read(MODE_FILE) or {}
    on = str(data.get("mode") or "on").lower() != "off"
    by = str(data.get("by") or "")
    return {"on": on, "since": data.get("since"), "by": by or None,
            "label": ("включена" if on else "выключена") + (f" {BY_LABELS.get(by, by)}" if by else "")}


def gpu_state(now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Последняя отметка проверки видеокарты: {fresh, busy, reason, at, age_seconds} или «нет данных»."""
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    data = _read(GPU_STATE_FILE)
    try:
        at = datetime.datetime.fromisoformat(str((data or {})["at"]).replace("Z", "+00:00"))
        if at.tzinfo is None:
            at = at.replace(tzinfo=datetime.timezone.utc)
    except (KeyError, ValueError, TypeError):
        return {"fresh": False, "busy": False, "reason": "проверка видеокарты не работает", "at": None,
                "age_seconds": None}
    age = max(0.0, (moment - at).total_seconds())
    return {"fresh": age <= GPU_STATE_STALE_SECONDS, "busy": bool(data.get("busy")),
            "reason": str(data.get("reason") or "")[:200], "at": at.isoformat(), "age_seconds": round(age),
            "foreign_mb": data.get("foreign_mb"), "ollama_mb": data.get("ollama_mb"),
            "utilization": data.get("utilization")}


def pause_reason(now: Optional[datetime.datetime] = None) -> Optional[str]:
    """Почему локальную модель сейчас звать нельзя, или None."""
    m = mode()
    if not m["on"]:
        return f"локальная модель {m['label']}" + (f" ({m['since']})" if m.get("since") else "")
    return None


def status(now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Для админки и мониторинга: режим, пауза сейчас и что видит проверка видеокарты."""
    return {"mode": mode(), "paused": pause_reason(now), "gpu": gpu_state(now)}
