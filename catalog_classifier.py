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


@dataclass
class ClassificationResult:
    category_id: str
    category_name: str
    category_parent_id: Optional[str]
    category_icon: str
    brand: Optional[str]
    confidence: float
    source: str  # 'rule_category' | 'rule_title' | 'local_ai' | 'fallback'

    def to_dict(self) -> Dict[str, Any]:
        return {
            "category_id": self.category_id,
            "category_name": self.category_name,
            "category_parent_id": self.category_parent_id,
            "category_icon": self.category_icon,
            "brand": self.brand,
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

    def classify_rules(self, title: str, raw_category: str = "") -> Optional[ClassificationResult]:
        """
        Детерминированная классификация по правилам (таксономия + заголовок).
        Возвращает ClassificationResult или None, если правила не уверены.
        """
        brand = self.extract_brand(title)

        # 1. Сначала пробуем сопоставить заголовок напрямую по ключевым паттернам типа товара
        for pattern, cat_id in _TITLE_TYPE_RULES:
            if pattern.search(title):
                cat = self.taxonomy.get_by_id(cat_id)
                if cat:
                    return ClassificationResult(
                        category_id=cat.id,
                        category_name=cat.name,
                        category_parent_id=cat.parent_id,
                        category_icon=cat.icon,
                        brand=brand,
                        confidence=0.95,
                        source="rule_title",
                    )

        # 2. Если по заголовку тип не найден, пробуем через сырую категорию магазина
        if raw_category:
            cat = self.taxonomy.map_category(raw_category)
            if cat:
                # Если сырая категория дала L2 подкатегорию (напр. "laptops", "phones") - отличная уверенность
                confidence = 0.90 if cat.parent_id else 0.70
                return ClassificationResult(
                    category_id=cat.id,
                    category_name=cat.name,
                    category_parent_id=cat.parent_id,
                    category_icon=cat.icon,
                    brand=brand,
                    confidence=confidence,
                    source="rule_category",
                )

        return None

    def classify_with_local_ai(self, title: str, raw_category: str = "") -> Optional[ClassificationResult]:
        """
        Локальный ИИ классификатор через Ollama API (qwen3-vl:8b-instruct).
        Вызывается только если детерминированные правила не дали уверенного результата.
        """
        prompt = f"""Ты — классификатор каталога товаров маркетплейса в Казахстане.
По названию товара определи бренд и категорию товара.

Название: {title}
Раздел магазина: {raw_category}

Выбери наиболее подходящую категорию из списка:
- phones (Смартфоны)
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
{{"brand": "НазваниеБренда или null", "category_id": "код_категории_из_списка"}}"""

        payload = {
            "model": self.ollama_model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.0, "num_predict": 100},
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

                        if cat:
                            return ClassificationResult(
                                category_id=cat.id,
                                category_name=cat.name,
                                category_parent_id=cat.parent_id,
                                category_icon=cat.icon,
                                brand=brand or self.extract_brand(title),
                                confidence=0.88,
                                source="local_ai",
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
        return ClassificationResult(
            category_id="electronics",
            category_name="Электроника",
            category_parent_id=None,
            category_icon="📱",
            brand=brand,
            confidence=0.50,
            source="fallback",
        )
