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
}

# Regex word boundary match pattern for brands (sorted by length desc to match multi-word first)
_SORTED_BRAND_KEYS = sorted(KNOWN_BRANDS.keys(), key=lambda x: -len(x))
_BRAND_PATTERN = re.compile(
    r"(?<![a-zA-Z0-9а-яА-ЯёЁ])(" + "|".join(re.escape(k) for k in _SORTED_BRAND_KEYS) + r")(?![a-zA-Z0-9а-яА-ЯёЁ])",
    re.IGNORECASE
)

# Product Type keyword rules: Title keywords -> Category ID
_TITLE_TYPE_RULES: List[Tuple[re.Pattern, str]] = [
    # Audio & TV (проверяем наушники раньше телефонов, чтобы "наушники для iPhone" не становились телефоном)
    (re.compile(r"\b(наушник[и|ов]|гарнитур[аы]|airpods|tws|вкладыши)\b", re.I), "headphones"),
    (re.compile(r"\b(телевизор|smart\s+tv|oled\s+tv|qled\s+tv)\b", re.I), "tv"),
    (re.compile(r"\b(игров[ая|ые]\s+приставк[аи]|игровая\s+консоль|playstation|ps5|xbox|nintendo\s+switch)\b", re.I), "consoles"),

    # Phones & Tablets
    (re.compile(r"\b(смартфон|айфон|iphone|сотовый\s+телефон)\b", re.I), "phones"),
    (re.compile(r"\b(планшет|ipad|tablet)\b", re.I), "tablets"),
    (re.compile(r"\b(смарт-час[ыа]|умные\s+часы|фитнес-браслет|apple\s+watch|galaxy\s+watch)\b", re.I), "smartwatches"),

    # Laptops & Computers
    (re.compile(r"\b(ноутбук|ультрабук|macbook|лэптоп)\b", re.I), "laptops"),
    (re.compile(r"\b(системный\s+блок|компьютер|моноблок|неттоп|desktop\s+pc)\b", re.I), "pc_desktops"),
    (re.compile(r"\b(монитор|дисплей)\b", re.I), "monitors"),

    # Hardware Components
    (re.compile(r"\b(видеокарт[аы]|geforce\s+rtx|radeon\s+rx|графический\s+процессор)\b", re.I), "gpus"),
    (re.compile(r"\b(процессор|intel\s+core|amd\s+ryzen)\b", re.I), "cpus"),
    (re.compile(r"\b(материнск[ая|ие]\s+плат[аы]|motherboard)\b", re.I), "motherboards"),
    (re.compile(r"\b(накопитель\s+ssd|твердотельный\s+накопитель|жесткий\s+диск|hdd|ssd\s+m\.2)\b", re.I), "storage"),
    (re.compile(r"\b(клавиатур[аы]|компьютерн[ая|ые]\s+мыш[ьи]|мышь\s+беспроводная|геймпад)\b", re.I), "peripherals"),

    # Audio & TV
    (re.compile(r"\b(наушник[и|ов]|гарнитур[аы]|airpods|tws|вкладыши)\b", re.I), "headphones"),
    (re.compile(r"\b(телевизор|smart\s+tv|oled\s+tv|qled\s+tv)\b", re.I), "tv"),
    (re.compile(r"\b(игров[ая|ые]\s+приставк[аи]|игровая\s+консоль|playstation|ps5|xbox|nintendo\s+switch)\b", re.I), "consoles"),

    # Appliances
    (re.compile(r"\b(стиральн[ая|ые]\s+машин[аы]|стиралка)\b", re.I), "washing_machines"),
    (re.compile(r"\b(холодильник|морозильник|морозильн[ая|ые]\s+камер[аы])\b", re.I), "refrigerators"),
    (re.compile(r"\b(пылесос|робот-пылесос|вертикальный\s+пылесос)\b", re.I), "vacuums"),
    (re.compile(r"\b(кондиционер|сплит-систем[аы])\b", re.I), "air_conditioners"),
    (re.compile(r"\b(микроволнов[ка|ая]\s*печь|кофемашин[аы]|кофеварк[аи]|чайник\s+электрический|блендер|мультиварк[аи]|тостер|мясорубк[аи])\b", re.I), "kitchen_appliances"),
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
        re.compile(r"\b(ремешок\s+для|браслет\s+для\s+часов|ремешок\s+apple\s+watch|watch\s+band|watch\s+strap)\b", re.I),
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
    r"\b(?:apple\s+)?watch\s*(?:(ultra\s*2|ultra)|(se(?:\s*3|\s*\d{4})?)|series\s*(\d{1,2})|(\d{1,2}))\b",
    re.IGNORECASE,
)


def extract_apple_watch_model(text: str) -> Optional[str]:
    m = _APPLE_WATCH_REGEX.search(text)
    if not m:
        return None
    ultra = m.group(1)
    se = m.group(2)
    series = m.group(3) or m.group(4)
    if ultra:
        u = re.sub(r"\s+", " ", ultra).strip().title()
        return f"Apple Watch {u}"
    if se:
        return "Apple Watch SE"
    if series:
        return f"Apple Watch Series {series}"
    return "Apple Watch"


_AIRPODS_REGEX = re.compile(
    r"\bairpods\s*(max|pro\s*2|pro|4|3|2)\b",
    re.IGNORECASE,
)


def extract_airpods_model(text: str) -> Optional[str]:
    m = _AIRPODS_REGEX.search(text)
    if not m:
        return None
    sub = m.group(1).lower()
    if sub == "max":
        return "AirPods Max"
    if sub.startswith("pro 2"):
        return "AirPods Pro 2"
    if sub.startswith("pro"):
        return "AirPods Pro"
    return f"AirPods {sub}"


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
    r"\b(смартфон|сотовый телефон|мобильный телефон|ноутбук|ультрабук|планшет|пылесос|беспроводн(?:ой|ые|ая)|вертикальный|робот-пылесос|телевизор|видеокарта|наушники|смарт-часы|умные часы|монитор|холодильник|стиральная машина|кондиционер)\b",
    # Compatibility clauses
    r"\b(?:для|for|на)\s+.*$",
    # Memory, storage, specs
    r"\b\d+\s*(?:/\s*\d+)?\s*(?:gb|гб|tb|тб|mb|мб|ram|rom|mah|мач|w|вт|hz|гц|dpi)\b",
    r"\b\d+[\'\"«»]\b",
    r"\b\d+\s*(?:дюйм(?:ов|а)?|см|mm|мм)\b",
    r"\b(4k|8k|fhd|uhd|oled|qled|hdr|ips|amoled|retina)\b",
    r"\b(5g|4g|lte|wi-fi|wifi|bluetooth|nfc|dual[\s\-]sim|nano[\s\-]sim|\+[\s\-]esim|esim)\b",
    # Colors (ru & en)
    r"\b(черный|белый|серый|синий|зеленый|красный|желтый|розовый|золотой|серебристый|фиолетовый|голубой|бежевый|титан(?:овый)?|титан|графит|никель|медь|черная|белая|серая|синяя|зеленая|красная)\b",
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

    # Для аксессуаров и расходников модель не извлекается (не засоряем фасет «Модель»)
    if item_kind in ("accessory", "consumable"):
        return None

    # Отсекаем комплекты подарков и бандлы
    main_title = title.split("+")[0].strip() if "+" in title else title

    # Стандартизированный нейминг Apple устройств
    iphone_mod = extract_iphone_model(main_title)
    if iphone_mod:
        return iphone_mod

    watch_mod = extract_apple_watch_model(main_title)
    if watch_mod:
        return watch_mod

    airpods_mod = extract_airpods_model(main_title)
    if airpods_mod:
        return airpods_mod

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

    res = re.sub(r"[/\\,\-\+\:\;\s]+", " ", res).strip()
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

        # Ищем первое точное совпадение бренда в заголовке
        match = _BRAND_PATTERN.search(title)
        if match:
            raw_key = match.group(1).lower()
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
        main_part = title.split("+")[0].strip() if "+" in title else title

        # 0. Проверяем первичность ключевых устройств (смарт-часы, смартфоны и т.д.),
        # чтобы "Aluminium Case" в смарт-часах не превращал часы в чехлы
        is_watch = bool(re.search(r"\b(смарт-час[ыа]|умные\s+часы|apple\s+watch|galaxy\s+watch)\b", main_part, re.I)) and not bool(re.search(r"\b(ремешок|браслет\s+для|watch\s+band|watch\s+strap)\b", main_part, re.I))
        is_phone = bool(re.search(r"\b(смартфон|айфон|сотовый\s+телефон)\b", main_part, re.I)) and not bool(re.search(r"\b(чехол|стекло|пленка|ремешок|кабель|держатель|подставка|наушники|гарнитур|адаптер|зарядк|переходник)\b", main_part, re.I))

        item_kind = "device"
        compat_target = None
        matched_cat_id = None

        if is_watch:
            matched_cat_id = "smartwatches"
            item_kind = "device"
        elif is_phone:
            matched_cat_id = "phones"
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
            brand = self.extract_brand(before_part)

            # Проверяем, не является ли это фирменным аксессуаром Apple
            if not brand and re.search(r"\b(?:оригинальный\s+чехол\s+apple|apple\s+silicone\s+case|apple\s+leather|apple\s+clear\s+case|apple\s+finewoven)\b", main_part, re.I):
                brand = "Apple"
            elif not brand and len(parts) > 1:
                # Очищаем вторую часть от целевых брендов устройств (Apple, Samsung), чтобы чехол не стал Apple!
                cleaned_after = re.sub(r"\b(apple|iphone|ipad|samsung|galaxy|xiaomi|redmi|huawei|honor)\b", "", parts[1], flags=re.I)
                brand = self.extract_brand(cleaned_after)
        else:
            # Для основных товаров убираем совместимость «для / на / for ...», если она есть (напр. «Наушники для iPhone»)
            parts = re.split(r"\b(?:для|на|for)\b", main_part, flags=re.I, maxsplit=1)
            before_part = parts[0]
            brand = self.extract_brand(before_part)
            if not brand and len(parts) == 1:
                # Если фразы «для / for» не было, можно искать бренд по всей строке
                brand = self.extract_brand(main_part)
            if not brand:
                check_target = before_part if len(parts) > 1 else main_part
                if re.search(r"\b(iphone|ipad|macbook|airpods|apple\s+watch)\b", check_target, re.I):
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
        if res and res.confidence >= 0.85:
            return res

        # 2. Если правила не уверены, вызываем локальную модель
        ai_res = self.classify_with_local_ai(title, raw_category)
        if ai_res:
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
