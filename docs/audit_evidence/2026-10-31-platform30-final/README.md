# Финальная фиксация приёмки

SHA: `5a29e79ea5bb6616edd5632e6e5f40fd81386359`. Кодовый коммит — `e808d88`.
[Решение и известный P2 SP30-F01](../../ACCEPTANCE_2026-10-31_SP30_FINAL_CODEX.md).

- `snapshot.json` — git archive и изолированные временные данные; при повторении заменить пути новым архивом этого SHA.
- `python-suite.txt`, `python-result.json` — **2369 OK**, 142,470 с.
- `frontend-summary.json`, `frontend.txt` — **21/21 PASS**, Chrome/Playwright.
- `scale-edges.py/json` — повтор исходных невалидных метрик: NOT_MEASURED / false.
- `fallback-metrics.py/json` — отказ dbstat/resource: estimated / null, без выдуманных 45 МБ.
- `monitor-verdict.cjs/json` — настоящий renderer показывает нейтральную неизвестность/красную ошибку; для отсутствующего scale_level прежний HEALTHY_SQLITE не найден. Строка «НЕТ ДАННЫХ» дополнительно проверена в коде шаблона.
- `scale_probe.py`, `scale-100k.json` — p95 12,92 мс, 0 ошибочных связей, история с индексами соответствует dbstat.
- `concurrency-overlap.py/json` — инструментирование времён с задержкой worker 250 мс: verified=true при отсутствии overlap поиска и записи с backup. Остаток SP30-F01, не доказательство повреждения БД.
- `behavior-checks.json` — свойства исправлений и явно не выполненное свойство конкурентного теста.
- `stand.json` — SSH HEAD, healthy, SHA-256 трёх изменённых продуктовых файлов, HTTP-коды/версия без каталога, cookies и ключей.
- `secrets.json` — PASS / 0 findings.
- `final-checks.json` — неизменность SHA и продуктового кода, форматирование и разбор свидетельств.

Все Python-сценарии запускаются из архивного source с PYTHONPATH на этот каталог, новым временным DATA_DIR и venv-интерпретатором из snapshot.json; ключи провайдеров не нужны. Node использует Chrome/Playwright и NODE_PATH из environment.json предыдущего аудита `../2026-10-31-platform30-reaccept/`. Существующая рабочая prices.db не читалась/не менялась; архив временный и его пути могут перестать существовать. Продуктовые файлы обёртками аудита не редактировались. Приёмка с известным P2 не означает прохождение всех масштабов и не запускает публикацию.
