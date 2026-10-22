# Пакет `web/` — Веб-панель KZ Price Hunter

`web/` — HTTP-слой приложения: aiohttp-сервер, HTML-страницы и статические файлы. Вместе с приложением здесь запускаются фоновый планировщик обхода магазинов и задача Telegram-бота. Бизнес-логика (поиск, БД, скраперы, AI) лежит в корне репозитория: пакет вызывает её и отдаёт результат браузеру.

## Как запускается

Точка входа — `gui.py`; пакет `web/` сам по себе не запускается.

- `gui.py` вызывает `create_app()` из `web/server.py`, берёт порт из `PORT` (по умолчанию `8080`), слушает `0.0.0.0`, печатает локальный и LAN-адрес и открывает браузер.
- Docker (`Dockerfile`, `docker-compose.yml`): `CMD ["python3", "gui.py"]`, `PORT=35539`, `DATA_DIR=/app/data` (том `./data`).
- Локальный запуск со входом разработчика: `./run_local.sh` (`ALLOW_DEV_LOGIN=1`, `http://localhost:8080`).
- `main.py` — консольный режим без HTTP: импортирует из `web.server` `_do_scan_task`, `enabled_shop_keys`, `SHOP_REGISTRY`, `scan_state` и выполняет тот же обход. Переименование этих имён ломает CLI.

`create_app()` (последняя функция `web/server.py`):

1. `init_db()` — инициализация и миграции SQLite;
2. `web.Application(middlewares=[telemetry_middleware, auth_middleware, gzip_middleware])`;
3. `app.cleanup_ctx.append(background_tasks)` — автообход, AI-нормализация, рассылка уведомлений, Telegram-бот;
4. `app.add_routes(routes)` — 79 маршрутов из общего `routes = web.RouteTableDef()`;
5. `app.router.add_static("/static/", BASE_DIR / "web" / "static", show_index=False)`;
6. `app["stats_cache"] = BoundedTTLCache(...)` — кэш сводных счётчиков `/api/stats` (TTL 30 с, сбрасывается при обходе, настройках и разборе алертов).

Данные: `DATA_DIR` из `config.py` (по умолчанию — корень репозитория), в нём `prices.db` и `settings.json`. `BASE_DIR` и `TEMPLATES_DIR` считаются от `web/server.py`, поэтому шаблоны находятся независимо от `DATA_DIR`. Пакет — namespace package: `__init__.py` нет, `from web.server import create_app` работает как есть.

## Структура

- `web/server.py` (~2950 строк) — маршруты, хендлеры, фоновые работники, реестр магазинов `SHOP_REGISTRY` (37 магазинов) и состояние `scan_state` / `wave_state`.
- `web/templates/`: `index.html` (~440 КБ) — витрина с вкладками «Аномалии», «Скидки», «Лучшая цена», «AI-консультант», «Каталог», «Настройки», «Логи», модалками и админ-блоками; `monitoring.html` (~78 КБ) — центр мониторинга администратора; `privacy.html` — статическая страница «Как используются данные».
- `web/static/js/format.js` — клиентские `escapeHtml`, `fmtPrice`, `isStale`, `freshnessBadge`; подключается и в браузере, и в Node-тестах.

Jinja2 не используется: шаблонизатора в зависимостях нет. `index_handler`, `privacy_handler` и `monitoring_page_handler` отдают файлы через `web.FileResponse`, а `index.html` / `monitoring.html` — обычный HTML с клиентским JS (Tailwind и иконки магазинов подключаются с CDN). Данные приходят JSON-ом через `fetch`; серверной подстановки в HTML нет, экранирование выполняет `escapeHtml` на клиенте.

## Список функций `web/server.py`

В файле 112 функций верхнего уровня (93 `async`) и класс `LeaseLost`. 79 функций — aiohttp-хендлеры: 19 публичных, 10 под `@require_login`, 50 под `@require_admin`. Ниже перечислены все маршруты; соседние хендлеры с общим смыслом объединены в одну строку. Колонки: **функция** — точное имя из кода, **маршрут** — метод и путь, **доступ** — публичный / вход / админ, **что делает** — по коду.

### Страницы и метаданные

| Функция | Маршрут | Доступ | Что делает |
|---|---|---|---|
| `index_handler`, `privacy_handler` | `GET /`, `GET /privacy` | публичный | Отдают `index.html` и `privacy.html` через `FileResponse`. |
| `monitoring_page_handler` | `GET /monitoring` | публичный | Отдаёт `monitoring.html`; страница без данных, данные отдают только админские API. |
| `version_handler` | `GET /api/version` | публичный | Версия из `version.get_version_info()`; её проверяет Docker HEALTHCHECK. |

### Поиск и цены

| Функция | Маршрут | Доступ | Что делает |
|---|---|---|---|
| `best_price_handler` | `GET /api/best-price` | публичный; `live=1` — только после входа | Главный поиск: `q`, `live`, `shop`, `city`, `category`, `ai`, `min_price`, `max_price`, `exclude_acc`, `only_discount`, `mode`, `sort`, `neg`; при `v2=1` уходит в Search Core 2.0. AI-разбор — только вошедшим; `live` и AI требуют заголовок `X-KZPH-Request`. |
| `search_v2_handler` | `GET /api/v2/search` | публичный | Search Core 2.0: `q`, `city`, `condition`, `channel_type`, `min_price`, `max_price`, `sort`, `limit`; пишет событие в аналитику поиска. |
| `compare_model_offers_handler` | `GET /api/models/compare` | публичный | Сравнение цен на каноническую модель (`canonical_key` или `title`); помечает устаревшие предложения, считает min/max и выгоду. |
| `product_detail_handler` | `GET /api/products/{id}` | публичный | Карточка товара: `data_quality.annotate`, живое описание при отсутствии в БД, история цен (50 точек). |
| `fetch_product_description_live` | — | — | Вспомогательная: описание со страницы магазина через `product_details.get_description` (белый список доменов, лимиты). |

### Товары, магазины, скидки

| Функция | Маршрут | Доступ | Что делает |
|---|---|---|---|
| `stats_handler` | `GET /api/stats` | публичный | Сводка БД для пользователя и города, `scan_state` и план волн (`wave_info`). |
| `alerts_handler` | `GET /api/alerts` | публичный | Ценовые сбои; фильтры `city`, `type`, `limit` (1–500). |
| `dismiss_alert_handler`, `delete_alert_handler` | `POST /api/alerts/dismiss`, `DELETE /api/alerts/{id}` | админ | Скрывают алерт по `id` из JSON или из пути. |
| `deals_handler` | `GET /api/deals` | публичный | Акции и скидки: `shop`, `city`, `category`, `search`, `type`, `sort`, `limit`, `offset`. |
| `products_handler` | `GET /api/products` | публичный | Каталог: `shop`, `city`, `search`, `limit` (1–200), `offset`; отдаёт `products` и `total`. |
| `cities_handler`, `categories_handler` | `GET /api/cities`, `GET /api/categories` | публичный | Города `CITIES_KZ` и 20 популярных категорий витрины (`POPULAR_CATEGORIES`). |

### Авторизация и пользователь

| Функция | Маршрут | Доступ | Что делает |
|---|---|---|---|
| `me_handler` | `GET /api/me` | публичный | Текущий пользователь или гость: `user` (`_public_user`), настройки, `auth.bot_username`, `auth.dev_login`, магазины. |
| `telegram_login_handler`, `dev_login_handler` | `POST /api/auth/telegram`, `POST /api/auth/dev-login` | публичный; dev — только прямой loopback | Проверка подписи Telegram и лимит 20 попыток / 10 мин на IP; вход разработчика при `ALLOW_DEV_LOGIN=1` (`dev_login_allowed` запрещает прокси, LAN и `PUBLIC_ORIGIN`). |
| `logout_handler` | `POST /api/auth/logout` | публичный | Удаляет сессию из БД и сбрасывает cookie. |
| `export_my_data_handler`, `delete_my_account_handler` | `GET /api/me/export`, `POST /api/me/delete` | вход | Выгрузка своих данных JSON-файлом; удаление аккаунта только при `{"confirm": true}`. |
| `save_my_settings_handler`, `reset_my_settings_handler` | `POST /api/me/settings`, `POST /api/me/settings/reset` | вход | Валидация и сохранение личных настроек; сброс к `USER_DEFAULTS` с сохранением `USER_RESET_KEEP`; dev-админу Telegram принудительно выключается. |
| `test_my_telegram_handler` | `POST /api/me/test-telegram` | вход | Тестовое сообщение в Telegram; лимит 3/мин на пользователя. |
| `_public_user`, `_login_response`, `_initial_settings_for` | — | — | Вспомогательные: безопасная проекция пользователя, выпуск сессии и cookie, стартовые настройки администратора. |

### Наблюдения

| Функция | Маршрут | Доступ | Что делает |
|---|---|---|---|
| `my_watches_handler`, `create_watch_handler` | `GET /api/me/watches`, `POST /api/me/watches` | вход | Свои наблюдения и последние 30 срабатываний (лимиты и типы из `watches.py`); создание наблюдения, `ValueError` → 400. |
| `update_watch_handler`, `delete_watch_handler` | `POST /api/me/watches/{watch_id}`, `DELETE /api/me/watches/{watch_id}` | вход | Обновление и удаление только своего наблюдения; чужое → 404. |

### Мониторинг и администрирование

| Функция | Маршрут | Доступ | Что делает |
|---|---|---|---|
| `get_admin_config_handler`, `post_admin_config_handler` | `GET /api/admin/config`, `POST /api/admin/config` | админ | Чтение настроек с маскировкой ключей Gemini/OpenAI; запись, где `GUARDED_SETTINGS` отклоняется, маска не затирает реальный ключ, сбрасываются cooldown моделей. |
| `admin_users_handler`, `admin_block_user_handler` | `GET /api/admin/users`, `POST /api/admin/users/{user_id}/block` | админ | Пользователи без приватных полей; блокировка и разблокировка, администратора заблокировать нельзя. |
| `start_scan_handler` | `POST /api/scan/start` | админ | Запускает обход (`spawn_scan`) с проверкой списка магазинов; повторный запуск → `already_running`. |
| `start_category_scan_handler` | `POST /api/scan/category` | админ | Точечный обход мастер-категории, `tracked:ID` или `create_query:...`; иначе `spawn_scan(..., scan_type="category")`. |
| `sync_shopkz_yml_handler` | `POST /api/scan/shopkz-yml` | админ | Быстрый обход «Белого Ветра» через YML-фид. |
| `admin_categories_handler`, `admin_save_categories_handler` | `GET /api/admin/categories`, `POST /api/admin/categories` | админ | Мастер-категории, волны, дерево и отслеживаемые категории; сохранение `enabled_categories`, `hot_categories`, `wave_mode`, `wave_size`. |
| `get_tracked_categories_handler`, `add_tracked_category_handler` | `GET /api/categories/tracked`, `POST /api/categories/tracked` | админ | Список отслеживаемых категорий и ручное добавление категории или запроса. |
| `toggle_tracked_category_handler`, `toggle_tracked_category_hot_handler` | `POST /api/categories/tracked/{id}/toggle`, `POST /api/categories/tracked/{id}/hot` | админ | Включение в волнах и признак «горячей» категории (с лимитом мест). |
| `delete_tracked_category_handler`, `scan_single_tracked_category_handler` | `DELETE /api/categories/tracked/{id}`, `POST /api/categories/tracked/{id}/scan` | админ | Удаление категории и её немедленное обновление. |
| `admin_categories_tree_handler`, `set_category_parent_handler` | `GET /api/admin/categories/tree`, `POST /api/categories/tracked/{id}/parent` | админ | Иерархия групп и перенос категории в мастер-группу. |
| `admin_categories_ai_classify_handler` | `POST /api/admin/categories/ai-classify` | админ | AI-классификация неразобранных категорий каталога. |
| `admin_catalog_reset_handler`, `admin_shops_handler` | `POST /api/admin/catalog/reset`, `GET /api/admin/shops` | админ | Сброс каталога с резервной копией (запрещён во время обхода); состояние обхода по магазинам и статистика уведомлений. |
| `monitoring_overview_handler`, `monitoring_shop_handler` | `GET /api/admin/monitoring`, `GET /api/admin/monitoring/shop/{key}` | админ | Обзор Monitoring Center (`monitoring.overview`) и детали магазина; неизвестный ключ → 404. |
| `monitoring_events_handler`, `monitoring_incidents_handler` | `GET /api/admin/monitoring/events`, `GET /api/admin/monitoring/incidents` | админ | Журнал событий (`component`, `severity`, `type`, `shop`, `scan_id`, `limit`, `before`) и инциденты, открытые первыми. |
| `monitoring_backups_handler`, `monitoring_notices_handler`, `monitoring_environment_handler` | `GET /api/admin/monitoring/backups`, `GET /api/admin/monitoring/notices`, `GET /api/admin/monitoring/environment` | админ | Бэкапы и их проверка, обращения магазинов (`shop_notices.py`), окружение и пульс работников; только чтение. |
| `scheduler_rollout_status_handler`, `scheduler_rollout_switch_handler` | `GET /api/admin/scheduler/rollout`, `POST /api/admin/scheduler/rollout` | админ | Состояние поэтапного включения и переключение шага: вперёд по одному, назад и в «выключено» — всегда. |
| `monitoring_scheduler_handler`, `monitoring_matching_handler` | `GET /api/admin/monitoring/scheduler`, `GET /api/admin/monitoring/matching` | админ | Предложения теневого планировщика и теневой отчёт сопоставления, `days` 1–90; обходы не запускают. |
| `monitoring_search_handler` | `GET /api/admin/monitoring/search` | админ | Отчёт по поиску: дневные агрегаты, доля успеха, проблемные запросы. |
| `admin_daily_digest_handler`, `admin_daily_telegram_handler` | `GET /api/admin/monitoring/daily`, `POST /api/admin/monitoring/daily/telegram` | админ | Суточная сводка (`day`, `tz`, `refresh`) и выключатель её отправки в Telegram с часом отправки. |
| `logs_handler`, `logs_clear_handler`, `logs_export_handler` | `GET /api/logs`, `POST /api/logs/clear`, `GET /api/logs/export` | админ | Кольцевой буфер логов (инкрементально по `since`, фильтры `limit`, `level`, `q`), очистка и скачивание `.txt`. |

### AI

| Функция | Маршрут | Доступ | Что делает |
|---|---|---|---|
| `ai_status_handler` | `GET /api/ai/status` | публичный | Настроен ли AI и какой провайдер; ключи не отдаются. |
| `ai_consultant_handler` | `POST /api/ai/consultant` | вход | AI-консультант: `message` ≤ 4000, история ≤ 8 реплик; лимит 10/мин; ошибки без деталей. |
| `admin_ai_models_handler`, `admin_ai_prices_handler` | `GET /api/admin/ai/models`, `GET /api/admin/ai/prices` | админ | Модели у провайдеров (кэш 5 мин, `refresh=1`, только имена); подсказка цен по публичному прайсу до 10 моделей, ничего не сохраняет. |
| `monitoring_matching_ai_handler`, `monitoring_ai_usage_handler` | `POST /api/admin/monitoring/matching/ai`, `GET /api/admin/monitoring/ai-usage` | админ | Режим AI-сопоставления `off → shadow → on` (`catalog_ai.MODES`); расходы AI по задачам и моделям за `days` 1–90. |
| `admin_daily_summary_handler` | `POST /api/admin/monitoring/daily/summary` | админ | Пересказ сводки словами; число вне отчёта отменяет пересказ. |
| `admin_assistant_handler` | `POST /api/admin/assistant` | админ | AI-помощник администратора: только чтение отчётов, `question` и `days` 1–90. |

### API v2

| Функция | Маршрут | Доступ | Что делает |
|---|---|---|---|
| `search_quality_analytics_handler` | `GET /api/v2/analytics/search-quality` | админ | KPI поиска, топ-запросы и сигналы для discovery (`city`, `since`, `limit`). |
| `scheduler_plan_handler` | `GET /api/v2/scheduler/plan` | админ | Последний план Adaptive Scheduler 2.0 (`ADAPTIVE_SCHEDULER_2=off/shadow/on`, по умолчанию shadow) и список категорий из него. |
| `get_discovery_tasks_handler`, `generate_discovery_tasks_handler` | `GET /api/v2/discovery/tasks`, `POST /api/v2/discovery/generate` | админ | Задачи adaptive discovery (`status`, `city`, `min_priority`, `limit`) и генерация задач по сигналам спроса. |
| `resolve_discovery_task_handler` | `POST /api/v2/discovery/tasks/{task_id}/resolve` | админ | Меняет статус задачи (`status`, `notes`, `candidates_delta`), проверяет допустимые статусы. |

### Служебные и фоновые

| Функция | Что делает |
|---|---|
| `create_app`, `telemetry_middleware`, `background_tasks` | Фабрика приложения (БД, middleware, маршруты, static); middleware телеметрии (route-шаблон без параметров и тела) и запуск/остановка фоновых задач. |
| `auto_scan_background_worker` | Волновой автообход за 24 ч, контроль свежести магазинов и пульс окружения. |
| `_do_scan_task`, `_scan_task_body` | Один обход: флаг `is_running`, аренда планировщика, телеметрия, сброс состояния. |
| `_scan_shop`, `_scan_shop_categories` | Обход категорий одного магазина с фильтром `target_categories`. |
| `_save_and_detect`, `_process_anomaly`, `_process_anomaly_sync`, `_record_price_changes` | Пакетное сохранение, алерты с защитой от дублей, сводка изменений цен. |
| `queue_titles_for_ai`, `process_ai_pending`, `ai_normalize_background_worker` | Очередь названий без канонического ключа и фоновая AI-нормализация. |
| `spawn_scan`, `_ensure_lease`, `_keep_scheduler_lease`, `LeaseLost` | Запуск задачи обхода и удержание аренды; потеря аренды останавливает запись. |
| `enabled_shop_keys`, `_failed_shop_keys`, `_record_wave_outcome`, `apply_adaptive_order`, `check_adaptive_rollback` | Включённые и упавшие магазины, итог круга, порядок волны по шагу rollout, автооткат при ухудшении метрик. |
| `get_categories_overview`, `_monitoring_registry`, `_int_param` | Обзор мастер-категорий, реестр для мониторинга, безопасный разбор числовых query-параметров. |

## Инварианты и безопасность

- **CSRF/Origin.** `auth_middleware` для всех не-GET/HEAD/OPTIONS требует точного совпадения `Origin` с `expected_origin(request)`, иначе 403. `expected_origin` — это `PUBLIC_ORIGIN` (проверяется при импорте `auth.py`) либо loopback-origin без доверия произвольному `Host` и прокси-заголовкам. Запросы с `Sec-Fetch-Site: cross-site` отклоняются. Cookie `kzph_session`: `HttpOnly`, `SameSite=Lax`, `Secure` при HTTPS, срок из `SESSION_TTL_DAYS`.
- **Заголовок `X-KZPH-Request`.** `GET /api/best-price` с `live=1` или AI расходует квоту и пишет кэш, поэтому требует `browser_read_action_allowed`: заголовок `X-KZPH-Request: 1`, тот же Origin (или его отсутствие) и не cross-site. Гостю `live` запрещён (401).
- **Rate limiting.** `RateLimiter` — скользящее окно в памяти процесса: `search_limiter` 60/мин на IP (`/api/best-price`, `/api/v2/search`), `live_search_limiter` 10/10 мин на пользователя, `consultant_limiter` 10/мин на пользователя (`/api/ai/consultant`), `telegram_test_limiter` 3/мин, `auth_limiter` 20/10 мин на IP. Ответ 429 с `Retry-After`.
- **Adaptive Scheduler 2.0.** `ADAPTIVE_SCHEDULER_2=off|shadow|on` (по умолчанию shadow). Shadow считает и сохраняет план по сигналам спроса и PENDING-задачам discovery в `scheduler_plans`, но волну не меняет; `on` добавляет к волне не больше двух категорий, не меняя ротацию и порядок. Ошибка планировщика не ломает обход.
- **Сжатие и кэш.** `gzip_middleware` сжимает обычные ответы больше 1 КБ только при `Accept-Encoding: gzip`, не трогает потоковые и уже сжатые, ставит `Vary: Accept-Encoding`. `/api/stats` кэшируется на 30 с по ключу (пользователь/гость, город); `scan_state` и `wave_info` считаются заново в каждом ответе, кэш сбрасывается после обхода, смены настроек и разбора алертов. Остальные тяжёлые списки (`/api/deals`, `/api/products`, `/api/alerts`) не кэшируются, чтобы не показывать устаревшие цены.
- **Права.** `require_admin` даёт 401 без входа и 403 без прав; `require_login` — 401. Админские данные и действия только под `require_admin`; личные — под `require_login` и всегда по `request["user"]["id"]`, без id из запроса.
- **Секреты.** `/api/admin/config` маскирует `gemini_api_key` и `openai_api_key`; POST не затирает реальный ключ маской; `/api/admin/ai/models` отдаёт только имена моделей. Тексты ошибок проходят через `redact_secrets`, наружу не уходят токены, cookie и внутренние детали.
- **Персональные данные.** Наружу отдаётся только `_public_user` (id, имя, username, фото, `is_admin`). Middleware ошибок пишет route-шаблон без параметров и тела, но аналитика поиска отдельно хранит запросы — это описано в `privacy.html`. Логи администратора могут содержать данные: не добавляйте в них токены, cookie и лишние персональные сведения.
- **Один обход на базу.** Аренда планировщика в БД (`SCHEDULER_OWNER`, 180 с, продление каждые 60 с) не даёт двум процессам обходить магазины одновременно; потеря аренды (`LeaseLost`) останавливает запись.
- **Статика и страницы.** `/static/` отдаётся без листинга каталога; `/monitoring` — только оболочка, все данные за `require_admin`. `index.html` вставляет данные через `escapeHtml` — не ломайте это при добавлении разметки.

## Как добавить эндпоинт

1. **Маршрут.** Добавьте `@routes.get("/api/...")` (или `post`/`delete`) рядом с тематическими хендлерами в `web/server.py`; путь и метод должны быть уникальны.
2. **Хендлер.** Напишите `async def ..._handler(request)`, верните `web.json_response(...)`; для ошибок — 400/401/403/404/429/500 с коротким `message`. Числовые query-параметры разбирайте через `_int_param`, тяжёлые синхронные вызовы БД — через `asyncio.to_thread`.
3. **Права.** Админские данные и любые изменения — `@require_admin`, личные — `@require_login`. Порядок декораторов как в коде: сначала `@routes...`, под ним проверка прав. Публичными оставляйте только чтение каталога.
4. **Защита.** Для GET, который тратит квоту или пишет данные, проверьте `browser_read_action_allowed(request)`. Для не-GET ничего добавлять не нужно: Origin уже проверяет `auth_middleware`; не ослабляйте эту проверку.
5. **Контракт ответа.** Единообразные `status`/`error`, никаких секретов и лишних персональных полей; текст исключений — только через `redact_secrets`.
6. **Фронтенд.** Добавьте вызов в `web/templates/index.html` (или `monitoring.html`): функцию-загрузчик, `fetch`, вывод через `escapeHtml`; админские блоки помечайте `admin-only`.
7. **Тест.** Веб-тесты — в `tests/web/` (`test_security_headers.py`, `test_ui_data.py`, `test_auth_privacy.py`, `test_browser_server.py`), фронтенд — в `tests/frontend/*.cjs`. Новый тест импортирует первым `from tests import test_support` — он подменяет `DATA_DIR` на временный, чтобы не тронуть рабочую `prices.db`; `BrowserTestClient` из `tests/test_support.py` сам добавляет `Origin` и `X-KZPH-Request`.

## Где что искать

| Что ищем | Файл шаблона | Маршруты вкладки |
|---|---|---|
| Вкладка «Аномалии» (ценовые сбои) | `index.html`, `#tabAnomalies` | `GET /api/alerts`, `GET /api/stats`, `POST /api/alerts/dismiss`, `DELETE /api/alerts/{id}` |
| Вкладка «Скидки» | `index.html`, `#tabDiscounts` | `GET /api/deals`, `GET /api/stats` |
| Вкладка «Лучшая цена» | `index.html`, `#tabBestprice` | `GET /api/best-price`, `GET /api/v2/search`, `GET /api/models/compare`, `GET /api/products/{id}` |
| Вкладка «AI-консультант» | `index.html`, `#tabConsultant` | `POST /api/ai/consultant`, `GET /api/ai/status` |
| Вкладка «Каталог» | `index.html`, `#tabCatalog` | `GET /api/products`, `GET /api/cities`, `GET /api/categories` |
| Вкладка «Настройки» (личные и админ-блоки) | `index.html`, `#tabSettings` | `/api/me`, `/api/me/settings`, `/api/me/watches`, `/api/admin/config`, `/api/admin/categories`, `/api/categories/tracked`, `/api/admin/ai/models`, `/api/admin/ai/prices` |
| Вкладка «Логи» | `index.html`, `#tabLogs` | `GET /api/logs`, `POST /api/logs/clear`, `GET /api/logs/export` |
| Вход, выход, шапка и роль | `index.html`, шапка | `/api/me`, `/api/auth/telegram`, `/api/auth/dev-login`, `/api/auth/logout` |
| Центр мониторинга | `monitoring.html` | `/api/admin/monitoring*`, `/api/admin/scheduler/rollout`, `/api/admin/assistant`, `/api/scan/start` |
| Политика данных | `privacy.html` | нет, статический текст |

Связанные документы: [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md), [`docs/SECURITY_AND_PRIVACY.md`](../docs/SECURITY_AND_PRIVACY.md).
