"""Планировщик автоматического обновления цен для витрин маркетплейсов и каналов."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from domain.channel_sync import sync_channel_showcase

logger = logging.getLogger("kz_price_hunter.channel_scheduler")

DEFAULT_SYNC_INTERVAL_HOURS = 12
ALLOWED_INTERVAL_HOURS = (2, 4, 6, 12, 24)

# Глобальный замок для предотвращения одновременных запусков синхронизации витрин
_channel_sync_lock = asyncio.Lock()
_is_batch_syncing = False


def is_marketplace_channel(channel_type: Optional[str], base_url: Optional[str], external_id: Optional[str] = None) -> bool:
    """Определяет, является ли канал витриной маркетплейса (Kaspi, Halyk, Forte, Wildberries, Ozon)."""
    ch_type = (channel_type or "").lower().strip()
    if ch_type in ("marketplace", "kaspi", "halyk", "forte", "fortemarket", "wildberries", "wb", "ozon"):
        return True
    combined = f"{base_url or ''} {external_id or ''}".lower()
    return any(m in combined for m in ("kaspi.kz", "halykmarket", "forte.kz", "darbiz", "wildberries.", "ozon."))


def parse_iso_datetime(dt_str: Optional[str]) -> Optional[datetime]:
    """Безопасно парсит ISO дату с таймзоной."""
    if not dt_str or not isinstance(dt_str, str):
        return None
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def get_channel_sync_schedule(conn, default_interval_hours: int = DEFAULT_SYNC_INTERVAL_HOURS) -> List[Dict[str, Any]]:
    """Возвращает актуальный статус и расписание синхронизации всех витрин маркетплейсов."""
    interval_hours = default_interval_hours if default_interval_hours in ALLOWED_INTERVAL_HOURS else DEFAULT_SYNC_INTERVAL_HOURS
    now = datetime.now(timezone.utc)

    query = """
        SELECT c.id, c.seller_id, c.channel_type, c.name, c.base_url, c.external_store_id,
               c.is_active, c.metadata_json, s.name as seller_name
        FROM channels c
        LEFT JOIN sellers s ON s.id = c.seller_id
        WHERE c.is_active = 1
        ORDER BY c.created_at ASC
    """
    rows = conn.execute(query).fetchall()

    schedule: List[Dict[str, Any]] = []

    for row in rows:
        ch_id = row[0]
        seller_id = row[1]
        ch_type = row[2]
        ch_name = row[3]
        base_url = row[4]
        ext_store_id = row[5]
        is_active = bool(row[6])
        meta_str = row[7]
        seller_name = row[8] or seller_id

        if not is_marketplace_channel(ch_type, base_url, ext_store_id):
            continue

        try:
            meta = json.loads(meta_str or "{}") if isinstance(meta_str, str) else {}
        except Exception:
            meta = {}

        last_sync_iso = meta.get("last_sync_at")
        # Повтор отсчитывается от последней попытки: после отказа или неполного обхода
        # last_sync_at не меняется (G7-A03), а заблокированный источник нельзя дёргать каждый такт
        last_sync_dt = max(
            (d for d in (parse_iso_datetime(last_sync_iso), parse_iso_datetime(meta.get("last_attempt_at"))) if d),
            default=None,
        )
        last_status = meta.get("last_sync_status", "never")
        products_count = meta.get("products_count", 0)
        marketplace = meta.get("marketplace") or ch_type

        # Персональный интервал канала или глобальный по умолчанию
        ch_interval = int(meta.get("sync_interval_hours") or interval_hours)
        if ch_interval not in ALLOWED_INTERVAL_HOURS:
            ch_interval = interval_hours

        if last_sync_dt is None:
            is_due = True
            next_sync_dt = now
            next_sync_iso = now.isoformat()
            seconds_overdue = 999999
        else:
            next_sync_dt = last_sync_dt + timedelta(hours=ch_interval)
            next_sync_iso = next_sync_dt.isoformat()
            is_due = now >= next_sync_dt
            seconds_overdue = max(0, int((now - next_sync_dt).total_seconds())) if is_due else 0

        schedule.append({
            "channel_id": ch_id,
            "seller_id": seller_id,
            "seller_name": seller_name,
            "channel_name": ch_name,
            "channel_type": ch_type,
            "marketplace": marketplace,
            "base_url": base_url,
            "is_active": is_active,
            "last_sync_at": last_sync_iso,
            "last_sync_status": last_status,
            "last_attempt_at": meta.get("last_attempt_at"),
            "last_sync_error": meta.get("last_sync_error"),
            "products_count": products_count,
            "sync_interval_hours": ch_interval,
            "next_sync_at": next_sync_iso,
            "is_due": is_due,
            "seconds_overdue": seconds_overdue,
        })

    # Сортируем: сначала те, у кого подошло время (по величине просрочки), затем по времени следующего сбора
    schedule.sort(key=lambda x: (not x["is_due"], -x["seconds_overdue"], x["next_sync_at"] or ""))
    return schedule


def get_due_channel_ids(conn, default_interval_hours: int = DEFAULT_SYNC_INTERVAL_HOURS) -> List[str]:
    """Возвращает список идентификаторов каналов, требующих обновления в текущий момент."""
    schedule = get_channel_sync_schedule(conn, default_interval_hours=default_interval_hours)
    return [item["channel_id"] for item in schedule if item["is_due"]]


async def run_channel_sync_batch(
    conn_getter: Callable[[], Any],
    channel_ids: Optional[List[str]] = None,
    default_interval_hours: int = DEFAULT_SYNC_INTERVAL_HOURS,
    max_channels: int = 5,
    max_pages: int = 5,
    delay_between_channels: float = 2.0,
) -> Dict[str, Any]:
    """Выполняет пакетное обновление устаревших каналов с блокировкой от параллельных запусков."""
    global _is_batch_syncing

    if _channel_sync_lock.locked() or _is_batch_syncing:
        return {
            "ok": False,
            "busy": True,
            "message": "Синхронизация витрин уже выполняется другим процессом",
            "processed": 0,
            "results": [],
        }

    async with _channel_sync_lock:
        _is_batch_syncing = True
        try:
            # 1. Определяем список каналов
            if channel_ids is None:
                conn = conn_getter()
                try:
                    targets = get_due_channel_ids(conn, default_interval_hours=default_interval_hours)
                finally:
                    conn.close()
            else:
                targets = list(channel_ids)

            targets = targets[:max_channels]
            if not targets:
                return {
                    "ok": True,
                    "busy": False,
                    "message": "Нет каналов, требующих синхронизации",
                    "processed": 0,
                    "succeeded": 0,
                    "failed": 0,
                    "results": [],
                }

            logger.info(f"Запуск автосинхронизации каналов ({len(targets)} шт.): {targets}")
            results = []
            succeeded = 0
            failed = 0

            for i, ch_id in enumerate(targets):
                conn = conn_getter()
                try:
                    res = await sync_channel_showcase(conn, ch_id, max_pages=max_pages)
                    results.append({"channel_id": ch_id, "result": res})
                    if res.get("ok"):
                        succeeded += 1
                    else:
                        failed += 1
                except Exception as ex:
                    logger.exception(f"Исключение при автосинхронизации канала {ch_id}: {ex}")
                    results.append({"channel_id": ch_id, "result": {"ok": False, "error": str(ex)}})
                    failed += 1
                finally:
                    conn.close()

                if i < len(targets) - 1 and delay_between_channels > 0:
                    await asyncio.sleep(delay_between_channels)

            return {
                "ok": True,
                "busy": False,
                "message": f"Синхронизировано витрин: {succeeded}, с ошибками: {failed}",
                "processed": len(targets),
                "succeeded": succeeded,
                "failed": failed,
                "results": results,
            }
        finally:
            _is_batch_syncing = False
