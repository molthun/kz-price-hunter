# Стандарт подключения магазина — проект для согласования

Статус: **предложение, не реализованный интерфейс**. Основа — существующие PagedScraper/ScanResult и SHOP_REGISTRY. Не требуется переписывать все 18 адаптеров одновременно.

## Модель работы

`Источник → ограниченный транспорт → parser → validation → offer snapshot → DB → matching/detection → outbox`.

Все входы (scan, live, detail, tracked category, CLI) используют один источник конфигурации, enabled flag и общий транспортный бюджет. Пользовательский поиск по умолчанию читает БД. Refresh запускается отдельной авторизованной задачей с понятным статусом и временем последнего подтверждённого наблюдения.

## Минимальный manifest

Для каждого store: store_id, display_name, storefront domains, data domains, разрешённые redirect domains, source_type A/B/C/D/E, документация/условия/robots (URL, дата проверки, неизвестность явно), категории, location policy, capabilities, контакт ответственного и дата canary.

Capabilities: scan_category, search, get_product, healthcheck. Недоступная возможность возвращает явный unsupported, а не пустой успешный каталог. Текущий scrape остаётся совместимым wrapper; scan_category можно вводить постепенно. Не заставлять feed поддерживать искусственную пагинацию или browser scraper — ненужный search.

`healthcheck` — малый публичный запрос без записи данных/рассылки, по тому же limiter, не постоянный полный обход. Его результат различает transport, schema и data quality.

## OfferSnapshot

| Поле | Правило |
|---|---|
| store_id / external_product_id | Устойчивые идентификаторы; внешние ID строковые; не обрезать slug без collision check |
| seller_id | Отдельно, если источник действительно предоставляет; иначе None |
| title | Обязательная строка с лимитом длины, недоверенные данные |
| brand/model/sku/mpn/gtin/variant | Только полученные/детерминированно разобранные факты; provenance для извлечения |
| category | Категория источника + отдельный mapping master category |
| product_url / image_url | Проверенный http/https URL; source allowlist; WhatsApp как явный link_type/contact с проверенным получателем |
| current_price / old_price | Валидированное decimal/numeric значение; current>0; old>current либо None; не использовать installment вместо полной цены |
| currency | Подтверждённая KZT или иная валюта; не молча смешивать |
| availability | in_stock / out_of_stock / unknown, evidence отдельно |
| city/location_scope | Подтверждённый город / nationwide / unknown; запрошенный город хранить отдельно от подтверждённого |
| retrieved_at | UTC времени наблюдения, отдельно от DB updated_at |
| source_type / source_url | Происхождение данных; не переносить credentials/query secrets |
| description | Optional capability и отдельная content policy; ограниченный размер/TTL; не обязательное поле для нового магазина |
| match_confidence / identity_version | Результат отдельного matching, не догадка scraper |

Отсутствующие значения — None. Переход от текущих dict и схемы DB делать через адаптер/validator, затем согласованную migration. Сначала допустим shadow validation с метрикой отклонённых полей; не молча отбрасывать существующий каталог.

## ScanOutcome

Сохранять совместимость ScanResult, добавить явно:

- items, status (`complete`, `partial`, `limited`, `blocked`, `failed`), error_code;
- coverage scope (store/source/category/location), observed_at;
- pages/requests/items/rejected_items, duration, retry_after;
- доказательство окончания (валидное total/page metadata или проверенный next-link/empty marker).

`complete` допустим только при подтверждённом полном охвате конкретного scope. HTTP 200 или отсутствие parser matches этого не доказывают. HTTP 403/CAPTCHA/неожиданный redirect/malformed JSON — не конец каталога. Снятие отсутствующих предложений выполняется только внутри подтверждённого scope. Полностью пустой каталог требует отдельного подтверждения, а при резком падении числа товаров — карантин и повторная проверка; не массовое удаление.

## Транспорт и polite crawling

Начальные параметры ниже — консервативное предложение для нового источника, а не согласованный с магазинами лимит:

- per-origin max_concurrency=1; min_delay=2 с + jitter до 1 с между началами запросов; глобально не больше 4 сетевых операций на первом этапе;
- отдельный browser semaphore=1; внутренний fan-out iSpace тоже расходует domain slots;
- timeout connect=5 с, total=20 с; feed до 90 с с отдельным max_bytes; browser page до 30 с и общим deadline задачи;
- не более 2 повторов для timeout/выбранных 5xx с exponential backoff+jitter; 429 соблюдает Retry-After (seconds/date), общий cooldown сохраняется;
- 401/403/CAPTCHA — остановить источник/health status, без автоматической смены личности/прокси; session expiry можно восстанавливать только по документированному признаку, не любому 403;
- caps на bytes/items/страницы/поля, проверка Content-Type и schema; feed streaming не должен копить безлимитный список;
- TLS verification обязательно; запрещены internal/link-local/loopback targets для merchant data, проверять redirect и адрес фактического соединения, не только первоначальный hostname;
- retry/limiter используют monotonic clock; HTTP-date интерпретируется как UTC; sleep не держит DB lock;
- один явный lifecycle сессии; try/finally для browser/context; cancel не должен оставлять unmanaged задачи;
- идентифицируемый User-Agent с контактом там, где проверена совместимость. Существующий impersonation не менять без canary; отсутствие совместимости отражать в manifest;
- не подключать CAPTCHA solving, proxy/account farming или fingerprint rotation для обхода запрета.

Значения размера тела и поля определить после размеров реальных fixture/feed: например, отдельный feed cap не должен отсечь действующий Shop.kz. Пределы должны быть конфигурацией с тестами, а не разбросанными magic numbers.

## Scheduler для десятков/100 источников

Сохранить один процесс/SQLite на первом этапе. Store registry должен быть одним для UI, live, background, CLI и tests. Job identity — source/category/location; per-job due/cursor/last-success/coverage в DB; один владелец scheduler/lease. Disable источника запрещает все сетевые входы к нему.

Очередь bounded, честная ротация: hot tasks имеют квоту, не вытесняют холодные бесконечно. Live refresh объединяет одинаковые запросы single-flight и не создаёт постоянный scan без отдельной политики. Pending/running задачи удерживаются для shutdown; на рестарте явно отмечается partial/retry, не complete.

Метрики: request count/status, latency, queue age, coverage age, rejected schema/price, complete ratio, offers changed, DB lock wait, event-loop lag, RAM/browser processes, alerts/notification retries. Без user query/tokens в labels.

Переход к отдельным worker/DB/queue обсуждать только по измеренным bottlenecks: устойчивые DB waits, event-loop lag, невозможность уложиться в freshness budget при допустимых лимитах источников. Число магазинов само по себе не является основанием для enterprise rewrite.

## Matching и alerts

- Разделить offer identity и product identity. Один товар может иметь несколько предложений по городам/продавцам.
- GTIN/EAN → manufacturer SKU/MPN → brand+model+variant; normalized title — fallback с ограниченной уверенностью.
- Проверять capacity/RAM/storage/CPU/GPU/generation/size/SIM/region/bundle/pack count; цвет учитывать, когда меняется SKU/вариант.
- AI только предлагает кандидата; его текст не даёт статус exact. Хранить method/version/confidence и отрицательные тестовые пары.
- Автоматическое межмагазинное сравнение/уведомление — только по согласованному порогу confidence, совместимому региону/валюте/availability и свежим данным.
- Отделять old price on site от historical observation и competitor price. Не называть вероятную аномалию подтверждённой ошибкой продавца.
- Alert+outbox остаются атомарными; dedup должен иметь DB constraint/idempotency identity. Доставка остаётся at-least-once: timeout после фактической доставки Telegram может дать повтор, exactly-once не обещать. Учитывать Telegram Retry-After и permanent 403; проверять dismissed/cancelled alert при delivery по согласованной семантике.

## Проверки нового магазина

1. Manifest + source classification + законный публичный путь/условия; не помещать cookie/token/личные данные в fixtures.
2. Fixtures: обычная карточка, zero/decimal/old price, out-of-stock/unknown, missing optional fields, variant/город, unsafe URLs/HTML.
3. Пагинация: first/middle/last, duplicate page, genuinely empty, partial timeout, missing total, malformed, 403/429/5xx, redirect.
4. ID одинаков на feed/category/search/detail и разный для разных предложений. No title/slug truncation collisions.
5. Полная запись и чтение полей single/batch; reconcile не деактивирует при partial; inactive→active корректно.
6. Timeout/retry/Retry-After/domain limiter/cancel/resource close под fake clock/HTTP, без production запросов.
7. Canary одной категории с малым request budget, ручная проверка публичной карточки, старый и новый parser рядом на fixture. Не обрабатывать CAPTCHA автоматически.
8. Регистрация в едином реестре, включение по одному источнику, smoke tests, rollback переключателем, обновление SOURCES.

## Приёмка масштабирования

Предлагаемая проверка после исправлений: 100 synthetic sources, включая медленные/429/blocked/malformed, параллельный DB-only поиск и одну live задачу; затем нагрузка на обезличенной/локальной копии большого каталога. Проверить bounded RAM/tasks/requests, соблюдение domain intervals, отсутствие двойных scheduler jobs, сохранение cursor при restart, отсутствие массовой деактивации после ошибок и воспроизводимый restore.

До измерения согласовать целевые p95 UI, допустимую давность цен, дневной HTTP/AI budget и ресурсы production. Не объявлять готовность к 100 источникам по одному успешному unit-test прогону.
