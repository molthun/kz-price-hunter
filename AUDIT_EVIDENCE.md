# Доказательства аудита — 18.09.2026

Проверяемая версия: e7ebcdc + исходный пользовательский diff database.py. Ниже только синтетические результаты и агрегаты, без credentials/профилей/переписки.

## Базовые проверки

| Проверка | Результат | Ограничение |
|---|---|---|
| test_monitor.py, Python 3.14.7 | 85 tests, 2.474 s, OK | Temporary DATA_DIR; outbound blocked; нет подтверждения живых источников |
| test_frontend.cjs | guest_hidden=true, authenticated_card_rendered=true, logout_hidden=true, page_errors=[] | Chrome headless, mocked API, external requests abort |
| pip check | No broken requirements found | Только локальное окружение |
| OSV /v1/querybatch | 28 PyPI packages, findings=[] | Не контейнер/OS, не будущая сборка >= dependencies |
| Git scan | 67 commits, 430 unique blobs; token/private-key pattern hits=0 | Только достижимые локальные refs, ограниченный набор паттернов |
| Historical data | 14 prices.db blobs + 1 settings.json blob | В DB только products/alerts; непустых secret fields в settings не найдено |
| Current SQLite read-only | quick_check=ok; declared FK violations=0 | Отсутствие нарушений не означает FK enforcement |

## Воспроизведённые дефекты

| Сценарий | Фактический результат | Finding |
|---|---|---|
| Production renderAlertCard с синтетическим title markup, отдельная страница без сети | Безвредный window.auditExecuted=true выполнен браузером | C01 |
| cleanUrl('javascript:void(0)') | Небезопасная схема возвращается без изменения | C01 |
| escapeHtml для кавычек | Кавычки остаются; функция непригодна для атрибутного escaping | C01 |
| guest DELETE handler, временный alert | HTTP 200, общий is_dismissed=1 | H01 |
| get_best_price_summary(live=False), пустая mocked выдача | search_live_stores вызван 1 раз | H03 |
| Сохранить id в Астане 100000, затем тот же id в Алматы 80000 | Одна запись Алматы/80000; first_seen_price=100000 из Астаны | H05 |
| Сохранить old_price_on_site=150000 при current=100000 | DB old_price_on_site=0; batch также не сохраняет поле | H12 |
| get_connection() приложения, временная DB | PRAGMA foreign_keys=0 | H11 |
| same_model iPhone 15 128GB eSIM Black / Dual SIM White | true | H09 |
| TechnodomScraper._price(199990.0) | 1999900 | M08 |
| Halyk: 24 товара, products_total отсутствует | complete=true | H08 |
| Forte: 20 товаров, nbHits отсутствует | complete=true | H08 |

## Снимок данных и сохранность

Первое read-only чтение: 61 379 products, 1 591 alerts, 31 973 с canonical_key, 0 ненулевых old_price_on_site. 2513 групп shop+url имеют больше одной записи; среди URL есть общие страницы категорий, поэтому автоматически удалять такие записи нельзя.

При итоговом чтении products=61 381, alerts=1591, quick_check=ok. Уже существующий Python PID 62430 работает из директории проекта и слушает *:8080. Его не запускали, не останавливали и не опрашивали HTTP во время аудита. Изменение hash prices.db за время аудита зафиксировано; при работающем экземпляре это не замороженный снимок.

SHA-256 database.py и settings.json совпали с сохранёнными в начале проверки. Git diff исходного кода остался только пользовательским database.py; добавлены лишь четыре Markdown-документа аудита. На рабочей БД не вызывался init_db и не выполнялись изменения SQL. Временные тестовые базы изолированы.

## Незакрытые проверки

Docker build/image scan, production reverse proxy/TLS/backups/restore, живой canary всех источников, условия владельцев и robots, нагрузка на 100 sources, matching corpus на размеченных реальных парах. Эти проверки не следует считать выполненными по результатам unit tests.
