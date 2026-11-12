"""Периодический мониторинг качества и актуальности сопоставлений локальной модели.

Кандидат плана разработки: автоматический контроль дрейфа точности (drift detection)
теневой нормализации и сопоставления товаров локальной моделью (P17-C5).
"""
import datetime
from typing import Any, Dict, Optional

# Порог минимального совпадения (целевой уровень качества >= 85%)
DRIFT_MATCH_RATE_THRESHOLD = 0.85
# Минимальный охват выборки для репрезентативности (>= 80%)
DRIFT_COVERAGE_THRESHOLD = 0.80
# Минимальное число сравненных пар за окно для исключения ложных срабатываний
MIN_COMPARED_SAMPLES = 20

# Интервал регламентной проверки по расписанию (раз в 6 часов)
SCHEDULED_AUDIT_INTERVAL_SECONDS = 6 * 3600

_last_audit_at: Optional[datetime.datetime] = None


def evaluate_drift(days: int = 1, min_samples: int = MIN_COMPARED_SAMPLES,
                   now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Оценивает дрейф точности сопоставлений по таблице normalize_shadow.

    Возвращает статус:
    - healthy: достаточная выборка, совпадение >= 85% и охват >= 80%;
    - degraded: достаточная выборка, зафиксирован дрейф (низкое совпадение или неполный охват);
    - insufficient_data: выборка меньше min_samples (недостаточно данных за период).
    """
    import database
    summary = database.normalize_shadow_summary(days=days, now=now)
    compared = summary.get("compared", 0)
    scheduled = summary.get("scheduled", 0)
    matched = summary.get("matched", 0)
    mismatch = summary.get("mismatch", 0)
    rate = summary.get("match_rate")
    coverage = summary.get("coverage")

    if compared < min_samples:
        return {
            "drift_detected": False,
            "status": "insufficient_data",
            "reason": f"Недостаточно данных за {days} дн. для оценки дрейфа ({compared}/{min_samples} сравнений)",
            "match_rate": rate,
            "coverage": coverage,
            "compared": compared,
            "scheduled": scheduled,
            "matched": matched,
            "mismatch": mismatch,
            "by_kind": summary.get("by_kind", {}),
            "threshold": DRIFT_MATCH_RATE_THRESHOLD,
        }

    # Проверяем критерии дрейфа:
    is_drift = False
    reasons = []

    if rate is not None and rate < DRIFT_MATCH_RATE_THRESHOLD:
        is_drift = True
        reasons.append(f"точность {rate * 100:.1f}% ниже порога {DRIFT_MATCH_RATE_THRESHOLD * 100:.0f}% ({matched}/{compared})")

    if coverage is not None and coverage < DRIFT_COVERAGE_THRESHOLD:
        is_drift = True
        reasons.append(f"охват выборки {coverage * 100:.1f}% ниже порога {DRIFT_COVERAGE_THRESHOLD * 100:.0f}% ({compared}/{scheduled})")

    status = "degraded" if is_drift else "healthy"
    reason_str = "Дрейф сопоставления: " + "; ".join(reasons) if is_drift else f"Точность сопоставления в норме: {rate * 100:.1f}% ({matched}/{compared})"

    return {
        "drift_detected": is_drift,
        "status": status,
        "reason": reason_str,
        "match_rate": rate,
        "coverage": coverage,
        "compared": compared,
        "scheduled": scheduled,
        "matched": matched,
        "mismatch": mismatch,
        "by_kind": summary.get("by_kind", {}),
        "threshold": DRIFT_MATCH_RATE_THRESHOLD,
    }


def audit_scheduled_drift(days: int = 1, min_samples: int = MIN_COMPARED_SAMPLES,
                          now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Регламентная проверка дрейфа с записью в телеметрию при обнаружении проблем."""
    evaluation = evaluate_drift(days=days, min_samples=min_samples, now=now)
    if evaluation["drift_detected"]:
        try:
            from telemetry import telemetry, COMPONENT_AI, SEVERITY_WARNING
            telemetry.record_event(
                event_type="matching_drift",
                severity=SEVERITY_WARNING,
                component=COMPONENT_AI,
                message=f"[MatchingDrift] {evaluation['reason']}",
                data={
                    "match_rate": evaluation["match_rate"],
                    "coverage": evaluation["coverage"],
                    "compared": evaluation["compared"],
                    "scheduled": evaluation["scheduled"],
                    "threshold": evaluation["threshold"],
                }
            )
        except Exception:
            pass
    return evaluation


def scheduled_audit_if_due(interval_seconds: int = SCHEDULED_AUDIT_INTERVAL_SECONDS,
                           now: Optional[datetime.datetime] = None) -> Optional[Dict[str, Any]]:
    """Запускает аудит качества сопоставлений по расписанию (не чаще раза в interval_seconds)."""
    global _last_audit_at
    current_time = now or datetime.datetime.now(datetime.timezone.utc)
    if _last_audit_at is not None:
        elapsed = (current_time - _last_audit_at).total_seconds()
        if elapsed < interval_seconds:
            return None
    _last_audit_at = current_time
    return audit_scheduled_drift(now=current_time)


def reset_audit_state() -> None:
    """Сброс состояния аудита (для тестов)."""
    global _last_audit_at
    _last_audit_at = None
