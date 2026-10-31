# Проверка незакоммиченного дерева 6.9.3

Срез: HEAD 8d259981a4e405682910c62982ac7958dba1ee91 + исходная рабочая дельта. Это не голый git archive HEAD: после archive наложены изменённые tracked-файлы и неигнорируемые untracked-файлы, включая предыдущие аудиторские сценарии. Исходные изменения не откатывались. snapshot.json, initial-delta.patch и worktree-manifest.json фиксируют точный вход; копия исполняемого кода неизменна во время тестов.

Все DATA_DIR временные, реальные AI/Telegram ключи отключены. SQLite/WAL/читатели создавались на синтетических данных. Даже опасная побайтовая логика проверяемого scrub вызывалась только внутри этих временных БД. Продуктовый код не исправлялся.

Команды из каталога замороженной копии, с путями из snapshot.json:

```sh
DATA_DIR='<temp-suite>' GEMINI_API_KEY='' OPENAI_API_KEY='' TELEGRAM_BOT_TOKEN='' '<venv-python>' -m unittest discover -s . -p 'test_*.py'
DATA_DIR='<temp-gates>' '<venv-python>' release_gate.py secrets --root .
DATA_DIR='<temp-scrub>' PYTHONPATH='<snapshot>' '<venv-python>' '<evidence>/scrub_integrity.py'
DATA_DIR='<temp-units>' PYTHONPATH='<snapshot>' '<venv-python>' '<evidence>/small_units.py'
```

- python-suite.txt: 2294 OK, 145.604 с. frontend-summary.json: все 21 tests/frontend/test_frontend*.cjs прошли последовательно, каждый с собственным exit code.
- Node: /Users/molthun/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node. NODE_PATH: /Users/molthun/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules. TEST_PYTHON: venv из snapshot.json. HTTP в browser fixtures подменён.
- edge-original.txt: неизменённый docs/audit_evidence/2026-10-31-v692-reaccept/edge_checks.py, 7 OK.
- independent-original.txt: неизменённый docs/audit_evidence/2026-10-31-v691-reaccept/independent.py, 11 OK.
- pin-readback.txt и social-lock.txt: неизменённые pin_readback.py/social_lock.py из свидетельств v692, 2+1 OK; актуальное чтение по offer.id и настоящий второй писатель.
- scrub_integrity.py: 2 FAIL на desired-behavior — сохранность разрешённого catalog title, отсутствие фрагмента overflow-поста при старом WAL-reader. Независимый новый SQL-сеанс перечитывает каталог после scrub/закрытия первой сессии. PRAGMA integrity_check=ok не маскирует проверку значения.
- small_units.py: 1 FAIL на двух явных ценах /1g и /5g, 1 OK на standalone телефонном 5G. Настоящие ingestion/repositories/SearchCore, подменён только сетевой коннектор постов.

Новые скрипты ожидают желаемое поведение; exit 1 подтверждает замечания. Они не требуют сохранять дефект. Результаты содержат только синтетические данные.

release-state.json: только read-only refs и GET /api/version; DIAG_TOKEN отсутствует. Рабочие записи/бэкапы не читались, скрипт очистки на них не выполнялся. Docker, CI/publish, реальные провайдеры и независимый pip-audit вне этого прохода.
