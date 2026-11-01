"""Предложения с подарком переносятся к самому товару: «Товар + другой товар» — маркетинг, и он не должен
выводить предложение из общего сравнения цен. Подарок показывается значком, и покупатель решает сам.
"""
from __future__ import annotations

import re
from typing import Dict

import database
from domain.product_options import split_added_product
from model_matching import extract_canonical_key


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


def merge_gift_titles(apply: bool = True) -> Dict[str, int]:
    """Переносит предложения с подарком и отмечает их для фильтра. Повторный запуск ничего не меняет."""
    with database.get_connection() as conn:
        rows = conn.execute(
            """SELECT title, MIN(category), MIN(canonical_key) FROM products
               WHERE is_active = 1 AND (title LIKE '%+%' OR title LIKE '%&#43;%') GROUP BY title"""
        ).fetchall()
        known = dict(conn.execute("SELECT title, canonical_key FROM canonical_products").fetchall())
        moves: Dict[str, str] = {}
        gift_titles = []
        for title, category, current_key in rows:
            main, gift = split_added_product(title)
            if gift is None:
                continue
            gift_titles.append(title)
            key = _key_for(main, category or "", known)
            if key != current_key:
                moves[title] = key
        marked = 0
        if apply:
            items = list(moves.items())
            for i in range(0, len(items), 200):
                database.rekey_titles(dict(items[i:i + 200]))
            conn.execute("UPDATE offers SET has_gift = 0 WHERE has_gift = 1")
            for i in range(0, len(gift_titles), 500):
                chunk = gift_titles[i:i + 500]
                marks = ",".join("?" * len(chunk))
                marked += conn.execute(
                    f"UPDATE offers SET has_gift = 1 WHERE id IN (SELECT 'off_' || id FROM products WHERE title IN ({marks}))",
                    chunk,
                ).rowcount
            conn.commit()
    return {"gift_titles": len(gift_titles), "moved": len(moves), "marked_offers": marked}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Записать изменения")
    args = parser.parse_args()
    print(merge_gift_titles(apply=args.apply))
