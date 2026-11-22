# Повторная приёмка C-E08-01 / C-E06-01 / C-E06-02

Дата: 2026-10-27, Asia/Almaty. Исполнитель: Codex по прямому поручению владельца.
База: `main`, `021b58a95270326f32b6464b1e61415e5c4ac464`, исходное дерево чистое.
Проверяется локальный рабочий срез с перечисленными ниже дополнениями.

## Результат проверки кода

- **C-E08-01 подтверждён:** `ContentObservation.is_expired` уже приводит naive ISO к UTC,
  aware ISO — к UTC через `astimezone`; сравнение включает точный момент истечения (`>=`).
  Дополнительная регрессия проверяет 20 сочетаний: naive, `Z`, `+05:00`, `-12:00`,
  предыдущая локальная дата, микросекунда до/после границы и сама граница.
  Существующая регрессия подтверждает OUT_OF_STOCK для просроченной naive Story.
- **C-E06-01 подтверждён:** `posts_to_observations` синтезирует `t.me` только для Telegram;
  `candidate_to_offer` берёт только очищенный `raw_metadata.post_url`.
  Добавлен сквозной офлайн-тест `InstagramConnector.parse_media → posts_to_observations →
  extract_candidates → candidate_to_offer`: отсутствующий, пустой, пробельный permalink
  дают пустой `Offer.url`, настоящий Instagram permalink сохраняется.
- **Найден и исправлен остаток C-E06-02:** в карточке 2.0 список офферов использовал
  `storeUrl`, а отдельная кнопка лучшей цены — truthy-проверку `best.url` и `cleanUrl`.
  При `best.url="   "` отображалась кнопка с `href="#"`. Новый браузерный тест упал
  до правки (`actual: ['#'], expected: []`). Кнопка теперь использует `storeUrl`.
  Проверяются пустой/пробельный/null/undefined/`#`/javascript URL и положительный контроль:
  обе кнопки доступны при настоящем permalink.

## Проверки

- Целевые Python-модули: **68 PASS**.
- Полный Python suite: **1686 PASS**, 74.258 s, 0 failures/errors.
- Покрытие: **84%** (18 654 statements, 3 018 miss), CI-порог **80% PASS**;
  `domain/social_content.py` — 82%, `domain/social_connector.py` — 89%.
- Все **17 браузерных наборов PASS** на исправленном шаблоне; после добавления положительного
  контроля обеих кнопок набор `test_frontend_social_no_permalink.cjs` повторён — PASS.
- `python -m compileall -q domain repositories web tests` — PASS.
- `python release_gate.py secrets --root .` — `ok: true`, findings 0.
- `git diff --check` — PASS.

Команда полного Python-прогона:

```bash
TELEGRAM_BOT_TOKEN= GEMINI_API_KEY= OPENAI_API_KEY= \
COVERAGE_FILE=/private/tmp/kzph-reaccept-20261027/.coverage-final \
./venv/bin/python -m coverage run --rcfile=.coveragerc \
  -m unittest discover -s . -p 'test_*.py'
```

Тестовые модули импортируют `tests.test_support` до кода приложения: `DATA_DIR` указывает
на временный каталог. Реальные AI/Telegram credentials отключены в окружении запуска.
Первый Python-прогон в sandbox прерван: локальные HTTP-тесты не могли открывать порты.
Окончательный прогон выполнен с разрешёнными локальными портами. Chrome также запускался
вне sandbox после SIGABRT/EPERM внутри него. Это ограничения окружения проверки.

Логи: `/private/tmp/kzph-reaccept-20261027/python-final.log`,
`frontend-final.log`; coverage data — `.coverage-final` в том же каталоге.

## Границы приёмки

Повторная проверка существующих C-E08-01/C-E06-01 выполнена Codex независимо от автора
этих исправлений. Доработка отдельной кнопки C-E06-02 написана Codex и проверена тестами;
самопроверка этой новой правки не является независимым аудитом другим агентом.

Это приёмка указанных дефектов, не полный аудит выпуска 6.2.1. Live Instagram/Telegram,
Docker/CI, dependency-audit и production smoke в этой сессии не выполнялись.
Продуктовая БД, схема, настройки и секреты не менялись. Push, теги и выпуск не выполнялись.
Текущий рабочий срез содержит дополнения сверх локального тега `v6.2.1`.
