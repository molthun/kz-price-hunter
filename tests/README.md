# Тесты KZ Price Hunter

Все проверки проекта собраны в этом каталоге и разложены по функциональным блокам.
Корень проекта больше не содержит `test_*.py`: новые наборы добавляются внутрь `tests/<блок>/`.

Сейчас это **61 тестовый модуль Python (1547 проверок)** и **16 браузерных наборов** Playwright.

## Принципы

- Каждый тестовый модуль первым импортом делает `from tests import test_support` — это фиксирует
  временный `DATA_DIR` до первого импорта `config`. Без этого тесты писали бы в рабочую `prices.db`.
- Рабочая база `prices.db`, `settings.json` и `backups/` тестами не используются; данные создаются
  во временном каталоге и удаляются после прогона.
- Тесты — не «зелёная лампочка»: они проверяют поведение и контракты, поэтому часть модулей
  проверяет сам код проекта (см. «Контрактные проверки» ниже).
- Внешняя сеть, реальные AI-провайдеры и Telegram в тестах не вызываются — только mocks.

## Структура

| Каталог | Что проверяет | Примеры |
|---|---|---|
| `tests/core/` | конфигурация, схема и целостность БД, репозитории, доменные модели, identity/offer-namespace, миграции, валидаторы, timezone, release gate | `test_repositories.py`, `test_config_and_ids.py` |
| `tests/search/` | Search Core 2.0, query parser, FTS, агрегация продавцов, аналитика качества поиска и приватность запросов, UI 2.0 | `test_search_core_v2.py`, `test_search_privacy_v2.py` |
| `tests/sellers/` | продавцы и каналы, condition, Seller Discovery и профилирование источников, Adaptive Discovery | `test_seller_matcher.py`, `test_adaptive_discovery_v2.py` |
| `tests/scrapers/` | контракт скраперов, надёжность, полнота обхода, TLS | `test_scraper_contract.py`, `test_scraper_reliability.py` |
| `tests/monitoring/` | телеметрия, качество и свежесть данных, Центр мониторинга, backup health, environment, scaling, daily digest | `test_monitor.py`, `test_telemetry.py` |
| `tests/scheduler/` | теневой планировщик, rollout, аренды и устойчивость | `test_scheduler_rollout.py`, `test_shadow_read.py` |
| `tests/ai/` | AI-роутер и модели, цены, каталог и качество, matching, админ-помощник | `test_ai_router.py`, `test_catalog_quality.py` |
| `tests/notifications/` | Telegram-уведомления, наблюдения (watches), бот, обращения магазинов | `test_watches.py`, `test_notifications.py` |
| `tests/web/` | HTTP API и auth, browser-фикстура сервера, security headers/logging, UI data, self-service приватности | `test_ui_data.py`, `test_auth_privacy.py` |
| `tests/frontend/` | браузерные регрессии интерфейса (Playwright + Chrome), не Python | `test_frontend*.cjs` |

`tests/test_support.py` — общий помощник (изоляция `DATA_DIR`, `BrowserTestClient`, `REPO_DIR`);
это не тест, хотя имя подходит под шаблон discovery.

## Как запускать

```bash
# Полный Python-набор (рабочая prices.db не затрагивается)
DATA_DIR=$(mktemp -d) ./venv/bin/python -m unittest discover -s . -p "test_*.py"

# Один функциональный блок
DATA_DIR=$(mktemp -d) ./venv/bin/python -m unittest discover -s tests/search -t . -p "test_*.py"

# Один модуль
DATA_DIR=$(mktemp -d) ./venv/bin/python -m unittest tests.search.test_search_core_v2 -v

# Браузерные регрессии (нужны Node.js, Playwright и Chrome)
for f in tests/frontend/test_frontend*.cjs; do echo "== $f"; node "$f" || exit 1; done
```

`-t .` обязателен при запуске отдельного каталога: верхним уровнем должен оставаться корень
репозитория, иначе модули импортируются без пакета `tests` и ломается `from tests import test_support`.

### Быстрая проверка вместо всего набора

Полный прогон занимает около 70 секунд. Перед коммитом в конкретный блок достаточно контрактных
тестов и тестов изменённого блока — это единицы секунд:

```bash
# Контракты тестовой обвязки (изоляция DATA_DIR, валидаторы, импорты)
DATA_DIR=$(mktemp -d) ./venv/bin/python -m unittest \
    tests.core.test_config_and_ids tests.core.test_validators_are_used \
    tests.core.test_import_contract

# Только изменённый блок, например поиск
DATA_DIR=$(mktemp -d) ./venv/bin/python -m unittest discover -s tests/search -t . -p "test_*.py"
```

Полный набор и браузерные регрессии всё равно обязательны перед выпуском: их запускает CI
(`.github/workflows/docker-publish.yml`), и без них образ не публикуется.

## Куда добавить новый тест

1. Выберите блок по функции, а не по типу файла: тест поиска — в `tests/search/`, скрапера — в
   `tests/scrapers/`. Не создавайте `test_*.py` в корне.
2. Первый импорт модуля — `from tests import test_support  # noqa: F401`.
3. Пути к ресурсам репозитория (`docs/`, `scripts/`, `web/`, `requirements.txt`) считайте от
   `test_support.REPO_DIR`, а не от `__file__`: тест может лежать на любой глубине.
4. Не обращайтесь к рабочей `prices.db` и не выставляйте `DATA_DIR` в корень репозитория.
5. Запустите весь набор: `DATA_DIR=$(mktemp -d) ./venv/bin/python -m unittest discover -s . -p "test_*.py"`.

## Контрактные проверки

Эти тесты проверяют не только код, но и сами тесты — они первыми падают при нарушении правил:

- `tests/core/test_config_and_ids.py` — первый импорт каждого тестового модуля: `from tests import test_support`.
- `tests/core/test_validators_are_used.py` — каждый публичный валидатор/очистка (`is_*`, `validate_*`,
  `purge_*`, `cleanup_*`, `enforce_*`) из `domain/` и `repositories/` должен вызываться рабочим кодом,
  а не только собственным тестом.
- `tests/core/test_import_contract.py` — имена в аннотациях должны быть импортированы (совместимость
  со старыми версиями Python).
- `tests/core/test_release_gate.py` — CI публикует образ только после всех обязательных проверок.

## Браузерные тесты

`tests/frontend/test_frontend*.cjs` — Playwright-сценарии на реальном Chrome: поднимают HTML шаблона,
заглушают API, проверяют DOM и отсутствие JS-ошибок. Путь к корню репозитория вычисляется как
`ROOT = path.resolve(__dirname, '..', '..')`. `test_frontend_auth.cjs` поднимает настоящий сервер
фикстурой `python -m tests.web.test_browser_server` на временной БД.

В CI тесты Python запускаются шагом `test`, браузерные — шагом `frontend`; оба обязательны для
публикации образа (`.github/workflows/docker-publish.yml`).
