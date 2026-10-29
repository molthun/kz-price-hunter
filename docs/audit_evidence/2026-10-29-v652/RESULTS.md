# Свидетельства 6.5.2 (Claude, 2026-10-29)

## Прод 6.5.1, только чтение (SSH ai_agent на NAS)

- NAS: i3-7100T, 16 ГБ; /volume1 — один SATA-HDD (md2) + NVMe-кэш. load 19,6–26,8, из них IO 18,4–25,3.
  md2: ~7,7 тыс. записей и ~230 МБ за 10 с, занят ~98 % времени; владелец нашёл запущенную дедупликацию.
- prices.db 2,2 ГБ, WAL 561 МБ. telemetry_http_samples max(rowid) 609 453 328.

## Сброс телеметрии (`bench_telemetry_wal.py`, macOS, временная БД)

20 хостов, выборки minute/hour/day заполнены до 500, затем 10 сбросов по 50 запросов на хост:

| Версия | Прирост WAL | Строк в telemetry_http_samples | max(rowid) |
|---|---|---|---|
| 6.5.1 | 6,6 МБ | 30 000 | 456 000 |
| 6.5.2 | 2,4 МБ | 0 | — |

Запуск: `./venv/bin/python docs/audit_evidence/2026-10-29-v652/bench_telemetry_wal.py` (6.5.1 — через `git stash`
изменений telemetry.py и database.py).
