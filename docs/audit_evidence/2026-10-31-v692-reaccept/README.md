# Приёмка 8d25998 (6.9.2)

Точный archive, интерпретатор, временные пути и SHA — snapshot.json. Продуктовый код импортировался из отдельного git archive 8d259981a4e405682910c62982ac7958dba1ee91, не из общего checkout. Все рабочие данные синтетические/временные; AI/Telegram реальные ключи отключены.

Основные команды из archive, с путями из snapshot.json:

```sh
DATA_DIR='<temp-suite>' GEMINI_API_KEY='' OPENAI_API_KEY='' TELEGRAM_BOT_TOKEN='' '<venv-python>' -m unittest discover -s . -p 'test_*.py'
DATA_DIR='<temp-gates>' '<venv-python>' release_gate.py secrets --root .
DATA_DIR='<temp-independent>' PYTHONPATH='<archive>' GEMINI_API_KEY='' OPENAI_API_KEY='' TELEGRAM_BOT_TOKEN='' '<venv-python>' docs/audit_evidence/2026-10-31-v691-reaccept/independent.py
DATA_DIR='<temp-pin>' PYTHONPATH='<archive>' '<venv-python>' '<evidence>/pin_readback.py'
DATA_DIR='<temp-lock>' PYTHONPATH='<archive>' '<venv-python>' '<evidence>/social_lock.py'
DATA_DIR='<temp-edge>' PYTHONPATH='<archive>' '<venv-python>' '<evidence>/edge_checks.py'
```

- python-suite.txt: 2285 OK, 139.135 с. frontend-summary.json: 21/21 PASS. Каждый tests/frontend/test_frontend*.cjs запущен отдельно/последовательно. Node=/Users/molthun/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node; NODE_PATH=/Users/molthun/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules; TEST_PYTHON=<venv-python>, отдельный временный DATA_DIR. HTTP в browser fixtures подменён.
- independent-original.txt: прежний independent.py без изменения, 11 OK.
- pin-original.txt: прежний pin_race.py без изменения, 1 OK/1 FAIL. Оставшийся FAIL читает старый product_id из объекта до operator decision.
- pin_readback.py: копия того же файла с заменой только финального SELECT — join offers→canonical_products по стабильному offer.id. Два сценария OK. Исторический скрипт не редактировался.
- social_lock.py: один настоящий второй поток/соединение. Trace подтверждает, что оператор дошёл до BEGIN IMMEDIATE и ждёт social commit; затем настоящий DIFFERENT переносит строку. 1 OK.
- edge_checks.py: 4 унаследованных OperatorDecisionTests OK + 3 независимых новых FAIL. Свидетельства SAME/равная секунда, /100 г и /1 кг, физические байты при старом WAL-reader. Это desired-behavior assertions: exit 1 означает продуктовые остатки, а не требование сохранить дефект.
- api.txt: прежний api_checks.py, 9 OK (6 наследованных + 3 независимых). moderation.txt/durability.txt: прежние extra_checks.py/concurrency.py — 4+5 OK. Все пути и зависимости присутствуют внутри archive.

upgrade-scrub.txt: отдельно git archive 556ddf3, database.init_db и синтетическая старая content_observation. Согласованная копия создана SQLite backup API в другой временный DATA_DIR. Новый код init_db добавил таблицу social_offer_titles; scrub dry-run/apply/idempotence, integrity/FK и отсутствие маркера в файле после закрытия соединений прошли. Это offline-позитивный сценарий. Негативный WAL-reader проверен отдельно в edge_checks.py на ещё одной временной БД. Скрипт с --apply никогда не запускался на рабочей БД и существующих бэкапах.

release-state.json: read-only ls-remote и публичные GET версий; DIAG_TOKEN отсутствует. CI/publish, Docker и независимый pip-audit в этом проходе не выполнялись.
