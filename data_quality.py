"""Качество данных и свежесть (этап P02).

Пороги утверждены владельцем 2026-09-19 до реализации (docs/AGENT_HANDOFF.md, карточка P02):
- объём: valid < 50 % baseline → degraded, 50–80 % → warning (принимается, но не обучает baseline), при baseline ≥ 20;
- поля: rejected > 30 % от received → degraded; доля с фото ниже baseline более чем на 30 п.п. → warning;
- свежесть предложения по last_seen: Fresh ≤ 26 ч, Aging ≤ 72 ч, Stale > 72 ч, Unknown — нет времени;
- видимость: по возрасту скрываются только не виденные дольше 30 дней; Stale не участвует в лучшей цене и алертах.

Baseline источника (магазин + URL категории; город входит в конфигурацию источника) — медиана valid по последним
BASELINE_WINDOW принятым результатам того же вида (complete / limited). Degraded, warning и failed его не обучают.
Пока принятых результатов меньше MIN_HISTORY, нормой служит число активных предложений источника.

Ограничение: адаптеры сами отбрасывают карточки без цены (часто это товары не в наличии), поэтому rejected видит
только то, что адаптер вернул. Массовая потеря цен проявляется как падение объёма и ловится порогом объёма.
"""
import datetime
import statistics
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlsplit

VOLUME_DEGRADED_RATIO = 0.5
VOLUME_WARNING_RATIO = 0.8
MIN_BASELINE_ITEMS = 20
BASELINE_WINDOW = 5
MIN_HISTORY = 3
REJECTED_DEGRADED_SHARE = 0.3
MIN_RECEIVED_FOR_SHARE = 10
IMAGE_DROP_WARNING = 0.30
MAX_PRICE = 10_000_000

FRESH_HOURS = 26
AGING_HOURS = 72
HIDE_AFTER_DAYS = 30
SOURCE_SCANS_RETENTION_DAYS = 90

FRESH, AGING, STALE, UNKNOWN = "fresh", "aging", "stale", "unknown"
OK, WARNING, DEGRADED, FAILED = "ok", "warning", "degraded", "failed"


def is_valid_offer(item: Any) -> bool:
    """Карточка пригодна к сохранению: те же правила, что у save_or_update_products_batch и контракта адаптера."""
    if not isinstance(item, dict):
        return False
    try:
        price = int(item.get("price") or 0)
    except (TypeError, ValueError):
        return False
    url = str(item.get("url") or "")
    parts = urlsplit(url)
    return (bool(str(item.get("id") or "").strip()) and bool(str(item.get("title") or "").strip())
            and 0 < price <= MAX_PRICE and parts.scheme in ("http", "https") and bool(parts.hostname))


def measure(items: Iterable[Any]) -> Dict[str, int]:
    items = list(items)
    valid = [i for i in items if is_valid_offer(i)]
    return {
        "received": len(items),
        "valid": len(valid),
        "rejected": len(items) - len(valid),
        "with_image": sum(1 for i in valid if str(i.get("image_url") or "").strip()),
    }


def baseline_from_history(history: List[Dict[str, Any]]) -> Optional[Dict[str, float]]:
    """Норма по последним принятым результатам (новые первыми); None — истории мало."""
    accepted = history[:BASELINE_WINDOW]
    if len(accepted) < MIN_HISTORY:
        return None
    image_shares = [h["with_image"] / h["valid"] for h in accepted if h.get("valid")]
    return {"valid": float(statistics.median(h["valid"] for h in accepted)),
            "image_share": float(statistics.median(image_shares)) if image_shares else None,
            "basis": "history", "samples": len(accepted)}


def assess(metrics: Dict[str, int], *, complete: bool, error: Optional[str],
           baseline: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Оценка результата категории.

    Возвращает quality (ok / warning / degraded / failed / unknown), причины, признак обучения baseline и
    может ли результат снимать с продажи отсутствующие товары (только complete без degraded).
    """
    reasons: List[str] = []
    warnings: List[str] = []
    if error:
        return {"quality": FAILED, "reasons": [str(error)[:200]], "warnings": [], "learn": False,
                "may_retire": False, "baseline": baseline and baseline.get("valid"),
                "basis": baseline and baseline.get("basis")}

    received, valid, rejected = metrics["received"], metrics["valid"], metrics["rejected"]
    if received >= MIN_RECEIVED_FOR_SHARE and rejected / received > REJECTED_DEGRADED_SHARE:
        reasons.append(f"отброшено {rejected} из {received} карточек ({rejected / received:.0%})")

    base = baseline.get("valid") if baseline else None
    if base is not None and base >= MIN_BASELINE_ITEMS:
        ratio = valid / base
        if ratio < VOLUME_DEGRADED_RATIO:
            reasons.append(f"товаров {valid} при норме {base:.0f} ({ratio:.0%})")
        elif ratio < VOLUME_WARNING_RATIO:
            warnings.append(f"товаров {valid} при норме {base:.0f} ({ratio:.0%})")

    base_image = baseline.get("image_share") if baseline else None
    if base_image is not None and valid:
        share = metrics["with_image"] / valid
        if base_image - share > IMAGE_DROP_WARNING:
            warnings.append(f"с фото {share:.0%} при норме {base_image:.0%}")

    if reasons:
        quality = DEGRADED
    elif warnings:
        quality = WARNING
    elif base is None or base < MIN_BASELINE_ITEMS:
        quality = "unknown"
    else:
        quality = OK
    return {
        "quality": quality,
        "reasons": reasons,
        "warnings": warnings,
        # Результат без нормы обучает её (иначе история не накопится), но только если он не degraded
        "learn": quality in (OK, "unknown"),
        "may_retire": complete and quality != DEGRADED,
        "baseline": base,
        "basis": baseline.get("basis") if baseline else None,
    }


def _parse_time(value: Any) -> Optional[datetime.datetime]:
    if not value:
        return None
    if isinstance(value, datetime.datetime):
        dt = value
    else:
        try:
            dt = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00").replace(" ", "T", 1))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def freshness(last_seen: Any, now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Fresh / Aging / Stale / Unknown по времени последнего наблюдения."""
    seen = _parse_time(last_seen)
    if seen is None:
        return {"freshness": UNKNOWN, "age_hours": None}
    now = now or datetime.datetime.now(datetime.timezone.utc)
    hours = max(0.0, (now - seen).total_seconds() / 3600.0)
    state = FRESH if hours <= FRESH_HOURS else AGING if hours <= AGING_HOURS else STALE
    return {"freshness": state, "age_hours": round(hours, 1)}


def annotate(item: Dict[str, Any], now: Optional[datetime.datetime] = None,
             field: str = "updated_at") -> Dict[str, Any]:
    """Добавляет к предложению last_seen_at, freshness и age_hours (API/UI)."""
    info = freshness(item.get(field), now)
    item["last_seen_at"] = item.get(field)
    item["freshness"] = info["freshness"]
    item["age_hours"] = info["age_hours"]
    return item


def is_stale(item: Dict[str, Any]) -> bool:
    return (item.get("freshness") or freshness(item.get("updated_at"))["freshness"]) == STALE
