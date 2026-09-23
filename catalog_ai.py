"""P09 (AI-часть). Модель подключается только там, где правило честно не знает ответа.

Порядок неизменен: сначала детерминированные правила, и только потом модель. Что модель может и чего
не может — договорено заранее и держится кодом:

- она **не может** признать одним товаром пару, где правило нашло явное противоречие (1 л и 1,5 л,
  8 ГБ и 16 ГБ): такие пары ей не показываются вовсе;
- она **не может** отменить решение правила «это один товар» — правило уже уверено;
- она может только **разрешить** сравнение там, где правило воздержалось: фасовку указал лишь один
  магазин, а отличительные слова совпали.

Пороги согласованы владельцем ДО замеров (2026-09-23, вариант «строго»): ответ принимается при
уверенности не ниже 0.90, и включать AI-путь в работу можно, только если на проверочном наборе точность
осталась 1.0 (ни одного нового ложного сравнения), а полнота выросла хотя бы до 0.94.

Режимы: off — модель не вызывается совсем; shadow — вызывается и записывает решение, но сравнение цен
не меняется; on — принятое решение разрешает сравнение. По умолчанию off: вызовы модели стоят денег,
и включает их владелец.
"""
from __future__ import annotations

import datetime
import hashlib
import json
from typing import Any, Callable, Dict, List, Optional

OFF, SHADOW, ON = "off", "shadow", "on"
MODES = (OFF, SHADOW, ON)

# Согласовано владельцем до замеров (2026-09-23, вариант «строго»)
MIN_CONFIDENCE = 0.90
REQUIRED_PRECISION = 1.0
REQUIRED_RECALL = 0.94
AGREED_AT = "2026-09-23"

MAX_PAIRS_PER_RUN = 10          # столько спорных пар разбираем за один проход обслуживания
MAX_TITLE_CHARS = 200
KEY_SEPARATOR = "|::|"


def mode() -> str:
    import config
    value = str(config.load_settings().get("ai_matching_mode") or OFF).strip()
    return value if value in MODES else OFF


def pair_key(left: Any, right: Any) -> str:
    """Ключ пары, не зависящий от порядка: «A и B» — тот же случай, что «B и A»."""
    titles = sorted((str(left or "").strip().lower(), str(right or "").strip().lower()))
    return hashlib.sha1(KEY_SEPARATOR.join(titles).encode("utf-8")).hexdigest()


def may_ask(left: Any, right: Any) -> bool:
    """Можно ли вообще спрашивать модель об этой паре.

    Только спорный случай: правило воздержалось из-за неуказанной фасовки, а отличительные слова
    совпали. Пары с противоречием и уже признанные одним товаром модели не показываются.
    """
    import catalog_quality
    if not catalog_quality.comparable(left, right)[0]:
        return False
    if catalog_quality.same_product(left, right):
        return False
    if not catalog_quality.uncertain(left, right)[0]:
        return False
    tokens = catalog_quality.identity_tokens(left)
    return bool(tokens) and tokens == catalog_quality.identity_tokens(right)


def build_prompt(left: Any, right: Any) -> str:
    import ai_router
    data = json.dumps({"предложение_1": str(left or "")[:MAX_TITLE_CHARS],
                       "предложение_2": str(right or "")[:MAX_TITLE_CHARS]},
                      ensure_ascii=False, indent=1)
    return (
        "Ты сравниваешь два названия товаров из разных магазинов Казахстана. Вопрос один: это одно и то "
        "же предложение, цены которого можно сравнивать?\n"
        "Разная фасовка, разный объём, разное количество в упаковке, разная память или диагональ — это "
        "РАЗНЫЕ товары. Если данных не хватает, отвечай «не уверен» — ошибочное сравнение вреднее "
        "пропуска.\n"
        'Ответь строго JSON: {"same": true|false, "confidence": 0..1, "reason": "коротко по-русски"}.\n'
        + ai_router.untrusted_block(data, "ДАННЫЕ"))


def parse(value: Any) -> Optional[Dict[str, Any]]:
    """Разбор ответа модели. Непонятный ответ — это отсутствие ответа, а не «наверное, да»."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return None
    if not isinstance(value, dict) or "same" not in value:
        return None
    same = value.get("same")
    if not isinstance(same, bool):
        return None
    raw = value.get("confidence")
    # True вместо числа — это не «уверенность 1.0», а отсутствие уверенности (M11)
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        return None
    try:
        confidence = float(raw)
    except (TypeError, ValueError):
        return None
    if not 0.0 <= confidence <= 1.0:
        return None
    # Уверенность хранится как есть: округление 0.8999 до 0.9 подняло бы её до порога принятия (M11).
    # Округляется только показ.
    return {"same": same, "confidence": confidence,
            "reason": str(value.get("reason") or "")[:300]}


def accepted(decision: Optional[Dict[str, Any]]) -> bool:
    """Принимается только уверенное «да»: «нет» и «не уверен» оставляют прежнее поведение.

    Сравнивается исходная уверенность, а не округлённая для показа: 0.8999 — это ниже порога 0.90,
    и таким оно и должно остаться (M11).
    """
    if not decision:
        return False
    raw = decision.get("confidence")
    if isinstance(raw, bool):
        return False
    try:
        confidence = float(raw)
    except (TypeError, ValueError):
        return False
    return bool(decision.get("same")) and confidence >= MIN_CONFIDENCE


def show_confidence(value: Any) -> Optional[float]:
    """Уверенность для показа человеку — округление только здесь."""
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return None


async def resolve(left: Any, right: Any, config: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Спрашивает модель об одной спорной паре. Возвращает решение или None, если ответа нет."""
    import ai_router
    if not may_ask(left, right):
        return None
    try:
        routed = await ai_router.run("catalog_matching", build_prompt(left, right),
                                     validate=parse, cache_key=pair_key(left, right), config=config)
    except ai_router.AIUnavailable:
        return None
    decision = parse(routed.get("result"))
    if decision is None:
        return None
    decision["provider"] = routed.get("provider") or routed.get("source")
    return decision


async def resolve_pending(limit: int = MAX_PAIRS_PER_RUN,
                          now: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Разбирает накопившиеся спорные пары. В режиме off не делает ничего."""
    import database
    current = mode()
    result = {"mode": current, "asked": 0, "decided": 0, "accepted": 0}
    if current == OFF:
        return result
    moment = now or datetime.datetime.now(datetime.timezone.utc)
    for pair in database.pending_matching_pairs(limit=limit):
        result["asked"] += 1
        decision = await resolve(pair["left_title"], pair["right_title"])
        if decision is None:
            continue
        database.save_matching_decision(pair["pair_key"], decision["same"], decision["confidence"],
                                        decision.get("reason", ""), decision.get("provider"),
                                        current, moment)
        result["decided"] += 1
        result["accepted"] += 1 if accepted(decision) else 0
    return result


def evaluate_with_ai(pairs, decide: Callable[[Any, Any], Optional[Dict[str, Any]]]) -> Dict[str, Any]:
    """Замер AI-пути на проверочном наборе рядом с прежними цифрами.

    `decide` возвращает решение модели для спорной пары (или None). Правила остаются первыми: модель
    спрашивают только о тех парах, где правило воздержалось.
    """
    import catalog_quality
    base = catalog_quality.evaluate(pairs)
    tp = fp = tn = fn = 0
    resolved: List[Dict[str, Any]] = []
    for pair in pairs:
        left, right = pair["left"], pair["right"]
        decision = catalog_quality.same_product(left, right)
        if not decision and may_ask(left, right):
            verdict = decide(left, right)
            if accepted(verdict):
                decision = True
                resolved.append({"left": left, "right": right,
                                 "confidence": verdict.get("confidence"), "same": pair["same"]})
        if decision and pair["same"]:
            tp += 1
        elif decision and not pair["same"]:
            fp += 1
        elif not decision and pair["same"]:
            fn += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    meets = precision >= REQUIRED_PRECISION and recall >= REQUIRED_RECALL
    return {
        "rules": {"precision": base["precision"], "recall": base["recall"]},
        "with_ai": {"precision": round(precision, 4), "recall": round(recall, 4),
                    "tp": tp, "fp": fp, "tn": tn, "fn": fn},
        "resolved_by_ai": resolved,
        "agreed_thresholds": {"confidence": MIN_CONFIDENCE, "precision": REQUIRED_PRECISION,
                              "recall": REQUIRED_RECALL, "agreed_at": AGREED_AT},
        "meets_thresholds": meets,
        "note": ("Пороги согласованы до замера. Включать AI-путь в работу можно только при их выполнении."
                 if meets else
                 "Пороги не выполнены: AI-путь остаётся в теневом режиме, сравнение цен не меняется."),
    }
