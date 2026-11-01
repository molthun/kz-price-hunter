"""
Catalog Classifier & Normalizer for KZ Price Hunter.

Provides hybrid classification of products:
1. Fast deterministic rule-based matcher (Master Taxonomy aliases + Title heuristics + Known Brands).
2. Local AI fallback (Ollama qwen3-vl:8b-instruct) for ambiguous, vague or rare items.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from domain.category import Category, TaxonomyMapper, MASTER_TAXONOMY

logger = logging.getLogger(__name__)

# =====================================================================
# Canonical Brand Dictionary (Top Kazakhstan Retail Brands)
# =====================================================================
# Key: normalized lowercase brand name -> Value: Canonical display brand name
KNOWN_BRANDS: Dict[str, str] = {
    # Phones & Mobile
    "apple": "Apple",
    "iphone": "Apple",
    "samsung": "Samsung",
    "xiaomi": "Xiaomi",
    "redmi": "Xiaomi",
    "poco": "Xiaomi",
    "honor": "Honor",
    "huawei": "Huawei",
    "oppo": "OPPO",
    "vivo": "Vivo",
    "realme": "Realme",
    "oneplus": "OnePlus",
    "google": "Google",
    "pixel": "Google",
    "sony": "Sony",
    "motorola": "Motorola",
    "moto": "Motorola",
    "tecno": "Tecno",
    "infinix": "Infinix",
    "nothing": "Nothing",
    "cmf": "Nothing",
    "zte": "ZTE",
    "nubia": "ZTE",
    "meizu": "Meizu",
    "nokia": "Nokia",
    "blackview": "Blackview",
    "ulefone": "Ulefone",
    "oukitel": "Oukitel",
    "doogee": "Doogee",
    "itel": "Itel",

    # Laptops & PC
    "asus": "ASUS",
    "acer": "Acer",
    "lenovo": "Lenovo",
    "hp": "HP",
    "dell": "Dell",
    "msi": "MSI",
    "macbook": "Apple",
    "gigabyte": "Gigabyte",
    "asrock": "ASRock",
    "digma": "Digma",
    "chuwi": "Chuwi",
    "maibenben": "Maibenben",
    "thunderobot": "Thunderobot",
    "hasee": "Hasee",
    "fujitsu": "Fujitsu",

    # PC Components & Hardware
    "intel": "Intel",
    "amd": "AMD",
    "ryzen": "AMD",
    "nvidia": "NVIDIA",
    "geforce": "NVIDIA",
    "palit": "Palit",
    "zotac": "Zotac",
    "inno3d": "Inno3D",
    "gainward": "Gainward",
    "colorful": "Colorful",
    "sapphire": "Sapphire",
    "powercolor": "PowerColor",
    "xfx": "XFX",
    "kingston": "Kingston",
    "fury": "Kingston",
    "crucial": "Crucial",
    "corsair": "Corsair",
    "g.skill": "G.Skill",
    "gskill": "G.Skill",
    "patriot": "Patriot",
    "viper": "Patriot",
    "adata": "ADATA",
    "xpg": "ADATA",
    "netac": "Netac",
    "transcend": "Transcend",
    "lexar": "Lexar",
    "western digital": "Western Digital",
    "wd": "Western Digital",
    "seagate": "Seagate",
    "toshiba": "Toshiba",
    "deepcool": "DeepCool",
    "be quiet!": "be quiet!",
    "be quiet": "be quiet!",
    "cooler master": "Cooler Master",
    "thermaltake": "Thermaltake",
    "aerocool": "AeroCool",
    "cougar": "Cougar",
    "chieftec": "Chieftec",
    "zalman": "Zalman",
    "gamemax": "GameMax",
    "montech": "Montech",
    "lian li": "Lian Li",
    "id-cooling": "ID-Cooling",
    "noctua": "Noctua",

    # Monitors & Displays
    "viewsonic": "ViewSonic",
    "benq": "BenQ",
    "aoc": "AOC",
    "philips": "Philips",
    "lg": "LG",
    "iiyama": "iiyama",

    # Audio & Peripherals
    "logitech": "Logitech",
    "razer": "Razer",
    "hyperx": "HyperX",
    "steelseries": "SteelSeries",
    "a4tech": "A4Tech",
    "bloody": "A4Tech",
    "redragon": "Redragon",
    "defender": "Defender",
    "sven": "Sven",
    "canyon": "Canyon",
    "hator": "Hator",
    "dark project": "Dark Project",
    "keychron": "Keychron",
    "varmilo": "Varmilo",
    "akko": "Akko",
    "jbl": "JBL",
    "marshall": "Marshall",
    "sennheiser": "Sennheiser",
    "audio-technica": "Audio-Technica",
    "bose": "Bose",
    "edifier": "Edifier",
    "harman kardon": "Harman Kardon",
    "bang & olufsen": "Bang & Olufsen",
    "anker": "Anker",
    "soundcore": "Anker",
    "haylou": "Haylou",
    "qcy": "QCY",
    "baseus": "Baseus",
    "ugreen": "UGREEN",
    "hoco": "Hoco",
    "remax": "Remax",
    "borofone": "Borofone",
    "joyroom": "Joyroom",
    "nillkin": "Nillkin",
    "spigen": "Spigen",
    "mocolo": "Mocolo",
    "usams": "USAMS",
    "kuulaa": "KUULAA",

    # Consumables & Supplies
    "filtero": "Filtero",
    "topperr": "Topperr",
    "cactus": "Cactus",
    "nv print": "NV Print",
    "hi-black": "Hi-Black",
    "colorway": "ColorWay",

    # TV & Home Entertainment
    "tcl": "TCL",
    "hisense": "Hisense",
    "haier": "Haier",
    "panasonic": "Panasonic",
    "yasin": "Yasin",
    "kivi": "KIVI",
    "skyworth": "Skyworth",
    "artel": "Artel",
    "polarline": "Polarline",
    "nintendo": "Nintendo",
    "xbox": "Microsoft",
    "playstation": "Sony",
    "valve": "Valve",

    # Large & Small Appliances
    "bosch": "Bosch",
    "beko": "Beko",
    "indesit": "Indesit",
    "gorenje": "Gorenje",
    "electrolux": "Electrolux",
    "whirlpool": "Whirlpool",
    "candy": "Candy",
    "midea": "Midea",
    "hansa": "Hansa",
    "ariston": "Ariston",
    "hotpoint-ariston": "Hotpoint-Ariston",
    "atlant": "Атлант",
    "атлант": "Атлант",
    "delonghi": "DeLonghi",
    "braun": "Braun",
    "tefal": "Tefal",
    "moulinex": "Moulinex",
    "rowenta": "Rowenta",
    "krups": "Krups",
    "redmond": "Redmond",
    "polaris": "Polaris",
    "scarlett": "Scarlett",
    "kitfort": "Kitfort",
    "vitek": "Vitek",
    "centek": "Centek",
    "bork": "BORK",
    "dyson": "Dyson",
    "roborock": "Roborock",
    "dreame": "Dreame",
    "karcher": "Karcher",
    "thomas": "Thomas",
    "zelmer": "Zelmer",

    # Photo & Video
    "canon": "Canon",
    "nikon": "Nikon",
    "fujifilm": "Fujifilm",
    "dji": "DJI",
    "gopro": "GoPro",
    "insta360": "Insta360",

    # Household, Gardening & Others
    "green apple": "Green Apple",
    "lara": "Lara",
    "trolli": "Trolli",
    "sonnen": "Sonnen",
    "mvtag": "MVTAG",
    "devia": "Devia",
    "style shield": "Style Shield",
    "saffi": "Saffi",
    "vipe": "Vipe",
    "ugreen": "Ugreen",
    "viti": "ViTi",
}

# Regex word boundary match pattern for brands (sorted by length desc to match multi-word first)
_SORTED_BRAND_KEYS = sorted(KNOWN_BRANDS.keys(), key=lambda x: -len(x))
_BRAND_PATTERN = re.compile(
    r"(?<![a-zA-Z0-9а-яА-ЯёЁ])(" + "|".join(re.escape(k) for k in _SORTED_BRAND_KEYS) + r")(?![a-zA-Z0-9а-яА-ЯёЁ])",
    re.IGNORECASE
)

# Product Type keyword rules: Title keywords -> Category ID
_TITLE_TYPE_RULES: List[Tuple[re.Pattern, str]] = [
    # Footwear & Apparel (защита от ложного захвата обуви IMAC)
    (re.compile(r"\b(обувь|ботинки|туфли|кроссовки|сапоги|кеды|сандали|балетки|полуботинки|шлепанцы|ботильоны)\b", re.I), "shoes"),

    # Cosmetics & Beauty (защита от ложного захвата косметики MAC Studio / Apple)
    (re.compile(r"\b(консилер|пудра|праймер|тональн(?:ая|ый|ое)|палетк[аи]|помад[аы]|тушь|тени|блеск\s+для\s+губ|румян[аы]|хайлайтер|парфюм|духи|мыло|крем\b|шампунь|гель\b|гель-душ)\b", re.I), "beauty"),

    # Audio & TV (проверяем наушники раньше телефонов, но адаптеры для наушников относим к аксессуарам)
    (re.compile(r"\b(?:переходник|адаптер)\s+(?:для\s+)?наушников\b", re.I), "cables_chargers"),
    (re.compile(r"\b(наушник(?:и|ов)|гарнитур(?:а|ы)|airpods|earpods|tws|вкладыши)\b", re.I), "headphones"),
    (re.compile(r"\b(телевизор|smart\s+tv|oled\s+tv|qled\s+tv)\b", re.I), "tv"),
    (re.compile(r"\b(игров(?:ая|ые)\s+приставк(?:а|и)|игровая\s+консоль|playstation|ps5|xbox|nintendo\s+switch)\b", re.I), "consoles"),

    # Peripherals & Input devices (проверяем ДО планшетов, чтобы Magic Keyboard для iPad не становился планшетом)
    (re.compile(r"\b(клавиатур(?:а|ы)|keyboard|компьютерн(?:ая|ые)\s+мыш(?:ь|и)|мышь\s+беспроводная|magic\s+keyboard|magic\s+mouse|magic\s+trackpad|тачпад|геймпад|apple\s+pencil|стилус|stylus)\b", re.I), "peripherals"),

    # Phones & Tablets
    (re.compile(r"\b(смартфон|айфон|iphone|сотовый\s+телефон)\b", re.I), "phones"),
    (re.compile(r"\b(планшет|таблет)\b|\bipad\b", re.I), "tablets"),
    (re.compile(r"\b(смарт-час(?:ы|а)|умные\s+часы|фитнес-браслет|(?:apple\s+)?watch\s*(?:nike\s+)?series|apple\s+watch|galaxy\s+watch)\b", re.I), "smartwatches"),

    # Laptops & Computers
    (re.compile(r"\b(ноутбук|ультрабук|macbook|лэптоп)\b", re.I), "laptops"),
    (re.compile(r"\b(системный\s+блок|компьютер|моноблок|неттоп|desktop\s+pc|imac|mac\s+mini|mac\s+studio|mac\s+pro)\b", re.I), "pc_desktops"),
    (re.compile(r"\b(монитор|дисплей|studio\s+display|pro\s+display\s+xdr)\b", re.I), "monitors"),

    # Hardware Components
    (re.compile(r"\b(видеокарт(?:а|ы)|geforce\s+rtx|radeon\s+rx|графический\s+процессор)\b", re.I), "gpus"),
    (re.compile(r"\b(процессор|intel\s+core|amd\s+ryzen)\b", re.I), "cpus"),
    (re.compile(r"\b(материнск(?:ая|ие)\s+плат(?:а|ы)|motherboard)\b", re.I), "motherboards"),
    (re.compile(r"\b(ssd|твердотельный\s+накопитель|жесткий\s+диск|внешний\s+диск|hdd)\b", re.I), "storage"),

    # Appliances & Home
    (re.compile(r"\b(стиральн(?:ая|ые|ую)\s+машин(?:а|ы|у)|стиралка)\b", re.I), "washing_machines"),
    (re.compile(r"\b(холодильник|морозильник|морозильн(?:ая|ые)\s+камер(?:а|ы))\b", re.I), "refrigerators"),
    (re.compile(r"\b(пылесос|робот-пылесос|вертикальный\s+пылесос)\b", re.I), "vacuums"),
    (re.compile(r"\b(кондиционер(?!\s+для\b)|сплит-систем(?:а|ы))\b", re.I), "air_conditioners"),
    (re.compile(r"\b(микроволнов(?:ка|ая\s*печь)|кофемашин(?:а|ы)|кофеварк(?:а|и)|чайник\s+электрический|блендер|мультиварк(?:а|и)|тостер|(?:электро)?мясорубк(?:а|и))\b", re.I), "kitchen_appliances"),
    (re.compile(r"\b(кастрюл(?:я|и)|сковород(?:а|ы))\b", re.I), "kitchenware"),
    (re.compile(r"\b(конфет(?:ы|а)|маршмелоу|шоколад|джем|печенье)\b", re.I), "food"),
    (re.compile(r"\b(шланг|тройник\s+для\s+воды|распределитель\s+воды)\b", re.I), "home"),
]


# Accessory & Consumable detection rules: Title keyword regex -> (category_id, item_kind)
_ACCESSORY_CONSUMABLE_RULES: List[Tuple[re.Pattern, str, str]] = [
    # Phone Cases (исключаем корпус часов/ПК: aluminium case, titanium case, pc case)
    (
        re.compile(
            r"\b(чехол|чехлы|чехол-накладка|чехол-книжка|бампер|phone\s+case|silicone\s+case|clear\s+case|leather\s+case|чехол\s+на|case\s+(?:для|for|на))\b",
            re.I,
        ),
        "phone_cases",
        "accessory",
    ),
    # Protective Glass & Film
    (
        re.compile(
            r"\b(защитн(?:ое|ые|ая|ых)\s+стекл(?:о|а)|защитн(?:ая|ые)\s+пленк(?:а|и)|бронестекл(?:о|а)|стекло\s+(?:для|на)|пленка\s+(?:для|на))\b|"
            r"\b(?:full\s+cover|9h|privacy).*\bстекл(?:о|а)\b",
            re.I,
        ),
        "phone_glass",
        "accessory",
    ),
    # Cables, Chargers & Powerbanks
    (
        re.compile(
            r"\b(кабел[ьи]|зарядн(?:ое|ые)\s+устройств(?:о|а)|сетевое\s+зарядное|адаптер\s+питания|блок\s+питания\s+для|powerbank|повербанк|переходник\s+type|адаптер\s+type|aux\s+кабель|адаптер\s+(?:для|\d+w)|комплект\s+быстр(?:ой|ый)\s+зарядки)\b",
            re.I,
        ),
        "cables_chargers",
        "accessory",
    ),
    # Watch Straps
    (
        re.compile(
            r"^(?:ремешок|ремешки|браслет\s+для\s+часов|watch\s+band|watch\s+strap)\b|\b(?:ремешок\s+для|сменный\s+ремешок)\b",
            re.I,
        ),
        "smartwatches",
        "accessory",
    ),
    # Vacuum Parts & Consumables
    (
        re.compile(r"\b(фильтр[ы]?\s+для\s+пылесоса|мешк[и|ов]\s+для\s+пылесоса|пылесборник[и]?|hepa\s*фильтр|сменн(?:ые|ая)\s+салфетк(?:а|и)|насадк[аи]\s+для\s+пылесоса|щетк[аи]\s+для\s+пылесоса)\b", re.I),
        "vacuum_parts",
        "consumable",
    ),
    # Printer Consumables (Ink, Toner, Cartridges)
    (
        re.compile(r"\b(картридж[и]?|тонер[ы]?|чернил[а]?\s+для|краска\s+для\s+принтера)\b", re.I),
        "printer_ink",
        "consumable",
    ),
]

# Compatibility target extraction for accessories ("для Apple iPhone 15 Pro", "for Galaxy S24")
_COMPATIBILITY_PATTERN = re.compile(
    r"\b(?:для|for|на)\s+([A-Za-z0-9\s\+\-\.]+?)(?:\s+(?:силиконовый|черный|белый|прозрачный|матовый|кожаный|с\s|в\s|$))",
    re.I,
)

# Подарочные бандлы и комплекты ("Смартфон Apple iPhone 17 Pro Max + Наушники AirPods 4 + Адаптер...")
_BUNDLE_GIFT_SPLIT_PAT = re.compile(
    r"\s+\+\s+(?:наушники|наушник|адаптер|подарок|подарки|чехол|стекло|ремешок|powerbank|повербанк|зарядное|блок\s+питания|кабель)\b",
    re.I,
)

# Полная таблица канонических моделей iPhone
IPHONE_CANONICAL_MAP: Dict[str, str] = {
    "18 pro max": "iPhone 18 Pro Max",
    "18 pro": "iPhone 18 Pro",
    "18": "iPhone 18",
    "17 pro max": "iPhone 17 Pro Max",
    "17 pro": "iPhone 17 Pro",
    "17": "iPhone 17",
    "16 pro max": "iPhone 16 Pro Max",
    "16 pro": "iPhone 16 Pro",
    "16 plus": "iPhone 16 Plus",
    "16e": "iPhone 16e",
    "16": "iPhone 16",
    "15 pro max": "iPhone 15 Pro Max",
    "15 pro": "iPhone 15 Pro",
    "15 plus": "iPhone 15 Plus",
    "15": "iPhone 15",
    "14 pro max": "iPhone 14 Pro Max",
    "14 pro": "iPhone 14 Pro",
    "14 plus": "iPhone 14 Plus",
    "14": "iPhone 14",
    "13 pro max": "iPhone 13 Pro Max",
    "13 pro": "iPhone 13 Pro",
    "13 mini": "iPhone 13 mini",
    "13": "iPhone 13",
    "12 pro max": "iPhone 12 Pro Max",
    "12 pro": "iPhone 12 Pro",
    "12 mini": "iPhone 12 mini",
    "12": "iPhone 12",
    "11 pro max": "iPhone 11 Pro Max",
    "11 pro": "iPhone 11 Pro",
    "11": "iPhone 11",
    "se": "iPhone SE",
    "xs max": "iPhone XS Max",
    "xs": "iPhone XS",
    "xr": "iPhone XR",
    "x": "iPhone X",
    "8 plus": "iPhone 8 Plus",
    "8": "iPhone 8",
    "7 plus": "iPhone 7 Plus",
    "7": "iPhone 7",
    "6s plus": "iPhone 6s Plus",
    "6s": "iPhone 6s",
    "6 plus": "iPhone 6 Plus",
    "6": "iPhone 6",
    "5s": "iPhone 5s",
    "5c": "iPhone 5c",
    "5": "iPhone 5",
    "4s": "iPhone 4s",
    "4": "iPhone 4",
    "air": "iPhone Air",
    "duo": "iPhone Duo",
}

_IPHONE_MODEL_REGEX = re.compile(
    r"\biphone\s*"
    r"(?:"
        r"(\d{1,2}\s*(?:pro\s*max|pro|plus|mini|e|s|c)?)|"
        r"(xs\s*max|xs|xr|x)|"
        r"(se(?:\s*(?:202[0-9]|201[6-9]|\d+-\w+|[1-3](?:nd|rd|th)?\s*gen))?)|"
        r"(air|duo)"
    r")\b",
    re.IGNORECASE,
)


def extract_iphone_model(text: str) -> Optional[str]:
    m = _IPHONE_MODEL_REGEX.search(text)
    if not m:
        return None
    raw = (m.group(1) or m.group(2) or m.group(3) or m.group(4) or "").strip().lower()
    raw = re.sub(r"\s+", " ", raw)
    if raw in IPHONE_CANONICAL_MAP:
        return IPHONE_CANONICAL_MAP[raw]
    if raw.startswith("se"):
        return "iPhone SE"
    return f"iPhone {raw.title()}"


_APPLE_WATCH_REGEX = re.compile(
    r"\b(?:apple\s+)?watch\s*(?:(ultra\s*2|ultra)|(se(?:\s*3|\s*\d{4})?)|(?:nike\s+)?series\s*(\d{1,2})|(\d{1,2}))\b|"
    r"\b(ultra\s*2)\b|"
    r"\b(se)\s+gps\b",
    re.IGNORECASE,
)


def extract_apple_watch_model(text: str) -> Optional[str]:
    # Exclude straps and watch chargers
    if re.search(r"^(?:ремешок|браслет|сменный|зарядн|кабель|переходник|чехол)\b|\b(?:для|for)\s+(?:apple\s+)?watch\b", text, re.I):
        return None

    # Handle Series with intervening model numbers, e.g. "Watch 3N492RU/A Series 10" or "Series 11GPS42mm"
    m_series = re.search(r"\b(?:apple\s+)?watch\b.*?\b(?:nike\s+)?series\s*(\d{1,2})", text, re.I)
    if m_series:
        return f"Apple Watch Series {m_series.group(1)}"

    # Handle Nike SE or Series SE
    if re.search(r"\b(?:apple\s+)?watch\b.*?\b(?:nike\s+se|series\s+se|se\s*\d{0,4})\b", text, re.I):
        return "Apple Watch SE"

    # Handle Ultra 2 / Ultra (strictly with watch)
    if re.search(r"\b(?:apple\s+)?watch\b.*?\bultra\s*2\b|\b(?:apple\s+)?watch\s+ultra\s*2\b", text, re.I):
        return "Apple Watch Ultra 2"
    if re.search(r"\b(?:apple\s+)?watch\b.*?\bultra\b|\b(?:apple\s+)?watch\s+ultra\b", text, re.I):
        return "Apple Watch Ultra"

    m = _APPLE_WATCH_REGEX.search(text)
    if not m:
        return None
    ultra = m.group(1) or m.group(5)
    se = m.group(2) or m.group(6)
    series = m.group(3) or m.group(4)
    if ultra:
        u = re.sub(r"\s+", " ", ultra).strip().title()
        return f"Apple Watch {u}"
    if se:
        return "Apple Watch SE"
    if series:
        return f"Apple Watch Series {series}"
    return "Apple Watch"


def extract_apple_audio_model(text: str) -> Optional[str]:
    """Standardizes AirPods and EarPods models."""
    if re.search(r"\b(?:earpods|myqy3zm|mtjy3zm)\b", text, re.I):
        return "EarPods"
    if re.search(r"\bairpods\s+max\b", text, re.I):
        return "AirPods Max"
    if re.search(r"\bairpods\s+pro\s*2\b|\bairpods\s+pro\b.*\b(?:2(?:nd|nd\s*gen|-го\s*поколения)|type-c|usb-c)\b", text, re.I):
        return "AirPods Pro 2"
    if re.search(r"\bairpods\s+pro\b", text, re.I):
        return "AirPods Pro"
    if re.search(r"\bairpods\s*4\b|\bairpods\s*4th\s*gen\b", text, re.I):
        return "AirPods 4"
    if re.search(r"\bairpods\s*3\b|\bairpods\s*3rd\s*gen\b", text, re.I):
        return "AirPods 3"
    if re.search(r"\bairpods\s*2\b|\bairpods\s*2nd\s*gen\b", text, re.I):
        return "AirPods 2"
    if re.search(r"\bairpods\b", text, re.I):
        return "AirPods"
    return None


def extract_mac_model(text: str) -> Optional[str]:
    """Standardizes Mac laptop and desktop models."""
    # Guard against footwear and cosmetics
    if re.search(r"\b(обувь|ботинки|туфли|кроссовки|сапоги|кеды|консилер|пудра|праймер|тональн|палетк|помад|тушь|тени|румян)\b", text, re.I):
        return None

    # Normalize cyrillic 'о' in 'prо'
    text = re.sub(r"\bpr[оo]\b", "pro", text, flags=re.I)

    # MacBook 12
    if re.search(r"\b(?:12\s*(?:[\'\"″]|inch|-inch)?\s+macbook|macbook\s*12)\b", text, re.I):
        return "MacBook 12"

    # MacBook Neo
    if re.search(r"\bmacbook\s+neo\b", text, re.I):
        return "MacBook Neo 13"

    # MacBook Air
    if re.search(r"\b(?:15\s*(?:[\'\"″]|inch|-inch)?\s+macbook\s+air|macbook\s+air\s*(?:15(?:[\'\"″]|inch|\b)|15\.\d|a2941|a3114))\b", text, re.I):
        return "MacBook Air 15"
    if re.search(r"\b(?:13\s*(?:[\'\"″]|inch|-inch)?\s+macbook\s+air|macbook\s+air\s*(?:13(?:[\'\"″]|inch|\b)|13\.\d|a2337|a2681|a3113))\b", text, re.I):
        return "MacBook Air 13"
    if re.search(r"\bmacbook\s+air\b", text, re.I):
        return "MacBook Air"

    # MacBook Pro
    if re.search(r"\b(?:16\s*(?:[\'\"″]|inch|-inch)?\s+macbook\s+pro|macbook\s+pro\s*(?:16(?:[\'\"″]|inch|\b)|16\.\d|a2485|a2780|a2991))\b", text, re.I) or (re.search(r"\b16(?:\.2)?\b", text) and re.search(r"\bmacbook\b", text, re.I)):
        return "MacBook Pro 16"
    if re.search(r"\b(?:14\s*(?:[\'\"″]|inch|-inch)?\s+macbook\s+pro|macbook\s+pro\s*(?:14(?:[\'\"″]|inch|\b)|14\.\d|a2442|a2779|a2918|a2992))\b", text, re.I) or (re.search(r"\b14(?:\.2)?\b", text) and re.search(r"\bmacbook\b", text, re.I)):
        return "MacBook Pro 14"
    if re.search(r"\b(?:13\s*(?:[\'\"″]|inch|-inch)?\s+macbook\s+pro|macbook\s+pro\s*(?:13(?:[\'\"″]|inch|\b)|13\.\d|a2289|a2338))\b", text, re.I):
        return "MacBook Pro 13"
    if re.search(r"\b(?:15\s*(?:[\'\"″]|inch|-inch)?\s+macbook\s+pro|macbook\s+pro\s*(?:15(?:[\'\"″]|inch|\b)|15\.\d))\b", text, re.I):
        return "MacBook Pro 15"
    if re.search(r"\bmacbook\s+pro\b", text, re.I):
        return "MacBook Pro"

    # Desktops: iMac, Mac mini, Mac Studio, Mac Pro
    if re.search(r"\b(?:24\s*(?:[\'\"″]|inch|-inch)?\s+imac|imac\s*(?:24(?:[\'\"″]|inch|\b)|a3247|a2438|a2439))\b", text, re.I):
        return "iMac 24"
    if re.search(r"\bimac\s*27\b", text, re.I):
        return "iMac 27"
    if re.search(r"\bimac\b", text, re.I):
        return "iMac"
    if re.search(r"\bmac\s+mini\b", text, re.I):
        return "Mac mini"
    if re.search(r"\bmac\s+studio\b", text, re.I):
        return "Mac Studio"
    if re.search(r"\bmac\s+pro\b", text, re.I):
        return "Mac Pro"

    return None


def extract_ipad_model(text: str) -> Optional[str]:
    """Standardizes Apple iPad models."""
    # Normalize cyrillic 'о' in 'prо'
    text = re.sub(r"\bpr[оo]\b", "pro", text, flags=re.I)

    # Exclude tablet accessories
    if re.search(r"\b(клавиатур[аы]|keyboard|стилус|stylus|чехол|стекло|пленка|ремешок|кабель|адаптер)\b", text, re.I):
        return None

    # iPad Air
    if re.search(r"\b(?:13\s*(?:[\'\"″]|inch|-inch)?\s+ipad\s+air|ipad\s+air\s*(?:13(?:[\'\"″]|inch|\b)|13\.\d))\b", text, re.I):
        return "iPad Air 13"
    if re.search(r"\b(?:11\s*(?:[\'\"″]|inch|-inch)?\s+ipad\s+air|ipad\s+air\s*(?:11(?:[\'\"″]|inch|\b)|11\.\d))\b", text, re.I):
        return "iPad Air 11"
    if re.search(r"\bipad\s+air\b", text, re.I):
        return "iPad Air"

    # iPad Pro
    if re.search(r"\b(?:13\s*(?:[\'\"″]|inch|-inch)?\s+ipad\s+pro|ipad\s+pro\s*(?:13(?:[\'\"″]|inch|\b)|13\.\d))\b", text, re.I):
        return "iPad Pro 13"
    if re.search(r"\b(?:12\.9\s*(?:[\'\"″]|inch|-inch)?\s+ipad\s+pro|ipad\s+pro\s*(?:12\.9(?:[\'\"″]|inch|\b)|12\.9″|12\"))\b", text, re.I):
        return "iPad Pro 12.9"
    if re.search(r"\b(?:11\s*(?:[\'\"″]|inch|-inch)?\s+ipad\s+pro|ipad\s+pro\s*(?:11(?:[\'\"″]|inch|\b)|11\.\d))\b", text, re.I):
        return "iPad Pro 11"
    if re.search(r"\bipad\s+pro\b", text, re.I):
        return "iPad Pro"

    # iPad mini
    if re.search(r"\bipad\s+mini\b", text, re.I):
        return "iPad mini"

    # Base iPad
    if re.search(r"\b(?:11\s*(?:[\'\"″]|inch|-inch)?\s+ipad|ipad\s*(?:11(?:[\'\"″]|inch|\b)|11\.\d|a16))\b", text, re.I):
        return "iPad 11"
    if re.search(r"\b(?:9(?:th)?\s*(?:[\'\"″]|inch|-inch|gen)?\s+ipad|ipad\s*(?:9(?:[\'\"″]|inch|th)?|10\.2))\b", text, re.I):
        return "iPad 9"
    if re.search(r"\b(?:10(?:th)?\s*(?:[\'\"″]|inch|-inch|gen)?\s+ipad|ipad\s*(?:10(?:[\'\"″]|inch|th)?|10\.9))\b", text, re.I):
        return "iPad 10"
    if re.search(r"\bipad\b", text, re.I):
        return "iPad"

    return None


def extract_apple_display_model(text: str) -> Optional[str]:
    """Standardizes Apple displays."""
    if re.search(r"\bpro\s+display\s+xdr\b", text, re.I):
        return "Pro Display XDR"
    if re.search(r"\bstudio\s+display\b", text, re.I):
        return "Studio Display"
    return None


def extract_apple_input_device_model(text: str) -> Optional[str]:
    """Standardizes Apple Pencils, Keyboards, Mice and Trackpads."""
    if re.search(r"\bapple\s+pencil\s+pro\b", text, re.I):
        return "Apple Pencil Pro"
    if re.search(r"\bapple\s+pencil\s*[\(\[]?\s*(?:usb-c|type-c)[\)\]]?", text, re.I):
        return "Apple Pencil (USB-C)"
    if re.search(r"\bapple\s+pencil\s*(?:2|2nd|-го\s*поколения)\b", text, re.I):
        return "Apple Pencil 2"
    if re.search(r"\bapple\s+pencil\b", text, re.I):
        return "Apple Pencil"
    if re.search(r"\b(?:magic\s+keyboard|keyboard\s+with\s+touch\s+id|magic\s+with\s+touch\s+id)\b", text, re.I):
        return "Magic Keyboard"
    if re.search(r"\bmagic\s+trackpad\b", text, re.I):
        return "Magic Trackpad"
    if re.search(r"\bmagic\s+mouse(?:\s*\d+)?\b", text, re.I):
        return "Magic Mouse"
    return None


def extract_apple_tracker_model(text: str) -> Optional[str]:
    """Standardizes AirTag trackers."""
    if re.search(r"\bairtag\b", text, re.I):
        return "AirTag"
    return None


def extract_apple_canonical_model(text: str) -> Optional[str]:
    """Unified entrypoint for all Apple canonical models."""
    return (
        extract_iphone_model(text)
        or extract_mac_model(text)
        or extract_ipad_model(text)
        or extract_apple_watch_model(text)
        or extract_apple_audio_model(text)
        or extract_apple_display_model(text)
        or extract_apple_input_device_model(text)
        or extract_apple_tracker_model(text)
    )


# Canonical Model Line Rules (Top Consumer Electronics Models)
KNOWN_MODEL_FAMILIES: List[Tuple[re.Pattern, str]] = [
    # Apple iPad & Mac
    (re.compile(r"\bipad\s*pro\s*13\b", re.I), "iPad Pro 13"),
    (re.compile(r"\bipad\s*pro\s*11\b", re.I), "iPad Pro 11"),
    (re.compile(r"\bipad\s*air\s*13\b", re.I), "iPad Air 13"),
    (re.compile(r"\bipad\s*air\s*11\b", re.I), "iPad Air 11"),
    (re.compile(r"\bipad\s*air\s*5\b", re.I), "iPad Air 5"),
    (re.compile(r"\bipad\s*air\b", re.I), "iPad Air"),
    (re.compile(r"\bipad\s*mini\s*6\b", re.I), "iPad mini 6"),
    (re.compile(r"\bipad\s*mini\b", re.I), "iPad mini"),
    (re.compile(r"\bipad\s*10\b", re.I), "iPad 10"),
    (re.compile(r"\bipad\s*9\b", re.I), "iPad 9"),
    (re.compile(r"\bmacbook\s*pro\s*16\b", re.I), "MacBook Pro 16"),
    (re.compile(r"\bmacbook\s*pro\s*14\b", re.I), "MacBook Pro 14"),
    (re.compile(r"\bmacbook\s*pro\s*13\b", re.I), "MacBook Pro 13"),
    (re.compile(r"\bmacbook\s*air\s*15\b", re.I), "MacBook Air 15"),
    (re.compile(r"\bmacbook\s*air\s*13\b", re.I), "MacBook Air 13"),
    (re.compile(r"\bmacbook\s*air\b", re.I), "MacBook Air"),
    (re.compile(r"\bapple\s*watch\s*ultra\s*2\b", re.I), "Apple Watch Ultra 2"),
    (re.compile(r"\bapple\s*watch\s*ultra\b", re.I), "Apple Watch Ultra"),
    (re.compile(r"\bapple\s*watch\s*series\s*10\b", re.I), "Apple Watch Series 10"),
    (re.compile(r"\bapple\s*watch\s*series\s*9\b", re.I), "Apple Watch Series 9"),
    (re.compile(r"\bapple\s*watch\s*series\s*8\b", re.I), "Apple Watch Series 8"),
    (re.compile(r"\bapple\s*watch\s*se\b", re.I), "Apple Watch SE"),
    (re.compile(r"\bairpods\s*max\b", re.I), "AirPods Max"),
    (re.compile(r"\bairpods\s*pro\s*2\b", re.I), "AirPods Pro 2"),
    (re.compile(r"\bairpods\s*pro\b", re.I), "AirPods Pro"),
    (re.compile(r"\bairpods\s*4\b", re.I), "AirPods 4"),
    (re.compile(r"\bairpods\s*3\b", re.I), "AirPods 3"),
    (re.compile(r"\bairpods\s*2\b", re.I), "AirPods 2"),

    # Samsung Galaxy
    (re.compile(r"\bgalaxy\s*s24\s*ultra\b", re.I), "Galaxy S24 Ultra"),
    (re.compile(r"\bgalaxy\s*s24\s*\+\b", re.I), "Galaxy S24+"),
    (re.compile(r"\bgalaxy\s*s24\s*fe\b", re.I), "Galaxy S24 FE"),
    (re.compile(r"\bgalaxy\s*s24\b", re.I), "Galaxy S24"),
    (re.compile(r"\bgalaxy\s*s23\s*ultra\b", re.I), "Galaxy S23 Ultra"),
    (re.compile(r"\bgalaxy\s*s23\s*fe\b", re.I), "Galaxy S23 FE"),
    (re.compile(r"\bgalaxy\s*s23\b", re.I), "Galaxy S23"),
    (re.compile(r"\bgalaxy\s*s22\s*ultra\b", re.I), "Galaxy S22 Ultra"),
    (re.compile(r"\bgalaxy\s*s22\b", re.I), "Galaxy S22"),
    (re.compile(r"\bgalaxy\s*s21\s*fe\b", re.I), "Galaxy S21 FE"),
    (re.compile(r"\bgalaxy\s*z\s*fold\s*6\b", re.I), "Galaxy Z Fold 6"),
    (re.compile(r"\bgalaxy\s*z\s*flip\s*6\b", re.I), "Galaxy Z Flip 6"),
    (re.compile(r"\bgalaxy\s*z\s*fold\s*5\b", re.I), "Galaxy Z Fold 5"),
    (re.compile(r"\bgalaxy\s*z\s*flip\s*5\b", re.I), "Galaxy Z Flip 5"),
    (re.compile(r"\bgalaxy\s*a55\b", re.I), "Galaxy A55"),
    (re.compile(r"\bgalaxy\s*a54\b", re.I), "Galaxy A54"),
    (re.compile(r"\bgalaxy\s*a35\b", re.I), "Galaxy A35"),
    (re.compile(r"\bgalaxy\s*a34\b", re.I), "Galaxy A34"),
    (re.compile(r"\bgalaxy\s*a25\b", re.I), "Galaxy A25"),
    (re.compile(r"\bgalaxy\s*a15\b", re.I), "Galaxy A15"),
    (re.compile(r"\bgalaxy\s*a05s\b", re.I), "Galaxy A05s"),
    (re.compile(r"\bgalaxy\s*a05\b", re.I), "Galaxy A05"),
    (re.compile(r"\bgalaxy\s*watch\s*ultra\b", re.I), "Galaxy Watch Ultra"),
    (re.compile(r"\bgalaxy\s*watch\s*7\b", re.I), "Galaxy Watch 7"),
    (re.compile(r"\bgalaxy\s*watch\s*6\b", re.I), "Galaxy Watch 6"),
    (re.compile(r"\bgalaxy\s*buds\s*3\s*pro\b", re.I), "Galaxy Buds 3 Pro"),
    (re.compile(r"\bgalaxy\s*buds\s*3\b", re.I), "Galaxy Buds 3"),
    (re.compile(r"\bgalaxy\s*buds\s*2\s*pro\b", re.I), "Galaxy Buds 2 Pro"),
    (re.compile(r"\bgalaxy\s*buds\s*fe\b", re.I), "Galaxy Buds FE"),

    # Xiaomi / Redmi / POCO
    (re.compile(r"\bredmi\s*note\s*13\s*pro\s*\+\b", re.I), "Redmi Note 13 Pro+"),
    (re.compile(r"\bredmi\s*note\s*13\s*pro\b", re.I), "Redmi Note 13 Pro"),
    (re.compile(r"\bredmi\s*note\s*13\b", re.I), "Redmi Note 13"),
    (re.compile(r"\bredmi\s*note\s*12\b", re.I), "Redmi Note 12"),
    (re.compile(r"\bredmi\s*13c\b", re.I), "Redmi 13C"),
    (re.compile(r"\bredmi\s*13\b", re.I), "Redmi 13"),
    (re.compile(r"\bredmi\s*12c\b", re.I), "Redmi 12C"),
    (re.compile(r"\bredmi\s*12\b", re.I), "Redmi 12"),
    (re.compile(r"\bredmi\s*a3\b", re.I), "Redmi A3"),
    (re.compile(r"\bpoco\s*x6\s*pro\b", re.I), "POCO X6 Pro"),
    (re.compile(r"\bpoco\s*x6\b", re.I), "POCO X6"),
    (re.compile(r"\bpoco\s*f6\s*pro\b", re.I), "POCO F6 Pro"),
    (re.compile(r"\bpoco\s*f6\b", re.I), "POCO F6"),
    (re.compile(r"\bpoco\s*m6\s*pro\b", re.I), "POCO M6 Pro"),
    (re.compile(r"\bpoco\s*m6\b", re.I), "POCO M6"),
    (re.compile(r"\bpoco\s*c65\b", re.I), "POCO C65"),
    (re.compile(r"\bxiaomi\s*14\s*ultra\b", re.I), "Xiaomi 14 Ultra"),
    (re.compile(r"\bxiaomi\s*14\s*pro\b", re.I), "Xiaomi 14 Pro"),
    (re.compile(r"\bxiaomi\s*14\b", re.I), "Xiaomi 14"),
    (re.compile(r"\bxiaomi\s*13t\s*pro\b", re.I), "Xiaomi 13T Pro"),
    (re.compile(r"\bxiaomi\s*13t\b", re.I), "Xiaomi 13T"),

    # Dyson
    (re.compile(r"\bv15\s*detect\b", re.I), "V15 Detect"),
    (re.compile(r"\bv12\s*detect\s*slim\b", re.I), "V12 Detect Slim"),
    (re.compile(r"\bv11\b", re.I), "V11"),
    (re.compile(r"\bv8\b", re.I), "V8"),
    (re.compile(r"\bairwrap\b", re.I), "Airwrap"),
    (re.compile(r"\bsupersonic\b", re.I), "Supersonic"),
    (re.compile(r"\bairstrait\b", re.I), "Airstrait"),

    # Sony PlayStation & Consoles
    (re.compile(r"\bplaystation\s*5\s*slim\b", re.I), "PlayStation 5 Slim"),
    (re.compile(r"\bplaystation\s*5\s*pro\b", re.I), "PlayStation 5 Pro"),
    (re.compile(r"\bplaystation\s*5\b", re.I), "PlayStation 5"),
    (re.compile(r"\bps5\b", re.I), "PlayStation 5"),
    (re.compile(r"\bxbox\s*series\s*x\b", re.I), "Xbox Series X"),
    (re.compile(r"\bxbox\s*series\s*s\b", re.I), "Xbox Series S"),
    (re.compile(r"\bnintendo\s*switch\s*oled\b", re.I), "Switch OLED"),
    (re.compile(r"\bnintendo\s*switch\b", re.I), "Switch"),

    # GPUs
    (re.compile(r"\brtx\s*4090\b", re.I), "GeForce RTX 4090"),
    (re.compile(r"\brtx\s*4080\s*super\b", re.I), "GeForce RTX 4080 Super"),
    (re.compile(r"\brtx\s*4080\b", re.I), "GeForce RTX 4080"),
    (re.compile(r"\brtx\s*4070\s*ti\s*super\b", re.I), "GeForce RTX 4070 Ti Super"),
    (re.compile(r"\brtx\s*4070\s*ti\b", re.I), "GeForce RTX 4070 Ti"),
    (re.compile(r"\brtx\s*4070\s*super\b", re.I), "GeForce RTX 4070 Super"),
    (re.compile(r"\brtx\s*4070\b", re.I), "GeForce RTX 4070"),
    (re.compile(r"\brtx\s*4060\s*ti\b", re.I), "GeForce RTX 4060 Ti"),
    (re.compile(r"\brtx\s*4060\b", re.I), "GeForce RTX 4060"),
    (re.compile(r"\brtx\s*3060\b", re.I), "GeForce RTX 3060"),
    (re.compile(r"\brx\s*7900\s*xtx\b", re.I), "Radeon RX 7900 XTX"),
    (re.compile(r"\brx\s*7800\s*xt\b", re.I), "Radeon RX 7800 XT"),
    (re.compile(r"\brx\s*7700\s*xt\b", re.I), "Radeon RX 7700 XT"),
    (re.compile(r"\brx\s*7600\b", re.I), "Radeon RX 7600"),
]

# Patterns stripped from title when constructing fallback clean model
_MODEL_STRIP_PATTERNS = [
    # Category words
    r"\b(смартфон|сотовый телефон|мобильный телефон|ноутбук|ультрабук|планшет|пылесос|беспроводн(?:ой|ые|ая)|вертикальный|робот-пылесос|телевизор|видеокарта|наушники|смарт-часы|умные часы|монитор|холодильник|стиральная машина|кондиционер|кастрюл(?:я|и)|сковород(?:а|ы))\b",
    # Compatibility clauses
    r"\b(?:для|for|на)\s+.*$",
    # Memory, storage, specs
    r"\b\d+\s*(?:/\s*\d+)?\s*(?:gb|гб|tb|тб|mb|мб|ram|rom|mah|мач|w|вт|hz|гц|dpi)\b",
    r"\b\d+(?:\.\d+)?\s*(?:л|l|мл|ml|г|g|кг|kg)\.?\b",
    r"\b(с\s+крышкой|без\s+крышки)\b",
    r"\b\d+[\'\"«»]\b",
    r"\b\d+\s*(?:дюйм(?:ов|а)?|см|mm|мм)\b",
    r"\b(4k|8k|fhd|uhd|oled|qled|hdr|ips|amoled|retina)\b",
    r"\b(5g|4g|lte|wi-fi|wifi|bluetooth|nfc|dual[\s\-]sim|nano[\s\-]sim|\+[\s\-]esim|esim)\b",
    # Colors (ru & en)
    r"\b(черн(?:ый|ая|ое|ые)|бел(?:ый|ая|ое|ые)|сер(?:ый|ая|ое|ые)|син(?:ий|яя|ее|ие)|зелен(?:ый|ая|ое|ые)|красн(?:ый|ая|ое|ые)|желт(?:ый|ая|ое|ые)|розов(?:ый|ая|ое|ые)|золот(?:ой|ая|ое|ые)|серебрист(?:ый|ая|ое|ые)|фиолетов(?:ый|ая|ое|ые)|голуб(?:ой|ая|ое|ые)|бежев(?:ый|ая|ое|ые)|титан(?:овый|овая|овое|овые)?|титан|графит|никель|медь)\b",
    r"\b(black|white|gray|grey|blue|green|red|yellow|pink|gold|silver|purple|beige|titanium|natural|midnight|starlight|graphite|space\s+gray|dark)\b",
    # Parentheses and brackets
    r"\([^\)]*\)",
    r"\[[^\]]*\]",
    # Marketing and regional markers
    r"\b(global\s+version|global|рст|оригинал|original|новинка|new|eu|ru|kz)\b",
    r"\b(с\s+кейсом|case|aluminum|stainless\s+steel)\b",
]


def extract_clean_model(
    title: str,
    brand: Optional[str] = None,
    category: Optional[str] = None,
    item_kind: str = "device",
) -> Optional[str]:
    """
    Извлекает чистую базовую модель товара без цвета, объема памяти и маркетингового шума.
    """
    if not title:
        return None

    # Отсекаем комплекты подарков и бандлы ("iPhone + AirPods + Адаптер")
    m_bundle = _BUNDLE_GIFT_SPLIT_PAT.search(title)
    main_title = title[:m_bundle.start()].strip() if m_bundle else title

    # Для аксессуаров и расходников:
    # Обычные чехлы/стекла/адаптеры не получают модель смартфона ("Чехол для iPhone" != "iPhone").
    # Но фирменные аксессуары Apple (Pencil, Magic Keyboard/Mouse/Trackpad, AirTag) сохраняют свою модель!
    if item_kind in ("accessory", "consumable"):
        input_dev = extract_apple_input_device_model(main_title) or extract_apple_tracker_model(main_title)
        return input_dev

    # Стандартизированный нейминг всей линейки Apple устройств (только если бренд Apple или неизвестен)
    if brand in ("Apple", None):
        apple_mod = extract_apple_canonical_model(main_title)
        if apple_mod:
            return apple_mod

    # 1. Поиск по известным семействам моделей
    for pat, fam in KNOWN_MODEL_FAMILIES:
        if pat.search(main_title):
            return fam

    # 2. Очистка по регулярным выражениям
    res = main_title
    if brand:
        res = re.sub(r"\b" + re.escape(brand) + r"\b", "", res, flags=re.I)
    for pat in _MODEL_STRIP_PATTERNS:
        res = re.sub(pat, "", res, flags=re.I)

    res = re.sub(r"[/\\,\-\+\:\;\s\.]+", " ", res).strip()
    words = res.split()
    if not words:
        return None

    # Берём от 1 до 4 первых смысловых слов модели
    model_name = " ".join(words[:4]).strip()
    if len(model_name) < 2 or model_name.isdigit():
        return None
    return model_name


@dataclass
class ClassificationResult:
    category_id: str
    category_name: str
    category_parent_id: Optional[str]
    category_icon: str
    brand: Optional[str]
    confidence: float
    source: str  # 'rule_category' | 'rule_title' | 'rule_accessory' | 'local_ai' | 'fallback'
    model: Optional[str] = None
    item_kind: str = "device"  # 'device' | 'accessory' | 'consumable'
    compatibility_target: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category_id": self.category_id,
            "category_name": self.category_name,
            "category_parent_id": self.category_parent_id,
            "category_icon": self.category_icon,
            "brand": self.brand,
            "model": self.model,
            "item_kind": self.item_kind,
            "compatibility_target": self.compatibility_target,
            "confidence": self.confidence,
            "source": self.source,
        }


class CatalogClassifier:
    """
    Классификатор и нормализатор каталога.
    Объединяет мастер-таксономию, детерминированные правила по ключевым словам и локальную ИИ.
    """

    def __init__(
        self,
        taxonomy: Optional[TaxonomyMapper] = None,
        ollama_url: str = "http://192.168.28.25:11434",
        ollama_model: str = "qwen3-vl:8b-instruct",
    ):
        self.taxonomy = taxonomy or TaxonomyMapper(MASTER_TAXONOMY)
        self.ollama_url = ollama_url.rstrip("/")
        self.ollama_model = ollama_model

    def extract_brand(self, title: str) -> Optional[str]:
        """
        Извлекает каноническое имя бренда из названия товара.
        """
        if not title:
            return None

        # Обувной бренд IMAC (Италия) vs Apple iMac
        if re.search(r"\b(обувь|ботинки|туфли|кроссовки|сапоги|кеды|сандали|балетки|полуботинки|шлепанцы|ботильоны)\b", title, re.I):
            if re.search(r"\bimac\b", title, re.I):
                return "IMAC"

        # Косметический бренд M·A·C vs Apple Mac Studio / Mac Pro
        if re.search(r"\b(консилер|пудра|праймер|тональн(?:ая|ый|ое)|палетк[аи]|помад[аы]|тушь|тени|блеск\s+для\s+губ|румян[аы]|хайлайтер|concealer|powder|primer|foundation)\b", title, re.I):
            if re.search(r"\bmac\b", title, re.I):
                return "M·A·C"

        # Если в названии есть фраза совместимости «для/на/for <устройство>»,
        # вырезаем её, чтобы бренд целевого смартфона/консоли не приписывался чужому товару
        search_text = re.sub(
            r"\b(?:для|for|на)\s+(?:устройств\s+|техники\s+|продукции\s+|смартфонов\s+|телефонов\s+)?(?:apple|айфон|iphone|ipad|macbook|imac|airpods|samsung|galaxy|xiaomi|redmi|huawei|honor|oppo|vivo|realme|sony|ps5|xbox|dyson|switch|nintendo)\b",
            " ",
            title,
            flags=re.I,
        )

        # Ищем первое точное совпадение бренда в очищенном заголовке
        match = _BRAND_PATTERN.search(search_text)
        if match:
            raw_key = match.group(1).lower()
            # Защита от ложного срабатывания Apple на фруктах, сладостях, бытовой химии, садовых товарах и косметике
            if raw_key == "apple":
                if re.search(
                    r"\b(ароматизатор|духи|perfume|парфюм|мыло|soap|конфет[ыа]?|маршмелоу|шоколад|джем|паст[аы]|яблочн(?:ый|ое|ая|ого)?|печенье|сок\b|чай\b|напиток|sorbet|крем\b|шампунь|гель\b|гель-душ|шланг|полив|тройник|кастрюл[яи]|сковород[аы]|мытья\s+посуды|порошок|чистящ[ее|ие])\b|"
                    r"\b(?:green|crab)\s+apple\b",
                    title,
                    re.I,
                ):
                    return None
            return KNOWN_BRANDS.get(raw_key)

        return None

    def extract_clean_model(
        self,
        title: str,
        brand: Optional[str] = None,
        category: Optional[str] = None,
        item_kind: str = "device",
    ) -> Optional[str]:
        return extract_clean_model(title, brand=brand, category=category, item_kind=item_kind)

    def classify_rules(self, title: str, raw_category: str = "") -> Optional[ClassificationResult]:
        """
        Детерминированная классификация по правилам (таксономия + заголовок).
        Возвращает ClassificationResult или None, если правила не уверены.
        """
        if not title:
            return None

        # Отсекаем комплекты подарков и бандлы ("Смартфон Apple iPhone + Наушники AirPods...")
        m_bundle = _BUNDLE_GIFT_SPLIT_PAT.search(title)
        main_part = title[:m_bundle.start()].strip() if m_bundle else title

        # 0. Проверяем первичность ключевых устройств (смарт-часы, смартфоны, планшеты, ноутбуки),
        # чтобы аксессуары ("Aluminium Case", "Чехол для...", "Стилус", "Зарядное для Watch") не меняли тип основного устройства
        is_watch = bool(re.search(r"\b(смарт-час(?:ы|а)|умные\s+часы|(?:apple\s+)?watch\s*(?:nike\s+)?series|apple\s+watch|galaxy\s+watch)\b", main_part, re.I)) and not bool(re.search(r"\b(ремешок|браслет|сменный\s+ремешок|зарядн(?:ое|ый|ая)|кабель|док-станция|подставка|переходник|адаптер)\b|^ремешок\b|^браслет\b|\b(?:для|for)\s+(?:apple\s+)?watch\b", main_part, re.I))
        is_phone = bool(re.search(r"\b(смартфон|айфон|сотовый\s+телефон)\b", main_part, re.I)) and not bool(re.search(r"\b(чехол|стекло|пленка|ремешок|кабель|держатель|подставка|наушники|гарнитур|адаптер|зарядк|переходник)\b", main_part, re.I))
        is_tablet = bool(re.search(r"\b(планшет|ipad|tablet)\b", main_part, re.I)) and not bool(re.search(r"\b(клавиатур[аы]|keyboard|стилус|stylus|чехол|стекло|пленка|ремешок|кабель|адаптер|держатель|подставка)\b", main_part, re.I))
        is_laptop = bool(re.search(r"\b(ноутбук|macbook|laptop|ультрабук)\b", main_part, re.I)) and not bool(re.search(r"\b(чехол|сумка|рюкзак|накладка|пленка|клавиатур|зарядное|адаптер|блок\s+питания)\b", main_part, re.I))

        item_kind = "device"
        compat_target = None
        matched_cat_id = None

        if is_watch:
            matched_cat_id = "smartwatches"
            item_kind = "device"
        elif is_phone:
            matched_cat_id = "phones"
            item_kind = "device"
        elif is_tablet:
            matched_cat_id = "tablets"
            item_kind = "device"
        elif is_laptop:
            matched_cat_id = "laptops"
            item_kind = "device"
        else:
            for pat, cat_id, kind in _ACCESSORY_CONSUMABLE_RULES:
                if pat.search(main_part):
                    matched_cat_id = cat_id
                    item_kind = kind
                    break

        # Извлекаем совместимое устройство для аксессуара («для Apple iPhone 15»)
        if item_kind in ("accessory", "consumable"):
            m_target = _COMPATIBILITY_PATTERN.search(main_part)
            if m_target:
                compat_target = m_target.group(1).strip()

            # Для аксессуаров и расходников бренд извлекается строго ДО слова «для / на / for»
            parts = re.split(r"\b(?:для|на|for)\b", main_part, flags=re.I, maxsplit=1)
            before_part = parts[0]
            # Очищаем before_part от названий целевых гаджетов (iPhone, iPad, Watch, MacBook, Galaxy),
            # чтобы сторонние чехлы/стекла/кабели не получали бренд Apple!
            clean_before = re.sub(
                r"\b(?:iphone|ipad|macbook|galaxy|redmi|poco|apple\s+watch)\s*(?:\d{1,2}|pro\s*max|pro|plus|mini|ultra|se|air|duo|fe|\d{1,2}[a-z])*\b",
                " ",
                before_part,
                flags=re.I,
            )
            brand = self.extract_brand(clean_before)

            # Проверяем, не является ли это фирменным аксессуаром Apple
            if not brand and re.search(
                r"\b(?:чехол\s+apple|оригинальный\s+чехол\s+apple|apple\s+silicone\s+case|apple\s+leather|apple\s+clear\s+case|apple\s+finewoven|apple\s+smart\s+folio|apple\s+magic\s+keyboard|apple\s+pencil|apple\s+magsafe|apple\s+airtag)\b",
                main_part,
                re.I,
            ):
                brand = "Apple"
            elif not brand and len(parts) > 1:
                # Очищаем вторую часть от целевых брендов устройств (Apple, Samsung), чтобы чехол не стал Apple!
                cleaned_after = re.sub(r"\b(apple|iphone|ipad|samsung|galaxy|xiaomi|redmi|huawei|honor)\b", "", parts[1], flags=re.I)
                brand = self.extract_brand(cleaned_after)
        else:
            # Для основных товаров проверяем бренд по всему названию
            brand = self.extract_brand(main_part)
            if not brand:
                # Неявные Apple-устройства (iPhone, iPad, MacBook и др.), исключая совместимость «для iPhone»
                clean_target = re.sub(r"\b(?:для|for|на)\s+.*$", "", main_part, flags=re.I)
                if not re.search(r"\b(обувь|ботинки|туфли|кроссовки|сапоги|консилер|пудра|праймер|тональн|палетк|помад|тушь|тени|румян)\b", clean_target, re.I):
                    if re.search(
                        r"\b(iphone|ipad|macbook|imac|mac\s+mini|mac\s+studio|mac\s+pro|airpods|earpods|(?:apple\s+)?watch\s*(?:nike\s+)?series|apple\s+watch|studio\s+display|pro\s+display|airtag)\b",
                        clean_target,
                        re.I,
                    ):
                        brand = "Apple"

        # 1. Если определена категория аксессуара / расходника
        if matched_cat_id:
            cat = self.taxonomy.get_by_id(matched_cat_id)
            if cat:
                clean_model = self.extract_clean_model(title, brand=brand, category=cat.name, item_kind=item_kind)
                return ClassificationResult(
                    category_id=cat.id,
                    category_name=cat.name,
                    category_parent_id=cat.parent_id,
                    category_icon=cat.icon,
                    brand=brand,
                    confidence=0.92,
                    source="rule_accessory",
                    model=clean_model,
                    item_kind=item_kind,
                    compatibility_target=compat_target,
                )

        # 2. Сначала пробуем сопоставить заголовок напрямую по ключевым паттернам типа товара
        for pattern, cat_id in _TITLE_TYPE_RULES:
            if pattern.search(title):
                cat = self.taxonomy.get_by_id(cat_id)
                if cat:
                    clean_model = self.extract_clean_model(title, brand=brand, category=cat.name, item_kind="device")
                    return ClassificationResult(
                        category_id=cat.id,
                        category_name=cat.name,
                        category_parent_id=cat.parent_id,
                        category_icon=cat.icon,
                        brand=brand,
                        confidence=0.95,
                        source="rule_title",
                        model=clean_model,
                        item_kind="device",
                    )

        # 3. Если по заголовку тип не найден, пробуем через сырую категорию магазина
        if raw_category:
            cat = self.taxonomy.map_category(raw_category)
            if cat:
                # Если сырая категория дала L2 подкатегорию (напр. "laptops", "phones") - отличная уверенность
                confidence = 0.90 if cat.parent_id else 0.70
                clean_model = self.extract_clean_model(title, brand=brand, category=cat.name, item_kind="device")
                return ClassificationResult(
                    category_id=cat.id,
                    category_name=cat.name,
                    category_parent_id=cat.parent_id,
                    category_icon=cat.icon,
                    brand=brand,
                    confidence=confidence,
                    source="rule_category",
                    model=clean_model,
                    item_kind="device",
                )

        # 4. Если известен только бренд
        if brand:
            clean_model = self.extract_clean_model(title, brand=brand, item_kind="device")
            return ClassificationResult(
                category_id="electronics",
                category_name="Электроника",
                category_parent_id=None,
                category_icon="📱",
                brand=brand,
                confidence=0.70,
                source="rule_brand_only",
                model=clean_model,
                item_kind="device",
            )

        return None

    def classify_with_local_ai(self, title: str, raw_category: str = "") -> Optional[ClassificationResult]:
        """
        Локальный ИИ классификатор через Ollama API (qwen3-vl:8b-instruct).
        Вызывается только если детерминированные правила не дали уверенного результата.
        """
        prompt = f"""Ты — классификатор каталога товаров маркетплейса в Казахстане.
По названию товара определи бренд, модель товара, категорию и тип (device=устройство, accessory=аксессуар, consumable=расходник).

Название: {title}
Раздел магазина: {raw_category}

Выбери наиболее подходящую категорию из списка:
- phones (Смартфоны)
- phone_cases (Чехлы и бамперы)
- phone_glass (Защитные стекла и пленки)
- cables_chargers (Кабели и зарядные устройства)
- vacuum_parts (Расходники для пылесосов)
- printer_ink (Картриджи и чернила)
- laptops (Ноутбуки)
- tablets (Планшеты)
- smartwatches (Смарт-часы)
- headphones (Наушники)
- tv (Телевизоры)
- monitors (Мониторы)
- pc_desktops (Компьютеры и ПК)
- gpus (Видеокарты)
- cpus (Процессоры)
- motherboards (Материнские платы)
- storage (SSD накопители)
- peripherals (Клавиатуры и мыши)
- consoles (Игровые приставки)
- washing_machines (Стиральные машины)
- refrigerators (Холодильники)
- vacuums (Пылесосы)
- air_conditioners (Кондиционеры)
- kitchen_appliances (Техника для кухни)
- electronics (Прочая электроника)
- appliances (Прочая бытовая техника)
- other (Другое)

Верни ТОЛЬКО валидный JSON без пояснений:
{{"brand": "НазваниеБренда или null", "model": "ЧистаяМодельБезЦветаИПамяти или null", "category_id": "код_категории", "item_kind": "device|accessory|consumable"}}"""

        payload = {
            "model": self.ollama_model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.0, "num_predict": 120},
        }

        try:
            req = urllib.request.Request(
                f"{self.ollama_url}/api/generate",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    text = data.get("response", "").strip()

                    # Извлекаем JSON из ответа
                    match = re.search(r"\{.*?\}", text, re.DOTALL)
                    if match:
                        parsed = json.loads(match.group(0))
                        cat_id = parsed.get("category_id", "").strip().lower()
                        cat = self.taxonomy.get_by_id(cat_id) or self.taxonomy.get_by_id("electronics")
                        brand = parsed.get("brand")
                        if brand and (brand.lower() in ("null", "none", "unknown", "нет")):
                            brand = None
                        model = parsed.get("model")
                        if model and (model.lower() in ("null", "none", "unknown", "нет")):
                            model = None
                        item_kind = parsed.get("item_kind", "device").lower()
                        if item_kind not in ("device", "accessory", "consumable"):
                            item_kind = "device"

                        if cat:
                            return ClassificationResult(
                                category_id=cat.id,
                                category_name=cat.name,
                                category_parent_id=cat.parent_id,
                                category_icon=cat.icon,
                                brand=brand or self.extract_brand(title),
                                confidence=0.88,
                                source="local_ai",
                                model=model or self.extract_clean_model(title, brand=brand, category=cat.name, item_kind=item_kind),
                                item_kind=item_kind,
                            )
        except Exception as e:
            logger.warning(f"Local AI classification failed: {e}")

        return None

    def classify(self, title: str, raw_category: str = "") -> ClassificationResult:
        """
        Главная точка входа: Правила -> Локальный ИИ -> Безопасный фоллбэк.
        """
        # 1. Быстрые правила
        res = self.classify_rules(title, raw_category)
        if res and res.confidence >= 0.85 and (res.brand != "Apple" or res.model or res.item_kind != "device"):
            return res

        # 2. Если правила не уверены или у Apple-устройства не определилась модель, вызываем локальный ИИ
        ai_res = self.classify_with_local_ai(title, raw_category)
        if ai_res:
            if res and res.category_id:
                ai_res.category_id = res.category_id
                ai_res.category_name = res.category_name
            return ai_res

        # 3. Если ИИ недоступен, возвращаем частичный результат правил или дефолт
        if res:
            return res

        brand = self.extract_brand(title)
        clean_model = self.extract_clean_model(title, brand=brand, item_kind="device")
        return ClassificationResult(
            category_id="electronics",
            category_name="Электроника",
            category_parent_id=None,
            category_icon="📱",
            brand=brand,
            confidence=0.50,
            source="fallback",
            model=clean_model,
            item_kind="device",
        )
