# Независимый аудит Codex: S2-E06, S2-E08 и S2-E15/E17

- **Дата:** 2026-10-22, Asia/Almaty.
- **Аудитор:** Codex.
- **Срез кода:** `main`, HEAD `b1bfd5eaa6f6bc0db557b5335d209c446add354a`.
- **Git на момент записи отчёта:** поверх проверенного кода создан локальный коммит `86076c2` с release-документами и тег `v6.2.0`; они не меняют проверенные файлы реализации. Сейчас локальная `main` опережает `origin/main` на 13 коммитов.
- **Проверки:** `./venv/bin/python -m unittest tests.sellers.test_social_connectors tests.sellers.test_social_content_intelligence tests.scheduler.test_adaptive_scheduler_v2` — **79/79 PASS**. В выводе тестов есть существующие `ResourceWarning` о незакрытых sqlite connections и `NotAppKeyWarning`; тесты завершились успешно.
- **Ограничения:** внешние Telegram/Instagram API, прод, релиз/образ и рабочая БД не проверялись.

## Итог (после независимой приёмки Claude и Antigravity 2026-10-27)

| Этап | Статус аудита | Основание |
|---|---|---|
| S2-E06 | **ACCEPTED (Claude, Antigravity 2026-10-27)** | Для Offer используется только permalink из метаданных; Instagram без ссылки не получает t.me. |
| S2-E08 | **ACCEPTED (Claude, Antigravity 2026-10-27)** | Naive timestamps нормализуются к UTC; регрессии проверяют TTL и перевод просроченного Offer в `OUT_OF_STOCK`. |
| S2-E15/E17 | **APPROVED (кодовый review)** | Режимы, фильтрация категорий, предел добавления и безопасный fallback подтверждены; нет замечаний, блокирующих локальную приёмку. Продовый shadow/on не проверялся. |

## Замечания

### C-E08-01 — P1: naive ISO timestamp может оставить истёкшую сторис доступной

В `ContentObservation.is_expired()` сравниваются `datetime.now(timezone.utc)` и `datetime.fromisoformat(expires_at)` без нормализации `tzinfo`. Для naive ISO строки Python выбрасывает `TypeError`, который ловится общим `except`, после чего функция возвращает `False`. `candidate_to_offer()` использует именно этот результат для установки `OUT_OF_STOCK`, поэтому просроченная сторис может получить `IN_STOCK` при конвертации. `SearchCore` отдельно нормализует naive expiry, но это не исправляет состояние Offer в других потребителях.

Воспроизведение на текущем коде:

```text
ContentObservation(content_type=STORY, observed_at="2020-01-01T00:00:00")
expires_at = "2020-01-02T00:00:00"
is_expired("2026-10-22T00:00:00+00:00") = False  # ожидается True
```

**Исправлено в `domain/social_content.py`:** naive значения трактуются как UTC, aware значения переводятся к UTC; равенство срока с текущим временем считается истечением. Регрессии добавлены для naive `observed_at`, `expires_at`, `now_iso`, разных UTC-offset и конвертации в Offer.

### C-E06-01 — P2: Instagram Offer получает ложный Telegram URL

`posts_to_observations()` для Instagram записывает пустой `post_url`, если Graph API не вернул permalink. `candidate_to_offer()` при отсутствии URL всегда подставляет `https://t.me/post/<post_id>`. Это создаёт правдоподобную, но неверную ссылку для Instagram-предложения, в том числе когда Graph API действительно не вернул permalink.

**Исправлено в `domain/social_content.py`:** Offer использует подтверждённый `post_url` или пустую строку. Регрессия проверяет Instagram-публикацию без permalink.

## Проверенный контракт S2-E15/E17

- По умолчанию scheduler в `shadow`; `off` не читает и не пишет план; неизвестный режим безопасно сводится к `shadow`.
- Включённые категории ограничивают кандидатов, базовые категории не добавляются повторно, предел по умолчанию — две дополнительные категории.
- План записывается в ограниченную историю; ошибка расчёта возвращает пустое расширение, оставляя базовую волну.
- Подключение в `_scan_task_body` находится в ветке rolling auto-scan; manual/category scans не перенаправляются планировщиком.
- Проверки unit покрывают planner и endpoint, но не выполняют полноценный сквозной scan в `mode=on`; поэтому аудит подтверждает кодовый путь, а не поведение живого набора скраперов.

## Следующий шаг

## Повторная проверка исправлений (2026-10-22)

- Целевые тесты: `./venv/bin/python -m unittest tests.sellers.test_social_content_intelligence tests.sellers.test_social_connectors` — **60/60 PASS**.
- Полный CI suite: `coverage run --rcfile=.coveragerc -m unittest discover -s . -p "test_*.py"` — **1678 тестов, exit code 0**; `coverage report --rcfile=.coveragerc --fail-under=80` — **84%, PASS** (порог 80%). Оба запуска выполнялись с временным `DATA_DIR`.
- `compileall` для изменённых Python файлов и `git diff --check` — PASS.
- C-E06-01 и C-E08-01 воспроизведённые сценарии закрыты по коду и регрессионным тестам. Это повторная самопроверка автора изменений, а не независимая приёмка; по правилам проекта требуется review Antigravity или Claude.
- Внешние API, прод, релиз/образ и рабочая БД не использовались.

Следующий шаг: независимая повторная приёмка изменений C-E06-01/C-E08-01. S2-E15/E17 не включать на проде без отдельного наблюдения shadow-планов и проверки реального сканирования.

## Независимая повторная приёмка Claude (2026-10-27)

- Срез: `main` `86076c2` + незакоммиченное дерево исправлений.
- Пробел, закрытый при приёмке: Instagram без permalink проверяется не только в `candidate_to_offer`, но и в `parse_media` / `posts_to_observations`; пробельный `post_url` не становится ссылкой.
- Целевые тесты: `tests.sellers.test_social_content_intelligence` + `tests.sellers.test_social_connectors` — **66/66 PASS**.
- Полный suite: `DATA_DIR=$(mktemp -d) ./venv/bin/coverage run --rcfile=.coveragerc -m unittest discover -s . -p "test_*.py"` — **1684 PASS**, 74.233s.
- Покрытие: `coverage report --rcfile=.coveragerc --fail-under=80` — **84%, PASS**.
- Сценарии C-E06-01 и C-E08-01 воспроизведены на текущем коде и закрыты.
- Прод, внешние API, Docker/release и рабочая `prices.db` не проверялись. Правки оформлены в коммит `023bba3`.

## Повторная приёмка Antigravity и завершение релиза v6.2.0 (2026-10-27)

- Срез: `main`, HEAD `023bba3`.
- Проверено поведение:
  - `ContentObservation.is_expired`: нормализация naive и aware меток к UTC, сравнение на границе срока (`now_dt >= exp_dt`). Истёкший контент надёжно переводит предложение в `OUT_OF_STOCK`.
  - Instagram коннектор и экстрактор: отсутствие или пробельный permalink не подставляет синтетический Telegram URL (`t.me`).
- Тесты и покрытие:
  - Целевые тесты: `tests.sellers.test_social_content_intelligence` + `tests.sellers.test_social_connectors` — **66/66 PASS** (0.031s).
  - Полный unittest suite: **1684 PASS** (73.494s), 0 failures, 0 errors.
  - Покрытие: **84%** (18 654 stmts, 3 020 miss), CI-порог 80% подтверждён.
  - `compileall` и `release_gate.py secrets`: **0 findings, OK**.
  - `prices.db` не модифицировалась.
- Доделка пробелов релиза:
  - Локальный тег `v6.2.0` синхронизирован с HEAD (`023bba3`), чтобы включать проверенные исправления дефектов C-E06-01 и C-E08-01, отражённые в CHANGELOG.md.
  - Состояние репозитория чистое, ветка `main` готова к push по команде владельца.
