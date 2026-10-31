# Повторная приёмка 556ddf3

Срез и временные пути — `snapshot.json`. Исполняемый код взят из `git archive 556ddf38d4a3ca8531301c0cf0a76338c6f6d2cc`; все DATA_DIR временные. Рабочие БД и settings.json не копировались. AI/Telegram реальные ключи отключены. Node/Playwright — bundled runtime, Chrome headless.

Запуски из каталога archive (подставить пути из snapshot.json):

```sh
DATA_DIR='<temp-suite>' GEMINI_API_KEY='' OPENAI_API_KEY='' TELEGRAM_BOT_TOKEN='' '<venv-python>' -m unittest discover -s . -p 'test_*.py'
DATA_DIR='<temp-gates>' '<venv-python>' release_gate.py secrets --root .
DATA_DIR='<temp-independent>' PYTHONPATH='<archive>' GEMINI_API_KEY='' OPENAI_API_KEY='' TELEGRAM_BOT_TOKEN='' '<venv-python>' '<evidence>/independent.py'
DATA_DIR='<temp-pin>' PYTHONPATH='<archive>' GEMINI_API_KEY='' OPENAI_API_KEY='' TELEGRAM_BOT_TOKEN='' '<venv-python>' '<evidence>/pin_race.py'
DATA_DIR='<temp-api>' PYTHONPATH='<archive>' GEMINI_API_KEY='' OPENAI_API_KEY='' TELEGRAM_BOT_TOKEN='' '<venv-python>' '<evidence>/api_checks.py'
```

Frontend: каждый `tests/frontend/test_frontend*.cjs` последовательно, с отдельным кодом выхода в frontend-summary.json. Окружение: TEST_PYTHON=<venv-python>, NODE_PATH=/Users/molthun/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules; Node=/Users/molthun/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node. Все 21 набора прошли. Внешние HTTP в browser fixtures подменены; Discovery использует настоящий JSON monitoring.overview и реальные правила candidate_actions, полученные локальным Python на временной БД.

Прежние критические сценарии запускались с PYTHONPATH=<archive> и отдельными DATA_DIR:

- `docs/audit_evidence/2026-10-31-mod-reaccept/extra_checks.py` → moderation.txt, 4 OK;
- `docs/audit_evidence/2026-10-31-mmr-accepted/concurrency.py` → durability.txt, 5 OK.

`independent.py` и `pin_race.py` утверждают желаемое поведение. Их exit 1 и FAIL/ERROR — подтверждённые остатки, а не сбои настройки окружения. independent.py: 4 OK, 6 FAIL, 1 ERROR. pin_race.py: 2 FAIL — решение оператора после social-save и управляемое чередование через два настоящих соединения после чтения идентичности/перед записью. Это не многопоточный stress-test. Оператор вызывается настоящей database.resolve_disputed_pair_human, без подмены решения.

Сценарий контента использует синтетический текст и base64, не чужие реальные изображения/описания. SQLite backup проверяется между двумя временными in-memory БД; не затрагивает реальные бэкапы. Сценарий медиа с подписью подменяет fetch_image и ответ router, затем исполняет настоящие text parser/vision validator/ingest/repositories/SearchCore; это проверка слияния результатов, а не качества модели.

api_checks.py: 6 унаследованных тестов TestSellerDiscoveryApi плюс 3 независимых — review/права/сохранение статуса, domain/total/offset, social-история в настоящем history API. Все 9 OK. HTTP-сервер локальный с временной SQLite; admin identity подменена в тестовом middleware.

release-state.json — read-only ls-remote и публичные GET версий. DIAG_TOKEN отсутствует; его содержимого нигде нет. CI/публикация нового SHA не запускались. Локальный Docker и независимый pip-audit в этом проходе не выполнялись.
