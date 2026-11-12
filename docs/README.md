# Документация KZ Price Hunter

## Пользователям

- [Поиск, скидки, избранное и наблюдения](../README.md#как-пользоваться)
- [Telegram-бот и Mini App](TELEGRAM_BOT.md)
- [Использование данных и доступ к аккаунту](SECURITY_AND_PRIVACY.md) · [карта данных](../PRIVACY_DATA_MAP.md)

## Разработчикам

- [Архитектура](ARCHITECTURE.md) · [контекст проекта](../PROJECT_CONTEXT.md)
- [Веб-интерфейс и API](../web/README.md) · [доменные модели](../domain/README.md) · [тесты](../tests/README.md)
- [Подключение магазина](SCRAPER_GUIDE.md) · [стандарт адаптеров](../SCRAPER_STANDARD.md) · [реестр источников](../SOURCES.md)
- [Сопоставление товаров и цены](MATCHING_AND_ARBITRAGE.md) · [политика локального ИИ](AI_LOCAL_POLICY.md) · [критерии поиска с ИИ](LOCAL_AI_SEARCH_REQUIREMENTS.md)

## Администраторам

- [Испытательный стенд](TEST_STAND.md) · [безопасная настройка reverse proxy](../SECURITY_CONFIG.md)
- [Диагностика приложения](DIAGNOSTICS.md) · [выпуск и откат](RELEASE_AND_ROLLBACK.md) · [прод и восстановление](PROD_MOVE_P17E.md)
- [Версия прода](https://shop.molthun.ru/api/version) · [версия стенда](https://shdev.molthun.ru/api/version)

## План и история

[Правила команды](../AGENTS.md) → [план работ](DEVELOPMENT_PLAN.md) → [журнал передачи](AGENT_HANDOFF.md).
[История версий](../CHANGELOG.md) и [журнал реализации](../ROADMAP_AND_LOG.md) фиксируют выпуски и этапы.

Именованные аудиты, приёмки, отчёты о доработках и `audit_evidence/` описывают состояние на дату и коммит проверки. [Навигация по историческим материалам](HISTORY.md).
