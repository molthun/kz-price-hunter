# Следующий план: KZ Price Hunter 2.0 — Search Platform

## Статус и порядок перехода

Зафиксировано Codex 25.09.2026 по поручению владельца: «Сейчас пока идет добавление магазинов по окончании переходим к этому плану».

**Сейчас продолжается добавление магазинов. Реализация новой архитектуры ещё не начата.**
После завершения текущей волны этот документ становится главным планом дальнейшего развития;
предыдущие P00–P16 сохраняются как история и источник уже реализованных возможностей.

- Сначала завершить волну источников и записать её завершение в `AGENT_HANDOFF.md`.
- Затем выполнить этап 0: зафиксировать фактический HEAD, версию, количество источников/предложений,
  полноту и типы источников, время обходов, 403/429, размер БД и задержку поиска с методикой измерения.
- Итоговую последовательность в заключении исходного текста принять как рабочий приоритет:
  Seller/Product/Channel/Offer → Seller Identity → Unified Product Index → Search Core 2.0 → новый UI;
  затем Used → Discovery → Social → AI/Vision → Adaptive Scheduler.
  Taxonomy, channel-aware pricing, freshness, provenance и история цен должны быть учтены в зависимостях
  соответствующих задач. Нумерация разделов ниже — тематическая, версии релизов — предварительные.
- До изменения схемы подготовить отдельную карточку миграции: совместимость старого API, перенос ID
  и истории цен, проверка на копии БД, shadow-сравнение, критерии переключения и откат.
- Не повторять уже реализованные AI Router, watches, telemetry и scheduler: сначала сопоставить с кодом,
  затем описать только необходимые расширения. Не считать прежние тесты приёмкой новой модели.
- Исполнение и подхват: Antigravity → Claude → Codex; Codex проводит аудит, а реализацию берёт
  по прямому назначению или передаче разрешённой задачи. Каждую задачу вести в `AGENT_HANDOFF.md`
  с ID `S2-<этап>-<задача>`, исполнителем, областью файлов, проверками и следующим шагом.
- Разработка, независимый аудит и выпуск фиксируются отдельно; переход к плану не означает разрешения
  на автоматический выпуск или изменение прода.

Исходный текст ниже сохранён полностью. Его утверждения о версии, числе магазинов и текущем коде —
контекст автора, а не результаты проверки Codex. Актуальный baseline будет снят после волны магазинов.

## Исходный план владельца

Да. После всей нашей сегодняшней дискуссии старый план уже надо **пересобрать целиком**, а не просто дописать пару пунктов.

Я сверился со свежим `main`: сейчас проект уже на **v5.27.0**, подключено **33 магазина**, последние — Askona и Зоомаркет. В коде всё ещё живёт текущая модель, где UI и API ориентированы в первую очередь на магазины, категории, скидки, а популярные категории жёстко заданы в `server.py`. Значит, переход надо делать постепенно, не ломая текущую работающую систему.

Новый план я бы назвал:

# KZ Price Hunter 2.0 — Search Platform

Новая цель проекта:

> **Поиск товаров, продавцов и способов покупки по Казахстану.**

Скидки, арбитраж, история цен и уведомления становятся не главным назначением, а сильными функциями поверх поисковика.

---

## Этап 0. Закончить текущую волну магазинов

Пока активно добавляются магазины, архитектуру не трогаем.

Для каждого нового источника только обязательный минимум:

- стабильный ID;
- корректный `complete / limited / failed`;
- причина неполноты;
- корректная цена;
- город;
- категория;
- источник;
- контрактные тесты;
- отсутствие удаления старых данных при неполном обходе.

После окончания волны фиксируем baseline:

```text
Количество магазинов
Количество предложений
Полные/неполные источники
Browser-based источники
Feed/API/HTML источники
Среднее время обхода
Количество 403/429
Размер БД
Search latency
```

Это будет точка, от которой строим 2.0.

---

# Этап 1. Новая доменная модель

Это самый важный этап.

Сейчас логика близка к:

```text
Shop → Product → Price
```

Нужно перейти к:

```text
Product
  ↓
Seller
  ↓
Channel
  ↓
Offer
```

### Product

То, что человек ищет:

```text
Apple iPhone 16 Pro 256GB
RTX 5090
Nike Air Max 270
Сет Филадельфия
```

### Seller

Реальный продавец:

```text
4mobile
DNS
конкретный ресторан
локальный Instagram-магазин
частный продавец
```

### Channel

Где этот продавец продаёт:

```text
direct
website
physical_store
Kaspi
Forte
Halyk
Telegram
Instagram
WhatsApp
classifieds
food_aggregator
```

### Offer

Конкретное предложение:

```text
seller_id
product_id
channel_id

price
old_price
condition
availability
city
payment_methods
delivery
warranty
published_at
observed_at
source_url
```

---

# Этап 2. Seller Identity

Новая фундаментальная подсистема.

Нам надо понимать:

> 4mobile напрямую  
> 4mobile на Kaspi  
> 4mobile на Forte  

— это **один продавец**, а не три магазина.

Добавляем:

```text
seller
seller_identity
seller_channel
seller_source
```

Identity может содержать:

- normalized name;
- телефон;
- домен;
- адрес;
- публичный БИН, если доступен и уместен;
- Instagram;
- Telegram;
- WhatsApp;
- marketplace seller ID;
- другие устойчивые идентификаторы.

### Matching продавцов

Сначала детерминированно:

```text
same phone
same domain
same marketplace seller id
same address + similar name
cross-linked social accounts
```

Потом AI только для сомнительных случаев.

Обязательно:

```text
AUTO_MATCH
REVIEW
REJECT
```

Нельзя автоматически объединять продавцов только потому, что названия похожи.

### Тесты

Golden dataset:

```text
4mobile direct == 4mobile marketplace       TRUE
DNS == DNS другой город                     TRUE
Apple Store Kazakhstan == Apple reseller    FALSE
Два "Мир мебели" с разными телефонами       FALSE
```

---

# Этап 3. Channel-aware pricing

Это заменяет нынешнюю упрощённую идею арбитража.

Нужно различать:

### Cross-seller

Один товар дешевле у другого продавца.

### Cross-channel

Один и тот же продавец продаёт:

```text
напрямую       499 000
Kaspi          539 000
Forte          535 000
```

Это не межмагазинный арбитраж.

Это:

> **другой способ покупки у того же продавца**

Добавляем в Offer:

```text
payment_method
installment_available
credit_available
installment_months
delivery_type
pickup
```

Не предполагаем способ оплаты только по площадке — сохраняем фактические данные.

### UI

Карточка:

```text
4mobile
от 499 000 ₸

Напрямую      499 000 ₸
Kaspi         539 000 ₸
Forte         535 000 ₸
```

---

# Этап 4. Condition / б/у рынок

Добавляем:

```text
NEW
USED
REFURBISHED
OPEN_BOX
UNKNOWN
```

Б/у не смешивается с новыми товарами в одной «лучшей цене».

Поиск:

```text
Новое
Б/у
Все
```

Пример:

```text
MacBook Air M5 16/512

Новые
от 589 000 ₸

Б/у
от 430 000 ₸
```

### Фильтры

- состояние;
- город;
- продавец / частник;
- цена;
- дата публикации;
- наличие;
- источник.

---

# Этап 5. Seller Discovery

Задача:

> найти реальные магазины, которых у нас ещё нет.

Источники discovery:

```text
поисковые системы
2GIS/карты — при допустимом способе доступа
marketplace sellers
сайты
Instagram profiles
Telegram channels
seller self-registration
```

Получаем не магазин сразу, а:

```text
SELLER_CANDIDATE
```

Статусы:

```text
DISCOVERED
PROFILED
IDENTITY_MATCHED
CATALOG_FOUND
REVIEW_REQUIRED
APPROVED
INDEXED
REJECTED
```

---

# Этап 6. Seller Graph

У продавца может быть:

```text
Seller: XXX

2GIS
Instagram
Telegram
WhatsApp
Website
Kaspi
Forte
Halyk
```

Все они связаны с одним `seller_id`.

Очень важно:

**Source != Seller.**

И ещё:

**Channel != Source.**

Например сайт может одновременно быть:

```text
IDENTITY
CATALOG
PRICE
AVAILABILITY
ORDER
```

Instagram:

```text
NEWS
CATALOG
PRICE
```

WhatsApp:

```text
CONTACT
ORDER
```

---

# Этап 7. Source Profiler

После обнаружения продавца система определяет:

> где у него реально лежит каталог?

Порядок предпочтения:

```text
1. Feed / YML / documented API
2. Structured JSON / catalog endpoint
3. Structured website
4. HTML catalog
5. Telegram text
6. Instagram text
7. Instagram images/stories
```

То есть AI/Vision — **последний вариант**, а не первый.

Пример 4mobile:

```text
Instagram → advertising
WhatsApp  → contact
price page → PRIMARY CATALOG SOURCE
```

---

# Этап 8. Social Content Intelligence

Для продавцов, у которых каталог действительно живёт в соцсетях.

Не сразу Offer.

Сначала:

```text
ContentObservation
```

Пример:

```text
seller_id
source
content_type
post_id
published_at
observed_at
text
media_refs
```

`content_type`:

```text
POST
STORY
HIGHLIGHT
REEL
MESSAGE
```

Потом Extraction:

```text
product_candidate
price
old_price
condition
availability
sizes
quantity
unit
promotion
valid_until
confidence
```

И только потом Offer.

---

# Этап 9. Stories / short-lived offers

Stories должны иметь отдельный TTL.

Например:

```text
еда             несколько часов / 1 день
акция           до valid_until
одежда          несколько дней
электроника     до перепроверки
```

Цена из story:

```text
price_source = instagram_story
observed_at = ...
expires_at = ...
```

После expiration:

- товар может остаться;
- цена становится stale;
- в сравнении актуальных цен больше не участвует.

---

# Этап 10. Vision extraction

Только там, где цена действительно находится на изображении.

Pipeline:

```text
Image
 ↓
Vision/OCR
 ↓
structured candidate
 ↓
validation
 ↓
product matching
 ↓
Offer
```

Пример:

```text
IPHONE 15 PRO
256GB
399 990 ₸
```

Внутренне сохраняем confidence.

Низкая уверенность:

> не публикуем цену как достоверную.

Можно показать:

```text
Цена не определена
```

### Обязательные тесты

- одна цена / один товар;
- пять товаров на одной картинке;
- зачёркнутая старая цена;
- скидочная цена;
- цена частично закрыта;
- тенге/рубли/доллары;
- цена за кг;
- размер/объём рядом с ценой.

---

# Этап 11. Unified Product Index

Вот здесь локальная база становится ядром.

Search Core работает не по сайтам в реальном времени, а по:

```text
Product Index
```

Состав:

```text
Products
Sellers
Channels
Offers
Price History
Freshness
Search Index
```

Поиск пользователя:

```text
RTX 5090
```

НЕ:

```text
запросить 50 сайтов
```

а:

```text
локальный индекс
```

Live refresh используется только как дополнительная возможность.

---

# Этап 12. Search Core 2.0

Центральный функционал нового продукта.

Запрос:

```text
айфон 16 про 256 бу астана
```

Parser:

```text
product = iPhone 16 Pro
storage = 256GB
condition = USED
city = Astana
```

Дальше:

```text
deterministic normalization
↓
FTS/search index
↓
matching/ranking
↓
AI fallback
```

AI не должен быть необходим для каждого поиска.

---

# Этап 13. Search Quality

Вводим главный KPI:

## Search Success Rate

Результат запроса:

```text
FOUND
WEAK_MATCH
NOT_FOUND
ERROR
```

Например:

`RTX 5090`

если система показывает:

```text
RTX 5070
кабель питания
водоблок
```

это **не FOUND**.

Это WEAK/NOT_FOUND.

### Аналитика

Храним обезличенно:

```text
normalized_query
city
time_bucket
result_count
good_match_count
outcome
```

Не требуется связывать это с Telegram ID или человеком.

---

# Этап 14. Adaptive Discovery

Вот здесь наша сегодняшняя идея становится особенно сильной.

Система видит:

```text
"суши сет астана"
850 поисков
Search Success = 42%
```

Это сигнал:

> данных не хватает.

Discovery начинает искать:

- рестораны;
- Instagram;
- Telegram;
- сайты;
- marketplace listings.

А если:

```text
iPhone 16 Pro
Search Success = 99.4%
```

нет смысла тратить ресурсы на поиск ещё сотен источников.

---

# Этап 15. Adaptive Scheduler 2.0

Scheduler получает новые сигналы:

```text
search demand
search failures
offer freshness
active watches
source cost
HTTP failures
catalog completeness
price activity
source importance
```

Решает:

> что обновлять сейчас?

Не просто:

> прошло 6 часов — сканировать всё.

---

# Этап 16. Новый сайт

Только после создания новой модели.

Главная перестаёт быть dashboard скидок.

Будет примерно:

```text
KZ Price Hunter

Найди где купить в Казахстане

[ Что хотите найти?                     ]

📍 Астана

[Все] [Новое] [Б/у]

Категории

Электроника
Одежда
Дом
Детское
Авто
Красота
Еда
Спорт
Стройка
Животные
...
```

Навигация:

```text
Поиск
Категории
Скидки
Б/у
Мои наблюдения
```

---

# Этап 17. Category Architecture

Нынешний `POPULAR_CATEGORIES` в `server.py` уже явно станет недостаточен.

Нужна иерархия:

```text
Electronics
 ├ Phones
 ├ Computers
 ├ Components
 └ ...

Clothing
Home
Food
Kids
Auto
Beauty
Construction
Pets
Sport
...
```

У категории:

```text
id
parent_id
name
aliases
attributes_schema
icon
search_priority
```

Магазинные категории маппятся на master taxonomy.

---

# Этап 18. Результаты поиска

Не просто список Offers.

Сначала группировка по Product Identity:

```text
Apple iPhone 16 Pro 256GB

Новые:
31 предложение
от 579 990 ₸

Б/у:
12 предложений
от 390 000 ₸

12 продавцов
```

Затем Seller grouping.

Чтобы не было:

```text
4mobile
4mobile Kaspi
4mobile Forte
4mobile Halyk
```

как четыре магазина.

---

# Этап 19. Seller page

Отдельная страница продавца:

```text
4mobile

Астана
Физический магазин

Каналы:
Сайт
Instagram
WhatsApp
Kaspi
Forte
...

Товары
Цены
Скидки
```

Если один товар доступен через несколько каналов — показываем вместе.

---

# Этап 20. Telegram Watch V2

Теперь watches становятся намного мощнее.

Например:

```text
Найди RTX 5090
город: Астана
цена <= 900 000
новое + б/у
любые продавцы
```

Или:

```text
Найди детскую коляску
USED
до 120 000
```

Или:

```text
суши сет
до 10 000
сегодня
```

Триггеры:

```text
TARGET_PRICE
PRICE_DROP
NEW_OFFER
NEW_SELLER
BACK_IN_STOCK
NEW_USED_OFFER
BETTER_CHANNEL
BETTER_SELLER
```

---

# Этап 21. AI Router

Единый AI слой:

```text
Gemini
OpenAI
```

Задачи:

```text
QUERY_PARSE
CATEGORY_CLASSIFICATION
PRODUCT_EXTRACTION
SELLER_MATCH
PRODUCT_MATCH
VISION_EXTRACTION
SOURCE_PROFILING
SEARCH_QUALITY
SOURCE_HEALTH
ADMIN_ASSISTANT
USER_CONSULTANT
```

Для каждой задачи:

```text
provider
model
cost
latency
tokens
success
fallback
```

---

# Этап 22. AI Search Assistant

Человек пишет:

> ноутбук для бухгалтера до 400 тысяч

AI превращает это в фильтры и ищет **в нашей базе**.

Не придумывает товары.

Ещё:

> где дешевле купить iPhone 16 Pro, если мне нужна рассрочка?

Search Core возвращает реальные Offers, а AI объясняет.

---

# Этап 23. Monitoring Center

После перехода к новой архитектуре мониторим уже не только магазины.

Нужно четыре уровня:

```text
Seller
Source
Channel
Offer/Data Quality
```

Например:

```text
4mobile
Seller OK

Price page
Healthy
last scan 12 min

Instagram
Discovery only
last check 4h

Kaspi channel
Fresh 19 min
```

---

# Этап 24. Observability

Каждый pipeline получает:

```text
request_id
scan_id
seller_id
source_id
offer_id
```

Event Journal:

```text
10:31 DNS scan complete 18 320
10:29 Seller 4mobile direct price updated
10:28 Instagram source expired 12 story prices
10:26 Search "RTX 5090" NOT_FOUND
10:20 Discovery found 3 new sellers
```

---

# Этап 25. Freshness

Это критично.

Не одна универсальная свежесть.

По Source Type:

```text
Retail API       часы
YML              часы
Website          часы
Instagram post   дни
Story            часы
Used listing     дни
Food             часы
```

Offer:

```text
FRESH
AGING
STALE
EXPIRED
```

---

# Этап 26. Price History 2.0

История должна учитывать:

```text
product
seller
channel
condition
```

Нельзя смешивать:

```text
4mobile direct 499k
4mobile Kaspi 539k
```

в одну ценовую линию.

---

# Этап 27. Self-registration продавцов

Очень перспективная функция:

> Добавить магазин

Продавец указывает:

```text
Название
Город
Сайт
Instagram
Telegram
WhatsApp
Marketplace links
```

Система сама:

```text
profile
source discovery
catalog detection
identity match
```

После moderation → индекс.

---

# Этап 28. Trust / provenance

Не делать надпись:

> Проверенный продавец

только потому, что нашли его в нескольких источниках.

Показывать факты:

```text
Физический адрес найден
Сайт найден
Instagram найден
Marketplace presence
Последняя активность сегодня
```

Для каждого Offer:

```text
source
observed_at
published_at
freshness
```

---

# Этап 29. Local DB evolution

SQLite пока оставляем.

Но перед большой перестройкой создаём repository layer:

```text
ProductRepository
SellerRepository
OfferRepository
SearchRepository
HistoryRepository
DiscoveryRepository
TelemetryRepository
```

Чтобы приложение не зависело везде напрямую от SQLite.

Когда реальные метрики потребуют:

```text
SQLite → PostgreSQL
```

можно будет перейти без переписывания логики.

По текущим старым замерам SQLite ещё имеет хороший запас, поэтому миграцию заранее делать не надо.

---

# Этап 30. Масштабирование

Контрольные точки:

```text
100k offers
500k
1m
5m
10m
```

На каждой:

- search p95;
- DB size;
- write latency;
- scan concurrency;
- price history size;
- backup duration;
- memory;
- startup;
- FTS size.

Только после измерений принимать решение о PostgreSQL / отдельном search engine.

---

# Порядок релизов

Я бы после окончания текущей волны магазинов двигался так:

```text
v5.x
закончить расширение retail sources

v6.0
Domain Model
Seller / Channel / Offer foundations

v6.1
Seller Identity + Seller Graph

v6.2
Channel-aware pricing
Cross-seller / Cross-channel

v6.3
Used products

v6.4
Unified Product Index

v6.5
Search Core 2.0

v6.6
Search Analytics + Search Success

v6.7
New Categories / Taxonomy

v6.8
New Web UI

v6.9
Seller Discovery

v6.10
Source Profiler

v6.11
Telegram / social connectors

v6.12
Content Intelligence

v6.13
Instagram image/stories extraction

v6.14
Adaptive Discovery

v6.15
Telegram Watch V2

v6.16
AI Router

v6.17
Adaptive Scheduler 2.0

v6.18
Monitoring / Observability

v6.19
Seller self-onboarding

v7.0
полноценный KZ Product Search Platform
```

Версии условные — мелкие этапы можно объединять.

---

## Самый важный принцип миграции

Мы **не переписываем существующий KZ Price Hunter с нуля**.

Нынешние 33 scraper'а становятся:

```text
Source adapters
```

Текущие товары постепенно мигрируют в:

```text
Product + Seller + Channel + Offer
```

Старые API некоторое время продолжают работать.

Новый Search Core запускается параллельно.

То есть переход:

```text
Old Core
   +
New Core shadow
   ↓
comparison
   ↓
New Core primary
   ↓
old compatibility removed
```

Без большого одномоментного взрыва.

---

## И порядок внедрения я бы сделал именно такой

**Сейчас:** продолжаем магазины.

**После завершения:** замораживаем архитектурное расширение источников на короткое время и делаем:

**1. Seller/Product/Channel/Offer → 2. Seller Identity → 3. Unified Product Index → 4. Search Core 2.0 → 5. новый UI.**

А уже потом:

**Used → Discovery → Social → AI/Vision → Adaptive Scheduler.**

Потому что если сначала полезть в Instagram и б/у без новой модели данных, мы получим ещё большую кашу, которую потом придётся мигрировать второй раз.

И я бы этот план уже считал **новым главным roadmap проекта**, заменяющим старую идею «сначала полируем агрегатор скидок». Теперь мы строим **поисковик торговли Казахстана**.
