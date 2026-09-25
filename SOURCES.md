# Источники KZ Price Hunter

Дата: 19.09.2026. **Статический инвентарь кода, не акт живой проверки доступности магазинов.** Все 19 зарегистрированных источников включены, независимо от enabled flags.

## Классификация

A — feed/API владельца с документированным назначением; B — публичная schema.org/JSON-LD; C — internal JSON сайта; D — HTML; E — browser automation.

`A*` Shop.kz: код использует YML на домене магазина и называет feed официальным; отдельная документация/разрешение на стороннее использование не проверены. Это не утверждение о партнёрстве или лицензии. `C†` Technodom — embedded internal state в HTML, не отдельный API endpoint. Наличие доступного JSON не означает официальный API.

Для всех источников robots/terms/согласованный лимит владельца и дата последней успешной живой проверки требуют отдельного заполнения. CAPTCHA/anti-bot на сегодняшних ответах не проверялись. CAPTCHA solver, аккаунтный auth и private credentials в адаптерах не найдены. Браузерные cookies/impersonation — указаны ниже; необходимость для каждого домена пока не доказана сравнительным тестом.

## Общий контракт фактического сохранения

Все адаптеры возвращают dict с id, shop, title, category, url, image_url, price, old_price_on_site, city; description есть у части. DB сохраняет title/category/URL/image URL/description/current price и first/min/max/canonical_key/created_at/updated_at/is_active. **old_price_on_site из dict теряется в обоих writer (H12).** availability обычно фильтрует выдачу, но отдельного поля с unknown/in_stock/out_of_stock нет. Currency, GTIN, MPN, seller, исходный external_id отдельно, source_type и retrieved_at не записываются как структурированные поля. updated_at — время записи, не обязательно время наблюдения у продавца.

По умолчанию PagedScraper: максимум 50 страниц, один последовательный поток на scrape через asyncio.to_thread; source-specific overrides ниже. Нет единого domain limiter, jitter или Retry-After. SHOP_CONCURRENCY=4 действует только на scan. Backoff shop_scans при ошибке: 300 с с удвоением до 3600 с; live/details его не учитывают. Session close не унифицирован. Ошибка/partial не должна деактивировать старые товары; реализация complete требует исправлений H08.

Изображения сохраняются ссылками, server image proxy/cache не обнаружен. Browser и Telegram могут получать их с внешнего источника. Description используется в UI и может дополнительно сохраняться через guest product-detail GET для любого магазина. Его отсутствие в конкретном parser не означает отсутствие копирования текста в целом.

## Матрица 36 источников

| Модуль / класс | Тип / механизм | Timeout / pacing | Session, pagination, retry | Поля и ограничения |
|---|---|---|---|---|
| `scrapers/shopkz.py` / `ShopKzScraper` | A* + D; shop.kz: /bitrix/catalog_export/yandex.php; HTML .bx_catalog_item; live /search/ | 90 с feed / 15 с HTML / 10 с live; у legacy HTML нет page delay | HTML cookie BITRIX_SM_CITY=astana; chrome124; без общего retry | YML picture/description/available; HTML image src; id offer; live suffix href отличается; YML город Астана не подтверждается параметром запроса; переход offer URL |
| `scrapers/fourmobile.py` / `FourMobileScraper` | C + D; 4mobile.pages.dev/api/data; fallback window.MOBILE_DATA | 15 с на запрос; весь каталог одним ответом | chrome124; HTTP JSON→HTML fallback; auth нет | description item[3], image item[2]/favicon; hash category+name; наличие по положительной цене; Астана hardcoded; переход wa.me с текстом запроса, не товарная страница |
| `scrapers/halyk.py` / `HalykScraper` | C + D; halykmarket.kz/search-api/products; category resolution через HTML | 20 с API / 15 с homepage/category; 0.5 с между страницами | Session, homepage GET; chrome120; errors→ScanResult; без request retry | id/price/oldprice/picture; location=-2 Алматы; PAGE_SIZE=24; complete по count/total без строгой схемы; description не извлекает; переход /category/... |
| `scrapers/kaspi.py` / `KaspiScraper` | C; kaspi.kz/yml/product-view/pl/results (это JSON, не YML feed) | 30 с; 0.4 с | chrome124; X-KS-City и c; страница API с 0; без retry | id/title/unitSalePrice/unitPrice/stock/previewImages; city code; catalog card без seller identity; description нет; /shop/p/ или search fallback |
| `scrapers/fortemarket.py` / `ForteMarketScraper` | C; apigw.forte.kz/fm/v1/algolia/search/text; storefront market.forte.kz | 15 с category / 10 с live; 0.4 с | Session + browser UA/Origin/Referer; explicit impersonate нет; page с 0; без retry | objectID/Locations.Price/Price/Picture/URL; ParamMap/Param→description; старой цены нет; город только подтверждённый: без цены города — «Казахстан» (5.0.0); 100 товаров на страницу (5.3.1); hits/nbHits; переход /items/... |
| `scrapers/dns.py` / `DNSScraper` | E; www.dns-shop.kz HTML в Playwright Chromium | goto 25 с + selector 12 с; sleep 2+1.5 с/страницу | browser UA, city_path=astana; один page на scrape; общий browser semaphore отсутствует | data-code/data-product/title/current/prev/image; Астана; p=N; configured max_pages; всегда limited при успехе, полный каталог не подтверждает; описание нет |
| `scrapers/technodom.py` / `TechnodomScraper` | C†; www.technodom.kz: встроенный __NEXT_DATA__; не документированный REST API | 30 с; inherited 0.3 с | chrome124; city/ city_id cookies; page=N; без retry | sku/title/price/oldPrice/images/uri; image api.technodom.kz URL; Астана; numeric decoder удаляет десятичную точку; описание нет |
| `scrapers/mechta.py` / `MechtaScraper` | C; www.mechta.kz/api/v3/catalog/products | 15 с API + 10 с homepage; inherited 0.3 с | Session chrome124, device UUID, x-city-code; повтор после 403/422 с новой session | id/prices.finalPrice/basePrice/images/slug; Астана; page/pageSize24; 204/empty→complete; описание нет |
| `scrapers/forcecom.py` / `ForcecomScraper` | D; forcecom.kz HTML Bitrix/Aspro | 15 с; inherited 0.3 с | chrome124; PAGEN_1; 404/нет карточек→UnconfirmedEnd; retry нет | id из /model/; price new/old; image attrs; город Астана / Казахстан (не конкретное подтверждение доставки); описание нет |
| `scrapers/sulpak.py` / `SulpakScraper` | D; www.sulpak.kz HTML + data-* | 15 с; inherited 0.3 с; один retry timeout после 1 с | chrome124; city_id=1/language_id=3; page=N; 404→UnconfirmedEnd | data-code/data-price/title/href/image/old; fallback hash URL; Астана; описание нет |
| `scrapers/alser.py` / `AlserScraper` | D; alser.kz/astana/c/... HTML article.product-card | 15 с; inherited 0.3 с | chrome124; город в URL; page=N; 404/нет карточек→UnconfirmedEnd | slug truncated40 id; title/price/old/img; Астана; описание нет |
| `scrapers/evrika.py` / `EvrikaScraper` | C + D; back.evrika.com/catalog/nur-sultan-astana/... JSON с HTML products | 30 с; inherited 0.3 с | chrome124; verify=False; page=N; retry нет | data-json/HTML tile: id/name/price/image/href; old=0; Астана; fallback цены по всему tile text; description нет |
| `scrapers/moon.py` / `MoonScraper` | D; moon.kz HTML Bitrix/Aspro | 15 с; inherited 0.3 с | chrome124; verify=False; PAGEN_1; 404/нет карточек→UnconfirmedEnd | data-id/slug,title,current/old,image; Астана hardcoded; description нет |
| `scrapers/flip.py` / `FlipScraper` | D; www.flip.kz HTML .new-product a.product | 30 с; 1.0 с | curl без explicit impersonate; page=N; без auth/cookies/retry | prod numeric id; data-available=1; current/old/image; .description добавляется к title, не отдельное поле; регион не подтверждён; ссылка ?prod= |
| `scrapers/tgrad.py` / `TgradScraper` | D; tgrad.kz HTML + data-ga JSON в button | 45 с; 0.5 с | Session chrome124; /page-N/; redirects disabled; 301/302 page>1→complete | id/name/price из data-ga; old/image из HTML; Алматы; description нет; next-link проверяется только N+1 |
| `scrapers/ants.py` / `AntsScraper` | B + D; ants.kz schema.org microdata через SchemaListingScraper | 45 с; 0.4 с | Session chrome124; PAGEN_1; no retry; IN_STOCK_FIRST=True | itemprop name/url/image/price/availability; old HTML; ID /tovar-N или title hash; Алматы; первый OutOfStock завершает обход по предположению порядка |
| `scrapers/itmag.py` / `ItmagScraper` | B + D; itmag.kz schema.org microdata через SchemaListingScraper | 45 с; 0.4 с | Session chrome124; PAGEN_1; no retry; IN_STOCK_FIRST=False | itemprops + old HTML; ID /p/N или title hash; Алматы; description нет; конец по отсутствию next-link |
| `scrapers/ispace.py` / `ISpaceScraper` | D → B; ispace.kz HTML listing→JSON-LD Product на карточке | 45 с; listing 0.3 с; product workers=4 без pause | chrome124; ?page=N; без retry; отдельный ThreadPoolExecutor | sku→id/добавление к title; offers.price/availability/image; Астана hardcoded; old=0; JSON-LD missing и out-of-stock оба None; description нет |
| `scrapers/vkusmart.py` / `VkusmartScraper` | D + B; vkusmart.vmv.kz Bitrix Aspro HTML + Schema.org | 15 с; 0.4 с | Session chrome124; PAGEN_1; признака конца нет → «ограничен» (UnconfirmedEnd), пустая первая страница — ошибка; следующая страница Bitrix повторяет первую (подгрузка скриптом) — фактически собирается только первая страница | id/meta name/price/old_price/img; Астана; описание meta description |
| `scrapers/twelve_months.py` / `TwelveMonthsScraper` | D; 12.kz AdvantShop HTML .products-view-item | 15 с; 0.4 с | Session chrome124; ?page=N; признака конца нет → «ограничен», пустая первая страница — ошибка | id/title/price/old_price/img/sku; Казахстан; описание нет |
| `scrapers/zeta.py` / `ZetaScraper` | A; back.zeta.kz Next.js JSON API /good/list | 15 с; 0.3 с | Session chrome124; page=N&limit=40&isCount=true; **конец доказан** через `resultCount` → complete; без него — не complete | id/name.ru/price/oldPrice/images/article; Казахстан; описание нет |
| `scrapers/komfort.py` / `KomfortScraper` | D; komfort.kz Bitrix Aspro HTML .catalog-block-view__item | 20 с; 0.4 с | Session chrome124; PAGEN_1; признака конца нет → «ограничен» (UnconfirmedEnd), пустая первая страница — ошибка; следующая страница Bitrix повторяет первую (подгрузка скриптом) — фактически собирается только первая страница | id/title/price/old_price/image_url; Алматы; описание нет |
| `scrapers/lemanapro.py` / `LemanaProScraper` | D; lemanapro.kz SSR HTML div[data-qa-product] | 20 с; 0.4 с | Session chrome124; ?page=N; **конец доказан** номером последней страницы в пагинации (например, 348 страниц — при max_pages=15 «ограничен») | id/sku/title/price/old_price/image_url; Казахстан; описание нет |
| `scrapers/arbuz.py` / `ArbuzScraper` | D; arbuz.kz SSR HTML article.product-card | 20 с; 0.4 с | Session chrome124; ?page=N; признака конца нет → «ограничен», пустая первая страница и ошибки HTTP — ошибка (раньше маскировались пустым списком) | id/sku/title/price/old_price/image_url; Алматы; описание нет |
| `scrapers/masterok.py` / `MasterOkScraper` | D + B; masterok.kz Bitrix HTML + Schema.org Product | 20 с; 0.4 с | Session chrome124; ?PAGEN_1=N; seen_ids→complete | id/sku/title/price/old_price/description/image_url; Алматы; описание есть |
| `scrapers/magnum.py` / `MagnumScraper` | A; magnum.kz:1337 Strapi JSON API /api/new-product | 20 с; 0.3 с | Session chrome124; city={slug}&cunt=500{&category=slug}; конец доказан (полный список за 1 запрос) → complete | id/name/final_price/start_price/image/discount_type; Алматы/регионы; описание conditions |
| `scrapers/intertop.py` / `IntertopScraper` | D; intertop.kz SSR HTML .in-product-tile | 20 с; 0.4 с | Session chrome124; ?page=N; retry 5xx; **конец доказан** номером последней страницы в пагинации (pagination_last_page) | id/sku/brand+title/price/old_price/image_url; Алматы / Казахстан; описание нет |
| `scrapers/marwin.py` / `MarwinScraper` | D; marwin.kz SSR HTML (Magento 2) .product-item-info | 20 с; 0.4 с | Session chrome124; ?p=N; retry 5xx; **конец доказан** номером последней страницы в пагинации (pagination_last_page); живой поиск /catalogsearch/result/ | id/sku/title/price/old_price/image_url; Алматы / Казахстан; описание есть |
| `scrapers/iteka.py` / `ITekaScraper` | D; i-teka.kz SSR HTML (Yii2 + Alpine.js) div.rounded-16 | 20 с; 0.4 с | Session chrome124; ?page=N; retry 5xx; **конец доказан** номером последней страницы в пагинации (pagination_last_page с GlossaryTnfull_page); живой поиск /search?query= | id/sku/title/price/old_price/image_url; Астана / регионы; описание нет |
| `scrapers/mebel.py` / `MebelScraper` | D + B; mebel.kz SSR HTML .ProductCardMain + schema.org ItemList | 20 с; 0.4 с | Session chrome124; /page-N; retry 5xx; **конец доказан** номером последней страницы в пагинации (/page-N); живой поиск proxy.mebel.kz/backend/search/ | id/sku/title/price/old_price/image_url; Алматы / Казахстан; описание есть |
| `scrapers/detmir.py` / `DetmirScraper` | C + D; detmir.kz SSR HTML + window.appData JSON + section[data-product-id] | 20 с; 0.4 с | Session chrome124; /page/N/; retry 5xx; **конец доказан** по productsLength в appData и пагинатору; живой поиск /search/results/?qt= | id/sku/title/price/old_price/image_url; Алматы / Казахстан; описание нет |
| `scrapers/askona.py` / `AskonaScraper` | D; askona.kz SSR HTML div.card-v6 | 20 с; 0.4 с | Session chrome124; /page/N/; retry 5xx; **конец доказан** по номерам страниц в .pagination-v3; живой поиск /?digiSearch=true&term= | id/sku/title/price/old_price/image_url; Алматы / Казахстан; описание нет |
| `scrapers/zoomarket.py` / `ZooMarketScraper` | D; zoomarket.kz SSR HTML Bitrix .catalog_item | 20 с; 0.4 с | Session chrome124; ?PAGEN_1=N; retry 5xx; **конец доказан** по номерам страниц в .nums; живой поиск /catalog/?q= | id/sku/title/price/old_price/image_url; Алматы / Казахстан; описание нет |
| `scrapers/planeta.py` / `PlanetaScraper` | D; planeta.kz SSR HTML .unit-item-block | 20 с; 0.4 с | Session chrome124; /ru/site/search/term/{term}/page/{page}/; retry 5xx; **конец доказан** по номерам страниц в ul.pagination li a[data-page] и кнопке next; живой поиск /ru/site/search/?term= | id/sku/title/price/old_price/image_url; Алматы / Казахстан; описание нет |
| `scrapers/kimex.py` / `KimexScraper` | D; kimex.kz SSR HTML a.card[data-entity="item"] | 20 с; 0.4 с | Session chrome124; /page-{page}/; retry 5xx; **конец доказан** по кнопке .btn.js-load-more; живой поиск /search/?q= | id/sku/title/price/old_price/image_url; Алматы / Казахстан; описание нет |
| `scrapers/europharma.py` / `EuropharmaScraper` | D; europharma.kz SSR HTML .card-product | 20 с; 0.4 с | Session chrome124; ?page=N; retry 5xx; **конец доказан** по li.pagination__item.next.disabled; живой поиск /search?q= | id/sku/title/price/old_price/description/image_url; Алматы / Казахстан; описание есть |

## Пути вне основного фонового scan

- `search_engine.search_live_stores`: только Kaspi, Shop.kz, 4mobile, Forte; отдельный hardcoded реестр, собственный TTL-кэш без жёсткого размера. После этапа 1 учитывает enabled_shops; набор включённых live-источников входит в ключ кэша.
- `get_best_price_summary`: после этапа 1 live запускается только явно. Пустая/устаревшая обычная выдача не вызывает live. Вызовы из фоновых tracked categories и страницы описания остаются отдельными от общего scan-плана.
- `web.server.fetch_product_description_live`: GET product URL; для Forte дополнительно search gateway; нет общей URL/domain policy. Для 4mobile product URL — WhatsApp, значит попытка догрузить описание может уйти на wa.me, если description пустое.
- `sync_shopkz_yml_handler`: admin маршрут запускает общий scan Shop.kz, не отдельную безопасную схему транспорта.
- `tracked_categories`: пользовательский live-поиск добавляет запросы в постоянные фоновые волны.
- DNS: в коде нет альтернативного JSON/feed path и нет stealth plugin. Гарантированная замена браузера не установлена; оставить E до сопоставления полей на публичном источнике без обхода блокировок. Проверить в будущей canary сессию/timeout/cancellation/закрытие browser.
- Halyk: homepage GET создаёт обычную session; наличие комментария DDoS-Guard само по себе не доказывает обход защиты. Код не решает CAPTCHA. Нейтральный комментарий допустим; реальный transport сейчас не менять.

## Полный список настроенных категорий

Список извлечён из AST config.py без выполнения приложения. Лимиты обозначают конфигурацию, а не измеренный фактический охват. `max_pages=None` для PagedScraper означает default 50; Shop.kz YML и 4mobile этот лимит не используют; DNS имеет собственную логику. Категории `master=all` попадают в каждую подходящую волну.

### ShopKzScraper — 1 настроенных источников категории

| Категория | Master | URL/query | max_pages |
|---|---|---|---|
| Белый Ветер: 📦 Официальная YML выгрузка | all | `https://shop.kz/bitrix/catalog_export/yandex.php` | 1 |

### FourMobileScraper — 1 настроенных источников категории

| Категория | Master | URL/query | max_pages |
|---|---|---|---|
| 4mobile: Все товары | all | `https://4mobile.pages.dev/api/data` | не задан |

### HalykScraper — 8 настроенных источников категории

| Категория | Master | URL/query | max_pages |
|---|---|---|---|
| Halyk: Смартфоны | smartphones | `https://halykmarket.kz/category/smartfony` | 50 |
| Halyk: Ноутбуки | laptops | `https://halykmarket.kz/category/noutbuki` | 50 |
| Halyk: Телевизоры | tvs | `https://halykmarket.kz/category/televizori` | 50 |
| Halyk: Наушники | audio | `https://halykmarket.kz/category/naushniki` | 50 |
| Halyk: Планшеты | tablets_watches | `https://halykmarket.kz/category/plansheti` | 50 |
| Halyk: Смарт-часы | tablets_watches | `https://halykmarket.kz/category/smart-chasi` | 50 |
| Halyk: Игровые приставки | consoles | `https://halykmarket.kz/category/igrovie-pristavki` | 50 |
| Halyk: Мониторы | monitors | `https://halykmarket.kz/category/monitori` | 50 |

### KaspiScraper — 19 настроенных источников категории

| Категория | Master | URL/query | max_pages |
|---|---|---|---|
| Kaspi: 📱 Смартфоны | smartphones | `https://kaspi.kz/shop/c/smartphones/` | 30 |
| Kaspi: 💻 Ноутбуки | laptops | `https://kaspi.kz/shop/c/notebooks/` | 30 |
| Kaspi: ⌚️ Смарт-часы | tablets_watches | `https://kaspi.kz/shop/c/smart%20watches/` | 30 |
| Kaspi: 🎧 Наушники | audio | `https://kaspi.kz/shop/c/headphones/` | 30 |
| Kaspi: 📱 Планшеты | tablets_watches | `https://kaspi.kz/shop/c/tablets/` | 30 |
| Kaspi: 🖥 Мониторы | monitors | `https://kaspi.kz/shop/c/monitors/` | 30 |
| Kaspi: 🎮 Видеокарты | pc_components | `https://kaspi.kz/shop/c/videocards/` | 30 |
| Kaspi: ⚙️ Процессоры | pc_components | `https://kaspi.kz/shop/c/cpus/` | 30 |
| Kaspi: 🔌 Материнские платы | pc_components | `https://kaspi.kz/shop/c/motherboards/` | 30 |
| Kaspi: 🎮 Игровые приставки | consoles | `https://kaspi.kz/shop/c/game%20consoles/` | 30 |
| Kaspi: 📺 Телевизоры | tvs | `https://kaspi.kz/shop/c/tvs/` | 30 |
| Kaspi: ❄️ Холодильники | appliances_large | `https://kaspi.kz/shop/c/refrigerators/` | 30 |
| Kaspi: 🧺 Стиральные машины | appliances_large | `https://kaspi.kz/shop/c/washers/` | 30 |
| Kaspi: 🧹 Пылесосы | appliances_small | `https://kaspi.kz/shop/c/vacuum%20cleaners/` | 30 |
| Kaspi: 🤖 Роботы-пылесосы | appliances_small | `https://kaspi.kz/shop/c/robot%20vacuum%20cleaners/` | 30 |
| Kaspi: ☕️ Кофемашины | appliances_small | `https://kaspi.kz/shop/c/coffee%20machines%20and%20coffee%20makers/` | 30 |
| Kaspi: 💨 Кондиционеры | appliances_large | `https://kaspi.kz/shop/c/air%20conditioners/` | 30 |
| Kaspi: 🖨 Принтеры и МФУ | office_network | `https://kaspi.kz/shop/c/mf%20printers/` | 30 |
| Kaspi: 📽 Проекторы | tvs | `https://kaspi.kz/shop/c/video%20projectors/` | 30 |

### ForteMarketScraper — 14 настроенных источников категории

Фасеты сверены с API 19.09.2026; `max_pages` — страницы по 100 товаров (глубина пагинации не ограничена, «Смартфоны» — 9 733 товара).

| Категория | Master | URL/query | max_pages |
|---|---|---|---|
| Forte: 📱 Смартфоны | smartphones | `https://market.forte.kz/catalog?facet=CategoryMap.Lvl3:Смартфоны` | 120 |
| Forte: 💻 Ноутбуки | laptops | `https://market.forte.kz/catalog?facet=CategoryMap.Lvl2:Ноутбуки и ультрабуки` | 60 |
| Forte: 🖥 Мониторы | monitors | `https://market.forte.kz/catalog?facet=CategoryMap.Lvl3:Мониторы` | 30 |
| Forte: 🎧 Наушники и гарнитуры | audio | `https://market.forte.kz/catalog?facet=CategoryMap.Lvl3:Наушники и гарнитуры` | 60 |
| Forte: 📱 Планшеты | tablets_watches | `https://market.forte.kz/catalog?facet=CategoryMap.Lvl3:Планшеты` | 30 |
| Forte: ⌚️ Смарт-часы | tablets_watches | `https://market.forte.kz/catalog?facet=CategoryMap.Lvl3:Смарт-часы и браслеты` | 40 |
| Forte: 📺 Телевизоры | tvs | `https://market.forte.kz/catalog?facet=CategoryMap.Lvl3:Телевизоры` | 20 |
| Forte: 🎮 Игровые приставки | consoles | `https://market.forte.kz/catalog?facet=CategoryMap.Lvl3:Игровые консоли` | 20 |
| Forte: 🧹 Пылесосы | appliances_small | `https://market.forte.kz/catalog?facet=CategoryMap.Lvl3:Пылесосы` | 30 |
| Forte: ❄️ Холодильники | appliances_large | `https://market.forte.kz/catalog?facet=CategoryMap.Lvl3:Холодильники` | 20 |
| Forte: 🧺 Стиральные машины | appliances_large | `https://market.forte.kz/catalog?facet=CategoryMap.Lvl3:Стиральные машины` | 20 |
| Forte: 🚗 Автотовары | all | `https://market.forte.kz/catalog?facet=CategoryMap.Lvl1:Автотовары` | 5 |
| Forte: 🛠 Строительство и ремонт | diy | `https://market.forte.kz/catalog?facet=CategoryMap.Lvl1:Строительство и ремонт` | 5 |
| Forte: 🏡 Товары для дома и дачи | all | `https://market.forte.kz/catalog?facet=CategoryMap.Lvl1:Товары для дома и дачи` | 5 |

### DNSScraper — 13 настроенных источников категории

Со второй страницы категории DNS отвечает 403 с проверкой Cloudflare на бота (19.09.2026). Защиту не обходим: берётся только первая страница (~24 товара на категорию), обход помечается «ограничен». Полный каталог — только через официальную выгрузку/API магазина.

| Категория | Master | URL/query | max_pages |
|---|---|---|---|
| DNS: 🔥 Все акции и распродажи | actions | `https://www.dns-shop.kz/catalog/actions/` | 1 |
| DNS: 💻 Ноутбуки | laptops | `https://www.dns-shop.kz/catalog/17a892f816404e77/noutbuki/` | 1 |
| DNS: 📱 Смартфоны | smartphones | `https://www.dns-shop.kz/catalog/17a8a01d16404e77/smartfony/` | 1 |
| DNS: 🎮 Видеокарты | pc_components | `https://www.dns-shop.kz/catalog/17a89aab16404e77/videokarty/` | 1 |
| DNS: ⚙️ Процессоры | pc_components | `https://www.dns-shop.kz/catalog/17a899cd16404e77/processory/` | 1 |
| DNS: 🖥 Мониторы | monitors | `https://www.dns-shop.kz/catalog/17a8943716404e77/monitory/` | 1 |
| DNS: 📺 Телевизоры | tvs | `https://www.dns-shop.kz/catalog/17a8ae4916404e77/televizory/` | 1 |
| DNS: 📱 Планшеты | tablets_watches | `https://www.dns-shop.kz/catalog/17a890dc16404e77/planshety/` | 1 |
| DNS: 💾 SSD накопители | pc_components | `https://www.dns-shop.kz/catalog/8a9ddbe317404e77/nakopiteli-ssd/` | 1 |
| DNS: 🧠 Оперативная память | pc_components | `https://www.dns-shop.kz/catalog/17a89a3916404e77/operativnaya-pamyat-dimm/` | 1 |
| DNS: 🎧 Наушники и гарнитуры | audio | `https://www.dns-shop.kz/catalog/17a8f3cd16404e77/naushniki-i-garnitury/` | 1 |
| DNS: ⌚️ Смарт-часы | tablets_watches | `https://www.dns-shop.kz/catalog/17a9e70116404e77/smart-chasy-i-braslety/` | 1 |
| DNS: 🎮 Игровые консоли | consoles | `https://www.dns-shop.kz/catalog/17a8a65f16404e77/igrovye-konsoli/` | 1 |

### TechnodomScraper — 9 настроенных источников категории

| Категория | Master | URL/query | max_pages |
|---|---|---|---|
| Технодом: 💻 Ноутбуки | laptops | `https://www.technodom.kz/catalog/noutbuki-i-komp-jutery/noutbuki/noutbuki` | не задан |
| Технодом: 📱 Смартфоны | smartphones | `https://www.technodom.kz/catalog/smartfony-i-gadzhety/smartfony-i-telefony/smartfony` | не задан |
| Технодом: 📺 Телевизоры | tvs | `https://www.technodom.kz/catalog/tv-audio-foto-video/televizory/led-televizory` | не задан |
| Технодом: 📱 Планшеты | tablets_watches | `https://www.technodom.kz/catalog/smartfony-i-gadzhety/planshety-i-knigi/planshety` | не задан |
| Технодом: ⌚️ Смарт-часы | tablets_watches | `https://www.technodom.kz/catalog/smartfony-i-gadzhety/gadzhety/smart-chasy` | не задан |
| Технодом: 🎧 Наушники | audio | `https://www.technodom.kz/catalog/tv-audio-foto-video/audio-tehnika/naushniki` | не задан |
| Технодом: 🖥 Мониторы | monitors | `https://www.technodom.kz/catalog/noutbuki-i-komp-jutery/komp-jutery-i-monobloki/monitory` | не задан |
| Технодом: 🎮 Игровые приставки | consoles | `https://www.technodom.kz/catalog/noutbuki-i-komp-jutery/igrovye-pristavki-i-igry/igrovye-pristavki` | не задан |
| Технодом: 🧹 Пылесосы | appliances_small | `https://www.technodom.kz/catalog/bytovaja-tehnika/tehnika-dlja-doma/pylesosy` | не задан |

### MechtaScraper — 14 настроенных источников категории

| Категория | Master | URL/query | max_pages |
|---|---|---|---|
| Мечта: 💻 Ноутбуки | laptops | `https://www.mechta.kz/section/noutbuki/` | не задан |
| Мечта: 📱 Смартфоны | smartphones | `https://www.mechta.kz/section/smartfony/` | не задан |
| Мечта: 📺 Телевизоры | tvs | `https://www.mechta.kz/section/televizory/` | не задан |
| Мечта: 🖥 Мониторы | monitors | `https://www.mechta.kz/section/monitory/` | не задан |
| Мечта: 📱 Планшеты | tablets_watches | `https://www.mechta.kz/section/planshety/` | не задан |
| Мечта: ⌚️ Смарт-часы | tablets_watches | `https://www.mechta.kz/section/smart-chasy/` | не задан |
| Мечта: 🎧 Наушники | audio | `https://www.mechta.kz/section/naushniki/` | не задан |
| Мечта: 🎮 Игровые приставки | consoles | `https://www.mechta.kz/section/igrovye-pristavki/` | не задан |
| Мечта: 🧹 Пылесосы | appliances_small | `https://www.mechta.kz/section/pylesosy/` | не задан |
| Мечта: ❄️ Холодильники | appliances_large | `https://www.mechta.kz/section/holodilniki/` | не задан |
| Мечта: 🧺 Стиральные машины | appliances_large | `https://www.mechta.kz/section/stiralnye-mashiny/` | не задан |
| Мечта: ☕️ Кофемашины | appliances_small | `https://www.mechta.kz/section/kofemashiny/` | не задан |
| Мечта: 🌬 Кондиционеры | appliances_large | `https://www.mechta.kz/section/kondicionery/` | не задан |
| Мечта: 💨 Утюги и отпариватели | appliances_small | `https://www.mechta.kz/section/utyugi/` | не задан |

### ForcecomScraper — 14 настроенных источников категории

| Категория | Master | URL/query | max_pages |
|---|---|---|---|
| Forcecom: 🔥 Распродажа | actions | `https://forcecom.kz/sale/rasprodazha/` | не задан |
| Forcecom: 💻 Ноутбуки | laptops | `https://forcecom.kz/catalog/laptops/` | не задан |
| Forcecom: 🎮 Видеокарты | pc_components | `https://forcecom.kz/catalog/graphics-cards/` | не задан |
| Forcecom: 🧩 Материнские платы | pc_components | `https://forcecom.kz/catalog/motherboards/` | не задан |
| Forcecom: 🧠 Оперативная память | pc_components | `https://forcecom.kz/catalog/ram/` | не задан |
| Forcecom: 🖥 Мониторы | monitors | `https://forcecom.kz/catalog/monitors/` | не задан |
| Forcecom: 💾 SSD диски | pc_components | `https://forcecom.kz/catalog/ssd/` | не задан |
| Forcecom: 💾 Жесткие диски | pc_components | `https://forcecom.kz/catalog/hdd/` | не задан |
| Forcecom: 📦 Корпуса | pc_components | `https://forcecom.kz/catalog/cases/` | не задан |
| Forcecom: 🎧 Наушники | audio | `https://forcecom.kz/catalog/headphones/` | не задан |
| Forcecom: ⌨️ Клавиатуры | pc_components | `https://forcecom.kz/catalog/keyboards/` | не задан |
| Forcecom: 🖱 Мыши | pc_components | `https://forcecom.kz/catalog/mice/` | не задан |
| Forcecom: 🖨 Принтеры | office_network | `https://forcecom.kz/catalog/printers/` | не задан |
| Forcecom: 🌐 Роутеры | office_network | `https://forcecom.kz/catalog/marshrutizatory/` | не задан |

### SulpakScraper — 13 настроенных источников категории

| Категория | Master | URL/query | max_pages |
|---|---|---|---|
| Sulpak: 💻 Ноутбуки | laptops | `https://www.sulpak.kz/f/noutbuki` | не задан |
| Sulpak: 📱 Смартфоны | smartphones | `https://www.sulpak.kz/f/smartfoniy/` | не задан |
| Sulpak: 📺 Телевизоры | tvs | `https://www.sulpak.kz/f/led_oled_televizoriy` | не задан |
| Sulpak: 📱 Планшеты | tablets_watches | `https://www.sulpak.kz/f/planshetiy` | не задан |
| Sulpak: ⌚️ Смарт-часы | tablets_watches | `https://www.sulpak.kz/f/smart_chasiy` | не задан |
| Sulpak: 🎧 Наушники | audio | `https://www.sulpak.kz/f/naushniki` | не задан |
| Sulpak: 🎮 Игровые приставки | consoles | `https://www.sulpak.kz/f/igroviye_pristavki` | не задан |
| Sulpak: 🧺 Стиральные машины | appliances_large | `https://www.sulpak.kz/f/stiralniye_mashiniy` | не задан |
| Sulpak: ❄️ Холодильники | appliances_large | `https://www.sulpak.kz/f/holodilniki` | не задан |
| Sulpak: 🌬 Кондиционеры | appliances_large | `https://www.sulpak.kz/f/kondicioneriy` | не задан |
| Sulpak: ☕️ Кофемашины | appliances_small | `https://www.sulpak.kz/f/kofemashiniy` | не задан |
| Sulpak: ♨️ Микроволновые печи | appliances_small | `https://www.sulpak.kz/f/mikrovolnoviye_pechi` | не задан |
| Sulpak: 🔥 Распродажа | actions | `https://www.sulpak.kz/sale/1` | не задан |

### AlserScraper — 8 настроенных источников категории

| Категория | Master | URL/query | max_pages |
|---|---|---|---|
| Alser: 💻 Ноутбуки | laptops | `https://alser.kz/astana/c/noutbuki` | не задан |
| Alser: 📱 Смартфоны | smartphones | `https://alser.kz/astana/c/smartfony` | не задан |
| Alser: 📺 Телевизоры | tvs | `https://alser.kz/astana/c/televizory` | не задан |
| Alser: 🖥 Мониторы | monitors | `https://alser.kz/astana/c/monitory` | не задан |
| Alser: 📱 Планшеты | tablets_watches | `https://alser.kz/astana/c/planshety` | не задан |
| Alser: 🧹 Пылесосы | appliances_small | `https://alser.kz/astana/c/pylesosy` | не задан |
| Alser: ❄️ Холодильники | appliances_large | `https://alser.kz/astana/c/vse-holodilniki` | не задан |
| Alser: 🌬 Кондиционеры | appliances_large | `https://alser.kz/astana/c/vse-kondicioneri` | не задан |

### EvrikaScraper — 9 настроенных источников категории

| Категория | Master | URL/query | max_pages |
|---|---|---|---|
| Эврика: 💻 Ноутбуки | laptops | `https://evrika.com/catalog/nur-sultan-astana/noutbuki/c207` | не задан |
| Эврика: 📱 Смартфоны | smartphones | `https://evrika.com/catalog/nur-sultan-astana/smartfony/c234` | не задан |
| Эврика: 📺 Телевизоры | tvs | `https://evrika.com/catalog/nur-sultan-astana/led-televizory/c228` | не задан |
| Эврика: 🖥 Мониторы | monitors | `https://evrika.com/catalog/nur-sultan-astana/monitory/c300` | не задан |
| Эврика: 📱 Планшеты | tablets_watches | `https://evrika.com/catalog/nur-sultan-astana/planshety/c70` | не задан |
| Эврика: 🎧 Наушники | audio | `https://evrika.com/catalog/nur-sultan-astana/naushniki-1/c183` | не задан |
| Эврика: 🎮 Игровые приставки | consoles | `https://evrika.com/catalog/nur-sultan-astana/igrovye-pristavki/c120` | не задан |
| Эврика: 🖨 Принтеры | office_network | `https://evrika.com/catalog/nur-sultan-astana/printery/c65` | не задан |
| Эврика: 💨 Утюги | appliances_small | `https://evrika.com/catalog/nur-sultan-astana/utyugi/c161` | не задан |

### MoonScraper — 11 настроенных источников категории

| Категория | Master | URL/query | max_pages |
|---|---|---|---|
| Moon: 💻 Ноутбуки | laptops | `https://moon.kz/noutbuki-i-aksessuary/` | не задан |
| Moon: 🎮 Видеокарты | pc_components | `https://moon.kz/videokarty/` | не задан |
| Moon: ⚙️ Процессоры | pc_components | `https://moon.kz/protsessory/` | не задан |
| Moon: 🖥 Мониторы | monitors | `https://moon.kz/monitory/` | не задан |
| Moon: 🧩 Материнские платы | pc_components | `https://moon.kz/materinskie-platy/` | не задан |
| Moon: 🧠 Оперативная память | pc_components | `https://moon.kz/moduli-pamyati/` | не задан |
| Moon: 💾 SSD диски | pc_components | `https://moon.kz/nakopiteli-ssd/` | не задан |
| Moon: ⚡️ Блоки питания | pc_components | `https://moon.kz/bloki-pitaniya/` | не задан |
| Moon: 📦 Корпуса | pc_components | `https://moon.kz/korpusa/` | не задан |
| Moon: ❄️ Системы охлаждения | pc_components | `https://moon.kz/kulery-i-sistemy-okhlazhdeniya/` | не задан |
| Moon: 🔥 Распродажа | actions | `https://moon.kz/rasprodazha/` | не задан |

### FlipScraper — 1 настроенных источников категории

| Категория | Master | URL/query | max_pages |
|---|---|---|---|
| Flip: Электроника | all | `https://www.flip.kz/catalog?subsection=5319` | 50 |

### TgradScraper — 28 настроенных источников категории

| Категория | Master | URL/query | max_pages |
|---|---|---|---|
| Tgrad: 📺 Телевизоры | tvs | `https://tgrad.kz/televizory/` | не задан |
| Tgrad: 🔊 Портативная акустика | audio | `https://tgrad.kz/portativnaya-akustika/` | не задан |
| Tgrad: 🔈 Акустические системы | audio | `https://tgrad.kz/akusticheskie-sistemy/` | не задан |
| Tgrad: 📱 Смартфоны | smartphones | `https://tgrad.kz/smartfony/` | не задан |
| Tgrad: ⌚️ Смарт-часы | tablets_watches | `https://tgrad.kz/umnye-chasy/` | не задан |
| Tgrad: 📱 Планшеты | tablets_watches | `https://tgrad.kz/planshety/` | не задан |
| Tgrad: 🎧 Беспроводные наушники | audio | `https://tgrad.kz/besprovodnye-naushniki/` | не задан |
| Tgrad: 💻 Ноутбуки | laptops | `https://tgrad.kz/noutbuki/` | не задан |
| Tgrad: 🖥 Мониторы | monitors | `https://tgrad.kz/monitory/` | не задан |
| Tgrad: 🎧 Игровые наушники | audio | `https://tgrad.kz/igrovye-naushniki/` | не задан |
| Tgrad: 📡 Роутеры | office_network | `https://tgrad.kz/routery/` | не задан |
| Tgrad: 🧹 Пылесосы | appliances_small | `https://tgrad.kz/pylesosy/` | не задан |
| Tgrad: 🧹 Вертикальные пылесосы | appliances_small | `https://tgrad.kz/vertikalnye-pylesosy/` | не задан |
| Tgrad: 🤖 Роботы-пылесосы | appliances_small | `https://tgrad.kz/roboty-pylesosy/` | не задан |
| Tgrad: 👕 Утюги | appliances_small | `https://tgrad.kz/utyugi/` | не задан |
| Tgrad: 💨 Отпариватели | appliances_small | `https://tgrad.kz/otparivateli/` | не задан |
| Tgrad: 🚿 Водонагреватели | appliances_large | `https://tgrad.kz/vodonagrevateli/` | не задан |
| Tgrad: ❄️ Кондиционеры | appliances_large | `https://tgrad.kz/konditsionery/` | не задан |
| Tgrad: 🌬 Воздухоочистители | appliances_large | `https://tgrad.kz/vozdukhoochistiteli/` | не задан |
| Tgrad: 🧺 Стиральные машины | appliances_large | `https://tgrad.kz/stiralnye-mashiny/` | не задан |
| Tgrad: 🧺 Сушильные машины | appliances_large | `https://tgrad.kz/sushilnye-mashiny/` | не задан |
| Tgrad: 🍽 Посудомоечные машины | appliances_large | `https://tgrad.kz/posudomoechnye-mashiny/` | не задан |
| Tgrad: 🔥 Вытяжки | appliances_large | `https://tgrad.kz/vytyazhki/` | не задан |
| Tgrad: 📦 Микроволновые печи | appliances_small | `https://tgrad.kz/mikrovolnovye-pechi/` | не задан |
| Tgrad: 🍲 Мультиварки | appliances_small | `https://tgrad.kz/multivarki/` | не задан |
| Tgrad: 🥤 Блендеры | appliances_small | `https://tgrad.kz/blendery/` | не задан |
| Tgrad: 💇 Фены | appliances_small | `https://tgrad.kz/feny-i-fen-shhetki/` | не задан |
| Tgrad: 🪒 Электробритвы | appliances_small | `https://tgrad.kz/elektrobritvy/` | не задан |

### AntsScraper — 19 настроенных источников категории

| Категория | Master | URL/query | max_pages |
|---|---|---|---|
| ANTS: 💻 Ноутбуки | laptops | `https://ants.kz/catalog/noutbuki/` | 25 |
| ANTS: 🖥 Моноблоки | monitors | `https://ants.kz/catalog/monobloki/` | 25 |
| ANTS: 🖥 Системные блоки | pc_components | `https://ants.kz/catalog/sistemnye-bloki/` | 25 |
| ANTS: ⚙️ Процессоры | pc_components | `https://ants.kz/catalog/protsessory/` | 25 |
| ANTS: 🔌 Материнские платы | pc_components | `https://ants.kz/catalog/materinskie-platy/` | 25 |
| ANTS: 🧠 Оперативная память | pc_components | `https://ants.kz/catalog/operativnaya-pamyat/` | 25 |
| ANTS: 🎮 Видеокарты | pc_components | `https://ants.kz/catalog/videokarty/` | 25 |
| ANTS: 💾 SSD-накопители | pc_components | `https://ants.kz/catalog/ssd-nakopiteli/` | 25 |
| ANTS: 🗄 Корпуса | pc_components | `https://ants.kz/catalog/korpusa-dlya-kompyuterov/` | 25 |
| ANTS: 🖥 Мониторы | monitors | `https://ants.kz/catalog/monitory/` | 25 |
| ANTS: 🎧 Наушники и гарнитуры | audio | `https://ants.kz/catalog/naushniki-i-garnitury/` | 25 |
| ANTS: 📡 Wi-Fi роутеры | office_network | `https://ants.kz/catalog/wi-fi-routery/` | 25 |
| ANTS: 🖨 Принтеры | office_network | `https://ants.kz/catalog/printery/` | 25 |
| ANTS: 🖨 МФУ | office_network | `https://ants.kz/catalog/mnogofunktsionalnye-ustroystva-mfu/` | 25 |
| ANTS: 📽 Проекторы | tvs | `https://ants.kz/catalog/proektory/` | 25 |
| ANTS: 📱 Смартфоны | smartphones | `https://ants.kz/catalog/smartfony/` | 25 |
| ANTS: 📱 Планшеты | tablets_watches | `https://ants.kz/catalog/planshety/` | 25 |
| ANTS: ⌚️ Смарт-часы | tablets_watches | `https://ants.kz/catalog/smart-chasy/` | 25 |
| ANTS: 📺 Телевизоры | tvs | `https://ants.kz/catalog/televizory/` | 25 |

### ItmagScraper — 19 настроенных источников категории

| Категория | Master | URL/query | max_pages |
|---|---|---|---|
| ITMag: 💻 Ноутбуки | laptops | `https://itmag.kz/catalog/noutbuki/` | не задан |
| ITMag: 🖥 Моноблоки | monitors | `https://itmag.kz/catalog/monobloki/` | не задан |
| ITMag: 🖥 Системные блоки | pc_components | `https://itmag.kz/catalog/personal-nye-komp-yutery/` | не задан |
| ITMag: ⚙️ Процессоры | pc_components | `https://itmag.kz/catalog/protsessory/` | не задан |
| ITMag: 🔌 Материнские платы | pc_components | `https://itmag.kz/catalog/materinskie_platy/` | не задан |
| ITMag: 🧠 Оперативная память | pc_components | `https://itmag.kz/catalog/operativnaya_pamyat/` | не задан |
| ITMag: 🎮 Видеокарты | pc_components | `https://itmag.kz/catalog/videokarty/` | не задан |
| ITMag: 💾 Жесткие диски и SSD | pc_components | `https://itmag.kz/catalog/zhestkie_diski/` | не задан |
| ITMag: 🗄 Корпуса | pc_components | `https://itmag.kz/catalog/korpusa/` | не задан |
| ITMag: 🔋 Блоки питания | pc_components | `https://itmag.kz/catalog/bloki-pitaniya-k-korpusam/` | не задан |
| ITMag: 🖥 Мониторы | monitors | `https://itmag.kz/catalog/monitory/` | не задан |
| ITMag: 🎧 Наушники и гарнитуры | audio | `https://itmag.kz/catalog/naushniki-garnitury-i-mikrofony/` | не задан |
| ITMag: 📡 Wi-Fi роутеры | office_network | `https://itmag.kz/catalog/besprovod_marshrutizatory_wifi_routery/` | не задан |
| ITMag: 🖨 Принтеры | office_network | `https://itmag.kz/catalog/printery/` | не задан |
| ITMag: 🖨 МФУ | office_network | `https://itmag.kz/catalog/mnogofunktsionalnye_ustroystva_mfu/` | не задан |
| ITMag: 📽 Проекторы | tvs | `https://itmag.kz/catalog/proektory/` | не задан |
| ITMag: 📱 Смартфоны | smartphones | `https://itmag.kz/catalog/smartfony-i-mobilnye-telefony/` | не задан |
| ITMag: 📱 Планшеты | tablets_watches | `https://itmag.kz/catalog/planshety/` | не задан |
| ITMag: 📺 Телевизоры | tvs | `https://itmag.kz/catalog/televizory/` | не задан |

### ISpaceScraper — 7 настроенных источников категории

| Категория | Master | URL/query | max_pages |
|---|---|---|---|
| iSpace: 💻 Mac | laptops | `https://ispace.kz/category/mac` | не задан |
| iSpace: 📱 iPad | tablets_watches | `https://ispace.kz/category/ipad` | не задан |
| iSpace: 📱 iPhone | smartphones | `https://ispace.kz/category/iphone` | не задан |
| iSpace: ⌚️ Apple Watch | tablets_watches | `https://ispace.kz/category/apple-watch` | не задан |
| iSpace: 🎧 AirPods | audio | `https://ispace.kz/category/apple-airpods` | не задан |
| iSpace: 🎧 Наушники | audio | `https://ispace.kz/category/headsets` | не задан |
| iSpace: 🔊 Колонки | audio | `https://ispace.kz/category/speakers` | не задан |

### TwelveMonthsScraper — 8 настроенных источников категории

| Категория | Master | URL/query | max_pages |
|---|---|---|---|
| 12 Месяцев: 🔌 Электроинструменты | diy | `https://12.kz/categories/elektroinstrumenty` | 15 |
| 12 Месяцев: 🪛 Дрели и шуруповерты | diy | `https://12.kz/categories/dreli-shurupoverty` | 15 |
| 12 Месяцев: 🔨 Перфораторы | diy | `https://12.kz/categories/perforatory` | 10 |
| 12 Месяцев: 🔧 Ручной инструмент | diy | `https://12.kz/categories/ruchnoi-instrument` | 15 |
| 12 Месяцев: 🚰 Смесители и сантехника | diy | `https://12.kz/categories/smesiteli` | 15 |
| 12 Месяцев: 🧑‍🏭 Сварочные аппараты | diy | `https://12.kz/categories/svarochnye-apparaty` | 10 |
| 12 Месяцев: 🌿 Садовая техника | diy | `https://12.kz/categories/sadovaya-tekhnika` | 15 |
| 12 Месяцев: 🧼 Бытовая химия | household | `https://12.kz/categories/bytovaya-khimiya` | 15 |

### ZetaScraper — 8 настроенных источников категории

| Категория | Master | URL/query | max_pages |
|---|---|---|---|
| Zeta: 🪣 Емкости, баки и ведра | household | `6851938995dd04035cad42d6` | 10 |
| Zeta: 👟 Обувницы и подставки | household | `6851938095dd04035cad42c5` | 10 |
| Zeta: 🧥 Вешалки | household | `6851938395dd04035cad42cb` | 10 |
| Zeta: 🪜 Стеллажи и этажерки | household | `6851937e95dd04035cad42c0` | 10 |
| Zeta: 🌿 Садовый инвентарь | diy | `6851939795dd04035cad42f3` | 10 |
| Zeta: 🪜 Стремянки | diy | `6851938d95dd04035cad42de` | 10 |
| Zeta: 🥩 Гриль и мангалы | diy | `6851939d95dd04035cad42fe` | 10 |
| Zeta: 🪑 Мебель для дачи и сада | diy | `6851938895dd04035cad42d5` | 10 |

### KomfortScraper — 10 настроенных источников категории

| Категория | Master | URL/query | max_pages |
|---|---|---|---|
| Комфорт: 🪚 Электроинструменты | diy | `https://komfort.kz/instrumenty/elektroinstrumenty/` | 15 |
| Комфорт: 🪛 Шуруповерты и дрели | diy | `https://komfort.kz/instrumenty/elektroinstrumenty/dreli-shurupoverty/` | 15 |
| Комфорт: 🔨 Перфораторы | diy | `https://komfort.kz/instrumenty/elektroinstrumenty/perforatory/` | 10 |
| Комфорт: 🔧 Ручной инструмент | diy | `https://komfort.kz/instrumenty/ruchnoy-instrument/` | 15 |
| Комфорт: 🚰 Сантехника | diy | `https://komfort.kz/santekhnika/` | 15 |
| Комфорт: 🧑‍🏭 Сварочные аппараты | diy | `https://komfort.kz/instrumenty/silovaya-tekhnika/svarochnoe-oborudovanie/` | 10 |
| Комфорт: 🌿 Садовая техника | diy | `https://komfort.kz/tovary-dlya-sada-i-otdykha/sadovaya-tekhnika/` | 15 |
| Комфорт: 🧼 Бытовая химия | household | `https://komfort.kz/khozyaystvennye-tovary/bytovaya-khimiya/` | 15 |
| Комфорт: 🧹 Уборочный инвентарь | household | `https://komfort.kz/khozyaystvennye-tovary/uborochnyy-inventar/` | 15 |
| Комфорт: ☕️ Мелкая техника для кухни | appliances_small | `https://komfort.kz/bytovaya-tekhnika/melkaya-tekhnika-dlya-kukhni/` | 15 |

### LemanaProScraper — 11 настроенных источников категории

| Категория | Master | URL/query | max_pages |
|---|---|---|---|
| Лемана ПРО: 🔌 Электроинструменты | diy | `https://lemanapro.kz/catalogue/elektroinstrumenty/` | 15 |
| Лемана ПРО: 🪛 Дрели и шуруповерты | diy | `https://lemanapro.kz/catalogue/dreli-shurupoverty/` | 15 |
| Лемана ПРО: 🔨 Перфораторы | diy | `https://lemanapro.kz/catalogue/perforatory/` | 10 |
| Лемана ПРО: 🔧 Ручной инструмент | diy | `https://lemanapro.kz/catalogue/ruchnoy-instrument/` | 15 |
| Лемана ПРО: 🚰 Сантехника | diy | `https://lemanapro.kz/catalogue/santehnika/` | 15 |
| Лемана ПРО: 🧱 Стройматериалы | diy | `https://lemanapro.kz/catalogue/stroymaterialy/` | 15 |
| Лемана ПРО: 🌿 Садовая техника | diy | `https://lemanapro.kz/catalogue/sadovaya-tehnika/` | 15 |
| Лемана ПРО: 🪜 Стремянки | diy | `https://lemanapro.kz/catalogue/stremyanki/` | 10 |
| Лемана ПРО: 🧑‍🏭 Сварочное оборудование | diy | `https://lemanapro.kz/catalogue/svarochnoe-oborudovanie/` | 10 |
| Лемана ПРО: 🧼 Бытовая химия | household | `https://lemanapro.kz/catalogue/bytovaya-himiya/` | 15 |
| Лемана ПРО: 📦 Аксессуары для хранения | household | `https://lemanapro.kz/catalogue/aksessuary-dlya-hraneniya/` | 15 |

### ArbuzScraper — 10 настроенных источников категории

| Категория | Master | URL/query | max_pages |
|---|---|---|---|
| Arbuz: 🧼 Средства для мытья посуды | household | `https://arbuz.kz/ru/almaty/catalog/cat/224494-sredstva_dlya_mytya_posudy` | 15 |
| Arbuz: 🧺 Стирка и уход за бельём | household | `https://arbuz.kz/ru/almaty/catalog/cat/224405-stirka_i_uhod_za_bel_m` | 15 |
| Arbuz: 🧻 Салфетки и туалетная бумага | household | `https://arbuz.kz/ru/almaty/catalog/cat/224493-calfetki_tualetnaya_bumaga` | 15 |
| Arbuz: 🧴 Личная гигиена и косметика | household | `https://arbuz.kz/ru/almaty/catalog/cat/224407-kosmetika_i_sredstva_lichnoi_gigieny` | 15 |
| Arbuz: 🥫 Бакалея | grocery | `https://arbuz.kz/ru/almaty/catalog/cat/225169-bakaleya` | 15 |
| Arbuz: ☕️ Кофе, чай, какао | grocery | `https://arbuz.kz/ru/almaty/catalog/cat/226099-kofe_chai_kakao` | 15 |
| Arbuz: 🧀 Молоко, сыр и яйца | grocery | `https://arbuz.kz/ru/almaty/catalog/cat/225161-moloko_syr_i_yaica` | 15 |
| Arbuz: 🥩 Мясо и птица | grocery | `https://arbuz.kz/ru/almaty/catalog/cat/225162-myaso_i_ptica` | 15 |
| Arbuz: 🍬 Кондитерские изделия | grocery | `https://arbuz.kz/ru/almaty/catalog/cat/225166-konditerskie_izdeliya` | 15 |
| Arbuz: 🧃 Вода и напитки | grocery | `https://arbuz.kz/ru/almaty/catalog/cat/14-voda_i_napitki` | 15 |

### MasterOkScraper — 8 настроенных источников категории

| Категория | Master | URL/query | max_pages |
|---|---|---|---|
| MasterOK: 🔌 Инструменты | diy | `https://masterok.kz/catalog/instrumenty/` | 10 |
| MasterOK: 🧑‍🏭 Сварочное оборудование | diy | `https://masterok.kz/catalog/svarochnoe-oborudovanie1/` | 10 |
| MasterOK: 🌿 Садовое оборудование | diy | `https://masterok.kz/catalog/sadovoe-oborudovanie1/` | 10 |
| MasterOK: 🏗 Строительное оборудование | diy | `https://masterok.kz/catalog/stroitelnoe-oborudovanie1/` | 10 |
| MasterOK: ⚡️ Силовая техника и генераторы | diy | `https://masterok.kz/catalog/silovaya-tekhnika/` | 10 |
| MasterOK: 🚰 Мотопомпы и насосы | diy | `https://masterok.kz/catalog/motopompy1-/` | 10 |
| MasterOK: 🪵 Деревообрабатывающее оборудование | diy | `https://masterok.kz/catalog/derevoobrabatyvayushchee-oborudovanie-1/` | 10 |
| MasterOK: 📦 Складское оборудование | diy | `https://masterok.kz/catalog/skladskoe-oborudovanie-/` | 10 |

### MagnumScraper — 15 настроенных источников категории

| Категория | Master | URL/query | max_pages |
|---|---|---|---|
| Магнум: 🔥 Все акции и скидки | actions | `https://magnum.kz/catalog` | 5 |
| Магнум: 🥫 Бакалея | grocery | `https://magnum.kz/catalog?category=bakaleia` | 5 |
| Магнум: ☕️ Чай, кофе, какао | grocery | `https://magnum.kz/catalog?category=chai-koffee-kakao` | 5 |
| Магнум: 🥛 Молочные продукты | grocery | `https://magnum.kz/catalog?category=molochnye-produkty` | 5 |
| Магнум: 🥩 Мясо и птица | grocery | `https://magnum.kz/catalog?category=myaso` | 5 |
| Магнум: 🍏 Фрукты и овощи | grocery | `https://magnum.kz/catalog?category=frukty-ovoschi` | 5 |
| Магнум: 🧀 Гастрономия | grocery | `https://magnum.kz/catalog?category=gastronomiya` | 5 |
| Магнум: 🍬 Кондитерские изделия | grocery | `https://magnum.kz/catalog?category=konditerskie-izdeliya` | 5 |
| Магнум: 🐟 Консервы | grocery | `https://magnum.kz/catalog?category=konservy` | 5 |
| Магнум: 🧃 Безалкогольные напитки | grocery | `https://magnum.kz/catalog?category=bezalkogolnye-napitki` | 5 |
| Магнум: 🧊 Замороженные продукты | grocery | `https://magnum.kz/catalog?category=zamorojennye-produkty` | 5 |
| Магнум: 🧼 Бытовая химия | household | `https://magnum.kz/catalog?category=bytovaiya-himiya` | 5 |
| Магнум: 🧴 Средства гигиены | household | `https://magnum.kz/catalog?category=sredstva-gigieny` | 5 |
| Магнум: 👶 Детские товары | household | `https://magnum.kz/catalog?category=detskie-tovary` | 5 |
| Магнум: 🍳 Собственное производство | grocery | `https://magnum.kz/catalog?category=sobstvennoe-proizvodstvo` | 5 |

### IntertopScraper — 8 настроенных источников категории

| Категория | Master | URL/query | max_pages |
|---|---|---|---|
| Интертоп: 🔥 Скидки и распродажи | clothes | `https://intertop.kz/catalog/?discount=1` | 10 |
| Интертоп: 👠 Женская обувь | clothes | `https://intertop.kz/catalog/zhenskaya_obuv/` | 10 |
| Интертоп: 👗 Женская одежда | clothes | `https://intertop.kz/catalog/zhenskaya_odezhda/` | 10 |
| Интертоп: 👞 Мужская обувь | clothes | `https://intertop.kz/catalog/muzhskaya_obuv/` | 10 |
| Интертоп: 👔 Мужская одежда | clothes | `https://intertop.kz/catalog/muzhskaya_odezhda/` | 10 |
| Интертоп: 👟 Детская обувь | clothes | `https://intertop.kz/catalog/detskaya_obuv/` | 10 |
| Интертоп: 👕 Детская одежда | clothes | `https://intertop.kz/catalog/detskaya_odezhda/` | 10 |
| Интертоп: 🎒 Аксессуары | clothes | `https://intertop.kz/catalog/aksessuary/` | 10 |

### MarwinScraper — 9 настроенных источников категории

| Категория | Master | URL/query | max_pages |
|---|---|---|---|
| Меломан: 📚 Книги | household | `https://www.marwin.kz/books/` | 10 |
| Меломан: 🎮 Видеоигры и консоли | consoles | `https://www.marwin.kz/videogames/` | 10 |
| Меломан: 🧸 Игрушки и развлечения | household | `https://www.marwin.kz/toys-and-entertainment/` | 10 |
| Меломан: 🧱 Конструкторы LEGO | household | `https://www.marwin.kz/toys-and-entertainment/lego/` | 10 |
| Меломан: 🎲 Настольные игры | household | `https://www.marwin.kz/toys-and-entertainment/nastol-nye-igry/` | 10 |
| Меломан: 🎨 Творчество и хобби | household | `https://www.marwin.kz/tvorchestvo-19692/` | 10 |
| Меломан: ✏️ Школа и канцелярия | office_network | `https://www.marwin.kz/shkola-kancelyariya-19236/` | 10 |
| Меломан: 🎵 Музыка и винил | audio | `https://www.marwin.kz/music/` | 10 |
| Меломан: 🍬 Сладости и подарки | grocery | `https://www.marwin.kz/food-items/` | 10 |

### ITekaScraper — 9 настроенных источников категории

| Категория | Master | URL/query | max_pages |
|---|---|---|---|
| i-Teka: 💊 Все лекарственные препараты | beauty_health | `https://i-teka.kz/astana/medicaments/lekarstvennye-sredstva` | 10 |
| i-Teka: 🩹 Обезболивающие и спазмолитики | beauty_health | `https://i-teka.kz/astana/medicaments/obezbolivayushie-preparaty` | 10 |
| i-Teka: 🛡 Противовирусные и иммунитет | beauty_health | `https://i-teka.kz/astana/medicaments/protivovirusnye-preparaty` | 10 |
| i-Teka: 🔬 Антибиотики и противомикробные | beauty_health | `https://i-teka.kz/astana/medicaments/antibiotiki-i-protivomikrobnye-preparaty` | 10 |
| i-Teka: 🩺 Медицинская техника и приборы | beauty_health | `https://i-teka.kz/astana/medicaments/medicinskaya-tehnika` | 10 |
| i-Teka: 💓 Тонометры и термометры | beauty_health | `https://i-teka.kz/astana/medicaments/tonometry-i-termometry` | 10 |
| i-Teka: 🧼 Гигиена и уход | beauty_health | `https://i-teka.kz/astana/medicaments/gigiena-i-uhod` | 10 |
| i-Teka: 👶 Мама и малыш | beauty_health | `https://i-teka.kz/astana/medicaments/mama-i-malysh` | 10 |
| i-Teka: 🧴 Лечебная косметика | beauty_health | `https://i-teka.kz/astana/medicaments/lechebnaya-kosmetika` | 10 |

### MebelScraper — 10 настроенных источников категории

| Категория | Master | URL/query | max_pages |
|---|---|---|---|
| Mebel.kz: 🛋 Диваны и кресла | home_furniture | `https://mebel.kz/category/divany` | 10 |
| Mebel.kz: 🛏 Кровати | home_furniture | `https://mebel.kz/category/krovati` | 10 |
| Mebel.kz: 💤 Матрасы | home_furniture | `https://mebel.kz/category/matrasy` | 10 |
| Mebel.kz: 🚪 Шкафы и стеллажи | home_furniture | `https://mebel.kz/category/shkafy` | 10 |
| Mebel.kz: 💻 Рабочие столы | home_furniture | `https://mebel.kz/category/stoly-pismennye-i-kompyuternye` | 10 |
| Mebel.kz: 🍽 Обеденные столы и кухни | home_furniture | `https://mebel.kz/category/kuhonnye-stoly` | 10 |
| Mebel.kz: 🗄 Комоды и тумбы | home_furniture | `https://mebel.kz/category/komody` | 10 |
| Mebel.kz: 💺 Компьютерные кресла | home_furniture | `https://mebel.kz/category/kompyuternye-kresla` | 10 |
| Mebel.kz: 🧶 Ковры и текстиль | home_furniture | `https://mebel.kz/category/kovry-i-tekstil` | 10 |
| Mebel.kz: 💡 Светильники и свет | home_furniture | `https://mebel.kz/category/svet` | 10 |

### DetmirScraper — 10 настроенных источников категории

| Категория | Master | URL/query | max_pages |
|---|---|---|---|
| Детский мир: 🧩 Игрушки и игры | household | `https://detmir.kz/catalog/index/name/igry_i_igrushki/` | 10 |
| Детский мир: 🧱 Конструкторы и LEGO | household | `https://detmir.kz/catalog/index/name/konstruktory/` | 10 |
| Детский мир: 🍼 Детское питание и кормление | grocery | `https://detmir.kz/catalog/index/name/nutrition_feeding/` | 10 |
| Детский мир: 👶 Подгузники и гигиена | household | `https://detmir.kz/catalog/index/name/hygiene_care/` | 10 |
| Детский мир: 👕 Одежда и обувь | clothes | `https://detmir.kz/catalog/index/name/children_clothes/` | 10 |
| Детский мир: 🚼 Детские коляски | household | `https://detmir.kz/catalog/index/name/kolyaski/` | 10 |
| Детский мир: 🚗 Автокресла | household | `https://detmir.kz/catalog/index/name/avtokresla/` | 10 |
| Детский мир: 🛏 Детская комната | home_furniture | `https://detmir.kz/catalog/index/name/childrens_room/` | 10 |
| Детский мир: 🎨 Хобби и творчество | household | `https://detmir.kz/catalog/index/name/hobbies_creativity/` | 10 |
| Детский мир: 🛴 Детский транспорт | household | `https://detmir.kz/catalog/index/name/childrens_transport/` | 10 |

### AskonaScraper — 10 настроенных источников категории

| Категория | Master | URL/query | max_pages |
|---|---|---|---|
| Аскона: 💤 Анатомические матрасы | home_furniture | `https://askona.kz/matrasy/` | 10 |
| Аскона: 🛏 Кровати и изголовья | home_furniture | `https://askona.kz/krovati/` | 10 |
| Аскона: 🛋 Анатомические диваны | home_furniture | `https://askona.kz/divany/` | 10 |
| Аскона: ☁️ Анатомические подушки | home_furniture | `https://askona.kz/podushki/` | 10 |
| Аскона: 🪶 Одеяла | home_furniture | `https://askona.kz/odeyala/` | 10 |
| Аскона: 🧵 Постельное белье и текстиль | home_furniture | `https://askona.kz/tekstil/` | 10 |
| Аскона: 🚪 Мебель для спальни | home_furniture | `https://askona.kz/mebel/` | 10 |
| Аскона: 👶 Товары для детей | household | `https://askona.kz/dlya-detey/` | 10 |
| Аскона: 💺 Кресла и пуфы | home_furniture | `https://askona.kz/kresla/` | 10 |
| Аскона: 🏡 Товары для дома и декор | home_furniture | `https://askona.kz/dlya-doma/` | 10 |

### ZooMarketScraper — 10 настроенных источников категории

| Категория | Master | URL/query | max_pages |
|---|---|---|---|
| Зоомаркет: 🐱 Корма для кошек | pets | `https://zoomarket.kz/catalog/cat/korm_k/` | 10 |
| Зоомаркет: 🐶 Корма для собак | pets | `https://zoomarket.kz/catalog/dog/korm/` | 10 |
| Зоомаркет: 🥫 Консервы для кошек | pets | `https://zoomarket.kz/catalog/cat/konservy_k/` | 10 |
| Зоомаркет: 🍖 Консервы для собак | pets | `https://zoomarket.kz/catalog/dog/konservy/` | 10 |
| Зоомаркет: 🚽 Наполнители и туалеты | pets | `https://zoomarket.kz/catalog/cat/tualet_k/` | 10 |
| Зоомаркет: 💊 Ветаптека для кошек | pets | `https://zoomarket.kz/catalog/cat/apteka_k/` | 10 |
| Зоомаркет: 💉 Ветаптека для собак | pets | `https://zoomarket.kz/catalog/dog/apteka/` | 10 |
| Зоомаркет: 🦴 Лакомства для собак | pets | `https://zoomarket.kz/catalog/dog/lakomstva/` | 10 |
| Зоомаркет: 🐭 Товары для грызунов | pets | `https://zoomarket.kz/catalog/rodent/` | 10 |
| Зоомаркет: 🐠 Аквариумистика и рыбы | pets | `https://zoomarket.kz/catalog/fish/` | 10 |

### PlanetaScraper — 10 настроенных источников категории

| Категория | Master | URL/query | max_pages |
|---|---|---|---|
| Планета: 📱 Смартфоны | smartphones | `https://planeta.kz/ru/site/search/?term=смартфон` | 10 |
| Планета: 💻 Ноутбуки | laptops | `https://planeta.kz/ru/site/search/?term=ноутбук` | 10 |
| Планета: 📺 Телевизоры | tvs | `https://planeta.kz/ru/site/search/?term=телевизор` | 10 |
| Планета: ❄️ Холодильники | appliances_large | `https://planeta.kz/ru/site/search/?term=холодильник` | 10 |
| Планета: 💨 Кондиционеры | appliances_large | `https://planeta.kz/ru/site/search/?term=кондиционер` | 10 |
| Планета: 🧹 Пылесосы | appliances_small | `https://planeta.kz/ru/site/search/?term=пылесос` | 10 |
| Планета: 🫖 Чайники | appliances_small | `https://planeta.kz/ru/site/search/?term=чайник` | 10 |
| Планета: 👔 Утюги | appliances_small | `https://planeta.kz/ru/site/search/?term=утюг` | 10 |
| Планета: 🍹 Блендеры | appliances_small | `https://planeta.kz/ru/site/search/?term=блендер` | 10 |
| Планета: 🎧 Наушники | audio | `https://planeta.kz/ru/site/search/?term=наушники` | 10 |

### KimexScraper — 10 настроенных источников категории

| Категория | Master | URL/query | max_pages |
|---|---|---|---|
| KIMEX: 👟 Женские кроссовки | clothes | `https://kimex.kz/catalog/zhenskoe/obuv/krossovki/` | 10 |
| KIMEX: 👠 Женские туфли | clothes | `https://kimex.kz/catalog/zhenskoe/obuv/tufli/` | 10 |
| KIMEX: 👢 Женские ботинки | clothes | `https://kimex.kz/catalog/zhenskoe/obuv/botinki/` | 10 |
| KIMEX: 👡 Женские босоножки | clothes | `https://kimex.kz/catalog/zhenskoe/obuv/bosonozhki/` | 10 |
| KIMEX: 🥿 Женские лоферы | clothes | `https://kimex.kz/catalog/zhenskoe/obuv/lofery/` | 10 |
| KIMEX: 👟 Мужские кроссовки | clothes | `https://kimex.kz/catalog/muzhskoe/obuv/krossovki/` | 10 |
| KIMEX: 👞 Мужские туфли | clothes | `https://kimex.kz/catalog/muzhskoe/obuv/tufli/` | 10 |
| KIMEX: 🥾 Мужские ботинки | clothes | `https://kimex.kz/catalog/muzhskoe/obuv/botinki/` | 10 |
| KIMEX: 👟 Мужские кеды | clothes | `https://kimex.kz/catalog/muzhskoe/obuv/kedy/` | 10 |
| KIMEX: 👞 Мужские мокасины | clothes | `https://kimex.kz/catalog/muzhskoe/obuv/mokasiny/` | 10 |

### EuropharmaScraper — 10 настроенных источников категории

| Категория | Master | URL/query | max_pages |
|---|---|---|---|
| Europharma: 💊 Лекарственные средства | beauty_health | `https://europharma.kz/catalog/lekarstvennye-sredstva` | 10 |
| Europharma: 🌡 Жаропонижающие | beauty_health | `https://europharma.kz/catalog/zharoponizhayushchiye` | 10 |
| Europharma: 🛡 Противовирусные препараты | beauty_health | `https://europharma.kz/catalog/protivovirusnyye-preparaty` | 10 |
| Europharma: 🦠 Антибиотики | beauty_health | `https://europharma.kz/catalog/antibiotiki` | 10 |
| Europharma: 🩹 Обезболивающие | beauty_health | `https://europharma.kz/catalog/analgetiki` | 10 |
| Europharma: 💊 Спазмолитики | beauty_health | `https://europharma.kz/catalog/spazmoliticeskie-preparaty` | 10 |
| Europharma: 🩺 Медицинские приборы | beauty_health | `https://europharma.kz/catalog/meditsinskiye-pribory` | 10 |
| Europharma: 🦴 Витамины и минералы | beauty_health | `https://europharma.kz/catalog/vitaminy-i-mineraly` | 10 |
| Europharma: 👶 Мать и дитя | beauty_health | `https://europharma.kz/catalog/mat-i-ditya` | 10 |
| Europharma: 🧴 Дермакосметика | beauty_health | `https://europharma.kz/catalog/dermakosmetika` | 10 |

## Перед подключением следующего магазина

Заполнить capabilities и source policy по [SCRAPER_STANDARD.md](SCRAPER_STANDARD.md), получить offline fixtures (без cookies/токенов), проверить ID/варианты/регион, current/old price/availability, redirect/domain restrictions, complete/partial/blocked, bounded timeout/retry, cancellation и disabled-state на всех entrypoints. Малый canary только после успешных fixture tests. Не обходить CAPTCHA/403 или авторизацию.
