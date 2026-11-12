# Контекст проекта KZ Price Hunter

KZ Price Hunter — поиск цен и предложений продавцов по Казахстану. Основной сценарий — поисковый запрос, карточка товара, сравнение предложений по городу и переход к продавцу. Избранное и наблюдения сохраняются в аккаунте, уведомления доставляются через Telegram по настройкам пользователя.

## Компоненты и источники сведений

| Область | Код | Документация |
|---|---|---|
| Сайт и API | [gui.py](gui.py), [web/server.py](web/server.py), [web/templates](web/templates/) | [Веб-слой](web/README.md) |
| Поиск | [repositories](repositories/), [search_engine.py](search_engine.py), [local_search.py](local_search.py) | [Требования к локальному ИИ](docs/LOCAL_AI_SEARCH_REQUIREMENTS.md) |
| Каталог и цены | [database.py](database.py), [domain](domain/), [product_key.py](product_key.py) | [Архитектура](docs/ARCHITECTURE.md), [сопоставление](docs/MATCHING_AND_ARBITRAGE.md) |
| Сбор и мониторинг | [config.py](config.py), [scrapers](scrapers/), [monitoring.py](monitoring.py) | [Источники](SOURCES.md), [диагностика](docs/DIAGNOSTICS.md) |
| Telegram | [telegram_bot.py](telegram_bot.py), [notifier.py](notifier.py), [watches.py](watches.py) | [Бот и Mini App](docs/TELEGRAM_BOT.md) |
| Модели и политика провайдеров | [ai_router.py](ai_router.py), [ai_service.py](ai_service.py), [local_ai.py](local_ai.py) | [Политика локального ИИ](docs/AI_LOCAL_POLICY.md) |

## Где смотреть актуальное состояние

- [README](README.md) содержит установку и основные маршруты.
- [План](docs/DEVELOPMENT_PLAN.md) и [журнал передачи](docs/AGENT_HANDOFF.md) фиксируют требования, решения и состояние работ.
- [Прод](https://shop.molthun.ru/api/version) и [стенд](https://shdev.molthun.ru/api/version) показывают фактически запущенные версии.
- [Порядок выката](docs/RELEASE_AND_ROLLBACK.md), [стенд](docs/TEST_STAND.md) и [восстановление](docs/PROD_MOVE_P17E.md#восстановление) описывают сопровождение.
