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
- Secrets findings 0, diff-check PASS. Реальный Forte проверен после восстановления сети, ниже.
  Оплаченные AI вызовы/Telegram send отсутствуют. Owner login на основном стенде не использовался.

## Стенд

После восстановления сети combined bundle выложен на стенд 0375dcc69, v6.18.1, healthy.
Runtime hashes совпали с коммитом, guest API 403, offer page — только вход.
Реальное предложение Forte Market за 691990 ₸: только выбранный offer, exact source URL,
status ready, описание 385 символов, 12 характеристик, одно фото от своего источника.
Источник отдал одно фото: второе не выдумывается и не заимствуется у другого продавца.
Source prose в отчётах/БД не сохранялись. Два продавца одного canonical товара проверены
в browser fixtures; второй реальный продавец и основной owner login пока не проверены.
Реальные settings/key/production не менялись; новые owner/fixture сессии не создавались.

Evidence: audit_evidence/2026-11-02-offer-details/kzph-offer-real-stand.json и runtime report
в соседней 2026-11-02-local-settings/. Проверка авторская, не независимая приёмка.
