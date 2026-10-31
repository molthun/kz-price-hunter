# Приёмка 74b80a0 / 6.9.3

snapshot.json фиксирует SHA, отдельный git archive и временные DATA_DIR. Приложение импортируется из archive, не из изменяемого checkout. Python — venv проекта, реальные AI/Telegram ключи отключены. Рабочие БД/бэкапы/настройки не копировались и не очищались.

Основные команды из archive, с путями из snapshot.json:

```sh
DATA_DIR='<temp-suite>' GEMINI_API_KEY='' OPENAI_API_KEY='' TELEGRAM_BOT_TOKEN='' '<venv-python>' -m unittest discover -s . -p 'test_*.py'
DATA_DIR='<temp-gates>' '<venv-python>' release_gate.py secrets --root .
DATA_DIR='<temp-acceptance>' PYTHONPATH='<archive>' '<venv-python>' '<evidence>/acceptance.py'
```

python-suite.txt: 2302 OK, 141.107 с. frontend-summary.json: 21/21 PASS. Каждый tests/frontend/test_frontend*.cjs выполнен отдельно/последовательно. Node: /Users/molthun/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node; NODE_PATH: /Users/molthun/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules; TEST_PYTHON=<venv-python>. Браузеры Chrome headless, внешний HTTP в fixtures подменён.

Исторические независимые скрипты, все из archive и без правок:

- docs/audit_evidence/2026-10-31-v691-reaccept/independent.py → independent.txt, 11 OK;
- docs/audit_evidence/2026-10-31-v693-audit/small_units.py → small-units.txt, 2 OK;
- docs/audit_evidence/2026-10-31-v692-reaccept/pin_readback.py → pin-readback.txt, 2 OK;
- docs/audit_evidence/2026-10-31-v692-reaccept/social_lock.py → social-lock.txt, 1 OK;
- docs/audit_evidence/2026-10-31-v691-reaccept/api_checks.py → api.txt, 9 OK;
- docs/audit_evidence/2026-10-31-v693-audit/scrub_integrity.py → scrub-original.txt, 1 OK/1 FAIL;
- docs/audit_evidence/2026-10-31-v692-reaccept/edge_checks.py → edge-original.txt, 6 OK/1 FAIL.

Последние два FAIL — прежнее требование немедленного отсутствия удерживаемых страниц при старом читателе. Они не проверяют physical_complete/reason, хотя разрешённый контракт допускает отличимую незавершённость. Исторические файлы не редактировались и их exit 1 не выдан за pass.

Новая независимая acceptance.py: **5 OK**. Проверяет каталог/разрешённые поля, overflow/base64, SQLite backup API, dry-run, действующий старый WAL-reader, результат physical_complete=False/reason=wal_busy, повторный запуск после rollback с нулём грязных строк и physical_complete=True. Дополнительно реальные дочерние CLI-процессы возвращают 3→0; timeout SQLite уменьшен до 0.05 с только для теста. OCR малых граммовых количеств и отрицательный телефонный 5G проверены без реальной модели.

Все данные синтетические. В файлы SQLite аудит напрямую не пишет: пользуется SQL и backup API. release-state.json — только read-only refs и публичные GET версий; DIAG_TOKEN отсутствует. CI/publish/Docker и независимый pip-audit в этом проходе не выполнялись.
