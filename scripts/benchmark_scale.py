#!/usr/bin/env python3
"""
CLI Scale Benchmark Runner (Stage 30, KZ Price Hunter 2.0).

Usage:
  python3 scripts/benchmark_scale.py [--target 10k|100k|500k|1m] [--output-json] [--db-path /path/to/db]
"""

import argparse
import json
import os
import sys

# Ensure repository root is in python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from domain.scale_bench import evaluate_storage_decision, run_scale_benchmark


def parse_target(target_str: str) -> int:
    s = target_str.strip().lower()
    if s.endswith("k"):
        return int(float(s[:-1]) * 1000)
    elif s.endswith("m"):
        return int(float(s[:-1]) * 1000000)
    else:
        return int(s)


def main():
    parser = argparse.ArgumentParser(description="KZ Price Hunter 2.0 Scale Benchmark Runner")
    parser.add_argument("--target", default="10k", help="Target offers count (e.g. 1k, 10k, 100k, 500k, 1m)")
    parser.add_argument("--db-path", default=None, help="Optional database file path (uses temp isolated DB by default)")
    parser.add_argument("--output-json", action="store_true", help="Print benchmark results as JSON")
    args = parser.parse_args()

    target_count = parse_target(args.target)
    if not args.output_json:
        print(f"\n=======================================================")
        print(f"🚀 KZ Price Hunter 2.0: Scale Benchmark ({args.target})")
        print(f"=======================================================")
        print(f"Target Offers: {target_count:,}")
        print("Running synthetic data generation and measurement...")

    metrics = run_scale_benchmark(
        target_offers=target_count,
        db_path=args.db_path,
        checkpoint_name=args.target,
    )
    decision = evaluate_storage_decision(metrics)

    if args.output_json:
        res = {
            "checkpoint": metrics.to_dict(),
            "decision": decision.to_dict(),
        }
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return

    print("\n-------------------------------------------------------")
    print("📊 Контрольные метрики производительности:")
    print("-------------------------------------------------------")
    print(f"  • Офферов в каталоге:       {metrics.actual_offers:,}")
    print(f"  • Канонических товаров:     {metrics.total_products:,}")
    print(f"  • Продавцов / Каналов:      {metrics.total_sellers:,} / {metrics.total_channels:,}")
    print(f"  • Задержка поиска (p95):    {metrics.search_p95_ms:.2f} мс")
    print(f"  • Средняя задержка поиска:  {metrics.search_avg_ms:.2f} мс")
    print(f"  • Запись пакета 1k офферов: {metrics.write_latency_1k_ms:.2f} мс")
    print(f"  • Размер базы данных:       {metrics.db_size_mb:.2f} МБ")
    print(f"  • Размер FTS индекса:       {metrics.fts_size_mb:.2f} МБ")
    print(f"  • Размер истории цен:       {metrics.price_history_size_mb:.2f} МБ")
    print(f"  • Длительность бэкапа:      {metrics.backup_duration_s:.3f} с")
    print(f"  • Память процесса (RSS):    {metrics.memory_mb:.2f} МБ")
    print(f"  • Холодный старт:           {metrics.startup_ms:.2f} мс")

    print("\n-------------------------------------------------------")
    print("⚖️ Решение по архитектуре хранилища (Stage 30):")
    print("-------------------------------------------------------")
    status_icon = "✅" if decision.is_sqlite_sufficient else "⚠️"
    print(f"{status_icon} Решение:  {decision.decision}")
    print(f"📝 Вердикт:  {decision.summary}")
    for r in decision.reasons:
        print(f"   - {r}")
    print("=======================================================\n")


if __name__ == "__main__":
    main()
