#!/usr/bin/env python3
"""
Перерасчёт моделей видеокарт, у которых разбор потерял суффикс варианта (RX 9070 GRE → RX 9070).

Безопасность (аудит v6.28.6, 05.11.2026):
- по умолчанию только отчёт (dry-run); запись — с флагом --apply и только на копии/стенде по протоколу;
- canonical_products.model меняется, только если новый разбор названия лишь ДОБАВЛЯЕТ суффикс к
  текущему значению (`Radeon RX 9070` → `Radeon RX 9070 GRE`). Любое другое расхождение не трогается
  и попадает в отчёт как «пропущено» — там могут быть ручные решения или другие правила;
- ID, категория, цены, предложения и история не меняются; обновляются только model и updated_at;
- значения атрибута chipset в product_search_attributes приводятся к тому же разбору (value),
  основание (evidence) и привязка к названию сохраняются;
- запись короткими пачками; в отчёте старые значения для отката.

Запуск из корня репозитория:
    python scripts/recompute_gpu_models.py --db stand-data/prices.db --report /tmp/gpu-models.json
    python scripts/recompute_gpu_models.py --db stand-data/prices.db --report /tmp/gpu-models.json --apply
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from catalog_classifier import canonical_gpu_model  # noqa: E402
from domain.attribute_indexing import normalize_attribute_value  # noqa: E402

BATCH = 200


def plan_changes(conn: sqlite3.Connection) -> Dict[str, List[Dict[str, Any]]]:
    """Что изменится: только уточнение суффиксом; остальные расхождения — в пропущенные."""
    models: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for pid, title, model in conn.execute(
            "SELECT id, title, model FROM canonical_products WHERE COALESCE(model, '') != '' AND title IS NOT NULL"):
        current = model.strip()
        if canonical_gpu_model(current) is None:
            continue                              # не модель видеокарты
        new = canonical_gpu_model(title)
        if not new or new == current:
            continue
        if new.lower().startswith(current.lower() + " "):
            models.append({"id": pid, "title": title, "old": model, "new": new})
        else:
            skipped.append({"id": pid, "title": title, "model": model, "parsed": new})

    chipsets: List[Dict[str, Any]] = []
    for pid, value in conn.execute("SELECT product_id, value FROM product_search_attributes WHERE key = 'chipset'"):
        new = normalize_attribute_value("chipset", value)
        if new and new != value:
            chipsets.append({"id": pid, "old": value, "new": new})
    return {"models": models, "chipsets": chipsets, "skipped": skipped}


def apply_changes(conn: sqlite3.Connection, plan: Dict[str, List[Dict[str, Any]]]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    for i in range(0, len(plan["models"]), BATCH):
        with conn:
            for row in plan["models"][i:i + BATCH]:
                conn.execute("UPDATE canonical_products SET model = ?, updated_at = ? WHERE id = ? AND model = ?",
                             (row["new"], now, row["id"], row["old"]))
    for i in range(0, len(plan["chipsets"]), BATCH):
        with conn:
            for row in plan["chipsets"][i:i + BATCH]:
                conn.execute("UPDATE product_search_attributes SET value = ? "
                             "WHERE product_id = ? AND key = 'chipset' AND value = ?",
                             (row["new"], row["id"], row["old"]))


def main() -> int:
    parser = argparse.ArgumentParser(description="Перерасчёт моделей видеокарт с потерянным суффиксом")
    parser.add_argument("--db", required=True, help="Путь к SQLite (копия или стенд)")
    parser.add_argument("--report", required=True, help="Куда записать JSON-отчёт со старыми значениями")
    parser.add_argument("--apply", action="store_true", help="Записать изменения (без флага — только отчёт)")
    args = parser.parse_args()

    if not Path(args.db).exists():
        print(f"База {args.db} не найдена", file=sys.stderr)
        return 1
    conn = sqlite3.connect(args.db, timeout=30)
    plan = plan_changes(conn)
    if args.apply:
        apply_changes(conn, plan)
    conn.close()

    summary = {"db": args.db, "applied": args.apply, "models": len(plan["models"]),
               "chipsets": len(plan["chipsets"]), "skipped": len(plan["skipped"])}
    Path(args.report).write_text(json.dumps({**summary, **plan}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
