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
        aliases=["смартфоны", "сотовые телефоны", "мобильные телефоны", "мобильный телефон", "сотовый телефон", "smartphones", "phones", "iphone", "айфон"],
        attributes_schema={"ram": "list", "storage": "list", "screen_size": "float", "color": "string", "5g": "bool"},
    ),
    Category(
        id="telephony",
        parent_id="electronics",
        name="Стационарные и IP-телефоны",
        icon="☎️",
        search_priority=86,
        aliases=[
            "стационарные и ip-телефоны", "телефония", "ip-телефоны", "ip телефоны",
            "ip-телефон", "ip телефон", "стационарные телефоны", "стационарный телефон",
            "проводные телефоны", "проводной телефон", "радиотелефоны", "радиотелефон",
            "радио-телефоны", "радио-телефон", "dect", "voip", "sip-телефоны", "sip-телефон",
            "sip телефоны", "sip телефон", "офисные телефоны", "офисный телефон",
            "базовые станции dect", "voip телефоны", "voip телефон",
        ],
        attributes_schema={"type": "string", "screen": "bool", "interfaces": "list"},
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
        attributes_schema={
            "type": {"label": "Тип устройства", "type": "string"},
            "case_size": {"label": "Размер корпуса", "type": "string"},
            "screen_tech": {"label": "Тип дисплея", "type": "string"},
            "cellular": {"label": "Поддержка eSIM / 4G", "type": "string"},
            "strap_material": {"label": "Материал ремешка", "type": "string"},
            "color": {"label": "Цвет", "type": "string"},
        },
    ),
    Category(
        id="headphones",
        parent_id="electronics",
        name="Наушники",
        icon="🎧",
        search_priority=88,
        aliases=["наушники", "гарнитуры", "беспроводные наушники", "tws", "airpods", "headphones"],
        attributes_schema={
            "type": {"label": "Тип конструкции", "type": "string"},
            "wireless": {"label": "Беспроводные", "type": "string"},
            "interface": {"label": "Интерфейс подключения", "type": "string"},
            "anc": {"label": "Шумоподавление (ANC)", "type": "string"},
            "microphone": {"label": "Микрофон", "type": "string"},
            "color": {"label": "Цвет", "type": "string"},
        },
    ),
    Category(
        id="pc_desktops",
        parent_id="electronics",
        name="Компьютеры и ПК",
        icon="🖥",
        search_priority=87,
        aliases=["моноблоки", "моноблок", "компьютеры", "пк", "системные блоки", "персональные компьютеры", "персональный компьютер", "desktops", "pc"],
        attributes_schema={
            "type": {"label": "Тип компьютера", "type": "string"},
            "screen_size": {"label": "Диагональ экрана", "type": "string"},
            "cpu": {"label": "Процессор", "type": "string"},
            "ram": {"label": "Оперативная память", "type": "list"},
            "storage": {"label": "Накопитель SSD/HDD", "type": "list"},
            "gpu": {"label": "Видеокарта", "type": "string"},
        },
    ),
    Category(
        id="tv",
        parent_id="electronics",
        name="Телевизоры",
        icon="📺",
        search_priority=87,
        aliases=["телевизоры", "тв", "smart tv", "televisions", "oled", "qled", "проекторы", "проектор"],
        attributes_schema={
            "screen_diagonal": {"label": "Диагональ экрана", "type": "string"},
            "resolution": {"label": "Разрешение", "type": "string"},
            "screen_tech": {"label": "Технология экрана", "type": "string"},
            "smart_tv": {"label": "Smart TV", "type": "string"},
            "refresh_rate": {"label": "Частота обновления", "type": "string"},
            "color": {"label": "Цвет", "type": "string"},
        },
    ),
    Category(
        id="consoles",
        parent_id="electronics",
        name="Игровые приставки",
        icon="🕹",
        search_priority=85,
        aliases=["игровые приставки", "консоли", "playstation", "ps5", "ps4", "xbox", "nintendo switch", "steam deck"],
        attributes_schema={
            "type": {"label": "Тип устройства", "type": "string"},
            "platform": {"label": "Платформа", "type": "string"},
            "storage": {"label": "Объем памяти", "type": "string"},
            "disc_drive": {"label": "Дисковод", "type": "string"},
            "color": {"label": "Цвет", "type": "string"},
        },
    ),
    Category(
        id="pc_components",
        parent_id="electronics",
        name="Комплектующие для ПК",
        icon="🧩",
        search_priority=85,
        aliases=["комплектующие для пк", "комплектующие", "запчасти для пк", "комплектующие пк", "pc components", "pc parts"],
        attributes_schema={"power": "string", "form_factor": "string", "socket": "string", "chipset": "string", "vram": "string", "ram": "string", "storage": "string", "color": "string"},
    ),
    Category(
        id="gpus",
        parent_id="pc_components",
        name="Видеокарты",
        icon="🎮",
        search_priority=84,
        aliases=["видеокарты", "графические карты", "gpu", "graphics cards", "geforce", "radeon", "rtx"],
        attributes_schema={"vram": "list", "chipset": "string", "interface": "string"},
    ),
    Category(
        id="cpus",
        parent_id="pc_components",
        name="Процессоры",
        icon="⚙️",
        search_priority=83,
        aliases=["процессоры", "cpu", "центральные процессоры", "intel core", "amd ryzen"],
        attributes_schema={
            "cpu_family": {"label": "Серия процессора", "type": "string"},
            "socket": {"label": "Сокет", "type": "string"},
            "delivery_type": {"label": "Комплектация", "type": "string"},
            "cores": {"label": "Количество ядер", "type": "int"},
            "frequency": {"label": "Тактовая частота", "type": "float"},
        },
    ),
    Category(
        id="motherboards",
        parent_id="pc_components",
        name="Материнские платы",
        icon="🔌",
        search_priority=80,
        aliases=["материнские платы", "материнки", "motherboards", "mainboards"],
        attributes_schema={
            "socket": {"label": "Сокет", "type": "string"},
            "chipset": {"label": "Чипсет", "type": "string"},
            "memory_type": {"label": "Тип памяти", "type": "string"},
            "form_factor": {"label": "Форм-фактор", "type": "string"},
        },
    ),
    Category(
        id="psu",
        parent_id="pc_components",
        name="Блоки питания",
        icon="🔌",
        search_priority=82,
        aliases=["блоки питания", "блок питания", "бп", "power supply", "psu", "блок питания для пк", "источники питания"],
        attributes_schema={"power": "string", "form_factor": "string", "certificate": "string", "color": "string"},
    ),
    Category(
        id="pc_cases",
        parent_id="pc_components",
        name="Корпуса",
        icon="🖥",
        search_priority=82,
        aliases=["корпуса", "корпус", "компьютерный корпус", "корпус для пк", "системный блок", "pc cases", "case", "cases"],
        attributes_schema={
            "form_factor": {"label": "Форм-фактор", "type": "string"},
            "side_window": {"label": "Боковое окно", "type": "string"},
            "backlight": {"label": "Подсветка", "type": "string"},
            "psu_included": {"label": "Блок питания", "type": "string"},
            "color": {"label": "Цвет", "type": "string"},
        },
    ),
    Category(
        id="cooling",
        parent_id="pc_components",
        name="Охлаждение и кулеры",
        icon="❄️",
        search_priority=81,
        aliases=["кулеры", "кулер", "охлаждение", "системы охлаждения", "кулеры для процессора", "вентиляторы для корпуса", "сжо", "сво", "водяное охлаждение", "термопаста", "cpu cooler", "cooling"],
        attributes_schema={
            "type": {"label": "Тип охлаждения", "type": "string"},
            "fan_size": {"label": "Размер вентилятора / радиатора", "type": "string"},
            "backlight": {"label": "Подсветка", "type": "string"},
            "socket": {"label": "Сокет", "type": "string"},
            "color": {"label": "Цвет", "type": "string"},
        },
    ),
    Category(
        id="ram",
        parent_id="pc_components",
        name="Оперативная память",
        icon="🧠",
        search_priority=82,
        aliases=["оперативная память", "модули памяти", "ram", "dimm", "sodimm", "udimm"],
        attributes_schema={"ram": "string", "memory_type": "string", "frequency": "string"},
    ),
    Category(
        id="storage",
        parent_id="pc_components",
        name="SSD накопители",
        icon="💾",
        search_priority=78,
        aliases=["ssd диски", "ssd", "ssd накопители", "твердотельные накопители"],
        attributes_schema={"storage": "string", "form_factor": "string", "interface": "string"},
    ),
    Category(
        id="hdd",
        parent_id="pc_components",
        name="Жесткие диски HDD",
        icon="💽",
        search_priority=77,
        aliases=["hdd", "жесткие диски", "жёсткие диски", "жесткий диск", "жёсткий диск", "винчестеры"],
        attributes_schema={"storage": "string", "form_factor": "string"},
    ),
    Category(
        id="monitors",
        parent_id="electronics",
        name="Мониторы",
        icon="🖥",
        search_priority=82,
        aliases=["мониторы", "дисплеи", "компьютерные мониторы", "monitors"],
        attributes_schema={
            "screen_size": {"label": "Диагональ экрана", "type": "string"},
            "resolution": {"label": "Разрешение экрана", "type": "string"},
            "refresh_rate": {"label": "Частота обновления", "type": "string"},
            "panel_type": {"label": "Тип матрицы", "type": "string"},
            "curved": {"label": "Изогнутый экран", "type": "string"},
            "aspect_ratio": {"label": "Соотношение сторон", "type": "string"},
            "color": {"label": "Цвет", "type": "string"},
        },
    ),
    Category(
        id="removable_storage", parent_id="electronics", name="Флешки и карты памяти", icon="💾",
        search_priority=76,
        aliases=["флешки", "флеш накопители", "карты памяти", "карта памяти", "microsd", "sdhc", "sdxc"],
        attributes_schema={
            "storage": {"label": "Объем памяти", "type": "string"},
            "type": {"label": "Тип носителя", "type": "string"},
            "interface": {"label": "Интерфейс", "type": "string"},
        },
    ),
    Category(
        id="peripherals",
        parent_id="electronics",
        name="Клавиатуры и мыши",
        icon="⌨️",
        search_priority=75,
        aliases=[
            "клавиатуры", "мыши", "компьютерные мыши", "клавиатуры и мыши", "периферия",
            "игровая мышь", "механическая клавиатура", "коврик для мыши",
        ],
        attributes_schema={
            "type": {"label": "Тип устройства", "type": "string"},
            "connection_type": {"label": "Тип подключения", "type": "string"},
            "keyboard_type": {"label": "Тип клавиатуры", "type": "string"},
            "sensor_dpi": {"label": "Разрешение сенсора (DPI)", "type": "string"},
            "color": {"label": "Цвет", "type": "string"},
        },
    ),
    Category(
        id="network",
        parent_id="electronics",
        name="Сетевое оборудование",
        icon="📡",
        search_priority=79,
        aliases=["роутеры", "роутер", "wi-fi роутеры", "маршрутизаторы", "коммутаторы", "точки доступа", "сетевое оборудование", "mesh система", "свитч"],
        attributes_schema={
            "type": {"label": "Тип устройства", "type": "string"},
            "wifi_standard": {"label": "Стандарт Wi-Fi", "type": "string"},
            "ports_count": {"label": "Количество портов", "type": "string"},
            "lan_speed": {"label": "Скорость портов", "type": "string"},
            "poe": {"label": "Поддержка PoE", "type": "string"},
        },
    ),
    Category(
        id="printers",
        parent_id="electronics",
        name="Принтеры и МФУ",
        icon="🖨",
        search_priority=79,
        aliases=["принтеры", "принтер", "мфу", "сканеры", "плоттеры"],
        attributes_schema={
            "type": {"label": "Тип устройства", "type": "string"},
            "print_tech": {"label": "Технология печати", "type": "string"},
            "print_color": {"label": "Цветность печати", "type": "string"},
            "paper_format": {"label": "Формат бумаги", "type": "string"},
            "connectivity": {"label": "Интерфейсы и функции", "type": "string"},
        },
    ),
    Category(
        id="power",
        parent_id="electronics",
        name="ИБП и питание",
        icon="🔋",
        search_priority=78,
        aliases=["ибп", "стабилизаторы", "инверторы", "ups", "бесперебойник", "бесперебойники"],
        attributes_schema={
            "type": {"label": "Тип устройства", "type": "string"},
            "power_va": {"label": "Полная мощность (ВА)", "type": "string"},
            "power": {"label": "Активная мощность (Вт)", "type": "string"},
            "certificate": {"label": "Сертификат 80 PLUS", "type": "string"},
            "color": {"label": "Цвет", "type": "string"},
        },
    ),
    Category(
        id="photo_video",
        parent_id="electronics",
        name="Фото и видео",
        icon="📷",
        search_priority=78,
        aliases=["фотоаппараты", "экшн камеры", "веб камеры", "видеонаблюдение"],
    ),
    Category(
        id="software",
        parent_id="electronics",
        name="Программы",
        icon="💿",
        search_priority=77,
        aliases=["антивирусы", "операционные системы", "офисные программы"],
    ),
    Category(
        id="audio",
        parent_id="electronics",
        name="Аудиотехника",
        icon="🔊",
        search_priority=80,
        aliases=["аудиотехника", "колонки", "акустика", "саундбары", "микрофоны", "портативная акустика", "яндекс станция", "умная колонка"],
        attributes_schema={
            "type": {"label": "Тип аудиотехники", "type": "string"},
            "power": {"label": "Мощность", "type": "string"},
            "wireless": {"label": "Беспроводные интерфейсы", "type": "string"},
            "color": {"label": "Цвет", "type": "string"},
        },
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
        aliases=["кабели", "зарядные устройства", "адаптеры", "кабель usb", "блок питания", "зарядка", "повербанки", "powerbank", "cable", "charger", "внешний аккумулятор"],
        attributes_schema={
            "type": {"label": "Тип аксессуара", "type": "string"},
            "battery": {"label": "Емкость аккумулятора", "type": "string"},
            "power": {"label": "Мощность зарядки", "type": "string"},
            "connector": {"label": "Разъем", "type": "string"},
            "color": {"label": "Цвет", "type": "string"},
        },
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
        aliases=["бытовая техника", "техника для дома", "appliances", "home appliances", "утюги", "отпариватели", "увлажнители воздуха", "водонагреватели", "обогреватели"],
        attributes_schema={
            "type": {"label": "Тип прибора", "type": "string"},
            "power": {"label": "Мощность", "type": "string"},
            "capacity": {"label": "Объем", "type": "string"},
            "color": {"label": "Цвет", "type": "string"},
        },
    ),
    Category(
        id="refrigerators",
        parent_id="appliances",
        name="Холодильники",
        icon="❄️",
        search_priority=85,
        aliases=["холодильники", "морозильники", "refrigerators", "freezers"],
        attributes_schema={
            "type": {"label": "Тип устройства", "type": "string"},
            "refrigeration_defrost": {"label": "Разморозка", "type": "string"},
            "doors_type": {"label": "Тип конструкции", "type": "string"},
            "height_cm": {"label": "Высота", "type": "string"},
            "compressor_type": {"label": "Тип компрессора", "type": "string"},
            "freezer_location": {"label": "Расположение морозильника", "type": "string"},
            "color": {"label": "Цвет", "type": "string"},
        },
    ),
    Category(
        id="washing_machines",
        parent_id="appliances",
        name="Стиральные машины",
        icon="🧺",
        search_priority=85,
        aliases=["стиральные машины", "сушильные машины", "стиралки", "washing machines"],
        attributes_schema={
            "load_capacity": {"label": "Максимальная загрузка", "type": "string"},
            "drying": {"label": "Сушка", "type": "string"},
            "spin_speed": {"label": "Скорость отжима", "type": "string"},
            "depth_type": {"label": "Глубина корпуса", "type": "string"},
            "motor_type": {"label": "Тип двигателя", "type": "string"},
            "installation_type": {"label": "Тип установки", "type": "string"},
            "color": {"label": "Цвет", "type": "string"},
        },
    ),
    Category(
        id="vacuums",
        parent_id="appliances",
        name="Пылесосы",
        icon="🧹",
        search_priority=84,
        aliases=["пылесосы", "роботы-пылесосы", "робот пылесос", "vacuum cleaners", "dyson"],
        attributes_schema={
            "type": {"label": "Тип устройства", "type": "string"},
            "cleaning_type": {"label": "Тип уборки", "type": "string"},
            "dust_collector_type": {"label": "Тип пылесборника", "type": "string"},
            "suction_power": {"label": "Мощность всасывания", "type": "string"},
            "power": {"label": "Потребляемая мощность", "type": "string"},
            "color": {"label": "Цвет", "type": "string"},
        },
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
        attributes_schema={
            "type": {"label": "Тип", "type": "string"},
            "power_btu": {"label": "Мощность (BTU)", "type": "string"},
            "area_sqm": {"label": "Рекомендуемая площадь", "type": "string"},
            "inverter": {"label": "Инверторный компрессор", "type": "string"},
            "color": {"label": "Цвет", "type": "string"},
        },
    ),
    Category(
        id="dishwashers",
        parent_id="appliances",
        name="Посудомоечные машины",
        icon="🍽️",
        search_priority=82,
        aliases=[
            "посудомоечные машины", "посудомоечная машина", "посудомойка", "посудомойки",
            "посудомоечную машину", "посудомоечных машин", "посудомойку",
            "встраиваемая посудомоечная машина", "настольная посудомоечная машина",
            "компактная посудомоечная машина", "отдельностоящая посудомоечная машина",
            "dishwashers", "dishwasher",
        ],
        attributes_schema={
            "installation_type": {"label": "Тип установки", "type": "string"},
            "capacity_sets": {"label": "Вместимость комплектов", "type": "string"},
            "width_cm": {"label": "Ширина", "type": "string"},
            "color": {"label": "Цвет", "type": "string"},
            "water_consumption": {"label": "Расход воды", "type": "string"},
        },
    ),
    Category(
        id="kitchen_appliances",
        parent_id="appliances",
        name="Техника для кухни",
        icon="🍲",
        search_priority=78,
        aliases=[
            "микроволновые печи", "чайники", "кофемашины", "блендеры", "кухонная техника", "плиты",
            "мясорубки", "электромясорубки", "кофеварки", "кофемолки", "вспениватели молока",
            "соковыжималки", "мультиварки", "тостеры", "хлебопечки", "хлебопечи", "миксеры",
            "йогуртницы", "кухонные комбайны",
        ],
        attributes_schema={
            "type": {"label": "Тип устройства", "type": "string"},
            "power": {"label": "Мощность", "type": "int"},
            "capacity": {"label": "Объем", "type": "float"},
            "color": {"label": "Цвет", "type": "string"},
        },
    ),

    # 3. Одежда и обувь (L1)
    Category(
        id="clothing",
        name="Одежда и обувь",
        icon="👕",
        search_priority=75,
        aliases=["одежда", "обувь", "одежда и обувь", "clothing", "shoes", "fashion"],
        attributes_schema={
            "type": {"label": "Вид изделия", "type": "string"},
            "gender": {"label": "Пол и возраст", "type": "string"},
            "season": {"label": "Сезон", "type": "string"},
            "apparel_size": {"label": "Размер", "type": "string"},
            "color": {"label": "Цвет", "type": "string"},
        },
    ),
    Category(
        id="men_clothing",
        parent_id="clothing",
        name="Мужская одежда",
        icon="👔",
        search_priority=70,
        aliases=["мужская одежда", "куртки мужские", "костюмы", "футболки мужские"],
        attributes_schema={
            "type": {"label": "Вид одежды", "type": "string"},
            "season": {"label": "Сезон", "type": "string"},
            "apparel_size": {"label": "Размер", "type": "string"},
            "color": {"label": "Цвет", "type": "string"},
        },
    ),
    Category(
        id="women_clothing",
        parent_id="clothing",
        name="Женская одежда",
        icon="👗",
        search_priority=70,
        aliases=["женская одежда", "платья", "юбки", "блузки", "куртки женские"],
        attributes_schema={
            "type": {"label": "Вид одежды", "type": "string"},
            "season": {"label": "Сезон", "type": "string"},
            "apparel_size": {"label": "Размер", "type": "string"},
            "color": {"label": "Цвет", "type": "string"},
        },
    ),
    Category(
        id="shoes",
        parent_id="clothing",
        name="Обувь",
        icon="👟",
        search_priority=72,
        aliases=["обувь", "кроссовки", "ботинки", "туфли", "кеды", "сапоги", "босоножки", "сандалии", "shoes", "sneakers"],
        attributes_schema={
            "type": {"label": "Тип обуви", "type": "string"},
            "gender": {"label": "Пол и возраст", "type": "string"},
            "season": {"label": "Сезон", "type": "string"},
            "color": {"label": "Цвет", "type": "string"},
        },
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
            "строительство", "ремонт", "инструменты", "сантехника", "бытовая химия", "освещение", "лампочки", "светильники"
        ],
        attributes_schema={
            "type": {"label": "Категория товара", "type": "string"},
            "lamp_base": {"label": "Цоколь лампы", "type": "string"},
            "color_temp": {"label": "Свет / Цветовая температура", "type": "string"},
            "power": {"label": "Мощность", "type": "string"},
            "color": {"label": "Цвет", "type": "string"},
        },
    ),
    Category(
        id="furniture",
        parent_id="home",
        name="Мебель",
        icon="🛋",
        search_priority=63,
        aliases=["мебель", "шкафы", "столы", "диваны", "стулья", "кровати", "тумбы", "комоды", "матрасы", "матрас", "кресло игровое", "компьютерное кресло"],
        attributes_schema={
            "type": {"label": "Тип мебели", "type": "string"},
            "bed_size": {"label": "Размер (спальное место / габариты)", "type": "string"},
            "material": {"label": "Материал / Обивка", "type": "string"},
            "color": {"label": "Цвет", "type": "string"},
        },
    ),
    Category(
        id="home_textile",
        parent_id="home",
        name="Текстиль и декор",
        icon="🛏",
        search_priority=62,
        aliases=["текстиль", "постельное белье", "шторы", "подушки", "пледы", "ковры"],
        attributes_schema={
            "type": {"label": "Вид текстиля и декора", "type": "string"},
            "bed_size": {"label": "Размер", "type": "string"},
            "material": {"label": "Материал", "type": "string"},
            "color": {"label": "Цвет", "type": "string"},
        },
    ),
    Category(
        id="garden",
        parent_id="home",
        name="Сад и огород",
        icon="🌱",
        search_priority=61,
        aliases=["товары для сада и огорода", "товары для сада", "сад и огород", "огород", "садовая техника", "бассейны"],
        attributes_schema={
            "type": {"label": "Категория садового товара", "type": "string"},
            "power_supply": {"label": "Тип питания", "type": "string"},
            "color": {"label": "Цвет", "type": "string"},
        },
    ),
    Category(
        id="household_chem",
        parent_id="home",
        name="Бытовая химия и хозтовары",
        icon="🧼",
        search_priority=61,
        aliases=[
            "бытовая химия", "хозтовары", "средства для стирки", "стирка и уход", "чистота и порядок",
            "салфетки и туалетная бумага", "таблетки для посудомоечных машин", "капсулы для посудомоечных машин",
            "соль для посудомоечных машин", "ополаскиватель для посудомоечных машин", "средство для посудомоечных машин",
        ],
        attributes_schema={
            "type": {"label": "Назначение средства / инвентаря", "type": "string"},
            "volume_ml": {"label": "Объем / Вес", "type": "string"},
            "pieces_count": {"label": "Количество в упаковке", "type": "string"},
        },
    ),
    Category(
        id="kitchenware",
        parent_id="home",
        name="Посуда и кухня",
        icon="🍳",
        search_priority=62,
        aliases=["посуда", "сковороды", "кастрюли", "ножи", "бокалы", "тарелки", "термосы"],
        attributes_schema={
            "type": {"label": "Предмет посуды", "type": "string"},
            "diameter_cm": {"label": "Диаметр", "type": "string"},
            "capacity": {"label": "Объем", "type": "string"},
            "material": {"label": "Материал", "type": "string"},
            "color": {"label": "Цвет", "type": "string"},
        },
    ),
    Category(
        id="tools_repair",
        parent_id="home",
        name="Инструменты и ремонт",
        icon="🔨",
        search_priority=61,
        aliases=["инструменты", "дрели", "шуруповерты", "сантехника", "электроинструмент", "шлифовальные машины", "перфораторы", "болгарки", "сварочные аппараты"],
        attributes_schema={
            "type": {"label": "Тип товара", "type": "string"},
            "power_supply": {"label": "Тип питания", "type": "string"},
            "voltage": {"label": "Напряжение АКБ", "type": "string"},
            "power": {"label": "Мощность", "type": "string"},
        },
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
        aliases=["шины", "автошины", "колесные диски", "автодиски", "литые диски", "штампованные диски", "летние шины", "зимние шины", "tires"],
        attributes_schema={
            "diameter": {"label": "Диаметр диска", "type": "string"},
            "season": {"label": "Сезонность", "type": "string"},
            "tire_width": {"label": "Ширина шины", "type": "string"},
            "tire_profile": {"label": "Профиль шины", "type": "string"},
            "tire_size": {"label": "Типоразмер", "type": "string"},
        },
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
        attributes_schema={
            "type": {"label": "Категория средства / прибора", "type": "string"},
            "volume_ml": {"label": "Объем", "type": "string"},
            "color": {"label": "Цвет", "type": "string"},
        },
    ),
    Category(
        id="perfume",
        parent_id="beauty",
        name="Парфюмерия",
        icon="🌸",
        search_priority=59,
        aliases=["парфюмерия", "духи", "туалетная вода", "парфюм", "парфюмерная вода"],
        attributes_schema={
            "type": {"label": "Концентрация / Тип", "type": "string"},
            "gender": {"label": "Для кого", "type": "string"},
            "volume_ml": {"label": "Объем флакона", "type": "string"},
        },
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

    Category(
        id="medicine",
        parent_id="beauty",
        name="Лекарства и витамины",
        icon="💊",
        search_priority=56,
        aliases=["лекарства", "витамины", "аптека", "обезболивающие", "лекарственные средства", "бады"],
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
        id="diapers_hygiene",
        parent_id="kids",
        name="Подгузники и гигиена",
        icon="👶",
        search_priority=59,
        aliases=[
            "подгузники", "трусики для малышей", "памперсы", "влажные салфетки детские",
            "одноразовые пеленки", "горшки", "детский горшок", "подгузники трусики",
            "пеленальные пеленки", "утилизатор подгузников", "pampers", "huggies", "yokosun", "merries", "moony"
        ],
        attributes_schema={
            "diaper_type": {"label": "Тип", "type": "list"},
            "diaper_size": {"label": "Размер", "type": "list"},
            "weight_range": {"label": "Вес ребенка", "type": "list"},
            "pieces_count": {"label": "Количество в упаковке", "type": "list"},
        },
    ),
    Category(
        id="baby_food",
        parent_id="kids",
        name="Детское питание",
        icon="🍼",
        search_priority=58,
        aliases=[
            "детское питание", "молочная смесь", "смеси для новорожденных", "детские каши",
            "детское пюре", "пюре детское", "детские соки", "детская вода", "детское печенье",
            "детский чай", "friso", "nan", "nestle", "hipp", "nutrilon", "фрутоняня", "агуша", "тема", "gerber", "малютка"
        ],
    ),
    Category(
        id="baby_feeding",
        parent_id="kids",
        name="Кормление и посуда",
        icon="🥣",
        search_priority=57,
        aliases=[
            "бутылочки для кормления", "соски", "пустышки", "поильники", "детская посуда",
            "нагрудники", "слюнявчики", "молокоотсосы", "подогреватель бутылочек", "стерилизатор",
            "стульчик для кормления", "пакеты для грудного молока", "avent", "philips avent", "chicco", "canpol", "pigeon"
        ],
    ),
    Category(
        id="baby_bath_care",
        parent_id="kids",
        name="Купание и уход",
        icon="🛁",
        search_priority=56,
        aliases=[
            "детская ванночка", "ванночки для купания", "горка для купания", "детский шампунь",
            "детское мыло", "детский крем", "детская присыпка", "термометр для воды",
            "детский аспиратор", "круг для плавания", "уход за малышом", "детская косметика",
            "weleda", "mustela", "bubchen", "бюбхен"
        ],
    ),
    Category(
        id="nursery_sleep",
        parent_id="kids",
        name="Детская комната и сон",
        icon="🛏️",
        search_priority=55,
        aliases=[
            "детская кроватка", "детские кроватки", "матрас в кроватку", "детское постельное белье",
            "пеленальный столик", "пеленальный комод", "детский манеж", "манежи",
            "детский шезлонг", "детские качели", "мобиль в кроватку", "радионяня",
            "видеоняня", "кокон для новорожденных", "бортики в кроватку"
        ],
    ),
    Category(
        id="strollers",
        parent_id="kids",
        name="Коляски и автокресла",
        icon="🚼",
        search_priority=54,
        aliases=[
            "коляски", "автокресла", "детские коляски", "коляска люлька", "прогулочная коляска",
            "коляска 2 в 1", "коляска 3 в 1", "коляска трость", "автолюлька", "бустер",
            "слинг", "рюкзак кенгуру", "эргорюкзак", "cybex", "anex", "britax"
        ],
    ),
    Category(
        id="baby_toys",
        parent_id="kids",
        name="Игрушки для малышей",
        icon="🪅",
        search_priority=53,
        aliases=[
            "погремушки", "прорезыватели", "грызунки", "развивающий коврик", "коврик для ползания",
            "дуга с игрушками", "мобиль", "пирамидка", "сортер", "бизиборд", "игрушки для ванной",
            "tiny love", "fisher-price"
        ],
    ),
    Category(
        id="baby_care",
        parent_id="kids",
        name="Товары для малышей",
        icon="🍼",
        search_priority=52,
        aliases=["подгузники", "детское питание", "бутылочки", "соски", "пеленание", "товары для малышей", "мать и дитя", "товары для новорожденных", "для малышей"],
    ),
    Category(
        id="toys",
        parent_id="kids",
        name="Игрушки и конструкторы",
        icon="🧸",
        search_priority=51,
        aliases=["игрушки", "lego", "конструкторы", "настольные игры", "куклы", "машинки", "лего"],
        attributes_schema={
            "type": {"label": "Тип игрушки", "type": "string"},
            "lego_series": {"label": "Серия конструктора", "type": "string"},
            "pieces_count": {"label": "Количество деталей", "type": "string"},
        },
    ),
    Category(
        id="books_stationery",
        parent_id="kids",
        name="Книги и канцелярия",
        icon="📚",
        search_priority=50,
        aliases=["книги", "канцелярия", "канцтовары", "для рисования", "товары для творчества"],
        attributes_schema={
            "type": {"label": "Вид товара", "type": "string"},
            "paper_format": {"label": "Формат бумаги", "type": "string"},
            "pieces_count": {"label": "Количество листов / предметов", "type": "string"},
            "color": {"label": "Цвет", "type": "string"},
        },
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
        attributes_schema={
            "type": {"label": "Тип зоотовара", "type": "string"},
            "pet_type": {"label": "Вид питомца", "type": "string"},
            "pet_age": {"label": "Возраст и назначение", "type": "string"},
            "pack_weight": {"label": "Вес упаковки", "type": "string"},
        },
    ),
    Category(
        id="cat_food",
        parent_id="pets",
        name="Товары для кошек",
        icon="🐱",
        search_priority=54,
        aliases=["корм для кошек", "наполнители", "когтеточки", "кошачий корм"],
        attributes_schema={
            "type": {"label": "Тип товара", "type": "string"},
            "pet_age": {"label": "Возраст и особенности", "type": "string"},
            "pack_weight": {"label": "Вес упаковки", "type": "string"},
        },
    ),
    Category(
        id="dog_food",
        parent_id="pets",
        name="Товары для собак",
        icon="🐶",
        search_priority=53,
        aliases=["корм для собак", "поводки", "ошейники", "лакомства для собак"],
        attributes_schema={
            "type": {"label": "Тип товара", "type": "string"},
            "pet_age": {"label": "Возраст и порода", "type": "string"},
            "pack_weight": {"label": "Вес упаковки", "type": "string"},
        },
    ),

    # 11. Книги, музыка и хобби (L1)
    Category(
        id="books_media",
        name="Книги, музыка и хобби",
        icon="📚",
        search_priority=58,
        aliases=[
            "книги, музыка и хобби", "книги и музыка", "музыка", "книги", "винил", "хобби",
            "развлечения", "media", "books", "книги и развлечения",
        ],
    ),
    Category(
        id="vinyl",
        parent_id="books_media",
        name="Виниловые пластинки",
        icon="💿",
        search_priority=57,
        aliases=[
            "винил", "виниловые пластинки", "виниловая пластинка", "винил lp", "грампластинки",
            "грампластинка", "пластинки", "пластинка", "музыка и винил", "музыка и cd / винил",
            "винил и музыка", "vinyl", "lp", "2lp", "винил / lp",
        ],
        attributes_schema={
            "artist": {"label": "Исполнитель", "type": "string"},
            "format": {"label": "Формат", "type": "string"},
            "genre": {"label": "Жанр", "type": "string"},
            "release_year": {"label": "Год издания", "type": "int"},
        },
    ),
    Category(
        id="music_cd",
        parent_id="books_media",
        name="CD и аудиодиски",
        icon="🎵",
        search_priority=56,
        aliases=[
            "музыка и cd", "музыка на cd", "cd диски", "cd диск", "аудиодиски", "компакт диски",
            "музыкальные диски", "audio cd",
        ],
    ),
    Category(
        id="books",
        parent_id="books_media",
        name="Книги",
        icon="📖",
        search_priority=57,
        aliases=[
            "книги", "книги и канцелярия", "художественная литература", "литература",
            "books", "книга", "нонфикшн", "деловая литература",
        ],
        attributes_schema={
            "type": {"label": "Вид товара", "type": "string"},
            "paper_format": {"label": "Формат бумаги", "type": "string"},
            "author": {"label": "Автор", "type": "string"},
            "publisher": {"label": "Издательство", "type": "string"},
            "cover_type": {"label": "Переплет", "type": "string"},
        },
    ),
    Category(
        id="board_games",
        parent_id="books_media",
        name="Настольные игры",
        icon="🎲",
        search_priority=55,
        aliases=[
            "настольные игры", "настольная игра", "настолки", "board games",
            "игры для компании", "игры настольные",
        ],
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
        # Explicit IDs win before underscore normalization and fuzzy aliases (storage vs removable_storage).
        exact = self._categories.get(raw_category.strip().lower())
        if exact:
            return exact
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

    def get_children(self, category_id: str) -> List[Category]:
        """
        Возвращает список активных прямых дочерних категорий.
        """
        child_ids = self._children_map.get(category_id, [])
        return [self._categories[cid] for cid in child_ids if cid in self._categories and self._categories[cid].is_active]

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
