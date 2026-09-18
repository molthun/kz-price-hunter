import os
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "prices.db"
SETTINGS_FILE = DATA_DIR / "settings.json"
APP_URL = os.getenv("APP_URL", "https://shop.molthun.ru")

# Магазины: ключ настроек -> название магазина в базе (SHOP_NAME скраперов)
SHOP_KEYS = {
    "kaspi": "Kaspi Магазин",
    "dns": "DNS Казахстан",
    "shopkz": "Белый Ветер",
    "technodom": "Технодом",
    "forcecom": "Forcecom",
    "sulpak": "Sulpak",
    "mechta": "Мечта",
    "alser": "Alser",
    "evrika": "Эврика",
    "moon": "Moon.kz",
    "fourmobile": "4mobile",
    "flip": "Flip.kz",
    "halyk": "Halyk Market",
}

def _all_shops_enabled():
    return {key: True for key in SHOP_KEYS}

# ===== Общие (системные) настройки — меняет только администратор, хранятся в settings.json =====
SYSTEM_DEFAULTS = {
    # Сканирование (авто-обновление, если база старше интервала)
    "check_interval_seconds": 300,
    "scan_interval_minutes": 180,
    "enabled_shops": _all_shops_enabled(),

    # Мягкие пороги записи «кандидатов» в аномалии. Сканер сохраняет всё, что их проходит,
    # а лента и уведомления каждого пользователя фильтруются уже по его личным порогам.
    "candidate_min_item_price_kzt": 10000,
    "candidate_drop_pct": 30.0,
    "candidate_min_savings_kzt": 10000,
    "candidate_arbitrage_drop_pct": 15.0,
    "candidate_arbitrage_diff_kzt": 10000,
}

# ===== Личные настройки пользователя (у гостей — значения по умолчанию) =====
USER_DEFAULTS = {
    # Детекция аномалий
    "detect_zero_glitch": True,
    "detect_super_discount": True,
    "detect_market_arbitrage": True,
    "min_item_price_kzt": 30000,
    "max_item_price_kzt": 3000000,
    "price_glitch_drop_pct": 65.0,
    "min_savings_kzt": 40000,
    "arbitrage_min_drop_pct": 25.0,
    "arbitrage_min_diff_kzt": 25000,

    # Фильтры хлама / стоп-слова
    "junk_keywords": [
        "чехол", "пленка", "плёнка", "стекло", "кабель", "переходник",
        "ремешок", "держатель", "подставка", "амбушюры", "накладка", "салфетки"
    ],
    "exclude_used_goods": True,

    # Поиск по умолчанию
    "search_exclude_accessories_default": True,
    "search_default_sort": "price_asc",

    # Магазины в ленте алертов и уведомлениях
    "alert_shops": _all_shops_enabled(),

    # Telegram-уведомления (чат = Telegram-аккаунт пользователя)
    "telegram_notify_enabled": False,
    "telegram_notify_level": "ALL",  # ALL, CRITICAL_ONLY, HIGH_SAVINGS
}

ENUM_VALUES = {
    "search_default_sort": ("price_asc", "price_desc", "savings_desc"),
    "telegram_notify_level": ("ALL", "CRITICAL_ONLY", "HIGH_SAVINGS"),
}

# Обратная совместимость: объединенные значения по умолчанию
DEFAULT_SETTINGS = {**SYSTEM_DEFAULTS, **USER_DEFAULTS}

# Администраторы (Telegram ID через запятую) и токен бота — только из окружения
ADMIN_TELEGRAM_IDS = {
    int(x) for x in os.getenv("ADMIN_TELEGRAM_IDS", "").replace(" ", "").split(",") if x.isdigit()
}
ALLOW_DEV_LOGIN = os.getenv("ALLOW_DEV_LOGIN", "") == "1"

def _read_settings_file():
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            pass
    return {}

def _merge(defaults, data):
    merged = {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v) for k, v in defaults.items()}
    for key, value in data.items():
        if key not in defaults:
            continue
        if isinstance(defaults[key], dict) and isinstance(value, dict):
            merged[key].update({k: bool(v) for k, v in value.items() if k in defaults[key]})
        else:
            merged[key] = value
    return merged

def get_bot_token():
    """Токен Telegram-бота: переменная окружения, для совместимости — старое поле settings.json."""
    return (
        os.getenv("TELEGRAM_BOT_TOKEN")
        or os.getenv("DNS_BOT_TOKEN")
        or str(_read_settings_file().get("telegram_bot_token", "")).strip()
    )

def load_settings():
    """Общие (системные) настройки."""
    return _merge(SYSTEM_DEFAULTS, _read_settings_file())

def legacy_user_settings():
    """Личные настройки из старого однопользовательского settings.json — переносятся администраторам при первом входе."""
    return _merge(USER_DEFAULTS, _read_settings_file())

def merge_user_settings(data):
    return _merge(USER_DEFAULTS, data or {})

def get_candidate_settings(system=None):
    """Настройки детектора для записи кандидатов: мягкие системные пороги, все типы включены."""
    s = system if system is not None else load_settings()
    return {
        **USER_DEFAULTS,
        "detect_zero_glitch": True,
        "detect_super_discount": True,
        "detect_market_arbitrage": True,
        "min_item_price_kzt": s["candidate_min_item_price_kzt"],
        "max_item_price_kzt": 10**12,
        "price_glitch_drop_pct": s["candidate_drop_pct"],
        "min_savings_kzt": s["candidate_min_savings_kzt"],
        "arbitrage_min_drop_pct": s["candidate_arbitrage_drop_pct"],
        "arbitrage_min_diff_kzt": s["candidate_arbitrage_diff_kzt"],
        # Встроенные стоп-слова аксессуаров применяются всегда; уценку каждый фильтрует сам
        "junk_keywords": [],
        "exclude_used_goods": False,
    }

# Допустимый диапазон интервала автообновления базы
SCAN_INTERVAL_MIN_MINUTES = 5
SCAN_INTERVAL_MAX_MINUTES = 30 * 24 * 60  # 30 дней

def get_scan_interval_seconds(settings=None):
    """Порог устаревания базы (в секундах), после которого запускается автообновление."""
    s = settings if settings is not None else load_settings()
    try:
        minutes = int(s.get("scan_interval_minutes", SYSTEM_DEFAULTS["scan_interval_minutes"]))
    except (TypeError, ValueError):
        minutes = SYSTEM_DEFAULTS["scan_interval_minutes"]
    minutes = min(max(minutes, SCAN_INTERVAL_MIN_MINUTES), SCAN_INTERVAL_MAX_MINUTES)
    return minutes * 60

def _validate(new_settings, defaults):
    """Оставляет только известные ключи и приводит значения к типам из defaults."""
    if not isinstance(new_settings, dict):
        raise ValueError("Настройки должны быть JSON-объектом")

    clean = {}
    for key, value in new_settings.items():
        if key not in defaults:
            continue
        default = defaults[key]
        try:
            if isinstance(default, bool):
                if not isinstance(value, bool):
                    raise ValueError
                clean[key] = value
            elif isinstance(default, (int, float)):
                if isinstance(value, bool):
                    raise ValueError
                num = float(value)
                if num < 0:
                    raise ValueError
                clean[key] = int(num) if isinstance(default, int) else num
            elif isinstance(default, list):
                if not isinstance(value, list):
                    raise ValueError
                clean[key] = [str(v).strip()[:100] for v in value if str(v).strip()][:200]
            elif isinstance(default, dict):
                if not isinstance(value, dict):
                    raise ValueError
                clean[key] = {k: bool(v) for k, v in value.items() if k in default}
            else:
                value = str(value).strip()
                if key in ENUM_VALUES and value not in ENUM_VALUES[key]:
                    raise ValueError
                clean[key] = value
        except (TypeError, ValueError):
            raise ValueError(f"Некорректное значение настройки «{key}»: {value!r}")
    return clean

def _validate_settings(new_settings):
    clean = _validate(new_settings, SYSTEM_DEFAULTS)
    interval = clean.get("scan_interval_minutes")
    if interval is not None and not (SCAN_INTERVAL_MIN_MINUTES <= interval <= SCAN_INTERVAL_MAX_MINUTES):
        raise ValueError(f"Интервал автообновления должен быть от {SCAN_INTERVAL_MIN_MINUTES} минут до 30 дней")
    return clean

def validate_user_settings(new_settings):
    return _validate(new_settings, USER_DEFAULTS)

def save_settings(new_settings):
    """Сохраняет общие настройки. Посторонние ключи старого формата в файле сохраняются как есть."""
    raw = _read_settings_file()
    raw.update(_validate_settings(new_settings))
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(raw, f, indent=2, ensure_ascii=False)
    return load_settings()

CHECK_INTERVAL_SECONDS = load_settings()["check_interval_seconds"]

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
    # Официальная YML-выгрузка содержит весь каталог в наличии (~14 000 товаров) и грузится за пару секунд,
    # поэтому обход HTML-категорий Белого Ветра не нужен.
    {"name": "Белый Ветер: 📦 Официальная YML выгрузка", "url": "https://shop.kz/bitrix/catalog_export/yandex.php", "max_pages": 1},
]

# 3. Технодом (technodom.kz)
TECHNODOM_CATEGORIES = [
    {"name": "Технодом: 💻 Ноутбуки", "url": "https://www.technodom.kz/catalog/noutbuki-i-komp-jutery/noutbuki/noutbuki"},
    {"name": "Технодом: 📱 Смартфоны", "url": "https://www.technodom.kz/catalog/smartfony-i-gadzhety/smartfony-i-telefony/smartfony"},
    {"name": "Технодом: 📺 Телевизоры", "url": "https://www.technodom.kz/catalog/tv-audio-foto-video/televizory/led-televizory"},
    {"name": "Технодом: 📱 Планшеты", "url": "https://www.technodom.kz/catalog/smartfony-i-gadzhety/planshety-i-knigi/planshety"},
    {"name": "Технодом: ⌚️ Смарт-часы", "url": "https://www.technodom.kz/catalog/smartfony-i-gadzhety/gadzhety/smart-chasy"},
    {"name": "Технодом: 🎧 Наушники", "url": "https://www.technodom.kz/catalog/tv-audio-foto-video/audio-tehnika/naushniki"},
    {"name": "Технодом: 🖥 Мониторы", "url": "https://www.technodom.kz/catalog/noutbuki-i-komp-jutery/komp-jutery-i-monobloki/monitory"},
    {"name": "Технодом: 🎮 Игровые приставки", "url": "https://www.technodom.kz/catalog/noutbuki-i-komp-jutery/igrovye-pristavki-i-igry/igrovye-pristavki"},
    {"name": "Технодом: 🧹 Пылесосы", "url": "https://www.technodom.kz/catalog/bytovaja-tehnika/tehnika-dlja-doma/pylesosy"}
]

# 4. Forcecom (forcecom.kz)
FORCECOM_CATEGORIES = [
    {"name": "Forcecom: 🔥 Распродажа", "url": "https://forcecom.kz/sale/rasprodazha/"},
    {"name": "Forcecom: 💻 Ноутбуки", "url": "https://forcecom.kz/catalog/laptops/"},
    {"name": "Forcecom: 🎮 Видеокарты", "url": "https://forcecom.kz/catalog/graphics-cards/"},
    {"name": "Forcecom: 🧩 Материнские платы", "url": "https://forcecom.kz/catalog/motherboards/"},
    {"name": "Forcecom: 🧠 Оперативная память", "url": "https://forcecom.kz/catalog/ram/"},
    {"name": "Forcecom: 🖥 Мониторы", "url": "https://forcecom.kz/catalog/monitors/"},
    {"name": "Forcecom: 💾 SSD диски", "url": "https://forcecom.kz/catalog/ssd/"},
    {"name": "Forcecom: 💾 Жесткие диски", "url": "https://forcecom.kz/catalog/hdd/"},
    {"name": "Forcecom: 📦 Корпуса", "url": "https://forcecom.kz/catalog/cases/"},
    {"name": "Forcecom: 🎧 Наушники", "url": "https://forcecom.kz/catalog/headphones/"},
    {"name": "Forcecom: ⌨️ Клавиатуры", "url": "https://forcecom.kz/catalog/keyboards/"},
    {"name": "Forcecom: 🖱 Мыши", "url": "https://forcecom.kz/catalog/mice/"},
    {"name": "Forcecom: 🖨 Принтеры", "url": "https://forcecom.kz/catalog/printers/"},
    {"name": "Forcecom: 🌐 Роутеры", "url": "https://forcecom.kz/catalog/marshrutizatory/"}
]

# 5. Sulpak (sulpak.kz)
SULPAK_CATEGORIES = [
    {"name": "Sulpak: 💻 Ноутбуки", "url": "https://www.sulpak.kz/f/noutbuki"},
    {"name": "Sulpak: 📱 Смартфоны", "url": "https://www.sulpak.kz/f/smartfoniy/"},
    {"name": "Sulpak: 📺 Телевизоры", "url": "https://www.sulpak.kz/f/led_oled_televizoriy"},
    {"name": "Sulpak: 📱 Планшеты", "url": "https://www.sulpak.kz/f/planshetiy"},
    {"name": "Sulpak: ⌚️ Смарт-часы", "url": "https://www.sulpak.kz/f/smart_chasiy"},
    {"name": "Sulpak: 🎧 Наушники", "url": "https://www.sulpak.kz/f/naushniki"},
    {"name": "Sulpak: 🎮 Игровые приставки", "url": "https://www.sulpak.kz/f/igroviye_pristavki"},
    {"name": "Sulpak: 🧺 Стиральные машины", "url": "https://www.sulpak.kz/f/stiralniye_mashiniy"},
    {"name": "Sulpak: ❄️ Холодильники", "url": "https://www.sulpak.kz/f/holodilniki"},
    {"name": "Sulpak: 🌬 Кондиционеры", "url": "https://www.sulpak.kz/f/kondicioneriy"},
    {"name": "Sulpak: ☕️ Кофемашины", "url": "https://www.sulpak.kz/f/kofemashiniy"},
    {"name": "Sulpak: ♨️ Микроволновые печи", "url": "https://www.sulpak.kz/f/mikrovolnoviye_pechi"},
    {"name": "Sulpak: 🔥 Распродажа", "url": "https://www.sulpak.kz/sale/1"}
]

# 6. Мечта (mechta.kz)
MECHTA_CATEGORIES = [
    {"name": "Мечта: 💻 Ноутбуки", "url": "https://www.mechta.kz/section/noutbuki/"},
    {"name": "Мечта: 📱 Смартфоны", "url": "https://www.mechta.kz/section/smartfony/"},
    {"name": "Мечта: 📺 Телевизоры", "url": "https://www.mechta.kz/section/televizory/"},
    {"name": "Мечта: 🖥 Мониторы", "url": "https://www.mechta.kz/section/monitory/"},
    {"name": "Мечта: 📱 Планшеты", "url": "https://www.mechta.kz/section/planshety/"},
    {"name": "Мечта: ⌚️ Смарт-часы", "url": "https://www.mechta.kz/section/smart-chasy/"},
    {"name": "Мечта: 🎧 Наушники", "url": "https://www.mechta.kz/section/naushniki/"},
    {"name": "Мечта: 🎮 Игровые приставки", "url": "https://www.mechta.kz/section/igrovye-pristavki/"},
    {"name": "Мечта: 🧹 Пылесосы", "url": "https://www.mechta.kz/section/pylesosy/"},
    {"name": "Мечта: ❄️ Холодильники", "url": "https://www.mechta.kz/section/holodilniki/"},
    {"name": "Мечта: 🧺 Стиральные машины", "url": "https://www.mechta.kz/section/stiralnye-mashiny/"},
    {"name": "Мечта: ☕️ Кофемашины", "url": "https://www.mechta.kz/section/kofemashiny/"},
    {"name": "Мечта: 🌬 Кондиционеры", "url": "https://www.mechta.kz/section/kondicionery/"},
    {"name": "Мечта: 💨 Утюги и отпариватели", "url": "https://www.mechta.kz/section/utyugi/"}
]

# 7. Alser (alser.kz)
ALSER_CATEGORIES = [
    {"name": "Alser: 💻 Ноутбуки", "url": "https://alser.kz/astana/c/noutbuki"},
    {"name": "Alser: 📱 Смартфоны", "url": "https://alser.kz/astana/c/smartfony"},
    {"name": "Alser: 📺 Телевизоры", "url": "https://alser.kz/astana/c/televizory"},
    {"name": "Alser: 🖥 Мониторы", "url": "https://alser.kz/astana/c/monitory"},
    {"name": "Alser: 📱 Планшеты", "url": "https://alser.kz/astana/c/planshety"},
    {"name": "Alser: 🧹 Пылесосы", "url": "https://alser.kz/astana/c/pylesosy"},
    {"name": "Alser: ❄️ Холодильники", "url": "https://alser.kz/astana/c/vse-holodilniki"},
    {"name": "Alser: 🌬 Кондиционеры", "url": "https://alser.kz/astana/c/vse-kondicioneri"}
]

# 8. Эврика (evrika.com)
EVRIKA_CATEGORIES = [
    {"name": "Эврика: 💻 Ноутбуки", "url": "https://evrika.com/catalog/nur-sultan-astana/noutbuki/c207"},
    {"name": "Эврика: 📱 Смартфоны", "url": "https://evrika.com/catalog/nur-sultan-astana/smartfony/c234"},
    {"name": "Эврика: 📺 Телевизоры", "url": "https://evrika.com/catalog/nur-sultan-astana/led-televizory/c228"},
    {"name": "Эврика: 🖥 Мониторы", "url": "https://evrika.com/catalog/nur-sultan-astana/monitory/c300"},
    {"name": "Эврика: 📱 Планшеты", "url": "https://evrika.com/catalog/nur-sultan-astana/planshety/c70"},
    {"name": "Эврика: 🎧 Наушники", "url": "https://evrika.com/catalog/nur-sultan-astana/naushniki-1/c183"},
    {"name": "Эврика: 🎮 Игровые приставки", "url": "https://evrika.com/catalog/nur-sultan-astana/igrovye-pristavki/c120"},
    {"name": "Эврика: 🖨 Принтеры", "url": "https://evrika.com/catalog/nur-sultan-astana/printery/c65"},
    {"name": "Эврика: 💨 Утюги", "url": "https://evrika.com/catalog/nur-sultan-astana/utyugi/c161"}
]

# 9. Moon.kz (moon.kz)
MOON_CATEGORIES = [
    {"name": "Moon: 💻 Ноутбуки", "url": "https://moon.kz/noutbuki-i-aksessuary/"},
    {"name": "Moon: 🎮 Видеокарты", "url": "https://moon.kz/videokarty/"},
    {"name": "Moon: ⚙️ Процессоры", "url": "https://moon.kz/protsessory/"},
    {"name": "Moon: 🖥 Мониторы", "url": "https://moon.kz/monitory/"},
    {"name": "Moon: 🧩 Материнские платы", "url": "https://moon.kz/materinskie-platy/"},
    {"name": "Moon: 🧠 Оперативная память", "url": "https://moon.kz/moduli-pamyati/"},
    {"name": "Moon: 💾 SSD диски", "url": "https://moon.kz/nakopiteli-ssd/"},
    {"name": "Moon: ⚡️ Блоки питания", "url": "https://moon.kz/bloki-pitaniya/"},
    {"name": "Moon: 📦 Корпуса", "url": "https://moon.kz/korpusa/"},
    {"name": "Moon: ❄️ Системы охлаждения", "url": "https://moon.kz/kulery-i-sistemy-okhlazhdeniya/"},
    {"name": "Moon: 🔥 Распродажа", "url": "https://moon.kz/rasprodazha/"}
]

# 10. Kaspi Магазин (kaspi.kz)
KASPI_CATEGORIES = [
    {"name": "Kaspi: 📱 Смартфоны", "url": "https://kaspi.kz/shop/c/smartphones/", "max_pages": 30},
    {"name": "Kaspi: 💻 Ноутбуки", "url": "https://kaspi.kz/shop/c/notebooks/", "max_pages": 30},
    {"name": "Kaspi: ⌚️ Смарт-часы", "url": "https://kaspi.kz/shop/c/smart%20watches/", "max_pages": 30},
    {"name": "Kaspi: 🎧 Наушники", "url": "https://kaspi.kz/shop/c/headphones/", "max_pages": 30},
    {"name": "Kaspi: 📱 Планшеты", "url": "https://kaspi.kz/shop/c/tablets/", "max_pages": 30},
    {"name": "Kaspi: 🖥 Мониторы", "url": "https://kaspi.kz/shop/c/monitors/", "max_pages": 30},
    {"name": "Kaspi: 🎮 Видеокарты", "url": "https://kaspi.kz/shop/c/videocards/", "max_pages": 30},
    {"name": "Kaspi: ⚙️ Процессоры", "url": "https://kaspi.kz/shop/c/cpus/", "max_pages": 30},
    {"name": "Kaspi: 🔌 Материнские платы", "url": "https://kaspi.kz/shop/c/motherboards/", "max_pages": 30},
    {"name": "Kaspi: 🎮 Игровые приставки", "url": "https://kaspi.kz/shop/c/game%20consoles/", "max_pages": 30},
    {"name": "Kaspi: 📺 Телевизоры", "url": "https://kaspi.kz/shop/c/tvs/", "max_pages": 30},
    {"name": "Kaspi: ❄️ Холодильники", "url": "https://kaspi.kz/shop/c/refrigerators/", "max_pages": 30},
    {"name": "Kaspi: 🧺 Стиральные машины", "url": "https://kaspi.kz/shop/c/washers/", "max_pages": 30},
    {"name": "Kaspi: 🧹 Пылесосы", "url": "https://kaspi.kz/shop/c/vacuum%20cleaners/", "max_pages": 30},
    {"name": "Kaspi: 🤖 Роботы-пылесосы", "url": "https://kaspi.kz/shop/c/robot%20vacuum%20cleaners/", "max_pages": 30},
    {"name": "Kaspi: ☕️ Кофемашины", "url": "https://kaspi.kz/shop/c/coffee%20machines%20and%20coffee%20makers/", "max_pages": 30},
    {"name": "Kaspi: 💨 Кондиционеры", "url": "https://kaspi.kz/shop/c/air%20conditioners/", "max_pages": 30},
    {"name": "Kaspi: 🖨 Принтеры и МФУ", "url": "https://kaspi.kz/shop/c/mf%20printers/", "max_pages": 30},
    {"name": "Kaspi: 📽 Проекторы", "url": "https://kaspi.kz/shop/c/video%20projectors/", "max_pages": 30},
]

# 11. 4mobile (4mobile.pages.dev)
FOURMOBILE_CATEGORIES = [
    # The endpoint already contains every group: fetch once and reconcile as one source.
    {"name": "4mobile: Все товары", "url": "https://4mobile.pages.dev/api/data"}
]





# Public catalog; regional stock is not confirmed, so offers appear under All cities.
FLIP_CATEGORIES = [
    {"name": "Flip: Электроника", "url": "https://www.flip.kz/catalog?subsection=5319", "max_pages": 50},
]

# Halyk Market catalog (Almaty location=-2)
HALYK_CATEGORIES = [
    {"name": "Halyk: Смартфоны", "url": "https://halykmarket.kz/category/smartfony", "max_pages": 50},
    {"name": "Halyk: Ноутбуки", "url": "https://halykmarket.kz/category/noutbuki", "max_pages": 50},
    {"name": "Halyk: Телевизоры", "url": "https://halykmarket.kz/category/televizori", "max_pages": 50},
    {"name": "Halyk: Наушники", "url": "https://halykmarket.kz/category/naushniki", "max_pages": 50},
    {"name": "Halyk: Планшеты", "url": "https://halykmarket.kz/category/plansheti", "max_pages": 50},
    {"name": "Halyk: Смарт-часы", "url": "https://halykmarket.kz/category/smart-chasi", "max_pages": 50},
    {"name": "Halyk: Игровые приставки", "url": "https://halykmarket.kz/category/igrovie-pristavki", "max_pages": 50},
    {"name": "Halyk: Мониторы", "url": "https://halykmarket.kz/category/monitori", "max_pages": 50},
]
