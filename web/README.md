# Веб-интерфейс и API

[gui.py](../gui.py) запускает aiohttp и [создаёт приложение](server.py). Локальный порт по умолчанию — 8080, в контейнере — 35539. Приложение читает `DATA_DIR` из окружения; там хранятся `prices.db` и `settings.json`.

`./run_local.sh` включает вход разработчика для прямого loopback-подключения при выполнении условий в [auth.py](../auth.py). Перед локальной пробой задайте отдельный `DATA_DIR`. На внешних установках вход разработчика выключен.

## Интерфейс

- [search_first.html](templates/search_first.html) — основной интерфейс поиска, результатов, карточки товара, скидок и профиля. В браузере работает клиентская навигация.
- [closed_login.html](templates/closed_login.html) — экран входа и ограниченного доступа.
- [admin.html](templates/admin.html) и [monitoring.html](templates/monitoring.html) — инструменты администратора и центр мониторинга.
- [privacy.html](templates/privacy.html) — сведения об использовании данных.
- [static](static/) — ресурсы браузера и общие модули.

## Основные маршруты

| Путь | Назначение |
|---|---|
| `/`, `/search`, `/product/{product_id}`, `/offer/{offer_id}`, `/deals`, `/profile` | Пользовательские страницы. Доступ определяется режимом среды. |
| `GET /api/v2/search` | Результаты каталога с фильтрами и предложениями по городам. |
| `GET /api/v2/products/{product_id}`, `GET /api/v2/products/{product_id}/history` | Карточка товара и история цен. |
| `GET /api/v2/deals` | Лента скидок. |
| `/api/auth/telegram`, `/api/auth/telegram-webapp`, `/api/auth/logout` | Вход через Telegram и завершение сессии. Возможности входа зависят от версии. |
| `GET /api/me`, `GET /api/me/export`, `POST /api/me/delete` | Состояние доступа, выгрузка и удаление данных. |
| `GET /api/version`, `GET /api/changelog`, `GET /api/health`, `GET /api/live` | Версия, заметки к выпуску, готовность и liveness. |
| `GET /api/diag`, `GET /diag` | Защищённая диагностика. |

Полный реестр маршрутов и декораторы доступа находятся в [server.py](server.py). Некоторые маршруты доступны только после входа, часть административных API защищена дополнительно. Режимы доступа, проверка Origin и доверенных прокси описаны в [SECURITY_CONFIG.md](../SECURITY_CONFIG.md). Docker HEALTHCHECK использует `/api/live`; для готовности приложения используйте `/api/health`.

Совместимый `/api/best-price` сохраняется для прежних клиентов. Основной веб-поиск использует API v2. При изменении веб-контракта проверьте вызовы в HTML и браузерные тесты; для отрицательных проверок авторизации передавайте заголовки явно. [Тесты](../tests/README.md)
