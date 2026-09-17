# 🎯 KZ Price Hunter

Мониторинг цен в крупнейших сетях электроники Казахстана: поиск ценовых ошибок («пропущенный ноль»), глубоких скидок и межмагазинного арбитража. Результаты выводятся в веб-панели и отправляются в Telegram.

**Прод:** [https://shop.molthun.ru](https://shop.molthun.ru)

## Магазины

| | Магазин | Сайт |
|---|---|---|
| <img src="https://www.google.com/s2/favicons?domain=kaspi.kz&sz=32" width="16"> | Kaspi Магазин | `kaspi.kz` |
| <img src="https://www.google.com/s2/favicons?domain=dns-shop.kz&sz=32" width="16"> | DNS Казахстан | `dns-shop.kz` |
| <img src="https://www.google.com/s2/favicons?domain=shop.kz&sz=32" width="16"> | Белый Ветер | `shop.kz` (официальная YML-выгрузка) |
| <img src="https://www.google.com/s2/favicons?domain=technodom.kz&sz=32" width="16"> | Технодом | `technodom.kz` |
| <img src="https://www.google.com/s2/favicons?domain=sulpak.kz&sz=32" width="16"> | Sulpak | `sulpak.kz` |
| <img src="https://www.google.com/s2/favicons?domain=mechta.kz&sz=32" width="16"> | Мечта | `mechta.kz` |
| <img src="https://www.google.com/s2/favicons?domain=alser.kz&sz=32" width="16"> | Alser | `alser.kz` |
| <img src="https://www.google.com/s2/favicons?domain=evrika.com&sz=32" width="16"> | Эврика | `evrika.com` |
| <img src="https://www.google.com/s2/favicons?domain=moon.kz&sz=32" width="16"> | Moon.kz | `moon.kz` |
| <img src="https://www.google.com/s2/favicons?domain=forcecom.kz&sz=32" width="16"> | Forcecom | `forcecom.kz` |
| <img src="https://www.google.com/s2/favicons?domain=4mobile.pages.dev&sz=32" width="16"> | 4mobile | `4mobile.pages.dev` |

## Возможности

- **🚨 Аномалии цен** — «пропущенный ноль»: цена упала в 8–12 раз относительно истории цен в базе или зачеркнутой цены на сайте.
- **🔥 Супер-скидки** — падение цены от заданного процента и суммы выгоды.
- **⚖️ Межмагазинный арбитраж** — товар заметно дешевле самой низкой цены того же товара в других сетях.
- **🔍 Поиск лучшей цены** — поиск по всем магазинам через SQLite FTS5 (режимы «все слова» / «любое слово», минус-слова, фильтры по цене и магазину); по желанию — прямой опрос Kaspi, Белого Ветра и 4mobile.
- **📦 База товаров** — каталог 20 000+ товаров с поиском и фильтром по магазину.
- **🤖 Автообновление базы** — фоновое сканирование, когда база старше заданного интервала (5 минут – 30 дней, по умолчанию 3 часа).
- **📨 Telegram** — уведомления с фото товара и выбором уровня: все аномалии, только «пропущенный ноль» или только крупные скидки.
- **📜 Логи (Live)** — системные логи в реальном времени прямо в панели, с фильтрами и выгрузкой.
- **📍 Регион** — выбор города Казахстана, автоопределение по геолокации (только по HTTPS или на localhost) или по IP.

## Деплой (прод)

```
push в main ──► GitHub Actions: тесты ──► сборка образа ghcr.io/molthun/kz-price-hunter:latest
                                                   │
          Synology (Docker) ◄── Watchtower проверяет образ каждые 5 минут
                 │
          Reverse proxy DSM ──► https://shop.molthun.ru
```

- **Любой пуш в `main` уходит на прод примерно через 5 минут.** В CI тесты запускаются до сборки: если они падают, образ не собирается.
- `latest` публикуется **только из `main`**. Теги `vX.Y.Z` собирают образы с номером версии (`:2.9.0`) — на них можно откатить прод.
- Контейнер описан в [`docker-compose.yml`](docker-compose.yml): порт `35539`, данные (база и `settings.json`) в томе `./data` → `/app/data`.
- GitHub не запускает сборки, если за один пуш отправлено больше трех тегов — пушьте теги по одному.

## Локальный запуск

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/playwright install chromium   # нужен скраперам DNS, Технодом и Kaspi
./venv/bin/python3 gui.py
```

Панель откроется на `http://localhost:8080`, в локальной сети — `http://<ip-компьютера>:8080`.

### Переменные окружения

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `PORT` | `8080` | Порт веб-панели |
| `DATA_DIR` | папка проекта | Где лежат `prices.db` и `settings.json` |
| `APP_URL` | `https://shop.molthun.ru` | Ссылка на панель в Telegram-уведомлениях |
| `DNS_BOT_TOKEN`, `DNS_CHAT_ID` | — | Токен и чат Telegram, если они не заданы в настройках |

### Консольный режим

```bash
./venv/bin/python3 main.py --once   # один цикл сканирования
./venv/bin/python3 main.py          # сканирование по кругу
```

> CLI-режим отстает от веб-панели: в нем нет Kaspi, не учитываются включенные магазины и уровень Telegram-уведомлений. Основной режим — `gui.py`.

### Тесты

```bash
./venv/bin/python3 test_monitor.py
```

Тесты работают на временной базе и не трогают `prices.db` и `settings.json`.

## Настройки

Все настройки меняются во вкладке **«Настройки & Telegram»** и хранятся в `settings.json` внутри `DATA_DIR`. Файл не хранится в git. Сервер проверяет значения при сохранении: неизвестные поля отбрасываются, отрицательные числа и неверные типы отклоняются.

| Группа | Что настраивается |
|---|---|
| Детекция аномалий | Типы аномалий, мин./макс. цена товара, мин. скидка (%) и выгода (₸), порог арбитража (% и ₸) |
| Стоп-слова | Минус-слова для аксессуаров, исключение уцененных и б/у товаров |
| Поиск по умолчанию | Сортировка, исключение аксессуаров, прямой опрос магазинов |
| Telegram-уведомления | Токен бота, Chat ID, уровень алертов, тестовое сообщение |
| Сканирование магазинов | Интервал автообновления базы, включенные магазины |

## Структура проекта

```
gui.py              — точка входа: запуск веб-панели (aiohttp)
web/server.py       — API и фоновое сканирование
web/templates/      — веб-интерфейс (одна страница, Tailwind)
scrapers/           — скраперы магазинов
detector.py         — детекция аномалий, скидок и арбитража
search_engine.py    — поиск лучшей цены (FTS5 + прямой опрос)
database.py         — SQLite (WAL + FTS5): товары, история цен, алерты
config.py           — настройки, категории, города
notifier.py         — Telegram-уведомления
log_manager.py      — перехват логов для вкладки «Логи»
main.py             — консольный режим
test_monitor.py     — тесты
version.py          — версия приложения
```

## Документация

- [`CHANGELOG.md`](CHANGELOG.md) — история изменений по версиям
- [`ROADMAP_AND_LOG.md`](ROADMAP_AND_LOG.md) — текущий статус, известные проблемы и планы
- [`PROJECT_CONTEXT.md`](PROJECT_CONTEXT.md) — архитектура, схема БД и детали реализации

Версии ведутся по [SemVer](https://semver.org/lang/ru/): каждый релиз меняет `version.py`, `CHANGELOG.md` и `ROADMAP_AND_LOG.md` и получает git-тег `vX.Y.Z`.
