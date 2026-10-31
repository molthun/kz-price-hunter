# Свидетельства независимого аудита Search Platform 2.0

Проверяемый SHA: `7b689dd5d1f58575f6e2199a70b9b6927f25bb32`.
Отчёт: [AUDIT_2026-10-31_SEARCH_PLATFORM_30_CODEX.md](../../AUDIT_2026-10-31_SEARCH_PLATFORM_30_CODEX.md).

Все БД синтетические, временные. Исходники запускались из неизменяемого git archive; рабочая `prices.db` не читалась и не менялась. Пути временного архива в `snapshot.json` относятся к этой машине и могут быть удалены позднее; для повторения создайте новый `git archive` указанного SHA.

- `snapshot.json`, `environment.json` — SHA, окружение и изоляция.
- `python-suite.txt` — полный regression log, 2361 OK / 144,986 с.
- `frontend-summary.json`, `frontend.txt` — 21 browser suite, все exit=0.
- `independent.py`, `independent.json`, `independent.stderr` — история, доступные цены, Discovery, бренд, активность и достоверность verdict. Float NaN при сохранении JSON заменён строкой, перед оценкой оставался настоящим NaN.
- `behavior-checks.json` — ожидаемые свойства и фактическое выполнение; false означает воспроизведённый пробел, true существующего writer — положительный контроль. Это отдельная проверка, не часть 2361 теста.
- `render-contract.cjs`, `render-contract.json` — настоящий DTO SearchCore → архивный UI в Chrome. Внутри группы отсутствуют плашка Kaspi и ссылка магазина; общие ссылки ниже существуют, JS errors=[].
- `scale_probe.py`, `scale-100k.json`, `scale-100k.stderr` — исходный benchmark 100k и SQL/dbstat-проверка валидности и размеров. p95=2299,90 мс, 99 900 неверных seller/channel связей.
- `scale-valid-fixture.py`, `scale-valid-100k.json`, `scale-valid-100k.stderr` — повтор 100k, исправление только тестовых связей перед warmup через audit wrapper. p95=2334,64 мс; сохранены запросы/outcome/latency. Продуктовые файлы не менялись.
- `stand.json` — read-only HTTPS, статус/версия/имена полей, hashes шаблонов. Не содержит полного каталога, данных людей, cookies или токенов. Ошибочный URL мониторинга с 404 сохранён вместе с корректным 401, не интерпретируется как дефект.
- `secrets.json` — исходный release gate PASS / 0 findings.
- `final-checks.json` — заключительная проверка неизменности SHA, diff и границ аудиторских изменений.

Воспроизведение: из корня нового архива, с `PYTHONPATH` на этот корень и новым временным `DATA_DIR`, запустить Python-скрипты указанным в `environment.json` venv-интерпретатором. Секреты провайдеров не нужны. Для Node-скрипта нужны Chrome и Playwright, `NODE_PATH` указан в окружении; адаптируйте путь `snapshot.json` к новому архиву. Stand-проверки не изменяли данные или настройки.
