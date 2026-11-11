# Воспроизведение аудита 6.31.0

Срез `9bd34ae924c4f090788f5d6ab64bc0ac6449becc`. Запускать из корня проекта с Python 3.14 и установленными зависимостями проекта. Скрипт браузера использует Playwright и Google Chrome.

```sh
EVIDENCE=docs/audit_evidence/2026-11-11-full-v6310
PYTHONPATH=. DATA_DIR="$(mktemp -d)" venv/bin/python "$EVIDENCE/reproduce_api.py"
venv/bin/python "$EVIDENCE/reproduce_scanner.py"
```

Для API используется новый временный каталог на каждый запуск. Скрипт подставляет тестовый Origin, синтетическую выдачу и результат адаптера Telegram. Маршруты, middleware и запись в SQLite выполняются кодом проекта. Камера подменена синтетическим видеопотоком из Canvas.

## Зафиксированный результат

```text
prepared_anonymous_closed 200 called 1 for_supplied_user True
prepared_other_user 200 for_supplied_user True
barcode_anonymous_nonexistent 200 persisted True
barcode_search_wrong_variant 200 advertised True persisted True
barcode_unicode_search 500
privacy_export_fields False False remaining_topic_and_feedback [1, 1]
anonymous_deep_link_duplicate [200, 200, 200] (3 rows, 1 distinct payload)
ean=4870237159941 reversedEan=4870237159941
openAfterClose=true modalIsOpen=false lateTrackState=live hiddenCameraAttached=true
```

- `pytest -q`: 3 070 passed, 2 failed, 1 286 subtests passed.
- `unittest discover` под coverage: 3 000 тестов, два падения, покрытие 83%. Имена падающих тестов приведены в отчёте.
- [Браузерный прогон](frontend.txt): один из 37 наборов завершился кодом 1. Отдельный тест Mini App завершился успешно.
- `release_gate.py upgrade` и `rollback`: проверка запросов обоих релизов и целостности прошла на временной БД.
- `release_gate.py secrets`: [результат](secrets.json). `pip-audit`: 25 пакетов, 0 известных уязвимостей и 0 пропусков.
- SQL стенда выполнялся с `mode=ro` и `query_only=ON`. Для `4870237159941` было четыре канонических карточки, включая две карточки с противоположными признаками аромата. Данные пользователей и секреты в доказательства не включены.

[Полный отчёт](../../FULL_AUDIT_2026-11-11_V6310_CODEX.md)
