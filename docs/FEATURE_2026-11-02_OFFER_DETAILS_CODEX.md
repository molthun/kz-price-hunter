# Карточка предложения выбранного продавца — 02.11.2026

Codex, прямое поручение владельца; база 7ab8452, v6.18.0, один пишущий агент.
Только стенд; production/main push не разрешены. Чужие untracked сохранены.

## Поведение

Выбор продавца/«Выбрать предложение» открывает /offer/{offer_id}. Внутри:
его исходное название, цена, состояние, город, канал, история именно этого предложения,
описание/характеристики/фото его источника. Возврат ко всем предложениям и внешний переход
«К продавцу» сохраняются. Варианты других продавцов не примешиваются.

Metadata читаются в одном snapshot. Product ID служит идентичностью товара, выбранный offer ID —
контекстом предложения. Общие product attributes/description/image не используются в scoped DTO;
медиаданные загружаются отдельным offer API. Если источник не ответил — только собственное
сохранённое фото предложения и явное отсутствие описания; данные конкурента не подставляются.
Общая карточка товара и существующая карусель сохраняются. Наблюдения/избранное остаются
существующими функциями канонического товара; новый механизм seller-specific watches не добавлялся.

Offer-only RAM cache, signature (URL/title/image/seller/channel/SKU), single-flight;
source prose не записывается в БД/логи/копии. Существующий semaphore и общий rate limit сохранены.
HTML fetch с DNS pinning/SSRF/body/redirect guards; редирект в другой магазин запрещён.
Forte API сопоставляется строго с external_sku objectID, не с первым поисковым результатом.
Проверки прав закрытого сайта и same-origin header защищают новые endpoints.
Данные и идентичности товаров/предложений в БД не изменяются.

На клиенте serial/scope guard отклоняет поздний ответ общей карточки или другого продавца,
даже когда canonical product ID совпадает. На загрузке/404 старая медиакарточка очищается.

## Проверки

- Полный suite: 2628 PASS + 1120 subtests, 164.23 s (до последних scoped guards).
- Конечный профильный набор: 26 PASS, включая ClosingConnection, реальные HTTP handlers/auth middleware,
  guest gate, same-origin, isolate cache/media, stale source signature, exact Forte objectID,
  запрет cross-store redirect; источники/Telegram/API provider в этих тестах подставлены.
- Offer browser 390/1366 × обе темы: 4/4, выбор/возврат, две цены/источника, отсутствие подмены
  при отказе, запоздавший generic response, 404. Screenshot QA PASS на тестовых SVG.
- Прежние facts/description/gallery/carousel/feed/variant browser сценарии: 4/4.
- Secrets findings 0, diff-check PASS. Новые source данные на реальном Forte не проверялись.
  Оплаченные AI вызовы/Telegram send отсутствуют. Owner login на основном стенде не использовался.

## Выкладка ожидает доступа

SSH 192.168.28.25:2222 несколько раз timeout; https://shdev.molthun.ru/api/live тоже timeout.
Маршрут на stand IP проходит через utun9. Сетевые/VPN настройки не менялись.
Стенд **не обновлён этой задачей**. Нельзя считать локальную проверку подтверждением на стенде.
Подготовить git bundle от последнего подтверждённого stand SHA 1c0339acf и после восстановления
доступа проверить remote HEAD/dirty, fast-forward/build/recreate только stand app, затем проверить
runtime hashes, guest gate и два разных предложения одного товара с реальной загрузкой источника.
Новых owner/fixture пользователей/сессий на основном стенде не создавать.

Evidence: audit_evidence/2026-11-02-offer-details/. Проверка авторская, не независимая приёмка.
