import os
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "prices.db"
SETTINGS_FILE = DATA_DIR / "settings.json"

# Дефолтные настройки
DEFAULT_SETTINGS = {
    "telegram_bot_token": os.getenv("DNS_BOT_TOKEN", ""),
    "telegram_chat_id": os.getenv("DNS_CHAT_ID", ""),
    "telegram_notify_level": "ALL",  # ALL, CRITICAL_ONLY, HIGH_SAVINGS
    "city_name": "Астана",

    # Детекция аномалий
    "detect_zero_glitch": True,
    "detect_super_discount": True,
    "detect_market_arbitrage": True,
    "min_item_price_kzt": 30000,
    "max_item_price_kzt": 3000000,
    "price_glitch_drop_pct": 65,
    "min_savings_kzt": 40000,
    "arbitrage_min_diff_kzt": 35000,

    # Фильтры хлама / стоп-слова
    "junk_keywords": [
        "чехол", "пленка", "плёнка", "стекло", "кабель", "переходник",
        "ремешок", "держатель", "подставка", "амбушюры", "накладка", "салфетки"
    ],
    "exclude_used_goods": True,

    # Сканирование (авто-обновление если база старше 3 часов)
    "check_interval_seconds": 300,
    "scan_interval_minutes": 180,

    # Межмагазинный арбитраж (глубокие скидки по сравнению с другими магазинами)
    "detect_market_arbitrage": True,
    "arbitrage_min_drop_pct": 25.0,
    "arbitrage_min_diff_kzt": 25000,

    # Поиск по умолчанию (Cache-First / Anti-DDoS)
    "search_exclude_accessories_default": True,
    "search_default_sort": "price_asc",
    "search_auto_live": False,

    # Магазины
    "enabled_shops": {
        "dns": True,
        "shopkz": True,
        "technodom": True,
        "forcecom": True,
        "sulpak": True,
        "mechta": True,
        "alser": True,
        "evrika": True,
        "moon": True,
        "kaspi": True,
        "fourmobile": True
    }
}

def load_settings():
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                merged = dict(DEFAULT_SETTINGS)
                merged.update(data)
                merged_shops = dict(DEFAULT_SETTINGS["enabled_shops"])
                if "enabled_shops" in data:
                    merged_shops.update(data["enabled_shops"])
                merged["enabled_shops"] = merged_shops
                return merged
        except Exception:
            pass
    return dict(DEFAULT_SETTINGS)

def save_settings(new_settings):
    current = load_settings()
    current.update(new_settings)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(current, f, indent=2, ensure_ascii=False)
    return current

# Инициализируем текущие значения
_s = load_settings()

TELEGRAM_BOT_TOKEN = _s.get("telegram_bot_token", "")
TELEGRAM_CHAT_ID = _s.get("telegram_chat_id", "")
TELEGRAM_NOTIFY_LEVEL = _s.get("telegram_notify_level", "ALL")
CITY_NAME = _s.get("city_name", "Астана")

DETECT_ZERO_GLITCH = _s.get("detect_zero_glitch", True)
DETECT_SUPER_DISCOUNT = _s.get("detect_super_discount", True)
DETECT_MARKET_ARBITRAGE = _s.get("detect_market_arbitrage", True)
MIN_ITEM_PRICE_KZT = _s.get("min_item_price_kzt", 30000)
MAX_ITEM_PRICE_KZT = _s.get("max_item_price_kzt", 3000000)
PRICE_GLITCH_DROP_PCT = _s.get("price_glitch_drop_pct", 65)
MIN_SAVINGS_KZT = _s.get("min_savings_kzt", 40000)
ARBITRAGE_MIN_DIFF_KZT = _s.get("arbitrage_min_diff_kzt", 35000)
JUNK_KEYWORDS = _s.get("junk_keywords", DEFAULT_SETTINGS["junk_keywords"])
EXCLUDE_USED_GOODS = _s.get("exclude_used_goods", True)

CHECK_INTERVAL_SECONDS = _s.get("check_interval_seconds", 300)
SCAN_INTERVAL_MINUTES = _s.get("scan_interval_minutes", 15)
ENABLED_SHOPS = _s.get("enabled_shops", DEFAULT_SETTINGS["enabled_shops"])

SEARCH_CACHE_TTL_SECONDS = 3600

# Справочник городов Казахстана и параметров сетей
CITIES_KZ = {
    "astana": {
        "id": "astana",
        "name": "Астана",
        "flag": "🏛",
        "kaspi_code": "710000000",
        "dns_slug": "astana",
        "shopkz_city": "astana",
        "mechta_code": "astana",
        "sulpak_id": 1,
        "technodom_slug": "astana",
        "alser_slug": "astana",
        "evrika_slug": "nur-sultan-astana"
    },
    "almaty": {
        "id": "almaty",
        "name": "Алматы",
        "flag": "🍏",
        "kaspi_code": "750000000",
        "dns_slug": "almaty",
        "shopkz_city": "almaty",
        "mechta_code": "almaty",
        "sulpak_id": 2,
        "technodom_slug": "almaty",
        "alser_slug": "almaty",
        "evrika_slug": "almaty"
    },
    "shymkent": {
        "id": "shymkent",
        "name": "Шымкент",
        "flag": "☀️",
        "kaspi_code": "591010000",
        "dns_slug": "shymkent",
        "shopkz_city": "shymkent",
        "mechta_code": "shymkent",
        "sulpak_id": 3,
        "technodom_slug": "shymkent",
        "alser_slug": "shymkent",
        "evrika_slug": "shymkent"
    },
    "karaganda": {
        "id": "karaganda",
        "name": "Караганда",
        "flag": "🏭",
        "kaspi_code": "351010000",
        "dns_slug": "karaganda",
        "shopkz_city": "karaganda",
        "mechta_code": "karaganda",
        "sulpak_id": 4,
        "technodom_slug": "karaganda",
        "alser_slug": "karaganda",
        "evrika_slug": "karaganda"
    },
    "aktobe": {
        "id": "aktobe",
        "name": "Актобе",
        "flag": "🌊",
        "kaspi_code": "151010000",
        "dns_slug": "aktobe",
        "shopkz_city": "aktobe",
        "mechta_code": "aktobe",
        "sulpak_id": 5,
        "technodom_slug": "aktobe",
        "alser_slug": "aktobe",
        "evrika_slug": "aktobe"
    },
    "pavlodar": {
        "id": "pavlodar",
        "name": "Павлодар",
        "flag": "⛏",
        "kaspi_code": "551010000",
        "dns_slug": "pavlodar",
        "shopkz_city": "pavlodar",
        "mechta_code": "pavlodar",
        "sulpak_id": 6,
        "technodom_slug": "pavlodar",
        "alser_slug": "pavlodar",
        "evrika_slug": "pavlodar"
    },
    "oskemen": {
        "id": "oskemen",
        "name": "Усть-Каменогорск",
        "flag": "🌲",
        "kaspi_code": "631010000",
        "dns_slug": "ust-kamenogorsk",
        "shopkz_city": "ust-kamenogorsk",
        "mechta_code": "oskemen",
        "sulpak_id": 7,
        "technodom_slug": "oskemen",
        "alser_slug": "oskemen",
        "evrika_slug": "oskemen"
    }
}

ZERO_DROP_RATIO_MIN = 8.0
ZERO_DROP_RATIO_MAX = 12.0

# 1. DNS Казахстан (dns-shop.kz)
DNS_CATEGORIES = [
    {"name": "DNS: 🔥 Все акции и распродажи", "url": "https://www.dns-shop.kz/catalog/actions/", "max_pages": 3},
    {"name": "DNS: 💻 Ноутбуки", "url": "https://www.dns-shop.kz/catalog/17a892f816404e77/noutbuki/", "max_pages": 3},
    {"name": "DNS: 📱 Смартфоны", "url": "https://www.dns-shop.kz/catalog/17a8a01d16404e77/smartfony/", "max_pages": 3},
    {"name": "DNS: 🎮 Видеокарты", "url": "https://www.dns-shop.kz/catalog/17a89aab16404e77/videokarty/", "max_pages": 3},
    {"name": "DNS: ⚙️ Процессоры", "url": "https://www.dns-shop.kz/catalog/17a899cd16404e77/processory/", "max_pages": 3},
    {"name": "DNS: 🖥 Мониторы", "url": "https://www.dns-shop.kz/catalog/17a8943716404e77/monitory/", "max_pages": 3},
    {"name": "DNS: 📺 Телевизоры", "url": "https://www.dns-shop.kz/catalog/17a8ae4916404e77/televizory/", "max_pages": 3},
    {"name": "DNS: 📱 Планшеты", "url": "https://www.dns-shop.kz/catalog/17a890dc16404e77/planshety/", "max_pages": 2},
    {"name": "DNS: 💾 SSD накопители", "url": "https://www.dns-shop.kz/catalog/8a9ddbe317404e77/nakopiteli-ssd/", "max_pages": 2},
    {"name": "DNS: 🧠 Оперативная память", "url": "https://www.dns-shop.kz/catalog/17a89a3916404e77/operativnaya-pamyat-dimm/", "max_pages": 2},
    {"name": "DNS: 🎧 Наушники и гарнитуры", "url": "https://www.dns-shop.kz/catalog/17a8f3cd16404e77/naushniki-i-garnitury/", "max_pages": 2},
    {"name": "DNS: ⌚️ Смарт-часы", "url": "https://www.dns-shop.kz/catalog/17a9e70116404e77/smart-chasy-i-braslety/", "max_pages": 2},
    {"name": "DNS: 🎮 Игровые консоли", "url": "https://www.dns-shop.kz/catalog/17a8a65f16404e77/igrovye-konsoli/", "max_pages": 2}
]

# 2. Белый Ветер (shop.kz)
SHOPKZ_CATEGORIES = [
    {"name": "Белый Ветер: 📦 Официальная YML выгрузка (16k+ товаров)", "url": "https://shop.kz/bitrix/catalog_export/yandex.php", "max_pages": 1},
    {"name": "Белый Ветер: 💻 Ноутбуки", "url": "https://shop.kz/offers/noutbuki/", "max_pages": 4},
    {"name": "Белый Ветер: 📱 Смартфоны", "url": "https://shop.kz/offers/smartfony/", "max_pages": 4},
    {"name": "Белый Ветер: 🎮 Видеокарты", "url": "https://shop.kz/offers/videokarty/", "max_pages": 4},
    {"name": "Белый Ветер: ⚙️ Процессоры", "url": "https://shop.kz/offers/protsessory/", "max_pages": 4},
    {"name": "Белый Ветер: 🖥 Мониторы", "url": "https://shop.kz/offers/monitory/", "max_pages": 4},
    {"name": "Белый Ветер: 🧩 Материнские платы", "url": "https://shop.kz/offers/materinskie-platy/", "max_pages": 3},
    {"name": "Белый Ветер: 🧠 Оперативная память", "url": "https://shop.kz/offers/operativnaya-pamyat/", "max_pages": 3},
    {"name": "Белый Ветер: 💾 SSD диски", "url": "https://shop.kz/offers/ssd-diski/", "max_pages": 3},
    {"name": "Белый Ветер: ⚡️ Блоки питания", "url": "https://shop.kz/offers/bloki-pitaniya/", "max_pages": 3},
    {"name": "Белый Ветер: 📦 Корпуса", "url": "https://shop.kz/offers/korpusa/", "max_pages": 3},
    {"name": "Белый Ветер: ❄️ Кулеры процессоров", "url": "https://shop.kz/offers/kulery-dlya-protsessora/", "max_pages": 3},
    {"name": "Белый Ветер: 📱 Планшеты", "url": "https://shop.kz/offers/planshety/", "max_pages": 3},
    {"name": "Белый Ветер: 📺 Телевизоры", "url": "https://shop.kz/offers/televizory/", "max_pages": 3},
    {"name": "Белый Ветер: 🎧 Наушники и гарнитуры", "url": "https://shop.kz/offers/naushniki-i-garnitury/", "max_pages": 3},
    {"name": "Белый Ветер: ⌚️ Смарт-часы", "url": "https://shop.kz/offers/smart-chasy/", "max_pages": 3},
    {"name": "Белый Ветер: ⌨️ Клавиатуры", "url": "https://shop.kz/offers/klaviatury/", "max_pages": 3},
    {"name": "Белый Ветер: 🖱 Мыши", "url": "https://shop.kz/offers/myshi/", "max_pages": 3},
    {"name": "Белый Ветер: 🌐 Роутеры и модемы", "url": "https://shop.kz/offers/routery-modemy/", "max_pages": 3},
    {"name": "Белый Ветер: 🏷 Уценка Ноутбуки", "url": "https://shop.kz/offers/noutbuki/utsenennyy-tovar/", "max_pages": 3},
    {"name": "Белый Ветер: 🏷 Уценка Смартфоны", "url": "https://shop.kz/offers/smartfony/utsenennyy-tovar/", "max_pages": 3}
]

# 3. Технодом (technodom.kz)
TECHNODOM_CATEGORIES = [
    {"name": "Технодом: 💻 Ноутбуки", "url": "https://www.technodom.kz/catalog/noutbuki-i-komp-jutery/noutbuki/noutbuki", "max_pages": 3},
    {"name": "Технодом: 📱 Смартфоны", "url": "https://www.technodom.kz/catalog/smartfony-i-gadzhety/smartfony-i-telefony/smartfony", "max_pages": 3},
    {"name": "Технодом: 📺 Телевизоры", "url": "https://www.technodom.kz/catalog/tv-audio-foto-video/televizory/led-televizory", "max_pages": 3},
    {"name": "Технодом: 📱 Планшеты", "url": "https://www.technodom.kz/catalog/smartfony-i-gadzhety/planshety-i-knigi/planshety", "max_pages": 2},
    {"name": "Технодом: ⌚️ Смарт-часы", "url": "https://www.technodom.kz/catalog/smartfony-i-gadzhety/gadzhety/smart-chasy", "max_pages": 2},
    {"name": "Технодом: 🎧 Наушники", "url": "https://www.technodom.kz/catalog/tv-audio-foto-video/audio-tehnika/naushniki", "max_pages": 2},
    {"name": "Технодом: 🖥 Мониторы", "url": "https://www.technodom.kz/catalog/noutbuki-i-komp-jutery/komp-jutery-i-monobloki/monitory", "max_pages": 2},
    {"name": "Технодом: 🎮 Игровые приставки", "url": "https://www.technodom.kz/catalog/noutbuki-i-komp-jutery/igrovye-pristavki-i-igry/igrovye-pristavki", "max_pages": 2},
    {"name": "Технодом: 🧹 Пылесосы", "url": "https://www.technodom.kz/catalog/bytovaja-tehnika/tehnika-dlja-doma/pylesosy", "max_pages": 2}
]

# 4. Forcecom (forcecom.kz)
FORCECOM_CATEGORIES = [
    {"name": "Forcecom: 🔥 Распродажа", "url": "https://forcecom.kz/sale/rasprodazha/", "max_pages": 3},
    {"name": "Forcecom: 💻 Ноутбуки", "url": "https://forcecom.kz/catalog/laptops/", "max_pages": 3},
    {"name": "Forcecom: 🎮 Видеокарты", "url": "https://forcecom.kz/catalog/graphics-cards/", "max_pages": 3},
    {"name": "Forcecom: 🧩 Материнские платы", "url": "https://forcecom.kz/catalog/motherboards/", "max_pages": 3},
    {"name": "Forcecom: 🧠 Оперативная память", "url": "https://forcecom.kz/catalog/ram/", "max_pages": 3},
    {"name": "Forcecom: 🖥 Мониторы", "url": "https://forcecom.kz/catalog/monitors/", "max_pages": 3},
    {"name": "Forcecom: 💾 SSD диски", "url": "https://forcecom.kz/catalog/ssd/", "max_pages": 3},
    {"name": "Forcecom: 💾 Жесткие диски", "url": "https://forcecom.kz/catalog/hdd/", "max_pages": 3},
    {"name": "Forcecom: 📦 Корпуса", "url": "https://forcecom.kz/catalog/cases/", "max_pages": 3},
    {"name": "Forcecom: 🎧 Наушники", "url": "https://forcecom.kz/catalog/headphones/", "max_pages": 3},
    {"name": "Forcecom: ⌨️ Клавиатуры", "url": "https://forcecom.kz/catalog/keyboards/", "max_pages": 3},
    {"name": "Forcecom: 🖱 Мыши", "url": "https://forcecom.kz/catalog/mice/", "max_pages": 3},
    {"name": "Forcecom: 🖨 Принтеры", "url": "https://forcecom.kz/catalog/printers/", "max_pages": 2},
    {"name": "Forcecom: 🌐 Роутеры", "url": "https://forcecom.kz/catalog/marshrutizatory/", "max_pages": 2}
]

# 5. Sulpak (sulpak.kz)
SULPAK_CATEGORIES = [
    {"name": "Sulpak: 💻 Ноутбуки", "url": "https://www.sulpak.kz/f/noutbuki", "max_pages": 3},
    {"name": "Sulpak: 📱 Смартфоны", "url": "https://www.sulpak.kz/f/smartfoniy/", "max_pages": 3},
    {"name": "Sulpak: 📺 Телевизоры", "url": "https://www.sulpak.kz/f/led_oled_televizoriy", "max_pages": 3},
    {"name": "Sulpak: 📱 Планшеты", "url": "https://www.sulpak.kz/f/planshetiy", "max_pages": 3},
    {"name": "Sulpak: ⌚️ Смарт-часы", "url": "https://www.sulpak.kz/f/smart_chasiy", "max_pages": 3},
    {"name": "Sulpak: 🎧 Наушники", "url": "https://www.sulpak.kz/f/naushniki", "max_pages": 3},
    {"name": "Sulpak: 🎮 Игровые приставки", "url": "https://www.sulpak.kz/f/igroviye_pristavki", "max_pages": 2},
    {"name": "Sulpak: 🧺 Стиральные машины", "url": "https://www.sulpak.kz/f/stiralniye_mashiniy", "max_pages": 3},
    {"name": "Sulpak: ❄️ Холодильники", "url": "https://www.sulpak.kz/f/holodilniki", "max_pages": 3},
    {"name": "Sulpak: 🌬 Кондиционеры", "url": "https://www.sulpak.kz/f/kondicioneriy", "max_pages": 3},
    {"name": "Sulpak: ☕️ Кофемашины", "url": "https://www.sulpak.kz/f/kofemashiniy", "max_pages": 2},
    {"name": "Sulpak: ♨️ Микроволновые печи", "url": "https://www.sulpak.kz/f/mikrovolnoviye_pechi", "max_pages": 2},
    {"name": "Sulpak: 🔥 Распродажа", "url": "https://www.sulpak.kz/sale/1", "max_pages": 3}
]

# 6. Мечта (mechta.kz)
MECHTA_CATEGORIES = [
    {"name": "Мечта: 💻 Ноутбуки", "url": "https://www.mechta.kz/section/noutbuki/", "max_pages": 4},
    {"name": "Мечта: 📱 Смартфоны", "url": "https://www.mechta.kz/section/smartfony/", "max_pages": 4},
    {"name": "Мечта: 📺 Телевизоры", "url": "https://www.mechta.kz/section/televizory/", "max_pages": 4},
    {"name": "Мечта: 🖥 Мониторы", "url": "https://www.mechta.kz/section/monitory/", "max_pages": 3},
    {"name": "Мечта: 📱 Планшеты", "url": "https://www.mechta.kz/section/planshety/", "max_pages": 3},
    {"name": "Мечта: ⌚️ Смарт-часы", "url": "https://www.mechta.kz/section/smart-chasy/", "max_pages": 3},
    {"name": "Мечта: 🎧 Наушники", "url": "https://www.mechta.kz/section/naushniki/", "max_pages": 3},
    {"name": "Мечта: 🎮 Игровые приставки", "url": "https://www.mechta.kz/section/igrovye-pristavki/", "max_pages": 2},
    {"name": "Мечта: 🧹 Пылесосы", "url": "https://www.mechta.kz/section/pylesosy/", "max_pages": 3},
    {"name": "Мечта: ❄️ Холодильники", "url": "https://www.mechta.kz/section/holodilniki/", "max_pages": 3},
    {"name": "Мечта: 🧺 Стиральные машины", "url": "https://www.mechta.kz/section/stiralnye-mashiny/", "max_pages": 3},
    {"name": "Мечта: ☕️ Кофемашины", "url": "https://www.mechta.kz/section/kofemashiny/", "max_pages": 3},
    {"name": "Мечта: 🌬 Кондиционеры", "url": "https://www.mechta.kz/section/kondicionery/", "max_pages": 2},
    {"name": "Мечта: 💨 Утюги и отпариватели", "url": "https://www.mechta.kz/section/utyugi/", "max_pages": 2}
]

# 7. Alser (alser.kz)
ALSER_CATEGORIES = [
    {"name": "Alser: 💻 Ноутбуки", "url": "https://alser.kz/astana/c/noutbuki", "max_pages": 3},
    {"name": "Alser: 📱 Смартфоны", "url": "https://alser.kz/astana/c/smartfony", "max_pages": 3},
    {"name": "Alser: 📺 Телевизоры", "url": "https://alser.kz/astana/c/televizory", "max_pages": 3},
    {"name": "Alser: 🖥 Мониторы", "url": "https://alser.kz/astana/c/monitory", "max_pages": 3},
    {"name": "Alser: 📱 Планшеты", "url": "https://alser.kz/astana/c/planshety", "max_pages": 2},
    {"name": "Alser: 🧹 Пылесосы", "url": "https://alser.kz/astana/c/pylesosy", "max_pages": 2},
    {"name": "Alser: ❄️ Холодильники", "url": "https://alser.kz/astana/c/vse-holodilniki", "max_pages": 2},
    {"name": "Alser: 🌬 Кондиционеры", "url": "https://alser.kz/astana/c/vse-kondicioneri", "max_pages": 2}
]

# 8. Эврика (evrika.com)
EVRIKA_CATEGORIES = [
    {"name": "Эврика: 💻 Ноутбуки", "url": "https://evrika.com/catalog/nur-sultan-astana/noutbuki/c207", "max_pages": 3},
    {"name": "Эврика: 📱 Смартфоны", "url": "https://evrika.com/catalog/nur-sultan-astana/smartfony/c234", "max_pages": 3},
    {"name": "Эврика: 📺 Телевизоры", "url": "https://evrika.com/catalog/nur-sultan-astana/led-televizory/c228", "max_pages": 3},
    {"name": "Эврика: 🖥 Мониторы", "url": "https://evrika.com/catalog/nur-sultan-astana/monitory/c300", "max_pages": 3},
    {"name": "Эврика: 📱 Планшеты", "url": "https://evrika.com/catalog/nur-sultan-astana/planshety/c70", "max_pages": 2},
    {"name": "Эврика: 🎧 Наушники", "url": "https://evrika.com/catalog/nur-sultan-astana/naushniki-1/c183", "max_pages": 3},
    {"name": "Эврика: 🎮 Игровые приставки", "url": "https://evrika.com/catalog/nur-sultan-astana/igrovye-pristavki/c120", "max_pages": 2},
    {"name": "Эврика: 🖨 Принтеры", "url": "https://evrika.com/catalog/nur-sultan-astana/printery/c65", "max_pages": 2},
    {"name": "Эврика: 💨 Утюги", "url": "https://evrika.com/catalog/nur-sultan-astana/utyugi/c161", "max_pages": 2}
]

# 9. Moon.kz (moon.kz)
MOON_CATEGORIES = [
    {"name": "Moon: 💻 Ноутбуки", "url": "https://moon.kz/noutbuki-i-aksessuary/", "max_pages": 3},
    {"name": "Moon: 🎮 Видеокарты", "url": "https://moon.kz/videokarty/", "max_pages": 3},
    {"name": "Moon: ⚙️ Процессоры", "url": "https://moon.kz/protsessory/", "max_pages": 3},
    {"name": "Moon: 🖥 Мониторы", "url": "https://moon.kz/monitory/", "max_pages": 3},
    {"name": "Moon: 🧩 Материнские платы", "url": "https://moon.kz/materinskie-platy/", "max_pages": 3},
    {"name": "Moon: 🧠 Оперативная память", "url": "https://moon.kz/moduli-pamyati/", "max_pages": 3},
    {"name": "Moon: 💾 SSD диски", "url": "https://moon.kz/nakopiteli-ssd/", "max_pages": 3},
    {"name": "Moon: ⚡️ Блоки питания", "url": "https://moon.kz/bloki-pitaniya/", "max_pages": 3},
    {"name": "Moon: 📦 Корпуса", "url": "https://moon.kz/korpusa/", "max_pages": 3},
    {"name": "Moon: ❄️ Системы охлаждения", "url": "https://moon.kz/kulery-i-sistemy-okhlazhdeniya/", "max_pages": 3},
    {"name": "Moon: 🔥 Распродажа", "url": "https://moon.kz/rasprodazha/", "max_pages": 3}
]

# 10. Kaspi Магазин (kaspi.kz)
KASPI_CATEGORIES = [
    {"name": "Kaspi: ⌚️ Смарт-часы", "url": "https://kaspi.kz/shop/c/smart%20watches/", "max_pages": 2},
    {"name": "Kaspi: 📱 Смартфоны", "url": "https://kaspi.kz/shop/c/smartphones/", "max_pages": 2},
    {"name": "Kaspi: 💻 Ноутбуки", "url": "https://kaspi.kz/shop/c/laptops/", "max_pages": 2},
    {"name": "Kaspi: 🎧 Наушники", "url": "https://kaspi.kz/shop/c/headphones/", "max_pages": 2},
    {"name": "Kaspi: 🎮 Видеокарты", "url": "https://kaspi.kz/shop/c/video%20cards/", "max_pages": 2},
    {"name": "Kaspi: 📱 Планшеты", "url": "https://kaspi.kz/shop/c/tablets/", "max_pages": 2},
    {"name": "Kaspi: 🖥 Мониторы", "url": "https://kaspi.kz/shop/c/monitors/", "max_pages": 2},
    {"name": "Kaspi: 🎮 Игровые консоли", "url": "https://kaspi.kz/shop/c/game%20consoles/", "max_pages": 2}
]

# 11. 4mobile (4mobile.pages.dev)
FOURMOBILE_CATEGORIES = [
    {"name": "4mobile: 📱 iPhone (eSIM & Nano-SIM)", "url": "https://4mobile.pages.dev/api/data", "max_pages": 1},
    {"name": "4mobile: ⌚️ Apple Watch & Garmin", "url": "https://4mobile.pages.dev/api/data", "max_pages": 1},
    {"name": "4mobile: 🎧 AirPods & Наушники WiWU", "url": "https://4mobile.pages.dev/api/data", "max_pages": 1},
    {"name": "4mobile: 💻 MacBook & iPad", "url": "https://4mobile.pages.dev/api/data", "max_pages": 1},
    {"name": "4mobile: 💨 Dyson & Яндекс Станции", "url": "https://4mobile.pages.dev/api/data", "max_pages": 1},
    {"name": "4mobile: 🎮 PS5, Игры & Дроны DJI", "url": "https://4mobile.pages.dev/api/data", "max_pages": 1},
    {"name": "4mobile: ⚡️ Аксессуары Apple & WiWU", "url": "https://4mobile.pages.dev/api/data", "max_pages": 1}
]




