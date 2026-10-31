# Исправление SF1-R01 и остатков SF1-04/06/07/08/09/10 — свидетельства

Фикстуры синтетические, это не реальный каталог; стенд и прод не использовались.

- `real_dto_probe.py`, `real-dto.json`: Chrome на настоящих DTO SearchCore (SQLite :memory:, история через
  HistoryRepository), сеть перехвачена. Повторный фильтр, фасеты, условия предложения, история.
- PNG: выдача и товар на 1366 и 390 px в обеих темах, мобильная панель фильтров в обеих темах.
- Поведенческий набор: `tests/frontend/test_frontend_search_first.cjs` (360, 390, 430, 768, 1366 px).

```sh
PYTHONDONTWRITEBYTECODE=1 ./venv/bin/python docs/audit_evidence/2026-10-31-search-first-stage1-fix/real_dto_probe.py
```
