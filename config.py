import os
import json
import tempfile
import threading
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "prices.db"
SETTINGS_FILE = DATA_DIR / "settings.json"
# Адрес панели для кнопки в Telegram-уведомлениях; задается на сервере, в репозитории не хранится
APP_URL = os.getenv("APP_URL", "").strip()

# Настройки AI (Google Gemini API / OpenAI API)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
# Пустая переменная — берётся адрес из настроек админки (раньше непустой default всегда перекрывал его)
OPENAI_API_BASE = os.getenv("OPENAI_API_BASE", "").strip()

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
    "tgrad": "Tgrad",
    "ants": "ANTS",
    "itmag": "ITMag",
    "ispace": "iSpace",
    "fortemarket": "Forte Market",
    "vkusmart": "Вкусмарт",
    "twelve_months": "12 Месяцев",
    "zeta": "Zeta",
    "komfort": "Комфорт",
    "lemanapro": "Лемана ПРО",
    "arbuz": "Arbuz",
    "masterok": "MasterOK",
    "magnum": "Магнум",
    "intertop": "Интертоп",
    "marwin": "Меломан",
    "iteka": "i-Teka",
    "mebel": "Mebel.kz",
    "detmir": "Детский мир",
    "askona": "Askona",
    "zoomarket": "Зоомаркет",
}


MASTER_CATEGORIES = {
    "smartphones": {
        "id": "smartphones",
        "name": "Смартфоны",
        "icon": "📱",
        "description": "Смартфоны, мобильные телефоны и аксессуары связи"
    },
    "laptops": {
        "id": "laptops",
        "name": "Ноутбуки и ПК",
        "icon": "💻",
        "description": "Ноутбуки, ультрабуки, моноблоки и системные блоки"
    },
    "pc_components": {
        "id": "pc_components",
        "name": "ПК Комплектующие",
        "icon": "⚙️",
        "description": "Видеокарты, процессоры, материнские платы, ОЗУ, SSD, БП, корпуса"
    },
    "monitors": {
        "id": "monitors",
        "name": "Мониторы",
        "icon": "🖥",
        "description": "Компьютерные мониторы, игровые дисплеи"
    },
    "tvs": {
        "id": "tvs",
        "name": "Телевизоры и проекторы",
        "icon": "📺",
        "description": "Телевизоры, LED/OLED панели, проекторы"
    },
    "audio": {
        "id": "audio",
        "name": "Наушники и Аудио",
        "icon": "🎧",
        "description": "Беспроводные и проводные наушники, гарнитуры, колонки, акустика"
    },
    "tablets_watches": {
        "id": "tablets_watches",
        "name": "Планшеты и смарт-часы",
        "icon": "⌚️",
        "description": "Планшеты, электронные книги, умные часы и фитнес-браслеты"
    },
    "consoles": {
        "id": "consoles",
        "name": "Игровые приставки",
        "icon": "🎮",
        "description": "PlayStation, Xbox, Nintendo Switch, геймпады"
    },
    "appliances_large": {
        "id": "appliances_large",
        "name": "Крупная бытовая техника",
        "icon": "❄️",
        "description": "Холодильники, стиральные и сушильные машины, кондиционеры, плиты"
    },
    "appliances_small": {
        "id": "appliances_small",
        "name": "Мелкая бытовая техника",
        "icon": "🧹",
        "description": "Пылесосы, роботы-пылесосы, кофемашины, СВЧ, утюги, блендеры, фены"
    },
    "office_network": {
        "id": "office_network",
        "name": "Оргтехника и Сеть",
        "icon": "🖨",
        "description": "Принтеры, МФУ, сканеры, Wi-Fi роутеры и маршрутизаторы"
    },
    "actions": {
        "id": "actions",
        "name": "Акции и распродажи",
        "icon": "🔥",
        "description": "Специальные разделы распродаж и скидок магазинов"
    },
    "grocery": {
        "id": "grocery",
        "name": "Продукты и бакалея",
        "icon": "🥫",
        "description": "Продукты питания, напитки, чай, кофе, сладости"
    },
    "household": {
        "id": "household",
        "name": "Бытовые товары и химия",
        "icon": "🧼",
        "description": "Бытовая химия, стирка, уборка, гигиена, хозтовары"
    },
    "diy": {
        "id": "diy",
        "name": "Стройка и ремонт",
        "icon": "🛠",
        "description": "Электроинструменты, ручной инструмент, сантехника, стройматериалы, садовая техника"
    },
    "clothes": {
        "id": "clothes",
        "name": "Одежда, обувь и аксессуары",
        "icon": "👕",
        "description": "Мужская, женская и детская одежда, обувь, белье, чулочно-носочные изделия"
    },
    "pets": {
        "id": "pets",
        "name": "Зоотовары",
        "icon": "🐾",
        "description": "Корма, наполнители туалетов, лакомства и аксессуары для животных"
    },
    "home_furniture": {
        "id": "home_furniture",
        "name": "Дом, мебель и уют",
        "icon": "🛋",
        "description": "Мебель, посуда, емкости для хранения, текстиль и декор"
    },
    "beauty_health": {
        "id": "beauty_health",
        "name": "Красота и здоровье",
        "icon": "💄",
        "description": "Косметика, парфюмерия, уход за кожей и волосами, гигиена"
    },
}

def _all_shops_enabled():
    return {key: True for key in SHOP_KEYS}

def _all_categories_enabled():
    return {key: True for key in MASTER_CATEGORIES}

DEFAULT_HOT_CATEGORIES = ["actions"]
CYCLE_BUDGET_HOURS = 24
CYCLE_BUDGET_SECONDS = 24 * 3600  # 86400 секунд в сутках

def get_wave_plan(enabled_categories=None, hot_categories=None, wave_index=0, wave_size=2, wave_mode="rolling"):
    """Рассчитывает состав текущей волны сканирования: Hot-категории + порция второстепенных категорий.
    
    Гарантирует, что полный круг ротации всех не-Hot категорий укладывается в 24 часа.
    Hot-категории включаются в каждую волну без ожидания очереди.
    """
    if enabled_categories is None:
        enabled_set = set(MASTER_CATEGORIES.keys())
    elif isinstance(enabled_categories, dict):
        enabled_set = {k for k, v in enabled_categories.items() if v and k in MASTER_CATEGORIES}
    else:
        enabled_set = set(enabled_categories) & set(MASTER_CATEGORIES.keys())

    if hot_categories is None:
        hot_list = list(DEFAULT_HOT_CATEGORIES)
    else:
        hot_list = [c for c in hot_categories if c in MASTER_CATEGORIES]

    active_hot = [c for c in hot_list if c in enabled_set]

    if wave_mode == "all":
        enabled_list = [c for c in MASTER_CATEGORIES if c in enabled_set]
        return {
            "active_categories": enabled_list,
            "hot_categories": active_hot,
            "wave_categories": enabled_list,
            "wave_index": 0,
            "total_waves": 1,
            "next_wave_index": 0,
            "next_wave_categories": enabled_list,
            "cycle_budget_hours": CYCLE_BUDGET_HOURS,
            "wave_interval_minutes": CYCLE_BUDGET_HOURS * 60,
            "wave_interval_seconds": CYCLE_BUDGET_SECONDS,
            "estimated_cycle_hours": CYCLE_BUDGET_HOURS,
            "is_last_wave_of_cycle": True,
        }

    rotating = [c for c in MASTER_CATEGORIES if c in enabled_set and c not in active_hot]

    if not rotating:
        return {
            "active_categories": active_hot,
            "hot_categories": active_hot,
            "wave_categories": [],
            "wave_index": 0,
            "total_waves": 1,
            "next_wave_index": 0,
            "next_wave_categories": [],
            "cycle_budget_hours": CYCLE_BUDGET_HOURS,
            "wave_interval_minutes": CYCLE_BUDGET_HOURS * 60,
            "wave_interval_seconds": CYCLE_BUDGET_SECONDS,
            "estimated_cycle_hours": CYCLE_BUDGET_HOURS,
            "is_last_wave_of_cycle": True,
        }

    wave_size = max(1, wave_size)
    total_waves = -(-len(rotating) // wave_size)
    safe_wave_idx = wave_index % total_waves

    start = safe_wave_idx * wave_size
    current_wave_cats = rotating[start : start + wave_size]

    next_wave_idx = (safe_wave_idx + 1) % total_waves
    next_start = next_wave_idx * wave_size
    next_wave_cats = rotating[next_start : next_start + wave_size]

    active_combined = list(dict.fromkeys(active_hot + current_wave_cats))
    is_last_wave_of_cycle = (safe_wave_idx == total_waves - 1)
    wave_interval_sec = CYCLE_BUDGET_SECONDS // total_waves
    wave_interval_minutes = wave_interval_sec // 60
    estimated_cycle_hours = round((wave_interval_sec * total_waves) / 3600, 1)

    return {
        "active_categories": active_combined,
        "hot_categories": active_hot,
        "wave_categories": current_wave_cats,
        "wave_index": safe_wave_idx,
        "total_waves": total_waves,
        "next_wave_index": next_wave_idx,
        "next_wave_categories": next_wave_cats,
        "cycle_budget_hours": CYCLE_BUDGET_HOURS,
        "wave_interval_minutes": wave_interval_minutes,
        "wave_interval_seconds": wave_interval_sec,
        "estimated_cycle_hours": estimated_cycle_hours,
        "is_last_wave_of_cycle": is_last_wave_of_cycle,
    }

# ===== Общие (системные) настройки — меняет только администратор, хранятся в settings.json =====
SYSTEM_DEFAULTS = {
    # Сканирование (авто-обновление, если база старше интервала)
    "check_interval_seconds": 300,
    "scan_interval_minutes": 180,
    "enabled_shops": _all_shops_enabled(),

    # Управление категориями и волнами
    "enabled_categories": _all_categories_enabled(),
    "hot_categories": list(DEFAULT_HOT_CATEGORIES),
    "wave_size": 2,
    "wave_mode": "rolling",  # "rolling" (Hot + ротация волн) или "all" (все включенные каждый цикл)

    # Мягкие пороги записи «кандидатов» в аномалии. Сканер сохраняет всё, что их проходит,
    # а лента и уведомления каждого пользователя фильтруются уже по его личным порогам.
    "candidate_min_item_price_kzt": 10000,
    "candidate_drop_pct": 30.0,
    "candidate_min_savings_kzt": 10000,
    "candidate_arbitrage_drop_pct": 15.0,
    "candidate_arbitrage_diff_kzt": 10000,

    # AI интеграция
    "gemini_api_key": "",
    "openai_api_key": "",
    "openai_api_base": "https://api.openai.com/v1",
    "ai_search_enabled": True,
    "ai_provider": "auto",
    "gemini_model_mode": "auto",
    "gemini_model": "gemini-3.5-flash-lite",
    "openai_model_mode": "auto",
    "openai_model": "gpt-4o-mini",
    # Шаг включения адаптивного планировщика (P11). По умолчанию выключено: обходы идут прежним порядком,
    # расширение — только вручную владельцем, откат — автоматический по согласованным порогам.
    "adaptive_scheduler_stage": "off",
    # Цены моделей задаёт администратор: {"gemini-3.5-flash-lite": {"input": 0.3, "output": 2.5}} — доллары за
    # миллион токенов. Пустое значение означает «цена неизвестна», и расход в деньгах не показывается:
    # тарифы меняются, и зашитая в код цифра вводила бы в заблуждение (P08).
    "ai_model_prices": {},
    # Суточная сводка администратору в Telegram (P13). По умолчанию выключена: план прямо отложил
    # отправку как отдельное последующее включение, и владелец включает её сам.
    # AI-часть сопоставления каталога (P09). off — модель не вызывается, shadow — вызывается и
    # записывает решения, on — принятое решение разрешает сравнение цен. По умолчанию off.
    "ai_matching_mode": "off",
    "daily_digest_telegram_enabled": False,
    "daily_digest_hour": 10,
    "daily_digest_timezone": "Asia/Almaty",
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
    "wave_mode": ("rolling", "all"),
    # Шаг включения адаптивного планировщика (P11): перечисление держится здесь, чтобы проверка значений
    # и общий тест системных параметров знали о нём наравне с остальными
    "adaptive_scheduler_stage": ("off", "canary_one", "canary_few", "all"),
    # Режим AI-части сопоставления (P09): сначала тень, включение — решение владельца
    "ai_matching_mode": ("off", "shadow", "on"),
}

# Обратная совместимость: объединенные значения по умолчанию
DEFAULT_SETTINGS = {**SYSTEM_DEFAULTS, **USER_DEFAULTS}

# Администраторы (Telegram ID через запятую) и токен бота — только из окружения
ADMIN_TELEGRAM_IDS = {
    int(x) for x in os.getenv("ADMIN_TELEGRAM_IDS", "").replace(" ", "").split(",") if x.isdigit()
}
# Local developer login is explicitly opt-in and never impersonates Telegram IDs.
ALLOW_DEV_LOGIN = os.getenv("ALLOW_DEV_LOGIN", "0").strip() == "1"
DEV_ADMIN_ID = -1  # Reserved local principal; Telegram user IDs are positive.

# Empty means no forwarded headers are trusted. Use exact proxy IPs where possible.
TRUSTED_PROXIES = tuple(x.strip() for x in os.getenv("TRUSTED_PROXIES", "").split(",") if x.strip())
# APP_URL remains a compatible default for existing single-origin deployments.
PUBLIC_ORIGIN = (os.getenv("PUBLIC_ORIGIN", "").strip() or APP_URL).rstrip("/")

def _read_settings_file():
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            pass
    return {}

# Настройки-словари, у которых ключи задаёт владелец, а не код: сюда нельзя применять правило
# «оставить только ключи по умолчанию», иначе сохранённое значение исчезает при чтении.
FREEFORM_DICT_SETTINGS = ("ai_model_prices",)


def _merge(defaults, data):
    merged = {k: (dict(v) if isinstance(v, dict) else list(v) if isinstance(v, list) else v) for k, v in defaults.items()}
    for key, value in data.items():
        if key not in defaults:
            continue
        if key in FREEFORM_DICT_SETTINGS and isinstance(value, dict):
            # Карта «модель → цены»: имена моделей знает владелец, а не значения по умолчанию.
            # Прежнее правило вырезало их все, и заданные цены не доходили до расчёта расхода.
            merged[key] = {str(k): v for k, v in value.items()}
        elif isinstance(defaults[key], dict) and isinstance(value, dict):
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

def get_ai_config():
    """Параметры AI-сервиса (Gemini/OpenAI): переменные окружения имеют приоритет над settings.json."""
    sys_settings = load_settings()
    gemini_key = GEMINI_API_KEY or str(sys_settings.get("gemini_api_key", "")).strip()
    openai_key = OPENAI_API_KEY or str(sys_settings.get("openai_api_key", "")).strip()
    openai_base = OPENAI_API_BASE or str(sys_settings.get("openai_api_base", "https://api.openai.com/v1")).strip()
    ai_enabled = bool(sys_settings.get("ai_search_enabled", True))
    selection = sys_settings.get("ai_provider", "auto")
    available = bool(gemini_key if selection == "gemini" else openai_key if selection == "openai" else gemini_key or openai_key)
    return {
        "ai_provider": selection,
        "gemini_model_mode": sys_settings.get("gemini_model_mode", "auto"),
        "openai_model_mode": sys_settings.get("openai_model_mode", "auto"),
        "gemini_model": sys_settings.get("gemini_model", "gemini-3.5-flash-lite") if sys_settings.get("gemini_model_mode") == "manual" else "gemini-3.5-flash-lite",
        "openai_model": sys_settings.get("openai_model", "gpt-4o-mini") if sys_settings.get("openai_model_mode") == "manual" else "gpt-4o-mini",
        "gemini_api_key": gemini_key,
        "openai_api_key": openai_key,
        "openai_api_base": openai_base,
        "ai_search_enabled": ai_enabled,
        "has_ai": available,
        "configured": available,
        "provider": selection if selection != "auto" else ("gemini" if gemini_key else ("openai" if openai_key else "none")),
        "enabled": ai_enabled
    }

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

def get_wave_interval_seconds(settings=None, total_waves=None):
    """Рассчитывает интервал между волнами так, чтобы полный круг гарантированно укладывался в 24 часа.

    Если в настройках задан scan_interval_minutes, интервал волны берется как минимум
    между пользовательским интервалом и максимально допустимым шагом (24ч / total_waves).
    """
    s = settings if settings is not None else load_settings()
    user_interval = get_scan_interval_seconds(s)

    if total_waves is None:
        plan = get_wave_plan(
            enabled_categories=s.get("enabled_categories"),
            hot_categories=s.get("hot_categories", DEFAULT_HOT_CATEGORIES),
            wave_size=s.get("wave_size", 2),
            wave_mode=s.get("wave_mode", "rolling")
        )
        total_waves = plan.get("total_waves", 1)

    waves = max(1, int(total_waves))
    # Лимит 24 часов на полный круг всех волн (86400 секунд)
    max_interval_sec = CYCLE_BUDGET_SECONDS // waves
    return max(SCAN_INTERVAL_MIN_MINUTES * 60, min(user_interval, max_interval_sec))


# Допустимые диапазоны числовых настроек (P05): (минимум, максимум, подпись для сообщения об ошибке)
_KZT_MAX = 100_000_000
SETTING_RANGES = {
    "min_item_price_kzt": (0, _KZT_MAX, "Мин. цена товара"),
    "max_item_price_kzt": (1, _KZT_MAX, "Макс. цена товара"),
    "price_glitch_drop_pct": (1, 99, "Мин. скидка, %"),
    "min_savings_kzt": (0, _KZT_MAX, "Мин. выгода"),
    "arbitrage_min_drop_pct": (1, 99, "Арбитраж: разница, %"),
    "arbitrage_min_diff_kzt": (0, _KZT_MAX, "Арбитраж: выгода"),
    "candidate_min_item_price_kzt": (0, _KZT_MAX, "Порог кандидатов: мин. цена"),
    "candidate_drop_pct": (1, 99, "Порог кандидатов: скидка, %"),
    "candidate_min_savings_kzt": (0, _KZT_MAX, "Порог кандидатов: выгода"),
    "candidate_arbitrage_drop_pct": (1, 99, "Порог кандидатов: арбитраж, %"),
    "candidate_arbitrage_diff_kzt": (0, _KZT_MAX, "Порог кандидатов: арбитраж, ₸"),
    "check_interval_seconds": (30, 86400, "Интервал проверки, с"),
    "daily_digest_hour": (0, 23, "Час суточной сводки"),
}


def _check_range(key, num):
    if key in SETTING_RANGES:
        low, high, label = SETTING_RANGES[key]
        if not (low <= num <= high):
            fmt = lambda n: f"{n:,}".replace(",", " ")
            raise ValueError(f"{label}: допустимо от {fmt(low)} до {fmt(high)}")


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
                if num < 0 or num != num or num in (float("inf"), float("-inf")):
                    raise ValueError
                _check_range(key, num)
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
        except (TypeError, ValueError) as e:
            if str(e) and key in SETTING_RANGES and SETTING_RANGES[key][2] in str(e):
                raise
            raise ValueError(f"Некорректное значение настройки «{key}»: {value!r}")
    return clean

def _validate_settings(new_settings):
    clean = _validate(new_settings, SYSTEM_DEFAULTS)
    if "daily_digest_timezone" in clean:
        import timezones
        # None означает «в системе нет базы часовых поясов»: это не повод отвергать правильное имя и
        # блокировать сохранение всех настроек разом (регрессия 5.13.0)
        if timezones.known(clean["daily_digest_timezone"]) is False:
            raise ValueError(f"Неизвестный часовой пояс: {clean['daily_digest_timezone']}")
    for name, choices in (("ai_provider", {"auto", "gemini", "openai"}),
                          ("gemini_model_mode", {"auto", "manual"}),
                          ("openai_model_mode", {"auto", "manual"}),
                          ("adaptive_scheduler_stage", {"off", "canary_one", "canary_few", "all"}),
                          ("ai_matching_mode", {"off", "shadow", "on"})):
        if name in clean and clean[name] not in choices:
            raise ValueError(f"Некорректный режим: {name}")
    merged = {**load_settings(), **clean}
    import re
    for provider in ("gemini", "openai"):
        model = merged[f"{provider}_model"]
        if merged[f"{provider}_model_mode"] == "manual" and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,149}", model):
            raise ValueError(f"Укажите корректное имя модели {provider}")
    interval = clean.get("scan_interval_minutes")
    if interval is not None and not (SCAN_INTERVAL_MIN_MINUTES <= interval <= SCAN_INTERVAL_MAX_MINUTES):
        raise ValueError(f"Интервал автообновления должен быть от {SCAN_INTERVAL_MIN_MINUTES} минут до 30 дней")
    if "hot_categories" in clean:
        clean["hot_categories"] = [c for c in clean["hot_categories"] if c in MASTER_CATEGORIES]
    if "wave_size" in clean:
        clean["wave_size"] = max(1, min(int(clean["wave_size"]), len(MASTER_CATEGORIES)))
    # Цены моделей — это карта «модель → числа», а не карта переключателей, поэтому разбираются отдельно
    if isinstance(new_settings, dict) and "ai_model_prices" in new_settings:
        clean["ai_model_prices"] = _validate_model_prices(new_settings["ai_model_prices"])
    return clean


def _validate_model_prices(raw):
    """Цены моделей: имя модели → {input, output} в долларах за миллион токенов (P08)."""
    if not isinstance(raw, dict):
        raise ValueError("Цены моделей передаются объектом «модель: {input, output}»")
    import re
    clean = {}
    for model, value in raw.items():
        name = str(model).strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,149}", name):
            raise ValueError(f"Некорректное имя модели: {model}")
        if not isinstance(value, dict):
            raise ValueError(f"Цена модели {name}: нужен объект с полями input и output")
        prices = {}
        for field in ("input", "output"):
            try:
                price = float(value.get(field, 0) or 0)
            except (TypeError, ValueError):
                raise ValueError(f"Цена модели {name} ({field}): нужно число")
            if not 0 <= price <= 10000:
                raise ValueError(f"Цена модели {name} ({field}): допустимо от 0 до 10 000 $ за млн токенов")
            prices[field] = price
        clean[name] = prices
    return clean

def validate_user_settings(new_settings, current=None):
    """Личные настройки: типы, диапазоны и согласованность с уже сохранёнными (мин. цена ≤ макс.)."""
    clean = _validate(new_settings, USER_DEFAULTS)
    merged = {**merge_user_settings(current or {}), **clean}
    if merged["min_item_price_kzt"] > merged["max_item_price_kzt"]:
        raise ValueError("Мин. цена товара не может быть больше макс. цены")
    return clean


# Что не трогает сброс личных настроек: включённость уведомлений меняется только явным действием пользователя
USER_RESET_KEEP = ("telegram_notify_enabled",)

_SETTINGS_WRITE_LOCK = threading.Lock()


def _write_settings_file(data) -> None:
    """Атомарная запись: временный файл рядом + os.replace. Сбой посреди записи не оставляет
    обрезанный settings.json; права 0600 — в файле ключи API."""
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".settings-", suffix=".json", dir=str(SETTINGS_FILE.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, SETTINGS_FILE)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def save_settings(new_settings):
    """Сохраняет общие настройки. Посторонние ключи старого формата в файле сохраняются как есть.
    Чтение-изменение-запись под блокировкой: одновременные сохранения не теряют изменения."""
    with _SETTINGS_WRITE_LOCK:
        raw = _read_settings_file()
        raw.update(_validate_settings(new_settings))
        _write_settings_file(raw)
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
# Вторая и дальнейшие страницы DNS закрыты проверкой Cloudflare на бота (403, 19.09.2026).
# Защиту не обходим: берём только первую страницу категории.
DNS_CATEGORIES = [
    {"name": "DNS: 🔥 Все акции и распродажи", "url": "https://www.dns-shop.kz/catalog/actions/", "max_pages": 1, "master": "actions"},
    {"name": "DNS: 💻 Ноутбуки", "url": "https://www.dns-shop.kz/catalog/17a892f816404e77/noutbuki/", "max_pages": 1, "master": "laptops"},
    {"name": "DNS: 📱 Смартфоны", "url": "https://www.dns-shop.kz/catalog/17a8a01d16404e77/smartfony/", "max_pages": 1, "master": "smartphones"},
    {"name": "DNS: 🎮 Видеокарты", "url": "https://www.dns-shop.kz/catalog/17a89aab16404e77/videokarty/", "max_pages": 1, "master": "pc_components"},
    {"name": "DNS: ⚙️ Процессоры", "url": "https://www.dns-shop.kz/catalog/17a899cd16404e77/processory/", "max_pages": 1, "master": "pc_components"},
    {"name": "DNS: 🖥 Мониторы", "url": "https://www.dns-shop.kz/catalog/17a8943716404e77/monitory/", "max_pages": 1, "master": "monitors"},
    {"name": "DNS: 📺 Телевизоры", "url": "https://www.dns-shop.kz/catalog/17a8ae4916404e77/televizory/", "max_pages": 1, "master": "tvs"},
    {"name": "DNS: 📱 Планшеты", "url": "https://www.dns-shop.kz/catalog/17a890dc16404e77/planshety/", "max_pages": 1, "master": "tablets_watches"},
    {"name": "DNS: 💾 SSD накопители", "url": "https://www.dns-shop.kz/catalog/8a9ddbe317404e77/nakopiteli-ssd/", "max_pages": 1, "master": "pc_components"},
    {"name": "DNS: 🧠 Оперативная память", "url": "https://www.dns-shop.kz/catalog/17a89a3916404e77/operativnaya-pamyat-dimm/", "max_pages": 1, "master": "pc_components"},
    {"name": "DNS: 🎧 Наушники и гарнитуры", "url": "https://www.dns-shop.kz/catalog/17a8f3cd16404e77/naushniki-i-garnitury/", "max_pages": 1, "master": "audio"},
    {"name": "DNS: ⌚️ Смарт-часы", "url": "https://www.dns-shop.kz/catalog/17a9e70116404e77/smart-chasy-i-braslety/", "max_pages": 1, "master": "tablets_watches"},
    {"name": "DNS: 🎮 Игровые консоли", "url": "https://www.dns-shop.kz/catalog/17a8a65f16404e77/igrovye-konsoli/", "max_pages": 1, "master": "consoles"}
]

# 2. Белый Ветер (shop.kz)
SHOPKZ_CATEGORIES = [
    # Официальная YML-выгрузка содержит весь каталог в наличии (~14 000 товаров) и грузится за пару секунд,
    # поэтому обход HTML-категорий Белого Ветра не нужен.
    {"name": "Белый Ветер: 📦 Официальная YML выгрузка", "url": "https://shop.kz/bitrix/catalog_export/yandex.php", "max_pages": 1, "master": "all"},
]

# 3. Технодом (technodom.kz)
TECHNODOM_CATEGORIES = [
    {"name": "Технодом: 💻 Ноутбуки", "url": "https://www.technodom.kz/catalog/noutbuki-i-komp-jutery/noutbuki/noutbuki", "master": "laptops"},
    {"name": "Технодом: 📱 Смартфоны", "url": "https://www.technodom.kz/catalog/smartfony-i-gadzhety/smartfony-i-telefony/smartfony", "master": "smartphones"},
    {"name": "Технодом: 📺 Телевизоры", "url": "https://www.technodom.kz/catalog/tv-audio-foto-video/televizory/led-televizory", "master": "tvs"},
    {"name": "Технодом: 📱 Планшеты", "url": "https://www.technodom.kz/catalog/smartfony-i-gadzhety/planshety-i-knigi/planshety", "master": "tablets_watches"},
    {"name": "Технодом: ⌚️ Смарт-часы", "url": "https://www.technodom.kz/catalog/smartfony-i-gadzhety/gadzhety/smart-chasy", "master": "tablets_watches"},
    {"name": "Технодом: 🎧 Наушники", "url": "https://www.technodom.kz/catalog/tv-audio-foto-video/audio-tehnika/naushniki", "master": "audio"},
    {"name": "Технодом: 🖥 Мониторы", "url": "https://www.technodom.kz/catalog/noutbuki-i-komp-jutery/komp-jutery-i-monobloki/monitory", "master": "monitors"},
    {"name": "Технодом: 🎮 Игровые приставки", "url": "https://www.technodom.kz/catalog/noutbuki-i-komp-jutery/igrovye-pristavki-i-igry/igrovye-pristavki", "master": "consoles"},
    {"name": "Технодом: 🧹 Пылесосы", "url": "https://www.technodom.kz/catalog/bytovaja-tehnika/tehnika-dlja-doma/pylesosy", "master": "appliances_small"}
]

# 4. Forcecom (forcecom.kz)
FORCECOM_CATEGORIES = [
    {"name": "Forcecom: 🔥 Распродажа", "url": "https://forcecom.kz/sale/rasprodazha/", "master": "actions"},
    {"name": "Forcecom: 💻 Ноутбуки", "url": "https://forcecom.kz/catalog/laptops/", "master": "laptops"},
    {"name": "Forcecom: 🎮 Видеокарты", "url": "https://forcecom.kz/catalog/graphics-cards/", "master": "pc_components"},
    {"name": "Forcecom: 🧩 Материнские платы", "url": "https://forcecom.kz/catalog/motherboards/", "master": "pc_components"},
    {"name": "Forcecom: 🧠 Оперативная память", "url": "https://forcecom.kz/catalog/ram/", "master": "pc_components"},
    {"name": "Forcecom: 🖥 Мониторы", "url": "https://forcecom.kz/catalog/monitors/", "master": "monitors"},
    {"name": "Forcecom: 💾 SSD диски", "url": "https://forcecom.kz/catalog/ssd/", "master": "pc_components"},
    {"name": "Forcecom: 💾 Жесткие диски", "url": "https://forcecom.kz/catalog/hdd/", "master": "pc_components"},
    {"name": "Forcecom: 📦 Корпуса", "url": "https://forcecom.kz/catalog/cases/", "master": "pc_components"},
    {"name": "Forcecom: 🎧 Наушники", "url": "https://forcecom.kz/catalog/headphones/", "master": "audio"},
    {"name": "Forcecom: ⌨️ Клавиатуры", "url": "https://forcecom.kz/catalog/keyboards/", "master": "pc_components"},
    {"name": "Forcecom: 🖱 Мыши", "url": "https://forcecom.kz/catalog/mice/", "master": "pc_components"},
    {"name": "Forcecom: 🖨 Принтеры", "url": "https://forcecom.kz/catalog/printers/", "master": "office_network"},
    {"name": "Forcecom: 🌐 Роутеры", "url": "https://forcecom.kz/catalog/marshrutizatory/", "master": "office_network"}
]

# 5. Sulpak (sulpak.kz)
SULPAK_CATEGORIES = [
    {"name": "Sulpak: 💻 Ноутбуки", "url": "https://www.sulpak.kz/f/noutbuki", "master": "laptops"},
    {"name": "Sulpak: 📱 Смартфоны", "url": "https://www.sulpak.kz/f/smartfoniy/", "master": "smartphones"},
    {"name": "Sulpak: 📺 Телевизоры", "url": "https://www.sulpak.kz/f/led_oled_televizoriy", "master": "tvs"},
    {"name": "Sulpak: 📱 Планшеты", "url": "https://www.sulpak.kz/f/planshetiy", "master": "tablets_watches"},
    {"name": "Sulpak: ⌚️ Смарт-часы", "url": "https://www.sulpak.kz/f/smart_chasiy", "master": "tablets_watches"},
    {"name": "Sulpak: 🎧 Наушники", "url": "https://www.sulpak.kz/f/naushniki", "master": "audio"},
    {"name": "Sulpak: 🎮 Игровые приставки", "url": "https://www.sulpak.kz/f/igroviye_pristavki", "master": "consoles"},
    {"name": "Sulpak: 🧺 Стиральные машины", "url": "https://www.sulpak.kz/f/stiralniye_mashiniy", "master": "appliances_large"},
    {"name": "Sulpak: ❄️ Холодильники", "url": "https://www.sulpak.kz/f/holodilniki", "master": "appliances_large"},
    {"name": "Sulpak: 🌬 Кондиционеры", "url": "https://www.sulpak.kz/f/kondicioneriy", "master": "appliances_large"},
    {"name": "Sulpak: ☕️ Кофемашины", "url": "https://www.sulpak.kz/f/kofemashiniy", "master": "appliances_small"},
    {"name": "Sulpak: ♨️ Микроволновые печи", "url": "https://www.sulpak.kz/f/mikrovolnoviye_pechi", "master": "appliances_small"},
    {"name": "Sulpak: 🔥 Распродажа", "url": "https://www.sulpak.kz/sale/1", "master": "actions"}
]

# 6. Мечта (mechta.kz)
MECHTA_CATEGORIES = [
    {"name": "Мечта: 💻 Ноутбуки", "url": "https://www.mechta.kz/section/noutbuki/", "master": "laptops"},
    {"name": "Мечта: 📱 Смартфоны", "url": "https://www.mechta.kz/section/smartfony/", "master": "smartphones"},
    {"name": "Мечта: 📺 Телевизоры", "url": "https://www.mechta.kz/section/televizory/", "master": "tvs"},
    {"name": "Мечта: 🖥 Мониторы", "url": "https://www.mechta.kz/section/monitory/", "master": "monitors"},
    {"name": "Мечта: 📱 Планшеты", "url": "https://www.mechta.kz/section/planshety/", "master": "tablets_watches"},
    {"name": "Мечта: ⌚️ Смарт-часы", "url": "https://www.mechta.kz/section/smart-chasy/", "master": "tablets_watches"},
    {"name": "Мечта: 🎧 Наушники", "url": "https://www.mechta.kz/section/naushniki/", "master": "audio"},
    {"name": "Мечта: 🎮 Игровые приставки", "url": "https://www.mechta.kz/section/igrovye-pristavki/", "master": "consoles"},
    {"name": "Мечта: 🧹 Пылесосы", "url": "https://www.mechta.kz/section/pylesosy/", "master": "appliances_small"},
    {"name": "Мечта: ❄️ Холодильники", "url": "https://www.mechta.kz/section/holodilniki/", "master": "appliances_large"},
    {"name": "Мечта: 🧺 Стиральные машины", "url": "https://www.mechta.kz/section/stiralnye-mashiny/", "master": "appliances_large"},
    {"name": "Мечта: ☕️ Кофемашины", "url": "https://www.mechta.kz/section/kofemashiny/", "master": "appliances_small"},
    {"name": "Мечта: 🌬 Кондиционеры", "url": "https://www.mechta.kz/section/kondicionery/", "master": "appliances_large"},
    {"name": "Мечта: 💨 Утюги и отпариватели", "url": "https://www.mechta.kz/section/utyugi/", "master": "appliances_small"}
]

# 7. Alser (alser.kz)
ALSER_CATEGORIES = [
    {"name": "Alser: 💻 Ноутбуки", "url": "https://alser.kz/astana/c/noutbuki", "master": "laptops"},
    {"name": "Alser: 📱 Смартфоны", "url": "https://alser.kz/astana/c/smartfony", "master": "smartphones"},
    {"name": "Alser: 📺 Телевизоры", "url": "https://alser.kz/astana/c/televizory", "master": "tvs"},
    {"name": "Alser: 🖥 Мониторы", "url": "https://alser.kz/astana/c/monitory", "master": "monitors"},
    {"name": "Alser: 📱 Планшеты", "url": "https://alser.kz/astana/c/planshety", "master": "tablets_watches"},
    {"name": "Alser: 🧹 Пылесосы", "url": "https://alser.kz/astana/c/pylesosy", "master": "appliances_small"},
    {"name": "Alser: ❄️ Холодильники", "url": "https://alser.kz/astana/c/vse-holodilniki", "master": "appliances_large"},
    {"name": "Alser: 🌬 Кондиционеры", "url": "https://alser.kz/astana/c/vse-kondicioneri", "master": "appliances_large"}
]

# 8. Эврика (evrika.com)
EVRIKA_CATEGORIES = [
    {"name": "Эврика: 💻 Ноутбуки", "url": "https://evrika.com/catalog/nur-sultan-astana/noutbuki/c207", "master": "laptops"},
    {"name": "Эврика: 📱 Смартфоны", "url": "https://evrika.com/catalog/nur-sultan-astana/smartfony/c234", "master": "smartphones"},
    {"name": "Эврика: 📺 Телевизоры", "url": "https://evrika.com/catalog/nur-sultan-astana/led-televizory/c228", "master": "tvs"},
    {"name": "Эврика: 🖥 Мониторы", "url": "https://evrika.com/catalog/nur-sultan-astana/monitory/c300", "master": "monitors"},
    {"name": "Эврика: 📱 Планшеты", "url": "https://evrika.com/catalog/nur-sultan-astana/planshety/c70", "master": "tablets_watches"},
    {"name": "Эврика: 🎧 Наушники", "url": "https://evrika.com/catalog/nur-sultan-astana/naushniki-1/c183", "master": "audio"},
    {"name": "Эврика: 🎮 Игровые приставки", "url": "https://evrika.com/catalog/nur-sultan-astana/igrovye-pristavki/c120", "master": "consoles"},
    {"name": "Эврика: 🖨 Принтеры", "url": "https://evrika.com/catalog/nur-sultan-astana/printery/c65", "master": "office_network"},
    {"name": "Эврика: 💨 Утюги", "url": "https://evrika.com/catalog/nur-sultan-astana/utyugi/c161", "master": "appliances_small"}
]

# 9. Moon.kz (moon.kz)
MOON_CATEGORIES = [
    {"name": "Moon: 💻 Ноутбуки", "url": "https://moon.kz/noutbuki-i-aksessuary/", "master": "laptops"},
    {"name": "Moon: 🎮 Видеокарты", "url": "https://moon.kz/videokarty/", "master": "pc_components"},
    {"name": "Moon: ⚙️ Процессоры", "url": "https://moon.kz/protsessory/", "master": "pc_components"},
    {"name": "Moon: 🖥 Мониторы", "url": "https://moon.kz/monitory/", "master": "monitors"},
    {"name": "Moon: 🧩 Материнские платы", "url": "https://moon.kz/materinskie-platy/", "master": "pc_components"},
    {"name": "Moon: 🧠 Оперативная память", "url": "https://moon.kz/moduli-pamyati/", "master": "pc_components"},
    {"name": "Moon: 💾 SSD диски", "url": "https://moon.kz/nakopiteli-ssd/", "master": "pc_components"},
    {"name": "Moon: ⚡️ Блоки питания", "url": "https://moon.kz/bloki-pitaniya/", "master": "pc_components"},
    {"name": "Moon: 📦 Корпуса", "url": "https://moon.kz/korpusa/", "master": "pc_components"},
    {"name": "Moon: ❄️ Системы охлаждения", "url": "https://moon.kz/kulery-i-sistemy-okhlazhdeniya/", "master": "pc_components"},
    {"name": "Moon: 🔥 Распродажа", "url": "https://moon.kz/rasprodazha/", "master": "actions"}
]

# 10. Kaspi Магазин (kaspi.kz)
KASPI_CATEGORIES = [
    {"name": "Kaspi: 📱 Смартфоны", "url": "https://kaspi.kz/shop/c/smartphones/", "max_pages": 30, "master": "smartphones"},
    {"name": "Kaspi: 💻 Ноутбуки", "url": "https://kaspi.kz/shop/c/notebooks/", "max_pages": 30, "master": "laptops"},
    {"name": "Kaspi: ⌚️ Смарт-часы", "url": "https://kaspi.kz/shop/c/smart%20watches/", "max_pages": 30, "master": "tablets_watches"},
    {"name": "Kaspi: 🎧 Наушники", "url": "https://kaspi.kz/shop/c/headphones/", "max_pages": 30, "master": "audio"},
    {"name": "Kaspi: 📱 Планшеты", "url": "https://kaspi.kz/shop/c/tablets/", "max_pages": 30, "master": "tablets_watches"},
    {"name": "Kaspi: 🖥 Мониторы", "url": "https://kaspi.kz/shop/c/monitors/", "max_pages": 30, "master": "monitors"},
    {"name": "Kaspi: 🎮 Видеокарты", "url": "https://kaspi.kz/shop/c/videocards/", "max_pages": 30, "master": "pc_components"},
    {"name": "Kaspi: ⚙️ Процессоры", "url": "https://kaspi.kz/shop/c/cpus/", "max_pages": 30, "master": "pc_components"},
    {"name": "Kaspi: 🔌 Материнские платы", "url": "https://kaspi.kz/shop/c/motherboards/", "max_pages": 30, "master": "pc_components"},
    {"name": "Kaspi: 🎮 Игровые приставки", "url": "https://kaspi.kz/shop/c/game%20consoles/", "max_pages": 30, "master": "consoles"},
    {"name": "Kaspi: 📺 Телевизоры", "url": "https://kaspi.kz/shop/c/tvs/", "max_pages": 30, "master": "tvs"},
    {"name": "Kaspi: ❄️ Холодильники", "url": "https://kaspi.kz/shop/c/refrigerators/", "max_pages": 30, "master": "appliances_large"},
    {"name": "Kaspi: 🧺 Стиральные машины", "url": "https://kaspi.kz/shop/c/washers/", "max_pages": 30, "master": "appliances_large"},
    {"name": "Kaspi: 🧹 Пылесосы", "url": "https://kaspi.kz/shop/c/vacuum%20cleaners/", "max_pages": 30, "master": "appliances_small"},
    {"name": "Kaspi: 🤖 Роботы-пылесосы", "url": "https://kaspi.kz/shop/c/robot%20vacuum%20cleaners/", "max_pages": 30, "master": "appliances_small"},
    {"name": "Kaspi: ☕️ Кофемашины", "url": "https://kaspi.kz/shop/c/coffee%20machines%20and%20coffee%20makers/", "max_pages": 30, "master": "appliances_small"},
    {"name": "Kaspi: 💨 Кондиционеры", "url": "https://kaspi.kz/shop/c/air%20conditioners/", "max_pages": 30, "master": "appliances_large"},
    {"name": "Kaspi: 🖨 Принтеры и МФУ", "url": "https://kaspi.kz/shop/c/mf%20printers/", "max_pages": 30, "master": "office_network"},
    {"name": "Kaspi: 📽 Проекторы", "url": "https://kaspi.kz/shop/c/video%20projectors/", "max_pages": 30, "master": "tvs"},
]

# 11. 4mobile (4mobile.pages.dev)
FOURMOBILE_CATEGORIES = [
    # The endpoint already contains every group: fetch once and reconcile as one source.
    {"name": "4mobile: Все товары", "url": "https://4mobile.pages.dev/api/data", "master": "all"}
]

# Public catalog; regional stock is not confirmed, so offers appear under All cities.
FLIP_CATEGORIES = [
    {"name": "Flip: Электроника", "url": "https://www.flip.kz/catalog?subsection=5319", "max_pages": 50, "master": "all"},
]

# Halyk Market catalog (Almaty location=-2)
HALYK_CATEGORIES = [
    {"name": "Halyk: Смартфоны", "url": "https://halykmarket.kz/category/smartfony", "max_pages": 50, "master": "smartphones"},
    {"name": "Halyk: Ноутбуки", "url": "https://halykmarket.kz/category/noutbuki", "max_pages": 50, "master": "laptops"},
    {"name": "Halyk: Телевизоры", "url": "https://halykmarket.kz/category/televizori", "max_pages": 50, "master": "tvs"},
    {"name": "Halyk: Наушники", "url": "https://halykmarket.kz/category/naushniki", "max_pages": 50, "master": "audio"},
    {"name": "Halyk: Планшеты", "url": "https://halykmarket.kz/category/plansheti", "max_pages": 50, "master": "tablets_watches"},
    {"name": "Halyk: Смарт-часы", "url": "https://halykmarket.kz/category/smart-chasi", "max_pages": 50, "master": "tablets_watches"},
    {"name": "Halyk: Игровые приставки", "url": "https://halykmarket.kz/category/igrovie-pristavki", "max_pages": 50, "master": "consoles"},
    {"name": "Halyk: Мониторы", "url": "https://halykmarket.kz/category/monitori", "max_pages": 50, "master": "monitors"},
]
# ТехноGRAD (tgrad.kz): склад в Алматы, страницы категорий /page-N/, конец выдачи — редирект 302
TGRAD_CATEGORIES = [
    {"name": "Tgrad: 📺 Телевизоры", "url": "https://tgrad.kz/televizory/", "master": "tvs"},
    {"name": "Tgrad: 🔊 Портативная акустика", "url": "https://tgrad.kz/portativnaya-akustika/", "master": "audio"},
    {"name": "Tgrad: 🔈 Акустические системы", "url": "https://tgrad.kz/akusticheskie-sistemy/", "master": "audio"},
    {"name": "Tgrad: 📱 Смартфоны", "url": "https://tgrad.kz/smartfony/", "master": "smartphones"},
    {"name": "Tgrad: ⌚️ Смарт-часы", "url": "https://tgrad.kz/umnye-chasy/", "master": "tablets_watches"},
    {"name": "Tgrad: 📱 Планшеты", "url": "https://tgrad.kz/planshety/", "master": "tablets_watches"},
    {"name": "Tgrad: 🎧 Беспроводные наушники", "url": "https://tgrad.kz/besprovodnye-naushniki/", "master": "audio"},
    {"name": "Tgrad: 💻 Ноутбуки", "url": "https://tgrad.kz/noutbuki/", "master": "laptops"},
    {"name": "Tgrad: 🖥 Мониторы", "url": "https://tgrad.kz/monitory/", "master": "monitors"},
    {"name": "Tgrad: 🎧 Игровые наушники", "url": "https://tgrad.kz/igrovye-naushniki/", "master": "audio"},
    {"name": "Tgrad: 📡 Роутеры", "url": "https://tgrad.kz/routery/", "master": "office_network"},
    {"name": "Tgrad: 🧹 Пылесосы", "url": "https://tgrad.kz/pylesosy/", "master": "appliances_small"},
    {"name": "Tgrad: 🧹 Вертикальные пылесосы", "url": "https://tgrad.kz/vertikalnye-pylesosy/", "master": "appliances_small"},
    {"name": "Tgrad: 🤖 Роботы-пылесосы", "url": "https://tgrad.kz/roboty-pylesosy/", "master": "appliances_small"},
    {"name": "Tgrad: 👕 Утюги", "url": "https://tgrad.kz/utyugi/", "master": "appliances_small"},
    {"name": "Tgrad: 💨 Отпариватели", "url": "https://tgrad.kz/otparivateli/", "master": "appliances_small"},
    {"name": "Tgrad: 🚿 Водонагреватели", "url": "https://tgrad.kz/vodonagrevateli/", "master": "appliances_large"},
    {"name": "Tgrad: ❄️ Кондиционеры", "url": "https://tgrad.kz/konditsionery/", "master": "appliances_large"},
    {"name": "Tgrad: 🌬 Воздухоочистители", "url": "https://tgrad.kz/vozdukhoochistiteli/", "master": "appliances_large"},
    {"name": "Tgrad: 🧺 Стиральные машины", "url": "https://tgrad.kz/stiralnye-mashiny/", "master": "appliances_large"},
    {"name": "Tgrad: 🧺 Сушильные машины", "url": "https://tgrad.kz/sushilnye-mashiny/", "master": "appliances_large"},
    {"name": "Tgrad: 🍽 Посудомоечные машины", "url": "https://tgrad.kz/posudomoechnye-mashiny/", "master": "appliances_large"},
    {"name": "Tgrad: 🔥 Вытяжки", "url": "https://tgrad.kz/vytyazhki/", "master": "appliances_large"},
    {"name": "Tgrad: 📦 Микроволновые печи", "url": "https://tgrad.kz/mikrovolnovye-pechi/", "master": "appliances_small"},
    {"name": "Tgrad: 🍲 Мультиварки", "url": "https://tgrad.kz/multivarki/", "master": "appliances_small"},
    {"name": "Tgrad: 🥤 Блендеры", "url": "https://tgrad.kz/blendery/", "master": "appliances_small"},
    {"name": "Tgrad: 💇 Фены", "url": "https://tgrad.kz/feny-i-fen-shhetki/", "master": "appliances_small"},
    {"name": "Tgrad: 🪒 Электробритвы", "url": "https://tgrad.kz/elektrobritvy/", "master": "appliances_small"},
]
# ANTS (ants.kz): Bitrix/Aspro, микроразметка schema.org, выдача «сначала в наличии».
# Каталог большой (в «Ноутбуках» больше 12 страниц в наличии), поэтому глубина ограничена 25 страницами.
ANTS_CATEGORIES = [
    {"name": "ANTS: 💻 Ноутбуки", "url": "https://ants.kz/catalog/noutbuki/", "max_pages": 25, "master": "laptops"},
    {"name": "ANTS: 🖥 Моноблоки", "url": "https://ants.kz/catalog/monobloki/", "max_pages": 25, "master": "monitors"},
    {"name": "ANTS: 🖥 Системные блоки", "url": "https://ants.kz/catalog/sistemnye-bloki/", "max_pages": 25, "master": "pc_components"},
    {"name": "ANTS: ⚙️ Процессоры", "url": "https://ants.kz/catalog/protsessory/", "max_pages": 25, "master": "pc_components"},
    {"name": "ANTS: 🔌 Материнские платы", "url": "https://ants.kz/catalog/materinskie-platy/", "max_pages": 25, "master": "pc_components"},
    {"name": "ANTS: 🧠 Оперативная память", "url": "https://ants.kz/catalog/operativnaya-pamyat/", "max_pages": 25, "master": "pc_components"},
    {"name": "ANTS: 🎮 Видеокарты", "url": "https://ants.kz/catalog/videokarty/", "max_pages": 25, "master": "pc_components"},
    {"name": "ANTS: 💾 SSD-накопители", "url": "https://ants.kz/catalog/ssd-nakopiteli/", "max_pages": 25, "master": "pc_components"},
    {"name": "ANTS: 🗄 Корпуса", "url": "https://ants.kz/catalog/korpusa-dlya-kompyuterov/", "max_pages": 25, "master": "pc_components"},
    {"name": "ANTS: 🖥 Мониторы", "url": "https://ants.kz/catalog/monitory/", "max_pages": 25, "master": "monitors"},
    {"name": "ANTS: 🎧 Наушники и гарнитуры", "url": "https://ants.kz/catalog/naushniki-i-garnitury/", "max_pages": 25, "master": "audio"},
    {"name": "ANTS: 📡 Wi-Fi роутеры", "url": "https://ants.kz/catalog/wi-fi-routery/", "max_pages": 25, "master": "office_network"},
    {"name": "ANTS: 🖨 Принтеры", "url": "https://ants.kz/catalog/printery/", "max_pages": 25, "master": "office_network"},
    {"name": "ANTS: 🖨 МФУ", "url": "https://ants.kz/catalog/mnogofunktsionalnye-ustroystva-mfu/", "max_pages": 25, "master": "office_network"},
    {"name": "ANTS: 📽 Проекторы", "url": "https://ants.kz/catalog/proektory/", "max_pages": 25, "master": "tvs"},
    {"name": "ANTS: 📱 Смартфоны", "url": "https://ants.kz/catalog/smartfony/", "max_pages": 25, "master": "smartphones"},
    {"name": "ANTS: 📱 Планшеты", "url": "https://ants.kz/catalog/planshety/", "max_pages": 25, "master": "tablets_watches"},
    {"name": "ANTS: ⌚️ Смарт-часы", "url": "https://ants.kz/catalog/smart-chasy/", "max_pages": 25, "master": "tablets_watches"},
    {"name": "ANTS: 📺 Телевизоры", "url": "https://ants.kz/catalog/televizory/", "max_pages": 25, "master": "tvs"},
]

# ITMag (itmag.kz): Bitrix/Aspro, микроразметка schema.org, конец выдачи — нет ссылки на следующую страницу
ITMAG_CATEGORIES = [
    {"name": "ITMag: 💻 Ноутбуки", "url": "https://itmag.kz/catalog/noutbuki/", "master": "laptops"},
    {"name": "ITMag: 🖥 Моноблоки", "url": "https://itmag.kz/catalog/monobloki/", "master": "monitors"},
    {"name": "ITMag: 🖥 Системные блоки", "url": "https://itmag.kz/catalog/personal-nye-komp-yutery/", "master": "pc_components"},
    {"name": "ITMag: ⚙️ Процессоры", "url": "https://itmag.kz/catalog/protsessory/", "master": "pc_components"},
    {"name": "ITMag: 🔌 Материнские платы", "url": "https://itmag.kz/catalog/materinskie_platy/", "master": "pc_components"},
    {"name": "ITMag: 🧠 Оперативная память", "url": "https://itmag.kz/catalog/operativnaya_pamyat/", "master": "pc_components"},
    {"name": "ITMag: 🎮 Видеокарты", "url": "https://itmag.kz/catalog/videokarty/", "master": "pc_components"},
    {"name": "ITMag: 💾 Жесткие диски и SSD", "url": "https://itmag.kz/catalog/zhestkie_diski/", "master": "pc_components"},
    {"name": "ITMag: 🗄 Корпуса", "url": "https://itmag.kz/catalog/korpusa/", "master": "pc_components"},
    {"name": "ITMag: 🔋 Блоки питания", "url": "https://itmag.kz/catalog/bloki-pitaniya-k-korpusam/", "master": "pc_components"},
    {"name": "ITMag: 🖥 Мониторы", "url": "https://itmag.kz/catalog/monitory/", "master": "monitors"},
    {"name": "ITMag: 🎧 Наушники и гарнитуры", "url": "https://itmag.kz/catalog/naushniki-garnitury-i-mikrofony/", "master": "audio"},
    {"name": "ITMag: 📡 Wi-Fi роутеры", "url": "https://itmag.kz/catalog/besprovod_marshrutizatory_wifi_routery/", "master": "office_network"},
    {"name": "ITMag: 🖨 Принтеры", "url": "https://itmag.kz/catalog/printery/", "master": "office_network"},
    {"name": "ITMag: 🖨 МФУ", "url": "https://itmag.kz/catalog/mnogofunktsionalnye_ustroystva_mfu/", "master": "office_network"},
    {"name": "ITMag: 📽 Проекторы", "url": "https://itmag.kz/catalog/proektory/", "master": "tvs"},
    {"name": "ITMag: 📱 Смартфоны", "url": "https://itmag.kz/catalog/smartfony-i-mobilnye-telefony/", "master": "smartphones"},
    {"name": "ITMag: 📱 Планшеты", "url": "https://itmag.kz/catalog/planshety/", "master": "tablets_watches"},
    {"name": "ITMag: 📺 Телевизоры", "url": "https://itmag.kz/catalog/televizory/", "master": "tvs"},
]

# iSpace (ispace.kz): ссылки из сетки категории + JSON-LD карточки товара (цены в сетке не отдаются)
ISPACE_CATEGORIES = [
    {"name": "iSpace: 💻 Mac", "url": "https://ispace.kz/category/mac", "master": "laptops"},
    {"name": "iSpace: 📱 iPad", "url": "https://ispace.kz/category/ipad", "master": "tablets_watches"},
    {"name": "iSpace: 📱 iPhone", "url": "https://ispace.kz/category/iphone", "master": "smartphones"},
    {"name": "iSpace: ⌚️ Apple Watch", "url": "https://ispace.kz/category/apple-watch", "master": "tablets_watches"},
    {"name": "iSpace: 🎧 AirPods", "url": "https://ispace.kz/category/apple-airpods", "master": "audio"},
    {"name": "iSpace: 🎧 Наушники", "url": "https://ispace.kz/category/headsets", "master": "audio"},
    {"name": "iSpace: 🔊 Колонки", "url": "https://ispace.kz/category/speakers", "master": "audio"},
]

# Forte Market (market.forte.kz): универсальный маркетплейс, фасеты Algolia API
# Названия фасетов сверены с API 19.09.2026. Электроника — весь каталог категории (по 100 товаров
# на страницу); огромные неэлектронные разделы (десятки тысяч позиций) — ограниченная выборка.
FORTE_CATEGORIES = [
    {"name": "Forte: 📱 Смартфоны", "url": "https://market.forte.kz/catalog?facet=CategoryMap.Lvl3:Смартфоны", "master": "smartphones", "max_pages": 120},
    {"name": "Forte: 💻 Ноутбуки", "url": "https://market.forte.kz/catalog?facet=CategoryMap.Lvl2:Ноутбуки и ультрабуки", "master": "laptops", "max_pages": 60},
    {"name": "Forte: 🖥 Мониторы", "url": "https://market.forte.kz/catalog?facet=CategoryMap.Lvl3:Мониторы", "master": "monitors", "max_pages": 30},
    {"name": "Forte: 🎧 Наушники и гарнитуры", "url": "https://market.forte.kz/catalog?facet=CategoryMap.Lvl3:Наушники и гарнитуры", "master": "audio", "max_pages": 60},
    {"name": "Forte: 📱 Планшеты", "url": "https://market.forte.kz/catalog?facet=CategoryMap.Lvl3:Планшеты", "master": "tablets_watches", "max_pages": 30},
    {"name": "Forte: ⌚️ Смарт-часы", "url": "https://market.forte.kz/catalog?facet=CategoryMap.Lvl3:Смарт-часы и браслеты", "master": "tablets_watches", "max_pages": 40},
    {"name": "Forte: 📺 Телевизоры", "url": "https://market.forte.kz/catalog?facet=CategoryMap.Lvl3:Телевизоры", "master": "tvs", "max_pages": 20},
    {"name": "Forte: 🎮 Игровые приставки", "url": "https://market.forte.kz/catalog?facet=CategoryMap.Lvl3:Игровые консоли", "master": "consoles", "max_pages": 20},
    {"name": "Forte: 🧹 Пылесосы", "url": "https://market.forte.kz/catalog?facet=CategoryMap.Lvl3:Пылесосы", "master": "appliances_small", "max_pages": 30},
    {"name": "Forte: ❄️ Холодильники", "url": "https://market.forte.kz/catalog?facet=CategoryMap.Lvl3:Холодильники", "master": "appliances_large", "max_pages": 20},
    {"name": "Forte: 🧺 Стиральные машины", "url": "https://market.forte.kz/catalog?facet=CategoryMap.Lvl3:Стиральные машины", "master": "appliances_large", "max_pages": 20},
    {"name": "Forte: 🚗 Автотовары", "url": "https://market.forte.kz/catalog?facet=CategoryMap.Lvl1:Автотовары", "master": "all", "max_pages": 5},
    {"name": "Forte: 🛠 Строительство и ремонт", "url": "https://market.forte.kz/catalog?facet=CategoryMap.Lvl1:Строительство и ремонт", "master": "diy", "max_pages": 5},
    {"name": "Forte: 🏡 Товары для дома и дачи", "url": "https://market.forte.kz/catalog?facet=CategoryMap.Lvl1:Товары для дома и дачи", "master": "all", "max_pages": 5},
]

# Вкусмарт (vkusmart.vmv.kz): онлайн-супермаркет продуктов и бытовой химии
VKUSMART_CATEGORIES = [
    {"name": "Вкусмарт: 🔥 Акции супермаркета", "url": "https://vkusmart.vmv.kz/catalog/aktsii/", "master": "actions", "max_pages": 10},
    {"name": "Вкусмарт: 🥫 Бакалея", "url": "https://vkusmart.vmv.kz/catalog/bakaleya/", "master": "grocery", "max_pages": 15},
    {"name": "Вкусмарт: ☕️ Чай, кофе, какао", "url": "https://vkusmart.vmv.kz/catalog/chay-kofe-kakao/", "master": "grocery", "max_pages": 15},
    {"name": "Вкусмарт: 🧼 Чистота и порядок", "url": "https://vkusmart.vmv.kz/catalog/chistota-i-poryadok/", "master": "household", "max_pages": 15},
]

# 12 Месяцев (12.kz): гипермаркет стройматериалов, инструментов и ремонта
TWELVE_MONTHS_CATEGORIES = [
    {"name": "12 Месяцев: 🔌 Электроинструменты", "url": "https://12.kz/categories/elektroinstrumenty", "master": "diy", "max_pages": 15},
    {"name": "12 Месяцев: 🪛 Дрели и шуруповерты", "url": "https://12.kz/categories/dreli-shurupoverty", "master": "diy", "max_pages": 15},
    {"name": "12 Месяцев: 🔨 Перфораторы", "url": "https://12.kz/categories/perforatory", "master": "diy", "max_pages": 10},
    {"name": "12 Месяцев: 🔧 Ручной инструмент", "url": "https://12.kz/categories/ruchnoi-instrument", "master": "diy", "max_pages": 15},
    {"name": "12 Месяцев: 🚰 Смесители и сантехника", "url": "https://12.kz/categories/smesiteli", "master": "diy", "max_pages": 15},
    {"name": "12 Месяцев: 🧑‍🏭 Сварочные аппараты", "url": "https://12.kz/categories/svarochnye-apparaty", "master": "diy", "max_pages": 10},
    {"name": "12 Месяцев: 🌿 Садовая техника", "url": "https://12.kz/categories/sadovaya-tekhnika", "master": "diy", "max_pages": 15},
    {"name": "12 Месяцев: 🧼 Бытовая химия", "url": "https://12.kz/categories/bytovaya-khimiya", "master": "household", "max_pages": 15},
]

# Zeta (zeta.kz): производитель и ритейлер товаров для дома, быта, пластика и мебели
ZETA_CATEGORIES = [
    {"name": "Zeta: 🪣 Емкости, баки и ведра", "url": "6851938995dd04035cad42d6", "master": "household", "max_pages": 10},
    {"name": "Zeta: 👟 Обувницы и подставки", "url": "6851938095dd04035cad42c5", "master": "household", "max_pages": 10},
    {"name": "Zeta: 🧥 Вешалки", "url": "6851938395dd04035cad42cb", "master": "household", "max_pages": 10},
    {"name": "Zeta: 🪜 Стеллажи и этажерки", "url": "6851937e95dd04035cad42c0", "master": "household", "max_pages": 10},
    {"name": "Zeta: 🌿 Садовый инвентарь", "url": "6851939795dd04035cad42f3", "master": "diy", "max_pages": 10},
    {"name": "Zeta: 🪜 Стремянки", "url": "6851938d95dd04035cad42de", "master": "diy", "max_pages": 10},
    {"name": "Zeta: 🥩 Гриль и мангалы", "url": "6851939d95dd04035cad42fe", "master": "diy", "max_pages": 10},
    {"name": "Zeta: 🪑 Мебель для дачи и сада", "url": "6851938895dd04035cad42d5", "master": "diy", "max_pages": 10},
]

# Комфорт (komfort.kz): гипермаркет товаров для дома, ремонта и стройки
KOMFORT_CATEGORIES = [
    {"name": "Комфорт: 🪛 Дрели и шуруповерты", "url": "https://komfort.kz/catalog/instrumenty/elektroistrumenty/dreli_shurupoverty/", "master": "diy", "max_pages": 15},
    {"name": "Комфорт: 🔨 Перфораторы", "url": "https://komfort.kz/catalog/instrumenty/elektroistrumenty/perforatory/", "master": "diy", "max_pages": 10},
    {"name": "Комфорт: 🪜 Стремянки", "url": "https://komfort.kz/catalog/instrumenty/lestnitsy_pomosty/stremyanki/", "master": "diy", "max_pages": 10},
    {"name": "Комфорт: 🧱 Стройматериалы", "url": "https://komfort.kz/catalog/stroymaterialy/", "master": "diy", "max_pages": 15},
    {"name": "Комфорт: 🎨 Отделочные материалы", "url": "https://komfort.kz/catalog/otdelochnye_materialy/", "master": "diy", "max_pages": 15},
    {"name": "Комфорт: 🚰 Сантехника и отопление", "url": "https://komfort.kz/catalog/santekhnika_i_otoplenie/", "master": "diy", "max_pages": 15},
    {"name": "Комфорт: 🌿 Сад, огород и дача", "url": "https://komfort.kz/catalog/sad_ogorod_i_dacha/", "master": "diy", "max_pages": 15},
    {"name": "Комфорт: 💡 Электротовары, свет и климат", "url": "https://komfort.kz/catalog/elektrotovary_svet_i_klimat/", "master": "diy", "max_pages": 15},
    {"name": "Комфорт: 📦 Хранение и порядок", "url": "https://komfort.kz/catalog/khranenie_1/", "master": "household", "max_pages": 15},
    {"name": "Комфорт: 🏡 Товары для дома", "url": "https://komfort.kz/catalog/tovary_dlya_doma/", "master": "household", "max_pages": 15},
    {"name": "Комфорт: 🧹 Мелкая бытовая техника", "url": "https://komfort.kz/catalog/bytovaya_tekhnika/melkobytovaya_tekhnika/", "master": "appliances_small", "max_pages": 15},
]

# Лемана ПРО (lemanapro.kz, ex-Leroy Merlin): гипермаркет товаров для строительства, ремонта и сада
LEMANA_PRO_CATEGORIES = [
    {"name": "Лемана ПРО: 🔌 Электроинструменты", "url": "https://lemanapro.kz/catalogue/elektroinstrumenty/", "master": "diy", "max_pages": 15},
    {"name": "Лемана ПРО: 🪛 Дрели и шуруповерты", "url": "https://lemanapro.kz/catalogue/dreli-shurupoverty/", "master": "diy", "max_pages": 15},
    {"name": "Лемана ПРО: 🔨 Перфораторы", "url": "https://lemanapro.kz/catalogue/perforatory/", "master": "diy", "max_pages": 10},
    {"name": "Лемана ПРО: 🔧 Ручной инструмент", "url": "https://lemanapro.kz/catalogue/ruchnoy-instrument/", "master": "diy", "max_pages": 15},
    {"name": "Лемана ПРО: 🚰 Сантехника", "url": "https://lemanapro.kz/catalogue/santehnika/", "master": "diy", "max_pages": 15},
    {"name": "Лемана ПРО: 🧱 Стройматериалы", "url": "https://lemanapro.kz/catalogue/stroymaterialy/", "master": "diy", "max_pages": 15},
    {"name": "Лемана ПРО: 🌿 Садовая техника", "url": "https://lemanapro.kz/catalogue/sadovaya-tehnika/", "master": "diy", "max_pages": 15},
    {"name": "Лемана ПРО: 🪜 Стремянки", "url": "https://lemanapro.kz/catalogue/stremyanki/", "master": "diy", "max_pages": 10},
    {"name": "Лемана ПРО: 🧑‍🏭 Сварочное оборудование", "url": "https://lemanapro.kz/catalogue/svarochnoe-oborudovanie/", "master": "diy", "max_pages": 10},
    {"name": "Лемана ПРО: 🧼 Бытовая химия", "url": "https://lemanapro.kz/catalogue/bytovaya-himiya/", "master": "household", "max_pages": 15},
    {"name": "Лемана ПРО: 📦 Аксессуары для хранения", "url": "https://lemanapro.kz/catalogue/aksessuary-dlya-hraneniya/", "master": "household", "max_pages": 15},
]

# Arbuz.kz (arbuz.kz): онлайн-супермаркет продуктов питания и товаров для дома
ARBUZ_CATEGORIES = [
    {"name": "Arbuz: 🧼 Средства для мытья посуды", "url": "https://arbuz.kz/ru/almaty/catalog/cat/224494-sredstva_dlya_mytya_posudy", "master": "household", "max_pages": 15},
    {"name": "Arbuz: 🧺 Стирка и уход за бельём", "url": "https://arbuz.kz/ru/almaty/catalog/cat/224405-stirka_i_uhod_za_bel_m", "master": "household", "max_pages": 15},
    {"name": "Arbuz: 🧻 Салфетки и туалетная бумага", "url": "https://arbuz.kz/ru/almaty/catalog/cat/224493-calfetki_tualetnaya_bumaga", "master": "household", "max_pages": 15},
    {"name": "Arbuz: 🧴 Личная гигиена и косметика", "url": "https://arbuz.kz/ru/almaty/catalog/cat/224407-kosmetika_i_sredstva_lichnoi_gigieny", "master": "household", "max_pages": 15},
    {"name": "Arbuz: 🥫 Бакалея", "url": "https://arbuz.kz/ru/almaty/catalog/cat/225169-bakaleya", "master": "grocery", "max_pages": 15},
    {"name": "Arbuz: ☕️ Кофе, чай, какао", "url": "https://arbuz.kz/ru/almaty/catalog/cat/226099-kofe_chai_kakao", "master": "grocery", "max_pages": 15},
    {"name": "Arbuz: 🧀 Молоко, сыр и яйца", "url": "https://arbuz.kz/ru/almaty/catalog/cat/225161-moloko_syr_i_yaica", "master": "grocery", "max_pages": 15},
    {"name": "Arbuz: 🥩 Мясо и птица", "url": "https://arbuz.kz/ru/almaty/catalog/cat/225162-myaso_i_ptica", "master": "grocery", "max_pages": 15},
    {"name": "Arbuz: 🍬 Кондитерские изделия", "url": "https://arbuz.kz/ru/almaty/catalog/cat/225166-konditerskie_izdeliya", "master": "grocery", "max_pages": 15},
    {"name": "Arbuz: 🧃 Вода и напитки", "url": "https://arbuz.kz/ru/almaty/catalog/cat/14-voda_i_napitki", "master": "grocery", "max_pages": 15},
]

# MasterOK (masterok.kz): гипермаркет строительного, садового и силового оборудования
MASTEROK_CATEGORIES = [
    # Разделы настоящего каталога. Раньше здесь были подразделы «Распродажи витрины» — на сайте
    # это отдельная ветка, и весь каталог (около 2000 позиций) не собирался вовсе.
    # У masterok.kz нет общих разделов «Инструменты» или «Сварочное оборудование»: такими именами
    # названы только подразделы распродажи и уценки. Каталог устроен по видам техники.
    {"name": "MasterOK: 📦 Складское оборудование", "url": "https://masterok.kz/catalog/skladskoe-oborudovanie/", "master": "diy", "max_pages": 60},
    {"name": "MasterOK: 🔩 Запчасти", "url": "https://masterok.kz/catalog/zapchasti/", "master": "diy", "max_pages": 50},
    {"name": "MasterOK: 🚜 Минитракторы", "url": "https://masterok.kz/catalog/minitraktory/", "master": "diy", "max_pages": 25},
    {"name": "MasterOK: 🌱 Мотоблоки и культиваторы", "url": "https://masterok.kz/catalog/kultivatory-motobloki/", "master": "diy", "max_pages": 25},
    {"name": "MasterOK: 🪵 Деревообрабатывающие станки", "url": "https://masterok.kz/catalog/derevoobrabatyvayushchee-oborudovanie1/", "master": "diy", "max_pages": 20},
    {"name": "MasterOK: 🧹 Техника для уборки", "url": "https://masterok.kz/catalog/tekhnika-dlya-uborki/", "master": "diy", "max_pages": 20},
    {"name": "MasterOK: ⚡️ Генераторы и электростанции", "url": "https://masterok.kz/catalog/generatory-el-stantsii/", "master": "diy", "max_pages": 10},
    # Скидочные ветки: одна ссылка на корень заменяет восемь прежних подразделов и даёт больше
    {"name": "MasterOK: 🔥 Распродажа витрины", "url": "https://masterok.kz/catalog/rasprodazha-vitrina/", "master": "actions", "max_pages": 20},
    {"name": "MasterOK: 🏷 Уценка", "url": "https://masterok.kz/catalog/ucenennyj-tovar/", "master": "actions", "max_pages": 10},
    {"name": "MasterOK: ✨ Новинки", "url": "https://masterok.kz/catalog/newproduct/", "master": "diy", "max_pages": 10},
]

# Магнум (magnum.kz): крупнейшая розничная сеть супермаркетов Казахстана
MAGNUM_CATEGORIES = [
    {"name": "Магнум: 🔥 Все акции и скидки", "url": "https://magnum.kz/catalog", "master": "actions", "max_pages": 5},
    {"name": "Магнум: 🥫 Бакалея", "url": "https://magnum.kz/catalog?category=bakaleia", "master": "grocery", "max_pages": 5},
    {"name": "Магнум: ☕️ Чай, кофе, какао", "url": "https://magnum.kz/catalog?category=chai-koffee-kakao", "master": "grocery", "max_pages": 5},
    {"name": "Магнум: 🥛 Молочные продукты", "url": "https://magnum.kz/catalog?category=molochnye-produkty", "master": "grocery", "max_pages": 5},
    {"name": "Магнум: 🥩 Мясо и птица", "url": "https://magnum.kz/catalog?category=myaso", "master": "grocery", "max_pages": 5},
    {"name": "Магнум: 🍏 Фрукты и овощи", "url": "https://magnum.kz/catalog?category=frukty-ovoschi", "master": "grocery", "max_pages": 5},
    {"name": "Магнум: 🧀 Гастрономия", "url": "https://magnum.kz/catalog?category=gastronomiya", "master": "grocery", "max_pages": 5},
    {"name": "Магнум: 🍬 Кондитерские изделия", "url": "https://magnum.kz/catalog?category=konditerskie-izdeliya", "master": "grocery", "max_pages": 5},
    {"name": "Магнум: 🐟 Консервы", "url": "https://magnum.kz/catalog?category=konservy", "master": "grocery", "max_pages": 5},
    {"name": "Магнум: 🧃 Безалкогольные напитки", "url": "https://magnum.kz/catalog?category=bezalkogolnye-napitki", "master": "grocery", "max_pages": 5},
    {"name": "Магнум: 🧊 Замороженные продукты", "url": "https://magnum.kz/catalog?category=zamorojennye-produkty", "master": "grocery", "max_pages": 5},
    {"name": "Магнум: 🧼 Бытовая химия", "url": "https://magnum.kz/catalog?category=bytovaiya-himiya", "master": "household", "max_pages": 5},
    {"name": "Магнум: 🧴 Средства гигиены", "url": "https://magnum.kz/catalog?category=sredstva-gigieny", "master": "household", "max_pages": 5},
    {"name": "Магнум: 👶 Детские товары", "url": "https://magnum.kz/catalog?category=detskie-tovary", "master": "household", "max_pages": 5},
    {"name": "Магнум: 🍳 Собственное производство", "url": "https://magnum.kz/catalog?category=sobstvennoe-proizvodstvo", "master": "grocery", "max_pages": 5},
]

# Интертоп (intertop.kz): крупнейшая мультибрендовая сеть обуви, одежды и аксессуаров в Казахстане
INTERTOP_CATEGORIES = [
    {"name": "Интертоп: 🔥 Скидки и распродажи", "url": "https://intertop.kz/ru-kz/shopping/catalog/women/shoes/?discount=1", "master": "actions", "max_pages": 10},
    {"name": "Интертоп: 👠 Женская обувь", "url": "https://intertop.kz/ru-kz/shopping/catalog/women/shoes/", "master": "clothes", "max_pages": 10},
    {"name": "Интертоп: 👗 Женская одежда", "url": "https://intertop.kz/ru-kz/shopping/catalog/women/clothing/", "master": "clothes", "max_pages": 10},
    {"name": "Интертоп: 👞 Мужская обувь", "url": "https://intertop.kz/ru-kz/shopping/catalog/men/shoes/", "master": "clothes", "max_pages": 10},
    {"name": "Интертоп: 👕 Мужская одежда", "url": "https://intertop.kz/ru-kz/shopping/catalog/men/clothing/", "master": "clothes", "max_pages": 10},
    {"name": "Интертоп: 🧒 Детская обувь", "url": "https://intertop.kz/ru-kz/shopping/catalog/kids/shoes/", "master": "clothes", "max_pages": 10},
    {"name": "Интертоп: 👶 Детская одежда", "url": "https://intertop.kz/ru-kz/shopping/catalog/kids/clothing/", "master": "clothes", "max_pages": 10},
    {"name": "Интертоп: 👜 Аксессуары", "url": "https://intertop.kz/ru-kz/shopping/catalog/women/accessories/", "master": "clothes", "max_pages": 10},
]

MARWIN_CATEGORIES = [
    {"name": "Меломан: 📚 Книги", "url": "https://www.marwin.kz/books/", "master": "household", "max_pages": 10},
    {"name": "Меломан: 🎮 Видеоигры и консоли", "url": "https://www.marwin.kz/videogames/", "master": "consoles", "max_pages": 10},
    {"name": "Меломан: 🧸 Игрушки и развлечения", "url": "https://www.marwin.kz/toys-and-entertainment/", "master": "household", "max_pages": 10},
    {"name": "Меломан: 🧱 Конструкторы LEGO", "url": "https://www.marwin.kz/toys-and-entertainment/lego/", "master": "household", "max_pages": 10},
    {"name": "Меломан: 🎲 Настольные игры", "url": "https://www.marwin.kz/toys-and-entertainment/nastol-nye-igry/", "master": "household", "max_pages": 10},
    {"name": "Меломан: 🎨 Творчество и хобби", "url": "https://www.marwin.kz/tvorchestvo-19692/", "master": "household", "max_pages": 10},
    {"name": "Меломан: ✏️ Школа и канцелярия", "url": "https://www.marwin.kz/shkola-kancelyariya-19236/", "master": "office_network", "max_pages": 10},
    {"name": "Меломан: 🎵 Музыка и винил", "url": "https://www.marwin.kz/music/", "master": "audio", "max_pages": 10},
    {"name": "Меломан: 🍬 Сладости и подарки", "url": "https://www.marwin.kz/food-items/", "master": "grocery", "max_pages": 10},
]

ITEKA_CATEGORIES = [
    {"name": "i-Teka: 💊 Лекарства и БАД", "url": "https://i-teka.kz/astana/drug-categories/lekarstvennye-preparaty-i-bad", "master": "beauty_health", "max_pages": 10},
    {"name": "i-Teka: 🩺 Обезболивающие и спазмолитики", "url": "https://i-teka.kz/astana/drug-categories/obezbolivayuschie-i-spazmalitiki", "master": "beauty_health", "max_pages": 10},
    {"name": "i-Teka: 🦠 Противовирусные препараты", "url": "https://i-teka.kz/astana/drug-categories/protivovirusnye-preparaty", "master": "beauty_health", "max_pages": 10},
    {"name": "i-Teka: 💉 Антибиотики и противомикробные", "url": "https://i-teka.kz/astana/drug-categories/antibakterialjnye-preparaty-i-antibiotiki", "master": "beauty_health", "max_pages": 10},
    {"name": "i-Teka: 🩹 Медтехника и изделия мед. назначения", "url": "https://i-teka.kz/astana/drug-categories/izdeliya-medicinskogo-naznacheniya-i-medtehnika", "master": "beauty_health", "max_pages": 10},
    {"name": "i-Teka: 🫀 Тонометры и диагностика", "url": "https://i-teka.kz/astana/drug-categories/tonometry-i-komplektuyuschie-dlya-tonometrov", "master": "beauty_health", "max_pages": 10},
    {"name": "i-Teka: 🧴 Санитария и гигиена", "url": "https://i-teka.kz/astana/drug-categories/sanitariya-i-gigiena", "master": "beauty_health", "max_pages": 10},
    {"name": "i-Teka: 👶 Товары для матери и ребенка", "url": "https://i-teka.kz/astana/drug-categories/tovary-dlya-materi-i-rebenka", "master": "beauty_health", "max_pages": 10},
    {"name": "i-Teka: 💄 Косметика и дерматологический уход", "url": "https://i-teka.kz/astana/drug-categories/kosmetika-i-uhod", "master": "beauty_health", "max_pages": 10},
]

MEBEL_CATEGORIES = [
    {"name": "Mebel.kz: 🛋 Диваны и кресла", "url": "https://mebel.kz/category/divany", "master": "home_furniture", "max_pages": 10},
    {"name": "Mebel.kz: 🛏 Кровати", "url": "https://mebel.kz/category/krovati", "master": "home_furniture", "max_pages": 10},
    {"name": "Mebel.kz: 💤 Матрасы", "url": "https://mebel.kz/category/matrasy", "master": "home_furniture", "max_pages": 10},
    {"name": "Mebel.kz: 🚪 Шкафы и стеллажи", "url": "https://mebel.kz/category/shkafy", "master": "home_furniture", "max_pages": 10},
    {"name": "Mebel.kz: 💻 Рабочие столы", "url": "https://mebel.kz/category/stoly-pismennye-i-kompyuternye", "master": "home_furniture", "max_pages": 10},
    {"name": "Mebel.kz: 🍽 Обеденные столы и кухни", "url": "https://mebel.kz/category/kuhonnye-stoly", "master": "home_furniture", "max_pages": 10},
    {"name": "Mebel.kz: 🗄 Комоды и тумбы", "url": "https://mebel.kz/category/komody", "master": "home_furniture", "max_pages": 10},
    {"name": "Mebel.kz: 💺 Компьютерные кресла", "url": "https://mebel.kz/category/kompyuternye-kresla", "master": "home_furniture", "max_pages": 10},
    {"name": "Mebel.kz: 🧶 Ковры и текстиль", "url": "https://mebel.kz/category/kovry-i-tekstil", "master": "home_furniture", "max_pages": 10},
    {"name": "Mebel.kz: 💡 Светильники и свет", "url": "https://mebel.kz/category/svet", "master": "home_furniture", "max_pages": 10},
]

# Детский мир (detmir.kz): крупнейшая сеть детских товаров в Казахстане
DETMIR_CATEGORIES = [
    {"name": "Детский мир: 🧩 Игрушки и игры", "url": "https://detmir.kz/catalog/index/name/igry_i_igrushki/", "master": "household", "max_pages": 10},
    {"name": "Детский мир: 🧱 Конструкторы и LEGO", "url": "https://detmir.kz/catalog/index/name/konstruktory/", "master": "household", "max_pages": 10},
    {"name": "Детский мир: 🍼 Детское питание и кормление", "url": "https://detmir.kz/catalog/index/name/nutrition_feeding/", "master": "grocery", "max_pages": 10},
    {"name": "Детский мир: 👶 Подгузники и гигиена", "url": "https://detmir.kz/catalog/index/name/hygiene_care/", "master": "household", "max_pages": 10},
    {"name": "Детский мир: 👕 Одежда и обувь", "url": "https://detmir.kz/catalog/index/name/children_clothes/", "master": "clothes", "max_pages": 10},
    {"name": "Детский мир: 🚼 Детские коляски", "url": "https://detmir.kz/catalog/index/name/kolyaski/", "master": "household", "max_pages": 10},
    {"name": "Детский мир: 🚗 Автокресла", "url": "https://detmir.kz/catalog/index/name/avtokresla/", "master": "household", "max_pages": 10},
    {"name": "Детский мир: 🛏 Детская комната", "url": "https://detmir.kz/catalog/index/name/childrens_room/", "master": "home_furniture", "max_pages": 10},
    {"name": "Детский мир: 🎨 Хобби и творчество", "url": "https://detmir.kz/catalog/index/name/hobbies_creativity/", "master": "household", "max_pages": 10},
    {"name": "Детский мир: 🛴 Детский транспорт", "url": "https://detmir.kz/catalog/index/name/childrens_transport/", "master": "household", "max_pages": 10},
]

# Askona (askona.kz): товары для здорового сна, ортопедические матрасы и мебель
ASKONA_CATEGORIES = [
    {"name": "Аскона: 💤 Анатомические матрасы", "url": "https://askona.kz/matrasy/", "master": "home_furniture", "max_pages": 10},
    {"name": "Аскона: 🛏 Кровати и изголовья", "url": "https://askona.kz/krovati/", "master": "home_furniture", "max_pages": 10},
    {"name": "Аскона: 🛋 Анатомические диваны", "url": "https://askona.kz/divany/", "master": "home_furniture", "max_pages": 10},
    {"name": "Аскона: ☁️ Анатомические подушки", "url": "https://askona.kz/podushki/", "master": "home_furniture", "max_pages": 10},
    {"name": "Аскона: 🪶 Одеяла", "url": "https://askona.kz/odeyala/", "master": "home_furniture", "max_pages": 10},
    {"name": "Аскона: 🧵 Постельное белье и текстиль", "url": "https://askona.kz/tekstil/", "master": "home_furniture", "max_pages": 10},
    {"name": "Аскона: 🚪 Мебель для спальни", "url": "https://askona.kz/mebel/", "master": "home_furniture", "max_pages": 10},
    {"name": "Аскона: 👶 Товары для детей", "url": "https://askona.kz/dlya-detey/", "master": "household", "max_pages": 10},
    {"name": "Аскона: 💺 Кресла и пуфы", "url": "https://askona.kz/kresla/", "master": "home_furniture", "max_pages": 10},
    {"name": "Аскона: 🏡 Товары для дома и декор", "url": "https://askona.kz/dlya-doma/", "master": "home_furniture", "max_pages": 10},
]

# ZooMarket (zoomarket.kz): интернет-зоомагазин, корма, ветаптека, лакомства и уход
ZOOMARKET_CATEGORIES = [
    {"name": "Зоомаркет: 🐱 Корма для кошек", "url": "https://zoomarket.kz/catalog/cat/korm_k/", "master": "pets", "max_pages": 10},
    {"name": "Зоомаркет: 🐶 Корма для собак", "url": "https://zoomarket.kz/catalog/dog/korm/", "master": "pets", "max_pages": 10},
    {"name": "Зоомаркет: 🥫 Консервы для кошек", "url": "https://zoomarket.kz/catalog/cat/konservy_k/", "master": "pets", "max_pages": 10},
    {"name": "Зоомаркет: 🍖 Консервы для собак", "url": "https://zoomarket.kz/catalog/dog/konservy/", "master": "pets", "max_pages": 10},
    {"name": "Зоомаркет: 🚽 Наполнители и туалеты", "url": "https://zoomarket.kz/catalog/cat/tualet_k/", "master": "pets", "max_pages": 10},
    {"name": "Зоомаркет: 💊 Ветаптека для кошек", "url": "https://zoomarket.kz/catalog/cat/apteka_k/", "master": "pets", "max_pages": 10},
    {"name": "Зоомаркет: 💉 Ветаптека для собак", "url": "https://zoomarket.kz/catalog/dog/apteka/", "master": "pets", "max_pages": 10},
    {"name": "Зоомаркет: 🦴 Лакомства для собак", "url": "https://zoomarket.kz/catalog/dog/lakomstva/", "master": "pets", "max_pages": 10},
    {"name": "Зоомаркет: 🐭 Товары для грызунов", "url": "https://zoomarket.kz/catalog/rodent/", "master": "pets", "max_pages": 10},
    {"name": "Зоомаркет: 🐠 Аквариумистика и рыбы", "url": "https://zoomarket.kz/catalog/fish/", "master": "pets", "max_pages": 10},
]















