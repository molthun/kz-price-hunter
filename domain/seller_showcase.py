"""
Domain service for Seller Page & Product Showcase in KZ Price Hunter 2.0 (S2-E19).
Aggregates seller profile, sales channels (Website, Kaspi, Instagram, Telegram),
and groups multi-channel offers per product.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional
from domain.models import Channel, Seller
from domain.provenance import build_offer_provenance, build_seller_provenance


def is_offer_expired(expires_at: Optional[str], now: Optional[datetime] = None) -> bool:
    """Проверяет, истёк ли срок действия предложения (S2-E08, A04)."""
    from domain.purchasability import offer_expired
    return offer_expired(expires_at, now=now)


def is_offer_purchasable(row: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    """A04, B11, C08: общее правило доступности (domain.purchasability)."""
    from domain.purchasability import offer_purchasable
    return offer_purchasable(row, now=now)


def group_seller_offers_by_product(offers_with_details: List[Dict[str, Any]], now: Optional[datetime] = None) -> List[Dict[str, Any]]:
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
                "min_price_new": float("inf"),
                "min_price_used": float("inf"),
                "has_purchasable": False,
                "conditions": set(),
                "cities": set(),
                "channels": [],
            }

        grp = groups[group_key]
        price = float(row.get("price") or 0.0)
        old_price = float(row.get("old_price")) if row.get("old_price") is not None else None
        condition = (row.get("condition") or "NEW").upper()
        city = row.get("city") or "Казахстан"

        purchasable = is_offer_purchasable(row, now=now)
        expired = is_offer_expired(row.get("expires_at"), now=now)

        if purchasable:
            if price < grp["min_price"]:
                grp["min_price"] = price
            if price > grp["max_price"]:
                grp["max_price"] = price
            grp["has_purchasable"] = True
            if condition == "NEW":
                if price < grp["min_price_new"]:
                    grp["min_price_new"] = price
            else:
                if price < grp["min_price_used"]:
                    grp["min_price_used"] = price

        grp["conditions"].add(condition)
        grp["cities"].add(city)
        prov = build_offer_provenance(row, now=now)

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
            "observed_at": prov.observed_at,
            "expires_at": row.get("expires_at"),
            "published_at": row.get("published_at"),
            "freshness": prov.freshness,
            "legacy_freshness": prov.legacy_freshness,
            "source_freshness_type": prov.source_freshness_type,
            "age_hours": prov.age_hours,
            "provenance": prov.to_dict(),
            "is_purchasable": purchasable,
            "is_expired": expired,
        })

    result = []
    for grp in groups.values():
        if grp["min_price"] == float("inf"):
            grp["min_price"] = 0.0
        if grp["min_price_new"] == float("inf"):
            grp["min_price_new"] = 0.0
        if grp["min_price_used"] == float("inf"):
            grp["min_price_used"] = 0.0
        # Sort channels by purchasable first, then price ASC
        grp["channels"].sort(key=lambda c: (not c["is_purchasable"], c["price"]))
        grp["conditions"] = sorted(list(grp["conditions"]))
        grp["cities"] = sorted(list(grp["cities"]))
        grp["offers_count"] = len(grp["channels"])
        result.append(grp)

    # Sort grouped products: purchasable first, then min_price ASC
    result.sort(key=lambda p: (not p["has_purchasable"], p["min_price"] if p["has_purchasable"] else float("inf")))
    return result


def build_seller_showcase(
    seller: Seller,
    channels: List[Channel],
    grouped_products: List[Dict[str, Any]],
    total_offers_count: int,
    available_cities: List[str],
    available_categories: List[str],
    conditions_breakdown: Optional[Dict[str, int]] = None,
    identities: Iterable[Any] = (),
    verified_identities: Iterable[Any] = (),
    now: Optional[Any] = None,
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

    flat_offers = [ch for p in grouped_products for ch in p.get("channels", [])]
    eff_identities = list(identities) or list(verified_identities)
    seller_prov = build_seller_provenance(
        seller=seller,
        channels=channels,
        identities=eff_identities,
        offers=flat_offers,
        now=now,
    )

    return {
        "seller": seller.to_dict(),
        "provenance": seller_prov.to_dict(),
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
