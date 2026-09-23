"""P13. Суточная сводка: цифры считает код, AI только пересказывает их словами.

Три правила этого модуля:

1. Отчёт воспроизводим без AI. Любое число можно получить повторным расчётом того же дня — сводка
   хранится, но пересчёт даёт тот же результат и не создаёт второй записи за день.
2. Границы суток называются явно. События берутся по точным меткам времени, а дневные агрегаты
   (поиск, расходы AI) хранятся по дням UTC и не режутся на часы — если запрошенные сутки не совпадают
   с сутками UTC, такой блок честно помечается как приблизительный.
3. Неполные сутки — не то же самое, что тихие сутки. Незакрытый день помечается, а отсутствие данных
   называется отсутствием данных, а не нулём достижений.
"""
from __future__ import annotations

import datetime
import json
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_TZ = "UTC"
MAX_ITEMS = 5           # столько примеров показываем в блоках — сводка, а не выгрузка
MAX_SUMMARY_CHARS = 1500


# ---------------------------------------------------------------------------
# Границы суток
# ---------------------------------------------------------------------------

def _zone(tz: str):
    """Часовой пояс отчёта. Без базы часовых поясов считаем по UTC, а не падаем (регрессия 5.13.0)."""
    import timezones
    return timezones.zone(tz)


def day_window(day: str, tz: str = DEFAULT_TZ) -> Tuple[datetime.datetime, datetime.datetime]:
    """Начало и конец суток в UTC. Конец не входит в интервал: [начало, конец)."""
    zone = _zone(tz)
    date = datetime.date.fromisoformat(day)
    start = datetime.datetime.combine(date, datetime.time.min, tzinfo=zone)
    end = start + datetime.timedelta(days=1)
    return start.astimezone(datetime.timezone.utc), end.astimezone(datetime.timezone.utc)


def today(tz: str = DEFAULT_TZ, now: Optional[datetime.datetime] = None) -> str:
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    return moment.astimezone(_zone(tz)).strftime("%Y-%m-%d")


def previous_day(tz: str = DEFAULT_TZ, now: Optional[datetime.datetime] = None) -> str:
    """Последние завершённые сутки — про них можно говорить целиком."""
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    local = moment.astimezone(_zone(tz)).date() - datetime.timedelta(days=1)
    return local.isoformat()


def day_keys(start: datetime.datetime, end: datetime.datetime) -> List[str]:
    """Ключи дневных агрегатов (UTC), которые задевает окно."""
    first = start.astimezone(datetime.timezone.utc).date()
    last = (end - datetime.timedelta(microseconds=1)).astimezone(datetime.timezone.utc).date()
    keys, cur = [], first
    while cur <= last:
        keys.append(cur.isoformat())
        cur += datetime.timedelta(days=1)
    return keys


def is_exact_utc_day(start: datetime.datetime, end: datetime.datetime) -> bool:
    s = start.astimezone(datetime.timezone.utc)
    return s.hour == 0 and s.minute == 0 and s.second == 0 and (end - start) == datetime.timedelta(days=1)


# ---------------------------------------------------------------------------
# Блоки отчёта
# ---------------------------------------------------------------------------

def _search_block(conn, start, end) -> Dict[str, Any]:
    import search_analytics as sa
    keys = day_keys(start, end)
    counts = {k: 0 for k in sa.OUTCOMES}
    placeholders = ",".join("?" * len(keys))
    for row in conn.execute(f"SELECT outcome, SUM(searches) AS n FROM search_stats "
                            f"WHERE bucket IN ({placeholders}) GROUP BY outcome", keys):
        counts[row["outcome"]] = counts.get(row["outcome"], 0) + (row["n"] or 0)
    total = sum(counts.values())
    return {"searches": total, "outcomes": counts,
            "success_rate": sa.success_rate(counts), "error_rate": sa.error_rate(counts),
            "approximate": not is_exact_utc_day(start, end),
            "day_keys": keys,
            "note": "Поиски хранятся дневными агрегатами по UTC" if not is_exact_utc_day(start, end) else ""}


def _catalog_block(conn, start, end) -> Dict[str, Any]:
    lo, hi = start.isoformat(), end.isoformat()
    new_products = conn.execute(
        "SELECT COUNT(*) FROM products WHERE replace(created_at, ' ', 'T') >= ? "
        "AND replace(created_at, ' ', 'T') < ?", (lo, hi)).fetchone()[0]
    rows = conn.execute("""
        SELECT product_id, price, prev_price FROM (
            SELECT product_id, price, observed_at,
                   LAG(price) OVER (PARTITION BY product_id ORDER BY observed_at, rowid) AS prev_price
            FROM price_observations)
        WHERE observed_at >= ? AND observed_at < ? AND prev_price IS NOT NULL AND prev_price <> price
    """, (lo, hi)).fetchall()
    cheaper = [r for r in rows if r["price"] < r["prev_price"]]
    dearer = [r for r in rows if r["price"] > r["prev_price"]]
    drops = sorted(cheaper, key=lambda r: (r["price"] - r["prev_price"]) / r["prev_price"])[:MAX_ITEMS]
    examples = []
    for r in drops:
        title = conn.execute("SELECT title, shop FROM products WHERE id = ?", (r["product_id"],)).fetchone()
        examples.append({"title": title["title"] if title else r["product_id"],
                         "shop": title["shop"] if title else "",
                         "was": r["prev_price"], "now": r["price"],
                         "drop_pct": round(100.0 * (r["prev_price"] - r["price"]) / r["prev_price"], 1)})
    return {"new_products": new_products, "price_changes": len(rows),
            "cheaper": len(cheaper), "dearer": len(dearer), "biggest_drops": examples,
            "note": "Изменением считается наблюдение с ценой, отличной от предыдущей у того же предложения"}


def _telegram_block(conn, start, end) -> Dict[str, Any]:
    from telemetry import EVENT_TELEGRAM_ALERT
    sums = {k: 0 for k in ("sent", "cancelled", "retry", "failed", "errors")}
    for row in conn.execute("SELECT data_json FROM telemetry_events WHERE type = ? "
                            "AND timestamp >= ? AND timestamp < ?",
                            (EVENT_TELEGRAM_ALERT, start.isoformat(), end.isoformat())):
        try:
            data = json.loads(row["data_json"] or "{}")
        except (TypeError, ValueError):
            continue
        for key in sums:
            sums[key] += int(data.get(key) or 0)
    attempts = sums["sent"] + sums["retry"] + sums["failed"] + sums["errors"]
    return {"attempts": attempts, **sums,
            "note": "Отменённые уведомления не были попыткой отправки"}


def _ai_block(conn, start, end) -> Dict[str, Any]:
    keys = day_keys(start, end)
    placeholders = ",".join("?" * len(keys))
    row = conn.execute(f"SELECT SUM(requests) AS requests, SUM(input_tokens) AS input_tokens, "
                       f"SUM(output_tokens) AS output_tokens, SUM(cost_usd) AS cost, SUM(errors) AS errors "
                       f"FROM ai_usage WHERE day IN ({placeholders})", keys).fetchone()
    by_task = [{"task": r["task"], "requests": r["requests"], "cost_usd": round(r["cost"], 4) if r["cost"] else None}
               for r in conn.execute(f"SELECT task, SUM(requests) AS requests, SUM(cost_usd) AS cost "
                                     f"FROM ai_usage WHERE day IN ({placeholders}) GROUP BY task "
                                     f"ORDER BY requests DESC LIMIT ?", keys + [MAX_ITEMS])]
    # Стоимость: «вызовов не было», «неизвестна», «посчитана не по всем вызовам» и «известный ноль» —
    # четыре разных ответа. Считается по числу оценённых вызовов внутри строки агрегата, потому что одна
    # строка объединяет вызовы с ценой и без неё (M06).
    priced = conn.execute(
        f"SELECT SUM(COALESCE(priced_requests, 0)) AS priced, SUM(cost_usd) AS cost, "
        f"SUM(CASE WHEN priced_requests IS NULL THEN requests ELSE 0 END) AS legacy "
        f"FROM ai_usage WHERE day IN ({placeholders})", keys).fetchone()
    requests = row["requests"] or 0
    priced_requests = int(priced["priced"] or 0)
    legacy_requests = int(priced["legacy"] or 0)     # старые строки без счётчика: покрытие неизвестно
    cost_sum = round(priced["cost"], 4) if priced["cost"] is not None else None
    if not requests:
        cost_state, cost = "no_calls", None
    elif legacy_requests:
        cost_state, cost = "partial", cost_sum
    elif not priced_requests:
        cost_state, cost = "unknown", None
    elif priced_requests < requests:
        cost_state, cost = "partial", cost_sum
    else:
        cost_state, cost = "known", cost_sum if cost_sum is not None else 0.0
    return {"requests": requests, "errors": row["errors"] or 0,
            "input_tokens": row["input_tokens"] or 0, "output_tokens": row["output_tokens"] or 0,
            "cost_usd": cost, "cost_state": cost_state,
            "priced_requests": priced_requests, "unpriced_requests": requests - priced_requests,
            "legacy_requests": legacy_requests,
            "by_task": by_task,
            "approximate": not is_exact_utc_day(start, end), "day_keys": keys,
            "note": "Стоимость считается только по ценам, заданным владельцем: без них она неизвестна, "
                    "а не равна нулю"}


def _shops_block(conn, start, end) -> Dict[str, Any]:
    from telemetry import EVENT_DEGRADATION, EVENT_RECOVERY
    lo, hi = start.isoformat(), end.isoformat()
    scans, shops = [], {}
    for row in conn.execute("SELECT shop_key, quality, received, valid FROM source_scans "
                            "WHERE finished_at >= ? AND finished_at < ?", (lo, hi)):
        scans.append(row)
        s = shops.setdefault(row["shop_key"], {"scans": 0, "received": 0, "valid": 0, "bad_quality": 0})
        s["scans"] += 1
        s["received"] += row["received"] or 0
        s["valid"] += row["valid"] or 0
        if row["quality"] not in ("ok", "good"):
            s["bad_quality"] += 1
    # Счёт отдельно от примеров: список примеров ограничен пятью, а итог — нет (M05)
    totals = {"degradation": 0, "recovery": 0}
    events = {"degradation": [], "recovery": []}
    for row in conn.execute("SELECT type, shop, message, timestamp FROM telemetry_events "
                            "WHERE type IN (?, ?) AND timestamp >= ? AND timestamp < ? ORDER BY timestamp",
                            (EVENT_DEGRADATION, EVENT_RECOVERY, lo, hi)):
        key = "degradation" if row["type"] == EVENT_DEGRADATION else "recovery"
        totals[key] += 1
        if len(events[key]) < MAX_ITEMS:
            events[key].append({"shop": row["shop"], "message": row["message"], "at": row["timestamp"]})
    return {"scans": len(scans), "shops": len(shops),
            "by_shop": [{"shop": k, **v} for k, v in sorted(shops.items())][:20],
            "degraded": totals["degradation"], "recovered": totals["recovery"],
            "events": events, "examples_limited_to": MAX_ITEMS,
            "note": "«Восстановился» — это записанное событие восстановления, а не отсутствие жалоб; "
                    "в примерах показаны не больше пяти событий, счётчик считает все"}


BLOCKS = {
    "search": ("Поиск", _search_block),
    "catalog": ("Каталог и цены", _catalog_block),
    "telegram": ("Уведомления Telegram", _telegram_block),
    "ai": ("Расходы AI", _ai_block),
    "shops": ("Магазины", _shops_block),
}


def compute(day: Optional[str] = None, tz: str = DEFAULT_TZ,
            now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Считает сутки целиком. Никакого AI, только запросы к собственным данным."""
    import database
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    day = day or previous_day(tz, moment)
    start, end = day_window(day, tz)
    blocks: Dict[str, Any] = {}
    unavailable: Dict[str, str] = {}
    with database.get_connection() as conn:
        for name, (title, fn) in BLOCKS.items():
            try:
                blocks[name] = {"источник": title, **fn(conn, start, end)}
            except Exception as e:                      # один блок не должен уносить весь отчёт
                unavailable[name] = f"{type(e).__name__}: {e}"
    empty = all(_is_quiet(name, block) for name, block in blocks.items())
    return {
        "day": day, "tz": tz,
        "window": {"from": start.isoformat(), "to": end.isoformat(),
                   "exact_utc_day": is_exact_utc_day(start, end)},
        "partial": end > moment,
        "computed_at": moment.isoformat(),
        "blocks": blocks, "unavailable": unavailable,
        "empty": empty and not unavailable,
        "note": ("Сутки ещё не закончились — цифры неполные. " if end > moment else "")
                + "Все числа получены расчётом по собственным данным и воспроизводятся повторным расчётом.",
    }


def _is_quiet(name: str, block: Dict[str, Any]) -> bool:
    if name == "search":
        return not block.get("searches")
    if name == "catalog":
        return not block.get("new_products") and not block.get("price_changes")
    if name == "telegram":
        return not block.get("attempts") and not block.get("cancelled")
    if name == "ai":
        return not block.get("requests")
    if name == "shops":
        return not block.get("scans") and not block.get("degraded") and not block.get("recovered")
    return False


# ---------------------------------------------------------------------------
# Хранение: один день — одна запись
# ---------------------------------------------------------------------------

def save(report: Dict[str, Any], summary: Optional[str] = None,
         provider: Optional[str] = None) -> None:
    import database
    database.save_daily_report(report["day"], report["tz"], report["computed_at"],
                               1 if report["partial"] else 0,
                               json.dumps(report, ensure_ascii=False, default=str),
                               summary, provider)


def load(day: str, tz: str = DEFAULT_TZ) -> Optional[Dict[str, Any]]:
    import database
    row = database.daily_report(day, tz)
    if not row:
        return None
    try:
        report = json.loads(row["payload"])
    except (TypeError, ValueError):
        return None
    report["summary"] = row["summary"]
    report["summary_provider"] = row["summary_provider"]
    report["stored_at"] = row["computed_at"]
    return report


def report(day: Optional[str] = None, tz: str = DEFAULT_TZ, refresh: bool = False,
           now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Сохранённый отчёт или новый расчёт. Незакрытые сутки всегда пересчитываются."""
    day = day or previous_day(tz, now)
    if not refresh:
        stored = load(day, tz)
        if stored and not stored.get("partial"):
            return stored
    fresh = compute(day, tz, now)
    save(fresh)
    fresh.setdefault("summary", None)
    fresh.setdefault("summary_provider", None)
    return fresh


# ---------------------------------------------------------------------------
# Пересказ словами
# ---------------------------------------------------------------------------

def numbers_source(report: Dict[str, Any]) -> Dict[str, Any]:
    return {"blocks": report.get("blocks", {}), "day": report.get("day")}


def build_prompt(report: Dict[str, Any]) -> str:
    import admin_assistant
    import ai_router
    data = json.dumps(numbers_source(report), ensure_ascii=False, indent=1, default=str)
    return (
        f"Ты пишешь короткую сводку за сутки {report['day']} ({report['tz']}) для владельца сервиса.\n"
        "Правила: пиши по-русски, 3–6 предложений. НЕ ПРИВОДИ НИКАКИХ ЧИСЕЛ И ССЫЛОК НА НИХ: все цифры "
        "владелец видит рядом, их показывает сервис. Твоя часть — словесное объяснение: на что похоже "
        "происходящее и что стоит проверить. Догадку помечай словом «вероятно». Если данных за сутки "
        "нет, так и напиши.\n"
        + ("Сутки ещё не закончились, скажи об этом.\n" if report.get("partial") else "")
        + ai_router.untrusted_block(data, "ДАННЫЕ")
    )


async def summarize(report: Dict[str, Any]) -> Dict[str, Any]:
    """Просит модель пересказать цифры. Число, которого нет в отчёте, отменяет пересказ."""
    import admin_assistant
    import ai_router
    result = {"summary": None, "provider": None, "rejected": None}
    try:
        routed = await ai_router.run("daily_digest", build_prompt(report),
                                     validate=lambda v: v if isinstance(v, (dict, str)) else None)
    except ai_router.AIUnavailable as e:
        result["rejected"] = f"AI недоступен: {e}"
        return result
    value = routed["result"]
    text = (value if isinstance(value, str) else str(value.get("summary") or value.get("answer") or "")).strip()
    text = text[:MAX_SUMMARY_CHARS]
    if not text:
        result["rejected"] = "модель не дала пересказа"
        return result
    comment, refusal = admin_assistant.verify_comment(text)
    if refusal:
        result["rejected"] = refusal
        result["provider"] = routed.get("provider")
        return result
    text = comment
    result["summary"] = text
    result["provider"] = routed.get("provider")
    save(report, summary=text, provider=routed.get("provider"))
    return result


# ---------------------------------------------------------------------------
# Отправка сводки администратору в Telegram (P13, отдельное включение)
# ---------------------------------------------------------------------------

# Своё пространство идентификаторов в общей очереди уведомлений: alert_id отрицательный и не может
# совпасть ни с алертом ленты (положительный), ни со срабатыванием наблюдения (−id события, небольшое).
DIGEST_ALERT_BASE = 1_000_000_000


def digest_alert_id(day: str) -> int:
    """Идентификатор задания для этих суток: одна запись на день — значит, одно сообщение на день."""
    return -(DIGEST_ALERT_BASE + datetime.date.fromisoformat(day).toordinal())


def settings_view() -> Dict[str, Any]:
    import config
    settings = config.load_settings()
    return {"enabled": bool(settings.get("daily_digest_telegram_enabled")),
            "hour": int(settings.get("daily_digest_hour") or 0),
            "tz": str(settings.get("daily_digest_timezone") or DEFAULT_TZ)}


def due_day(now: Optional[datetime.datetime] = None,
            view: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Сутки, о которых пора написать, или None.

    Пишем только о завершившихся сутках и только после назначенного часа: сводка за неполный день
    дала бы цифры, которые к вечеру изменятся.
    """
    view = view or settings_view()
    if not view["enabled"]:
        return None
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    local = moment.astimezone(_zone(view["tz"]))
    if local.hour < view["hour"]:
        return None
    return previous_day(view["tz"], moment)


def recipients() -> List[int]:
    """Кому уходит сводка: администраторам сервиса. Это админские цифры, и чужим они не нужны."""
    import config
    import database
    people = []
    for admin_id in sorted(config.ADMIN_TELEGRAM_IDS):
        user = database.get_user(admin_id)
        if user and not user["is_blocked"]:
            people.append(int(admin_id))
    return people


def _cost_text(ai: Dict[str, Any]) -> str:
    """Стоимость словами: неизвестно, по части вызовов или точная сумма (M06)."""
    state = ai.get("cost_state")
    cost = ai.get("cost_usd")
    if state == "no_calls":
        return "вызовов не было"
    if state == "unknown" or cost is None:
        return "неизвестна — цены моделей не заданы"
    if state == "partial":
        if ai.get("legacy_requests"):
            return (f"${cost} — но {ai.get('legacy_requests')} вызовов записаны до раздельного учёта, "
                    f"поэтому полнота суммы неизвестна")
        return (f"${cost} по {ai.get('priced_requests')} из {ai.get('requests')} вызовов "
                f"(для остальных цена не задана)")
    return f"${cost}"


def message_lines(report: Dict[str, Any]) -> List[str]:
    """Текст сводки: сначала цифры, потом пересказ. Без AI сообщение всё равно осмысленно."""
    blocks = report.get("blocks") or {}
    lines = [f"📅 <b>Сводка за {report.get('day')}</b> ({report.get('tz')})"]
    if report.get("partial"):
        lines.append("⚠️ Сутки ещё не закончились — цифры неполные.")
    if report.get("empty"):
        lines.append("За эти сутки наблюдений нет — это отсутствие данных, а не ноль достижений.")
    search = blocks.get("search") or {}
    catalog = blocks.get("catalog") or {}
    telegram = blocks.get("telegram") or {}
    ai = blocks.get("ai") or {}
    shops = blocks.get("shops") or {}
    lines.append("")
    lines.append(f"🔎 Поиски: <b>{search.get('searches', 0)}</b>"
                 + (f" · успех {search.get('success_rate')} %" if search.get("success_rate") is not None else "")
                 + (" (границы по суткам UTC)" if search.get("approximate") else ""))
    lines.append(f"🏷 Новые товары: <b>{catalog.get('new_products', 0)}</b> · изменений цен "
                 f"<b>{catalog.get('price_changes', 0)}</b> (дешевле {catalog.get('cheaper', 0)}, "
                 f"дороже {catalog.get('dearer', 0)})")
    lines.append(f"📨 Уведомления: отправлено <b>{telegram.get('sent', 0)}</b> из "
                 f"{telegram.get('attempts', 0)} попыток")
    lines.append(f"🤖 AI: вызовов <b>{ai.get('requests', 0)}</b> · стоимость {_cost_text(ai)}")
    lines.append(f"🏬 Обходы: <b>{shops.get('scans', 0)}</b> · ухудшений {shops.get('degraded', 0)} · "
                 f"восстановлений {shops.get('recovered', 0)}")
    drops = catalog.get("biggest_drops") or []
    if drops:
        lines.append("")
        lines.append("📉 Заметные снижения:")
        for item in drops[:3]:
            import html as html_module
            lines.append(f"• {html_module.escape(str(item.get('title') or ''))} — "
                         f"{item.get('was')} → {item.get('now')} ₸ (−{item.get('drop_pct')} %)")
    unavailable = report.get("unavailable") or {}
    if unavailable:
        lines.append("")
        lines.append(f"⚠️ Недоступные блоки: {', '.join(sorted(unavailable))}")
    if report.get("summary"):
        import html as html_module
        lines.append("")
        lines.append("🗒 Объяснение словами (цифры выше собраны кодом): "
                     + html_module.escape(str(report["summary"])))
    lines.append("")
    lines.append("ℹ️ Цифры посчитаны кодом по собственным данным и воспроизводятся повторным расчётом.")
    return lines


def queue_if_due(now: Optional[datetime.datetime] = None) -> int:
    """Ставит сводку в общую очередь уведомлений. Повторный вызов в те же сутки ничего не дублирует.

    Одно задание на день и человека обеспечивает UNIQUE(alert_id, user_id) очереди: даже если проверка
    вызовется десятки раз за день или сервис перезапустится, сообщение уйдёт один раз.
    """
    import json as json_module
    import database
    view = settings_view()
    day = due_day(now, view)
    if not day:
        return 0
    people = recipients()
    if not people:
        return 0
    stored = load(day, view["tz"]) or report(day, view["tz"], now=now)
    payload = {"kind": "daily_digest", "day": day, "tz": view["tz"],
               "lines": message_lines(stored)}
    moment = (now or datetime.datetime.now(datetime.timezone.utc)).timestamp()
    queued = 0
    with database.get_connection() as conn:
        for user_id in people:
            cur = conn.execute("""INSERT OR IGNORE INTO notification_outbox
                                  (alert_id, user_id, payload, status, created_at, next_attempt_at)
                                  VALUES (?, ?, ?, 'pending', ?, ?)""",
                               (digest_alert_id(day), user_id,
                                json_module.dumps(payload, ensure_ascii=False), moment, 0))
            queued += cur.rowcount or 0
        conn.commit()
    return queued
