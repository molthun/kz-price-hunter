# Рабочий реестр и передача задач

Общие правила: [AGENTS.md](../AGENTS.md). Требования и проверки: [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md).

## Текущая контрольная точка

- Дата: 2026-09-19 23:16, Asia/Almaty.
- Последнее действие: Codex завершил точечный аудит C02 (`31d0ea4`, HEAD `e784982`).
  **P02 READY по аудиту кода: C01–C03 закрыты.** [Отчёт](P02_AUDIT_CODEX.md). P02 не выпущен.
- main = v5.8.0 (P01, на проде). Локальный main впереди origin на 1 docs-коммит (4bbde64, не запушен — см. R-P01).
- Полный suite независимо повторён Codex: 378 тестов, 32.752 с, OK.
- Пишущих исполнителей нет: Codex завершил аудит; изменены только отчёт и передача.

## Реестр этапов

Статусы: `TODO` → `IN_PROGRESS` → `REVIEW` → `READY` → `DEPLOYED`.
Дополнительно: `HANDOFF` — подготовлен подхват; `BLOCKED` — указан конкретный блокер. READY означает принятую реализацию, DEPLOYED — подтверждение на целевом окружении. Поля автора, аудитора и владельца приёмки заполняются по факту.

| Этап | Статус | Исполнитель | Аудит | Прод |
|---|---|---|---|---|
| P00 | READY | Antigravity | Самопроверка (требует review) | Не проверен (нет прямого доступа) |
| P01 | DEPLOYED (v5.8.0) | Antigravity → Claude | Codex: A01–A05 и B01/B02 закрыты | v5.8.0 на shop.molthun.ru с 19:41 (проверка чтением /api/version, /api/stats, /api/products) |
| P02 | READY (аудит кода) | Claude | Codex: C01–C03 закрыты | Не выпущен |
| P03 | TODO | — | Не проведён | Не проверен |
| P04 | TODO | — | Не проведён | Не проверен |
| P05 | TODO | — | Не проведён | Не проверен |
| P06 | TODO | — | Не проведён | Не проверен |
| P07 | TODO | — | Не проведён | Не проверен |
| P08 | TODO | — | Не проведён | Не проверен |
| P09 | TODO | — | Не проведён | Не проверен |
| P10 | TODO | — | Не проведён | Не проверен |
| P11 | TODO | — | Не проведён | Не проверен |
| P12 | TODO | — | Не проведён | Не проверен |
| P13 | TODO | — | Не проведён | Не проверен |
| P14 | TODO | — | Не проведён | Не проверен |
| P15 | TODO | — | Не проведён | Не проверен |
| P16 | TODO | — | Не проведён | Не проверен |

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
