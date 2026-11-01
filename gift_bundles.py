"""Предложения с подарком переносятся к самому товару: подарок — маркетинг, и он не должен выводить
предложение из общего сравнения цен.

Явный подарок («+ подарок») переносится всегда. «Товар + другой товар» — только если цена почти как у
самого товара: дорогой комплект вроде стиральной и сушильной машины остаётся отдельным товаром.
"""
from __future__ import annotations

import re
from typing import Dict, Optional

import database
from domain.adapter import canonical_product_id
from domain.product_options import split_added_product, split_explicit_gift
from model_matching import extract_canonical_key

MAX_PRICE_RATIO = 1.15


def _key_for(title: str, category: str, known: Dict[str, str]) -> str:
    """Ключ самого товара: уже существующий, иначе тот, что он получил бы (domain/adapter.py)."""
    if title in known:
        return known[title]
    cached = database.get_cached_canonical_key(title)
    if cached:
        return cached
    key = extract_canonical_key(title)
    if key:
        return key
    norm = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ]+", "_", title.lower()).strip("_")
    return f"norm:{category}:{norm}" if category else f"norm:{norm}"


def merge_gift_titles(apply: bool = True, limit: Optional[int] = None) -> Dict[str, int]:
    """Сверяет предложения с подарком и переносит нужные. Повторный запуск ничего не меняет."""
    with database.get_connection() as conn:
        rows = conn.execute(
            """SELECT title, MIN(category), MIN(current_price), MIN(canonical_key) FROM products
               WHERE is_active = 1 AND title LIKE '%+%' GROUP BY title"""
        ).fetchall()
        known = dict(conn.execute("SELECT title, canonical_key FROM canonical_products").fetchall())
        moves: Dict[str, str] = {}
        stats = {"explicit": 0, "by_price": 0, "kept_expensive": 0, "kept_no_base": 0}
        for title, category, price, current_key in rows:
            main, gift = split_added_product(title)
            if gift is None:
                continue
            explicit = split_explicit_gift(title)[1] is not None
            key = _key_for(main, category or "", known)
            if key == current_key:
                continue
            if not explicit:
                base = conn.execute(
                    "SELECT MIN(price) FROM offers WHERE product_id = ? AND is_active = 1 AND price > 0",
                    (canonical_product_id(key),),
                ).fetchone()[0]
                if base is None:
                    stats["kept_no_base"] += 1
                    continue
                if price > base * MAX_PRICE_RATIO:
                    stats["kept_expensive"] += 1
                    continue
            stats["explicit" if explicit else "by_price"] += 1
            moves[title] = key
            if limit and len(moves) >= limit:
                break
    if apply and moves:
        items = list(moves.items())
        for i in range(0, len(items), 200):
            database.rekey_titles(dict(items[i:i + 200]))
    return stats


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Записать изменения")
    args = parser.parse_args()
    print(merge_gift_titles(apply=args.apply))
