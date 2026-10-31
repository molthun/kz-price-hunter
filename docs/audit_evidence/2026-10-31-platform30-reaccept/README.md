# Свидетельства повторной приёмки SP30-01…09

Срез: `96f42ddc31e440540440e87260aed8507ebad94f`.
Итог: **7 закрыты, SP30-04/06 частично исправлены (2 P2), открытых P1 нет**.
[Отчёт](../../REACCEPTANCE_2026-10-31_SP30_CODEX.md).

- `snapshot.json`, `environment.json` — архив проверяемого коммита, временные DATA_DIR и runtime. Для повторения создать новый git archive этого SHA; сохранённые временные пути могут перестать существовать.
- `python-suite.txt`, `python-result.json` — **2369 OK**, 144,239 с, exit=0.
- `frontend-summary.json`, `frontend.txt` — **21/21 PASS**, Chrome/Playwright.
- `independent.py/json/stderr` — копия исходных диагностических сценариев на новом коде. Скрипт печатает факты, его exit=0 не эквивалентен утверждению «все критерии выполнены».
- `history-rerecord.py/json/stderr` — все три writer-path, повторная запись той же точки после мутации оффера и чтение модели; assertions PASS.
- `render-contract.cjs/json`, `render.stderr` — реальный DTO → архивный HTML; внутри группы ссылка и Kaspi есть, JS errors=[].
- `scale_probe.py`, `scale-100k.json/stderr` — 100k: p95=12,73 мс; исправленные связи и фактический dbstat; PK истории по-прежнему не включён в сообщаемый размер.
- `scale-without-analyze.py/json/stderr` — независимый контроль без дополнительного ANALYZE: p95=12,87 мс. Отключение только в обёртке аудита, продуктовые файлы не менялись.
- `scale-edges.py/json` — NOT_MEASURED с положительным boolean; STAY_ON_SQLITE при NaN иных метрик и неполной контрольной точке.
- `fallback-metrics.py/json/stderr` — управляемый отказ dbstat/resource; размеры остаются непомеченными оценками, memory_mb становится выдуманными 45,0.
- `monitor-verdict.cjs/json/stderr` — реальный renderer мониторинга: зелёная галочка при NOT_MEASURED/ERROR_NO_SCHEMA; HEALTHY_SQLITE при отсутствии scale_level.
- `behavior-checks.json` — ожидаемые независимые свойства; false обозначает оставшийся пробел. Это отдельно от проходящей регрессии.
- `stand.json` — публичные HTTPS-коды, версия 6.9.3, совпадение HTML, имена полей без сохранения каталога/личных данных.
- `stand-ssh.json` — отказ SSH на публичном имени, не отказ HTTPS.
- `stand-ssh-internal.json` — read-only внутренний маршрут: HEAD 96f42dd, running healthy, совпадение SHA-256 восьми изменённых Python-файлов контейнера.
- `secrets.json` — release gate PASS, 0 findings.
- `final-checks.json` — неизменность SHA, границ изменений и исходников в архиве, форматирование, разбор JSON/скриптов.

Воспроизведение Python: из корня нового архива, `PYTHONPATH` на архив, новый временный `DATA_DIR`, venv-интерпретатор из environment.json; запустить скрипт по абсолютному пути. Ключи провайдеров не нужны. Для Node нужны Chrome и Playwright с NODE_PATH из environment.json; пути snapshot.json адаптировать к новому архиву. Browser fixtures не подтверждают реальные внешние сервисы. SSH-проверки только читали SHA/статус/хеши файлов, данных и настроек не меняли.
