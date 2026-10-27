"""
Domain service for Seller Page & Product Showcase in KZ Price Hunter 2.0 (S2-E19).
Aggregates seller profile, sales channels (Website, Kaspi, Instagram, Telegram),
and groups multi-channel offers per product.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from domain.models import Channel, Seller


def group_seller_offers_by_product(offers_with_details: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Groups raw offers for a seller by product (canonical product_id or title).
    If a seller offers the same product via Website, Kaspi, and Instagram,
    they are presented together with their respective channel prices and links.
    """
    groups: Dict[str, Dict[str, Any]] = {}

    for row in offers_with_details:
        pid = row.get("product_id") or ""
        title = (
            row.get("product_title")
            or row.get("title")
            or (row.get("raw_payload") or {}).get("title")
            or "Товар"
        )
        category = row.get("product_category") or row.get("category") or "general"
        brand = row.get("product_brand") or row.get("brand")
        image_url = row.get("product_image_url") or row.get("image_url")

        # Group key: product_id if present, otherwise normalized title
        group_key = pid if pid else title.strip().lower()

        if group_key not in groups:
            groups[group_key] = {
                "product_id": pid,
                "title": title,
                "category": category,
                "brand": brand,
                "image_url": image_url,
                "min_price": float("inf"),
                "max_price": 0.0,
                "conditions": set(),
                "cities": set(),
                "channels": [],
            }

        grp = groups[group_key]
        price = float(row.get("price") or 0.0)
        old_price = float(row.get("old_price")) if row.get("old_price") is not None else None
        condition = (row.get("condition") or "NEW").upper()
        city = row.get("city") or "Казахстан"

        if price > 0:
            if price < grp["min_price"]:
                grp["min_price"] = price
            if price > grp["max_price"]:
                grp["max_price"] = price

        grp["conditions"].add(condition)
        grp["cities"].add(city)

        grp["channels"].append({
            "offer_id": row.get("id"),
            "channel_id": row.get("channel_id"),
            "channel_name": row.get("channel_name") or row.get("channel_type") or "Магазин",
            "channel_type": row.get("channel_type") or "website",
            "channel_base_url": row.get("channel_base_url") or "",
            "price": price,
            "old_price": old_price,
            "condition": condition,
            "availability": row.get("availability") or "in_stock",
            "city": city,
            "url": str(row.get("url") or "").strip(),
            "external_sku": row.get("external_sku"),
            "expires_at": row.get("expires_at"),
            "published_at": row.get("published_at"),
        })

    result = []
    for grp in groups.values():
        if grp["min_price"] == float("inf"):
            grp["min_price"] = 0.0
        # Sort channels by price ASC
        grp["channels"].sort(key=lambda c: c["price"])
        grp["conditions"] = sorted(list(grp["conditions"]))
        grp["cities"] = sorted(list(grp["cities"]))
        grp["offers_count"] = len(grp["channels"])
        result.append(grp)

    # Sort grouped products by min_price ASC
    result.sort(key=lambda p: p["min_price"])
    return result


def build_seller_showcase(
    seller: Seller,
    channels: List[Channel],
    grouped_products: List[Dict[str, Any]],
    total_offers_count: int,
    available_cities: List[str],
    available_categories: List[str],
    conditions_breakdown: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """
    Builds the complete serializable seller showcase profile.
    """
    channel_types_summary: Dict[str, int] = {}
    for c in channels:
        ctype = c.channel_type
        channel_types_summary[ctype] = channel_types_summary.get(ctype, 0) + 1

    min_p = min((p["min_price"] for p in grouped_products if p["min_price"] > 0), default=0.0)
    max_p = max((p["max_price"] for p in grouped_products), default=0.0)

    return {
        "seller": seller.to_dict(),
        "channels": [c.to_dict() for c in channels],
        "channel_types_summary": channel_types_summary,
        "stats": {
            "total_offers": total_offers_count,
            "products_count": len(grouped_products),
            "min_price": min_p,
            "max_price": max_p,
            "conditions_breakdown": conditions_breakdown or {},
            "available_cities": available_cities,
            "available_categories": available_categories,
        },
        "products": grouped_products,
    }
