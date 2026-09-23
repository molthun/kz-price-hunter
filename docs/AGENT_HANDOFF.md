# Рабочий реестр и передача задач

Общие правила: [AGENTS.md](../AGENTS.md). Требования и проверки: [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md).

## Текущая контрольная точка

- Дата: 2026-09-24 01:40, Asia/Almaty. Исполнитель — Claude. Выпуск **v5.13.0** по поручению владельца.
- Аудит Codex пройден полностью: M01–M11 закрыты (четыре круга), пачка принята как READY по коду.
  Отчёты — [BATCH_AUDIT_CODEX.md](BATCH_AUDIT_CODEX.md), разбор исправлений — в
  [AUDIT_REQUEST_BATCH.md](AUDIT_REQUEST_BATCH.md).
- Перед выпуском отрепетированы обновление и возврат: база, созданная кодом тега `v5.12.0`, обновлена
  новым кодом (схема 5 → 5), и на обновлённой копии код v5.12.0 выполнил все запросы приложения.
  Значит, откат на прежний образ не требует восстановления базы.
- Состояние выпуска: версия поднята до 5.13.0, CHANGELOG и дорожная карта закрыты разделом релиза,
  README обновлён. Ветка `dev/p07-watch-v2` сливается в `main`, тег `v5.13.0`.
- Все новые возможности выключены по умолчанию: шаг адаптивного планировщика `off`, отправка сводки в
  Telegram выключена, режим AI-сопоставления `off`. Первые включения — отдельные решения владельца.
- Не проверялось до выпуска: Docker-образ и контейнерный smoke на машине разработчика, живой прогон
  GitHub Actions (новые задания шлюза пойдут впервые), реальный pip-audit, настоящие вызовы моделей и
  Telegram, замер AI-части на живой модели, production canary.
- **Следующий точный шаг: дождаться сборки образа и подтвердить версию на проде чтением
  /api/version, /api/stats, /monitoring; затем зафиксировать результат здесь.**

## Реестр этапов

Статусы: `TODO` → `IN_PROGRESS` → `REVIEW` → `READY` → `DEPLOYED`.
Дополнительно: `HANDOFF` — подготовлен подхват; `BLOCKED` — указан конкретный блокер. READY означает принятую реализацию, DEPLOYED — подтверждение на целевом окружении. Поля автора, аудитора и владельца приёмки заполняются по факту.

| Этап | Статус | Исполнитель | Аудит | Прод |
|---|---|---|---|---|
| P00 | READY | Antigravity | Самопроверка (требует review) | Не проверен (нет прямого доступа) |
| P01 | DEPLOYED (v5.8.0) | Antigravity → Claude | Codex: A01–A05 и B01/B02 закрыты | v5.8.0 на shop.molthun.ru с 19:41 (проверка чтением /api/version, /api/stats, /api/products) |
| P02 | DEPLOYED (v5.9.0) | Claude | Codex: C01–C03 закрыты | v5.9.0 на shop.molthun.ru с 23:46 (проверка чтением /api/version, /api/stats, /api/best-price, /api/products) |
| P03 | DEPLOYED (v5.10.0) | Claude | Codex: D01/D02 закрыты | v5.10.0 на shop.molthun.ru с 13:16 (проверка чтением /api/version, /api/stats, /api/deals, /monitoring) |
| P04 | READY (аудит кода) | Claude | Codex: E01/E02 закрыты | v5.11.0 на shop.molthun.ru с 18:26 (проверка чтением /api/version, /api/stats, /api/deals, /monitoring) |
| P05 | DEPLOYED (v5.10.0) | Claude | Codex: замечаний нет | v5.10.0 на shop.molthun.ru с 13:16 (проверка чтением /api/version, /api/stats, /api/deals, /monitoring) |
| P06 | DEPLOYED (v5.11.0) | Claude | Codex: F01–F04 закрыты | v5.11.0 на shop.molthun.ru с 18:26 (проверка чтением /api/version, /api/stats, /api/deals, /monitoring) |
| P07 | V1 DEPLOYED (v5.11.0); V2 DEPLOYED (v5.13.0) | Claude | Codex: G01/G02, M10 закрыты | Проверяется после выката |
| P08 | DEPLOYED (v5.11.0) | Claude | Codex: H01/H02/H03 закрыты | v5.11.0 на shop.molthun.ru с 18:26 (проверка чтением /api/version, /api/stats, /api/deals, /monitoring) |
| P09 | DEPLOYED (v5.11.0); AI-часть DEPLOYED (v5.13.0) | Claude | Codex: I01/I02, M11 закрыты | Проверяется после выката |
| P10 | DEPLOYED (v5.12.0) | Claude → Codex | Claude: просмотр исправлений J01–J03 без замечаний | v5.12.0 на shop.molthun.ru с 14:52 (проверка чтением /api/version, /api/stats, /monitoring) |
| P11 | DEPLOYED (v5.13.0) | Claude | Codex: M01–M03 закрыты | Проверяется после выката |
| P12 | DEPLOYED (v5.13.0) | Claude | Codex: M04 закрыт | Проверяется после выката |
| P13 | DEPLOYED (v5.13.0) | Claude | Codex: M04–M06 закрыты | Проверяется после выката |
| P14 | DEPLOYED (v5.12.0) | Claude → Codex → Claude | Claude: просмотр исправлений K01/K02; совместный suite OK | v5.12.0 на shop.molthun.ru с 14:52 (проверка чтением /api/version, /api/stats, /monitoring) |
| P15 | DEPLOYED (v5.12.0) | Claude | Codex: L01–L04 закрыты в проверенном объёме | v5.12.0 на shop.molthun.ru с 14:52 (проверка чтением /api/version, /api/stats, /monitoring) |
| P16 | DEPLOYED (v5.13.0) | Claude | Codex: M07–M09 закрыты | Проверяется после выката |

## Карточка задачи: T01 (изоляция тестов)

```text
ID / этап: T01. Изоляция тестов от рабочей prices.db (вне нумерации P, дефект найден при P01)
Цель и критерии приёмки:
  - Любой тестовый модуль в любом порядке запуска использует временный DATA_DIR/БД.
  - Замерить mtime и размер prices.db / prices.db-wal до и после полного прогона — без изменений.
  - Все тесты проходят (299 на d3d3fde).
Основание/поручение владельца: прямое поручение владельца Claude (2026-09-19). Не удалять и не восстанавливать prices.db без указания.
Статус: REVIEW (реализация завершена; не влита в dev/p00-baseline — там идёт аудит P01 Codex)
Исполнитель / следующий: Claude (завершил) / аудит + слияние по решению владельца
Начало и checkpoint: 2026-09-19 18:50 – 19:10 (Asia/Almaty)
Checkout / ветка / базовый HEAD: .claude/worktrees/affectionate-lehmann-490dc0 / claude/affectionate-lehmann-490dc0 /
  начато от d3d3fde, перебазировано на 5f9ce5f (конфликт только в ROADMAP_AND_LOG.md и этой карточке; решён)
Область изменений (файлы): test_support.py, все test_*.py (одна строка импорта в начале), test_config_and_ids.py (контрактный тест),
  test_monitor.py (сброс флага аренды), ROADMAP_AND_LOG.md, docs/AGENT_HANDOFF.md. Прод-код не менялся.
Чужие изменения, замеченные до начала: нет. Во время работы в основном checkout появились коммиты 6fc7e04..5f9ce5f и
  незакоммиченная правка docs/AGENT_HANDOFF.md (аудит Codex) — основной checkout не трогал.
Причина дефекта (подтверждено): config вычисляет DB_PATH один раз при первом импорте. При discovery первым идёт
  test_ai_category_classification (DATA_DIR не задаёт) → DB_PATH = <repo>/prices.db для всего прогона. Воспроизведено:
  прогон на d3d3fde создал prices.db и settings.json в корне чистого checkout.
Сделано:
  1. test_support.py: если DATA_DIR не задан или равен корню репо — mkdtemp + os.environ["DATA_DIR"] (удаляется atexit);
     если config уже импортирован с DATA_DIR = корень репо — RuntimeError (громкий отказ вместо тихой записи).
  2. Каждый test_*.py первым импортом делает `import test_support` → изоляция не зависит от порядка.
     Существующие _TMP/DATA_DIR в модулях оставлены (не мешают; subprocess в test_auth_privacy получает временный DATA_DIR).
  3. Контракт test_config_and_ids.TestDataIsolationTest: первый импорт каждого test_*.py — test_support (AST);
     config.DB_PATH, database.DB_PATH, config.SETTINGS_FILE вне корня репо.
  4. Найдена вторая зависимость от порядка (была и на d3d3fde): web.server._lease_state["lost"] остаётся True после
     тестов аренды; в обратном порядке 2 теста test_monitor.TestReliability падали LeaseLost. setUp теперь сбрасывает флаг.
Проверки (macOS, Python 3.14.7 venv основного checkout, DATA_DIR снят через env -u, 2026-09-19):
  - discovery: Ran 302 tests OK (300 upstream + 2 новых контрактных).
  - Обратный порядок модулей и перемешанные (seed 1–6): 302/301 OK, 0 ошибок. На d3d3fde обратный порядок: 2 ошибки LeaseLost.
  - Каждый модуль отдельно: OK.
  - Корень worktree после всех прогонов: prices.db, settings.json, backups/ не создаются (до правки создавались).
  - Приёмка mtime/размер: экспорт HEAD в scratchpad + sentinel prices.db (WAL) с mtime 12:00 → после полного прогона
    prices.db 8192 байт и prices.db-wal 0 байт, mtime не изменились.
  - Негативные: `import config; import test_support` → RuntimeError; модуль без test_support → контракт падает.
Не проведено:
  - Приёмка на реальной prices.db основного checkout (148 738 048 байт, mtime 18:55:19, WAL нет): там сейчас работает Codex,
    ветку не вливал и тесты там не запускал. Сделать после слияния: записать stat prices.db*, прогнать suite, сравнить.
  - Тестовые данные, уже попавшие в локальную prices.db (303→725 товаров), не чистились — решение владельца.
Следующий точный шаг: ревью диффа 5f9ce5f..claude/affectionate-lehmann-490dc0, слияние в dev/p00-baseline (fast-forward,
  если там не будет новых коммитов), затем приёмка на реальной prices.db по пункту выше.
Готовность к выпуску: правки только тестов; версию не повышал.
```

## Карточка текущей задачи: P01

```text
ID / этап: P01. Telemetry Foundation
Цель и критерии приёмки:
  - Исправить дефект недетерминированного fallback-ID Arbuz на стабильное хеширование (sha256).
  - Реализовать модуль структурированной телеметрии telemetry.py (события, HTTP-метрики, p95).
  - Добавить таблицы телеметрии (изначально миграция 6; по решению владельца 19.09 — без повышения schema_version, остаётся 5).
  - Инструментировать HTTP-клиент (scrapers/http.py) и цикл обхода (web/server.py, main.py).
  - Обеспечить принцип Fail-Open (отказ телеметрии не ломает сбор и сервис).
  - Очистка данных (санитизация URL от токенов, redact_secrets).
  - Автотесты: p95, fake-источники 200->200->429, 200->403, timeout, retention, отсутствие регрессий.
Основание/поручение владельца: Утверждённый implementation_plan.md (в репозитории отсутствует), поручение: «перейти к этапу P01»;
  подхват Claude по команде владельца «Подхвати текущую задачу по AGENTS.md и docs/AGENT_HANDOFF.md».
Статус: READY по аудиту кода — A01–A05 и B01/B02 закрыты Codex; проверки выпуска/прода не выполнены
Исполнитель / предыдущий исполнитель / следующий: Claude (завершил) / Antigravity / Codex — независимый аудит
Начало и checkpoint: 2026-09-19 18:18 (Antigravity), подхват Claude 18:36, передача на аудит 18:56 (Asia/Almaty)
Checkout / ветка / базовый HEAD / текущий HEAD:
  - Checkout: /Users/molthun/Documents/kz-price-hunter
  - Ветка: dev/p00-baseline
  - Базовый HEAD: 12672ec
  - Текущий HEAD: коммит передачи на аудит (код P01: feeff43 + 6fc7e04; документация: d3d3fde, 8d87d0c, 93e6d8f)
Зависимости: P00 (READY, ожидает аудита)
Область изменений (файлы):
  telemetry.py (новый), test_telemetry.py (новый), database.py, scrapers/http.py, scrapers/arbuz.py,
  web/server.py, main.py, test_offer_identity.py, test_offer_namespace.py, ROADMAP_AND_LOG.md, docs/AGENT_HANDOFF.md
Чужие изменения, замеченные до начала: незакоммиченные правки Antigravity (18:19–18:20) в database.py, scrapers/arbuz.py,
  scrapers/http.py, web/server.py, telemetry.py; карточка их не отражала. Приняты как основа, проверены и доработаны.
Сделано (конкретное поведение):
  Antigravity: sha256 fallback-ID Arbuz; черновик telemetry.py; таблицы и миграция 6; инструментирование _limited
  и событий категорий в _scan_shop_categories (contextvars shop/category).
  Claude, исправленные дефекты черновика:
  1. shop = NULL в UNIQUE агрегата: ON CONFLICT не срабатывал, каждая строка — отдельный запрос. Теперь shop NOT NULL DEFAULT ''.
  2. Потоковая выгрузка shop.kz (Session stream=True): _limited читал response.content — весь YML в память до проверки
     MAX_FEED_BYTES. Теперь для stream только Content-Length; флаг stream передают request/get/post/Session.
  3. latency включала ожидание паузы/слота лимитера; теперь только send().
  4. HostCooldown (запрос не отправлялся) считался как запрос 429; теперь отдельный счётчик cooldown_rejections.
  5. «Резервуар» хранил первые 500 задержек бакета (смещённый p95); теперь Algorithm R поверх БД.
  6. Синхронная запись в SQLite на каждый HTTP-запрос и событие (busy_timeout 15 с, в т.ч. из event loop).
     Теперь запись в память, фоновый сброс батчами раз в 10 с (telemetry.start() в background_tasks и main.py),
     busy_timeout 2 с, буферы ограничены (dropped_* счётчики). Сброс в конце обхода убран при исправлении A04.
  7. Нет minute-бакетов (требование плана) — добавлены. Нет connection_errors, 3xx, Retry-After max — добавлены.
  8. prune сравнивал строки разных форматов; теперь cutoff в формате каждого бакета, отдельные сроки.
  9. scan_id не устанавливался; теперь _do_scan_task задаёт current_scan_id, пишет scan_start/scan_end,
     ошибки категорий и падения магазинов — scan_error.
  10. Флаг TELEMETRY_ENABLED=0 для отключения.
  Retention (дни): events 30, samples 7, minute 7, hour 90, day 365 (ориентиры P15; p95 остаётся в агрегате после удаления samples).
Незавершённые правки: нет (всё в коммите P01 на dev/p00-baseline).
Проверки (macOS, Python 3.14.7 venv, 2026-09-19):
  - ./venv/bin/python -m unittest test_telemetry → 21 тест OK (p95 не среднее p95; 200→200→429; 200→403; timeout/connection;
    latency без ожидания лимитера (реальная пауза 0.4 с); stream не читается; слияние агрегатов между flush;
    ограниченный резервуар; retention по бакетам; fail-open при сломанной БД/сервисе; флаг отключения; очистка секретов;
    scan_id через create_task/to_thread; миграция 6 на пустой БД идемпотентна).
  - Полный suite: ./venv/bin/python -m unittest discover -s . -p "test_*.py" → Ran 299 tests, OK.
  - После отказа от миграции 6: DATA_DIR=<scratchpad>/suite ./venv/bin/python -m unittest discover -s . -p "test_*.py"
    → Ran 300 tests, OK (с временным DATA_DIR, чтобы не писать в prices.db). test_telemetry.SchemaTest: новая БД и
    существующая v5 с данными получают 3 таблицы, schema_version = 5, бэкап не создаётся.
    Базовый HEAD 12672ec в отдельном worktree: 278 OK. Два теста миграций обновлены под версию 6
    (ожидали [1..5] и бэкап pre-v5); падение test_scaling было каскадом от них.
  - Миграция на копии prices.db.backup_1789811567 (backup API, scratchpad): бэкап pre-v6 создан, init_db 0.26 с,
    33332 товара, integrity ok, повторный init_db идемпотентен, flush/get_http_summary работают (p95 146.55 на 100..149).
  - Цепочка scan_id по синтетическим обходам test_scaling: scan_start → scan_category/scan_error → scan_end с одним scan_id.
Не проведено / ограничения проверки:
  - Реальный обход магазинов с сетью не запускался; реальные HTTP-метрики на живых источниках не подтверждены.
  - Frontend suites (нет node), docker smoke (нет docker), прод (нет доступа).
  - iSpace грузит карточки в ThreadPoolExecutor без копирования contextvars: такие запросы учитываются с shop=''
    (host заполнен). Исправление — в адаптере, вне области P01.
  - API/страница просмотра телеметрии не делались (P03 Monitoring Center).
  - События поиска, Telegram, AI, backup, деградации/восстановления: константы есть, точки записи не добавлены —
    предлагается отдельной задачей P01-02 (или в соответствующих этапах P02/P07/P08/P15) по решению владельца.
Запущенные процессы: нет (фоновый поток телеметрии стартует только в сервере/main.py).
Миграции / feature flags / данные для отката: миграция 6 аддитивная (только новые таблицы); init_db делает бэкап pre-v6.
  Решение владельца (19.09, после первого коммита): миграцию 6 убрать, schema_version остаётся 5. Таблицы телеметрии
  создаёт идемпотентная _create_schema в init_db; бэкап перед обновлением не делается (версия не меняется).
  Откат на образ 5.7.1 возможен без восстановления бэкапа: он стартует на этой базе и не использует новые таблицы.
  TELEMETRY_ENABLED=0 отключает сбор без отката.
  Локальная prices.db в корне репозитория уже помечена schema_version=6 (тестовый прогон 18:40, см. ниже):
  на ней не стартует ни 5.7.1, ни текущий код (SchemaTooNew). Исправление — владельцу, после остановки gui.py:
  UPDATE schema_metadata SET value='5' WHERE name='schema_version' (таблицы и данные идентичны),
  либо восстановить backups/prices-pre-v6-20260919T134030705461Z-259c27.db.
  Выполнено 19.09 по поручению владельца после остановки gui.py: schema_version=5, integrity ok, init_db текущего
  кода проходит без миграций и бэкапа, 42899 товаров, 3 таблицы телеметрии на месте.
Проблемы и принятые решения:
  - Выявлено вне P01: полный прогон тестов пишет в prices.db репозитория (DB_PATH фиксируется первым импортом config,
    часть модулей не задаёт DATA_DIR). В 18:40 первый прогон мигрировал локальную prices.db до v6 (бэкап
    backups/prices-pre-v6-20260919T134030705461Z-259c27.db). Существовало до P01 (303→725 тестовых товаров). Отдельная задача.
Следующий точный шаг:
  - Codex: независимый аудит P01 по брифу ниже; выводы и рекомендации записать в эту карточку (раздел «Аудит»),
    статус: READY при отсутствии блокирующих замечаний, иначе IN_PROGRESS с перечнем исправлений (исполнитель — Claude
    по порядку подхвата или по назначению владельца). Код без поручения не менять.
  - Затем владельцу: решение о P01-02 (остальные источники событий) и о выпуске (версия, CHANGELOG, тег).

Бриф аудита P01 (для Codex):
  Объём: git diff 12672ec..HEAD -- . ':!docs' ':!ROADMAP_AND_LOG.md'
    (9 файлов, ~+1185/-38: telemetry.py, test_telemetry.py, database.py, scrapers/http.py, scrapers/arbuz.py,
    web/server.py, main.py, test_offer_identity.py, test_offer_namespace.py).
  Воспроизведение (не на рабочей prices.db):
    DATA_DIR=$(mktemp -d) ./venv/bin/python -m unittest discover -s . -p "test_*.py"   # ожидается Ran 300, OK
    DATA_DIR=$(mktemp -d) ./venv/bin/python -m unittest test_telemetry -v              # 22 теста P01
    Без DATA_DIR полный прогон пишет в prices.db репозитория (давняя проблема, отдельная задача в worktree).
  Приоритетные точки риска:
  1. scrapers/http.py::_limited — семантика лимитера не должна измениться: слот освобождается ровно один раз при
     успехе, исключении send() и исключении _observe; HostCooldown из _acquire пробрасывается как прежде;
     429 учитывается _observe до освобождения слота. Существующие LimiterTest в test_scraper_reliability проходят.
  2. Потоковые ответы (shop.kz YML, stream=True): тело не читается, bytes = Content-Length. Проверить, что все
     пути (request/get/post/Session.request) передают stream. На 18:56 stream=True есть только в scrapers/shopkz.py:51.
  3. Fail-open: record_* только память под threading.Lock; сброс в фоновом потоке (telemetry.start в
     web/server.py::background_tasks и main.py), busy_timeout 2 с; при ошибке сброса данные теряются (stats.failed_flushes),
     не повторяются. Оценить: приемлема ли потеря; ограничены ли буферы (MAX_PENDING_*) и память _pending_http
     (ключ = бакет×host×shop, очищается каждым flush).
  4. p95: Algorithm R поверх telemetry_http_samples при flush (MAX_SAMPLES_PER_BUCKET=500); p95 материализуется в
     агрегат и переживает удаление samples. Проверить корректность счёта seen (= total_requests до flush) и что
     cooldown_rejections не попадают в выборку.
  5. Счётчики: total_requests = фактически отправленные; HostCooldown → cooldown_rejections; 429 отдельно от 4xx;
     errors ⊇ timeouts ∪ connection_errors (классификация по тексту исключения — эвристика, classify_error).
  6. scan_id: _do_scan_task задаёт contextvar до create_task, reset в finally; scan_end пишется и при ошибке/отмене.
     Проверить путь отмены (потеря аренды, остановка приложения): не нарушен ли сброс scan_state и release lease
     из-за добавленного await asyncio.to_thread(telemetry.flush) в finally.
  7. Схема: таблицы аддитивны, создаются _create_schema без повышения schema_version (решение владельца);
     образ 5.7.1 должен стартовать на такой базе. shop NOT NULL DEFAULT '' (NULL ломал UNIQUE/upsert).
  8. Секреты: sanitize_url (query-ключи token/key/secret/..., userinfo), redact_secrets для message/data_json,
     data_json ≤ 4 КБ. Проверить, что в события не попадают cookies/заголовки.
  9. Retention: events 30, samples 7, minute 7, hour 90, day 365 дней; prune раз в 6 ч в фоновом потоке.
     Сверить с политикой P15 (ориентиры плана) — не удаляет ли больше принятого.
  10. scrapers/arbuz.py: fallback-ID sha256[:12] — формат/длина совместимы с offer id и matching.
  Известные ограничения (не дефекты аудита, решение владельца):
  - Нет точек записи событий поиска, Telegram, AI, backup, деградации/восстановления (константы есть) → P01-02?
  - iSpace: ThreadPoolExecutor без copy_context → HTTP-метрики карточек с shop=''.
  - Нет API/UI просмотра телеметрии (P03).
  - Реальный обход с сетью, frontend-, docker-проверки и прод не выполнялись.
Команда для Codex:
  «Прочитай AGENTS.md, docs/DEVELOPMENT_PLAN.md и docs/AGENT_HANDOFF.md. Проведи независимый аудит P01 по брифу
  в карточке P01 на ветке dev/p00-baseline, не меняя код. Запиши выводы, замечания с приоритетом и рекомендации
  в карточку, обнови статус P01 в реестре.»
Готовность к выпуску: не выпускать до аудита; версия не повышалась.
```

## Аудит Codex — результат и следующий шаг

- Срез: `5f9ce5f`, независимый аудит P01 относительно `12672ec`.
- Полный suite на временном DATA_DIR: 300 OK (27.217 с). Дополнительные воспроизведения подтвердили утечку синтетических секретов в текст событий, превышение 4 КБ, смещение p95 при переполнении и ложный успешный исход отмены.
- A01 (P1): очищать сырой текст HTTP-ошибок и чувствительные payload-поля.
- A02 (P2): обеспечивать 4 КБ для окончательного UTF-8 JSON.
- A03 (P2): исправить выборку latency при переполнении pending-буфера.
- A04 (P2): отмена обхода должна иметь явный outcome, а не «ок».
- A05 (P2): различать 401/403/404 и привязывать HTTP-диагностику к обходу/категории.
- Подробные причины, места, воспроизведение и критерии: [P01_AUDIT_CODEX.md](P01_AUDIT_CODEX.md).
- Следующий точный шаг: Claude исправляет A01–A05 по поручению владельца, добавляет регрессии и возвращает на аудит Codex. Самостоятельно следующий агент не запускался.
- В этом checkpoint изменены только `docs/AGENT_HANDOFF.md` и новый `docs/P01_AUDIT_CODEX.md`; код/рабочая БД/прод не изменялись. Фоновых процессов аудита не осталось.
- Оставшийся объём P01-02 требует явного решения о переносе; наличие констант не означает реализацию событий. P00 отдельно не проверялся.

## Исправления по аудиту Codex (Claude, 2026-09-19 19:03–19:08, коммит f8c63f3)

- A01 (P1): из транспортных исключений сохраняется только имя класса (`safe_error_name`) и вид ошибки (timeout/connection/other).
  Вид определяется в `scrapers/http.py::_error_kind` по классу исключения (curl_cffi Timeout/ConnectionError/SSLError,
  встроенные TimeoutError/ConnectionError), с запасным разбором текста; сам текст не сохраняется. Свободный текст (message,
  строки payload) проходит `sanitize_text`: встроенные URL очищаются структурно (userinfo и фрагмент удаляются целиком,
  чувствительные query-параметры → [REDACTED]), значения Cookie/Set-Cookie скрываются, затем `redact_secrets`. Payload очищается
  рекурсивно по явному списку чувствительных имён (`_SENSITIVE_NAME_REGEX`: token, secret, password, auth, authorization,
  signature/sig, cookie, credential, jwt, session/sid, key/api_key/access_key/…; разделители _ - .). `shop_key` не скрывается.
  Глубина 6, до 100 элементов. Ошибка сериализации → только имя класса. В server.py ошибки категорий и падения магазинов
  тоже пишутся только именем класса.
- A02 (P2): `_fit_json` — итоговый UTF-8 JSON ≤ 4096 байт: наибольший префикс ищется бинарным поиском с учётом обёртки
  и экранирования, многобайтовые символы не разрываются. Строковый payload оборачивается в {"text": …} (валидный JSON).
- A03 (P2): в памяти до flush — Algorithm R (≤ MAX_SAMPLES_PER_BUCKET на ключ) по всем запросам окна; при flush
  `merge_reservoirs` объединяет выборку из БД (популяция = total_requests) и окна (популяция = запросы окна) выбором без
  возвращения с весами популяций. Число ключей до flush ограничено MAX_PENDING_HTTP_KEYS (переполнение → stats.dropped_http).
  Flush в транзакции BEGIN IMMEDIATE.
- A04 (P2): `_do_scan_task` пишет scan_end с outcome completed / failed / cancelled / lease_lost (INFO / ERROR / WARNING / WARNING).
  Запись события обёрнута в try; await flush в finally убран — очистка (release lease, is_running, contextvar) не зависит
  от телеметрии.
- A05 (P2): колонка `status_codes` (JSON: {"200": n, "403": n, "timeout": n, "cooldown": n}) в telemetry_http_aggregates;
  добавляется аддитивно через _add_column и в существующие таблицы, schema_version не меняется. Диагностическое событие
  на каждый 4xx (WARNING) / 5xx (ERROR) / 429 / транспортную ошибку с безопасным URL, методом, кодом и latency; scan_id,
  category и shop — из контекста. Частота: ≤ MAX_HTTP_DIAG_EVENTS_PER_MINUTE (20) на host+код в минуту, счётчики
  агрегата остаются точными. Сводка HTTP категории (`current_http_trace`: requests, bytes, status_codes) — в data.http
  событий scan_category/scan_error.
  Минимальный контракт цепочки «обход → страницы → результат»: scan_start/scan_end (outcome) и scan_category/scan_error
  по одному scan_id; сводка кодов категории в data.http; неуспешные страницы — отдельные события с тем же scan_id и category;
  успешные страницы — только в сводке и агрегатах (без трассы каждой страницы).
- Регрессии: test_telemetry.py, классы AuditA01SecretsTest, AuditA02SizeTest, AuditA03ReservoirTest, AuditA04ScanOutcomeTest,
  AuditA05HttpDiagnosticsTest (18 новых тестов, всего в модуле 40). На коде до исправлений (HEAD edad2d6, отдельный worktree)
  эти тесты падают: 19 FAIL/ERROR; на f8c63f3 — OK. Команды воспроизведения из P01_AUDIT_CODEX.md теперь дают:
  A01 {'FAKE_PASSWORD': False, 'FAKE_SIGNATURE': False}, A02 4093 байта, A03 total 3000, p95 1000.0 (эталон 1000.0).
- Полный suite: DATA_DIR=$(mktemp -d) ./venv/bin/python -m unittest discover -s . -p "test_*.py" → Ran 318 tests, OK.
- Не проведено: реальный обход с сетью, frontend, docker, прод, запуск образа 5.7.1 на новой БД.
- Остаётся без изменений (решение владельца): P01-02 — события search/Telegram/AI/backup/price/degradation/recovery; iSpace без
  copy_context (HTTP карточек iSpace — shop='' и без сводки категории).
- Следующий точный шаг: Codex — повторный аудит A01–A05 на f8c63f3 (diff edad2d6..f8c63f3), затем статус READY или список исправлений.
  Команда: «Прочитай AGENTS.md, docs/DEVELOPMENT_PLAN.md и docs/AGENT_HANDOFF.md. Проведи повторный аудит исправлений P01
  A01–A05 (diff edad2d6..f8c63f3, раздел «Исправления по аудиту Codex» в AGENT_HANDOFF.md), не меняя код. Запиши выводы
  в P01_AUDIT_CODEX.md и карточку, обнови статус P01.»

## Исправления B01/B02 по аудиту P01-02 (Claude, 2026-09-19 19:26–19:28, коммит 158c68e)

- B01: деградация/восстановление магазина определяются по полноте итога обхода, а не по error/failure_count.
  Порядок failed < partial < limited < complete (`database.SHOP_HEALTH_RANK`). Состояние хранится в schema_metadata
  `shop_health:<shop_key>` = {status, degraded} и обновляется в той же транзакции, что shop_scans; running его не
  трогает, failure_count и retry-политика не менялись. События: degradation — итог хуже предыдущего (failed → ERROR,
  иначе WARNING); recovery — complete после деградации (одно на восстановление); partial_recovery (новый тип) —
  улучшение до не-complete после деградации (например failed → limited). Первый итог без истории — точка отсчёта,
  событий нет; улучшение без предшествующей деградации (limited → complete с самого начала) — не recovery.
  Данные события: shop_key, from, to, items (+ error у degradation).
  Существующие базы: shop_health появляется с первым итогом после выката — первый обход каждого магазина
  событий не даёт.
- B02: `telemetry.canonical_city` — id поддерживаемого города (config.CITIES_KZ по id/названию), all («Все»),
  kz («Казахстан») или unknown; исходный текст не сохраняется. Применяется на границе телеметрии: колонка city
  (включая contextvar current_city) и поля data city / requested_city любого события. Live-поиск пишет
  requested_city (канонический запрошенный) и city (фактически опрошенный по offer_identity.city_config).
- Неблокирующее замечание AI: Gemini HTTP 400/404 теперь outcome http_400/http_404 (если следующая модель не ответила).
- Регрессии: ShopTransitionTest (5: сценарий Codex complete→failed→limited, complete→limited, одно recovery при
  повторах и running, старт без истории, неизменность retry-политики), CityPrivacyTest (4: канонические значения,
  воспроизведение Codex через настоящий get_best_price_summary на временной БД, live found/cached/error,
  колонка/контекст/данные). На коде до исправлений (46ae29f + audit docs) 8 из 9 падают (retry-тест — страховка,
  проходит на обоих). На 158c68e — OK.
- Воспроизведение Codex без подмен (init_db на временном DATA_DIR): complete → failed(HTTP 403) → limited → complete
  даёт degradation, partial_recovery, recovery; get_best_price_summary('x', city='FAKE_PERSON +77010000000') →
  city=unknown, маркеров в telemetry_events нет.
- Полный suite: DATA_DIR=$(mktemp -d) ./venv/bin/python -m unittest discover -s . -p "test_*.py" → Ran 339, OK.
- Следующий шаг: повторный аудит Codex B01/B02 (diff 6843818..158c68e). Команда: «Прочитай AGENTS.md,
  docs/DEVELOPMENT_PLAN.md и docs/AGENT_HANDOFF.md. Проведи повторный аудит исправлений P01-02 B01/B02
  (коммит 158c68e, раздел «Исправления B01/B02» в AGENT_HANDOFF.md), не меняя код. Запиши выводы в P01_AUDIT_CODEX.md
  и карточку, обнови статус P01.»

## P01-02 — оставшиеся источники событий (Claude → аудит Codex)

- Решение владельца 2026-09-19 ~19:14: остаток P01 делать сейчас в рамках P01 (не переносить).
- Статус: аудит Codex завершён, B01/B02 требуют исправлений. Реализация Claude 19:15–19:20 Asia/Almaty, коммит `66f1b7e` (база `2c1396b`).
  Claude больше не пишет в эти файлы.
- Сделано (точки записи; всё через telemetry.record_event — память, фоновый сброс, fail-open, очистка A01):
  - Поиск (`search_engine.py`): `get_best_price_summary` (source=summary: live, city, имена применённых фильтров),
    `search_live_stores` (source=live: city, cached), `/search` в Telegram (source=telegram). Данные: outcome
    found/not_found/error, results, duration_ms, форма запроса (query_len, query_tokens, query_has_digits) — без текста
    и без хеша запроса (P06: текст может содержать ПДн). Не больше 60 событий в минуту на источник.
  - Цены/matching (`web/server.py::_save_and_detect`): сводка price_changed на пачку категории — items, new,
    price_down, price_up, discount_candidates, arbitrage_candidates, alerts_recorded; scan_id/category из контекста.
    Каждый записанный алерт — anomaly_detected (alert_id, product_id, type, цены, drop_pct, competitor_shop), ≤ 100/мин.
    Событие на каждую смену цены не пишется (тысячи за обход).
  - Telegram (`notifier.py`): сводка цикла deliver_pending (sent/cancelled/retry/failed/errors, pause_seconds при 429),
    пустые циклы не пишутся, chat_id/пользователи не сохраняются. Ошибки notification_worker и polling бота — system_error.
  - AI (`ai_service.py::_limited_provider_call`): каждый реальный вызов провайдера — provider, purpose (user/normalize),
    outcome (ok/empty/http_NNN/timeout/error), duration_ms, http_status, model, input/output tokens из ответа провайдера
    (usageMetadata / usage); отказ до вызова — skipped_busy / budget_exhausted с provider_called=false (≤ 5/мин).
    Промпт и ответ не сохраняются. Стоимость в деньгах не считается — только токены (тарифы не заданы в проекте).
  - Backup (`database.py::backup_database`): outcome ok/failed, файл (имя), size_bytes, duration_sec; исключение по-прежнему
    пробрасывается.
  - Деградация/восстановление (`database.py::record_shop_scan_result`): degradation — первый неуспешный обход
    (failed → ERROR, partial → WARNING) после истории успеха; recovery — успех после сбоев (failed_before). Определяется
    по failure_count (status running между обходами не мешает). Порог деградации по качеству данных — P02.
  - Системные ошибки: middleware `telemetry_middleware` (необработанные исключения обработчиков: метод, шаблон маршрута,
    класс исключения; HTTPException не считается), воркеры auto_scan / ai_normalize / notification / telegram polling.
    Только имя класса, ≤ 5/мин на компонент+место+класс (`record_system_error`).
  - iSpace: карточки грузятся в копиях контекста вызывающего потока (по копии на карточку) — HTTP-метрики и события
    получают shop, category, scan_id и сводку категории.
  - Ядро: общий throttle_key в record_event (счётчик stats.suppressed_events), query_shape, record_system_error.
- Проверки (2026-09-19, macOS, Python 3.14.7 venv):
  - DATA_DIR=$(mktemp -d) ./venv/bin/python -m unittest test_telemetry → 53 OK. Новые классы: ShopTransitionTest,
    BackupEventTest, AiEventTest, TelegramEventTest, SearchEventTest, PriceAndAnomalyEventTest, SystemErrorTest,
    ISpaceContextTest (13 тестов). На коде до P01-02 (2c1396b, отдельный worktree) все 13 падают, на 66f1b7e — OK.
  - Полный suite: DATA_DIR=$(mktemp -d) ./venv/bin/python -m unittest discover -s . -p "test_*.py" → Ran 331, OK.
- Не проведено: реальный запуск сервера с обходом, реальные AI/Telegram-вызовы (стоимость/токены подтверждены только
  на подменённых ответах), frontend, docker, прод.
- Для аудита: diff 2c1396b..66f1b7e (9 файлов кода/тестов). Приоритет: отсутствие ПДн/секретов в событиях поиска,
  Telegram и AI; что обёртки get_best_price_summary/search_live_stores/deliver_pending/backup_database/_limited_provider_call
  не меняют возвращаемые значения и исключения; объём событий (троттлинг, сводки вместо событий на каждый товар);
  корректность переходов degradation/recovery.
- Команда для Codex: «Прочитай AGENTS.md, docs/DEVELOPMENT_PLAN.md и docs/AGENT_HANDOFF.md. Проведи аудит P01-02
  (diff 2c1396b..66f1b7e, раздел «P01-02» в AGENT_HANDOFF.md), не меняя код. Запиши выводы в P01_AUDIT_CODEX.md
  и карточку, обнови статус P01.»

## Повторный аудит Codex — 2026-09-19 19:12

- Проверен HEAD `3e1afec`, исправления `edad2d6..f8c63f3`; рабочее дерево до аудита чистое.
- A01–A05 закрыты. Итог и доказательства — в начале [P01_AUDIT_CODEX.md](P01_AUDIT_CODEX.md). Исторические брифы и первый аудит выше сохраняются для контекста; актуальное решение — этот раздел.
- Независимые проверки: 318 тестов OK на временном DATA_DIR; сохранение безопасных событий в БД, UTF-8 размер, slow-tail p95 через несколько flush, 1000 слияний выборок, различение HTTP-кодов и связь с scan_id/category.
- Следующий точный шаг: владелец определяет, реализовать остаток P01 (P01-02 и iSpace) сейчас или явно перенести в отдельные задачи. После этого назначенный агент продолжает соответствующий объём; исправления A01–A05 не открывать заново без новых оснований.
- Код, рабочая БД и прод не менялись. Изменены только `docs/P01_AUDIT_CODEX.md` и `docs/AGENT_HANDOFF.md`; коммита/push нет. Фоновых процессов аудита нет.
- Весь P01 не переводится в READY автоматически: независимый аудит закрывает конкретные замечания, но не исключает невыполненные требования DEVELOPMENT_PLAN. Прод и P00 не приняты этим аудитом.

## Аудит P01-02 Codex — 2026-09-19 19:24

- Срез `46ae29f`; до аудита рабочее дерево чистое. Проверен diff `2c1396b..66f1b7e`.
- Полный suite на временном DATA_DIR: 331 OK, 31.794 с. Дополнительные проверки на временной БД подтвердили B01/B02.
- B01 (P2): failed→limited не должен объявляться полным восстановлением; здоровье нельзя определять только по error/failure_count. Проверить также complete→limited→complete и running между результатами.
- B02 (P2): city для поисковой телеметрии должен быть разрешённым каноническим значением; произвольный текст с персональными данными не сохранять.
- Подробности, причины и критерии — в начале [P01_AUDIT_CODEX.md](P01_AUDIT_CODEX.md). Предыдущие разделы аудита сохранены как история; этот результат актуален для P01-02.
- Следующий точный шаг: Claude исправляет B01/B02 и добавляет регрессии, затем повторный аудит Codex. Агент не запускался. A01–A05 остаются закрытыми, контекст iSpace замечаний в проверенном объёме не вызвал.
- Изменены только `docs/P01_AUDIT_CODEX.md` и `docs/AGENT_HANDOFF.md`. Код/рабочая БД/прод не менялись, коммита и push нет. Фоновых процессов аудита нет.

## Итоговый аудит C02 (Codex, 2026-09-19 23:16, HEAD e784982)

- C02 закрыто: доставка применяет к записи alerts то же правило свежести основания, что и выдача.
- Исходный дефект повторён на временной БД: legacy-арбитраж возрастом 4 дня при свежем товаре →
  выдача=0, delivered=0, Mock отправки=0, outbox=cancelled. Реальные сообщения не отправлялись.
- Новые сквозные регрессии старого/свежего legacy, нового устаревшего основания и обычной скидки проходят.
- Полный suite независимо: 378 тестов, 32.752 с, OK. C01/C03 остаются закрытыми.
- P02 READY по коду; не DEPLOYED. Следующий шаг: подготовить релиз и выполнить проверки выпуска по плану,
  включая контейнерный smoke и обновление на копии БД; выпуск и проверка прода — по поручению владельца.
- Изменения Codex: docs/P02_AUDIT_CODEX.md и docs/AGENT_HANDOFF.md. Код и прод не менялись.
- Ниже — история исправлений и предыдущих передач; текущий статус указан в контрольной точке и карточке P02.

## Исправление остатка C02 — доставка (Claude, 2026-09-19 23:15–23:18, коммит 31d0ea4)

- notifier._deliver_batch читает из alerts тот же признак, что и выдача: `fresh_benchmark_clause("")`
  (COALESCE(competitor_seen_at, created_at) ≤ 72 ч для MARKET_ARBITRAGE/ARBITRAGE; прочие типы — всегда да).
  Устаревшее основание → cancelled без отправки. Проверка по payload (_stale_benchmark) удалена — единое правило из БД.
  Порядок условий: сначала «алерт удалён/скрыт», затем основание (нет обращения к полю несуществующего алерта).
- Регрессии полного пути очереди (test_notifications.DeliveryTest, настоящие record_alert → outbox → claim →
  deliver_pending, отправка — Mock): legacy-арбитраж старше 72 ч без поля → cancelled, send не вызван; свежий legacy →
  sent; новое устаревшее основание при свежем created_at → cancelled; обычная скидка с created_at 96 ч → sent.
  На коде до исправления (HEAD 1-го коммита аудита) падают оба теста отмены, остальные проходят.
  Тест test_data_quality … переименован в test_existing_alert_hidden_when_benchmark_stale (проверяет выдачу).
- Полный suite: 378 OK.
- Следующий шаг: точечный повторный аудит Codex C02 (коммит 31d0ea4). Команда: «Прочитай AGENTS.md,
  docs/DEVELOPMENT_PLAN.md и docs/AGENT_HANDOFF.md. Проведи точечный повторный аудит остатка C02 P02 (коммит 31d0ea4,
  раздел «Исправление остатка C02» в AGENT_HANDOFF.md), не меняя код. Запиши вывод в docs/P02_AUDIT_CODEX.md и
  карточку, обнови статус P02.»

## Исправления P02 по аудиту Codex C01–C03 (Claude, 2026-09-19 23:04–23:08, коммит 5cd1cca)

- C01 (P1, дубли): data_quality.measure считает по нормализованному id (после assign_offer_ids — магазин+город):
  received (строки), duplicates (повторы id), conflicts (повторы с другой ценой), valid (уникальные пригодные),
  rejected (уникальные непригодные). В порог брака Q2 (> 30 %) входят rejected + duplicates; объём Q1 — уникальные
  valid. data_quality.dedupe (первая строка id) применяется в _scan_shop_categories сразу после измерения — в
  сохранение, reconcile и детектор идёт список без дублей; конфликт цен → warning (не обучает), цена — первой строки.
  source_scans.duplicates (аддитивно через _add_column).
- C02 (P1, устаревшие конкуренты): find_market_comparisons — обе ветви (canonical_key и FTS) через fresh_price_clause
  (≤ 72 ч); возвращает benchmark_seen_at (updated_at самого дешёвого конкурента). check_market_arbitrage кладёт его
  в competitor_seen_at аномалии; record_alert сохраняет в alerts.competitor_seen_at (новая аддитивная колонка).
  fresh_benchmark_clause: арбитражные алерты (MARKET_ARBITRAGE/ARBITRAGE) видны в _fetch_filtered_alerts и на витрине
  арбитража, только пока COALESCE(competitor_seen_at, created_at) ≤ 72 ч (старые алерты без поля — по времени
  создания). notifier._stale_benchmark отменяет доставку, если основание устарело.
- C03 (P2, all-stale): _get_best_price_summary при пустом наборе неустаревших цен возвращает best_deal=None,
  price_stats=None, store_comparison=[], all_stale=True, items с freshness и diff_from_best=None; у Stale-строк
  diff_from_best=None и в смешанном случае. UI: при all_stale — предупреждение «Нет актуальных цен для сравнения»,
  в блоке магазинов нет лидера и разброса; в карточках магазинов — freshnessBadge.
- Регрессии (test_data_quality.py, +12, всего 33): DuplicatesTest (воспроизведение Codex: baseline 200 → 200 копий
  одного id → degraded, 200 активных, норма не испорчена; дубли не раздувают норму; конфликт цены — первая строка;
  разные города не склеиваются), StaleBenchmarkTest (один Stale-конкурент — нет арбитража; смесь Fresh/Aging/Stale —
  только допустимые; FTS-ветвь отдельно; граница 72 ч; существующий алерт скрывается и не доставляется при устаревшем
  основании; старые алерты без поля), AllStaleComparisonTest (один и несколько магазинов, смешанный, пустой).
  На коде до исправлений 11 из 12 падают (пустой случай — страховка). Команды воспроизведения из отчёта Codex теперь:
  арбитраж None; all_stale True, best_deal/price_stats None, store_comparison [].
- UI (встроенный браузер, локальный сервер на копии БД, без сети): «Galaxy Buds Core» со всеми 27 предложениями
  возрастом 5 дней — предупреждение, нет лидера/разброса, строки с бейджем «цена устарела (5 дн.)» и «не сравнивается»;
  ошибок JS нет.
- Полный suite: 374 OK. Не проведено: frontend-тесты (нет node), Docker, реальные обходы, прод.
- Следующий шаг: повторный аудит Codex (diff 93120f6..5cd1cca). Команда:
  «Прочитай AGENTS.md, docs/DEVELOPMENT_PLAN.md и docs/AGENT_HANDOFF.md. Проведи повторный аудит исправлений P02
  C01–C03 (коммит 5cd1cca, раздел «Исправления P02 по аудиту Codex» в AGENT_HANDOFF.md), не меняя код. Запиши выводы
  в docs/P02_AUDIT_CODEX.md и карточку, обнови статус P02.»

## Повторный аудит P02 (Codex, 2026-09-19 23:12, HEAD 96d7930)

- Независимо подтверждены исправления C01 (200 повторов не снимают каталог) и C03 (all-stale без сравнения/лидера).
- C02: новые сравнения исправлены, но старый outbox без competitor_seen_at обходит проверку свежести основания.
  На временной БД: основной товар свежий, арбитражный алерт создан 4 дня назад, выдача=0; deliver_pending=1,
  Mock отправки вызван 1 раз, outbox=sent. Реального Telegram не было.
- Следующий точный шаг для Claude: применить при доставке правило свежести основания по alerts
  COALESCE(competitor_seen_at, created_at), согласованное с выдачей; добавить сквозные тесты очереди для старого
  и свежего legacy, нового stale benchmark и обычной скидки. Затем передать C02 на повторный аудит Codex.
- 374 теста OK (32.790 с); существующий тест с названием not_delivered фактическую доставку не вызывает.
- Область изменений Codex: docs/P02_AUDIT_CODEX.md, docs/AGENT_HANDOFF.md. Код и прод не менялись.
- P02 не READY и не выпущен. Подробности и критерии приёмки: [отчёт](P02_AUDIT_CODEX.md).

## Проверка PR P02 — Codex, 2026-09-19 23:31 Asia/Almaty

- Проверен PR https://github.com/molthun/kz-price-hunter/pull/1 на head `73d9dda2f0ae7a8a651ea9cd3b2fd4c2f0ef665d`:
  OPEN, MERGEABLE, CI test — SUCCESS (завершён 18:30:46 UTC).
- Проверка: https://github.com/molthun/kz-price-hunter/actions/runs/35461368096 .
  CI для PR выполняет compileall и Python suite; frontend и Docker в эту проверку не входят.
- Рабочее дерево до проверки чистое. Код не изменялся; слияние, тег и выпуск не выполнялись.
- Следующий шаг остаётся за владельцем: разрешить слияние PR и выпуск. R-P02 требует отдельного разрешения;
  разрешение на открытие PR само по себе не подтверждает разрешение на автодеплой.

## Аудит P03 Codex — 2026-09-20 00:05

- D01 (P1): успех другой категории закрывает инцидент нерешённой проблемы; восстановление не сопоставляется с областью ошибки.
- D02 (P1): 10 retry, 0 sent → Telegram healthy, «ошибок 0»; временные ошибки исключены из числителя.
- Независимые воспроизведения на временной БД подтверждены. Полный suite: 392 теста OK (33.018 с).
- Следующий точный шаг: Claude исправляет D01/D02 и добавляет регрессии по [отчёту](P03_AUDIT_CODEX.md), затем повторный аудит Codex.
- Изменены только docs/P03_AUDIT_CODEX.md и docs/AGENT_HANDOFF.md; код и прод не менялись. P03 не READY.

## Повторный аудит P03 — Codex, 2026-09-20 00:39

- Исправление категорий D01 подтверждено; D02 закрыто. 408 тестов OK (33.259 с).
- Остаток D01 (P2): system_error.where=telegram_polling → telegram_alert.sent=1 ошибочно даёт recovered.
  Отправка не подтверждает получение обновлений. Воспроизведено на временной БД без сети.
- Следующий точный шаг: Claude ограничивает восстановление system_error областью where, включая fallback по component;
  без подходящего сигнала — open/quiet, не recovered. Регрессия polling → sent и точечный аудит.
- Область изменений Codex: docs/P03_AUDIT_CODEX.md, docs/AGENT_HANDOFF.md; код и прод не менялись.

## Точечный аудит D01 — Codex, 2026-09-20 00:44

- Polling → доставка больше не даёт ложного recovered, подтверждено независимо.
- Остаток D01 (P2): normalize/error → user/error → normalize/ok образует один recovered-инцидент count=2.
  scope не включён в ключ группировки. Следующий шаг Claude: разделить ключ и ID по области операции,
  проверить оба порядка ошибок и независимое восстановление; затем точечный аудит.
- 411 тестов OK (33.410 с). Изменены только docs/P03_AUDIT_CODEX.md и docs/AGENT_HANDOFF.md. Прод не менялся.

## Карточка передачи: повторная приёмка пачки (Claude → Codex)

```text
ID / этап: повторная приёмка после исправлений M01–M11
Основание: поручение владельца «Передавай Codex на повторную приёмку» (2026-09-24).
Снимок: текущий HEAD ветки dev/p07-watch-v2; последний коммит кода — bd8323d, дальше только документы.
  База сравнения 7375c91 (main, v5.12.0).
Что проверять:
  - Закрыты ли одиннадцать замечаний по критериям приёмки из BATCH_AUDIT_CODEX.md.
  - Не появилось ли новых дефектов в изменённых местах: границы периодов и состав шага (P11),
    проверка чисел (P12/P13), копия и разбор отчёта зависимостей (P16), пакет сводки (P07 V2),
    порог уверенности (P09).
  - Соответствуют ли документы коду: AUDIT_REQUEST_BATCH.md (раздел исправлений), CHANGELOG,
    ROADMAP_AND_LOG.md, RELEASE_AND_ROLLBACK.md.
Команды воспроизведения:
  ./venv/bin/python -m unittest discover -p "test_*.py"            # ожидаемо 1093 OK
  for f in test_frontend*.cjs; do deno run -A --node-modules-dir=false "$f" || break; done   # 14 PASS
  ./venv/bin/python release_gate.py secrets --root .               # ожидаемо findings 0
Известные границы, которые автор НЕ считает закрытыми:
  - Проверка чисел в ответах AI ловит выдуманные величины, но не величины, отнесённые не к тому
    показателю. Это записано в самом ответе помощника, а не спрятано.
  - Граница периода «после» точна до часа: неполный час включения не засчитывается вовсе.
  - Обещание отката подтверждается запуском прежнего кода на обновлённой копии, но в живом конвейере
    GitHub Actions эта проверка ещё ни разу не выполнялась.
  - Замер AI-части P09 на живой модели не проводился; до него режим on включать нельзя.
Не проведено: прод, Docker, живой конвейер, реальный аудит зависимостей, настоящие вызовы моделей и
  Telegram, production canary.
```

## Карточка задачи: P09 AI-часть (Claude → аудит)

```text
ID / этап: P09 (AI-часть). Модель для неопределённости в сопоставлении
Основание/поручение владельца: «Давай AI-часть P09» (2026-09-23); пороги выбраны владельцем ДО замеров —
  вариант «строго»: уверенность ≥ 0.90, точность 1.0, полнота ≥ 0.94.
Статус: REVIEW — реализовано, независимого аудита не было, замер на живой модели не проводился.
Ветка / базовый HEAD: dev/p07-watch-v2 / a4c644c. Коммит: 3832dcc.
Что сделано:
  - catalog_ai.may_ask: модель видит только спорные пары (правило воздержалось из-за неуказанной фасовки
    при совпавших отличительных словах). Противоречие фасовки и уже признанный товар ей не показываются.
  - Вердикт принимается только как уверенное «да» (≥ 0.90). «Нет» и «не уверен» оставляют прежнее
    поведение; непонятный ответ считается отсутствием ответа, а не согласием.
  - Режимы off / shadow / on (настройка ai_matching_mode, по умолчанию off). В тени решения записываются
    и видны в мониторинге, сравнение цен не меняется. Во включённом режиме принятое решение разрешает
    сравнение — и только его: противоречие всё равно блокируется правилом.
  - На пути сравнения цен нет сетевых вызовов: спорная пара кладётся в matching_pairs, разбор идёт
    отдельной задачей обслуживания порциями (MAX_PAIRS_PER_RUN = 10), очередь ограничена 5000 парами и
    чистится через 90 дней (срок в политике P15).
  - Ответ по паре кэшируется на сутки (ai_router, задача catalog_matching, внутренняя квота), названия
    подаются в блоке <<<ДАННЫЕ>>> как данные (правило P08).
  - evaluate_with_ai: замер рядом с прежними цифрами и проверка согласованных порогов.
  - Мониторинг: режим, число ждущих и разобранных пар, последние решения с уверенностью и причиной,
    сами пороги и дата их согласования; переключатель POST /api/admin/monitoring/matching/ai.
Проверки: Python 1047 OK (test_catalog_ai.py: 34); frontend 14/14 PASS (test_frontend_p09_ai.cjs).
Не проведено: замер на живой модели (нужен ключ владельца — до замера включать режим on нельзя),
  оценка расхода на реальном потоке спорных пар, независимый аудит, прод, Docker.
```

## Карточка задачи: P07 V2 (Claude → аудит)

**Актуально: аудит 8c591fd — IN_PROGRESS; замечания в [BATCH_AUDIT_CODEX.md](BATCH_AUDIT_CODEX.md). Ниже история реализации.**

```text
ID / этап: P07 V2. Наблюдения: следить в один клик, новые виды, настоящая сводка
Основание/поручение владельца: выбор «P07 до V2» в ответ на вопрос о дальнейшей разработке (2026-09-23).
Статус: REVIEW — реализовано, независимого аудита не было.
Ветка / базовый HEAD: dev/p07-watch-v2 / bc58003 (продолжение ветки P16). Коммит: 24dfc16.
Что сделано:
  - Кнопка «следить» на карточке находки, в строке результатов поиска (компактная, с подписью для чтения
    с экрана) и в карточке товара. Один клик создаёт наблюдение за товаром с условием «любое снижение»,
    повторный — снимает. Состояние кнопки берётся из списка наблюдений, а не угадывается.
  - Новые виды: магазин, выгодные предложения, разница цен между магазинами. Для двух последних цель —
    область поиска («все» или категория), а срабатывание — записанный алерт ленты (SUPER_DISCOUNT /
    ZERO_GLITCH и MARKET_ARBITRAGE / ARBITRAGE). Своей догадки о «хорошей цене» здесь нет.
  - Условие проверяется на соответствие виду: «снова в продаже» для ленты находок и «любая находка» для
    товара отклоняются словами, а не молчат впустую.
  - database.watched_offers добавляет к предложению свежий алерт (30 минут, не скрытый, с совпадающей
    ценой); наблюдения за арбитражем проверяются отдельным вызовом после записи его алерта — раньше
    находки ещё не существует.
  - Режим «сводка»: накопленные срабатывания одного наблюдения уходят одним сообщением; каждое проверяется
    отдельно (устаревшая цена отменяется), неудачная отправка возвращает всю сводку в очередь.
  - Ограничение MAX_EVENTS_PER_WATCH_PER_RUN = 25: широкое наблюдение не превращает один обход в поток.
  - Форма наблюдений: новые виды, подсказка по цели, скрытие неподходящих условий, «все» по умолчанию.
Проверки: Python 995 OK; frontend 13/13 PASS (новый test_frontend_p07_v2.cjs).
Не проведено: независимый аудит, прод, Docker, настоящая отправка в Telegram (подменён telegram_api),
  поведение на большом реальном каталоге (сколько срабатываний даст наблюдение за магазином в жизни).
```

## Карточка задачи: P16 (Claude → аудит)

**Актуально: аудит 8c591fd — IN_PROGRESS; замечания в [BATCH_AUDIT_CODEX.md](BATCH_AUDIT_CODEX.md). Ниже история реализации.**

```text
ID / этап: P16. CI/CD Gate — шлюз выката
Основание/поручение владельца: «сделаем всю пачку он позже проверит» (2026-09-23).
Статус: REVIEW — реализовано, независимого аудита не было.
Ветка / базовый HEAD: dev/p16-ci-gate / ea3383d (продолжение ветки P13). Коммит: 2f3effa.
Что сделано:
  - release_gate.py: четыре проверки одной командной строкой, каждая возвращает ненулевой код при провале.
    • secrets — узкие шаблоны настоящих ключей (Gemini, OpenAI, Telegram, AWS, GitHub, приватный ключ);
      сам секрет в отчёт не печатается; примеры и заглушки не считаются; файлы вне репозитория (git
      check-ignore) пропускаются, чтобы локальный settings.json владельца не останавливал выкат.
    • deps — разбор отчёта pip-audit: пропуск уязвимости действует только с причиной и датой окончания,
      просроченный пропуск снова блокирует публикацию, нечитаемый отчёт блокирует, а не «проходит».
    • upgrade — база предыдущего релиза копируется, проходит миграции новым кодом, на ней выполняются
      настоящие запросы приложения (SMOKE_QUERIES из P15) и PRAGMA integrity_check; отчёт называет,
      возможен ли возврат на прежний образ и какие данные теряются, если нет. Исходный файл не трогается.
    • smoke — по работающему образу: открытые разделы отвечают 200, закрытые отказывают (401 или 403),
      в ответах не должно быть похожего на секрет; недоступный контейнер — провал, а не пропуск.
  - .github/workflows/docker-publish.yml: задания test (плюс compileall), frontend, secret-scan,
    dependency-audit, upgrade-rehearsal, image-smoke; build-and-push зависит от всех шести. Образ
    собирается локально (load) и проверяется ДО публикации в GHCR.
  - .github/workflows/ci.yml: поиск секретов добавлен в проверку веток и PR.
  - docs/dependency_allowlist.json (пустой список пропусков) и docs/RELEASE_AND_ROLLBACK.md — процедура
    выката, возврата и ручного запуска шлюза.
Проверки: Python 950 OK (test_release_gate.py: 30); frontend 12/12 PASS. Репетиция обновления прогнана
  на базе, созданной кодом тега v5.12.0 (схема 5 → 5, запросы проходят, целостность ok). Smoke прогнан
  против настоящего приложения, а не только против заглушки.
Не проведено: прогон конвейера в GitHub Actions (будет при первом push), сборка образа и контейнерный
  smoke на машине владельца (Docker-операции вне моих полномочий), намеренная поломка проверки в реальном
  конвейере, независимый аудит.
```

## Карточка задачи: P13 (Claude → аудит)

**Актуально: аудит 8c591fd — IN_PROGRESS; замечания в [BATCH_AUDIT_CODEX.md](BATCH_AUDIT_CODEX.md). Ниже история реализации.**

```text
ID / этап: P13. Daily Intelligence — суточная сводка
Основание/поручение владельца: «Давай дальше кодекс пока не сможет сделать аудит. сделаем всю пачку он
  позже проверит» (2026-09-23).
Статус: REVIEW — реализовано, независимого аудита не было.
Ветка / базовый HEAD: dev/p13-daily-intelligence / 96c72d6 (продолжение ветки P12). Коммит: fe7169e.
Что сделано:
  - daily_digest.py: расчёт суток по собственным данным — поиски и доля успеха, новые товары, изменения цен
    (дешевле/дороже и самые заметные снижения), попытки и отправки Telegram, вызовы и стоимость AI, обходы,
    ухудшения и восстановления магазинов.
  - Границы суток: события берутся по точным меткам времени [начало, конец); дневные агрегаты (поиск,
    расходы AI) хранятся по суткам UTC и не режутся на часы — при другом часовом поясе блок помечается
    приблизительным с перечнем задетых дней UTC. Неизвестный часовой пояс не роняет отчёт, а даёт UTC.
  - Неполные сутки помечаются и пересчитываются при каждом обращении; завершённые берутся из хранилища.
    Тихие сутки называются отсутствием данных; стоимость AI без заданных цен — прочерк, а не ноль.
  - Таблица daily_reports (day, tz): повторный расчёт заменяет запись и не создаёт дубль, сохранённый
    пересказ при этом не затирается. Срок хранения 180 дней взят из константы кода и попал в политику P15.
  - Пересказ словами — отдельный запрос: промпт требует использовать ТОЛЬКО числа отчёта и помечать догадки;
    ответ с числом, которого в отчёте нет, отменяется целиком (проверка из P12), цифры остаются.
  - web/server.py: GET /api/admin/monitoring/daily (день, часовой пояс, пересчёт), POST .../daily/summary,
    обе под require_admin; чистка старых сводок добавлена в обслуживание после волны.
  - Центр мониторинга, раздел «Обзор»: выбор дня, пересчёт, пересказ, недоступные блоки названы.
Проверки: Python 920 OK (test_daily_digest.py: 21); frontend 12/12 PASS (test_frontend_p13.cjs).
  Сверка: цифры сводки сопоставлены с прямым подсчётом по исходным таблицам за те же сутки.
Отправка в Telegram (отложенный пункт плана) закрыта отдельно, коммит 17984ce: выключатель в карточке
  сводки, час и часовой пояс в системных настройках, одно письмо на сутки через общую очередь
  (своё пространство alert_id), отмена письма бывшему администратору, текст читается без AI.
Не проведено: независимый аудит, прод, Docker, настоящая модель и настоящая отправка в Telegram
  (провайдер и telegram_api подменены в тестах).
```

## Карточка задачи: P12 (Claude → аудит)

**Актуально: аудит 8c591fd — IN_PROGRESS; замечания в [BATCH_AUDIT_CODEX.md](BATCH_AUDIT_CODEX.md). Ниже история реализации.**

```text
ID / этап: P12. AI Admin Assistant — помощник администратора
Основание/поручение владельца: «Давай дальше кодекс пока не сможет сделать аудит. сделаем всю пачку он
  позже проверит» (2026-09-23).
Статус: REVIEW — реализовано, независимого аудита не было.
Ветка / базовый HEAD: dev/p12-admin-assistant / e8fba6f (продолжение ветки P11). Коммит: 5f50c44.
Что сделано:
  - admin_assistant.py: реестр из девяти инструментов только на чтение — магазины, обходы и HTTP, инциденты,
    аналитика поиска, расходы AI, планировщик, качество карточек, окружение, копии. Список закрытый:
    новый инструмент нельзя добавить мимо реестра, а тест читает исходники и падает на любой команде записи.
  - Подбор инструментов по вопросу детерминирован (ключевые слова, не более трёх блоков); непонятный вопрос
    даёт общее состояние, а не случайный набор. Объём данных ограничен, сырые логи и БД модели не уходят.
  - У каждого блока фактов есть «источник» и «период». Недоступный блок попадает в ответ как недоступный,
    а не молча исчезает.
  - Проверка чисел: всё, что модель написала цифрами, сверяется с собранными фактами; при расхождении ответ
    не показывается, остаётся сводка фактов и причина отказа. Доли и округления до целых процентов разрешены.
  - Промпт требует разделять факты и предположения и использовать ТОЛЬКО числа из блока данных; названия
    магазинов и категорий подаются в блоке <<<ДАННЫЕ>>> как данные, а не как указания (правило P08).
  - Без AI помощник отвечает фактами: сводка формируется кодом.
  - web/server.py: POST /api/admin/assistant под require_admin, период 1–90 дней, пустой вопрос — 400.
  - Центр мониторинга, раздел «Обзор»: поле вопроса, выбор периода, ответ, факты и оговорка рядом.
Проверки: Python 899 OK (test_admin_assistant.py: 17); frontend 11/11 PASS (test_frontend_p12.cjs).
  Сценарий приёмки: синтетическое падение каталога dns 2000 → 100 карточек и 200 отказов 4xx из 500 запросов —
  цифры попадают в факты с источником и периодом, ответ на них показывается, выдуманные «73 %» и «987» — нет.
Не проведено: независимый аудит, прод, Docker, вызов настоящей модели (в тестах провайдер подменён),
  оценка качества формулировок на живых данных.
```

## Карточка задачи: P11 (Claude → аудит)

**Актуально: аудит 8c591fd — IN_PROGRESS; замечания в [BATCH_AUDIT_CODEX.md](BATCH_AUDIT_CODEX.md). Ниже история реализации.**

```text
ID / этап: P11. Adaptive Scheduler — контролируемое включение
Основание/поручение владельца: «Начинай P11» (2026-09-23 14:55). Пороги отката и порядок расширения
  согласованы владельцем ДО включения (14:57): вариант «строго», расширение только вручную.
Статус: REVIEW — реализовано, независимого аудита не было.
Ветка / базовый HEAD: dev/p11-scheduler-rollout / 7375c91 (main, v5.12.0). Коммит: 91dd569.
Что сделано:
  - scheduler_rollout.py: шаги off → canary_one → canary_few (до 5) → all; переходы вперёд только по одному
    шагу, назад и в «выключено» — всегда; первым пробным берётся самый дружелюбный источник, выбор
    детерминирован и воспроизводим.
  - select_targets: адаптивный порядок применяется только к пробным магазинам и только внутри той же волны.
    Результат всегда подмножество прежнего списка — утверждение в коде плюс тест. Пробный магазин, которому
    по правилам рано, пропускается, а не обходится досрочно.
  - Снимок метрик «до» сохраняется при включении шага; сравнение «до/после» по доле ошибок, отказам по
    лимиту и полноте обходов. Мало наблюдений — ни откат, ни «готов к шагу»; об этом говорится словами.
  - Автоматический откат после циклов обхода: возвращает прежний порядок целиком, записывает причину и
    пишет событие телеметрии. Расширение шага автоматически не делается никогда.
  - web/server.py: порядок волны проходит через apply_adaptive_order (при любой ошибке — прежний список);
    проверка отката в обслуживании; админские GET/POST /api/admin/scheduler/rollout.
  - Центр мониторинга, раздел «Сканирование»: текущий шаг, пробные магазины, цифры «до и после», готовность
    к следующему шагу, история отката и кнопки переключения с блокировкой прыжков.
Пороги (согласованы до включения): рост доли ошибок или отказов по лимиту > ×1.33; падение полноты > 10 п.п.;
  наблюдение шага 24 ч; минимум 50 запросов и 5 обходов, иначе судить рано.
Проверки: Python 882 OK (test_scheduler_rollout.py: 41); frontend 10/10 PASS; браузер на стенде — включение,
  блокировка прыжка через шаг, возврат в «off».
Не проведено: независимый аудит, настоящий canary на проде, Docker, нагрузка, поведение при потере аренды
  планировщика проверено только через неизменность списка целей (аренда живёт в прежнем коде).
```

## Карточка задачи: P15 (Claude → аудит)

**Актуально на 79d5239: READY (аудит кода), не выпущен. Основания и следующий шаг — в верхней контрольной точке. Далее сохранена история.**

**Аудит Codex 732dde4: L02 закрыт, L03/L04 открыты. IN_PROGRESS.** Актуальны верхняя контрольная точка и начало отчёта; ниже история.

**Повторный аудит Codex 9200a61: L01 закрыт, L02/L03 открыты; IN_PROGRESS.** Актуальны верхняя контрольная точка и начало отчёта, ниже история.

**Аудит Codex 8b05167: IN_PROGRESS, L01–L03 требуют исправления.** Актуальны верхняя контрольная точка и [P15_AUDIT_CODEX.md](P15_AUDIT_CODEX.md); ниже история реализации.

```text
ID / этап: P15. Backup, Retention, Self-Monitoring
Основание/поручение владельца: «Токены у Codex опять закончились. Разработка без аудита, потом аудит и
  пушим» (2026-09-20 ~19:10).
Статус: REVIEW — реализовано, независимого аудита не было.
Исполнитель: Claude, 2026-09-20 19:10 — 2026-09-23.
Ветка / базовый HEAD: dev/p15-backup-retention / cc083f2 (содержит P10 и P14).
Что сделано:
  - backup_health.py: настоящая проверка копии (открыть только на чтение, integrity_check, прочитать схему,
    пересчитать ключевые таблицы) и репетиция восстановления — копия разворачивается во временную базу,
    проверяется чтением и удаляется.
  - Периодическая самопроверка раз в сутки в общем обслуживании базы; результат пишется в backup_checks,
    ставится пульс компонента (P14). Сбой самой проверки не ломает обслуживание.
  - Сроки хранения собраны в одном месте из действующих констант (а не переписаны отдельно), у каждого —
    причина; рядом фактическое потребление: сколько строк и насколько старые записи есть сейчас.
  - Раздел «Резервные копии» и «Сроки хранения» в Центре мониторинга + /api/admin/monitoring/backups.
  - Честная оговорка в отчёте: внутренняя проверка не заметит полную остановку процесса — это видит только
    наблюдатель снаружи.
Статусы без ложного зелёного: копий нет — «неизвестно»; копия есть, но ни разу не проверялась — тоже
  «неизвестно»; копия старше 48 ч или проверка не прошла — «деградация».
Проверки: Python 748 OK (test_backup_health.py: 44 — повреждённая копия, не-база, пустая копия, отсутствие
  файла, репетиция восстановления с удалением временной базы, заполненный диск, периодичность раз в сутки,
  запись результата и пульса, устойчивость обслуживания к сбою проверки, соответствие политики коду);
  frontend 10/10 PASS. На копии рабочей базы: восстановление 43 852 товаров за 0,42 с, схема 5.
Не проведено: независимый аудит, Docker, прод, внешняя проверка полной остановки процесса, нагрузка.
```

## Карточка задачи: P14 (Claude → аудит Codex)

**Актуально на 79d5239: READY (аудит кода), не выпущен. Основания и следующий шаг — в верхней контрольной точке. Далее сохранена история.**

**Аудит Codex cc083f2: IN_PROGRESS, исправить K01/K02.** Воспроизведения и критерии: [P14_AUDIT_CODEX.md](P14_AUDIT_CODEX.md). Ниже сохранена история реализации; актуален вывод аудита.

```text
ID / этап: P14. System & Environment
Основание/поручение владельца: «Давай ещё продолжим, потом аудит» (2026-09-20 18:45); этап выбран владельцем
  из трёх предложенных, потому что не зависит от непринятого P10 и не меняет поведение обходов.
Статус: REVIEW — реализовано, ждёт независимого аудита Codex.
Исполнитель: Claude, начало 2026-09-20 18:45 Asia/Almaty.
Ветка / базовый HEAD: dev/p14-system-environment / 07220b7 (main, v5.11.0). Коммит: 27a760e.
Что сделано:
  - environment.py — фактическое окружение: версия приложения, коммит сборки (GIT_SHA или .git), Python,
    SQLite, aiohttp, requests, playwright, curl_cffi, система, время работы без перезапуска.
  - Пульс работников (таблица component_heartbeats, schema_version без изменений): планировщик обходов,
    очередь Telegram, фоновая AI-нормализация, запись телеметрии, резервные копии. Пульс пишут сами
    работники, запись fail-open — её сбой не мешает работе.
  - Состояния различаются: «отзывается», «молчит» (пульса нет дольше своего порога), «пульса не было»,
    «выключено настройками». Выключенный владельцем компонент не портит общий статус; молчащий — портит.
    Пороги молчания соответствуют ритму работника (Telegram 5 мин, планировщик 20 мин, бэкапы 48 ч).
  - Раздел «Система» в Центре мониторинга + /api/admin/monitoring/environment (только чтение).
  - Секреты не показываются: видно «ключ задан» / «ключа нет», сами ключи и токены — никогда.
Проверки: Python 698 OK (test_environment.py: 16 — свежий и протухший пульс, никогда не отмечавшийся
  компонент, выключенное против сломавшегося, пороги по ритму, перезапуск, сокрытие секретов, fail-open
  записи); frontend 10/10 PASS. Браузер на стенде: раздел «Система» с версиями, временем работы и таблицей
  работников; выключенный Telegram помечен выключенным.
Не проведено: Docker, прод, внешняя проверка полной остановки процесса (внутренний пульс её не заменяет —
  это объём P15), нагрузка.
```

## Карточка задачи: P10 (Claude → аудит Codex)

**Актуально на 79d5239: READY (аудит кода), не выпущен. Основания и следующий шаг — в верхней контрольной точке. Далее сохранена история.**

**Аудит Codex cc083f2: IN_PROGRESS, исправить J01–J03.** Воспроизведения и критерии: [P10_AUDIT_CODEX.md](P10_AUDIT_CODEX.md). Ниже сохранена история реализации; актуален вывод аудита.

```text
ID / этап: P10. Adaptive Scheduler: shadow
Основание/поручение владельца: «Продолжи, передам на аудит позже» (2026-09-20 18:30).
Статус: REVIEW — реализовано, ждёт независимого аудита Codex.
Исполнитель: Claude, начало 2026-09-20 18:30 Asia/Almaty.
Ветка / базовый HEAD: dev/p10-scheduler-shadow / 07220b7 (main, v5.11.0). Коммит: ea398d1.
Что сделано:
  - scheduler_shadow.py — расчёт предложений по уже собранным данным: спрос и неудовлетворённый спрос из
    аналитики поиска (P06), ожидающие наблюдения (P07), возраст данных и качество последнего обхода (P02),
    изменчивость цен, профиль источника по его HTTP-метрикам (P01).
  - Профили FRIENDLY / NORMAL / EXPENSIVE / DEGRADED следуют из метрик (ошибки, 429/403, задержка p95, объём
    ответа). Проблемный и дорогой источник отодвигается, а не разгоняется; границы вынесены в константы с
    пояснением, что это отправные значения для обсуждения.
  - Ограничения проверяются при составлении плана: пауза после ошибок, минимальный интервал по профилю,
    не больше N категорий одного магазина и общий предел цикла. У каждого отклонённого кандидата видна причина.
  - Защита от забывания: источник, не обойдённый дольше порога (или не обойдённый ни разу), поднимается
    независимо от спроса.
  - Симуляция на записанных данных сравнивает адаптивный порядок с нынешним round-robin: средний и предельный
    возраст данных, охваченный неудовлетворённый спрос и наблюдения, нарушения ограничений, ни разу не
    обойдённые источники. Результат воспроизводим (тест сравнивает два прогона).
  - Отчёт в Центре мониторинга (раздел «Сканирование») + /api/admin/monitoring/scheduler: только чтение,
    кнопок запуска в разделе нет.
Теневой режим: побочных действий нет — тест сверяет счётчики восьми таблиц до и после построения плана и
  отдельно запрещает вызовы сетевого слоя.
Проверки: Python 682 OK (test_scheduler_shadow.py: 23 — профили, вклад сигналов, ограничения, starvation,
  воспроизводимость симуляции, отсутствие побочных действий); frontend 10/10 PASS (test_frontend_p10.cjs —
  предложения с причинами, сравнение стратегий, отсутствие кнопок действий, экранирование чужих названий).
На копии рабочей базы: 17 источников, 8 предложений с объяснениями; адаптивный порядок — средний возраст
  данных на 34,6 ч ниже, нарушений 0.
Не проведено: настоящее включение планировщика (это следующий этап P11), Docker, прод, нагрузка.
  Симуляция оценивает решения, но не доказывает ускорение реальных обходов.
```

## Карточка задачи: P09 (Claude → аудит Codex)

**Повторный аудит e48ccb8, Codex, 2026-09-20: READY (детерминированная часть): I01/I02 закрыты; AI-часть отдельно.** Актуальны верхняя контрольная точка и начало [P09_AUDIT_CODEX.md](P09_AUDIT_CODEX.md); нижележащие записи — история.

Актуализация Codex 2026-09-20: **IN_PROGRESS**, I01/I02 требуют исправления; реализация ниже сохранена как история. Следующий исполнитель — Claude. Критерии и воспроизведения: [P09_AUDIT_CODEX.md](P09_AUDIT_CODEX.md).

```text
ID / этап: P09. AI для качества каталога — детерминированная часть (фасовка и отбор кандидатов)
Основание/поручение владельца: «Двигаемся дальше и не пушим без аудита» (2026-09-20 17:05); пороги качества
  и порядок включения согласованы владельцем 17:06 — ДО проведения замера.
Статус: REVIEW — реализовано, ждёт независимого аудита Codex.
Исполнитель: Claude, начало 2026-09-20 17:05 Asia/Almaty.
Ветка / базовый HEAD: dev/p09-catalog-quality / af0c0c4 (dev/p08-ai-router). Коммит: f815e88.
Что сделано:
  - catalog_quality.py: фасовка (объём, вес, длина) и количество в упаковке разбираются из названия и
    приводятся к базовым единицам; «1 л» = «1000 мл», «2 кг» = «2000 г». Память, диагональ и проценты
    жирности фасовкой не считаются.
  - Разная фасовка — разные товары: Coca-Cola 1 л и 1,5 л, Whiskas 75 г и 85 г, Zewa 4 шт и 8 шт больше не
    сравниваются по цене.
  - Фасовка указана только у одного предложения — товары тоже не признаются одинаковыми: сравнение вслепую
    и есть та ошибка, которую этап должен убрать. Такие случаи идут в теневой отчёт.
  - same_product: совпадение по модели (прежнее правило) ИЛИ полное совпадение отличительных слов при
    непротиворечивой фасовке. Отличительные слова — без слов категории, фасовки и диагонали.
  - Отбор кандидатов (search_terms) переведён на отличительные слова: раньше поиск аналогов требовал
    совпадения слов вроде «напиток», из-за чего одинаковые продукты в разных магазинах не встречались.
  - Теневой отчёт (таблица matching_shadow, schema_version без изменений) + раздел в Центре мониторинга
    и /api/admin/monitoring/matching: сколько пар развела фасовка и сколько случаев спорных, с примерами.
Замер (docs/golden_matching.json, версия 1, 40 пар; пороги согласованы до замера):
  - точность 1.0 при цели 0.99, полнота 0.93 при нижней границе 0.70;
  - прежнее правило на парах «фасовка/упаковка» ошибалось, новое — ни разу (тест сравнивает «до и после»);
  - одно сознательное исключение задокументировано в наборе (iPad Air 11 без единицы измерения).
Проверка на копии рабочей базы: техника — 120 товаров, аналоги нашлись у 65, ни одной блокировки по фасовке
  (регрессии для техники нет), один спорный случай записан.
Проверки: Python 572 OK (test_catalog_quality.py: 16), frontend 9/9 PASS.
Не проведено: AI-часть этапа (модель для спорных случаев) — следующий шаг; Docker, прод, нагрузка;
  кириллическое написание брендов полнотекстовый поиск пока не находит (ограничение отбора кандидатов).
```

## Карточка задачи: P08 (Claude → аудит Codex)

**Точечный аудит 8a77f5a, Codex, 2026-09-20: READY, H01/H02/H03 закрыты.** Актуальны верхняя контрольная точка и начало отчёта; записи ниже сохранены как история.

**Повторный аудит e48ccb8, Codex, 2026-09-20: IN_PROGRESS: H02 закрыт, исправить H01/H03.** Актуальны верхняя контрольная точка и начало [P08_AUDIT_CODEX.md](P08_AUDIT_CODEX.md); нижележащие записи — история.

Актуализация Codex 2026-09-20: **IN_PROGRESS**, H01/H02 требуют исправления; реализация ниже сохранена как история. Следующий исполнитель — Claude. Критерии и воспроизведения: [P08_AUDIT_CODEX.md](P08_AUDIT_CODEX.md).

```text
ID / этап: P08. AI Platform / Router
Основание/поручение владельца: «Давай дальше, но ничего не пушим без аудита» (2026-09-20 14:15).
Статус: REVIEW — реализовано, ждёт независимого аудита Codex.
Исполнитель: Claude, начало 2026-09-20 14:15 Asia/Almaty.
Ветка / базовый HEAD: dev/p08-ai-router / ddd5617 (dev/p07-watch-system). Коммит: af0c0c4.
Что сделано:
  - ai_router.py — единая точка для всех AI-задач: реестр задач (разбор запроса, консультант, объяснение
    аномалии, нормализация, классификация категорий, атрибуты, сопоставление, здоровье источника, помощник
    администратора) с политикой: чья задача (люди или проект), таймаут, разрешён ли запасной провайдер, кэш.
  - Сначала без модели: детерминированный ответ и кэш не доходят до провайдера. Каталог, обход и обычный поиск
    работают при выключенном AI — вызывающий получает AIUnavailable и обязан обойтись сам.
  - Раздельные дневные бюджеты: внутренним задачам доступно 70 % лимита, людям — весь; общий счётчик вызовов
    остаётся один (проверено на параллельных вызовах).
  - Явно выбранный администратором провайдер не подменяется молча: запасной возможен только в режиме «auto»
    и только у задач, которым это разрешено. Переход записывается в учёт.
  - Учёт (таблица ai_usage, schema_version без изменений): запросы, токены входа и ответа, задержка, ошибки,
    попадания в кэш, переходы на запасного — по дню, задаче, провайдеру и модели. Срок хранения 365 дней.
  - Деньги считаются ТОЛЬКО по ценам, заданным администратором (`ai_model_prices`, доллары за миллион токенов,
    с проверкой значений). Цены не зашиты в код: тарифы меняются, и выдуманная цифра вводила бы в заблуждение.
    Пока цены не заданы, отчёт прямо говорит, что расход в деньгах показан не полностью.
  - Чужой текст в промпте: названия товаров вставляются размеченным блоком данных с прямым указанием не
    выполнять инструкции изнутри; попытка закрыть блок из содержимого обезвреживается.
  - Мониторинг: раздел AI получил сводку расходов (по задачам и по моделям) + /api/admin/monitoring/ai-usage.
  - Разбор поискового запроса переведён на роутер — политика и учёт работают на настоящем пути.
Проверки: Python 556 OK (test_ai_router.py: 41 — работа без модели, кэш, запасной провайдер и запрет молчаливой
  подмены, оба провайдера недоступны, таймаут и отказ, ответ не той формы, исчерпанный бюджет, раздельные
  бюджеты, параллельные вызовы и общий счётчик, учёт по задачам и моделям, цены и их отсутствие, защита от
  инструкций в названиях, отчёт мониторинга, срок хранения); frontend 9/9 PASS. Браузер: раздел AI со сводкой.
Не проведено: реальные вызовы Gemini и OpenAI (в тестах подменяются), проверка актуальных тарифов провайдеров,
  Docker, прод, нагрузка.
```

## Карточка задачи: P07 (Claude → аудит Codex)

**Повторный аудит e48ccb8, Codex, 2026-09-20: READY (V1): G01/G02 закрыты.** Актуальны верхняя контрольная точка и начало [P07_AUDIT_CODEX.md](P07_AUDIT_CODEX.md); нижележащие записи — история.

Актуализация Codex 2026-09-20: **IN_PROGRESS**, G01/G02 требуют исправления; реализация ниже сохранена как история. Следующий исполнитель — Claude. Критерии и воспроизведения: [P07_AUDIT_CODEX.md](P07_AUDIT_CODEX.md).

```text
ID / этап: P07. Telegram Watch System V2 — V1 по объёму, утверждённому владельцем
Основание/поручение владельца: «Двигаемся дальше как вчера, у Codex кончились лимиты» (2026-09-20 14:00);
  ответы по объёму и умолчаниям — 14:01.
Статус: REVIEW — реализовано, ждёт независимого аудита Codex.
Исполнитель: Claude, начало 2026-09-20 14:00 Asia/Almaty.
Ветка / базовый HEAD: dev/p07-watch-system / d259374 (dev/p06-search-analytics). Коммит: ddd5617.
Объём V1 (решение владельца): виды — товар, модель, поисковый запрос, категория. Магазин, скидки и арбитраж —
  следующим шагом. Общая рассылка алертов сохраняется без изменений, наблюдения добавляются сверху.
Что сделано:
  - watches.py — правила без базы и сети: условия (цена не выше, снижение в % и ₸, любое снижение, лучшая цена,
    снова в продаже), проверка значений с понятными сообщениями, тихие часы через полночь по часовому поясу
    владельца, пауза между сообщениями, сводка, одноразовое/повторяющееся, человеческое описание наблюдения.
  - Антиспам: снижение считается от цены последнего отправленного сообщения, поэтому 149 990 → 151 000 → 149 990
    даёт одно уведомление; цель по цене срабатывает один раз, пока цена не поднимется выше цели.
  - database.py — таблицы watches, watch_state, watch_events (schema_version без изменений), CRUD с проверкой
    владельца, evaluate_watches: одно срабатывание → одно задание в общей очереди уведомлений с
    alert_id = -id срабатывания, поэтому UNIQUE(alert_id, user_id) не даёт продублировать отправку при повторном
    запуске или параллельных воркерах.
  - notifier.py — сообщение объясняет, какое наблюдение сработало и почему; переиспользованы очередь, повторы,
    пауза по 429 и обработка блокировки бота. Перед отправкой проверяется, что наблюдение активно, человек не
    отключил Telegram и цена всё ещё та самая.
  - web/server.py — /api/me/watches (GET/POST), /api/me/watches/{id} (POST/DELETE) под require_login; проверка
    наблюдений подключена к обходу только для изменившихся предложений и не может сломать обход.
  - Наблюдения принадлежат человеку: входят в выгрузку данных и удаляются вместе с аккаунтом.
  - index.html — раздел «Мои наблюдения» в личных настройках: список, создание, включение/выключение, удаление.
Проверки: Python 515 OK (test_watches.py: 39 — правила, антиспам, тихие часы, изоляция пользователей, доставка,
  429, блокировка бота, перезапуск, отмена при изменении цены, выгрузка и удаление данных); frontend 9/9 PASS
  (test_frontend_p07.cjs — раздел только после входа, создание, ошибка сервера словами, экранирование чужого
  текста). Браузер на стенде: 1366 и 375 px, горизонтальной прокрутки и ошибок JS нет.
Не проведено: реальная отправка в Telegram, Docker, прод, поведение при настоящем таймауте сети (в тестах
  подменяется ответ), нагрузка при большом числе наблюдений.
```

## Карточка задачи: P06 (Claude → аудит Codex)

**Повторный аудит e48ccb8, Codex, 2026-09-20: READY: F01–F04 закрыты.** Актуальны верхняя контрольная точка и начало [P06_AUDIT_CODEX.md](P06_AUDIT_CODEX.md); нижележащие записи — история.

```text
ID / этап: P06. Search Analytics
Основание/поручение владельца: подхват передачи 2026-09-20 13:04 после выпуска v5.10.0; решения по приватности,
  сроку хранения и месту отчёта — ответы владельца 13:05.
Статус: IN_PROGRESS — повторный аудит a6c68cc: F01/F02/F04 закрыты; исправить F03 по docs/P06_AUDIT_CODEX.md.
Исполнитель: Claude, начало 2026-09-20 13:05 Asia/Almaty.
Ветка / базовый HEAD: dev/p06-search-analytics / 9f50085 (main, v5.10.0). Коммит реализации: 1eda1ab.
Что сделано:
  - search_analytics.py: исходы FOUND / WEAK / NOT_FOUND / ERROR по качеству совпадения (совпало не меньше двух
    слов запроса в названии либо точная фраза; аксессуар засчитывается, только если аксессуар и искали),
    нормализация запроса, отбраковка похожего на личные данные (телефон, почта, ссылка, ник, адрес, длинные
    номера, текст длиннее 80 символов), формулы доли успеха и доли ошибок.
  - database.py: таблицы search_stats (день/город/источник/исход), search_queries (текст с 3-го повтора),
    search_query_seen (счётчик повторов по sha256 — текст по нему не восстановить); record_search,
    search_totals, search_queries, prune_search_analytics (90 дней). schema_version не меняется.
  - search_engine.py: живой поиск и сводка лучшей цены пишут исход по качеству совпадения; запись fail-open.
  - monitoring.py + /api/admin/monitoring/search + раздел «Поиск» страницы мониторинга: точные доли, разбивка
    по источникам, «чаще всего ищут» и «плохо отвечаем» (только запросы с плохими исходами), формула на экране.
  - web/server.py: очистка по сроку хранения в общем обслуживании базы.
Приватность: идентификаторы (user_id, Telegram ID, IP, сессия) в модуль не передаются и в таблицах отсутствуют;
  редкий запрос считается только в общих числах и текстом не хранится.
Формула: Доля успеха = FOUND / (FOUND + WEAK + NOT_FOUND). Ошибки в знаменатель не входят и показаны отдельной
  долей error_rate — поломка поиска не маскируется под «ничего не найдено».
Проверки: Python 461 OK (test_search_analytics.py: 29 — приёмка плана «реальные товары → FOUND, только кабели →
  WEAK, пусто → NOT_FOUND, сбой → ERROR», приватность, город, срок хранения, статусы раздела); frontend 8/8 PASS
  (test_frontend_p06.cjs — доли, формула, таблицы, экранирование чужого текста, состояние ошибки; падает на
  странице до P06). Браузер на копии рабочей базы с засеянными поисками: 1366 и 375 px, прокрутки и ошибок нет.
Исправления по аудиту (13:49, d259374): F01 канонизация города и проверка источника; F02 обязательное совпадение
  модели/памяти/варианта и границы слов; F03 сбой источников = ERROR и не кэшируется; F04 одна запись на одно
  обращение человека, фоновые обходы исключены из спроса. Регрессии: 7 сквозных тестов через настоящие обёртки.
Не проведено: Docker, прод, реальные пользовательские поиски; поведение под нагрузкой не замерялось.
```

## Карточка задачи: P05 (Claude → аудит Codex завтра)

```text
ID / этап: P05. Settings UX
Основание/поручение владельца: «Начинай P05» (2026-09-20 ~01:09) — продолжение пачки без push; аудит Codex завтра.
Исполнитель: Claude, начало 2026-09-20 01:10 Asia/Almaty.
Ветка: dev/p05-settings от dev/p04-ui-audit (исправления по аудиту P03/P04 переносятся merge-ом).
Инвентаризация (01:15): бэкенд уже разделяет USER_DEFAULTS (личные, /api/me/settings, в users.settings) и
  SYSTEM_DEFAULTS (settings.json, /api/admin/config, require_admin); _validate проверяет типы и enum, но не диапазоны
  (можно сохранить скидку 500 %, мин. цену больше макс.). Сброса к умолчаниям нет. UI: пользователь — Telegram, магазины
  в ленте, чувствительность (3 пресета + 6 чисел), поиск, стоп-слова, мои данные; админ — «Состояние магазинов»
  (дубль мониторинга P03 с кнопкой «Проверить»), пороги кандидатов, Telegram-бот, AI, сканирование, категории,
  пользователи. «Наблюдения» — функция P07, в P05 не появляется.
Дизайн P05:
  1. Навигация настроек в две группы: «Мои настройки» (уведомления, магазины, что показывать, поиск, мои данные) и
     «Система — администратор» (сканирование и магазины, мониторинг ↗, AI, алгоритмы, Telegram-бот, пользователи).
  2. «Состояние магазинов» убрать из настроек → ссылка на /monitoring#shops; «Проверить» (обход одного магазина) —
     кнопка в карточке магазина в мониторинге (POST /api/scan/start, админ).
  3. «Какие скидки показывать»: режим (только ошибки цен / ошибки и распродажи / всё выгодное) + уровень выгоды
     (существующие пресеты) + «Точные параметры» в раскрывающемся блоке; индикатор «свои значения», если числа
     не совпадают ни с одним уровнем (старые настройки сохраняют смысл — числа не переписываются).
  4. Пороги кандидатов (внутренние) — в раскрывающийся блок «Алгоритмы (для опытных)».
  5. Проверка диапазонов на сервере: проценты 1–99, суммы 0–100 000 000 ₸, мин. цена ≤ макс. (с учётом сохранённых),
     интервал проверки 30 с–1 сутки; понятные сообщения.
  6. Сброс личных настроек к умолчаниям (POST /api/me/settings/reset), кроме включённости Telegram-уведомлений.
  7. Тесты: каждый параметр сохраняется/читается, старый settings.json, умолчания/сброс, ошибочные значения,
     пользователь не меняет системные параметры, изоляция пользователей; frontend — навигация, режимы, ссылка
     на мониторинг, раскрывающиеся блоки.
Реализация (2026-09-20 01:21, коммиты 2d756e8 и 513917c):
  - config.py: SETTING_RANGES (проценты 1–99, суммы до 100 000 000 ₸, интервал проверки 30 с–1 сутки) с понятными
    сообщениями; validate_user_settings(new, current) проверяет мин. цену ≤ макс. с учётом сохранённых; NaN/inf
    отклоняются; USER_RESET_KEEP. database.replace_user_settings; POST /api/me/settings/reset (require_login):
    личные настройки к умолчаниям, включённость Telegram-уведомлений сохраняется.
  - index.html: навигация «Мои настройки» / «Система · администратор» (admin-only); «Состояние магазинов» и её
    15-секундный опрос удалены из настроек → карточка-ссылка на /monitoring#shops; «Пороги записи кандидатов» → свёрнутый
    блок «Алгоритмы (для опытных)»; раздел «Какие скидки показывать»: режим (только ошибки цен / ошибки и распродажи /
    всё выгодное) меняет только типы, уровень выгоды (прежние 3 пресета) — только числа, индикатор «Сейчас: … · уровень: …»
    или «свои значения» (старые сохранённые числа не переписываются), «Точные параметры» свёрнуты; кнопка «Сбросить к
    умолчаниям»; scrollSettingsTo раскрывает <details>.
  - monitoring.html: в карточке магазина — «Запустить обход магазина» (POST /api/scan/start, заменяет «Проверить»
    из настроек).
  - «Наблюдения» (P07) и личные настройки AI в P05 не появляются: таких функций пока нет; город — селектор в шапке.
Проверки (2026-09-20 01:21):
  - test_settings.py (10): каждый личный параметр сохраняется и читается; ошибочные значения (150 %, 0 %, −5, 10¹², строка
    вместо числа/булева, неизвестный enum, строка вместо списка, список вместо объекта) → понятные ошибки; мин. > макс.
    (в том числе с сохранённым макс.); системные и неизвестные ключи игнорируются для пользователя; изоляция
    пользователей; умолчания; старый settings.json (системные значения, неизвестный магазин и старые опции
    отброшены, недостающее — умолчания, личные поля переносятся); каждый системный параметр валидируется; диапазоны
    системных; API: пользователь не меняет системные (settings.json не тронут, /api/admin/config → 403), 150 % → 400 с
    сообщением, сброс сохраняет Telegram, другой пользователь не затронут, гость → 401, админ → 200.
  - test_frontend_p05.cjs: группа «Система» видна только админу; таблицы магазинов нет, ссылка на мониторинг есть;
    «Алгоритмы» свёрнуты и раскрываются навигацией; старые числа → «свои значения»; режим меняет только типы, уровень —
    только числа; ручное изменение → «свои значения»; сброс вызывает API и показывает умолчания.
  - На коде до P05 (2d756e8~1) оба набора падают.
  - Полный suite 427 OK; frontend 7/7 PASS. Браузер (стенд p02-smoke, админ): навигация, раздел «Что показывать»,
    админские блоки — 1366×768 и 390×844, горизонтальной прокрутки нет, ошибок JS нет.
Не проведено: Docker, прод, реальная рассылка Telegram.
Статус: READY по аудиту кода Codex; не выпущен. См. docs/P05_AUDIT_CODEX.md.
```

## Повторный аудит P04 — Codex, 2026-09-20 12:50

- E02 закрыто; глобальная городская разбивка E01 исправлена. Остаток E01: type_totals рассчитывается до
  shop/category/search, поэтому при пустом поиске total=0, а разбивка остаётся 6/6/1.
- Точные критерии и следующий шаг — в docs/P04_AUDIT_CODEX.md. P04 не READY.
- 431 Python-тест OK, frontend 7/7 PASS. Код и прод не менялись.

## Карточка задачи: P04 (Claude → аудит Codex завтра)

```text
ID / этап: P04. Полный аудит UI и данных
Основание/поручение владельца: пока аудит P03 ждёт Codex — «Начать P04 с анализа» (~00:52), затем «давай пачку
  сделаем завтра проверит» и «не будем просто пушить» (~00:55): делаем P04 целиком (анализ + исправления) пачкой,
  без push и выпуска; завтра Codex аудирует P03 и P04 вместе.
Исполнитель: Claude, начало 2026-09-20 00:53 Asia/Almaty.
Ветка: dev/p04-ui-audit от dev/p03-monitoring (P03 ещё не принят: исправления по аудиту P03 делаются в
  dev/p03-monitoring и переносятся в dev/p04-ui-audit merge-ом).
Результат анализа: docs/P04_UI_DATA_ANALYSIS.md.
Frontend-тесты теперь запускаются локально (2026-09-20 00:55): Deno 2.9.6 (/opt/homebrew/bin/deno) + npm playwright 1.55.0
  в scratchpad (симлинк node_modules в корне, исключён через .git/info/exclude), Chrome установлен:
  `for f in test_frontend*.cjs; do NO_COLOR=1 deno run -A --node-modules-dir=manual "$f"; done` — все 4 PASS
  на dev/p03-monitoring (test_frontend, _auth, _modal_image, _security). Это закрывает «frontend не проверен» для P01–P03
  на уровне существующих тестов (новую страницу /monitoring они не покрывают).
Checkpoint 00:58: анализ завершён — docs/P04_UI_DATA_ANALYSIS.md: дефекты U01–U09 (U01–U03 P1: счётчики с двумя
  писателями и разными определениями — бейдж «Супер-скидки» 13 682 при «Показано 150 из 11 732» на одном экране;
  U04 ошибки API не видны; U05–U07 вёрстка; U08 монолит index.html; U09 → P05) и план исправлений.
Реализация (2026-09-20 01:07, коммит f1b02c2): U01–U08 и U10 исправлены, U09 перенесён в P05. Итоги и доказательства —
  в конце docs/P04_UI_DATA_ANALYSIS.md.
Проверки: ./venv/bin/python -m unittest discover -s . -p "test_*.py" → 417 OK (новый test_ui_data.py: 3);
  frontend (Deno) 6/6 PASS, новые test_frontend_format.cjs и test_frontend_p04.cjs; на коде до исправлений оба новых
  набора падают (frontend — ровно на U01: бейдж перезаписан числом витрины 9 999 вместо 11 732). Браузер со стилями:
  390/768/1366/1920 px — горизонтальной прокрутки нет, счётчики совпадают (11 732 на стенде, Астана), 10 иконок + «+15»
  на телефоне, «Мониторинг» виден.
Не проведено: Docker, прод. Офлайн frontend-тесты без Tailwind — геометрия проверена только в браузере.
Исправления по аудиту (2026-09-20 12:47): E01 — разбивка «из них арбитраж» считается по тому же набору
  витрины после склейки (`type_totals` из `get_store_deals`, кэш `store_deals_counts`), а не по ленте алертов;
  E02 — в запросе алертов витрины добавлено `p.current_price = a.new_price`, алерт с устаревшей ценой не попадает
  ни в список, ни в счётчики. Регрессии: test_ui_data (2 новых), test_store_deals (2 новых, фикстура приведена
  в соответствие детектору). Python 431 OK, frontend 7/7 PASS; счётчики сверены на копии рабочей базы.
Повторный аудит (12:50): E02 закрыто; остаток E01 — разбивка не учитывала фильтры списка. Исправлено 12:55:
  type_totals считается после фильтров магазина/категории/поиска и до вкладки и страницы; регрессии на 10 наборов
  фильтров и на независимость от сортировки и пагинации. Python 432 OK, frontend 7/7 PASS.
Статус: READY по аудиту кода Codex — E01/E02 закрыты на 67b4f9d; не выпущен.
Историческая команда первого аудита: «Прочитай AGENTS.md, docs/DEVELOPMENT_PLAN.md и docs/AGENT_HANDOFF.md.
  Проведи (1) точечный аудит группировки D01 P03 (коммит 7914071) и (2) аудит P04 на ветке dev/p04-ui-audit
  (diff 142515c..f1b02c2, docs/P04_UI_DATA_ANALYSIS.md), не меняя код. Запиши выводы в docs/P03_AUDIT_CODEX.md и
  docs/P04_AUDIT_CODEX.md, обнови карточки и реестр.»
```

## Исправление D01 — группировка по области операции (Claude, 2026-09-20 00:46–00:47, коммит 7914071)

- Ключ группировки инцидента = (component, type, shop, signature, scope), где scope = `_incident_scope` (область
  восстановления, например ai:user / ai:normalize); стабильный id — sha1 этого ключа. Область группировки совпадает
  с областью восстановления: ошибки разных операций с одинаковым исходом — разные инциденты.
- Регрессии: воспроизведение Codex (normalize error → user error → normalize ok: два инцидента, normalize recovered
  count 1, user open без recovered_at, id стабильны и различны) в обоих порядках; одинаковый producer-исход empty
  тоже разделяется. На коде до исправления все 3 падают. test_monitoring — 36, полный suite — 414 OK.
- Следующий шаг: точечный аудит Codex (коммит 7914071). Команда: «Прочитай AGENTS.md, docs/DEVELOPMENT_PLAN.md и
  docs/AGENT_HANDOFF.md. Проведи точечный аудит группировки D01 P03 (коммит 7914071, раздел «Исправление D01 —
  группировка по области операции» в AGENT_HANDOFF.md), не меняя код. Запиши вывод в docs/P03_AUDIT_CODEX.md и
  карточку, обнови статус P03.»

## Исправление остатка D01 — восстановление воркеров (Claude, 2026-09-20 00:41–00:41, коммит 610089c)

- Область успеха компонентного инцидента — конкретная операция (`_incident_scope`), без запасного перехода на
  компонент целиком: system_error where=ai_normalize_worker → AI-вызов с purpose=normalize (ai_service scan=True),
  notification_worker → сводка deliver_pending с sent > 0, auto_scan_worker → scan_end completed. У telegram_polling,
  http_handler и неизвестных where сигнала успеха в V1 нет: инцидент открыт, после 24 ч без повторов — quiet без
  recovered_at. Инциденты ai_query разделены по purpose (ai:user / ai:normalize); успехи — `_component_success`.
- Регрессии (test_monitoring.py, всего 33): воспроизведение Codex — telegram_polling (WARNING, формат producer
  telegram_bot.py) → успешная доставка → open, через 25 ч → quiet без recovered_at; пользовательский AI-вызов не
  закрывает ошибку ai_normalize_worker, вызов normalize — закрывает; notification_worker закрывается доставкой;
  неизвестный where не восстанавливается успехом компонента. На коде до исправления 3 теста падают (положительный
  тест notification_worker проходит на обоих).
- Полный suite: 411 OK.
- Следующий шаг: точечный аудит Codex (коммит 610089c). Команда: «Прочитай AGENTS.md, docs/DEVELOPMENT_PLAN.md и
  docs/AGENT_HANDOFF.md. Проведи точечный аудит остатка D01 P03 (коммит 610089c, раздел «Исправление остатка D01» в
  AGENT_HANDOFF.md), не меняя код. Запиши вывод в docs/P03_AUDIT_CODEX.md и карточку, обнови статус P03.»

## Исправления P03 по аудиту Codex D01–D02 (Claude, 2026-09-20 00:32–00:35, коммит 8454503)

- D01 (восстановление по области): incidents() соотносит восстановление с областью инцидента.
  - Категории магазина: у КАЖДОЙ затронутой категории после её последней проблемы должен быть подтверждённый успех
    (`_category_success`: scan_category INFO, complete=true, без error, quality ok/unknown — limited и warning не
    считаются), либо recovery магазина (P02: complete после деградации). Иначе открыт, `recovery_progress` N из M.
    Проблема после успеха снова открывает.
  - HTTP без категории: у каждого затронутого хоста после последней проблемы есть минутный бакет со status_2xx > 0
    (telemetry_http_aggregates), либо recovery.
  - Магазин без категории/хоста (degradation, падение магазина): только recovery. recovery пишется с ключом магазина,
    проблемы — с названием: сопоставление через параметр shop_keys (название → ключ из реестра).
  - Компоненты: явный успешный исход своего компонента (`_component_success`: AI outcome ok и provider_called, поиск
    found/not_found, Telegram sent > 0, scan_end completed, backup ok; system_error по where: ai_normalize_worker → AI,
    notification_worker/telegram_polling → Telegram, auto_scan_worker → scheduler) и час без повторов. Для областей без
    сигнала успеха (например http_handler) — через 24 ч без повторов состояние `quiet` («Затих · не подтверждено»),
    не «восстановлен» и не открыт.
  - Поля инцидента: state (open/recovered/quiet), recovery_progress.
- D02 (Telegram): неуспешные попытки = retry + failed + errors + ошибки воркера доставки за 24 ч; cancelled — не
  попытка. Причина в статусе расписывает временные/постоянные/исключения/воркер. Пауза 429 — degraded.
- Производительность (замечание Codex): incidents читает только проблемы (WARNING+) и события, способные подтвердить
  успех. Замер на синтетической истории 7 дней, 70 500 событий (7k категорий, 30k HTTP-предупреждений, 30k поиска,
  2k AI, 1k Telegram, 500 системных ошибок), БД 22 МБ: incidents 0,22 с (3027 инцидентов), overview 0,41 с,
  shop_detail 0,22 с, events 0,002 с. Из-за возможного числа инцидентов API отдаёт не больше 200 (открытые первыми) с
  total/open_total, карточка магазина — не больше 50; UI пишет «показаны N из M».
- Регрессии (test_monitoring.py, всего 30): воспроизведение Codex Phones→TV (открыт, 0 из 1); limited и warning не
  восстанавливают; подтверждённый успех той же категории восстанавливает (id стабилен); частичное восстановление
  двух категорий; повтор после успеха; recovery закрывает инцидент магазина без категории; HTTP без категории —
  по 2xx хоста; компонент — пропуск вызова AI не успех, успешный вызов + час тишины — успех; http_handler → quiet
  через 24 ч; Telegram: только retry → degraded, 10 % → healthy, 30 % → degraded, нет попыток и только cancelled →
  unknown, нет токена → disabled, пауза → degraded, ошибки воркера → degraded; API инцидентов отдаёт total/open_total.
  На коде до исправлений 13 из 16 новых/изменённых падают (часть — из-за нового параметра shop_keys).
- UI (встроенный браузер, стенд p03-smoke): состояния «Открыт · … · восстановлено N из M», «Восстановлен»,
  «Затих · не подтверждено»; инцидент Мечты по таймаутам теперь открыт — в данных стенда не было подтверждённого
  успеха и 2xx этого хоста (раньше его закрывал любой INFO). Ошибок JS нет.
- Полный suite: 408 OK. Не проведено: frontend-тесты (нет node), Docker, прод.
- Следующий шаг: повторный аудит Codex (diff d6dc6ca..8454503). Команда: «Прочитай AGENTS.md, docs/DEVELOPMENT_PLAN.md
  и docs/AGENT_HANDOFF.md. Проведи повторный аудит исправлений P03 D01/D02 (коммит 8454503, раздел «Исправления P03
  по аудиту Codex» в AGENT_HANDOFF.md), не меняя код. Запиши выводы в docs/P03_AUDIT_CODEX.md и карточку, обнови
  статус P03.»

## Карточка текущей задачи: P03 (Claude → аудит Codex)

```text
ID / этап: P03. Monitoring Center V1
Основание/поручение владельца: «Начинай P03» (2026-09-19 ~23:49).
Статус: READY по аудиту кода Codex — D01/D02 закрыты; не выпущен.
Исполнитель: Claude, начало 2026-09-19 23:50 Asia/Almaty.
Checkout / ветка / базовый HEAD: /Users/molthun/Documents/kz-price-hunter / dev/p03-monitoring / 0426523 (main, v5.9.0 + docs).
Чужие изменения до начала: нет.
Есть сейчас: /api/admin/shops (сводка shop_scans + P02 freshness/quality), вкладка «Логи» (буфер stdout),
  телеметрия P01 (telemetry_events, telemetry_http_aggregates), качество P02 (source_scans). Экрана мониторинга нет.
Дизайн:
  - Бэкенд monitoring.py (только чтение, данные P01/P02 + shop_scans/scan_state/outbox) и /api/admin/monitoring
    (обзор и все разделы), /api/admin/monitoring/shop/{key} (карточка), /api/admin/monitoring/events (журнал с фильтрами),
    /api/admin/monitoring/incidents. Все — require_admin (гость 401, пользователь 403).
  - Страница /monitoring (web/templates/monitoring.html): отдельная, адаптивная (390 px … 1920 px), разделы обзор /
    магазины / сканирование / поиск / AI / Telegram / система / события; краткое состояние + раскрываемая диагностика.
    В index.html — admin-only вкладка-ссылка «Мониторинг». Страница без данных для не-админа (данные только через API).
  - Статусы (нет ложного зелёного): unknown — нет наблюдений; healthy; limited; degraded; offline; empty; disabled.
    Магазин: disabled — выключен в настройках; unknown — ни одного итога обхода; offline — последний итог failed и нет
    полного обхода > 72 ч (или 3+ неудачи подряд); degraded — partial / качество degraded|warning / freshness aging|stale;
    empty — последний итог без товаров; limited — последний итог limited; healthy — complete, качество ok|unknown, fresh.
    Разделы: сканирование — offline, если последний scan_end старше 2 × max(интервал волны, 3 ч) или его нет → unknown;
    поиск/AI/Telegram — unknown без событий за 24 ч, degraded при доле ошибок > 20 %; AI/Telegram — disabled без ключа/токена.
  - Инциденты: группировка событий severity ≥ WARNING за 7 дней по (component, type, shop, признак ошибки: код/класс/
    причина качества); начало, последнее появление, число, масштаб (категории/хосты), гипотеза причины (403/429 —
    блокировка/лимит, timeout/connection — сеть/недоступность сайта, quality — смена вёрстки/тихая поломка, …),
    восстановление — последующее успешное событие того же магазина/компонента (recovery, scan_category без ошибки).
План: P03-01 бэкенд + API; P03-02 страница и вкладка; P03-03 тесты (статусы, инциденты, доступ, 25/50/100 источников)
  и проверка в браузере на 390 px / tablet / 1366×768 / 1920×1080.
Checkpoint 23:55: P03-01/P03-02 реализованы (коммит 8bacb56, WIP):
  - monitoring.py — shop_status / rate_status / worst (чистые правила статусов), shops_overview, shop_detail
    (категории с качеством и нормой из source_scans, HTTP за 24 ч по магазину: суммы часовых бакетов и «p95 худшего
    часа» — p95 суток из часовых не выводится), разделы scanning/search/ai/telegram/system, events (журнал с фильтрами),
    incidents (группировка WARNING+ за 7 дней; id — sha1 ключа, стабилен между процессами), overview.
  - web/server.py: GET /monitoring (страница без данных), GET /api/admin/monitoring, /shop/{key}, /events, /incidents
    (require_admin).
  - web/templates/monitoring.html — отдельная адаптивная страница: 8 разделов, карточка магазина (модальное окно),
    журнал с фильтрами, инциденты; всё экранируется; 401/403 — понятное сообщение без данных; автообновление 60 с.
  - index.html: admin-only ссылка «Мониторинг» во вкладках.
  - Проверено на копии prices.db.backup_1789811567: overview 0,43 с на 25 магазинах; статусы честные (3 магазина с
    проблемами, разделы без событий — unknown, AI/Telegram без ключей — disabled).
Осталось: P03-03 — тесты (статусы healthy/degraded/offline/limited/empty/unknown/disabled, отсутствие данных,
  инциденты и восстановление, доступ гость/пользователь/админ, 25/50/100 источников), проверка в браузере на 390 px,
  tablet, 1366×768, 1920×1080 с длинными названиями; затем журнал, передача на аудит Codex.
P03-03 выполнен (2026-09-20 00:01, коммит 759e656):
  - test_monitoring.py (14 тестов): все статусы магазина на управляемом времени (healthy, limited, degraded из-за
    partial/качества/aging/одиночного failed, offline после 3 неудач / без полного обхода > 72 ч / без полного обхода
    вообще, empty, disabled); отсутствие наблюдений (None, {}, unknown, running без данных) — unknown, не зелёный;
    rate_status/worst; пустая БД → shops/scanning/search unknown, Telegram disabled, общий статус не healthy;
    25/50/100 источников — полный обзор < 3 с, выключенный в конце, счётчики сходятся; сканирование offline, когда
    обходы прекратились; доля ошибок поиска > 20 % → degraded; инциденты: группировка 403 (масштаб категорий/обходов/
    хостов, гипотеза «блокировка»), восстановление после успешной категории, стабильный id, разные причины — разные
    инциденты, компонентный инцидент закрывается после часа тишины; фильтры журнала; карточка без данных
    (категории «нет нормы», пустые HTTP/история); гипотезы; доступ: гость 401, пользователь 403, админ 200 для всех
    /api/admin/monitoring*, страница /monitoring без данных магазинов, неизвестный магазин 404.
  - Браузер (встроенный, локальный стенд на копии prices.db.backup_1789811567 с синтетической телеметрией: магазины
    во всех состояниях, серия 403, таймауты с восстановлением, ошибки поиска и системы, HTTP Kaspi; без фоновых задач
    и сети; вход разработчика): гость — только приглашение войти; админ — обзор, «Магазины» (таблица на ≥ 768 px,
    карточки на 390 px), карточка магазина (обходы, HTTP с «нет данных», категории с качеством/нормой/причиной),
    «События» с фильтрами и инцидентами. Проверено 390×844, 768×1024, 1366×768, 1920×1080 — горизонтальной
    прокрутки нет, длинные названия обрезаются с подсказкой. Ссылка «Мониторинг» на главной видна админу, гостю — нет.
    По итогам проверки исправлено: бейджи инцидентов («Открыт · ошибка / предупреждение», «Восстановлен» вместо
    статусов магазина), гипотезы для system_error/search/ai, короткий заголовок на телефоне.
  - Полный suite: ./venv/bin/python -m unittest discover -s . -p "test_*.py" → Ran 392, OK.
Не проведено: frontend-тесты (нет node), Docker, реальные данные прода (нет доступа), проверка производительности
  на полной истории telemetry_events за 30 дней (запросы инцидентов читают 7 дней; при большом объёме может
  потребоваться индекс/ограничение — рекомендую аудитору оценить).
Ограничения V1 (записано в коде/UI): HTTP за сутки — суммы часовых бакетов, «p95 худшего часа» (p95 суток из часовых
  не выводится); события поиска ограничены 60/мин — раздел поиска показывает выборку; Git SHA/uptime/heartbeat — P14.
Следующий шаг: аудит Codex P03 (diff ad60897..759e656). Команда: «Прочитай AGENTS.md, docs/DEVELOPMENT_PLAN.md и
  docs/AGENT_HANDOFF.md. Проведи аудит P03 на ветке dev/p03-monitoring (diff ad60897..759e656, карточка P03), не меняя
  код. Запиши выводы в docs/P03_AUDIT_CODEX.md и карточку, обнови статус P03.»
```

## R-P02 — выпуск P02 → v5.9.0 (Claude, ВЫПУЩЕНО 2026-09-19 23:46)

- Разрешение владельца: «Выпускай 5.9.0» (2026-09-19 ~23:40).
- Выполнено: fetch (origin/main = 6302ba0, тега не было, origin/dev/p02-quality = локальной) → `git merge --ff-only
  dev/p02-quality` в main (264d5bd) → `git tag -a v5.9.0 dd6ab95` → push main, push тега. PR #1 закрылся как MERGED.
- GitHub Actions: CI (main) success; Build and Publish Docker Image (main → latest) success; (v5.9.0) success.
- Прод (только GET): /api/version 5.9.0 с 23:46:52. /api/stats — 77980 видимых товаров (до выката 77922), 17844 скидок,
  145 алертов. /api/best-price?q=samsung galaxy — 227 предложений, все fresh, лучшая цена 10890 (fresh), поля
  freshness/stale_count/all_stale присутствуют. /api/products?limit=200 — freshness у всех (fresh).
- Не проверено на проде: запись source_scans и оценок качества (появятся после ближайших обходов), доставка алертов,
  бейджи в браузере на проде; доступа к БД/контейнеру нет. Рекомендация владельцу: после ближайших волн посмотреть
  события degradation и data.quality в scan_category (частота warning/degraded на реальной истории), при желании
  внутри контейнера `sqlite3 data/prices.db "select quality, count(*) from source_scans group by quality"`.
  Откат: образ ghcr.io/molthun/kz-price-hunter:5.8.0, БД восстанавливать не нужно.

### Подготовка (история)


- Поручение владельца 2026-09-19 ~23:19: «Подготовить выпуск 5.9.0». Push в main, тег и выкат — только после отдельного
  разрешения. Выполнено Claude 23:19–23:20 Asia/Almaty.
- Сделано: версия 5.8.0 → 5.9.0 (minor): version.py, CHANGELOG.md, ROADMAP_AND_LOG.md, README.md; релизный коммит dd6ab95.
  Полный suite после бампа: 378 OK.
- Обновление/откат на копии prices.db.backup_1789811567 (backup API, scratchpad, код main=5.8.0 в отдельном worktree):
  5.8.0 → код P02 (init_db) → снова 5.8.0 на той же базе. Во всех трёх запусках: schema_version 5, integrity ok, бэкап
  перед миграцией не создавался (версия не менялась), видимых товаров 33294, скидок 13684, алертов 17, поиск «samsung»
  226, лучшая цена совпадает. Код P02 создал source_scans; 5.8.0 с ней работает. Данные копии свежие (≤ 10 ч), поэтому
  изменение видимости Aging/Stale на ней не проявляется — ожидаемый эффект на проде: товары магазинов, не обходившихся
  > 24 ч, снова видны с бейджем.
- UI проверен ранее во встроенном браузере (P02 и C03, см. карточку P02).
- Не проведено: Docker-сборка/smoke (нет docker локально), реальный обход, frontend-тесты (нет node), прод.
- Шаги выпуска после разрешения: `git checkout main && git merge --ff-only dev/p02-quality` (fast-forward: main 4bbde64,
  ветка впереди, позади 0) → `git tag -a v5.9.0 dd6ab95` → `git push origin main`, `git push origin v5.9.0` → CI →
  Watchtower ~5 мин → проверка чтением /api/version = 5.9.0, /api/stats, /api/best-price.
- Рекомендации владельцу: бэкап data/prices.db перед выкатом (SQLite backup API в контейнере); новых env нет.
  Откат: образ ghcr.io/molthun/kz-price-hunter:5.8.0, БД восстанавливать не нужно. После выката наблюдать события
  degradation и quality в scan_category (частота warning/degraded на реальной истории) и отчёт по магазинам.

## Карточка текущей задачи: P02 (Claude → аудит Codex)

```text
ID / этап: P02. Data Quality & Freshness
Основание/поручение владельца: «Начинай P02» (2026-09-19 ~19:46).
Статус: READY по аудиту кода — Codex закрыл C01–C03; выпуск и проверка прода не выполнены.
Исполнитель: Claude, начало 2026-09-19 19:47 Asia/Almaty.
Checkout / ветка / базовый HEAD: /Users/molthun/Documents/kz-price-hunter / dev/p02-quality / 4bbde64 (main).
Чужие изменения до начала: нет.

Текущее поведение (по коду 4bbde64):
  - products.updated_at обновляется только при фактическом получении товара в выдаче (save_or_update_products_batch)
    → это и есть last_seen_at предложения. price_observations.observed_at — время наблюдения цены (пишется при изменении).
    shop_scans.last_success_at — время последнего обхода магазина со статусом complete.
  - reconcile_source: результат complete снимает (is_active=0) товары источника, не пришедшие в выдаче.
    РИСК: «тихая поломка» с complete (5000 → 83) сейчас сняла бы ~4900 предложений. Проверки качества нет.
  - active_product_clause скрывает из поиска/витрины товары с updated_at старше max(24 ч, 2 × scan_interval (180 мин))
    = 24 ч. При сбое магазина дольше суток весь его каталог исчезает — противоречит P02 («FAILED сохраняет прежние
    товары, устаревшие цены явно обозначены»). Полный круг волн — до 24 ч, т.е. граница уже на пределе.
  - PagedScraper: повтор страницы → limited (не complete); пустая страница → complete только при явном
    подтверждении адаптера. Адаптеры вне PagedScraper (dns, shopkz, ants, fourmobile, itmag, ispace) проверить тестами.

Дизайн:
  1. Метрики результата категории (источник = shop_key + source_url; город входит в URL/конфигурацию источника):
     received (от адаптера), valid, rejected (нет id/названия/цены/URL, цена вне диапазона), заполненность
     price/title/image/SKU(id), статус complete/limited/failed/degraded, причина, время начала/конца.
     Хранение: новая таблица source_scans (аддитивно, schema_version без изменений), retention 90 дней.
  2. Baseline источника: медиана valid по последним 5 ПРИНЯТЫМ результатам того же вида (complete с complete,
     limited с limited — у limited-by-design источников с max_pages своя норма). Degraded/failed в baseline не входят.
     Меньше 3 принятых результатов → baseline по числу активных предложений источника (product_sources.active),
     если их ≥ 20; иначе Unknown (проверка объёма не выполняется).
  3. Проверка качества до reconcile_source. Degraded → категория считается неуспешной: товары из выдачи сохраняются
     (это реальные наблюдения), но старые НЕ снимаются, результат не обучает baseline, магазин получает partial
     с причиной «качество: …» (событие degradation из P01, повтор по существующей политике).
  4. Freshness предложения по last_seen (updated_at) и магазина по last_success_at: Fresh / Aging / Stale / Unknown.
  5. API: freshness и last_seen_at у предложений (products, best-price, модели); по магазинам — freshness, последний
     полный обход, качество последнего обхода. UI: бейдж Aging/Stale у цены. Frontend-тесты локально не запускаются (нет node).

Предлагаемые пороги (ждут подтверждения):
  Q1 объём: degraded, если valid < 50 % baseline (при baseline ≥ 20); 50–80 % — warning (принимается, но не обучает
     baseline); ≥ 80 % — норма. Проверки плана: 5000→5100→4900 норма; 5000→83 degraded.
  Q2 поля: degraded, если rejected > 30 % от received (70 % пустых цен → degraded); доля с фото ниже baseline более
     чем на 30 п.п. — warning.
  Q3 freshness: Fresh ≤ 26 ч, Aging ≤ 72 ч, Stale > 72 ч, Unknown — нет времени.
  Q4 видимость устаревших: вариант A — не скрывать по возрасту, показывать с бейджем Aging/Stale; скрывать только
     снятые полным обходом (is_active=0) и предложения старше 30 дней. Вариант B — оставить скрытие через 24 ч,
     бейдж только Aging внутри окна.

План задач: P02-01 метрики и source_scans; P02-02 baseline и проверка качества до reconcile; P02-03 freshness в API/UI
  и политика видимости; P02-04 контрактные тесты адаптеров (повтор страницы, HTML challenge); P02-05 регрессии
  с управляемым временем и сценариями плана.
РЕШЕНИЕ ВЛАДЕЛЬЦА (2026-09-19 ~19:52, до реализации): Q1/Q2 — 50 % / 30 % (как предложено: объём < 50 % baseline при
  baseline ≥ 20 → degraded, 50–80 % → warning без обучения baseline; rejected > 30 % → degraded; фото −30 п.п. → warning).
  Q3 — Fresh ≤ 26 ч, Aging ≤ 72 ч, Stale > 72 ч. Q4 — вариант A: не скрывать по возрасту, бейдж Aging/Stale;
  скрывать только снятые полным обходом (is_active=0) и не виденные > 30 дней; Stale не участвует в «лучшей цене» и алертах.
Статус реализации: P02-01..P02-05 выполнены Claude 19:52–20:00, коммит 89d4191 (ветка dev/p02-quality). HANDOFF на аудит.

Сделано:
  - data_quality.py — пороги (константы с комментарием о решении владельца), is_valid_offer/measure (received, valid,
    rejected, with_image), baseline_from_history (медиана последних 5 принятых, минимум 3), assess (ok / warning /
    degraded / failed / unknown; learn; may_retire), freshness / annotate (last_seen_at, freshness, age_hours).
  - database.py — таблица source_scans (аддитивно, schema_version 5): итог каждой категории с метриками, качеством,
    причиной, baseline и accepted; get_source_baseline (история того же вида complete/limited, иначе число активных
    предложений источника ≥ 20, иначе None), record_source_scan, get_last_source_quality, prune_source_scans (90 дн.,
    вызывается в конце обхода рядом с другими сроками хранения).
    active_product_clause теперь = is_active и last_seen ≤ 30 дн. (раньше 24 ч); новый fresh_price_clause (≤ 72 ч)
    — для алертов (_fetch_filtered_alerts), витрины скидок/арбитража и их счётчика, доставки уведомлений (notifier).
    get_products_list и отчёт по магазинам — freshness; отчёт — last_quality/last_quality_reason (худшая категория
    последнего scan_id).
  - web/server.py::_scan_shop_categories — measure → get_source_baseline → assess ДО reconcile_source. Degraded:
    error «Качество: …» (категория в failed_categories → магазин partial, событие degradation P01, повтор по
    существующей политике), reconcile без снятия, не обучает baseline; товары из выдачи сохраняются как наблюдения.
    Warning — принимается (может снимать), не обучает. Исключение адаптера — строка failed в source_scans.
    Качество и метрики — в data.quality события scan_category. Свежесть в /api/products/{id} и сравнении моделей
    (мин/макс/экономия только по неустаревшим).
  - search_engine.py::_get_best_price_summary — annotate всех предложений; best_deal, price_stats, store_comparison,
    comparable — только не-Stale; stale_count, all_stale (best_deal = None, если свежих нет).
  - web/templates/index.html — freshnessBadge (Aging: «⏳ цена от <дата>», Stale: «⚠️ цена устарела (N дн.)») в таблице
    лучших цен, карточке лучшей цены, каталоге и окне сравнения моделей; «Лидер»/«Лучшая цена» не у Stale.
Ограничение (записано в data_quality.py): адаптеры сами отбрасывают карточки без цены (часто товары не в наличии),
  поэтому rejected видит только возвращённое адаптером; массовая потеря цен ловится порогом объёма. Правка 24 адаптеров
  не делалась (план: не переписывать все адаптеры одновременно).
Проверки (2026-09-19, macOS, Python 3.14.7 venv):
  - ./venv/bin/python -m unittest test_data_quality → 21 OK: пороги (5000/5100/4900 ok, 5000→83 degraded, 70 % пустых
    цен degraded, warning не обучает), baseline, переходы свежести на управляемом времени (26/72 ч, формат SQLite),
    сквозной _scan_shop_categories на временной БД (тихая поломка 200→3 — каталог сохранён, partial, baseline не
    испорчен; baseline по активным предложениям при нехватке истории; нормальные изменения снимают исчезнувшие;
    FAILED сохраняет товары и не освежает непришедшие), видимость (Aging/Stale видны, > 30 дн. скрыты, Stale не лучшая
    цена и не скидка/уведомление), контракт: повтор первой страницы → не complete; HTML challenge → ни один из 24
    адаптеров реестра (кроме DNS/Playwright) не даёт complete и товаров.
  - Полный suite: ./venv/bin/python -m unittest discover -s . -p "test_*.py" → Ran 362, OK.
  - UI (встроенный браузер, локальный сервер на копии prices.db.backup_1789811567, без фоновых задач и сети; часть
    предложений состарена): поиск «Samsung Galaxy» — лучшая цена 21 275 ₸ (Aging, бейдж), две более дешёвые Stale
    с бейджем «цена устарела (4 дн.)» и «не сравнивается», в сравнении магазинов Halyk без устаревшей цены. Ошибок JS нет
    (в консоли только 401 закрытых эндпоинтов без входа и 404 внешних картинок).
Не проведено: frontend-тесты (нет node), Docker, реальный обход магазинов, прод. Поведение на реальной истории
  (частота warning/degraded) проверится только после выката — рекомендую наблюдать события degradation/scan_category.
Изменение поведения для пользователей: товары сбойного магазина больше не исчезают через 24 ч, а видны до 30 дней
  с бейджем; скидки/алерты — только по ценам не старше 72 ч.
Следующий шаг: аудит Codex P02 (diff 7a38304..89d4191 и эта карточка). Команда: «Прочитай AGENTS.md,
  docs/DEVELOPMENT_PLAN.md и docs/AGENT_HANDOFF.md. Проведи аудит P02 на ветке dev/p02-quality (diff 7a38304..89d4191,
  карточка P02), не меняя код. Запиши выводы в docs/P02_AUDIT_CODEX.md и карточку, обнови статус P02.»
```

## Аудит P02 Codex — 2026-09-19 23:01

- Срез `c11820e`, ветка `dev/p02-quality`, diff `7a38304..89d4191`; рабочее дерево до аудита чистое.
- C01 (P1): 200 копий одного ID обходят quality gate и снимают 199 из 200 товаров. Нужна оценка уникальных предложений и единая дедупликация до reconcile.
- C02 (P1): find_market_comparisons выбирает Stale-конкурентов до 30 дней и создаёт арбитраж по четырёхдневной цене. Проверять свежесть обеих сторон сравнения.
- C03 (P2): all-stale оставляет статистику/сравнение и лидерство по устаревшим ценам. Нужен отдельный результат «нет актуальных цен» при сохранении items.
- Полный suite независимо повторён с временным DATA_DIR: 362 OK, 32.467 с. Все три замечания воспроизведены на временных данных. Подробности и критерии — [P02_AUDIT_CODEX.md](P02_AUDIT_CODEX.md).
- Следующий точный шаг: Claude исправляет C01–C03 и добавляет регрессии, затем повторный аудит Codex. Пороги Q1–Q4 сохраняются. Агент не запускался, выпуск не выполнялся.
- Изменения только документации: `docs/P02_AUDIT_CODEX.md` (новый) и этот реестр. Без коммита/push; код/рабочая БД/прод не менялись. Фоновых процессов аудита нет.

## R-P01 — выпуск P01 → v5.8.0 (Claude, ВЫПУЩЕНО 2026-09-19 19:41)

- Разрешение владельца: «Да, выпускай» (2026-09-19 ~19:38).
- Выполнено: fetch (origin/main = ebf36cc, тега v5.8.0 не было) → `git merge --ff-only dev/p00-baseline` в main (6302ba0) →
  аннотированный тег v5.8.0 на f668383 → `git push origin main`, `git push origin v5.8.0`.
- GitHub Actions: CI (main) success; Build and Publish Docker Image (main → latest) success; (v5.8.0) success.
- Прод (только чтение): /api/version 5.7.1 в 19:40:48 → кратко страница 404 Synology во время перезапуска контейнера
  (19:41:18) → 5.8.0 в 19:41:48. /api/stats 200 (0.19 с, 77922 товара), /api/products?limit=1 200 (0.06 с).
- Не проверено на проде: запись телеметрии в БД прода, создание таблиц telemetry_*, фоновый поток сброса — нет доступа
  к контейнеру/БД. Рекомендация владельцу: в логах контейнера не должно быть «[Telemetry] Сброс в БД не удался»;
  при желании — `sqlite3 data/prices.db "select type, count(*) from telemetry_events group by type"` внутри контейнера
  после ближайшего обхода. Откат: образ ghcr.io/molthun/kz-price-hunter:5.7.1, БД восстанавливать не нужно.

### Подготовка (история)


- Поручение владельца 2026-09-19 ~19:34: «Подготовить выпуск P01». Push в main, тег и выкат — только после отдельного
  разрешения владельца. Выполнено Claude 19:34–19:37 Asia/Almaty.
- Сделано:
  1. Слита ветка T01 `claude/affectionate-lehmann-490dc0` (merge `a49e3de`); конфликт только в ROADMAP_AND_LOG.md.
     Полный suite без DATA_DIR: 341 OK; mtime/размер prices.db до и после прогона не изменились.
  2. Версия 5.7.1 → 5.8.0 (minor: новая функциональность): version.py, CHANGELOG.md, ROADMAP_AND_LOG.md, README.md.
     Релизный коммит `f668383` «release: P01 telemetry foundation (v5.8.0)». Suite после бампа: 341 OK.
  3. Smoke на копии prices.db.backup_1789811567 (backup API, scratchpad, без фоновых задач и сети): create_app → init_db;
     /api/version, /api/stats, /api/products, /api/best-price (Астана и произвольный city) — 200, /api/products/<нет> — 404;
     schema_version 5, 33332 товара; события search_query записаны (city astana / unknown), маркер ПДн в БД отсутствует,
     колонка status_codes создана.
  4. Откат: код 5.7.1 (ebf36cc, отдельный worktree) на этой же обновлённой копии — init_db без ошибок и без бэкапа,
     /api/version, /api/stats, /api/best-price — 200, record_shop_scan_result работает. Откат образа на 5.7.1 не требует
     восстановления БД.
- Не проведено: Docker-сборка/smoke образа (нет docker локально), реальный обход магазинов с сетью, реальные AI/Telegram,
  frontend-тесты (нет node), прод.
- Шаги выпуска (после разрешения владельца; выполняет Claude, т.к. push разрешён как проектное действие):
  1. `git checkout main && git merge --ff-only dev/p00-baseline` (main сейчас ebf36cc, dev впереди на 23+ коммита, позади 0).
  2. `git tag v5.8.0` на релизном коммите; `git push origin main` и `git push origin v5.8.0` (≤ 3 тегов за раз).
  3. CI собирает ghcr.io/molthun/kz-price-hunter:latest и :5.8.0; Watchtower обновляет контейнер в течение ~5 мин.
  4. Проверка только чтением: `curl https://shop.molthun.ru/api/version` → 5.8.0.
- Рекомендации владельцу (NAS — только владелец): перед выкатом сделать бэкап data/prices.db через SQLite backup API в
  контейнере (как для 4.6.0); новых обязательных env нет (TELEMETRY_ENABLED по умолчанию 1, выключение — =0 в NAS compose
  и `docker compose up -d`). Откат: образ ghcr.io/molthun/kz-price-hunter:5.7.1, БД восстанавливать не нужно.
  После выката наблюдать рост таблиц telemetry_* (ожидаемо ограничены retention) и логи «[Telemetry] Сброс в БД не удался».

## Повторный аудит B01/B02 Codex — 2026-09-19 19:31

- Проверен HEAD `d0790b4`, diff `6843818..158c68e`; до аудита рабочее дерево чистое.
- B01/B02 закрыты, новых блокирующих замечаний к исправлениям нет. Подтверждение и ограничения — в начале [P01_AUDIT_CODEX.md](P01_AUDIT_CODEX.md).
- 339 тестов OK на временном DATA_DIR, 32.027 с; отдельные проверки реальных переходов магазина и сохранённого city на временной БД проходят.
- P01 — READY по аудиту реализации. Это не DEPLOYED и не разрешение на выкат: frontend/Docker/живые интеграции/прод не проверены. P00 отдельно не принимался.
- Следующий точный шаг: назначить реализацию P02 по DEVELOPMENT_PLAN. При выборе выпуска P01 сначала выполнить оставшиеся релизные проверки на целевом окружении. В рамках текущего поручения P02 не запускался, выпуск не выполнялся.
- Код и рабочая БД не менялись; изменены только `docs/P01_AUDIT_CODEX.md` и `docs/AGENT_HANDOFF.md`, без коммита/push. Фоновых процессов аудита нет.
- Этот раздел и верхняя контрольная точка актуальны; прежние брифы и выводы сохранены как история.

## Завершённые задачи

### P00. Стабильная база (READY, 2026-09-19)
```text
ID / этап: P00. Стабильная база
Цель и критерии приёмки:
  - Зафиксировать HEAD, версию приложения и схемы, тестовое окружение.
  - Проверить предполагаемый нестабильный fallback-ID Arbuz до исправления.
  - Проверить scraper contracts, backup_check, открытие/миграцию копии БД.
  - Отметить отсутствие прямого доступа к проду.
  - Создать отдельную ветку разработки.
Основание/поручение владельца: Поручение: «Подхвати текущую задачу по AGENTS.md и docs/AGENT_HANDOFF.md».
Статус: READY (реализация и замеры завершены, готова к ревью)
Исполнитель / предыдущий исполнитель / следующий: Antigravity / Codex (план) / Claude/Codex (аудит) -> Antigravity (P01)
Начало и checkpoint: 2026-09-19 18:10 – 18:15 (Asia/Almaty)
Checkout / ветка / базовый HEAD / текущий HEAD:
  - Checkout: /Users/molthun/Documents/kz-price-hunter
  - Ветка: dev/p00-baseline
  - Базовый HEAD: ebf36cc
  - Текущий HEAD: ebf36cc
Зависимости: отсутствуют
Область изменений (файлы): docs/AGENT_HANDOFF.md
Чужие изменения, замеченные до начала: отсутствуют (рабочее дерево было чисто).
Сделано (конкретное поведение):
  1. Создана ветка `dev/p00-baseline` от актуального HEAD `ebf36cc`.
  2. Зафиксирована версия приложения: 5.7.1 (version.py, CHANGELOG.md, ROADMAP_AND_LOG.md).
  3. Зафиксирована версия схемы БД: schema_version = 5 (миграции 1-5).
  4. Зафиксировано тестовое окружение: macOS, Python 3.14.7 (venv), SQLite 3.x; Node.js и Docker CLI не найдены в PATH; внешних credentials/SSH для доступа к проду нет.
  5. Запущен полный regression suite: Ran 278 tests in 28.549s, OK.
  6. Проверены scraper contracts (test_scraper_contract.py: 11 тестов OK) и matching precision (test_matching_precision.py: 4 теста OK).
  7. Зафиксированы базы данных и проверен backup_check:
     - `prices.db`: 127.3 МБ, 303 товара (тестовые данные), integrity: ok, schema_version: 5.
     - `prices.db.backup_1789811567`: 107.6 МБ, 33332 товара из 19 магазинов, integrity: ok, schema_version: 5.
  8. Замерена скорость поиска (search_in_database):
     - По `prices.db.backup_1789811567` (33k товаров): iphone (0 найдено, 37.5 мс), samsung (27 найдено, 32.1 мс), ноутбук (35 найдено, 27.5 мс), кофе (50 найдено, 4.0 мс), rtx (50 найдено, 12.1 мс).
  9. Подтверждён и задокументирован дефект в `scrapers/arbuz.py`:
     - Строка 82 использует `hash(product_url)`. Из-за рандомизации SipHash в Python 3 между процессами генерируются разные ID для одного и того же URL (воспроизведено в отдельных процессах: 71499347 vs 97851216). В реальной БД товаров Arbuz пока 0 (магазин добавлен недавно). Требует перехода на детерминированный `hashlib.sha256` в первом шаге P01.
  10. Проверено открытие и запуск `init_db()` на изолированной копии БД через SQLite backup API: инициализация проходит успешно без побочных эффектов.
Незавершённые правки: docs/AGENT_HANDOFF.md
Коммиты или расположение передаваемых файлов: ветка dev/p00-baseline
Проверки:
  - `./venv/bin/python -m unittest discover -s . -p "test_*.py"` (OK, 278 тестов)
  - `./venv/bin/python scripts/backup_check.py prices.db.backup_1789811567` (integrity: ok, schema: 5)
  - `./venv/bin/python scripts/backup_check.py prices.db` (integrity: ok, schema: 5)
  - `./venv/bin/python -c "print(hash(...))"` x2 (подтверждена нестабильность hash)
  - Скрипт бенчмарка search_engine.search_in_database (3.96–37.55 мс)
  - Скрипт backup API + init_db() на копии во временной директории (OK)
Не проведено / ограничения проверки:
  - Frontend suite (test_frontend*.cjs) не проверен: в окружении отсутствует Node.js.
  - Docker smoke не проверен: в окружении отсутствует Docker CLI.
  - Прод не проверен: локально отсутствуют доступ и секреты к целевому серверу.
Запущенные процессы: фоновых процессов сканирования/сервера нет (процессы ChatGPT app не затрагивают KZ Price Hunter).
Миграции / feature flags / данные для отката: не требуются (правки только в AGENT_HANDOFF.md).
Проблемы и принятые решения:
  - В рабочей `prices.db` находятся 303 тестовых товара; реальные 33k товаров сохранены в бэкапе `prices.db.backup_1789811567`. Бенчмарки и контракты валидированы на обоих.
Следующий точный шаг:
  - Приёмка/ревью P00 и старт P01: разработка P01-01 (устранение недетерминированного fallback-ID Arbuz и проектирование схемы телеметрии `telemetry_events` / `telemetry_aggregates`).
Автор реализации / независимый аудитор / замечания: Antigravity / ожидает независимого аудита Claude или Codex.
Готовность к выпуску: P00 завершён, изменений в продакшен-коде нет.
```

## Шаблон карточки задачи

Копировать на каждую небольшую задачу:

```text
ID / этап:
Цель и критерии приёмки:
Основание/поручение владельца (объём, что разрешено):
Статус:
Исполнитель / предыдущий исполнитель / следующий:
Начало и checkpoint (дата, время, часовой пояс):
Checkout / ветка / базовый HEAD / текущий HEAD:
Зависимости:
Область изменений (файлы):
Чужие изменения, замеченные до начала:
Сделано (конкретное поведение):
Незавершённые правки (staged/unstaged/untracked):
Коммиты или расположение передаваемых файлов:
Проверки (команда, окружение, дата, результат):
Не проведено / ограничения проверки:
Запущенные процессы (назначение, PID/порт, можно ли остановить):
Миграции / feature flags / данные для отката (без секретов):
Проблемы и принятые решения:
Следующий точный шаг:
Автор реализации / независимый аудитор / замечания:
Готовность к выпуску / разрешение на выкат / результат на проде:
```

## Протокол смены агента

1. Передающий агент сохраняет файлы и обновляет карточку, отмечает HANDOFF, причину (лимит/поручение/блокер), следующего агента и ближайшее действие. Не начинает большой новый шаг, когда лимит близок.
2. Порядок: Antigravity → Claude → Codex. При прямом поручении владельца возможен любой исполнитель. Возвращение предыдущего агента не даёт ему права одновременно продолжать занятую задачу.
3. Владелец открывает следующего агента в том же checkout и даёт команду подхвата ниже. Этот протокол не переключает приложения автоматически и не проверяет их токены.
4. Принимающий читает документы, проверяет ветку/HEAD/diff и незавершённые команды. Убеждается, что предыдущий агент больше не пишет в эти файлы, затем записывает себя исполнителем и IN_PROGRESS.
5. Если передача внезапная и карточка устарела, восстановить состояние по файлам, diff, истории и имеющимся результатам тестов. Отметить неопределённость. Не удалять незнакомые правки; повторить только проверки, необходимые для надёжного продолжения.
6. Если checkout другой, передавать доступную ветку/коммит или явно перечисленные незакоммиченные файлы/patch по поручению владельца. Одного текста карточки недостаточно: проверить наличие фактических изменений. Не переносить рабочую БД, секреты и settings с ключами в Git.
7. Продолжить оставшийся шаг; после него сохранить новый checkpoint. При завершении — REVIEW, далее READY после устранения замечаний; DEPLOYED только после проверки прода.

## Команда для любого следующего агента

> Прочитай AGENTS.md, docs/DEVELOPMENT_PLAN.md и docs/AGENT_HANDOFF.md. Подхвати текущую разрешённую задачу предыдущего агента с последнего checkpoint. Сначала проверь фактические изменения и отсутствие второго пишущего исполнителя. Сохрани готовую работу, выполни оставшиеся шаги и необходимые проверки, обнови реестр и карточку передачи. Не расширяй объём задачи и не выпускай изменения на прод без поручения владельца.

Если активной задачи ещё нет: «Начни P00 из docs/DEVELOPMENT_PLAN.md, соблюдая AGENTS.md; зафиксируй задачу и checkpoint в docs/AGENT_HANDOFF.md».

## История передачи пачки на аудит (до заключения Codex)

### Передача Claude


- Дата: 2026-09-20 17:42, Asia/Almaty.
- Последнее действие: Claude реализовал **P09 (AI для качества каталога — детерминированная часть)** на ветке
  `dev/p09-catalog-quality` (от `dev/p08-ai-router`), коммит `f815e88`.
- Основание: «Двигаемся дальше и не пушим без аудита» (17:05). **Push не выполнялся.**
- Пачка на аудит Codex: **P06** (dev/p06-search-analytics, diff b1c6f2a..d259374), **P07**
  (dev/p07-watch-system, diff d259374..ddd5617), **P08** (dev/p08-ai-router, diff ddd5617..af0c0c4),
  **P09** (dev/p09-catalog-quality, diff af0c0c4..f815e88).
- Решения владельца по P09 (17:06, до замера): точность не ниже 99 % (ложное сравнение хуже пропуска);
  разные фасовки разводим сразу, спорные случаи — в теневой отчёт.
- Замер на версионируемом наборе docs/golden_matching.json (40 пар): **точность 1.0, полнота 0.93** при
  согласованных порогах 0.99 и 0.70.
- Тесты: Python **572 OK** (новый test_catalog_quality.py: 16), frontend **9/9 PASS**.
- Правило владельца: **без аудита ничего не пушим**. На проде v5.10.0.
- Пишущих исполнителей нет: Claude закончил. Следующий — Codex (когда появятся лимиты): аудит пачки P06–P09.
- Команда для Codex: «Прочитай AGENTS.md, docs/DEVELOPMENT_PLAN.md и docs/AGENT_HANDOFF.md. Проведи аудит пачки,
  не меняя код: P06 (diff b1c6f2a..d259374, приёмка F01–F04), P07 (diff d259374..ddd5617), P08
  (diff ddd5617..af0c0c4), P09 (diff af0c0c4..f815e88, docs/golden_matching.json): фасовка как часть личности
  товара, отсутствие ложных сравнений на опасных парах (память, SIM, вариант, фасовка, упаковка, аксессуар),
  честность замера и порогов, влияние нового отбора кандидатов на технику, теневой отчёт. Запиши выводы в
  docs/P06_AUDIT_CODEX.md … docs/P09_AUDIT_CODEX.md, обнови карточки и реестр.»

## История передачи исправлений на повторный аудит

### Передача Claude перед аудитом e48ccb8


- Дата: 2026-09-20 18:05, Asia/Almaty.
- Последнее действие: Claude исправил **все семь замечаний аудита пачки P06–P09** (коммиты `2c4ddb8` — отчёты
  Codex, `aaa5fe6` — исправления). Ветка `dev/p09-catalog-quality`. **Push не выполнялся.**
- Что сделано по замечаниям:
  - **F03** (P06): сводка узнаёт о работоспособности живых источников через контекстную переменную; пустой
    ответ при отказе — ERROR, успешная пустая выдача — NOT_FOUND; единичный учёт F04 сохранён. Регрессии идут
    через настоящий путь API с подменой только адаптера магазина.
  - **G01** (P07): одноразовое наблюдение помечается сработавшим (`watches.fired_at`), но остаётся включённым
    до отправки и выключается после неё; ручное выключение по-прежнему отменяет сообщение; временная ошибка и
    повтор доставку сохраняют.
  - **G02** (P07): чтение состояния, решение и запись события — в одной транзакции BEGIN IMMEDIATE; тест с
    двумя потоками и барьером даёт одно событие и одно задание.
  - **H01** (P08): раздельные дневные счётчики `ai_calls:user:…` и `ai_calls:internal:…`; списание на границе
    провайдера; исчерпание одной стороны не трогает другую.
  - **H02** (P08): консультант, нормализация и классификация категорий переведены на `ai_router.run` —
    политика запрета запасного провайдера, учёт и защита промпта работают на действующих путях.
  - **I01** (P09): память, оперативная память, диагональ и назначение корма больше не стираются, а сравниваются;
    противоречие проверяется до любого положительного решения. Попутно найден тот же дефект для памяти,
    записанной отдельными словами («8GB 256GB» против «16GB 256GB»).
  - **I02** (P09): совпадение модели не обходит неизвестную упаковку — «2 шт» не сравнивается с одиночным.
  - Golden расширен до **53 пар** (версия 2) опасными случаями аудита, добавлен отчёт по группам:
    **точность 1.0, полнота 0.94**, ложных совпадений в опасных группах нет.
- Тесты: Python **638 OK** (было 572; новых регрессий 22), frontend **9/9 PASS**.
- Правило владельца: **без аудита ничего не пушим**. На проде v5.10.0.
- Пишущих исполнителей нет: Claude закончил. Следующий — Codex: повторный аудит пачки P06–P09.
- Команда для Codex: «Прочитай AGENTS.md, docs/DEVELOPMENT_PLAN.md и docs/AGENT_HANDOFF.md. Проведи повторный
  аудит пачки P06–P09 на ветке dev/p09-catalog-quality, не меняя код (diff a6c68cc..aaa5fe6). Приёмка: F03 —
  полный отказ, успешная пустая выдача, частичный отказ и повтор; G01 — сквозная доставка одноразового
  наблюдения, ошибка и restart, ручное выключение; G02 — конкурентная оценка; H01 — независимость квот и
  списание на границе провайдера; H02 — публичные функции сервиса (консультант, нормализация, категории);
  I01/I02 — опасные пары и настоящий отбор конкурентов, честность замера на расширенном наборе. Запиши выводы
  в docs/P06_AUDIT_CODEX.md … docs/P09_AUDIT_CODEX.md, обнови карточки и реестр.»

## История передачи H01/H03 на точечный аудит

### Передача Claude перед аудитом 8a77f5a


- Дата: 2026-09-20 18:15, Asia/Almaty.
- Последнее действие: Claude исправил остаток аудита P08 — **H01 (атомарная квота) и H03 (локальный отказ
  считался вызовом провайдера)**; коммит `5161233`. Ветка `dev/p09-catalog-quality`. **Push не выполнялся.**
- Состояние пачки по последнему аудиту Codex (HEAD e48ccb8): **P06 READY, P07 V1 READY, P09 (детерминированная
  часть) READY**; P08 был IN_PROGRESS — теперь исправлен и ждёт повторного аудита.
- Что сделано:
  - **H01**: проверка и списание дневной квоты — один условный UPDATE в транзакции на запись
    (`ai_service.reserve_ai_call`). Два исполнителя, соревнующиеся за последнюю единицу, доходят до провайдера
    по одному — проверено тестом с двумя потоками и барьером.
  - **H03**: отказ собственного бюджета или ограничителя больше не пишется как обращение к провайдеру и его
    ошибка; исчерпанная квота не отправляет задачу к запасному провайдеру (он не пополняет наш бюджет).
    Проверено на настоящем роутере с подменой только сетевых функций.
  - **Исправлена и честность замера P09**: полнота **0.8889**, а не 0.94, как было записано ранее. Оба пропуска
    описаны в docs/golden_matching.json, а новый тест перепроверяет записанные числа — расхождение документации
    и кода теперь падает как ошибка.
- Тесты: Python **659 OK** (было 638), frontend **9/9 PASS**.
- Правило владельца: **без аудита ничего не пушим**. На проде v5.10.0.
- Пишущих исполнителей нет: Claude закончил. Следующий — Codex: точечный аудит P08 (H01/H03).
- Команда для Codex: «Прочитай AGENTS.md, docs/DEVELOPMENT_PLAN.md и docs/AGENT_HANDOFF.md. Проведи точечный
  аудит P08 на ветке dev/p09-catalog-quality, не меняя код (diff e48ccb8..HEAD). Приёмка: H01 — несколько
  исполнителей с отдельными соединениями соревнуются за последнюю единицу квоты, user/internal независимы,
  запасной провайдер получает квоту тем же способом; H03 — заблокированный бюджетом или ограничителем вызов не
  попадает в provider_calls и errors, исчерпанная квота не вызывает второго провайдера. Проверь также, что
  записанный в docs/golden_matching.json замер воспроизводится. Запиши выводы в docs/P08_AUDIT_CODEX.md,
  обнови карточку и реестр.»

## История передачи P10/P14 до аудита

### Передача Claude


- Дата: 2026-09-20 19:05, Asia/Almaty.
- Последнее действие: Claude реализовал **P14 (System & Environment)** на ветке `dev/p14-system-environment`
  (от main = v5.11.0), коммит `27a760e`. Основание: «Давай ещё продолжим, потом аудит» (18:45); выбор этапа —
  ответ владельца: P14, потому что он не зависит от непринятого P10 и не меняет поведение обходов.
- **Push не выполнялся. Не проаудировано: P10 и P14.**
- Локальные ветки на аудит: `dev/p10-scheduler-shadow` (diff 07220b7..6193ac3) и
  `dev/p14-system-environment` (diff 07220b7..27a760e). Обе от main = v5.11.0, между собой не пересекаются.
- На проде v5.11.0 (P06–P09).
- Тесты: Python **698 OK** (новый test_environment.py: 16), frontend **10/10 PASS**. Браузер на стенде:
  раздел «Система» показывает версии, время работы и пульс каждого работника; выключенный Telegram помечен
  выключенным, а не аварией.
- Пишущих исполнителей нет: Claude закончил.
- Команда для Codex: «Прочитай AGENTS.md, docs/DEVELOPMENT_PLAN.md и docs/AGENT_HANDOFF.md. Проведи аудит двух
  независимых веток, не меняя код: (1) P10 — dev/p10-scheduler-shadow (diff 07220b7..6193ac3, карточка P10):
  отсутствие побочных действий, соблюдение ограничений в плане и симуляции, starvation, воспроизводимость
  сравнения стратегий; (2) P14 — dev/p14-system-environment (diff 07220b7..27a760e, карточка P14): молчащий
  работник не зелёный, выключенное отличается от сломавшегося, пороги соответствуют ритму работников,
  секреты не раскрываются, версии фактические. Запиши выводы в docs/P10_AUDIT_CODEX.md и
  docs/P14_AUDIT_CODEX.md, обнови карточки и реестр.»

## История передачи P15 перед аудитом 2026-09-23

### Передача Claude


- Дата: 2026-09-23 13:25, Asia/Almaty. Исполнитель — Claude.
- Состояние пачки на ветке `dev/p15-backup-retention` (от main = v5.11.0; содержит P10 → P14 → P15):
  - **P10 и P14**: аудит Codex нашёл J01–J03 и K01/K02; **исправления написал сам Codex** 2026-09-21 по
    поручению владельца («продолжи, у всех закончились токены»). По AGENTS.md самопроверка не заменяет
    независимый аудит — эти исправления ещё должен просмотреть другой исполнитель.
  - **P15 (копии, сроки хранения, самопроверка)**: реализовал Claude, аудита не было.
- Принятая поправка аудита: ветки P10 и P14 **не независимы** — история `07220b7 → ea398d1 → 6193ac3 →
  27a760e → cc083f2`; прежняя запись в передаче об их независимости была ошибочной.
- Коммиты: `d2aed3e` (исправления Codex + его отчёты), `<P15>` (этап P15 и часть J03 в database.py).
- Тесты на объединённом дереве: Python **748 OK**, frontend **10/10 PASS** (для frontend-набора заново
  поставлен playwright в scratchpad — прежняя ссылка на node_modules пропала со сменой сессии).
- По поручению владельца открыт pull request в `main` (кнопка «Create PR»). **Слияние PR = автодеплой на
  shop.molthun.ru**, поэтому сливать только после независимого аудита J01–J03, K01/K02 и всего P15.
- На проде v5.11.0 (P06–P09).
- Следующий шаг: независимый аудит — (1) исправления Codex по P10/P14, (2) весь P15; затем решение владельца
  о выпуске.

## История передачи исправлений P15 на аудит 9200a61

### Передача Claude


- Дата: 2026-09-23 13:50, Asia/Almaty. Исполнитель — Claude.
- Последнее действие: исправлены **L01–L03** по аудиту P15 (коммит `e4fba39`) и проведён **независимый
  просмотр правок Codex** (J01–J03, K01/K02 в `d2aed3e` и часть J03 в `8b05167`).
- Что сделано по замечаниям P15:
  - **L01**: результат проверки привязан к конкретному файлу (имя, размер, время изменения). Новая или
    подменённая копия считается непроверенной и не наследует прошлый успех; суточная пауза считается по
    проверке этой же копии; последняя подтверждённая копия показывается отдельно. Список копий сортируется
    по точному времени изменения — две копии одной минуты больше не меняются местами.
  - **L02**: пригодной считается копия, из которой поднимется рабочая база: обязательные таблицы,
    поддерживаемая версия схемы, нужные колонки товаров и реальные запросы приложения. Файл SQLite с одной
    строкой товаров больше не проходит.
  - **L03**: заявленный предел времени действительно применяется — копирование кусками с проверкой времени
    между ними и прерывание долгих запросов; истечение предела записывается как непройденная проверка.
- Независимый просмотр правок Codex: замечаний нет. Проверено по существу: базовая стратегия теперь проходит
  через те же ограничения (`plan(order="oldest")`), порядок детерминирован, возраст в симуляции
  соответствует времени цикла, нарушения считаются по `allowed()` целиком, телеметрия распознаёт
  false/no/off. Правку K01 пришлось согласовать с P15: самопроверка копий идёт в обслуживании после обходов,
  поэтому пульс компонента ожидается при включённых обходах, а при выключенных — состояние «выключено».
  Тест Codex сохранил смысл и обновлён под это.
- Тесты: Python **777 OK** (test_backup_health.py: 71), frontend **10/10 PASS**. На копии рабочей базы:
  репетиция восстановления 42 899 товаров за 0,44 с, все запросы приложения проходят.
- PR [#2](https://github.com/molthun/kz-price-hunter/pull/2) обновлён этими коммитами. **Слияние = выпуск на
  прод**, поэтому только после приёмки. На проде v5.11.0.
- Следующий шаг: Codex — повторный аудит P15 (L01–L03) и приёмка пачки P10/P14/P15; затем решение владельца
  о выпуске.

## История передачи исправлений P15 перед аудитом 732dde4

### Передача Claude


- Дата: 2026-09-23 14:00, Asia/Almaty. Исполнитель — Claude.
- Последнее действие: закрыты **L02 и L03** по повторному аудиту P15 (коммит `1e442a2`). L01 закрыт аудитом
  ранее.
- **L02**: контракт схемы расширен на колонки всех критичных таблиц (users, watches, alerts, sessions,
  notification_outbox), а репетиция восстановления выполняет запросы, которые делает само приложение:
  вход пользователя, наблюдения, очередь уведомлений, лента алертов. Копия, где `watches` существует без
  `user_id` — ровно случай из аудита, — отклоняется. Критерием считается выполнение запроса, а не наличие
  строк: у нового сервиса законно нет ни пользователей, ни наблюдений.
- **L03**: обслуживание базы освобождается вовремя даже при зависшем чтении — репетиция идёт в отдельном
  потоке, вызывающий ждёт не дольше заявленного предела, истечение записывается как непройденная проверка.
  В коде прямо сказано, чего нельзя сделать средствами Python: прервать заблокированное чтение нельзя,
  поэтому зависший поток остаётся фоновым, блокировок рабочей базы не держит, убирает свою временную папку
  и не даёт начать новую проверку, пока не завершится.
- Тесты: Python **809 OK** (test_backup_health.py: 103), frontend **10/10 PASS**. На копии рабочей базы:
  репетиция 0,45 с, все шесть запросов приложения проходят.
- PR [#2](https://github.com/molthun/kz-price-hunter/pull/2) обновлён. **Слияние = выпуск на прод**, поэтому
  только после приёмки. На проде v5.11.0.
- Следующий шаг: Codex — повторный аудит P15 (L02/L03) и приёмка пачки P10/P14/P15; затем решение владельца
  о выпуске.

## История передачи перед итоговой приёмкой 79d5239

### Передача Claude

- Дата: 2026-09-23 14:12, Asia/Almaty. Исполнитель — Claude.
- Последнее действие: закрыты **L03 (остаток) и L04** по третьему кругу аудита P15 (коммит `3edb1e5`).
  L01 и L02 закрыты аудитом ранее.
- **L03**: под общее ограничение времени убран весь путь работы с хранилищем копий — перечисление каталога,
  чтение метаданных, выбор копии, копирование и проверка. Раньше зависший `listdir` держал обслуживание
  ещё до входа в ограниченную часть. Повторные вызовы задачи не копят: новая проверка не начинается, пока
  жива прежняя.
- **L04**: неудачная попытка привязывается к той копии, которую проверяли, поэтому таймаут несёт имя, размер
  и время изменения файла и перекрывает прежний успех этой же копии. Если попытка сорвалась так рано, что о
  файле ничего не известно, отчёт всё равно показывает деградацию по свежему отказу, а не прежнее зелёное;
  последняя подтверждённая копия остаётся видимой отдельной строкой.
- Честная граница (без изменений): прервать уже начатое блокирующее чтение средствами Python нельзя.
  Вызывающий освобождается вовремя, зависший поток остаётся фоновым, блокировок рабочей базы не держит,
  убирает свою временную папку и не даёт начать новую проверку до завершения. Это записано в коде.
- Тесты: Python **841 OK** (test_backup_health.py: 135), frontend **10/10 PASS**. На копии рабочей базы
  полный путь проверки проходит за 0,45 с, раздел показывает «проверка пройдена».
- PR [#2](https://github.com/molthun/kz-price-hunter/pull/2) обновлён. **Слияние = выпуск на прод**, поэтому
  только после приёмки. На проде v5.11.0.
- Следующий шаг: Codex — повторный аудит P15 (L03/L04) и приёмка пачки P10/P14/P15; затем решение владельца
  о выпуске.

## Передача автора перед аудитом пачки 8c591fd

### Контрольная точка Claude

- Дата: 2026-09-23 22:20, Asia/Almaty. Исполнитель — Claude.
- Последнее действие: **документация обновлена, пачка подготовлена к аудиту**. Заявка для Codex —
  [AUDIT_REQUEST_BATCH.md](AUDIT_REQUEST_BATCH.md): что проверять по каждому этапу, заявленные гарантии,
  как воспроизвести прогоны и честный список непроведённого.
- **Push не выполнялся, аудита не было.** На проде v5.12.0, все выключатели новых возможностей в «выкл».
- Пачка на ветке `dev/p07-watch-v2`, 15 коммитов одной линией от `7375c91` (main, v5.12.0):
  P11 `91dd569` → P12 `5f50c44` → P13 `fe7169e` → P16 `2f3effa` → P07 V2 `24dfc16` →
  сводка в Telegram `17984ce` → AI-часть P09 `3832dcc` (+ документные коммиты).
- Обновлены: README.md (блок «Готовится к выпуску»), CHANGELOG.md (раздел «Не выпущено» по всей пачке),
  docs/ARCHITECTURE.md (раздел о слоях наблюдения, AI-политики и выката + список добавленных таблиц),
  docs/SECURITY_AND_PRIVACY.md (админские разделы, помощник только на чтение, сводка в Telegram, AI-часть
  сопоставления, секреты), PRIVACY_DATA_MAP.md (наблюдения, спорные пары, суточные сводки, пульс
  работников, проверки копий), docs/README.md (указатели на выкат и на заявку), ROADMAP_AND_LOG.md.
- Состояние проверок на момент передачи: Python **1047 OK**, frontend **14/14 PASS**,
  `release_gate.py secrets` — чисто. `schema_version` = 5, откат на v5.12.0 без восстановления базы.
- Не проведено: независимый аудит, прод, Docker, прогон конвейера в GitHub Actions, настоящие вызовы
  моделей и настоящая отправка в Telegram, замер AI-части P09 на живой модели.
- Следующий шаг: передать ветку Codex по заявке. После приёмки — решение владельца о выпуске (версия,
  CHANGELOG, тег, push) и отдельное решение о первом включении каждой новой возможности на проде.

## Передача автора перед повторным аудитом 4a5604b

### Контрольная точка Claude

- Дата: 2026-09-24 00:10, Asia/Almaty. Исполнитель — Claude. **Передача: Codex, повторная приёмка.**
- Снимок для приёмки: **текущий HEAD ветки `dev/p07-watch-v2`** (`git rev-parse HEAD`), база сравнения
  `7375c91` (main, v5.12.0). Рабочее дерево чистое, push не выполнялся. Последний коммит с изменениями
  приложения — `bd8323d`; всё, что после него, — только документы, поэтому проверки, прогнанные на
  `214ea58`, относятся к тому же коду.
- Предмет: исправления **M01–M11** по отчёту [BATCH_AUDIT_CODEX.md](BATCH_AUDIT_CODEX.md). Разбор
  «что было → что сделано → каким тестом закреплено» — в [AUDIT_REQUEST_BATCH.md](AUDIT_REQUEST_BATCH.md),
  раздел «Исправления по аудиту».
- Коммиты исправлений: `57999b7` (M04, M05, M06, M11), `e8b92b2` (M07, M08, M09),
  `bd8323d` (M01, M02, M03, M10); документные идут после них. Отчёт аудита — `99dd336`.
- Проверки автора на этом снимке (2026-09-24 00:05): Python **1093 OK** за 49.7 с;
  frontend **14/14 PASS**; `release_gate.py secrets` — findings 0. Только временные данные;
  AI и Telegram подменены; рабочая `prices.db` не использовалась.
- Изменения относительно проверенного ранее снимка `8c591fd`, на которые стоит смотреть в первую очередь:
  `scheduler_rollout.py` (состав шага и границы периодов), `database.http_metrics_by_shop_window`,
  `web/server.py` (`GUARDED_SETTINGS`), `admin_assistant.unverified_numbers`, `daily_digest`
  (счётчики событий, состояния стоимости), `release_gate.py` (`_snapshot`, `rollback_check`,
  `_scan_files`, `check_dependencies`), `database.claim_watch_digest`, `catalog_ai` (`parse`,
  `accepted`, `show_confidence`).
- Критерии приёмки — из собственных формулировок отчёта M01–M11; воспроизведения аудита стоит повторить
  на новом снимке, особенно: подмена участника canary при смене профилей; 10 000 прежних запросов против
  100 провальных после включения; `POST /api/admin/config` с `adaptive_scheduler_stage: all`;
  ответ «12 ошибок» при нулевых фактах; 8 событий восстановления; `cost_usd IS NULL`; запись,
  подтверждённая в WAL, при репетиции обновления; токен в `.github/workflows`; отчёт pip-audit со
  `skip_reason`; 25 срабатываний сводки; `confidence: 0.8999`.
- Область работы Codex: аудит. Реализацию не начинать без поручения владельца; прод, Docker, push,
  merge и выпуск не трогать.
- Не проведено (без изменений): прод, Docker, живой прогон GitHub Actions, реальный аудит зависимостей,
  настоящие вызовы моделей и отправка в Telegram, замер AI-части P09 на живой модели, production canary.
- **Следующий точный шаг: Codex выносит решение по M01–M11 на текущем HEAD ветки и отмечает оставшиеся
  риски.** При принятии — решение владельца о выпуске (версия, CHANGELOG, тег, push) и отдельные решения
  о первом включении каждой новой возможности.

## Передача автора перед приёмкой доработки 8bb9480

### Контрольная точка Claude

- Дата: 2026-09-24 01:30, Asia/Almaty. Исполнитель — Claude. **Передача: Codex, приёмка доработки.**
- Последнее действие: закрыты **остатки M01, M02, M03, M04, M06, M07** из повторной приёмки, коммит
  `3f88c5f`. Разбор «остаток → что сделано → регрессия» — в
  [AUDIT_REQUEST_BATCH.md](AUDIT_REQUEST_BATCH.md), раздел «Доработка остатков».
- Снимок: текущий HEAD ветки `dev/p07-watch-v2`; последний коммит кода — `3f88c5f`, дальше документы.
  База сравнения `7375c91` (main, v5.12.0). Дерево чистое, push не выполнялся.
- Суть доработок: canary берётся только из включённых магазинов и первый шаг требует FRIENDLY (иначе
  отказ словами); час включения считается дельтой по снимку счётчиков, и провал обходов ведёт к откату
  даже при малом числе запросов; смена шага идёт единственным путём «проверка → подготовка →
  активация» с восстановлением состояния при сбое; модель больше не пишет чисел — она ставит ссылку
  `{блок.поле}`, а значение подставляет сервис, поэтому число нельзя приписать другому показателю;
  в `ai_usage` появился счётчик оценённых вызовов, и смешанная строка называется неполной; репетиция
  отката идёт по обновлённой базе, которую сохраняет `upgrade --keep`.
- Проверки автора: Python **1151 OK** за 50.9 с; frontend **14/14 PASS**; `release_gate.py secrets` —
  findings 0. Только временные данные, AI и Telegram подменены, рабочая `prices.db` не использовалась.
- Изменённые файлы: `scheduler_rollout.py`, `database.py` (`http_counters_for_hour`,
  `http_metrics_by_shop_window`, `set_metadata_many`, колонка `ai_usage.priced_requests`),
  `web/server.py` (переключатель шага), `admin_assistant.py` (ссылки на показатели), `daily_digest.py`,
  `release_gate.py`, `.github/workflows/docker-publish.yml`, соответствующие тесты.
- Ограничения, которые остаются и не выдаются за исправленные: ссылки только на числовые поля верхнего
  уровня блоков; без снимка счётчиков час старта помечается `excluded`; сводка больше 500 срабатываний
  делится на письма; живой конвейер, реальный pip-audit, настоящие вызовы моделей и Telegram, замер
  AI-части на живой модели и production canary не выполнялись.
- Область работы Codex: приёмка. Реализацию не начинать без поручения владельца; прод, Docker, push,
  merge и выпуск не трогать.
- **Следующий точный шаг: Codex принимает или возвращает доработку остатков M01–M07 на текущем HEAD.**
  При принятии — решение владельца о выпуске и отдельные решения о первых включениях.

## Передача автора перед приёмкой 9411725

### Контрольная точка Claude

- Дата: 2026-09-24 03:10, Asia/Almaty. Исполнитель — Claude. **Передача: Codex, приёмка M01/M03/M04.**
- Последнее действие: закрыты три замечания приёмки доработки, коммит `ad9da38`. Разбор — в
  [AUDIT_REQUEST_BATCH.md](AUDIT_REQUEST_BATCH.md), раздел «Доработка M01, M03, M04».
- Снимок: текущий HEAD ветки `dev/p07-watch-v2`; последний коммит кода — `ad9da38`, дальше документы.
  База сравнения `7375c91` (main, v5.12.0). Дерево чистое, push не выполнялся.
- Суть: пустой список разрешённых магазинов означает «никто», неизвестные права тоже запрещают шаг;
  переходы сериализованы замком и условной записью по версии состояния, отказ отменяет только свою
  подготовку, автоматический откат подчиняется тому же правилу; модель больше не делает количественных
  утверждений вовсе — любое число или ссылка в её тексте отклоняется, а цифры показывает сводка фактов,
  собранная кодом, с именем, источником и периодом у каждого значения.
- Проверки автора: Python **1177 OK** за 56.3 с; frontend **14/14 PASS**; `release_gate.py secrets` —
  findings 0. Временные данные, AI и Telegram подменены, рабочая `prices.db` не использовалась.
- Изменённые файлы: `scheduler_rollout.py`, `database.py` (`compare_and_set_metadata`),
  `admin_assistant.py` (`verify_comment`, развёрнутая `facts_summary`), `daily_digest.py`,
  `test_scheduler_rollout.py`, `test_admin_assistant.py`, `test_daily_digest.py`.
- Следствие M04, о котором стоит знать при приёмке: помощник и сводка больше не показывают чисел в
  тексте модели — объяснение словесное, цифры идут отдельным блоком кода. Это размен связности рассказа
  на невозможность приписать число не тому показателю.
- Ограничения прежние: ссылки на числа внутри списков фактов не поддерживаются вовсе; без снимка
  счётчиков час старта помечается `excluded`; сводка больше 500 срабатываний делится на письма; живой
  конвейер, реальный pip-audit, настоящие вызовы моделей и Telegram, замер AI-части на живой модели и
  production canary не выполнялись.
- Воспроизведения из отчёта, которые стоит повторить на новом снимке:
  `canary_shops(canary_one, [friendly kaspi, normal dns], allowed=[])` и `missing_members(..., allowed=[])`
  при выключенных магазинах (M01); два потока `switch_stage`, где первый останавливается в подготовке и
  затем падает, а второй проходит целиком (M03); полный `answer` с фактами `errors=0, requests=50` и
  ответом модели «За сутки было {http.requests} ошибок» — и тот же сценарий в `daily_digest.summarize`
  (M04).
- Проверки повторены на передаваемом снимке (2026-09-24 03:20): Python **1177 OK** за 55.9 с,
  `release_gate.py secrets` — findings 0. Браузерные наборы прогонялись на том же коде приложения
  (`ad9da38`), после него менялись только документы.
- Область работы Codex: приёмка. Реализацию не начинать без поручения владельца; прод, Docker, push,
  merge и выпуск не трогать.
- **Следующий точный шаг: Codex принимает или возвращает M01/M03/M04 на текущем HEAD.** При принятии —
  решение владельца о выпуске и отдельные решения о первых включениях.

## Передача автора перед приёмкой M03 на 7104195

### Контрольная точка Claude

- Дата: 2026-09-24 01:25, Asia/Almaty. Исполнитель — Claude. **Передача: Codex, приёмка остатка M03.**
- Последнее действие: закрыт остаток M03 (согласованность отката) и починены два дефекта тестов,
  коммит `8fd9cb9`. Разбор — в [AUDIT_REQUEST_BATCH.md](AUDIT_REQUEST_BATCH.md), раздел «Закрытие
  остатка M03».
- Снимок: текущий HEAD ветки `dev/p07-watch-v2`; последний коммит кода — `8fd9cb9`, дальше документы.
  База сравнения `7375c91` (main, v5.12.0). Дерево чистое, push не выполнялся.
- Суть исправления: откат сначала выключает настройку и только после успешной записи очищает состояние
  и фиксирует запись отката. Сбой записи оставляет шаг целиком — со своим составом, baseline и снимком
  счётчиков, — а откат не записывается и не выдаётся за выполненный. Выключение через `switch_stage`
  идёт тем же порядком; защита по версии состояния сохранена.
- Воспроизведение аудита для повтора: настоящий `switch_stage(canary_one)`, затем `config.save_settings`
  подменяется `OSError`, вызывается настоящий `rollback` — ожидается `stage=canary_one`,
  `canary_members=['kaspi']`, снимок счётчиков на месте, `last_rollback` отсутствует.
- Две починки тестов (дефекты авторские, не продукта): `test_frontend_p07_v2.cjs` ждал уже истинное
  условие и читал список запросов до прихода POST — теперь ждёт ответ сервера, три прогона подряд PASS;
  `test_watches.py` создавал наблюдения с тихими часами по умолчанию, из-за чего все тесты доставки
  падали при ночном прогоне (сообщения законно откладывались до утра) — тесты доставки теперь отключают
  тихие часы явно. Это объясняет, почему прежние прогоны были зелёными днём.
- Проверки автора на этом снимке (повторены при передаче, 01:25 по Алматы): Python **1191 OK** за 56.4 с;
  frontend **14/14 PASS одним проходом**; `release_gate.py secrets` — findings 0. Временные данные,
  AI и Telegram подменены, рабочая `prices.db` не использовалась.
- Прогон сделан внутри тихих часов (23:00–08:00 по Алматы) намеренно: именно ночью прежние тесты
  доставки падали. Теперь набор зелёный и ночью, и днём.
- Ограничения прежние: межпроцессная атомарность переходов проверена только внутрипроцессными тестами
  и условной записью по версии; без снимка счётчиков час старта помечается `excluded`; сводка больше
  500 срабатываний делится на письма; помощник и сводка не показывают чисел в тексте модели; живой
  конвейер, реальный pip-audit, настоящие вызовы моделей и Telegram, замер AI-части на живой модели и
  production canary не выполнялись.
- Область работы Codex: приёмка. Реализацию не начинать без поручения владельца; прод, Docker, push,
  merge и выпуск не трогать.
- **Следующий точный шаг: Codex принимает или возвращает остаток M03 на текущем HEAD.** При принятии —
  решение владельца о выпуске и отдельные решения о первых включениях.
