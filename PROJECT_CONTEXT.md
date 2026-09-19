# 🎯 KZ Price Hunter — Полный контекст проекта

> ⚠️ **Исторический обзор (по состоянию до v4.x).** Число магазинов, методы сбора и цифры каталога здесь устарели.
> Актуально: [README.md](README.md), [docs/](docs/README.md), [SOURCES.md](SOURCES.md), [CHANGELOG.md](CHANGELOG.md), [AUDIT_FINAL.md](AUDIT_FINAL.md).


> **Версия системы**: **v4.3.0** (2026-09-18)  
> **Для любого подключающегося агента / разработчика:**  
> Этот документ содержит полную архитектуру, актуальное состояние базы данных, список всех 17 поддерживаемых магазинов, структуру кода, AI-подсистему и инструкции для бесшовного продолжения работы.

---

## 1. О проекте
**KZ Price Hunter** — интеллектуальный сервис мониторинга цен, поиска ценовых аномалий («пропущенный ноль», супер-скидки, межмагазинный арбитраж) и агрегатор лучших цен по **17 крупнейшим розничным сетям и маркетплейсам Казахстана** (Астана, Алматы, Шымкент, Караганда и др.).

В проект интегрирована трехуровневая AI-система (понимание поисковых запросов естественным языком, RAG AI-консультант в Web UI и Telegram, а также каноническая нормализация моделей для консервативного кросс-магазинного арбитража).

### Ключевые принципы:
1. **Cache-First / Anti-DDoS:** Пользовательские поиски идут исключительно по локальной базе данных через полнотекстовый поиск FTS5 (скорость отклика 1–5 миллисекунд), не создавая паразитных запросов к сайтам магазинов.
2. **Параллельная работа без блокировок:** SQLite работает в режиме **WAL (Write-Ahead Logging)** со `synchronous = NORMAL`. Фоновый сборщик может обновлять тысячи позиций одновременно с чтением из базы через Web UI.
3. **Автономность:** Фоновый воркер проверяет свежесть базы данных (порог: **3 часа / 180 минут**). Если база устарела, запускается мягкое параллельное фоновое обновление (до 4 магазинов одновременно).
4. **Легковесность (Zero Heavy SDKs):** Все сетевые запросы, парсинг, интеграция с Google Gemini / OpenAI REST API и Telegram Bot Long Polling реализованы на чистом `aiohttp` / `curl_cffi` без тяжелых SDK (`google-generativeai`, `openai`, `aiogram`), что гарантирует моментальный запуск и минимальное потребление RAM/CPU на Synology NAS и VPS.
5. **Многопользовательская изоляция:** Настройки фильтрации, города и поисковые предпочтения сохраняются в `localStorage` браузера каждого пользователя и профиле `users`, не перезаписывая общие серверные параметры.

---

## 2. Поддерживаемые магазины (17 сетей Казахстана)

В каталоге мониторинга задействовано более **58 000+ товаров** из 17 сетей:

| # | Магазин | Эмодзи | Домен / Источник | Метод парсинга | Особенности |
|---|---|:---:|---|---|---|
| 1 | **DNS Казахстан** | 🟧 | `dns-shop.kz` | Playwright | Cloudflare: доступна только первая страница категории (защиту не обходим) |
| 2 | **Белый Ветер** | 🟦 | `shop.kz` | **YML выгрузка** (`/bitrix/catalog_export/yandex.php`) | 16 000+ товаров в XML, мгновенный потоковый парсинг |
| 3 | **Технодом** | 🔴 | `technodom.kz` | `curl_cffi` / REST JSON | Высокоскоростной обход без headless браузера |
| 4 | **Sulpak** | 🟢 | `sulpak.kz` | `curl_cffi` (Chrome124) | Быстрый HTML парсинг, учет скидок |
| 5 | **Мечта** | 🟣 | `mechta.kz` | `curl_cffi` REST API | `/api/v3/catalog/products`, пагинация |
| 6 | **Alser** | 🟡 | `alser.kz` | `curl_cffi` HTML | Городские каталоги `/astana/c/...` |
| 7 | **Эврика** | 🔷 | `evrika.com` | `curl_cffi` JSON API | `back.evrika.com/catalog/...` |
| 8 | **Moon.kz** | 🚀 | `moon.kz` | `curl_cffi` HTML | Компьютерная техника, серверы, комплектующие |
| 9 | **Forcecom** | ⚡️ | `forcecom.kz` | `curl_cffi` HTML | Компьютерная техника, серверы, оргтехника |
| 10 | **Kaspi Магазин**| 🔴 | `kaspi.kz` | `curl_cffi` JSON API | Маркетплейс №1, карточки продавцов и предложений |
| 11 | **4mobile** | 📱 | `4mobile.pages.dev` | `curl_cffi` REST API | `/api/data` — Apple, Dyson, WiWU, PS5, DJI |
| 12 | **Flip.kz** | 📦 | `flip.kz` | `curl_cffi` HTML | Крупный гипермаркет, электроника и гаджеты |
| 13 | **Halyk Market**| 🟢 | `halykmarket.kz` | `curl_cffi` JSON API | Маркетплейс Halyk Bank, учет старой цены (`oldprice`) |
| 14 | **Tgrad** | ⚪️ | `tgrad.kz` | `curl_cffi` HTML | 28 категорий техники и потребительской электроники |
| 15 | **ANTS** | 🐜 | `ants.kz` | `curl_cffi` Bitrix/Aspro | Разбор микроразметки Schema.org |
| 16 | **ITMag** | 💻 | `itmag.kz` | `curl_cffi` Bitrix/Aspro | Компьютерная и сетевая техника |
| 17 | **iSpace** | 🍏 | `ispace.kz` | `curl_cffi` JSON-LD | Официальный Apple Premium Reseller в Казахстане |

---

## 3. Архитектура и Структура файлов

```
kz-price-hunter/
├── auth.py               # Вход через Telegram Login Widget + Dev Login, сессии, роли, CSRF
├── config.py             # Общие (SYSTEM_DEFAULTS) и личные (USER_DEFAULTS) настройки, города, 17 сетей
├── database.py           # SQLite WAL, FTS5 products_fts, триггеры, индексы, canonical_key, статистика
├── detector.py           # Детектор аномалий: ZERO_GLITCH, SUPER_DISCOUNT, MARKET_ARBITRAGE
├── search_engine.py      # Интеллектуальный поиск (синонимы, стемминг, транслит раскладки, FTS5 + LIKE)
├── model_matching.py     # Детерминированная нормализация названий моделей (бренд, линейка, память, артикулы)
├── ai_service.py         # Прямая асинхронная интеграция с Google Gemini / OpenAI REST API (0 SDK, LRU кэш)
├── telegram_bot.py       # Интерактивный Telegram-бот (Long Polling на aiohttp, поиск, AI-консультант)
├── notifier.py           # Рассылка уведомлений в Telegram каждому пользователю с очередью повторов
├── log_manager.py        # Перехватчик sys.stdout/stderr, кольцевой буфер на 2 000 логов для Live UI
├── version.py            # Версионирование проекта (v4.0.0)
├── main.py               # Консольный раннер циклического мониторинга
├── gui.py                # Точка входа для Web Dashboard (запуск aiohttp на 8080)
├── scrapers/             # Модули парсеров 17 магазинов
│   ├── dns.py
│   ├── shopkz.py         # Потоковый YML-парсер yandex.php через iterparse
│   ├── technodom.py
│   ├── sulpak.py
│   ├── mechta.py
│   ├── alser.py
│   ├── evrika.py
│   ├── moon.py
│   ├── forcecom.py
│   ├── kaspi.py
│   ├── fourmobile.py
│   ├── flip.py
│   ├── halykmarket.py
│   ├── tgrad.py
│   ├── ants.py
│   ├── itmag.py
│   └── ispace.py
└── web/
    ├── server.py         # HTTP REST API эндпоинты (/api/search, /api/ai/..., /api/models/compare и др.)
    └── templates/
        └── index.html    # Tailwind CSS SPA интерфейс (Аномалии, Скидки, Каталог, AI-Чат, Мониторинг, Логи)
```

---

## 4. Схема Базы Данных (SQLite WAL + FTS5)

1. **Таблица `products`**:
   - `id TEXT PRIMARY KEY` — уникальный строковый ключ вида `shopkz_134723`, `kaspi_1029384`
   - `shop TEXT` — название магазина («Белый Ветер», «DNS Казахстан», «Kaspi Магазин» и т.д.)
   - `title TEXT` — полное наименование товара
   - `category TEXT` — категория товара
   - `url TEXT` — прямая ссылка на товар
   - `image_url TEXT` — ссылка на изображение
   - `current_price INTEGER` — актуальная цена в тенге (KZT)
   - `first_price INTEGER` — первая зафиксированная цена
   - `min_price INTEGER` — минимальная цена за всю историю
   - `max_price INTEGER` — максимальная цена за всю историю
   - `old_price_on_site INTEGER DEFAULT 0` — зачеркнутая/старая цена на сайте магазина
   - `canonical_key TEXT` — нормализованный ключ модели (`apple:iphone 15:128gb`) для консервативного сопоставления
   - `city TEXT` — город наличия (Астана, Алматы, Караганда и др.)
   - `is_active INTEGER DEFAULT 1` — флаг актуальности предложения
   - `created_at TIMESTAMP`, `updated_at TIMESTAMP`

2. **Виртуальная таблица `products_fts`**:
   - Полнотекстовый поиск FTS5 (`content='products'`, `content_rowid='rowid'`)
   - Автоматическая синхронизация триггерами `products_ai`, `products_ad`, `products_au`
   - Отклик `MATCH ?` от 1 до 3 миллисекунд по 58 000+ товарам.

3. **Таблица `title_canonical_cache`**:
   - `title_hash TEXT PRIMARY KEY` — SHA-256 хэш названия товара
   - `title TEXT` — исходное название
   - `canonical_key TEXT` — эталонный ключ, сохраненный алгоритмом или AI-нормализатором
   - `created_at TIMESTAMP`

4. **Индексы**:
   - `idx_products_shop_city_price ON products(shop, city, current_price)`
   - `idx_products_updated_at ON products(updated_at)`
   - `idx_products_canonical_key ON products(canonical_key)`

5. **Таблицы `price_history` & `alerts`**:
   - `price_history`: история срезов цен во времени
   - `alerts`: аномалии цен (`ZERO_GLITCH`, `SUPER_DISCOUNT`, `MARKET_ARBITRAGE`) с полями `competitor_shop`, `competitor_price`, `diff_percent`, `savings_kzt`.

6. **Таблицы `users`, `sessions`, `notification_outbox`, `shop_scans`**:
   - Управление пользователями, авторизацией, отложенной доставкой уведомлений в Telegram и метриками обхода магазинов.

---

## 5. Трехуровневая AI-Архитектура

### Этап 1: Интеллектуальный AI-поиск (Natural Language Search)
- Пользователь вводит естественный запрос: *«игровой ноутбук rtx 4060 до 500к в алматы со скидкой»*.
- `ai_service.parse_natural_query`: распознает намерение, извлекает структурированные параметры (`query`, `category`, `brand`, `min_price`, `max_price`, `city`, `only_discounts`).
- Эвристика сложных запросов отсекает простые запросы (например, «iphone 15»), не расходуя токены API.
- Результаты запросов к LLM кэшируются в LRU памяти с TTL 4 часа.

### Этап 2: Интерактивный AI-Консультант (RAG в Web & Telegram)
- Пайплайн `ai_service.ask_ai_consultant`:
  1. Извлечение поискового намерения пользователя.
  2. RAG-запрос в локальную SQLite базу по 17 сетям Казахстана (выборка релевантных товаров с лучшими ценами).
  3. Формирование персонализированного ответа с конкретными ссылками на магазины, ценами в ₸ и обоснованием выгоды.
- Доступен во вкладке `💬 AI-Консультант` в веб-интерфейсе и в Telegram-боте (`telegram_bot.py`).

### Этап 3: Каноническая нормализация моделей и Арбитраж цен
- Проблема: Магазины пишут названия по-разному:
  - Белый Ветер: `Смартфон Apple iPhone 15 128GB Black (MTMR3)`
  - Kaspi: `Apple iPhone 15 128Gb черный`
  - iSpace: `iPhone 15 128 ГБ, Черный`
- Решение:
  1. `model_matching.extract_canonical_key`: детерминированная очистка служебных слов, артикулов в скобках, SIM-спецификаций, диагоналей и приведение памяти (`128GB`/`128 ГБ` -> `128gb`).
  2. `ai_service.normalize_product_titles_batch`: пакетная обработка через Gemini Flash для сложных/нетипичных названий с сохранением в `title_canonical_cache`.
  3. `database.find_market_comparisons`: мгновенный подбор цен на идентичную модель в других сетях через индекс `idx_products_canonical_key`.
  4. Эндпоинт `/api/models/compare` и модальное окно в Web UI для наглядного сравнения цен во всех 17 сетях.

---

## 6. Переменные Окружения (`.env`)

```ini
# Основные параметры сервера
PORT=8080
HOST=0.0.0.0
APP_URL=https://your-domain.kz

# Искусственный Интеллект (Gemini / OpenAI)
# Приоритет: GEMINI_API_KEY -> OPENAI_API_KEY -> локальный fallback
GEMINI_API_KEY=AIzaSy...
# или OpenAI / совместимый шлюз:
OPENAI_API_KEY=sk-...
OPENAI_API_BASE=https://api.openai.com/v1

# Telegram бот и уведомления
TELEGRAM_BOT_TOKEN=123456789:ABC...
TELEGRAM_CHAT_ID=123456789
TELEGRAM_BOT_USERNAME=kz_price_hunter_bot

# Сессии и безопасность
SESSION_SECRET=super-secret-random-key
```

---

## 7. Запуск и Тестирование

```bash
# Активация виртуального окружения
source venv/bin/activate

# Запуск Web Dashboard + Telegram Bot
python gui.py

# Запуск одного цикла сканирования всех 17 магазинов
python main.py --once

# Запуск полного набора автоматических тестов (46 тестов)
pytest tests/test_monitor.py -v
```

---

## 8. Продовый сервер и Деплой
- **Docker Compose**: Контейнер `kz-price-hunter` с томом `./data:/app/data` и автообновлением через `watchtower`.
- **Реестр образов**: `ghcr.io/molthun/kz-price-hunter:latest` (сборка GitHub Actions на Node 24).
- **База данных**: более **58 000+ товаров** по 17 сетям Казахстана.
- **Интервал обновления базы**: **3 часа** (`scan_interval_minutes = 180`).
