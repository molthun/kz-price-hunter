"""Утреннее напоминание владельцу: кто вошёл на сайт и ждёт подтверждения доступа (6.36.0).

Правила:
1. Пишем только если ждущие есть, и только утром: с ACCESS_PENDING_NOTIFY_HOUR до полудня по
   ACCESS_PENDING_NOTIFY_TZ (по умолчанию 9:00–12:00, Алматы). Кто вошёл позже — попадёт в завтрашнее.
2. Одно сообщение в день на администратора: дубли отсекает UNIQUE(alert_id, user_id) очереди уведомлений,
   как у суточной сводки, — перезапуск сервиса или частые проверки второго сообщения не дадут.
3. Ждущие — вошедшие за последние PENDING_RECENT_DAYS дней, не подтверждённые, не заблокированные
   и не администраторы. Давно забытые аккаунты не напоминают о себе каждое утро.
"""
from __future__ import annotations

import datetime
import html
import json
from typing import Any, Dict, List, Optional

PENDING_ALERT_BASE = 2_000_000_000   # не пересекается с суточной сводкой (1 000 000 000 + день)
PENDING_RECENT_DAYS = 14
MAX_LISTED = 10
MORNING_END_HOUR = 12


def _owner_zone():
    """Пояс владельца. Без базы часовых поясов Алматы — фиксированные UTC+5 (с 2024 года без перевода часов)."""
    import config
    import timezones
    name = config.ACCESS_PENDING_NOTIFY_TZ
    zone = timezones.zone(name)
    if zone is timezones.UTC and name in ("Asia/Almaty", "Asia/Qostanay", "Asia/Aqtobe", "Asia/Aqtau", "Asia/Oral"):
        return datetime.timezone(datetime.timedelta(hours=5))
    return zone


def pending_alert_id(day: str) -> int:
    return -(PENDING_ALERT_BASE + datetime.date.fromisoformat(day).toordinal())


def _parse_utc(value: Any) -> Optional[datetime.datetime]:
    if not value:
        return None
    try:
        moment = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=datetime.timezone.utc)


def pending_users(now: Optional[datetime.datetime] = None) -> List[Dict[str, Any]]:
    """Кто ждёт подтверждения: вошёл недавно, не подтверждён, не заблокирован, не администратор."""
    import config
    import database
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    since = moment - datetime.timedelta(days=PENDING_RECENT_DAYS)
    people = []
    for user in database.list_users():
        if user.get("is_blocked") or int(user["id"]) in config.ADMIN_TELEGRAM_IDS or int(user["id"]) < 0:
            continue
        settings = user.get("settings") or {}
        if settings.get("is_approved") or settings.get("is_confirmed"):
            continue
        seen = _parse_utc(user.get("last_login_at")) or _parse_utc(user.get("created_at"))
        if seen is None or seen < since:
            continue
        people.append({"id": int(user["id"]), "first_name": user.get("first_name") or "",
                       "last_name": user.get("last_name") or "", "username": user.get("username") or "",
                       "seen_at": seen.isoformat()})
    return people


def due_day(now: Optional[datetime.datetime] = None) -> Optional[str]:
    """Сегодняшняя дата (в поясе владельца), если сейчас утро, иначе None."""
    import config
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    local = moment.astimezone(_owner_zone())
    if not (config.ACCESS_PENDING_NOTIFY_HOUR <= local.hour < max(MORNING_END_HOUR, config.ACCESS_PENDING_NOTIFY_HOUR + 1)):
        return None
    return local.date().isoformat()


def message_lines(people: List[Dict[str, Any]], now: Optional[datetime.datetime] = None) -> List[str]:
    zone = _owner_zone()
    lines = [f"👥 <b>Ждут подтверждения доступа: {len(people)}</b>", ""]
    for person in people[:MAX_LISTED]:
        name = " ".join(x for x in (person["first_name"], person["last_name"]) if x).strip() or "Без имени"
        handle = f" @{person['username']}" if person["username"] else ""
        seen = _parse_utc(person["seen_at"])
        when = seen.astimezone(zone).strftime("%d.%m %H:%M") if seen else "—"
        lines.append(f"• {html.escape(name)}{html.escape(handle)} — вход {when}")
    if len(people) > MAX_LISTED:
        lines.append(f"…и ещё {len(people) - MAX_LISTED}")
    lines.append("")
    lines.append("Без подтверждения у них 30 минут пробного доступа. Подтвердить или заблокировать — в разделе «Пользователи».")
    return lines


def queue_if_due(now: Optional[datetime.datetime] = None) -> int:
    """Ставит напоминание в очередь уведомлений, если утро и есть ждущие. Возвращает число новых заданий."""
    import config
    import database
    if config.ACCESS_MODE == "open":
        return 0
    day = due_day(now)
    if not day:
        return 0
    people = pending_users(now)
    if not people:
        return 0
    admins = []
    for admin_id in sorted(config.ADMIN_TELEGRAM_IDS):
        user = database.get_user(admin_id)
        if user and not user["is_blocked"]:
            admins.append(int(admin_id))
    if not admins:
        return 0
    payload = {"kind": "pending_approvals", "day": day, "count": len(people), "lines": message_lines(people, now)}
    moment = (now or datetime.datetime.now(datetime.timezone.utc)).timestamp()
    queued = 0
    with database.get_connection() as conn:
        for admin_id in admins:
            cur = conn.execute("""INSERT OR IGNORE INTO notification_outbox
                                  (alert_id, user_id, payload, status, created_at, next_attempt_at)
                                  VALUES (?, ?, ?, 'pending', ?, ?)""",
                               (pending_alert_id(day), admin_id, json.dumps(payload, ensure_ascii=False), moment, 0))
            queued += cur.rowcount or 0
        conn.commit()
    return queued
