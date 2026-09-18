# KZ Price Hunter — аудит перед масштабированием

Дата: 18 сентября 2026. Состояние: **аудит завершён; этап 1 согласован пользователем и выполнен локально, остальные этапы ожидают согласования**.
Репозиторий: `/Users/molthun/Documents/kz-price-hunter`, HEAD `e7ebcdc` (v4.6.0).
Проверена рабочая копия, включая существующий незакоммиченный diff `database.py` (+48/−21 строк). Этот diff не создан аудитом и не изменён.

## Вывод

Расширять сбор до десятков/100 источников пока преждевременно. Основные препятствия — безопасность интерфейса, обход ограничений live-поиска, идентичность предложения по городу, достоверность окончания обхода и отсутствие общего контроля исходящих запросов. Замена всего приложения, SQLite, aiohttp или существующих парсеров не требуется.

Есть хорошая основа: `PagedScraper` и `ScanResult`, WAL, пакетная запись, FTS5, проверка `same_model` после поиска кандидатов, ограничения AI, транзакционный outbox, проверка актуальной цены и блокировки пользователя перед доставкой. Эти механизмы нужно сохранить и довести до единого контракта.

Приложение остаётся агрегатором: сравнение предложений и переход к продавцу. Оплату и оформление заказа добавлять не предлагается.

## Границы и доказательства

- Проверены все 18 адаптеров, общий транспорт/пагинация, оба способа запуска, HTTP API, UI, Telegram, AI, DB/инициализация, deployment и CI; документация сопоставлена с реализацией и историей изменений.
- Git: 67 коммитов, 430 уникальных достижимых blobs во всех локально доступных refs. История удалённого сервера, удалённые refs, старые контейнеры, CI logs и резервные копии вне репозитория не проверялись. Fetch/push не выполнялись.
- Secrets: проверены шаблоны Telegram/provider tokens/private keys и исторические файлы БД/настроек; совпадений токенов не найдено. Это ограниченный скан, не гарантия отсутствия любых секретов. Значения действующих настроек, профилей, сессий и переписок не выводились.
- Рабочая SQLite открывалась с `mode=ro` и `PRAGMA query_only=ON`, только схема/агрегаты. `init_db()` на ней НЕ запускался. Старые Git DB анализировались в памяти; для чтения сохранённых WAL-заголовков переключены только байты in-memory копии. Наличие таблиц пользователей/сессий в этих снимках не обнаружено.
- На момент чтения: 61 379 products, 1 591 alerts, 18 shop_scans; 31 973 товара имеют canonical_key; `quick_check=ok`, нарушений объявленных FK не найдено. Это локальный снимок, не проверка production. При итоговом чтении products=61 381: уже запущенный Python PID 62430 работает из каталога проекта и слушает *:8080. Аудит не запускал/не останавливал этот процесс; hash рабочей DB за время аудита изменился, поэтому неизменность её содержимого не заявляется. Hash database.py и settings.json совпали с исходными.
- 85 Python tests: **OK**, 2.474 с, Python 3.14.7. DATA_DIR временный, реальные credentials отключены, внешний HTTP заблокирован; не замоканные обращения Forte в двух старых тестах были заблокированы и обработаны кодом. Живые интеграции этот запуск не подтверждает.
- Существующий `test_frontend.cjs`: **PASS**, Chrome headless, запросы перехвачены; guest/user/logout/manual model, page_errors=[]; использован уже доступный Node runtime без установки пакетов.
- Дополнительные сценарии на временной БД и изолированном браузере подтвердили XSS, guest dismiss, implicit live, city overwrite, потерю old price, ошибку decimal price, ложный complete без total и совпадение разных SIM-вариантов iPhone.
- `pip check`: OK. OSV QueryBatch для 28 установленных PyPI пакетов: совпадений advisories не вернул. Это не скан образа и не проверка будущего разрешения зависимостей из `>=`.
- `git diff --check` обнаруживает два trailing whitespace в исходном пользовательском diff, строки 1104/1132. Они сохранены.
- Приложение, сканирование магазинов, рассылки, платные AI-запросы, Docker build/deploy и migrations не запускались. Ограниченный поиск публичной документации DNS не подтвердил готовую замену Playwright; отсутствие подходящего API не доказано.

Сводка воспроизведений: [AUDIT_EVIDENCE.md](AUDIT_EVIDENCE.md).

Подробный инвентарь: [SOURCES.md](SOURCES.md). Предлагаемый контракт: [SCRAPER_STANDARD.md](SCRAPER_STANDARD.md). Ниже «воспроизведено» означает синтетический локальный сценарий, а не эксплуатацию production.

## Статус после этапа 1

Пользователь согласовал этап 1 сообщением «Делаем 1 Этап». Результат и границы: [AUDIT_STAGE1.md](AUDIT_STAGE1.md).

- C01: исправлены обнаруженные HTML/атрибутные/inline-handler инъекции и опасные URL. CSP остаётся отдельным усилением после проверки внешних ресурсов.
- H01/H13: глобальное скрытие алертов и чтение tracked queries доступны только admin.
- H03: обычный поиск больше не запускает live на пустой/устаревшей выдаче; явное live требует входа, действующий per-user limit сохранён; учитываются enabled_shops, включая ключ кэша.
- H04: Git/Docker исключают локальные env, ключи и backups. История не переписана.
- M02: ошибки провайдеров больше не выводят response body/transport exception; добавлен redact до терминала/буфера и в возвращаемые ошибки. Отдельный rate limit test-telegram остаётся на этапе auth/abuse protection.
- Итог проверки: 90 тестов приложения + 3 теста логов; оба браузерных набора PASS.
- Работающий сервер не перезапускался, внедрение в работающий процесс не подтверждено. При запуске выполняется init_db с миграциями, требующими отдельного согласования.

Описания ниже сохраняют **исходные доказательства и номера строк до исправлений**. Статус исправлений определяется этим разделом и отчётом этапа 1, а не исходной формулировкой дефекта. Полный проект ещё не объявляется готовым к масштабированию.

## CRITICAL

### C01 — Stored XSS из каталога в интерфейсе администратора

**Где:** `web/templates/index.html:2325`, `renderAlertCard`; строки 2369–2373, 3290, 3347; `escapeHtml:4530`, `cleanUrl:2155`.

**Доказательство:** значение title с безвредным тестовым обработчиком вставлено через настоящую `renderAlertCard`; браузер выполнил обработчик. Название и категория вставляются в `innerHTML` без экранирования, image_url — в атрибут без защиты. `escapeHtml` экранирует текст через DOM, но оставляет кавычки; его нельзя считать атрибутным/JS escaping. `cleanUrl` возвращает `javascript:` без запрета. Inline onclick также смешивает данные и код.

**Последствие:** подменённые данные источника могут выполнять same-origin запросы с правами вошедшего администратора. HttpOnly не предотвращает выполнение таких запросов. Подмена реального магазина не доказывалась; поверхность приёма недоверенных данных подтверждена.

**Исправление:** DOM/textContent, безопасная установка атрибутов, http/https allowlist URL с отдельным правилом wa.me, обработчики через addEventListener; исключить конкатенацию данных в JS. Затем CSP после проверки внешних ресурсов. Регрессии title/image/url/AI suggestions/tracked categories. **Нужно согласование изменения UI.**

## HIGH

### H01 — Гость скрывает алерты для всех

**Где:** `web/server.py:237–259`; `database.py:661`, `dismiss_alert`.

Оба mutating маршрута без require_login/require_admin. Воспроизведено: guest DELETE возвращает 200, `is_dismissed=1` в общей записи. При ошибке UPDATE есть fallback DELETE. Предложение для первого этапа: разрешить глобальное скрытие только администратору. Персональное скрытие — отдельное решение со связью user/alert, а не общий флаг. Проверить guest=401, user=403, admin=200, отсутствие изменений после отказа. **Согласовать семантику.**

### H02 — Dev login по умолчанию даёт администратора из LAN

**Где:** `config.py:270–278`, `auth.py:84`, `web/server.py:662–672`, `gui.py:10`.

Вне контейнера dev login включён автоматически; bind `0.0.0.0`; private IP допускается. При заданных ADMIN_TELEGRAM_IDS вход использует минимальный admin ID и обновляет его Telegram-профиль значениями dev. При прокси без X-Forwarded-For backend тоже может видеть private peer. Docker явно отключает dev login — это полезная защита, но не защита обычного запуска.

**Исправление:** opt-in dev login, только loopback, отдельная тестовая личность; production запрещает режим. Не менять до согласования локального рабочего процесса. Проверить private peer, loopback, proxy, отсутствие перезаписи admin profile.

### H03 — Скрытый live-поиск обходит проверку доступа и отключение магазина

**Где:** `web/server.py:493`, `search_engine.py:436`, `get_best_price_summary:632` (should_live_search).

HTTP разрешает гостю live=false, но пустая или устаревшая выдача всё равно вызывает `search_live_stores`. Воспроизведён вызов при live=false и пустой БД. Далее жёстко опрашиваются Kaspi/Shop.kz/4mobile/Forte без enabled_shops. Это также сохраняет предложения и tracked query; гость способен создавать постоянную работу планировщику. Прямой `curl_cffi.requests.get` Shop.kz внутри async блокирует event loop до 10 с.

**Исправление:** явная политика refresh на сервере, общий путь авторизации/лимитов для всех refresh, учитывать enabled_shops; default DB-only; single-flight одинакового запроса; вынести blocking HTTP/DB. Проверить пустую/старую выдачу гостя, disabled shops и конкурентные запросы. **Меняет поведение поиска — согласовать.**

### H04 — `.env` может попасть в Git и Docker image

**Где:** `.gitignore`, `.dockerignore`, `Dockerfile:14`, `docker-compose.yml:19`.

README/Compose предлагают .env рядом с проектом, но оба ignore его не исключают; `COPY . .` включит такой файл в образ. Фактической утечки .env не установлено.

**Исправление:** исключить `.env`/варианты/private keys/backups, оставить безопасный `.env.example`; проверить контекст сборки с dummy secret, добавить автоматическую проверку. Если позже подтвердится реальная утечка — ротация по владельцу; не переписывать Git и не отзывать ключи предположительно.

### H05 — Цена и город предложения перезаписывают друг друга

**Где:** `database.py:417/511`, products PK=id; `scrapers/kaspi.py:114`, `scrapers/fortemarket.py:155`, `search_engine.py:450–547`.

Идентификатор не содержит город. Воспроизведено: сохранение того же id в Алматы заменяет Астану, а first_seen_price остаётся от Астаны. Live-код также присваивает запрошенный город 4mobile без получения регионального предложения; «Все» и неизвестный город могут маркировать ответ дефолтной Астаны как иной регион. Forte fallback KZ/base получает метку запрошенного города без признака fallback.

**Исправление:** согласовать offer identity `(store, external_id, confirmed_location[, seller])`; неизвестный регион не выдумывать. Отдельная backward-compatible migration с dry-run коллизий и переносом связей alerts/sources/outbox. До неё не менять id и не пересобирать историю. Тестировать два города, fallback, отсутствие seller, миграцию/rollback.

### H06 — Эврика и Moon отключают TLS verification

**Где:** `scrapers/evrika.py:45`, `scrapers/moon.py:39`.

`verify=False` допускает неподтверждённый сертификат. Возможны подмена цены/URL/HTML и цепочка к C01/H07. Исправить trust chain/CA и включить verification; если upstream действительно сломан — fail closed и статус ошибки вместо молчаливого доверия. **Может остановить рабочий источник: согласовать и проверить отдельно, не включать вслепую.**

### H07 — Загрузка description делает исходящий запрос по URL из недоверенного каталога

**Где:** `web/server.py:279–393`, `fetch_product_description_live`, guest GET product detail.

Единственная проверка URL — startswith('http'); отсутствуют hostname allowlist, запрет внутренних адресов/redirect targets, предел тела, общий limiter и negative cache. Условие эксплуатации SSRF: попадание подменённого URL в БД из источника или иных данных; прямой guest ввод произвольного URL не найден. Повторные открытия товара без description заново грузят страницу.

**Исправление:** URL policy по источнику с контролем каждого redirect и разрешённого адреса соединения, ограничение размера/времени, single-flight/negative TTL. Подробное описание сделать управляемой возможностью; согласовать нужность до отключения. Проверить private IP, redirect, пустой ответ, 429. Не обращаться к внутренним адресам в проверках.

### H08 — Ложный полный обход может снять хорошие предложения

**Где:** `scrapers/halyk.py:112–147`, `scrapers/fortemarket.py:138–171`, `scrapers/ispace.py:90–121`, `scrapers/tgrad.py:40`, `database.py:18`.

Halyk/Forte трактуют отсутствующий total как 0: полная первая страница без total даёт complete=true (воспроизведено). Tgrad считает любой 301/302 после первой страницы концом без проверки Location. iSpace не различает отсутствие JSON-LD и out-of-stock (`None`), а пустой HTML listing после первой страницы может подтвердить конец. Далее reconcile может деактивировать ранее виденные товары.

**Исправление:** валидировать схему/метаданные и подтверждение конца; malformed/blocked/unknown → partial/error. Для законного полного пустого каталога нужен отдельный подтверждённый результат: сейчас `complete and ids` вообще не даёт снять все исчезнувшие товары. Нужны fixtures последних страниц, block page, schema drift, отсутствующего total и genuinely empty catalog. **Меняет availability — согласовать.**

### H09 — canonical_key объединяет разные варианты

**Где:** `model_matching.py:8`, `extract_canonical_key:159`, `same_model`; `ai_service.py:685–740`.

Обычные iPhone возвращают family key без variant tokens. Воспроизведено совпадение iPhone 15 128GB eSIM Black и Dual SIM White. Цвет отбрасывается глобально; MPN в скобках может удаляться. Нет структурированных GTIN/MPN, match_confidence, версии алгоритма/происхождения ключа. AI принимает любую непустую строку до 500 символов; его prompt тоже предлагает игнорировать SIM/цвет.

Дополнительный `same_model` уже защищает сравнения от части плохих AI keys — не утверждается, что произвольный AI key сам по себе всегда склеит цены.

**Исправление:** сначала размеченный корпус реальных public product titles, negative pairs вариантов; shadow matching без записи; правила GTIN→MPN→brand/model/variant и confidence; versioned identity migration только после согласования. Не глобально пересчитывать canonical_key сейчас.

### H10 — Нет общего бюджета исходящих запросов на домен

**Где:** `scrapers/base.py:70–113`, `web/server.py:825/994`, `search_engine.py:436`, `scrapers/ispace.py:26/114`.

Semaphore=4 ограничивает магазины только внутри одного scan; live и details идут отдельно. iSpace имеет 4 product threads без общей задержки; page sleeps 0.3–1 с не объединяют пути. Нет общего Retry-After, jitter и circuit breaker для 403/429; backoff shop_scans существует, но не управляет live/details.

**Исправление:** эволюционно добавить shared per-origin scheduler/transport policy, общий лимит соединений, bounded retries и очереди, уважение Retry-After, stop/cooldown при блокировке. Одна сессия на адаптер с явным close. Конкретный стандарт в SCRAPER_STANDARD.md. **Согласовать частоты и допустимую свежесть.**

### H11 — Миграции при старте меняют и удаляют данные без контролируемой версии

**Где:** `database.py:53–325`, особенно identity_v2 и cleanup; `web/server.py:1415`.

`init_db()` автоматически пересчитывает ключи при отсутствии identity_v2, чистит cache, удаляет products/alerts по ценам и одному hardcoded случаю, перестраивает весь FTS при каждом старте. Общие except скрывают не только duplicate-column, но и реальные ошибки. FK не включены: на настоящем соединении приложения PRAGMA=0 воспроизведено; текущие объявленные связи пока без нарушений.

**Исправление:** numbered migrations, одна транзакция/проверенная последовательность, metadata/version, backup через SQLite backup API, dry-run/restore test; один раз rebuild FTS по версии. Чистку данных вынести в явную операцию. FK включать после проверки сирот, не слепо. **Только после согласования схемы/данных.**

### H12 — Старые цены не сохраняются, полноценной истории наблюдений нет

**Где:** `database.py:417–562`, `get_price_history_batch:487`, `detector.py:71`.

Оба writer игнорируют old_price_on_site. Временная БД воспроизводит 150000→0; в рабочей БД ни одной ненулевой old_price_on_site. «История» — только current/first/min/max, без датированных наблюдений. Детектор выбирает max(previous, first_seen, crossed-out), смешивая независимые основания скидки; подпись «ошибка/пропущен ноль» не означает подтверждённую ошибку продавца.

**Исправление:** сохранять/обновлять старую цену с проверками, маркировать basis и observed_at; повторно подтверждать подозрительное падение перед alert; observations/history с retention — отдельная migration. Не заполнять утраченную историю вымышленными точками. Тест single/batch roundtrip, сброс старой цены, акция без history, скачок/возврат.

### H13 — Публичный API раскрывает сохранённые поисковые запросы

**Где:** `web/server.py:1240`, `search_engine.py:550–563`, `database.py:1068`.

GET tracked categories открыт гостю и отдаёт `query`, полученный из пользовательского поиска. Даже без user_id запрос может содержать личный текст; его же сервер использует в последующих волнах. Профили Telegram в общем API не обнаружены; admin users защищён.

**Исправление:** доступ к query только admin; публично — только утверждённые названия категорий; сохранять нормализованную категорию вместо произвольного пользовательского текста, согласовать retention и opt-in для фонового отслеживания. Проверить guest response и отсутствие исходного query в публичных JSON/логах.

## MEDIUM

### M01 — Доверие к proxy headers и слабая граница CSRF

**Где:** `auth.py:64–110`, RateLimiter.

X-Forwarded-For принимается от любого peer — меняя его, можно обходить IP buckets; X-Forwarded-Proto определяет Secure cookie, X-Forwarded-Host участвует в origin check. Нет explicit trusted proxy policy. CSRF проверяется только при наличии Origin и сравнивает netloc, не полную origin. Telegram HMAC/compare_digest реализованы; auth_date проверяется только на слишком старое, не будущее; 24-часовой replay window — осознанный выбор, а не обнаруженный обход подписи.

**Исправление:** trusted proxy networks + canonical public origin; same-origin/CSRF token для mutating browser routes; short login freshness с допустимым clock skew; negative tests headers/origins/future auth. Согласовать топологию reverse proxy до изменения.

### M02 — Возможное попадание токена в сообщения об ошибках

**Где:** `web/server.py:705–708`, `auth.py:166–168`, `telegram_bot.py:78`, `log_manager.py:30`.

requests exceptions могут содержать URL Telegram с bot token; test-telegram возвращает str(e), ряд путей печатает исключение/response body целиком; централизованного redact нет. Существующие type(e).__name__ в notifier/AI полезны. Реального опубликованного токена не найдено.

**Исправление:** безопасные error codes, редактирование token/query/Authorization до лога; test с фиктивным токеном и ошибкой URL; отдельно rate limit test-telegram (сейчас отсутствует).

### M03 — Неконтролируемый рост кешей и диалогов

**Где:** `auth.py:131`, `search_engine.py:_LIVE_CACHE`, `ai_service.py:_QUERY_CACHE`, `telegram_bot.py:169`.

TTL не удаляет автоматически старые уникальные ключи; RateLimiter оставляет пустые buckets, chat history удаляется при следующем обращении того же chat. AI кэш называется LRU, но им не является. У `_ai_pending` ограничено число titles, не сумма ID/байтов.

**Исправление:** bounded TTL/LRU, общий byte/item budget, periodic eviction, max title/query lengths, метрики; synthetic нагрузка уникальных ключей с проверкой памяти.

### M04 — Групповые Telegram-диалоги и незавершённые задачи

**Где:** `telegram_bot.py:227–304`, `_chat_histories`.

Нет ограничения chat.type=private; история общая по chat_id, значит участник группы может продолжить контекст другого. Незарегистрированные пользователи допускаются к AI (блокировка есть лишь для известных users); есть per-user и provider limits. create_task на каждое сообщение без общего bounded queue, задача не удерживается для cleanup. Polling offset только в памяти, запуск второго процесса конфликтует.

**Исправление:** выбрать private-only либо явный групповой режим с изоляцией, определить доступ к AI, bounded tasks, сериализация одного диалога и shutdown; тесты group/restart/token rotation. Ничего не отправлялось во время аудита.

### M05 — Планировщик не хранит состояние источника/города/категории

**Где:** `web/server.py:1045–1146`, `database.py:969–1015`, пользовательский diff `database.py:1116`.

Freshness по магазину обновляется после подмножества категорий; wave index в RAM. Нет общей lease между gui.py/main.py/вторым процессом и отслеживания созданной scan task при shutdown. Hot categories в существующем diff возвращаются все, даже выше limit=3, и могут вытеснить rolling; products_count делает correlated LIKE scans. Это исходные пользовательские изменения, не правка аудита.

**Исправление:** хранить cursor/due по source+location, один scheduler owner, ограниченный hot budget и честная очередь; tracked category last_scanned фиксировать по фактическому coverage, не по пойманным внутри live ошибкам. Изменять diff только после согласования.

### M06 — Стоимость арбитража растёт по каждому товару

**Где:** `web/server.py:846–860`, `database.py:788`, `search_engine.py:217–354`.

На каждый сохранённый товар отдельный поиск конкурентов/connection; часть HTTP handlers выполняет SQLite синхронно, busy_timeout до 15 с. LIKE fallback и correlated count плохо масштабируются; FTS trigger срабатывает даже при UPDATE только цены/description. Сортировка price_desc/relevance идёт после LIMIT по дешёвым товарам — не гарантирует топ по всему результату.

**Исправление:** батчи по canonical_key+city, отдельный DB executor/ограниченная очередь writer, пересчёт только затронутых групп, FTS updates только по индексируемым полям; запросы сортировать до LIMIT согласно режиму. Сначала EXPLAIN/измерения p95 на копии, затем изменение; SQLite пока сохранить.

### M07 — Разные идентификаторы одного источника и нестабильные fallback IDs

**Где:** `search_engine.py:504`, `scrapers/shopkz.py:87/182`, `scrapers/alser.py:95`, `scrapers/fourmobile.py:94`, `scrapers/schema_listing.py:76`.

Shop.kz live строит id из последних 20 символов href вместо ID feed; Alser обрезает slug до 40 символов; title-based hashes меняются при переименовании. В БД 2513 групп одинаковых shop+url с несколькими записями, но часть URL — категории и общие fallback, поэтому это НЕ доказанные 2513 дубликата.

**Исправление:** по источнику устойчивый external_id, cross-path fixtures, dry-run collision map. Не удалять дубликаты автоматически и не менять id без миграции связей.

### M08 — Ошибки числового парсинга и метаданных описания

**Где:** `scrapers/technodom.py:35`, `scrapers/base.py:11`, `web/server.py:321–346`.

`TechnodomScraper._price(199990.0)` даёт 1999900 (воспроизведено). Общий parse_price не имеет явного контракта знака/currency/разделителей и может трактовать число вне цены как цену. В Kaspi description ветке собранный specs не возвращается; Forte догрузка берёт первый search hit без проверки objectID. Предел 10 млн — бизнес-фильтр, не универсальная валидация.

**Исправление:** единый Decimal/numeric contract, проверка валюты и формата до преобразования; описание только совпавшего external_id. Тесты decimals, отрицательное, installment text, malformed, два похожих товара. Изменение парсеров — согласовать.

### M09 — Жизненный цикл HTTP/browser и ограничения объёма

**Где:** `scrapers/dns.py:22–103`, session-based adapters, `scrapers/shopkz.py:46–105`.

DNS browser.close не в finally вокруг всей работы/context creation; Playwright context manager помогает cleanup, но гарантии при отмене не протестированы. Sessions адаптеров не закрываются явно. YML скачивается во временный файл без size cap; products всё равно копятся в RAM. Нет общего лимита длины полей/response bytes.

**Исправление:** try/finally/async context manager, явный close contract, max response bytes/items/time, потоковые bounded batches; mock cancellation до/после navigation и partial feed. Не убирать Playwright без подтверждённой альтернативы.

### M10 — AI output остаётся недоверенным, нет денежного бюджета

**Где:** `ai_service.py:194`, `ask_ai_consultant:540–613`, `normalize_product_titles_batch:732`.

Titles/history/query интерполируются в prompt; нет явной границы «данные, не инструкции». Для answer/suggested_questions нет строгой схемы/длины; карточки ограничиваются ID из БД — это хорошая защита. AI не имеет shell/tools и сам по магазинам не ходит. Provider concurrency/rate есть, но нет daily token/cost budget/max output tokens. Часть текста ответа может содержать неподтверждённые характеристики/цены; prompt injection не означает RCE.

**Исправление:** строгие schemas и output bounds, structured untrusted context, deterministic цены/ссылки из DB, daily budgets, validated key format/provenance; offline malicious title/history/provider responses. Живые AI вызовы для исправления не нужны.

### M11 — Deployment невоспроизводим и не проверяет восстановление

**Где:** `requirements.txt`, `Dockerfile`, `docker-compose.yml`, `.github/workflows/docker-publish.yml`.

Все зависимости `>=`, base tag Playwright 1.49.0, pip разрешает более новый Playwright и отдельно ставит Chromium; это не доказанный crash, но сочетание browser/system libs дрейфует. В Dockerfile нет USER/HEALTHCHECK, Compose нет resource/log limits; watchtower:latest с docker socket обновляет latest каждые 5 минут. Release может одновременно запускать destructive init_db. Есть build-after-tests, но нет restore smoke, frontend CI, dependency/secret/image scan; actions tags не SHA-pinned.

**Исправление:** tested lock/constraints и согласованный browser image, non-root с проверкой volume/Chromium, health/readiness, log rotation/resources; immutable release + backup/restore/rollback drill. Watchtower отключать/менять только по решению владельца. Docker отсутствовал в доступном PATH; образ не собран и production не проверен.

### M12 — Privacy/retention не оформлены

**Где:** `database.py:172–218`, `ai_service.py:540–573`, `web/templates/index.html:7/1793`, `telegram_bot.py:166`.

Локальные settings.json и prices.db имеют режим 0644; доступность другим OS-пользователям зависит также от прав родительских каталогов. Хранятся Telegram profile, settings, sessions и outbox; нет удаления аккаунта/экспорта/retention job. AI получает пользовательский текст, диалог и каталог; browser грузит изображения продавцов, внешние CSS/JS/favicon, IP geo fallback обращается к внешним сервисам по действию определения города. Обработка точных координат происходит локально в JS. Серверного image proxy/cache в коде не найдено; Telegram получает photo URL и загружает его самостоятельно.

**Исправление:** карта данных/провайдеров, privacy notice и понятный выбор AI/гео, срок хранения/удаление/экспорт, минимизация логов; место production DB и условия передачи проверить отдельно. Юридическое соответствие законодательству РК этим техническим аудитом не подтверждено. Не удалять данные без согласования.

### M13 — Outbox не учитывает скрытие алерта и ответы Telegram по типам

**Где:** `notifier.py:141–172`, `database.py:564/574/1039`, `send_telegram_alert:66–84`.

Перед доставкой проверяются user/settings/current price, но не is_dismissed и не существование самого alert; скрытый алерт может остаться в pending. Telegram 429/403 сводятся к bool, Retry-After теряется; sendPhoto failure сразу пробует sendMessage даже после 429. Проверка dedup и INSERT alert в разных транзакциях, нет уникального ключа события; multi-process/повтор может создать два alert. UNIQUE(alert_id,user_id) защищает лишь доставку внутри одного alert. Сбой после успешной отправки до finish_notification оставляет возможность повтора — типичное at-least-once, не exactly-once.

**Исправление:** согласовать отмену при dismiss; проверять состояние alert при delivery, классифицировать retryable/permanent/Retry-After, атомарный event dedup; тесты 429/403/dismiss/send-then-crash/concurrent writers. Новые constraints — только вместе с согласованной migration.

## LOW

### L01 — Документация расходится с реализацией

**Где:** README, PROJECT_CONTEXT.md:14–35, UI справка, telegram_bot.py:97.

Встречаются 17 магазинов, DNS Stealth, Technodom REST/Эврика Next.js, cache-only и «100% точность», хотя код другой. История цен и LRU описаны шире реализации. Исправить по проверенным SOURCES/контрактам, без обещаний полной точности и официальности internal APIs.

### L02 — Исторические DB/settings увеличивают Git и риск случайной публикации

**Где:** Git history, введены `128875f`, удалены из tracking последующими изменениями до текущего HEAD.

14 DB blobs содержат каталог/alerts; один settings blob без непустых полей token/secret/api_key/password. Пользовательских таблиц в этих DB не найдено. Сейчас DB/settings игнорируются. Добавить secret scanning и проверку новых больших бинарных файлов; удаление истории — отдельное согласование, сейчас не нужно ради косметики.

### L03 — Настройки и редкие пути имеют технический долг

**Где:** `config.py:319/442`, `detector.py:145–165`, `requirements.txt`.

OPENAI_API_BASE получает непустой default из env getter, поэтому settings base обычно не действует; файл настроек записывается без atomic replace/lock. В other_stores_avg ветке check_market_arbitrage обращается к неинициализированной market (редкий путь, основной caller не использует). python-telegram-bot установлен, хотя бот использует aiohttp; удалять зависимость после import/test проверки. Исправления отдельными маленькими изменениями с targeted tests.

## SAFE AUTO-FIX

Исходный перечень безопасных работ приведён ниже. Ignore и регрессионные тесты уже выполнены в этапе 1; остальные пункты этого перечня не следует считать выполненными автоматически:

- Поддерживать AUDIT/SOURCES/проект стандарта и исправить фактические ошибки README без изменения поведения.
- Добавить исключения `.env`, ключей и backup в ignore; проверить, что `.env.example` доступен и runtime не зависит от COPY секретов.
- Добавить regression fixtures и тесты найденных багов без сетевого/production доступа.
- Нейтральный комментарий Halyk «Initialize website session», не меняя cookies/HTTP/impersonation.
- Убрать trailing whitespace только в собственных новых файлах. Существующий diff пользователя не трогать.

Изменение TLS, user-agent, retries, маршрутов, UI rendering, миграций и цен НЕ считать «косметическим auto-fix».

## NEEDS MY APPROVAL

Этап 1 согласован и выполнен локально. Для этапов 2–5 сохраняется отдельное согласование:

| Этап | Конкретные изменения | Приёмка | Откат |
|---|---|---|---|
| 1. Security без миграции — выполнен локально | C01 безопасный DOM/URL; H01 глобальное скрытие только admin; H03 DB-only guest/default и gated refresh; H13 query только admin; H04 ignore; M02 redact | 85 tests + frontend + новые guest/XSS/implicit-live/secret tests; без реальной рассылки | отдельные commits; revert к сохранённой версии кода, без DB rollback |
| 2. Auth/privacy | H02 opt-in loopback dev; M01 trusted proxy/CSRF/login freshness; M04 private-only bot по умолчанию, M12 карта данных | тесты local/proxy, role matrix, group isolation; согласованные env/examples | предыдущая конфигурация и версия, без публикации токенов |
| 3. Достоверность цен/сбора | H06 TLS после проверки; H08 complete; H12 old price; M08 decimals; H07 controlled details; H10 единый limiter | offline fixtures всех 18, canary одного магазина; error/429/cancel; сравнение counts | feature flags per source + revert адаптера; не массовое удаление данных |
| 4. DB/identity | H05 offer identity, H11 versioned migrations, H09 shadow matching, history/retention по необходимости | SQLite backup+restore; dry-run коллизий/связей; размеченный corpus; old/new API compatibility | проверенный snapshot + версия приложения; окно остановки writer |
| 5. Масштабирование/release | M03 bounded caches; M05 source schedule; M06 batch DB; M09 resources; M10 budgets; M11 reproducibility/CI | simulated 100 sources, нагрузка на копии, p95/память/queue age, canary/restore | immutable предыдущий image + совместимая DB либо restore |

Первое запрашиваемое решение: **разрешить этап 1 в указанном объёме?** Он меняет права скрытия алертов и поведение обновления поиска, поэтому требует согласования по прямому условию пользователя. Auth, схема, canonical_key и адаптеры на этом этапе не меняются.

## DO NOT CHANGE YET

- Не запускать текущий init_db на production ради проверки.
- Не пересчитывать canonical_key, не менять offer IDs, не удалять duplicate/description/history/users, не включать FK без dry-run.
- Не удалять рабочие HTML/internal JSON adapters, curl impersonation или DNS Playwright без сравнительной проверки.
- Не считать Halyk homepage session обходом CAPTCHA: в коде обычный GET/session, solver/access-control bypass не найден. При этом Mechta действительно пересоздаёт session после 403/422; будущая policy должна отличать session expiry от запрета доступа.
- Не вращать proxy/fingerprint/аккаунты и не добавлять CAPTCHA solving.
- Не публиковать ключи, не переписывать историю Git, не отзывать credentials без доказательств и решения владельца.
- Не менять Watchtower, не выполнять build/push/deploy и не отправлять Telegram во время аудита.
- Не переходить на PostgreSQL/Redis/Kafka/microservices только из-за целевого числа магазинов. Сначала измерить single-process pipeline.

## Что осталось непроверенным

Production topology/TLS/reverse proxy, запущенные контейнеры и их права, резервные копии/восстановление, реальная работоспособность всех источников сегодня, robots/условия/разрешения каждого владельца, причина нужности impersonation, пригодность альтернативы DNS, provider quotas и фактическая стоимость AI. Нагрузочная готовность к 100 источникам пока **не подтверждена**.

`AUDIT_FINAL.md` составляется после согласованных исправлений и повторной приёмки: закрытые/отложенные ID, commits, тесты, миграции/rollback, ограничения и оставшиеся решения. Сейчас не создаётся документ с ложным впечатлением завершённых исправлений.

## Внешние проверочные источники

- [SQLite: foreign keys](https://www.sqlite.org/foreignkeys.html) — декларации FK требуют включения enforcement на соединении.
- [Docker: build context / dockerignore](https://docs.docker.com/build/concepts/context/) — исключения управляют тем, что попадает в build context и COPY.
- [Telegram Login Widget](https://core.telegram.org/widgets/login/) — алгоритм подписи и проверка auth_date.
- [OSV QueryBatch](https://google.github.io/osv.dev/post-v1-querybatch/) — источник проверки установленных зависимостей; отсутствие совпадения не доказывает отсутствие уязвимостей.
