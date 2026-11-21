"""Внеочередной добор устаревших источников (6.42.7).

Раньше «устаревший магазин» запускал обычную волну каждые 20 с: она сдвигала общую ротацию,
сбрасывала таймер плановой волны и обходила только категории текущей волны. Магазин, у которого
в этой волне ничего нет, оставался устаревшим, поэтому круг из 9 волн проходил за минуты, плановая
волна не наступала, а у Kari обходились только разделы первой подходящей волны (household) —
одежда и дом не обходились сутками.

Добор теперь работает по источникам (категория магазина = URL): берутся только источники,
которые пропустили своё место в ротации или упали, и только их. Ротация и таймер волн не трогаются.
"""
from __future__ import annotations

import datetime
from typing import Dict, Iterable, List, Mapping, Optional, Set, Tuple

BAD_QUALITY = frozenset({"failed", "degraded"})
# Повтор упавшего источника не раньше чем через 10 мин; дальше темп задаёт пауза магазина
# после ошибки (shop_scans.next_retry_at: 5 мин … 1 ч), такой магазин в добор не берётся
RETRY_MIN_AGE = 600


def _age(started_at: Optional[str], now: float) -> Optional[float]:
    if not started_at:
        return None
    try:
        ts = datetime.datetime.fromisoformat(started_at)
    except (TypeError, ValueError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=datetime.timezone.utc)
    return now - ts.timestamp()


def source_max_age(master: str, hot: Set[str], wave_interval: int, cycle: int) -> int:
    """Источник «пропустил место»: горячие и общие — две волны, остальные — круг плюс волна."""
    if master == "all" or master in hot:
        return 2 * wave_interval
    return cycle + wave_interval


def plan_catchup(categories_by_shop: Mapping[str, Iterable[dict]], enabled_masters: Set[str],
                 hot: Set[str], last_scans: Mapping[Tuple[str, str], Tuple[Optional[str], Optional[str]]],
                 now: float, wave_interval: int, cycle: int,
                 blocked_shops: Iterable[str] = ()) -> Dict[str, List[str]]:
    """{shop_key: [url, ...]} источников для добора; пусто — добор не нужен.

    last_scans: (shop_key, url) -> (started_at последнего обхода, quality).
    Источник берётся, если его ни разу не обходили, если он старше своего места в ротации или
    если последний обход неудачный (failed/degraded) и прошло RETRY_MIN_AGE.
    Магазины на паузе после ошибки (blocked_shops) не берутся.
    """
    blocked = set(blocked_shops)
    plan: Dict[str, List[str]] = {}
    for key, cats in categories_by_shop.items():
        if key in blocked:
            continue
        urls = []
        for cat in cats:
            master = cat.get("master")
            if master != "all" and master not in enabled_masters:
                continue
            started_at, quality = last_scans.get((key, cat["url"]), (None, None))
            age = _age(started_at, now)
            if age is None or age >= source_max_age(master, hot, wave_interval, cycle) \
                    or (quality in BAD_QUALITY and age >= RETRY_MIN_AGE):
                urls.append(cat["url"])
        if urls:
            plan[key] = list(dict.fromkeys(urls))
    return plan
