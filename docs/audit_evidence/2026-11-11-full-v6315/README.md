# Доказательства аудита 6.31.5

Код `c21ce39cf52be4802b16ecc57ef0d97cb52b6911`, образ стенда `28dea7aef96e71d3bd2dabe2912585e6d44a3cc6`. Снимки собраны 11.11.2026 в 17:14–17:19 по Алматы.

Из корня проекта, с зависимостями проекта и Google Chrome:

```sh
EVIDENCE=docs/audit_evidence/2026-11-11-full-v6315
PYTHONPATH=. DATA_DIR="$(mktemp -d)" venv/bin/python "$EVIDENCE/reproduce_api.py"
PYTHONPATH=. DATA_DIR="$(mktemp -d)" venv/bin/python "$EVIDENCE/reproduce_browser_share.py"
```

Обработчики, HTML и SQLite настоящие. Каталог и сессии синтетические. Bot API подменён, чтобы перехватить параметры карточки без отправки.

```text
same_title_in_catalog 2
fts_exact_result ['candidate1']
barcode_ambiguity_persisted auto_linked ['candidate1']
barcode_startup_repair 0 ['candidate1']
legacy_share 200 80000 https://example.invalid/almaty Астана
visible {'city': 'Алматы', 'offer': 'Астана', 'price': 130000} payload Алматы b02_astana_2@astana share 200 80000 https://example.invalid/almaty Алматы
```

- [Браузерные наборы](frontend.txt) — 39 из 39, Node 24.20.0, Playwright 1.63.0, Chrome.
- Python 3.14 — `pytest -q` 3 100 passed и 1 286 subtests, штатный `unittest discover` 3 028 OK, покрытие 83%. Целевой повтор прежних API-проб — 25 passed.
- [Стенд](stand.json) и [прод](production.json) — SQL-агрегаты в режиме чтения и сокращённая защищённая диагностика. Возраст означает 7 суток по `offers.observed_at`.
- [Поиск, Tompi и /deals](stand-search-and-share.json) — живой локальный AI и чтение каталога стенда. Отправка Telegram подменена.
- [Восстановление](restore.jsonl) — ночная БД во временном контейнере, девять запросов. Из архива конфигурации прочитаны только имена файлов.
- [Smoke](image-smoke.json) — отдельный экземпляр образа стенда без внешней сети и с временной БД. Обновление с 6.30.0 и откат прошли на синтетической базе, синтаксис и проверка секретов прошли, аудит проверил 25 Python-пакетов без пропусков и известных уязвимостей.

Рабочие токены, конфигурация, cookies и данные пользователей в доказательства не включены.
