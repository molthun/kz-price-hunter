# Независимый аудит Codex: S2-E11, S2-E13, S2-E16 и S2-E14

- **Дата:** 2026-10-22, Asia/Almaty.
- **Аудитор:** Codex.
- **Автор реализации:** Antigravity.
- **Срез:** ветка `main`, HEAD `17fc864` (коммиты `c0903b5` и `17fc864`).
- **БД:** рабочая `prices.db` неприкосновенна (`PRAGMA integrity_check = ok`).
- **Тестовое окружение:** временный изолированный `DATA_DIR`, внешняя сеть не использовалась.

---

## 1. Итоговый статус этапов

| Этап 2.0 | Название | Вердикт аудита | Обоснование |
|---|---|---|---|
| **S2-E11** | Unified Product Index & Search Core 2.0 | **APPROVED** | Все 6 замечаний (C-E11-01…06) закрыты. Исключение stale/out-of-stock из `best_deal` подтверждено. |
| **S2-E13** | Search Quality & Search Analytics | **APPROVED** | Privacy blocker C-E13-01 снят (маскировка, правило редких запросов, `@require_admin`). Формула SSR P06 и retention подтверждены. |
| **S2-E16** | Search UI 2.0 (витрина, фильтры состояний) | **APPROVED** | Замечания C-E16-01…05 устранены (фильтры Condition, чтение root-полей, opt-in тумблер с `localStorage`). |
| **S2-E14** | Adaptive Discovery & Query Demand Generator | **APPROVED** | Доменная модель `DiscoveryTask`, генератор мультиплатформенных запросов, дедупликация и API реализованы в строгом соответствии с контрактом P10 / Stage 14. |

---

## 2. Подтверждение закрытия дефектов контрольного аудита (E11, E13, E16)

1. **C-E11-03 / C-E16-03 (Out-of-stock / Stale fallback):**
   - В `domain/search_core.py` и `web/server.py` полностью устранен fallback на просроченные или недоступные офферы. При отсутствии `is_purchasable` офферов возвращаются `null` и флаг `all_stale = true`.
2. **C-E13-01 (Privacy Blocker):**
   - В `domain/search_analytics.py` внедрена автоматическая маскировка персональных данных через `search_analytics.is_sensitive(query)`.
   - В `repositories/search_analytics_repo.py` соблюдено правило редких запросов `MIN_OCCURRENCES_TO_STORE_TEXT = 3`: до 3-го повторения текст не сохраняется ни в сыром, ни в нормализованном виде (`[rare]`).
   - Эндпоинт `/api/v2/analytics/search-quality` защищен `@require_admin`.
3. **C-E13-02 (Гармонизация формулы SSR):**
   - Формула приведена к спецификации P06: `answered = found + weak + not_found`, `SSR = found * 100 / answered`. Ошибки (`ERROR`) не искажают знаменатель SSR и выделены в метрику `error_rate`.
4. **C-E13-03 (Канонизация города в запросе):**
   - `detected_city` канонизируется через `search_analytics.canonical_city(...)`, исключая потерю города из естественного языка.
5. **C-E13-04 (Retention фонового обслуживания):**
   - `database.py:prune_search_analytics` вызывает `SearchAnalyticsRepository.purge_old_events(days=90)`.
6. **C-E16-01…05 (Фильтры UI, привязка данных и тумблер v2):**
   - Нормализация `Condition.normalize(condition)` обеспечивает корректную фильтрацию `NEW` / `USED`.
   - Карточки привязаны к корневым полям `min_price`, `sellers_count`, `conditions_available`.
   - Добавлен фильтр по магазину (`shop`) в `best_price_handler`.
   - Тумблер Search Core 2.0 сделан opt-in с персистентностью в `localStorage`.

---

## 3. Анализ реализации S2-E14: Adaptive Discovery

### 3.1. Доменная модель и приоритизация ([`domain/adaptive_discovery.py`](file:///Users/molthun/Documents/kz-price-hunter/domain/adaptive_discovery.py))
- Класс `DiscoveryTask` реализует 5 состояний: `PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED`.
- `SearchPatternGenerator` декомпозирует коммерческий интент на 5 платформ:
  - Google: покупка, каталог магазина, проверка наличия в городе.
  - Kaspi: карточки продавцов на маркетплейсе.
  - 2GIS: локальные ритейлеры в городах РК.
  - Instagram / Telegram: витрины и каналы с доставкой.
  - Регулярные выражения сохраняют казахские буквы алфавита (ә, і, ң, ғ, ү, ұ, қ, ө, һ).
- `AdaptiveDiscoveryEngine.calculate_priority(search_count, success_rate)` корректно масштабирует приоритет:
  - 100 поисков при 0% успеха → приоритет 100.0.
  - 100 поисков при 50% успеха → приоритет 50.0.
  - 100 поисков при 100% успеха → приоритет 0.0 (дефицита нет).

### 3.2. Хранилище и миграции ([`repositories/discovery_task_repo.py`](file:///Users/molthun/Documents/kz-price-hunter/repositories/discovery_task_repo.py), [`repositories/schema_v2.py`](file:///Users/molthun/Documents/kz-price-hunter/repositories/schema_v2.py))
- Таблица `discovery_tasks` создается аддитивно (`IF NOT EXISTS`) с B-tree индексами по статусу, приоритету и составному ключу `(normalized_query, city)`.
- В `seller_candidates` добавлена аддитивная колонка `discovery_task_id`.
- `generate_tasks_from_signals`: дедупликация задач исключает повторный запуск discovery по тому же запросу/городу и динамически обновляет счетчик спроса существующей активной задачи.

### 3.3. REST API ([`web/server.py`](file:///Users/molthun/Documents/kz-price-hunter/web/server.py))
- `GET /api/v2/discovery/tasks` защищен `@require_admin`, валидирует параметры `min_priority` и `limit`.
- `POST /api/v2/discovery/generate` защищен `@require_admin` и CSRF Origin middleware.
- `POST /api/v2/discovery/tasks/{task_id}/resolve` валидирует статусы по белому списку `DiscoveryTaskStatus.ALL` и возвращает 404 для несуществующих задач.

---

## 4. Рекомендации и замечания

### C-E14-01 (P2): Семантическая эквивалентность города в дедупликации (`city == 'Все'` vs `city IS NULL`) — УСТРАНЕНО
В `DiscoveryTaskRepository.find_active_task_for_query` внедрено единое правило для общеказахстанского поиска:
`if city and city != "Все"` ищет конкретный город, иначе `(city IS NULL OR city = 'Все')`.
Покрыто регрессионным тестом `test_deduplication_city_all_and_none_equivalence`.

### C-E14-02 (P2): Атомарность привязки кандидата (`link_candidate`) — УСТРАНЕНО
В `DiscoveryTaskRepository.link_candidate` вызовы обернуты в атомарный `SAVEPOINT link_candidate` с автоматическим `ROLLBACK` при сбое.

### C-E14-03 (P3, архитектурная рекомендация): Интеграция с адаптивным планировщиком
Задачи `DiscoveryTaskStatus.PENDING` готовы к потреблению будущим фоновым планировщиком обходов (этап **S2-E17 / Adaptive Scheduler 2.0**).

---

## 5. Результаты проверок

- `test_adaptive_discovery_v2.py`: **16/16 PASS**.
- Тесты платформы 2.0 (`test_search_ui_v2.py`, `test_search_analytics_v2.py`, `test_search_core_v2.py`, `test_search_privacy_v2.py`): **43/43 PASS**.
- `test_validators_are_used.py`: **1/1 PASS**.
- Полный регрессионный сьют проекта: **1547 PASS, 0 failures, 0 errors**.
- База `prices.db`: **148 738 048 байт, integrity_check = ok**.

---

## 6. Резюме

Этапы **S2-E11, S2-E13, S2-E16 и S2-E14 приняты аудитором Codex**.
Платформа поиска и адаптивного обнаружения готова к следующему шагу дорожной карты:
👉 **S2-E06: Telegram & Social Media Connectors** либо **S2-E15: Adaptive Scheduler 2.0**.
