#!/usr/bin/env python3
"""
Перерасчёт моделей видеокарт, у которых разбор потерял суффикс варианта (RX 9070 GRE → RX 9070).

Безопасность (аудиты v6.28.6 и v6.28.7, 05.11.2026):
- по умолчанию только отчёт (dry-run); запись — с флагом --apply и только на копии/стенде по протоколу;
- путь отчёта не может указывать на базу или её служебные файлы (-wal, -shm, -journal), в том числе
  через эквивалентный путь, symlink или жёсткую ссылку, и с учётом symlink в пути самой базы —
  проверка до любого открытия на запись;
- существующий отчёт не перезаписывается: после partial повторный запуск пишет новый отчёт, и вместе
  они хранят старые значения всех строк;
- план со старыми значениями записывается в отчёт атомарно ДО первого изменения базы: если отчёт
  записать нельзя, база не меняется; после записи отчёт дополняется фактически выполненными
  изменениями, а при ошибке на середине — статусом partial и списком применённого;
- canonical_products.model меняется, только если новый разбор названия лишь ДОБАВЛЯЕТ суффикс к
  текущему значению (`Radeon RX 9070` → `Radeon RX 9070 GRE`); прочие расхождения — «пропущено»;
- chipset: выведенный из названия (evidence «Название товара») пересчитывается по названию;
  факт источника только нормализуется тем же разбором, противоречие модели — в «конфликты»,
  без изменения;
- ID, категория, цены, предложения и история не меняются; запись короткими пачками.

Запуск из корня репозитория:
    python scripts/recompute_gpu_models.py --db stand-data/prices.db --report /tmp/gpu-models.json
    python scripts/recompute_gpu_models.py --db stand-data/prices.db --report /tmp/gpu-models.json --apply
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from catalog_classifier import canonical_gpu_model  # noqa: E402
from domain.attribute_indexing import EVIDENCE_TITLE, extract_title_attributes, normalize_attribute_value  # noqa: E402

BATCH = 200
DB_SIDE_FILES = ("", "-wal", "-shm", "-journal")


class ReportPathError(ValueError):
    pass


def _database_files(db: str) -> List[str]:
    """Файлы, которые SQLite использует для базы: по пути из аргумента И по фактическому пути
    после разрешения symlink (именно его открывает соединение) — с -wal/-shm/-journal (G1)."""
    names = []
    for base in (db, os.path.realpath(db)):
        for side in DB_SIDE_FILES:
            names.append(base + side)
    return list(dict.fromkeys(names))


def check_report_path(db: str, report: str) -> None:
    """Отчёт не должен совпадать с базой или её служебными файлами ни по пути, ни по файлу,
    и не должен уже существовать: прежний отчёт — единственная копия старых значений (G2)."""
    report_path = Path(report)
    if report_path.is_dir():
        raise ReportPathError(f"Путь отчёта — каталог: {report}")
    report_real = os.path.realpath(report)
    for target in _database_files(db):
        if os.path.realpath(target) == report_real:
            raise ReportPathError(f"Отчёт совпадает с файлом базы: {target}")
        if report_path.exists() and os.path.exists(target) and os.path.samefile(report, target):
            raise ReportPathError(f"Отчёт — тот же файл, что {target} (ссылка)")
    if report_path.exists() or report_path.is_symlink():
        raise ReportPathError(f"Отчёт {report} уже существует: прежние значения в нём не перезаписываются, "
                              "укажите новый путь")


def write_report(path: str, data: Dict[str, Any], create: bool = False) -> None:
    """Атомарная запись через временный файл в том же каталоге. create=True — только если файла
    ещё нет (os.link не заменяет существующий файл), иначе os.replace своего же отчёта."""
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".recompute-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
            fh.flush()
            os.fsync(fh.fileno())
        if create:
            os.link(tmp, path)          # FileExistsError, если отчёт появился после проверки
        else:
            os.replace(tmp, path)
            tmp = None
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)


def plan_changes(conn: sqlite3.Connection) -> Dict[str, List[Dict[str, Any]]]:
    """Что изменится: только уточнение суффиксом; остальные расхождения — в пропущенные/конфликты."""
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
    conflicts: List[Dict[str, Any]] = []
    for pid, value, evidence, title in conn.execute(
            "SELECT pa.product_id, pa.value, pa.evidence, p.title FROM product_search_attributes pa "
            "JOIN canonical_products p ON p.id = pa.product_id WHERE pa.key = 'chipset'"):
        if evidence == EVIDENCE_TITLE and title:
            new = extract_title_attributes(title).get("chipset") or normalize_attribute_value("chipset", value)
        else:
            new = normalize_attribute_value("chipset", value)
            parsed = canonical_gpu_model(title or "")
            if new and parsed and new != parsed:
                conflicts.append({"id": pid, "title": title, "chipset": value, "evidence": evidence, "parsed": parsed})
        if new and new != value:
            chipsets.append({"id": pid, "old": value, "new": new, "evidence": evidence})
    return {"models": models, "chipsets": chipsets, "skipped": skipped, "conflicts": conflicts}


def apply_changes(conn: sqlite3.Connection, plan: Dict[str, List[Dict[str, Any]]]) -> Dict[str, List[str]]:
    """Пишет пачками; возвращает ID фактически изменённых строк. Исключение несёт уже применённое."""
    now = datetime.now(timezone.utc).isoformat()
    done: Dict[str, List[str]] = {"models": [], "chipsets": []}
    try:
        for i in range(0, len(plan["models"]), BATCH):
            batch_done = []
            with conn:
                for row in plan["models"][i:i + BATCH]:
                    cur = conn.execute("UPDATE canonical_products SET model = ?, updated_at = ? WHERE id = ? AND model = ?",
                                       (row["new"], now, row["id"], row["old"]))
                    if cur.rowcount:
                        batch_done.append(row["id"])
            done["models"] += batch_done
        for i in range(0, len(plan["chipsets"]), BATCH):
            batch_done = []
            with conn:
                for row in plan["chipsets"][i:i + BATCH]:
                    cur = conn.execute("UPDATE product_search_attributes SET value = ? "
                                       "WHERE product_id = ? AND key = 'chipset' AND value = ?",
                                       (row["new"], row["id"], row["old"]))
                    if cur.rowcount:
                        batch_done.append(row["id"])
            done["chipsets"] += batch_done
    except Exception as exc:
        exc.applied = done      # type: ignore[attr-defined]
        raise
    return done


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Перерасчёт моделей видеокарт с потерянным суффиксом")
    parser.add_argument("--db", required=True, help="Путь к SQLite (копия или стенд)")
    parser.add_argument("--report", required=True, help="Куда записать JSON-отчёт со старыми значениями")
    parser.add_argument("--apply", action="store_true", help="Записать изменения (без флага — только отчёт)")
    args = parser.parse_args(argv)

    if not Path(args.db).is_file():
        print(f"База {args.db} не найдена", file=sys.stderr)
        return 1
    try:
        check_report_path(args.db, args.report)
    except ReportPathError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    conn = sqlite3.connect(f"file:{os.path.realpath(args.db)}?mode={'rw' if args.apply else 'ro'}", uri=True, timeout=30)
    try:
        plan = plan_changes(conn)
        report = {"db": args.db, "apply": args.apply, "status": "planned",
                  "counts": {k: len(v) for k, v in plan.items()}, **plan}
        try:
            write_report(args.report, report, create=True)       # до первого изменения базы
        except OSError as exc:
            print(f"Отчёт не записан, база не изменялась: {exc}", file=sys.stderr)
            return 3
        if not args.apply:
            print(json.dumps({"status": "planned", **report["counts"]}, ensure_ascii=False))
            return 0
        try:
            done = apply_changes(conn, plan)
            report.update(status="applied", applied=done)
            code = 0
        except Exception as exc:
            report.update(status="partial", applied=getattr(exc, "applied", {}), error=f"{type(exc).__name__}: {exc}")
            code = 4
        try:
            write_report(args.report, report)
        except OSError as exc:
            print(f"Итог не дописан в отчёт (план со старыми значениями уже в нём): {exc}", file=sys.stderr)
            code = code or 5
        print(json.dumps({"status": report["status"], **{k: len(v) for k, v in report["applied"].items()}},
                         ensure_ascii=False))
        return code
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
