#!/usr/bin/env python3
"""Ручной сбор контента из соцсетей (S2-E06).

Примеры:
    ./venv/bin/python scripts/social_ingest.py --list
    ./venv/bin/python scripts/social_ingest.py --add --platform telegram --handle shopkz_test
    ./venv/bin/python scripts/social_ingest.py --add --platform instagram --handle 17841400000000000
    ./venv/bin/python scripts/social_ingest.py --ingest <source_id>
    ./venv/bin/python scripts/social_ingest.py --once --limit 5

Работает с тем же DATA_DIR, что и приложение. Реальные запросы к Telegram/Instagram
выполняются только здесь, по явному запуску: фоновой сбор в веб-панели не включён.
"""

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import init_db, get_connection
from domain.social_connector import SocialPlatform
from repositories.social_source_repo import SocialSourceRepository
from social_ingest import IngestStatus, ingest_due_sources, ingest_source


def _print_sources(sources) -> None:
    if not sources:
        print("Источников нет. Добавьте: --add --platform telegram --handle <канал>")
        return
    for source in sources:
        state = "вкл" if source.enabled else "выкл"
        print(f"{source.id}  {source.platform:9s} @{source.handle:24s} {state}  "
              f"статус={source.last_status:11s}  {source.last_fetch_at or '—'}"
              + (f"  ошибка: {source.last_error}" if source.last_error else ""))


def _print_result(result) -> None:
    print(json.dumps(result.to_dict(), ensure_ascii=False))
    if result.status not in (IngestStatus.OK, IngestStatus.EMPTY):
        print(f"[!] {result.platform}/@{result.handle}: {result.status} — {result.error}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Сбор контента из Telegram/Instagram (S2-E06)")
    parser.add_argument("--list", action="store_true", help="показать настроенные источники")
    parser.add_argument("--add", action="store_true", help="добавить/обновить источник")
    parser.add_argument("--platform", choices=sorted(SocialPlatform.ALL), help="платформа источника")
    parser.add_argument("--handle", help="канал Telegram или числовой Business ID Instagram")
    parser.add_argument("--url", default="", help="публичный URL источника")
    parser.add_argument("--seller-id", default="", help="привязать наблюдения к продавцу")
    parser.add_argument("--channel-id", default="", help="привязать наблюдения к каналу")
    parser.add_argument("--disable", action="store_true", help="выключить источник при добавлении")
    parser.add_argument("--ingest", metavar="SOURCE_ID", help="собрать один источник")
    parser.add_argument("--once", action="store_true", help="собрать включённые источники по очереди")
    parser.add_argument("--limit", type=int, default=5, help="сколько источников забрать за --once")
    parser.add_argument("--with-vision", action="store_true", help="задействовать мультимодальный Vision по фото")
    args = parser.parse_args(argv)

    init_db()
    conn = get_connection()
    try:
        repo = SocialSourceRepository(conn)
        if args.list:
            _print_sources(repo.list_sources())
            return 0
        if args.add:
            if not args.platform or not args.handle:
                parser.error("--add требует --platform и --handle")
            try:
                source = repo.add_source(
                    args.platform, args.handle, url=args.url,
                    seller_id=args.seller_id, channel_id=args.channel_id,
                    enabled=not args.disable,
                )
            except ValueError as exc:
                print(f"Некорректный источник: {exc}", file=sys.stderr)
                return 2
            print(f"Источник сохранён: {source.id} ({source.platform} @{source.handle})")
            return 0
        if args.ingest:
            source = repo.get_by_id(args.ingest)
            if source is None:
                print(f"Источник {args.ingest} не найден", file=sys.stderr)
                return 1
            if args.with_vision:
                from social_ingest import ingest_source_async
                res = asyncio.run(ingest_source_async(source, with_vision=True))
                _print_result(res)
            else:
                _print_result(ingest_source(source))
            return 0
        if args.once:
            results = asyncio.run(ingest_due_sources(limit=args.limit, with_vision=args.with_vision))
            for result in results:
                _print_result(result)
            ok = sum(1 for r in results if r.status == IngestStatus.OK)
            total_offers = sum(r.offers for r in results)
            print(f"Готово: источников {len(results)}, успешно {ok}, создано предложений {total_offers}")
            return 0
        parser.print_help()
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
