# Исправление источников C2 — Codex, 31.10.2026

Прямое поручение владельца: «Исправь сам недоделки». База `614255e`, ветка `feature/p17-c5-shadow-normalize`. Это реализация и самопроверка, не независимая приёмка тем же агентом собственных правок.

Изменены только записи 22/113/171 в loss-labels и соответствующие строки 1292/1383/1441 общего sample (отсчёт от нуля). Единственное изменение значения метки — NOMAD 113: different → uncertain. Три URL карточек проверены GET: HTTP200, совпадают точные модели в h1, перенаправления на посторонний товар нет. Jabra PDF: HTTP200, сигнатура PDF и SHA256, таблица Products прочитана на странице 2. Детали — `source-checks.json`; журнал точечных изменений — `changes.json`.

- Philips: [карточка продавца](https://www.apltech.kz/tovar/438203/philips-27e2n1110-27e2n111001) содержит модель 27E2N1110 и партномер /01; same сохранено. Это подтверждение номенклатуры одной конкретной карточки; общего правила о региональных суффиксах производителя нет.
- NOMAD: [HSW](https://iplaza.kz/operativnaya-pamyat-16gb-ddr5-5600mhz-nomad-udimm-cl46-nmd5600d5u46-16gbhsw-heatsink-white) прямо указывает радиатор Heatsink White; [базовый модуль](https://www.apltech.kz/tovar/399655/nomad-nmd5600d5u46-nmd5600d5u46-16gb) описан как Bulk Pack. Второй источник не утверждает отсутствие радиатора, поэтому основание для different недостаточно и оставлено uncertain.
- Jabra: [официальный даташит RevC](https://www.jabra.com/_/media/Jabra_VXi_Product-Documentation/Jabra-Evolve2-65-Flex/Technical-specifications/RevC/ES-Evolve2-65-Flex-Tech-Specs-301024.pdf) различает 26699-999-889 с комплектом Wireless Charging и 26699-999-899; different сохранено, неработающие ссылки удалены из текущих меток. Исторические аудиты/передачи сохранены.

`update_labels.py` воспроизводит точечные изменения и сверяет их границы с git-срезом 614255e. Он не дополняет sample и не открывает БД. Версия sample 8 сохранена: корректируется существующая выборка, новая не создавалась. `apply_labels.py` исходного отбора повторно на текущем sample запускать нельзя: он предназначен для добавления новой выборки и защищён от дублей.

Проверено:

- Полное воспроизведение quality/registry из замороженного JSON C3: PASS, `report-replay.txt`, `quality.json`, `registry.json`.
- `./venv/bin/python docs/audit_evidence/2026-10-31-c2-source-fix/independent.py`: PASS, `independent.txt`, `independent-summary.json`. Скрипт независимо восстанавливает seed/порядок, разделение переписи и выборки, повторяет чистый merge на неизменяемом старом входе и арифметику всех четырёх сценариев без вызова основных функций фаз/взвешивания. Это отдельный вычислительный способ самопроверки, не отдельный аудитор.
- Текущие loss-метки 105 same / 155 different / 6 packaging / 14 uncertain; baseline 1336 true / 531 false / 102 open, candidate 2929 / 112 / 242. Нижняя граница истинных 1,838; верхняя ложных 1,092 > 1: C2 не подтверждён.
- Secrets gate и diff check: результаты в `secrets.txt`, `diff-check.txt`.

Основная команда пересчёта:

```sh
./venv/bin/python scripts/c2_quality_report.py --data docs/audit_evidence/2026-10-30-c3/stand-data.json --out docs/audit_evidence/2026-10-31-c2-loss-ext/quality.json --registry docs/audit_evidence/2026-10-31-c2-loss-ext/registry.json
```

Полный Python/frontend suite повторно не запускался: код приложения/интерфейса не менялся, проверены данные, источники и вычисления. Рабочие БД, модели, стенд/прод, настройки не менялись; реальных AI/SearXNG/Telegram нет. Коммит/push/merge/выпуск не выполнялись. Следующий шаг — независимая приёмка и дальнейшая работа с открытыми парами C2 по решению владельца.
