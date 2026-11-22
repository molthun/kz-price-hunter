# Документация KZ Price Hunter

Действующая схема: один живой сервер и воспроизводимая копия кода в Git. Старые отчёты описывают состояние на дату записи и не определяют текущий порядок эксплуатации.

## Начать здесь

| Мне нужно | Руководство |
|---|---|
| Пользоваться поиском | [Главная страница проекта](../README.md#как-пользоваться) |
| Развернуть свой сервис | [Установка](INSTALL.md) |
| Понять текущую рабочую среду | [Эксплуатация](OPERATIONS.md) |
| Проверить и обновить версию | [Изолированные проверки](TEST_STAND.md) · [Выпуск и откат](RELEASE_AND_ROLLBACK.md) |
| Восстановить данные | [Backup и восстановление](OPERATIONS.md#резервная-копия-и-восстановление) |
| Продолжить работу агентом | [Правила](../AGENTS.md) · [Текущая передача](AGENT_HANDOFF.md) |
| Найти старое решение | [История и архивы](HISTORY.md) |

## Возможности и устройство

- [Telegram и Mini App](TELEGRAM_BOT.md), [доступ и данные](SECURITY_AND_PRIVACY.md), [карта данных](../PRIVACY_DATA_MAP.md).
- [Архитектура](ARCHITECTURE.md), [контекст](../PROJECT_CONTEXT.md), [веб/API](../web/README.md), [доменные модели](../domain/README.md).
- [Подключение магазина](SCRAPER_GUIDE.md), [стандарт адаптеров](../SCRAPER_STANDARD.md), [источники](../SOURCES.md), [города и цены](REGIONAL_PRICING.md).
- [Сопоставление товаров](MATCHING_AND_ARBITRAGE.md), [локальный AI](AI_LOCAL_POLICY.md), [требования к поиску](LOCAL_AI_SEARCH_REQUIREMENTS.md).
- [Прокси и безопасность](../SECURITY_CONFIG.md), [диагностика](DIAGNOSTICS.md), [тесты](../tests/README.md).

## План и история

[План работ](DEVELOPMENT_PLAN.md) — требования и приоритеты. [CHANGELOG](../CHANGELOG.md) и [журнал реализации](../ROADMAP_AND_LOG.md) — история версий. [Миграция на один сервер](archive/SINGLE_LIVE_MIGRATION.md) — выполненная операция, её не повторяют при каждом обновлении.

Оперативные задачи хранятся в коротком [AGENT_HANDOFF.md](AGENT_HANDOFF.md). Исторические отчёты аудитов и приёмок вынесены в [archive/](archive/README.md); они и [audit_evidence/](audit_evidence/) не заменяют инструкцию установки или текущий статус сервиса.
