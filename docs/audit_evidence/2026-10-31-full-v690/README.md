# Воспроизведение аудита d252243

Точный снимок, интерпретатор и временный DATA_DIR — в `snapshot.json`. Все приложения импортировались из отдельного `git archive d252243`, а не из общего checkout. Рабочие settings.json/prices.db не копировались. Значения реальных ключей не использовались.

Команды запуска из каталога archive (заменить значения в угловых скобках путями из snapshot.json):

```sh
DATA_DIR='<temp-python-data>' GEMINI_API_KEY='' OPENAI_API_KEY='' TELEGRAM_BOT_TOKEN='' '<venv-python>' -m unittest discover -s . -p 'test_*.py'
DATA_DIR='<temp-gate-data>' '<venv-python>' release_gate.py secrets --root .
DATA_DIR='<temp-probe-data>' PYTHONPATH='<archive>' GEMINI_API_KEY='' OPENAI_API_KEY='' TELEGRAM_BOT_TOKEN='' '<venv-python>' '<evidence>/reproduce.py'
DATA_DIR='<temp-critical-data>' PYTHONPATH='<archive>' '<venv-python>' docs/audit_evidence/2026-10-31-mod-reaccept/extra_checks.py
DATA_DIR='<temp-critical-data>' PYTHONPATH='<archive>' '<venv-python>' docs/audit_evidence/2026-10-31-mmr-accepted/concurrency.py
```

Frontend: каждый `tests/frontend/test_frontend*.cjs` запускался по очереди, с сохранением всех кодов выхода, а не остановкой на первом FAIL. `frontend-summary.json` — окончательные результаты. Runtime:

```sh
export NODE_PATH='/Users/molthun/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules'
export TEST_PYTHON='<venv-python>'
export DATA_DIR='<temp-browser-data>'
# Из archive, для каждого файла:
'/Users/molthun/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node' tests/frontend/test_frontend_p12.cjs
# Независимый browser probe с реальным JSON monitoring:
'/Users/molthun/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node' '<evidence>/browser-probe.cjs' '<archive>'
```

`monitoring-response.json` получен после `database.init_db()` на отдельном временном DATA_DIR через `monitoring.overview({}, [], {}, 3600)`. Проверки `PRAGMA integrity_check` и `PRAGMA foreign_key_check` относятся к этой временной БД. `browser-probe.cjs` подменяет HTTP офлайн, использует этот настоящий JSON и исходный HTML проверяемого SHA. Фикстуры Discovery синтетические. Live-модели, CDN, магазины, Telegram и Instagram в probe не вызываются.

`frontend-baseline.txt` — только два ранее падающих сценария на archive b2c5edd в том же окружении. Общая регрессия базы повторно не заявляется.

`reproduce.py` завершается успешно, когда подтверждает наличие дефектов. Для повторной приёмки нужны противоположные утверждения желаемого поведения, а не требование сохранить exit 0 этого диагностического скрипта.

GitHub: `workflow-runs.json`, `publish-jobs.json`, `tag-publish-jobs.json`, `ci-frontend-excerpt.txt` — чтение выполненных запусков d252243. Выдержка CI ограничена относящимися к браузерной ошибке строками. Пустые combined statuses не использованы как итог проверки. `remote-refs.txt` — read-only ls-remote. `public-state.json` — публичные GET /api/version, только наличие DIAG_TOKEN как bool; самого токена в окружении нет.
