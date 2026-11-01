"""
Domain models and Master Taxonomy for Category Architecture (Search Platform 2.0, Stage 17).

Provides hierarchical categorization (L1 root categories, L2 subcategories),
attribute schemas, store category alias mapping, and tree navigation.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Category:
    """
    Каноническая категория товаров в мастер-таксономии KZ Price Hunter.
    """
    id: str
    name: str
    parent_id: Optional[str] = None
    icon: str = "📦"
    aliases: List[str] = field(default_factory=list)
    attributes_schema: Dict[str, Any] = field(default_factory=dict)
    search_priority: int = 50
    is_active: bool = True
    created_at: str = field(default_factory=_utc_now_iso)
    updated_at: str = field(default_factory=_utc_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> Category:
        aliases = row.get("aliases_json") or "[]"
        if isinstance(aliases, str):
            try:
                aliases = json.loads(aliases)
            except Exception:
                aliases = []
        schema = row.get("attributes_schema_json") or "{}"
        if isinstance(schema, str):
            try:
                schema = json.loads(schema)
            except Exception:
                schema = {}

        return cls(
            id=str(row["id"]),
            parent_id=str(row["parent_id"]) if row.get("parent_id") else None,
            name=str(row["name"]),
            icon=str(row.get("icon") or "📦"),
            aliases=list(aliases) if isinstance(aliases, list) else [],
            attributes_schema=dict(schema) if isinstance(schema, dict) else {},
            search_priority=int(row.get("search_priority") or 50),
            is_active=bool(row.get("is_active", 1)),
            created_at=str(row.get("created_at") or _utc_now_iso()),
            updated_at=str(row.get("updated_at") or _utc_now_iso()),
        )


# =====================================================================
# Встроенная мастер-таксономия Казахстана (10 корневых направлений и подкатегории)
# =====================================================================

MASTER_TAXONOMY: List[Category] = [
    # 1. Электроника (L1)
    Category(
        id="electronics",
        name="Электроника",
        icon="📱",
        search_priority=100,
        aliases=["электроника", "гаджеты", "цифровая техника", "electronics", "gadgets"],
    ),
    Category(
        id="phones",
        parent_id="electronics",
        name="Смартфоны",
        icon="📱",
        search_priority=99,
        aliases=["смартфоны", "телефоны", "сотовые телефоны", "мобильные телефоны", "smartphones", "phones", "iphone", "айфон"],
        attributes_schema={"ram": "list", "storage": "list", "screen_size": "float", "color": "string", "5g": "bool"},
    ),
    Category(
        id="laptops",
        parent_id="electronics",
        name="Ноутбуки",
        icon="💻",
        search_priority=98,
        aliases=["ноутбуки", "ноутбуки и ультрабуки", "ультрабуки", "laptops", "notebooks", "macbook"],
        attributes_schema={"cpu": "string", "ram": "list", "storage": "list", "gpu": "string", "screen_size": "float"},
    ),
    Category(
        id="tablets",
        parent_id="electronics",
        name="Планшеты",
        icon="📲",
        search_priority=90,
        aliases=["планшеты", "планшетные компьютеры", "tablets", "ipad"],
        attributes_schema={"ram": "list", "storage": "list", "screen_size": "float", "cellular": "bool"},
    ),
    Category(
        id="smartwatches",
        parent_id="electronics",
        name="Смарт-часы",
        icon="⌚️",
        search_priority=89,
        aliases=["смарт-часы", "умные часы", "фитнес-браслеты", "смарт часы", "smart watch", "apple watch"],
    ),
    Category(
        id="headphones",
        parent_id="electronics",
        name="Наушники",
        icon="🎧",
        search_priority=88,
        aliases=["наушники", "гарнитуры", "беспроводные наушники", "tws", "airpods", "headphones"],
    ),
    Category(
        id="pc_desktops",
        parent_id="electronics",
        name="Компьютеры и ПК",
        icon="🖥",
        search_priority=87,
        aliases=["компьютеры", "пк", "системные блоки", "персональные компьютеры", "персональный компьютер", "desktops", "pc"],
        attributes_schema={"cpu": "string", "ram": "list", "gpu": "string", "storage": "list"},
    ),
    Category(
        id="tv",
        parent_id="electronics",
        name="Телевизоры",
        icon="📺",
        search_priority=87,
        aliases=["телевизоры", "тв", "smart tv", "televisions", "oled", "qled", "проекторы", "проектор"],
    ),
    Category(
        id="consoles",
        parent_id="electronics",
        name="Игровые приставки",
        icon="🕹",
        search_priority=85,
        aliases=["игровые приставки", "консоли", "playstation", "ps5", "ps4", "xbox", "nintendo switch"],
    ),
    Category(
        id="gpus",
        parent_id="electronics",
        name="Видеокарты",
        icon="🎮",
        search_priority=84,
        aliases=["видеокарты", "графические карты", "gpu", "graphics cards", "geforce", "radeon", "rtx"],
        attributes_schema={"vram": "list", "chipset": "string", "interface": "string"},
    ),
    Category(
        id="cpus",
        parent_id="electronics",
        name="Процессоры",
        icon="⚙️",
        search_priority=83,
        aliases=["процессоры", "cpu", "центральные процессоры", "intel core", "amd ryzen"],
        attributes_schema={"socket": "string", "cores": "int", "frequency": "float"},
    ),
    Category(
        id="motherboards",
        parent_id="electronics",
        name="Материнские платы",
        icon="🔌",
        search_priority=80,
        aliases=["материнские платы", "материнки", "motherboards", "mainboards"],
    ),
    Category(
        id="monitors",
        parent_id="electronics",
        name="Мониторы",
        icon="🖥",
        search_priority=82,
        aliases=["мониторы", "дисплеи", "компьютерные мониторы", "monitors"],
    ),
    Category(
        id="storage",
        parent_id="electronics",
        name="SSD накопители",
        icon="💾",
        search_priority=78,
        aliases=["ssd", "ssd накопители", "жесткие диски", "твердотельные накопители", "hdd", "оперативная память", "ram"],
    ),
    Category(
        id="peripherals",
        parent_id="electronics",
        name="Клавиатуры и мыши",
        icon="⌨️",
        search_priority=75,
        aliases=[
            "клавиатуры", "мыши", "компьютерные мыши", "клавиатуры и мыши", "периферия",
            "корпуса", "корпус", "блоки питания", "блок питания", "кулеры", "охлаждение",
            "роутеры", "роутер", "wi-fi", "моноблоки", "моноблок", "принтеры", "принтер", "мфу"
        ],
    ),
    Category(
        id="phone_cases",
        parent_id="electronics",
        name="Чехлы и бамперы",
        icon="📱",
        search_priority=65,
        aliases=["чехлы", "чехол", "чехол-накладка", "чехол-книжка", "бампер", "case", "cover"],
    ),
    Category(
        id="phone_glass",
        parent_id="electronics",
        name="Защитные стекла и пленки",
        icon="🛡",
        search_priority=64,
        aliases=["защитные стекла", "защитная пленка", "защитное стекло", "бронестекло", "glass", "screen protector"],
    ),
    Category(
        id="cables_chargers",
        parent_id="electronics",
        name="Кабели и зарядные устройства",
        icon="🔌",
        search_priority=63,
        aliases=["кабели", "зарядные устройства", "адаптеры", "кабель usb", "блок питания", "зарядка", "повербанки", "powerbank", "cable", "charger"],
    ),
    Category(
        id="printer_ink",
        parent_id="electronics",
        name="Картриджи и чернила",
        icon="🖨",
        search_priority=60,
        aliases=["картриджи", "картридж", "тонер", "тонеры", "чернила", "cartridge", "toner"],
    ),

    # 2. Бытовая техника (L1)
    Category(
        id="appliances",
        name="Бытовая техника",
        icon="🧊",
        search_priority=90,
        aliases=["бытовая техника", "техника для дома", "appliances", "home appliances"],
    ),
    Category(
        id="refrigerators",
        parent_id="appliances",
        name="Холодильники",
        icon="❄️",
        search_priority=85,
        aliases=["холодильники", "морозильники", "refrigerators", "freezers"],
    ),
    Category(
        id="washing_machines",
        parent_id="appliances",
        name="Стиральные машины",
        icon="🧺",
        search_priority=85,
        aliases=["стиральные машины", "сушильные машины", "стиралки", "washing machines"],
    ),
    Category(
        id="vacuums",
        parent_id="appliances",
        name="Пылесосы",
        icon="🧹",
        search_priority=84,
        aliases=["пылесосы", "роботы-пылесосы", "робот пылесос", "vacuum cleaners", "dyson"],
    ),
    Category(
        id="vacuum_parts",
        parent_id="appliances",
        name="Расходники для пылесосов",
        icon="🌀",
        search_priority=62,
        aliases=["фильтры для пылесосов", "мешки для пылесоса", "пылесборники", "сменные салфетки", "hepa фильтр", "щетки для пылесоса", "насадки для пылесоса"],
    ),
    Category(
        id="air_conditioners",
        parent_id="appliances",
        name="Кондиционеры",
        icon="🌬",
        search_priority=80,
        aliases=["кондиционеры", "сплит-системы", "климатическая техника", "air conditioners"],
    ),
    Category(
        id="kitchen_appliances",
        parent_id="appliances",
        name="Техника для кухни",
        icon="🍲",
        search_priority=78,
        aliases=[
            "микроволновые печи", "чайники", "кофемашины", "блендеры", "кухонная техника", "посудомоечные машины", "плиты",
            "мясорубки", "электромясорубки", "кофеварки", "кофемолки", "вспениватели молока",
            "соковыжималки", "мультиварки", "тостеры", "хлебопечки", "хлебопечи", "миксеры",
            "йогуртницы", "кухонные комбайны",
        ],
    ),

    # 3. Одежда и обувь (L1)
    Category(
        id="clothing",
        name="Одежда и обувь",
        icon="👕",
        search_priority=75,
        aliases=["одежда", "обувь", "одежда и обувь", "clothing", "shoes", "fashion"],
    ),
    Category(
        id="men_clothing",
        parent_id="clothing",
        name="Мужская одежда",
        icon="👔",
        search_priority=70,
        aliases=["мужская одежда", "куртки мужские", "костюмы", "футболки мужские"],
    ),
    Category(
        id="women_clothing",
        parent_id="clothing",
        name="Женская одежда",
        icon="👗",
        search_priority=70,
        aliases=["женская одежда", "платья", "юбки", "блузки", "куртки женские"],
    ),
    Category(
        id="shoes",
        parent_id="clothing",
        name="Обувь",
        icon="👟",
        search_priority=72,
        aliases=["обувь", "кроссовки", "ботинки", "туфли", "кеды", "shoes", "sneakers"],
    ),

    # 4. Продукты питания (L1)
    Category(
        id="food",
        name="Продукты питания",
        icon="🍏",
        search_priority=70,
        aliases=[
            "продукты", "продукты питания", "еда", "супермаркет", "food", "grocery",
            "бакалея", "кондитерские изделия", "сладости", "шоколад", "сыр", "молоко", "яйца"
        ],
    ),
    Category(
        id="meat_poultry",
        parent_id="food",
        name="Мясо и птица",
        icon="🥩",
        search_priority=65,
        aliases=["мясо", "птица", "говядина", "конина", "баранина", "курица", "фарш"],
    ),
    Category(
        id="dairy",
        parent_id="food",
        name="Молочные продукты",
        icon="🧀",
        search_priority=65,
        aliases=["молочные продукты", "молоко", "сыр", "творог", "масло сливочное", "йогурт"],
    ),
    Category(
        id="drinks",
        parent_id="food",
        name="Напитки и соки",
        icon="🧃",
        search_priority=60,
        aliases=["напитки", "соки", "вода", "газировка", "чай", "кофе"],
    ),

    # 5. Дом и сад (L1)
    Category(
        id="home",
        name="Дом и сад",
        icon="🏠",
        search_priority=65,
        aliases=[
            "дом и сад", "товары для дома", "мебель", "текстиль", "посуда", "home",
            "шкафы", "стеллажи", "кровати", "комоды", "тумбы", "диваны", "кресла", "столы", "стулья", "автоматика для ворот",
            "строительство", "ремонт", "инструменты", "сантехника", "бытовая химия"
        ],
    ),
    Category(
        id="furniture",
        parent_id="home",
        name="Мебель",
        icon="🛋",
        search_priority=63,
        aliases=["мебель", "шкафы", "столы", "диваны", "стулья", "кровати", "тумбы", "комоды"],
    ),
    Category(
        id="home_textile",
        parent_id="home",
        name="Текстиль и декор",
        icon="🛏",
        search_priority=62,
        aliases=["текстиль", "постельное белье", "шторы", "подушки", "пледы", "ковры"],
    ),
    Category(
        id="kitchenware",
        parent_id="home",
        name="Посуда и кухня",
        icon="🍳",
        search_priority=62,
        aliases=["посуда", "сковороды", "кастрюли", "ножи", "бокалы", "тарелки"],
    ),
    Category(
        id="tools_repair",
        parent_id="home",
        name="Инструменты и ремонт",
        icon="🔨",
        search_priority=61,
        aliases=["инструменты", "дрели", "шуруповерты", "сантехника", "электроинструмент", "шлифовальные машины"],
    ),

    # 6. Автотовары (L1)
    Category(
        id="auto",
        name="Автотовары",
        icon="🚗",
        search_priority=65,
        aliases=[
            "автотовары", "запчасти", "автомасла", "авто", "автомобильные",
            "автокомпрессоры", "автодержатели",
        ],
    ),
    Category(
        id="tires",
        parent_id="auto",
        name="Шины и диски",
        icon="🛞",
        search_priority=64,
        aliases=["шины", "автошины", "диски", "летние шины", "зимние шины", "tires"],
    ),
    Category(
        id="car_electronics",
        parent_id="auto",
        name="Автоэлектроника",
        icon="📹",
        search_priority=63,
        aliases=["видеорегистраторы", "радар-детекторы", "автомагнитолы", "навигаторы", "парктроники"],
    ),
    Category(
        id="car_oils",
        parent_id="auto",
        name="Масла и автохимия",
        icon="🛢",
        search_priority=62,
        aliases=["моторные масла", "автомасла", "антифриз", "омыватель", "автохимия"],
    ),

    # 7. Красота и здоровье (L1)
    Category(
        id="beauty",
        name="Красота и здоровье",
        icon="💄",
        search_priority=60,
        aliases=[
            "красота", "здоровье", "косметика", "парфюмерия", "beauty", "cosmetics",
            "макияж", "уход за лицом", "уход за телом", "уход за волосами", "лекарственные средства",
            "дермакосметика",
        ],
    ),
    Category(
        id="perfume",
        parent_id="beauty",
        name="Парфюмерия",
        icon="🌸",
        search_priority=59,
        aliases=["парфюмерия", "духи", "туалетная вода", "парфюм"],
    ),
    Category(
        id="skincare",
        parent_id="beauty",
        name="Уход за кожей",
        icon="🧴",
        search_priority=58,
        aliases=["уход за лицом", "уход за кожей", "кремы", "сыворотки", "маски для лица"],
    ),
    Category(
        id="haircare",
        parent_id="beauty",
        name="Уход за волосами",
        icon="💇",
        search_priority=58,
        aliases=["уход за волосами", "шампуни", "бальзамы", "фены", "стайлеры", "машинки для стрижки"],
    ),
    Category(
        id="makeup",
        parent_id="beauty",
        name="Декоративная косметика",
        icon="💄",
        search_priority=57,
        aliases=["косметика", "макияж", "помада", "тушь", "тональный крем", "тени"],
    ),

    # 8. Детские товары (L1)
    Category(
        id="kids",
        name="Детские товары",
        icon="🧸",
        search_priority=60,
        aliases=[
            "детские товары", "игрушки", "товары для детей", "коляски", "kids", "детский транспорт",
            "lego", "конструкторы", "детское питание", "подгузники", "канцелярия", "школа", "книги"
        ],
    ),
    Category(
        id="strollers",
        parent_id="kids",
        name="Коляски и автокресла",
        icon="👶",
        search_priority=59,
        aliases=["коляски", "автокресла", "детские коляски"],
    ),
    Category(
        id="toys",
        parent_id="kids",
        name="Игрушки и конструкторы",
        icon="🧸",
        search_priority=58,
        aliases=["игрушки", "lego", "конструкторы", "настольные игры", "куклы", "машинки"],
    ),
    Category(
        id="baby_care",
        parent_id="kids",
        name="Товары для малышей",
        icon="🍼",
        search_priority=57,
        aliases=["подгузники", "детское питание", "бутылочки", "соски", "пеленание"],
    ),

    # 9. Спорт и отдых (L1)
    Category(
        id="sport",
        name="Спорт и отдых",
        icon="⚽",
        search_priority=55,
        aliases=["спорт", "спорт и отдых", "тренажеры", "туризм", "велосипеды", "sport"],
    ),
    Category(
        id="bicycles",
        parent_id="sport",
        name="Велосипеды и самокаты",
        icon="🚲",
        search_priority=54,
        aliases=["велосипеды", "самокаты", "электросамокаты", "скейтборды"],
    ),
    Category(
        id="fitness",
        parent_id="sport",
        name="Фитнес и тренажеры",
        icon="🏋️",
        search_priority=53,
        aliases=["тренажеры", "гантели", "коврики для фитнеса", "эспандеры", "беговые дорожки"],
    ),
    Category(
        id="tourism",
        parent_id="sport",
        name="Туризм и кемпинг",
        icon="⛺",
        search_priority=52,
        aliases=["палатки", "спальные мешки", "рюкзаки туристические", "термосы", "кемпинг"],
    ),

    # 10. Зоотовары (L1)
    Category(
        id="pets",
        name="Зоотовары",
        icon="🐾",
        search_priority=55,
        aliases=["зоотовары", "товары для животных", "корм для кошек", "корм для собак", "pets"],
    ),
    Category(
        id="cat_food",
        parent_id="pets",
        name="Товары для кошек",
        icon="🐱",
        search_priority=54,
        aliases=["корм для кошек", "наполнители", "когтеточки", "кошачий корм"],
    ),
    Category(
        id="dog_food",
        parent_id="pets",
        name="Товары для собак",
        icon="🐶",
        search_priority=53,
        aliases=["корм для собак", "поводки", "ошейники", "лакомства для собак"],
    ),
]


class TaxonomyMapper:
    """
    Нормализатор и сопоставитель сырых категорий магазинов с канонической мастер-таксономией.
    """

    def __init__(self, categories: Optional[Iterable[Category]] = None):
        self._categories: Dict[str, Category] = {}
        self._alias_map: Dict[str, str] = {}
        self._children_map: Dict[str, List[str]] = {}

        for cat in (categories or MASTER_TAXONOMY):
            self.register_category(cat)

    def register_category(self, cat: Category) -> None:
        self._categories[cat.id] = cat
        self._children_map.setdefault(cat.id, [])
        if cat.parent_id:
            self._children_map.setdefault(cat.parent_id, []).append(cat.id)

        # Каноническое имя как алиас
        clean_name = self._normalize_token(cat.name)
        if clean_name:
            self._alias_map[clean_name] = cat.id

        # id как алиас
        self._alias_map[cat.id.lower()] = cat.id

        # Все заявленные синонимы
        for alias in cat.aliases:
            clean_alias = self._normalize_token(alias)
            if clean_alias:
                self._alias_map[clean_alias] = cat.id

    @staticmethod
    def _normalize_token(text: str) -> str:
        text = (text or "").strip().lower()
        # Срезаем префикс магазина (напр. "Forte: ", "ITMag: ", "12 Месяцев: ")
        text = re.sub(r"^[^:]+:\s*", "", text)
        # Срезаем эмодзи и спецсимволы юникода
        text = re.sub(r"[\U00010000-\U0010ffff\u2600-\u26ff\u2700-\u27bf]", " ", text)
        text = re.sub(r"[\s\-_/\\|]+", " ", text).strip()
        return text

    @staticmethod
    def _words_match(needle: str, haystack: str) -> bool:
        """
        Ищет фразу по целым словам: окончание может отличаться на 1-2 буквы
        ("смартфон" и "смартфоны"), но "мясо" не совпадает с "мясорубки".
        """
        needle_words = re.findall(r"\w+", needle)
        hay_words = re.findall(r"\w+", haystack)
        if not needle_words:
            return False

        def same(a: str, b: str) -> bool:
            short, long_ = sorted((a, b), key=len)
            if a == b:
                return True
            return len(short) >= 4 and len(long_) - len(short) <= 2 and long_.startswith(short)

        for start in range(len(hay_words) - len(needle_words) + 1):
            if all(same(n, hay_words[start + i]) for i, n in enumerate(needle_words)):
                return True
        return False

    def get_by_id(self, category_id: str) -> Optional[Category]:
        return self._categories.get(category_id)

    def map_category(self, raw_category: str) -> Optional[Category]:
        """
        Сопоставляет строку категории магазина с канонической категорией мастер-таксономии.
        """
        if not raw_category:
            return None
        token = self._normalize_token(raw_category)
        if not token:
            return None

        # 1. Прямое совпадение
        if token in self._alias_map:
            return self._categories.get(self._alias_map[token])

        # 2. Поиск по подстроке алиасов (напр. "Каталог / Смартфоны и гаджеты / Apple" -> "phones")
        # Приоритет отдаем более длинным и более глубоким совпадениям (L2 перед L1)
        best_cat_id = None
        best_len = 0
        for alias, cat_id in self._alias_map.items():
            if len(alias) >= 4 and (self._words_match(alias, token) or self._words_match(token, alias)):
                cat = self._categories.get(cat_id)
                # Предпочитаем L2 категории (parent_id не None)
                bonus = 100 if cat and cat.parent_id else 0
                score = len(alias) + bonus
                if score > best_len:
                    best_len = score
                    best_cat_id = cat_id

        if best_cat_id:
            return self._categories.get(best_cat_id)

        return None

    def get_subtree_ids(self, category_id: str) -> Set[str]:
        """
        Возвращает множество идентификаторов: саму категорию и всех её прямых и косвенных потомков.
        """
        result = {category_id}
        stack = [category_id]
        while stack:
            curr = stack.pop()
            children = self._children_map.get(curr, [])
            for child in children:
                if child not in result:
                    result.add(child)
                    stack.append(child)
        return result

    def get_breadcrumbs(self, category_id: str) -> List[Dict[str, str]]:
        """
        Возвращает навигационную цепочку (хлебные крошки): [Root, L2, ...].
        """
        trail = []
        curr_id = category_id
        visited = set()
        while curr_id and curr_id not in visited:
            visited.add(curr_id)
            cat = self._categories.get(curr_id)
            if not cat:
                break
            trail.append({"id": cat.id, "name": cat.name, "icon": cat.icon})
            curr_id = cat.parent_id
        trail.reverse()
        return trail

    def build_tree(self) -> List[Dict[str, Any]]:
        """
        Строит иерархическое дерево категорий с сортировкой по search_priority.
        """
        roots = [c for c in self._categories.values() if c.parent_id is None and c.is_active]
        roots.sort(key=lambda x: -x.search_priority)

        def _build_node(cat: Category) -> Dict[str, Any]:
            node = cat.to_dict()
            child_ids = self._children_map.get(cat.id, [])
            children = [self._categories[cid] for cid in child_ids if cid in self._categories and self._categories[cid].is_active]
            children.sort(key=lambda x: -x.search_priority)
            node["children"] = [_build_node(c) for c in children]
            return node

        return [_build_node(r) for r in roots]
