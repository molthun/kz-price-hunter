"""Качество сопоставления товаров (этап P09): фасовка, объём и упаковка как часть личности товара.

Зачем: сравнивать цену Coca-Cola 1 л с ценой 1,5 л — значит показать человеку выгоду, которой нет.
То же с кормом (Whiskas 75 г ≠ 85 г), бумагой (4 рулона ≠ 8), памятью телефона и вариантом SIM.

Правила здесь детерминированные и работают без AI. Где правило честно говорит «не знаю» (фасовка указана
только у одного предложения), товары одним не признаются, а случай записывается в теневой отчёт — это
будущая работа для модели.

Согласовано с владельцем до замера (2026-09-20): точность (доля верных среди признанных одинаковыми)
не ниже 99 % на проверочном наборе — ложное сравнение хуже пропущенного совпадения.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, Optional, Tuple

PRECISION_TARGET = 0.99      # согласовано до измерения
RECALL_FLOOR = 0.70          # ниже этого правило стало бы бесполезно осторожным

# Единицы приводятся к базовым: миллилитры, граммы, метры, штуки
_UNIT_FACTORS = {
    "мл": ("ml", 1.0), "ml": ("ml", 1.0), "л": ("ml", 1000.0), "l": ("ml", 1000.0), "литр": ("ml", 1000.0),
    "литра": ("ml", 1000.0), "литров": ("ml", 1000.0),
    "г": ("g", 1.0), "гр": ("g", 1.0), "g": ("g", 1.0), "грамм": ("g", 1.0), "грамма": ("g", 1.0),
    "граммов": ("g", 1.0), "кг": ("g", 1000.0), "kg": ("g", 1000.0), "мг": ("g", 0.001), "mg": ("g", 0.001),
    "м": ("m", 1.0), "метр": ("m", 1.0), "метра": ("m", 1.0), "метров": ("m", 1.0),
    "см": ("m", 0.01), "мм": ("m", 0.001),
}

# «2 шт», «х6», «упаковка 12», «набор из 3»
_RE_PACK = re.compile(
    r"(?:(?:набор|упаковка|уп|комплект|пачка)\s*(?:из\s*)?(\d{1,3})\s*(?:штук\w*|шт\.?)?)"
    r"|(?:(\d{1,3})\s*(?:штук\w*|шт\.?))"
    r"|(?:\b[xх*]\s?(\d{1,3})\b)",
    re.IGNORECASE)

_RE_QUANTITY = re.compile(
    r"(?<![\w.,])(\d{1,5}(?:[.,]\d{1,3})?)\s*(мл|ml|литров|литра|литр|л\b|l\b|мг|mg|кг|kg|гр\b|грамм\w*|г\b|g\b|см|мм|метров|метра|метр|м\b)",
    re.IGNORECASE)

# Единицы, которые к фасовке не относятся: экран, память, частота и прочие характеристики
_RE_SCREEN = re.compile(r"\d+(?:[.,]\d+)?\s*(?:\"|''|дюйм\w*)", re.IGNORECASE)
_RE_MEMORY = re.compile(r"\d+\s*(?:gb|гб|tb|тб|mb|мб)\b", re.IGNORECASE)


def _clean(title: Any) -> str:
    text = unicodedata.normalize("NFKC", str(title or "")).lower().replace("ё", "е")
    text = _RE_SCREEN.sub(" ", text)
    text = _RE_MEMORY.sub(" ", text)
    return text


def quantity(title: Any) -> Optional[Dict[str, Any]]:
    """Фасовка из названия: {'unit': 'ml'|'g'|'m', 'value': в базовых единицах} или None.

    Берётся последнее указание в названии: «Сок Rich 1 л» и «Молоко 2,5% 900 мл» — первое число
    процентов фасовкой не является, потому что после него нет единицы объёма.
    """
    text = _clean(title)
    found = None
    for match in _RE_QUANTITY.finditer(text):
        raw_value, raw_unit = match.group(1), match.group(2).strip()
        unit_key = raw_unit.rstrip(".")
        base = _UNIT_FACTORS.get(unit_key)
        if not base:
            continue
        try:
            value = float(raw_value.replace(",", "."))
        except ValueError:
            continue
        if value <= 0:
            continue
        unit, factor = base
        found = {"unit": unit, "value": round(value * factor, 3)}
    return found


def pack_count(title: Any) -> Optional[int]:
    """Число единиц в упаковке: «4 шт», «х6», «набор из 3». Не найдено — None (а не 1)."""
    text = _clean(title)
    counts = [int(g) for match in _RE_PACK.finditer(text) for g in match.groups() if g]
    counts = [c for c in counts if 1 <= c <= 500]
    return counts[-1] if counts else None


def signature(title: Any) -> Dict[str, Any]:
    """Фасовочная подпись товара: объём/вес и количество в упаковке."""
    return {"quantity": quantity(title), "pack": pack_count(title)}


def _spec_conflict(left_title: Any, right_title: Any) -> str:
    """Явное противоречие характеристик: разная память, оперативная память или диагональ."""
    left, right = specs(left_title), specs(right_title)
    labels = {"ram_gb": "оперативная память", "storage_gb": "память", "diagonal": "диагональ"}
    for field, label in labels.items():
        a, b = left[field], right[field]
        if a is not None and b is not None and a != b:
            return f"разная {label}: {a:g} и {b:g}"
    return ""


def comparable(left_title: Any, right_title: Any) -> Tuple[bool, str]:
    """Можно ли сравнивать эти два предложения как один товар.

    Возвращает (можно, причина). «Можно» не означает «одинаковые» — это проверка на явные противоречия
    (фасовка, упаковка, память, диагональ) поверх обычного сопоставления модели.
    """
    spec = _spec_conflict(left_title, right_title)
    if spec:
        return False, spec
    left, right = signature(left_title), signature(right_title)
    lq, rq = left["quantity"], right["quantity"]
    if lq and rq:
        if lq["unit"] != rq["unit"]:
            return False, f"разные единицы: {lq['unit']} и {rq['unit']}"
        if abs(lq["value"] - rq["value"]) > 0.001:
            return False, f"разная фасовка: {_human(lq)} и {_human(rq)}"
    lp, rp = left["pack"], right["pack"]
    if lp and rp and lp != rp:
        return False, f"разное количество в упаковке: {lp} и {rp}"
    return True, ""


def uncertain(left_title: Any, right_title: Any) -> Tuple[bool, str]:
    """Случай, где правило честно не знает: у одного фасовка указана, у другого нет.

    «Whiskas 85 г» и просто «Whiskas» могут оказаться разной фасовкой, поэтому по одним отличительным
    словам такие пары одним товаром не признаются: цена сравнивалась бы вслепую. Совпадение по модели
    (техника с артикулом) это не затрагивает. Все такие случаи попадают в теневой отчёт — это работа
    для будущей модели, которая сможет посмотреть карточку товара.
    """
    left, right = signature(left_title), signature(right_title)
    if bool(left["quantity"]) != bool(right["quantity"]):
        return True, "фасовка указана только у одного предложения"
    if bool(left["pack"]) != bool(right["pack"]):
        return True, "количество в упаковке указано только у одного предложения"
    return False, ""


def _human(q: Dict[str, Any]) -> str:
    value, unit = q["value"], q["unit"]
    if unit == "ml":
        return f"{value / 1000:g} л" if value >= 1000 else f"{value:g} мл"
    if unit == "g":
        return f"{value / 1000:g} кг" if value >= 1000 else f"{value:g} г"
    return f"{value:g} м"


# --- Детерминированное сопоставление одинаковых товаров (P09) ---------------------------------
# Одинаковый товар в двух магазинах подписан по-разному: «Напиток Coca-Cola 1 л» и «Кока-Кола 1000 мл».
# Ниже — только явные и проверяемые правила: слова категории, фасовка и диагональ выносятся за скобки,
# сравнивается то, что осталось. Ничего не «угадывается»: если после очистки не осталось отличительного
# признака (цифры или бренда), товары одинаковыми не признаются.

# Слова, которые описывают вид товара, а не его личность. «Для кошек» и «для собак» сюда НЕ входят:
# это назначение товара, и корм для кошек не заменяет корм для собак (P09 I01).
_GENERIC = set("""напиток вода сок молоко мука кофе чай корм шампунь порошок салфетки бумага туалетная
влажные стиральный смартфон телефон мобильный ноутбук планшет телевизор монитор
приставка игровая наушники робот пылесос кабель защитное стекло чехол упаковка набор""".split())

# Спецификации, которые пишут не везде и которые не меняют товар
_SOFT_SPECS = {"4k", "uhd", "fhd", "hd", "smart", "wifi", "wi-fi", "bluetooth", "новый", "оригинал"}

# Написание брендов по-русски и латиницей
_BRAND_SYNONYMS = {
    "кока": "coca", "кола": "cola", "кока-кола": "coca cola", "пепси": "pepsi", "вискас": "whiskas",
    "ариэль": "ariel", "тайд": "tide", "хаггис": "huggies", "зева": "zewa", "якобс": "jacobs",
    "нескафе": "nescafe", "самсунг": "samsung", "эппл": "apple", "сяоми": "xiaomi", "ксиоми": "xiaomi",
    "редми": "redmi", "сони": "sony", "асус": "asus", "тассай": "tassay", "цесна": "cesna",
    "простоквашино": "prostokvashino",
}

_RE_RAM_STORAGE = re.compile(r"\b(\d{1,2})\s*/\s*(\d{2,4})\s*(gb|гб)\b", re.IGNORECASE)
_RE_TB = re.compile(r"\b(\d+)\s*(?:tb|тб)\b", re.IGNORECASE)
_RE_GB = re.compile(r"\b(\d+)\s*(?:gb|гб)\b", re.IGNORECASE)


def specs(title: Any) -> Dict[str, Any]:
    """Характеристики, которые прямо названы в заголовке: память, оперативная память, диагональ.

    Они не стираются из личности товара (P09 I01): 8/512 ГБ и 16/512 ГБ — разные ноутбуки, 32" и 43" —
    разные телевизоры. Неуказанная характеристика — это «неизвестно», а не «совпадает».
    """
    text = unicodedata.normalize("NFKC", str(title or "")).lower().replace("ё", "е")
    text = _RE_TB.sub(lambda m: f" {int(m[1]) * 1024}gb ", text)
    ram = storage = diagonal = None
    ram_match = _RE_RAM_STORAGE.search(text)
    if ram_match:
        ram, storage = int(ram_match[1]), int(ram_match[2])
    else:
        # Объёмы могут стоять отдельными словами: «MacBook Air M2 8GB 256GB» — это 8 ГБ оперативной
        # и 256 ГБ накопителя. Меньший объём считается оперативной памятью только когда их два и более.
        capacities = sorted({int(m[1]) for m in _RE_GB.finditer(text)})
        if capacities:
            storage = capacities[-1]
            ram = capacities[0] if len(capacities) > 1 else None
    screen = _RE_SCREEN.search(text)
    if screen:
        try:
            diagonal = float(re.sub(r"[^\d.,]", "", screen.group(0)).replace(",", "."))
        except ValueError:
            diagonal = None
    return {"ram_gb": ram, "storage_gb": storage, "diagonal": diagonal}


def identity_tokens(title: Any) -> frozenset:
    """Отличительные слова товара: без слов категории, фасовки, диагонали и мелких спецификаций.

    Память и диагональ здесь не участвуют — они сравниваются точно, через specs().
    """
    text = unicodedata.normalize("NFKC", str(title or "")).lower().replace("ё", "е")
    text = _RE_TB.sub(lambda m: f" {int(m[1]) * 1024}gb ", text)
    text = _RE_RAM_STORAGE.sub(" ", text)
    text = _RE_GB.sub(" ", text)
    text = _RE_SCREEN.sub(" ", text)
    text = _RE_QUANTITY.sub(" ", text)               # фасовка сравнивается отдельно и точно
    text = _RE_PACK.sub(" ", text)
    tokens = []
    for token in re.findall(r"[a-zа-я0-9]+(?:-[a-zа-я0-9]+)*", text):
        # Дефис — это разное написание одного бренда: «coca-cola» и «Кока-Кола» должны сойтись
        token = _BRAND_SYNONYMS.get(token, token).replace("-", " ")
        for part in token.split():
            part = _BRAND_SYNONYMS.get(part, part)
            if part in _GENERIC or part in _SOFT_SPECS or len(part) < 2 and not part.isdigit():
                continue
            tokens.append(part)
    return frozenset(tokens)


def _distinctive(tokens: frozenset) -> bool:
    """Есть ли в наборе то, что отличает товар: цифра модели или узнаваемый бренд."""
    if any(any(ch.isdigit() for ch in t) for t in tokens):
        return True
    return any(t in set(_BRAND_SYNONYMS.values()) for t in tokens)


def same_product(left: Any, right: Any) -> bool:
    """Один и тот же товар: нет противоречий И совпала модель (или отличительные слова).

    Проверки идут в таком порядке специально (P09 I01, I02): сначала явные противоречия характеристик и
    фасовки, затем неизвестная с одной стороны фасовка, и только потом положительное решение. Совпадение
    по модели не разрешает сравнивать упаковку из 2 штук с одиночным товаром.
    """
    if not comparable(left, right)[0]:
        return False
    if uncertain(left, right)[0]:
        return False
    from model_matching import same_model
    if same_model(left, right):
        return True
    left_tokens, right_tokens = identity_tokens(left), identity_tokens(right)
    if not left_tokens or left_tokens != right_tokens:
        return False
    return _distinctive(left_tokens)


def evaluate(pairs) -> Dict[str, Any]:
    """Оценка правила на проверочном наборе: точность и полнота с разбором ошибок.

    pairs — записи {"left", "right", "same": bool}. Решение «один товар» принимается, только если
    совпала модель И нет противоречия по фасовке.
    """
    tp = fp = tn = fn = 0
    false_positives, false_negatives = [], []
    for pair in pairs:
        left, right = pair["left"], pair["right"]
        decision = same_product(left, right)
        if decision and pair["same"]:
            tp += 1
        elif decision and not pair["same"]:
            fp += 1
            false_positives.append(pair)
        elif not decision and pair["same"]:
            fn += 1
            false_negatives.append(pair)
        else:
            tn += 1
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    # Отдельный разбор по группам: опасные группы (память, диагональ, назначение, фасовка) важнее среднего
    by_group: Dict[str, Dict[str, int]] = {}
    for pair in pairs:
        group = by_group.setdefault(pair.get("group", "—"), {"pairs": 0, "false_positives": 0,
                                                             "false_negatives": 0})
        group["pairs"] += 1
    for pair in false_positives:
        by_group[pair.get("group", "—")]["false_positives"] += 1
    for pair in false_negatives:
        by_group[pair.get("group", "—")]["false_negatives"] += 1
    return {"precision": round(precision, 4), "recall": round(recall, 4),
            "tp": tp, "fp": fp, "tn": tn, "fn": fn, "by_group": by_group,
            "false_positives": false_positives, "false_negatives": false_negatives}
