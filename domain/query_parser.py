"""
Query Parser 2.0 for KZ Price Hunter Search Platform.
Extracts intent, condition, city, brand, price constraints, and attributes from natural queries.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from domain.models import Condition, ChannelType


# ---------------------------------------------------------------------------
# Kazakhstan City Mapping (Russian, Kazakh, Translit)
# ---------------------------------------------------------------------------
KZ_CITIES_MAP: Dict[str, str] = {
    # Астана
    "астана": "Астана",
    "астане": "Астана",
    "астану": "Астана",
    "астаной": "Астана",
    "нур-султан": "Астана",
    "нурсултан": "Астана",
    "astana": "Астана",
    # Алматы
    "алматы": "Алматы",
    "алма-ата": "Алматы",
    "алмаата": "Алматы",
    "алмате": "Алматы",
    "алмату": "Алматы",
    "almaty": "Алматы",
    # Шымкент
    "шымкент": "Шымкент",
    "шымкенте": "Шымкент",
    "шымкента": "Шымкент",
    "чимкент": "Шымкент",
    "shymkent": "Шымкент",
    # Караганда
    "караганда": "Караганда",
    "караганде": "Караганда",
    "караганды": "Караганда",
    "қарағанды": "Караганда",
    "karaganda": "Караганда",
    # Актобе
    "актобе": "Актобе",
    "актөбе": "Актобе",
    "актобинск": "Актобе",
    "aktobe": "Актобе",
    # Тараз
    "тараз": "Тараз",
    "таразе": "Тараз",
    "taraz": "Тараз",
    # Павлодар
    "павлодар": "Павлодар",
    "павлодаре": "Павлодар",
    "pavlodar": "Павлодар",
    # Усть-Каменогорск
    "усть-каменогорск": "Усть-Каменогорск",
    "устькаменогорск": "Усть-Каменогорск",
    "оскемен": "Усть-Каменогорск",
    "өскемен": "Усть-Каменогорск",
    "oskemen": "Усть-Каменогорск",
    # Семей
    "семей": "Семей",
    "семее": "Семей",
    "семипалатинск": "Семей",
    "semey": "Семей",
    # Атырау
    "атырау": "Атырау",
    "atyrau": "Атырау",
    # Костанай
    "костанай": "Костанай",
    "қостанай": "Костанай",
    "kostanay": "Костанай",
    # Кызылорда
    "кызылорда": "Кызылорда",
    "қызылорда": "Кызылорда",
    "kyzylorda": "Кызылорда",
    # Уральск
    "уральск": "Уральск",
    "уральске": "Уральск",
    "орал": "Уральск",
    "uralsk": "Уральск",
    # Петропавловск
    "петропавловск": "Петропавловск",
    "петропавл": "Петропавловск",
    "petropavlovsk": "Петропавловск",
    # Актау
    "актау": "Актау",
    "ақтау": "Актау",
    "aktau": "Актау",
    # Темиртау
    "темиртау": "Темиртау",
    "temirtau": "Темиртау",
    # Туркестан
    "туркестан": "Туркестан",
    "түркістан": "Туркестан",
    "turkestan": "Туркестан",
    # Кокшетау
    "кокшетау": "Кокшетау",
    "көкшетау": "Кокшетау",
    "kokshetau": "Кокшетау",
    # Талдыкорган
    "талдыкорган": "Талдыкорган",
    "талдықорған": "Талдыкорган",
    "taldykorgan": "Талдыкорган",
    # Экибастуз
    "экибастуз": "Экибастуз",
    "ekibastuz": "Экибастуз",
    # Рудный
    "рудный": "Рудный",
    "рудном": "Рудный",
    "rudny": "Рудный",
}


# ---------------------------------------------------------------------------
# Brand Synonyms and Normalization
# ---------------------------------------------------------------------------
BRAND_MAPPINGS: Dict[str, str] = {
    "apple": "Apple",
    "айфон": "Apple",
    "эппл": "Apple",
    "эпл": "Apple",
    "iphone": "Apple",
    "ipad": "Apple",
    "macbook": "Apple",
    "airpods": "Apple",
    "samsung": "Samsung",
    "самсунг": "Samsung",
    "galaxy": "Samsung",
    "xiaomi": "Xiaomi",
    "сяоми": "Xiaomi",
    "ксиоми": "Xiaomi",
    "redmi": "Xiaomi",
    "редми": "Xiaomi",
    "poco": "Xiaomi",
    "поко": "Xiaomi",
    "asus": "Asus",
    "асус": "Asus",
    "lenovo": "Lenovo",
    "леново": "Lenovo",
    "acer": "Acer",
    "эйсер": "Acer",
    "асер": "Acer",
    "hp": "HP",
    "хп": "HP",
    "dell": "Dell",
    "делл": "Dell",
    "sony": "Sony",
    "сони": "Sony",
    "playstation": "Sony",
    "ps5": "Sony",
    "ps4": "Sony",
    "lg": "LG",
    "элджи": "LG",
    "huawei": "Huawei",
    "хуавей": "Huawei",
    "honor": "Honor",
    "хонор": "Honor",
    "dyson": "Dyson",
    "дайсон": "Dyson",
    "bosch": "Bosch",
    "бош": "Bosch",
    "philips": "Philips",
    "филипс": "Philips",
    "canon": "Canon",
    "кэнон": "Canon",
    "nikon": "Nikon",
    "никон": "Nikon",
    "xbox": "Microsoft",
    "хбокс": "Microsoft",
    "microsoft": "Microsoft",
    "майкрософт": "Microsoft",
    "nintendo": "Nintendo",
    "нинтендо": "Nintendo",
}


# ---------------------------------------------------------------------------
# Channel / Marketplace Indicators
# ---------------------------------------------------------------------------
CHANNEL_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"\b(?:на|в)\s+каспи\b|\b(?:на|в)\s+kaspi(?:\.kz)?\b|\bkaspi\b", re.IGNORECASE), ChannelType.KASPI),
    (re.compile(r"\b(?:на|в)\s+форте\b|\b(?:на|в)\s+forte(?:\.kz)?\b|\bforte\s*market\b", re.IGNORECASE), ChannelType.FORTE),
    (re.compile(r"\b(?:на|в)\s+халык\b|\b(?:на|в)\s+halyk(?:\.kz)?\b|\bhalyk\s*market\b", re.IGNORECASE), ChannelType.HALYK),
    (re.compile(r"\b(?:в|через)\s+(?:тг|телеграм|телеграме|tg|telegram)\b", re.IGNORECASE), ChannelType.TELEGRAM),
    (re.compile(r"\b(?:в|через)\s+(?:инст[ае]|инстаграм[е]?|instagram)\b", re.IGNORECASE), ChannelType.INSTAGRAM),
    (re.compile(r"\b(?:в|через)\s+(?:вацап|ватсап|whatsapp)\b", re.IGNORECASE), ChannelType.WHATSAPP),
]


@dataclass
class ParsedQuery:
    """Structured representation of a user search request."""
    raw_query: str
    clean_query: str
    detected_brand: Optional[str] = None
    detected_city: Optional[str] = None
    condition: Optional[str] = None
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    channel_type: Optional[str] = None
    attributes: Dict[str, str] = field(default_factory=dict)
    tokens: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class QueryParser:
    """
    Parses natural Russian/Kazakh/English e-commerce search queries into
    deterministic structured filters and cleaned text query.
    """

    @classmethod
    def parse(cls, query: str) -> ParsedQuery:
        raw = (query or "").strip()
        if not raw:
            return ParsedQuery(raw_query="", clean_query="", tokens=[])

        text = raw
        extracted_city: Optional[str] = None
        extracted_condition: Optional[str] = None
        extracted_brand: Optional[str] = None
        extracted_min_price: Optional[float] = None
        extracted_max_price: Optional[float] = None
        extracted_channel: Optional[str] = None
        attributes: Dict[str, str] = {}

        # 1. Channel detection
        for pat, ctype in CHANNEL_PATTERNS:
            if pat.search(text):
                extracted_channel = ctype
                text = pat.sub(" ", text)

        # 2. Price range extraction
        # "от 100000 до 500000", "от 100k", "до 500 000 тг"
        price_range_pat = re.compile(
            r"\b(?:от\s+(\d[\d\s]*(?:k|к)?))?\s*(?:до\s+(\d[\d\s]*(?:k|к)?))\s*(?:тг|тенге|kzt)?\b",
            re.IGNORECASE,
        )
        range_match = price_range_pat.search(text)
        if range_match:
            p_from, p_to = range_match.groups()
            if p_from:
                extracted_min_price = cls._parse_price_val(p_from)
            if p_to:
                extracted_max_price = cls._parse_price_val(p_to)
            text = price_range_pat.sub(" ", text)
        else:
            # Standalone "до 500000"
            to_pat = re.compile(r"\bдо\s+(\d[\d\s]*(?:k|к)?)\s*(?:тг|тенге|kzt)?\b", re.IGNORECASE)
            to_match = to_pat.search(text)
            if to_match:
                extracted_max_price = cls._parse_price_val(to_match.group(1))
                text = to_pat.sub(" ", text)

            # Standalone "от 100000"
            from_pat = re.compile(r"\bот\s+(\d[\d\s]*(?:k|к)?)\s*(?:тг|тенге|kzt)?\b", re.IGNORECASE)
            from_match = from_pat.search(text)
            if from_match:
                extracted_min_price = cls._parse_price_val(from_match.group(1))
                text = from_pat.sub(" ", text)

        # 3. Condition extraction
        # "б/у", "бу", "бывший в употреблении", "used", "с пробегом" -> USED
        used_pat = re.compile(r"\b(?:б[/\s._-]?у|бу|used|second[-_\s]?hand|бывш(?:ий|ее|ая|ие)?[-_\s]в[-_\s]употреблении|с[-_\s]пробегом)\b", re.IGNORECASE)
        if used_pat.search(text):
            extracted_condition = Condition.USED
            text = used_pat.sub(" ", text)

        # "refurbished", "восстановленный" -> REFURBISHED
        refurb_pat = re.compile(r"\b(?:refurb(?:ished)?|восстановлен(?:ный|ное|ная|ные)?|после[-_\s]?ремонта|после[-_\s]?сц)\b", re.IGNORECASE)
        if refurb_pat.search(text):
            extracted_condition = Condition.REFURBISHED
            text = refurb_pat.sub(" ", text)

        # "open box", "витринный", "уценка" -> OPEN_BOX
        openbox_pat = re.compile(r"\b(?:open[-_\s]?box|витрин(?:а|ный|ное|ная|ные)?|уценк(?:а|и)?|уценен(?:ный|ное|ная|ные)?)\b", re.IGNORECASE)
        if openbox_pat.search(text):
            extracted_condition = Condition.OPEN_BOX
            text = openbox_pat.sub(" ", text)

        # "новый", "new", "запечатанный" -> NEW
        new_pat = re.compile(r"\b(?:нов(?:ый|ое|ая|ые)|new|запечатан(?:ный|ное|ная|ные)?)\b", re.IGNORECASE)
        if new_pat.search(text):
            extracted_condition = Condition.NEW
            text = new_pat.sub(" ", text)

        # 4. Storage / Memory Attribute extraction
        # 4a. Explicit "256gb", "512 гб", "1tb", "1 тб"
        storage_pat = re.compile(r"\b(64|128|256|512)\s*(?:gb|гб)\b|\b(1|2)\s*(?:tb|тб)\b", re.IGNORECASE)
        storage_match = storage_pat.search(text)
        if storage_match:
            gb_val, tb_val = storage_match.groups()
            if gb_val:
                attributes["storage"] = f"{gb_val}gb"
            elif tb_val:
                attributes["storage"] = f"{tb_val}tb"

        # 5. City extraction & Preposition cleanup
        words = re.findall(r"[\w\-]+", text)
        clean_words: List[str] = []
        city_prepositions = {"в", "по", "на", "из", "c"}

        for w in words:
            w_lower = w.lower()
            if not extracted_city and w_lower in KZ_CITIES_MAP:
                extracted_city = KZ_CITIES_MAP[w_lower]
                # If preceding word was a preposition like "в", remove it from clean_words
                if clean_words and clean_words[-1].lower() in city_prepositions:
                    clean_words.pop()
                continue
            clean_words.append(w)

        # 4b. Standalone storage numbers without "gb" (e.g. "айфон 16 про 256")
        if "storage" not in attributes:
            standalone_storage_candidates = {"64", "128", "256", "512", "1024"}
            for w in clean_words:
                if w in standalone_storage_candidates:
                    attributes["storage"] = f"{w}gb"
                    break

        # 6. Brand extraction
        for w in clean_words:
            w_lower = w.lower()
            if w_lower in BRAND_MAPPINGS:
                extracted_brand = BRAND_MAPPINGS[w_lower]
                break

        # Reconstruct clean search query
        clean_query = " ".join(clean_words).strip()
        tokens = [t.lower() for t in clean_words if len(t) > 1]

        return ParsedQuery(
            raw_query=raw,
            clean_query=clean_query or raw,
            detected_brand=extracted_brand,
            detected_city=extracted_city,
            condition=extracted_condition,
            min_price=extracted_min_price,
            max_price=extracted_max_price,
            channel_type=extracted_channel,
            attributes=attributes,
            tokens=tokens,
        )

    @staticmethod
    def _parse_price_val(raw_str: str) -> Optional[float]:
        if not raw_str:
            return None
        cleaned = raw_str.lower().replace(" ", "").replace("\xa0", "")
        multiplier = 1.0
        if cleaned.endswith("k") or cleaned.endswith("к"):
            multiplier = 1000.0
            cleaned = cleaned[:-1]
        try:
            return float(cleaned) * multiplier
        except (ValueError, TypeError):
            return None
