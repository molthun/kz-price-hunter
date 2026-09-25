"""
Domain entities for KZ Price Hunter 2.0 (Search Platform).

Core domain hierarchy:
  Product (Canonical model/good)
    ↓
  Seller (Real merchant/business entity)
    ↓
  Channel (Sales channel: direct, website, kaspi, forte, halyk, instagram, etc.)
    ↓
  Offer (Specific merchant offer in a specific channel)
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def utc_now_iso() -> str:
    """Return current UTC time in ISO format."""
    return datetime.now(timezone.utc).isoformat()


class Condition:
    NEW = "NEW"
    USED = "USED"
    REFURBISHED = "REFURBISHED"
    OPEN_BOX = "OPEN_BOX"
    UNKNOWN = "UNKNOWN"

    ALL = {NEW, USED, REFURBISHED, OPEN_BOX, UNKNOWN}

    @classmethod
    def normalize(cls, val: Optional[str]) -> str:
        if not val:
            return cls.NEW
        val_strip = val.strip()
        val_upper = val_strip.upper()
        if val_upper in cls.ALL:
            return val_upper

        val_lower = val_strip.lower()
        # Word boundary pattern: match standalone б/у, б.у, бу, used, second-hand
        if re.search(r"(?:^|[\s,._\-(])(?:б[/\s._-]?у|бу|used|second[-_\s]?hand|уценк|уценен)(?:$|[\s,._\-)])", val_lower):
            return cls.USED
        if re.search(r"(?:^|[\s,._\-(])(?:refurb(?:ished)?|восстановлен(?:ный|ное|ная|ные)?)(?:$|[\s,._\-)])", val_lower):
            return cls.REFURBISHED
        if re.search(r"(?:^|[\s,._\-(])(?:open[-_\s]?box|распакован(?:ный|ное|ная|ные)?)(?:$|[\s,._\-)])", val_lower):
            return cls.OPEN_BOX
        if re.search(r"(?:^|[\s,._\-(])(?:нов(?:ый|ое|ая|ые)|new)(?:$|[\s,._\-)])", val_lower):
            return cls.NEW

        return cls.UNKNOWN


class Availability:
    IN_STOCK = "in_stock"
    OUT_OF_STOCK = "out_of_stock"
    PREORDER = "preorder"
    UNKNOWN = "unknown"

    ALL = {IN_STOCK, OUT_OF_STOCK, PREORDER, UNKNOWN}

    @classmethod
    def normalize(cls, val: Optional[str]) -> str:
        if not val:
            return cls.IN_STOCK
        val_lower = val.strip().lower()
        if val_lower in cls.ALL:
            return val_lower
        if "нет данных" in val_lower or "неизвестн" in val_lower:
            return cls.UNKNOWN
        if "нет" in val_lower or "out" in val_lower or "под заказ" in val_lower or "отсутств" in val_lower:
            return cls.OUT_OF_STOCK
        if "в наличии" in val_lower or "in_stock" in val_lower or "instock" in val_lower:
            return cls.IN_STOCK
        if "предзаказ" in val_lower or "preorder" in val_lower:
            return cls.PREORDER
        return cls.UNKNOWN


class ChannelType:
    DIRECT = "direct"
    WEBSITE = "website"
    KASPI = "kaspi"
    FORTE = "forte"
    HALYK = "halyk"
    INSTAGRAM = "instagram"
    TELEGRAM = "telegram"
    WHATSAPP = "whatsapp"
    PHYSICAL_STORE = "physical_store"
    CLASSIFIEDS = "classifieds"
    FOOD_AGGREGATOR = "food_aggregator"

    ALL = {
        DIRECT,
        WEBSITE,
        KASPI,
        FORTE,
        HALYK,
        INSTAGRAM,
        TELEGRAM,
        WHATSAPP,
        PHYSICAL_STORE,
        CLASSIFIEDS,
        FOOD_AGGREGATOR,
    }


@dataclass
class Seller:
    """
    Real merchant or business selling products across one or more channels.
    """
    id: str
    slug: str
    name: str
    legal_name: Optional[str] = None
    bin: Optional[str] = None
    domain: Optional[str] = None
    phone: Optional[str] = None
    rating: Optional[float] = None
    is_active: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> Seller:
        meta = row.get("metadata_json") or "{}"
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        return cls(
            id=row["id"],
            slug=row["slug"],
            name=row["name"],
            legal_name=row.get("legal_name"),
            bin=row.get("bin"),
            domain=row.get("domain"),
            phone=row.get("phone"),
            rating=row.get("rating"),
            is_active=bool(row.get("is_active", 1)),
            metadata=meta,
            created_at=row.get("created_at") or utc_now_iso(),
            updated_at=row.get("updated_at") or utc_now_iso(),
        )


@dataclass
class Channel:
    """
    Specific sales channel where a seller offers items.
    """
    id: str
    seller_id: str
    channel_type: str
    name: str
    external_store_id: Optional[str] = None
    base_url: Optional[str] = None
    is_active: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> Channel:
        meta = row.get("metadata_json") or "{}"
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        return cls(
            id=row["id"],
            seller_id=row["seller_id"],
            channel_type=row["channel_type"],
            name=row["name"],
            external_store_id=row.get("external_store_id"),
            base_url=row.get("base_url"),
            is_active=bool(row.get("is_active", 1)),
            metadata=meta,
            created_at=row.get("created_at") or utc_now_iso(),
        )


@dataclass
class CanonicalProduct:
    """
    Canonical product representation (e.g. 'Apple iPhone 16 Pro 256GB Desert Titanium').
    Shared across multiple sellers and channels.
    """
    id: str
    canonical_key: str
    title: str
    category: str
    brand: Optional[str] = None
    model: Optional[str] = None
    description: Optional[str] = None
    image_url: Optional[str] = None
    attributes: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> CanonicalProduct:
        attrs = row.get("attributes_json") or "{}"
        if isinstance(attrs, str):
            try:
                attrs = json.loads(attrs)
            except Exception:
                attrs = {}
        return cls(
            id=row["id"],
            canonical_key=row["canonical_key"],
            title=row["title"],
            category=row["category"],
            brand=row.get("brand"),
            model=row.get("model"),
            description=row.get("description"),
            image_url=row.get("image_url"),
            attributes=attrs,
            created_at=row.get("created_at") or utc_now_iso(),
            updated_at=row.get("updated_at") or utc_now_iso(),
        )


@dataclass
class Offer:
    """
    An offer from a specific seller via a specific channel for a canonical product.
    """
    id: str
    product_id: str
    seller_id: str
    channel_id: str
    external_sku: str
    url: str
    image_url: Optional[str] = None
    price: float = 0.0
    old_price: Optional[float] = None
    currency: str = "KZT"
    condition: str = Condition.NEW
    availability: str = Availability.IN_STOCK
    city: str = "Казахстан"
    payment_methods: List[str] = field(default_factory=list)
    installment_months: Optional[int] = None
    delivery_type: Optional[str] = None
    warranty: Optional[str] = None
    published_at: Optional[str] = None
    observed_at: str = field(default_factory=utc_now_iso)
    is_active: bool = True
    raw_payload: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> Offer:
        payments = row.get("payment_methods_json") or "[]"
        if isinstance(payments, str):
            try:
                payments = json.loads(payments)
            except Exception:
                payments = []
        raw = row.get("raw_payload_json") or "{}"
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                raw = {}
        return cls(
            id=row["id"],
            product_id=row["product_id"],
            seller_id=row["seller_id"],
            channel_id=row["channel_id"],
            external_sku=row["external_sku"],
            url=row["url"],
            image_url=row.get("image_url"),
            price=float(row.get("price") or 0.0),
            old_price=float(row["old_price"]) if row.get("old_price") is not None else None,
            currency=row.get("currency") or "KZT",
            condition=row.get("condition") or Condition.NEW,
            availability=row.get("availability") or Availability.IN_STOCK,
            city=row.get("city") or "Казахстан",
            payment_methods=payments,
            installment_months=row.get("installment_months"),
            delivery_type=row.get("delivery_type"),
            warranty=row.get("warranty"),
            published_at=row.get("published_at"),
            observed_at=row.get("observed_at") or utc_now_iso(),
            is_active=bool(row.get("is_active", 1)),
            raw_payload=raw,
            created_at=row.get("created_at") or utc_now_iso(),
            updated_at=row.get("updated_at") or utc_now_iso(),
        )


@dataclass
class OfferPriceHistory:
    """
    Time-series observation of offer pricing.
    """
    offer_id: str
    price: float
    old_price: Optional[float] = None
    observed_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> OfferPriceHistory:
        return cls(
            offer_id=row["offer_id"],
            price=float(row.get("price") or 0.0),
            old_price=float(row["old_price"]) if row.get("old_price") is not None else None,
            observed_at=row.get("observed_at") or utc_now_iso(),
        )
