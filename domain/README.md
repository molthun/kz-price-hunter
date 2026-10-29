# Пакет `domain/`: доменные модели и детерминированная логика

`domain/` — слой правил KZ Price Hunter 2.0: канонические товары, продавцы, каналы,
предложения, разбор запроса, поиск, матчинг и аналитика. Пакет не знает, откуда пришли данные
и как они отдаются наружу: хранение — `repositories/`, HTTP — `web/`, сбор — `scrapers/`.

Границы слоя:

- логика детерминирована и не имеет побочных эффектов: одинаковый вход даёт одинаковый выход,
  сеть и файлы не читаются, текущее время берётся из `utc_now_iso()` или передаётся аргументом;
- схема БД аддитивная: `domain/` не создаёт таблиц и не меняет `schema_version`; новые сущности
  добавляются так, чтобы откат на предыдущий образ не требовал восстановления базы;
- исключение одно — `adapter.py`: он пишет в **переданное** соединение в транзакции вызывающего
  и собственных соединений не открывает.

## 1. Модули

| Модуль | Назначение | Ключевые имена |
|---|---|---|
| `adapter.py` | Переходный слой: словарь товара старого контура → сущности 2.0 (продавец, канал, товар, предложение, история цен). Единственный модуль пакета, выполняющий запись в БД. | `transliterate_to_slug`, `resolve_seller_and_channel_meta`, `sync_legacy_product_to_v2` |
| `adaptive_discovery.py` | Превращает неудовлетворённый спрос в задачи поиска новых продавцов и каталогов. | `DiscoveryTask`, `DiscoveryTaskStatus`, `SearchPatternGenerator.generate_queries`, `AdaptiveDiscoveryEngine.task_from_signal` |
| `channel_pricing.py` | Ценовая матрица товара: один продавец в разных каналах и конкуренция независимых продавцов, без смешения состояний товара. | `ProductChannelMatrix`, `SellerChannelPricing`, `ChannelSavings`, `build_channel_matrix` |
| `condition.py` | Строгое разделение NEW / USED / REFURBISHED / OPEN_BOX: б/у и уценка не попадают в «лучшую цену нового». | `ConditionFilter`, `ConditionBreakdown`, `build_condition_breakdown`, `filter_offers_by_condition` |
| `discovery.py` | Сущности и разрешённые переходы жизненного цикла найденного кандидата до индексации. | `SellerCandidate`, `CandidateStatus`, `transition_allowed`, `DiscoverySource` |
| `models.py` | Базовые сущности и справочники: товар → продавец → канал → предложение, история цен. | `Condition`, `Availability`, `ChannelType`, `Seller`, `Channel`, `CanonicalProduct`, `Offer`, `OfferPriceHistory` |
| `query_parser.py` | Детерминированный разбор естественного запроса: город, состояние, бренд, цена, канал, атрибуты. | `QueryParser.parse`, `ParsedQuery`, `KZ_CITIES_MAP` |
| `search_analytics.py` | События поиска, KPI качества и сигнал спроса для адаптивного discovery. | `SearchEvent`, `SearchQualitySummary`, `QueryDemandSignal`, `normalize_search_query` |
| `search_core.py` | Оркестрация поиска: парсинг, выборка из репозитория, агрегация по продавцам, свежесть и покупаемость, релевантность. | `SearchCore.search`, `SearchResponse2`, `ProductSearchResult`, `AggregatedOffer`, `SearchOutcome` |
| `seller_identity.py` | Нормализация и проверка идентификаторов казахстанских продавцов: телефон, домен, БИН, название, площадка. | `normalize_phone`, `normalize_domain`, `normalize_bin`, `normalize_business_name`, `is_weak_identity`, `SellerMatchResult` |
| `seller_matcher.py` | Детерминированное сопоставление кандидата с существующим продавцом по сильным ключам и конфликтам. | `SellerMatcher.match_candidate_against_seller`, `MatchDecision` |
| `social_content.py` | Разбор коммерческого текста Telegram и Instagram: цены, состояние, город, срок жизни наблюдения. | `ContentObservation`, `ContentType`, `ExtractedOfferCandidate`, `SocialContentExtractor.extract_candidates` |
| `source_profiler.py` | Определяет предпочтительный способ сбора каталога продавца — от фида/API (Tier 1) до распознавания изображений (Tier 7). | `SourceProfiler.profile_url`, `SourceProfile`, `ExtractionTier`, `CatalogFormat` |
| `freshness_v2.py` | Срок жизни предложений по типу источника (`S2-E25`): retail API, YML-фид, сайт, пост Instagram/Telegram, сторис, б/у объявление, меню/еда. | `SourceFreshnessType`, `FreshnessStateV2`, `OfferFreshnessResult`, `infer_source_freshness_type`, `evaluate_offer_freshness` |
| `price_history_v2.py` | История цен 2.0 (`S2-E26`): ряды строго изолированы по `(product_id, seller_id, channel_id, condition)`, чтобы прямая цена магазина и маркетплейс не смешивались в одну линию. | `PriceHistoryPoint`, `PriceHistorySeries`, `ProductPriceHistoryReport`, `build_price_history_series`, `make_price_series_key` |
| `provenance.py` | Проверяемые факты о продавце и предложении (`S2-E28`): вместо субъективного значка «Проверенный продавец» показываются подтверждённые ключи (БИН, домен, телефон, каналы) и источник/возраст цены. | `SellerProvenance`, `OfferProvenance`, `build_seller_provenance`, `build_offer_provenance` |
| `vision_extraction.py` | Конвейер извлечения товаров и цен из изображений и сторис (`S2-E10`) с порогом уверенности `>= 0.75`, защитой от частично закрытых цен (`Цена не определена`), зачёркнутых цен, весовых цен и чужих валют. | `VisionCandidate`, `VisionExtractionEngine`, `MIN_VISION_PUBLISH_CONFIDENCE`, `PRICE_NOT_DETERMINED` |
| `search_assistant.py` | Помощник выбора поверх Search Core 2.0 (`S2-E22`): разбирает естественные запросы с бюджетом и условиями (рассрочка, скидки, город) и отвечает строго по реальным записям БД без галлюцинаций. | `SearchAssistant`, `SearchAssistantPlan`, `SearchAssistantResponse` |
| `seller_onboarding.py` | Саморегистрация продавцов («Добавить магазин», `S2-E27`): валидация формы, нормализация ключей, профилирование ссылок и постановка в очередь модерации (`REVIEW_REQUIRED` -> `APPROVED` -> `INDEXED`). | `SellerOnboardingSubmission`, `OnboardingResult`, `validate_onboarding_submission`, `prepare_onboarding_candidate` |
| `monitoring_v2.py` | Четырёхуровневый центр мониторинга и наблюдаемости (`S2-E23`, `S2-E24`): продавец, источник (Tier 1–7), канал (межканальные расхождения цен), предложение и качество данных. | `MonitoringOverviewV2`, `build_monitoring_overview_v2` |

## 2. Как это устроено

### Condition: отсутствие данных — не ответ

`Condition.normalize(..., default=UNKNOWN)` возвращает `UNKNOWN` для пустого или нераспознанного
значения: неразмеченный товар не становится «новым» по умолчанию. Фильтры `ConditionFilter.matches`
сопоставляют только точную категорию, поэтому `UNKNOWN` не проходит ни в `new`, ни в `used`;
в разбивке `build_condition_breakdown` он считается отдельно (`unknown_offers_count`), а не
приписывается к ближайшей категории. Так «нет данных» остаётся видимым, а не превращается в ответ.

### Свежесть и покупаемость

`SearchCore.search` для каждого предложения вызывает `data_quality.freshness(observed_at)` и
считает `is_purchasable` только при наличии на складе или предзаказе, не устаревшей цене и
`price > 0`. Лучшее предложение, минимум и максимум цены выбираются **только** среди покупаемых:
устаревшая или отсутствующая цена не объявляется выгодой. Предложения без покупки остаются в
выдаче, но сортируются после покупаемых.

### Приватность поисковой аналитики

`SearchEvent.__post_init__` проверяет запрос через `search_analytics.is_sensitive`: телефон, почта,
ссылка, ник, адресные слова и текст длиннее `MAX_QUERY_CHARS` (80) заменяются на `[redacted]`.
Слой хранения (`repositories/search_analytics_repo.py`) добавляет второе правило: текст хранится
только с третьего повтора запроса (`MIN_OCCURRENCES_TO_STORE_TEXT = 3`), иначе записывается
`[rare]`; если счётчик недоступен, запрос считается редким. Топ запросов исключает оба маркера.
Идентификаторы пользователя в событие не входят: полей для них нет.

### TTL и `expires_at`

Для эфемерного контента (`ContentType.STORY`) `ContentObservation.__post_init__` сам ставит
`expires_at = observed_at + 24h`; `is_expired()` сравнивает срок с переданным или текущим временем.
Извлечённое предложение наследует `expires_at` наблюдения: срок жизни истории переносится
в кандидата, а не теряется при разборе текста.

### Матчинг продавцов

`SellerMatcher` не сливает продавцов по похожести названий. Сильные ключи — БИН, домен, телефон,
идентификатор магазина на площадке; совпадение БИН даёт `AUTO_MATCH`, но **конфликт** БИН или
домена при родовом названии — `REJECT`. Общий домен при несовместимых названиях даёт `REVIEW`
(бренд и его реселлер), совпадение только названия — тоже `REVIEW`, если известные телефоны не
противоречат (иначе `REJECT`). Значения, которые делят многие (домен площадки, общий колл-центр,
заглушка, пустой БИН), сильными ключами не считаются: `is_weak_identity` и `shared_values`
отсекают их до сравнения.

### Жизненный цикл и гипотезы

`transition_allowed` разрешает только переходы из `ALLOWED_TRANSITIONS`; неизвестный статус
запрещён, `REJECTED` — конечный. `SourceProfile.observed = False` означает, что профиль построен
по виду адреса и является гипотезой: `profile_url` не выдаёт догадку за проверенный каталог.

## 3. Как добавить модуль

1. Создать `domain/<имя>.py`: dataclass-модели с `to_dict()` и, если модель читается из строки БД,
   `from_row()`; функции — чистые, без обращений к БД, сети и файлам.
2. Экспортировать публичные имена в `domain/__init__.py` и добавить их в `__all__` (список
   поддерживается в алфавитном порядке) — импорт из других слоёв идёт через пакет.
3. Написать тесты `test_*.py` в `tests/<блок>/`: доменные — в `tests/core/`, `tests/sellers/`,
   `tests/search/`; из корня их находит общий `unittest discover`. Проверяем поведение, а не
   наличие класса.
4. Не добавлять миграций и не менять `schema_version`; не помещать в пакет ключи, токены,
   cookies и персональные данные — только доменные значения.

## 4. Проверки

```bash
# полный набор, на временных данных — рабочая prices.db не затрагивается
DATA_DIR=$(mktemp -d) ./venv/bin/python -m unittest discover -s . -p "test_*.py"

# только доменные тесты — тоже на отдельном каталоге данных
DATA_DIR=$(mktemp -d) ./venv/bin/python -m unittest \
  tests.core.test_domain_models tests.sellers.test_condition \
  tests.sellers.test_channel_pricing tests.sellers.test_seller_matcher \
  tests.sellers.test_discovery_lifecycle tests.sellers.test_discovery_profiler \
  tests.search.test_search_core_v2 tests.search.test_search_analytics \
  tests.search.test_search_privacy_v2 tests.search.test_adaptive_discovery_v2
```

Перед изменением пакета полезно убедиться, что проходят `tests/core/test_offer_identity.py`,
`tests/search/test_search_analytics_v2.py` и `tests/ai/test_matching_precision.py`: они фиксируют
тождество предложений, приватность и точность матчинга.
