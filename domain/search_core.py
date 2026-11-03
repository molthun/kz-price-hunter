"""
Search Core 2.0 (Unified Product Index & Multi-Merchant Aggregation).
Provides multi-seller product aggregation, structured query execution,
availability/freshness filtering, and relevance ranking for KZ Price Hunter 2.0.
"""

from __future__ import annotations

import datetime
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from domain.product_options import build_family_options, build_options, family_id, mark_gifts, offer_options, split_added_product
from domain.condition import ConditionBreakdown, build_condition_breakdown
from domain.freshness_v2 import offer_freshness
from domain.models import CanonicalProduct, Channel, ChannelType, Condition, Offer, Seller
from domain.provenance import build_offer_provenance, build_seller_provenance
from domain.query_parser import ParsedQuery, QueryParser

logger = logging.getLogger("kz_price_hunter.search_core")


# Правило доступности одно на поиск, витрину и наблюдения (C08, R07–R09)
from domain.purchasability import offer_expired, offer_purchasable, utc_now  # noqa: E402,F401


class SearchOutcome:
    FOUND = "FOUND"
    WEAK_MATCH = "WEAK_MATCH"
    NOT_FOUND = "NOT_FOUND"
    ERROR = "ERROR"

    ALL = {FOUND, WEAK_MATCH, NOT_FOUND, ERROR}


@dataclass
class AggregatedOffer:
    """An offer enriched with seller, channel, availability, and freshness metadata."""
    id: str
    product_id: str
    seller_id: str
    seller_name: str
    seller_slug: str
    seller_rating: Optional[float]
    channel_id: str
    channel_type: str
    channel_name: str
    price: float
    old_price: Optional[float]
    condition: str
    availability: str
    city: str
    url: str
    image_url: Optional[str] = None
    observed_at: Optional[str] = None
    freshness: str = "fresh"
    is_purchasable: bool = True
    expires_at: Optional[str] = None
    is_private_seller: bool = False
    # Условия оплаты — только сохранённые в предложении; тип площадки их не доказывает (V05)
    installment_available: bool = False
    installment_months: Optional[int] = None
    credit_available: bool = False
    # Условия получения — только сохранённые в предложении (SF1-04)
    delivery_type: Optional[str] = None
    pickup_available: bool = False
    warranty: Optional[str] = None
    # Основание цены: за единицу или за упаковку какого объёма (FA69-07)
    is_unit_price: bool = False
    quantity: Optional[float] = None
    unit: Optional[str] = None
    # S2-E28: Trust & Offer Provenance (паспорт происхождения и свежести предложения)
    source: str = ""
    published_at: Optional[str] = None
    provenance: Optional[Dict[str, Any]] = None
    seller_domain: Optional[str] = None
    seller_phone: Optional[str] = None
    seller_bin: Optional[str] = None
    seller_legal_name: Optional[str] = None
    seller_metadata_json: Optional[str] = None
    title: Optional[str] = None  # название у магазина: по нему виден цвет
    options: Optional[Dict[str, str]] = None
    gift: Optional[str] = None  # что дарят; пустая строка, если магазин не уточнил

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def offer_price_basis(row: Dict[str, Any]) -> Dict[str, Any]:
    """Сохранённое основание цены предложения; у обычных предложений его нет."""
    payload = row.get("raw_payload_json") or "{}"
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {}
    basis = payload.get("price_basis") if isinstance(payload, dict) else None
    if not isinstance(basis, dict):
        return {}
    return {
        "is_unit_price": bool(basis.get("is_unit_price")),
        "quantity": basis.get("quantity"),
        "unit": basis.get("unit"),
    }


def offer_delivery_terms(row: Dict[str, Any]) -> Dict[str, Any]:
    """Сохранённые условия получения — по тем же полям, что Offer.from_row; догадок не добавляет."""
    payments = row.get("payment_methods_json") or "[]"
    if isinstance(payments, str):
        try:
            payments = json.loads(payments)
        except Exception:
            payments = []
    if not isinstance(payments, (list, tuple, set)):
        payments = []
    return {
        "delivery_type": row.get("delivery_type") or None,
        "pickup_available": (
            bool(row.get("pickup_available")) or row.get("delivery_type") == "pickup" or "pickup" in payments
        ),
        "warranty": row.get("warranty") or None,
    }


def offer_payment_terms(row: Dict[str, Any]) -> Dict[str, Any]:
    """Подтверждённые данными условия рассрочки/кредита — по тем же полям, что Offer.from_row."""
    payments = row.get("payment_methods_json") or "[]"
    if isinstance(payments, str):
        try:
            payments = json.loads(payments)
        except Exception:
            payments = []
    if not isinstance(payments, (list, tuple, set)):
        payments = []
    months = row.get("installment_months")
    try:
        months = int(months) if months is not None else None
    except (TypeError, ValueError):
        months = None
    return {
        "installment_available": bool(row.get("installment_available")) or bool(months and months > 0)
        or "installment" in payments,
        "installment_months": months if months and months > 0 else None,
        "credit_available": bool(row.get("credit_available")) or "credit" in payments,
    }


@dataclass
class SellerOffersGroup:
    """
    Группировка каналов и предложений одного продавца для конкретного товара (S2-E18).
    Позволяет покупателю видеть единого продавца с его каналами (магазин, Kaspi, Forte и т.д.),
    исключая ложное задвоение магазинов в выдаче.
    """
    seller_id: str
    seller_name: str
    seller_slug: str
    seller_rating: Optional[float] = None
    is_private_seller: bool = False
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    offers_count: int = 0
    channels_count: int = 0
    channels_available: List[str] = field(default_factory=list)
    best_offer: Optional[AggregatedOffer] = None
    offers: List[AggregatedOffer] = field(default_factory=list)
    has_new: bool = False
    has_used: bool = False
    best_new_price: Optional[float] = None
    best_used_price: Optional[float] = None
    # S2-E28: Trust & Provenance (проверяемые факты о продавце без надписи «Проверенный продавец»)
    facts: List[str] = field(default_factory=list)
    provenance: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        res = asdict(self)
        res["best_offer"] = self.best_offer.to_dict() if self.best_offer else None
        res["best_channel_offer"] = res["best_offer"]  # Алиас для контракта шаблона (SP30-03)
        res["channels"] = list(self.channels_available)  # Алиас для контракта шаблона (SP30-03)
        res["offers"] = [o.to_dict() for o in self.offers]
        return res


@dataclass
class ProductSearchResult:
    """A canonical product aggregating multiple seller offers across channels."""
    product: CanonicalProduct
    offers: List[AggregatedOffer] = field(default_factory=list)
    sellers: List[SellerOffersGroup] = field(default_factory=list)
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    sellers_count: int = 0
    offers_count: int = 0
    best_offer: Optional[AggregatedOffer] = None
    conditions_available: List[str] = field(default_factory=list)
    has_purchasable_offers: bool = False
    has_new: bool = False
    has_used: bool = False
    relevance_score: float = 0.0
    best_new_price: Optional[float] = None
    best_used_price: Optional[float] = None
    condition_breakdown: Optional[Dict[str, Any]] = None
    is_used_only: bool = False
    new_offers_count: int = 0
    used_offers_count: int = 0
    new_sellers_count: int = 0
    used_sellers_count: int = 0
    variants_count: int = 0  # другие исполнения той же модели
    has_gift: bool = False

    def to_dict(self) -> Dict[str, Any]:
        res = asdict(self)
        res["product"] = self.product.to_dict()
        res["offers"] = [o.to_dict() for o in self.offers]
        res["sellers"] = [s.to_dict() for s in self.sellers]
        res["best_offer"] = self.best_offer.to_dict() if self.best_offer else None
        return res


@dataclass
class SearchResponse2:
    """High-level response containing aggregated multi-merchant search results with pagination and global facets."""
    query: str
    parsed_query: ParsedQuery
    items: List[ProductSearchResult] = field(default_factory=list)
    total_products: int = 0
    total_offers: int = 0
    latency_ms: float = 0.0
    outcome: str = SearchOutcome.NOT_FOUND
    category_facets: List[Dict[str, Any]] = field(default_factory=list)
    brand_facets: List[Dict[str, Any]] = field(default_factory=list)
    model_facets: List[Dict[str, Any]] = field(default_factory=list)
    kind_facets: List[Dict[str, Any]] = field(default_factory=list)
    seller_facets: List[Dict[str, Any]] = field(default_factory=list)
    city_facets: List[Dict[str, Any]] = field(default_factory=list)
    attribute_facets: List[Dict[str, Any]] = field(default_factory=list)
    # False — фасеты посчитать не удалось; пустые списки тогда не значат «вариантов нет»
    facets_available: bool = True
    page: int = 1
    page_size: int = 50
    total_pages: int = 1
    has_next: bool = False
    has_prev: bool = False

    def to_dict(self) -> Dict[str, Any]:
        res = asdict(self)
        res["parsed_query"] = self.parsed_query.to_dict()
        res["items"] = [item.to_dict() for item in self.items]
        res["category_facets"] = self.category_facets
        res["brand_facets"] = self.brand_facets
        res["model_facets"] = self.model_facets
        res["kind_facets"] = self.kind_facets
        res["seller_facets"] = self.seller_facets
        res["city_facets"] = self.city_facets
        res["attribute_facets"] = self.attribute_facets
        res["facets_available"] = self.facets_available
        res["page"] = self.page
        res["page_size"] = self.page_size
        res["total_pages"] = self.total_pages
        res["has_next"] = self.has_next
        res["has_prev"] = self.has_prev
        return res


class SearchCore:
    """
    Executes search queries against the Unified Product Index 2.0.
    Coordinates QueryParser, SearchRepository, and multi-merchant aggregation.
    """

    def __init__(self, search_repo: Any):
        self.search_repo = search_repo

    def search(
        self,
        query: str,
        city: Optional[str] = None,
        condition: Optional[str] = None,
        channel_type: Optional[str] = None,
        seller_type: Optional[str] = None,
        seller_id: Optional[str] = None,
        installment: Optional[bool] = None,
        gift: Optional[bool] = None,
        in_stock: Optional[bool] = None,
        delivery: Optional[str] = None,
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
        sort_by: str = "relevance",
        limit: int = 50,
        page: int = 1,
        offset: Optional[int] = None,
        now: Optional[datetime.datetime] = None,
        category: Optional[str] = None,
        brand: Optional[str] = None,
        model: Optional[str] = None,
        kind: Optional[str] = None,
        attributes: Optional[Dict[str, str]] = None,
    ) -> SearchResponse2:
        """
        Executes a 2.0 multi-merchant search query with error handling, stable pagination, and global facets.
        """
        start_time = time.perf_counter()
        parsed: Optional[ParsedQuery] = None
        if attributes:
            self.search_repo.attribute_filters = attributes
        # Один момент времени для SQL-отбора и итоговой доступности (R09)
        now = utc_now(now)
        page_val = max(1, page)
        limit_val = max(1, min(limit, 100))
        offset_val = (page_val - 1) * limit_val if (offset is None and page_val > 1) else (offset or 0)

        try:
            # 1. Parse natural query
            parsed = QueryParser.parse(query)

            # Explicit parameters override query parser detections if provided
            target_city = city or parsed.detected_city
            norm_cond = Condition.normalize(condition) if condition else parsed.condition
            target_condition = norm_cond if norm_cond != Condition.UNKNOWN else None
            target_channel = channel_type or parsed.channel_type
            target_min_price = min_price if min_price is not None else parsed.min_price
            target_max_price = max_price if max_price is not None else parsed.max_price
            target_brand = brand or parsed.detected_brand

            if target_city:
                parsed.detected_city = target_city
            if target_condition:
                parsed.condition = target_condition
            if target_channel:
                parsed.channel_type = target_channel
            if target_min_price is not None:
                parsed.min_price = target_min_price
            if target_max_price is not None:
                parsed.max_price = target_max_price
            if target_brand:
                parsed.detected_brand = target_brand

            # 2. Search canonical products via repository with pagination
            raw_items = self.search_repo.search_products(
                search_text=parsed.clean_query,
                brand=target_brand,
                model=model,
                kind=kind,
                city=target_city,
                condition=target_condition,
                category=category,
                channel_type=target_channel,
                seller_type=seller_type,
                seller_id=seller_id,
                installment=installment, gift=gift,
                in_stock=in_stock,
                delivery=delivery,
                min_price=target_min_price,
                max_price=target_max_price,
                sort_by=sort_by,
                limit=limit_val,
                offset=offset_val,
                now=now,
            )

            # Exact full-count metrics across the whole dataset
            count_info = {}
            if hasattr(self.search_repo, "count_search_products"):
                try:
                    count_info = self.search_repo.count_search_products(
                        search_text=parsed.clean_query,
                        brand=target_brand,
                        model=model,
                        kind=kind,
                        city=target_city,
                        condition=target_condition,
                        category=category,
                        channel_type=target_channel,
                        seller_type=seller_type,
                        seller_id=seller_id,
                        installment=installment, gift=gift,
                        in_stock=in_stock,
                        delivery=delivery,
                        min_price=target_min_price,
                        max_price=target_max_price,
                    )
                except Exception as ex:
                    logger.warning(f"Failed to get exact count: {ex}")

            # 3. Aggregate offers and compute product cards
            product_results: List[ProductSearchResult] = []
            total_offers_count = 0

            # Extract numeric tokens from query to check model consistency (e.g. 5090 vs 5070)
            query_num_tokens = set(re.findall(r"\b\d{2,5}\b", parsed.clean_query))

            for item in raw_items:
                product = item["product"]
                offers_data = item.get("offers", [])
                bm25_score = item.get("bm25_score", 0.0)

                aggregated_offers: List[AggregatedOffer] = []
                for od in offers_data:
                    # C-E11-03: Assess freshness and purchasability
                    obs_at = od.get("observed_at")
                    # Метка свежести — по тому же правилу, что доступность (V03)
                    f_state = offer_freshness(od, now=now).legacy_freshness
                    price_val = float(od["price"])
                    expires_at = od.get("expires_at")
                    # S2-E08, C08: наличие, свежесть, цена, TTL и активность — общим правилом
                    purchasable = offer_purchasable(od, now=now)
                    # S2-E28: Trust & Offer Provenance
                    prov = build_offer_provenance(od, now=now)

                    agg_off = AggregatedOffer(
                        id=od["id"],
                        product_id=od["product_id"],
                        seller_id=od["seller_id"],
                        seller_name=od.get("seller_name", "Unknown Seller"),
                        seller_slug=od.get("seller_slug", ""),
                        seller_rating=float(od["seller_rating"]) if od.get("seller_rating") is not None else None,
                        channel_id=od["channel_id"],
                        channel_type=od.get("channel_type", "unknown"),
                        channel_name=od.get("channel_name", "Main Channel"),
                        price=price_val,
                        old_price=float(od["old_price"]) if od.get("old_price") is not None else None,
                        condition=od.get("condition", Condition.NEW),
                        availability=od.get("availability", "in_stock"),
                        city=od.get("city", "Все"),
                        url=od.get("url", ""),
                        image_url=od.get("image_url"),
                        observed_at=obs_at,
                        freshness=f_state,
                        is_purchasable=purchasable,
                        expires_at=expires_at,
                        is_private_seller=bool(od.get("is_private_seller", False)),
                        source=prov.source,
                        published_at=od.get("published_at"),
                        provenance=prov.to_dict(),
                        seller_domain=od.get("seller_domain"),
                        seller_phone=od.get("seller_phone"),
                        seller_bin=od.get("seller_bin"),
                        seller_legal_name=od.get("seller_legal_name"),
                        seller_metadata_json=od.get("seller_metadata_json"),
                        **offer_payment_terms(od),
                        **offer_delivery_terms(od),
                        **offer_price_basis(od),
                    )
                    aggregated_offers.append(agg_off)

                if not aggregated_offers:
                    continue

                # Sort offers by purchasable first, then price ASC
                aggregated_offers.sort(key=lambda o: (not o.is_purchasable, o.price))

                unique_sellers = {o.seller_id for o in aggregated_offers}
                purchasable_offers = [o for o in aggregated_offers if o.is_purchasable]

                new_purchasable = [o for o in purchasable_offers if o.condition == Condition.NEW]
                used_purchasable = [
                    o for o in purchasable_offers
                    if o.condition in (Condition.USED, Condition.REFURBISHED, Condition.OPEN_BOX)
                ]

                best_new_p = min(o.price for o in new_purchasable) if new_purchasable else None
                best_used_p = min(o.price for o in used_purchasable) if used_purchasable else None

                all_conditions = {o.condition for o in aggregated_offers}
                has_new = Condition.NEW in all_conditions
                has_used = any(c in (Condition.USED, Condition.REFURBISHED, Condition.OPEN_BOX) for c in all_conditions)

                if purchasable_offers:
                    p_prices = [o.price for o in purchasable_offers]
                    min_p = min(p_prices)
                    max_p = max(p_prices)
                    best_off = purchasable_offers[0]
                    conditions = sorted(list({o.condition for o in purchasable_offers}))
                    has_purchasable = True
                    is_used_only = (len(new_purchasable) == 0 and len(used_purchasable) > 0)
                else:
                    # C-E11-03: No purchasable offers (all out-of-stock or stale)
                    min_p = None
                    max_p = None
                    best_off = None
                    conditions = []
                    has_purchasable = False
                    is_used_only = False

                cond_breakdown = build_condition_breakdown(product.id, product.title, aggregated_offers).to_dict()

                # S2-E18: Seller grouping — группировка каналов одного продавца внутри карточки товара
                seller_map: Dict[str, List[AggregatedOffer]] = {}
                for o in aggregated_offers:
                    seller_map.setdefault(o.seller_id, []).append(o)

                seller_groups: List[SellerOffersGroup] = []
                new_sellers_set = set()
                used_sellers_set = set()

                for s_id, s_offers in seller_map.items():
                    s_offers.sort(key=lambda x: (not x.is_purchasable, x.price))
                    s_first = s_offers[0]
                    s_channels = list(dict.fromkeys(x.channel_type for x in s_offers if x.channel_type))

                    s_purchasable = [x for x in s_offers if x.is_purchasable]
                    s_best = s_purchasable[0] if s_purchasable else s_first

                    s_new = [x for x in s_offers if x.condition == Condition.NEW]
                    s_used = [x for x in s_offers if x.condition in (Condition.USED, Condition.REFURBISHED, Condition.OPEN_BOX)]

                    if s_new:
                        new_sellers_set.add(s_id)
                    if s_used:
                        used_sellers_set.add(s_id)

                    s_new_p = [x for x in s_purchasable if x.condition == Condition.NEW]
                    s_used_p = [x for x in s_purchasable if x.condition in (Condition.USED, Condition.REFURBISHED, Condition.OPEN_BOX)]

                    # SP30-02: Цены продавца и лучшая цена по condition вычисляются строго по purchasable офферам!
                    # Недоступные (out of stock, expired) не занижают цену продавца и не становятся best_new_price/best_used_price!
                    if s_purchasable:
                        s_purch_prices = [x.price for x in s_purchasable]
                        sg_min_price = min(s_purch_prices)
                        sg_max_price = max(s_purch_prices)
                    else:
                        sg_min_price = None
                        sg_max_price = None

                    best_np = min(x.price for x in s_new_p) if s_new_p else None
                    best_up = min(x.price for x in s_used_p) if s_used_p else None

                    # S2-E28: Trust & Provenance (объективные факты о продавце)
                    s_domain = next((x.seller_domain for x in s_offers if x.seller_domain), None)
                    s_phone = next((x.seller_phone for x in s_offers if x.seller_phone), None)
                    s_bin = next((x.seller_bin for x in s_offers if x.seller_bin), None)
                    s_legal = next((x.seller_legal_name for x in s_offers if x.seller_legal_name), None)
                    s_meta_raw = next((x.seller_metadata_json for x in s_offers if x.seller_metadata_json), None)
                    s_meta = {}
                    if isinstance(s_meta_raw, str) and s_meta_raw.strip():
                        try:
                            s_meta = json.loads(s_meta_raw)
                        except Exception:
                            s_meta = {}

                    seller_obj = Seller(
                        id=s_id,
                        name=s_first.seller_name,
                        slug=s_first.seller_slug,
                        domain=s_domain,
                        phone=s_phone,
                        bin=s_bin,
                        legal_name=s_legal,
                        metadata=s_meta,
                    )
                    seller_channels = [
                        Channel(
                            id=x.channel_id,
                            seller_id=s_id,
                            channel_type=x.channel_type,
                            name=x.channel_name,
                            base_url=x.url,
                        )
                        for x in s_offers
                    ]
                    seller_prov = build_seller_provenance(
                        seller_obj,
                        channels=seller_channels,
                        offers=[{"observed_at": x.observed_at} for x in s_offers],
                        now=now,
                    )

                    seller_groups.append(SellerOffersGroup(
                        seller_id=s_id,
                        seller_name=s_first.seller_name,
                        seller_slug=s_first.seller_slug,
                        seller_rating=s_first.seller_rating,
                        is_private_seller=s_first.is_private_seller,
                        min_price=sg_min_price,
                        max_price=sg_max_price,
                        offers_count=len(s_offers),
                        channels_count=len(s_channels),
                        channels_available=s_channels,
                        best_offer=s_best,
                        offers=s_offers,
                        has_new=bool(s_new),
                        has_used=bool(s_used),
                        best_new_price=best_np,
                        best_used_price=best_up,
                        facts=list(seller_prov.fact_labels),
                        provenance=seller_prov.to_dict(),
                    ))

                seller_groups.sort(key=lambda s: (
                    not (s.best_offer and s.best_offer.is_purchasable and s.min_price is not None),
                    s.min_price if s.min_price is not None else float("inf"),
                ))

                # ---------------------------------------------------------------
                # C-E11-05: Exact Model Match and Semantic Scoring
                # ---------------------------------------------------------------
                rel_score = abs(bm25_score) if bm25_score else 1.0
                prod_title_lower = product.title.lower()
                prod_model_lower = (product.model or "").lower()
                prod_text = f"{prod_title_lower} {product.brand or ''} {prod_model_lower}".lower()

                # Model exact match bonus: model tokens (e.g. 5090, 16 pro)
                has_model_match = False
                if product.model and product.model.lower() in parsed.clean_query.lower():
                    rel_score += 15.0
                    has_model_match = True
                elif any(nt in prod_model_lower or nt in prod_title_lower for nt in query_num_tokens):
                    rel_score += 12.0
                    has_model_match = True

                # Model conflict penalty: user searched 5090, but item is 5070
                prod_num_tokens = set(re.findall(r"\d{2,5}", prod_text))
                for q_num in query_num_tokens:
                    if q_num not in prod_num_tokens:
                        rel_score -= 25.0

                # Attribute match bonuses (e.g. 256gb)
                for attr_val in parsed.attributes.values():
                    if attr_val.lower() in prod_text:
                        rel_score += 5.0

                # Exact brand match bonus
                if parsed.detected_brand and product.brand and parsed.detected_brand.lower() == product.brand.lower():
                    rel_score += 5.0

                # Minor tie-breaker for multi-merchant availability
                rel_score += min(len(unique_sellers) * 0.1, 1.0)

                card = ProductSearchResult(
                    product=product,
                    offers=aggregated_offers,
                    sellers=seller_groups,
                    min_price=min_p,
                    max_price=max_p,
                    sellers_count=len(unique_sellers),
                    offers_count=len(aggregated_offers),
                    best_offer=best_off,
                    conditions_available=conditions,
                    has_purchasable_offers=has_purchasable,
                    has_new=has_new,
                    has_used=has_used,
                    relevance_score=rel_score,
                    best_new_price=best_new_p,
                    best_used_price=best_used_p,
                    condition_breakdown=cond_breakdown,
                    is_used_only=is_used_only,
                    new_offers_count=len(new_purchasable),
                    used_offers_count=len(used_purchasable),
                    new_sellers_count=len(new_sellers_set),
                    used_sellers_count=len(used_sellers_set),
                    variants_count=item.get("variants_count", 0),
                    has_gift=any(split_added_product(od.get("offer_title"))[1] is not None for od in offers_data),
                )
                product_results.append(card)
                total_offers_count += len(aggregated_offers)

            # 4. Sorting results
            if sort_by == "price_asc":
                product_results.sort(key=lambda x: (not x.has_purchasable_offers, x.min_price or float("inf")))
            elif sort_by == "price_desc":
                product_results.sort(key=lambda x: (not x.has_purchasable_offers, -(x.min_price or 0)))
            elif sort_by == "sellers_count":
                product_results.sort(key=lambda x: x.sellers_count, reverse=True)
            elif sort_by == "discount":
                product_results.sort(
                    key=lambda x: (x.best_offer.old_price - x.best_offer.price)
                    if (x.best_offer and x.best_offer.old_price and x.best_offer.old_price > x.best_offer.price)
                    else 0,
                    reverse=True,
                )
            else:  # relevance
                product_results.sort(key=lambda x: x.relevance_score, reverse=True)

            latency_ms = (time.perf_counter() - start_time) * 1000.0

            # 5. Outcome determination (C-E11-05)
            if not product_results:
                outcome = SearchOutcome.NOT_FOUND
            elif len(product_results) >= 1 and product_results[0].relevance_score >= 8.0:
                outcome = SearchOutcome.FOUND
            else:
                outcome = SearchOutcome.WEAK_MATCH

            facets_info: Dict[str, Any] = {}
            facets_available = True
            has_full_facets = hasattr(self.search_repo, "get_search_facets")
            if has_full_facets:
                try:
                    facets_info = self.search_repo.get_search_facets(
                        search_text=parsed.clean_query,
                        brand=target_brand,
                        model=model,
                        kind=kind,
                        city=target_city,
                        condition=target_condition,
                        category=category,
                        channel_type=target_channel,
                        seller_type=seller_type,
                        seller_id=seller_id,
                        installment=installment, gift=gift,
                        in_stock=in_stock,
                        delivery=delivery,
                        min_price=target_min_price,
                        max_price=target_max_price,
                    )
                except Exception as ex:
                    # Страница не подменяет полную статистику: интерфейс честно скажет о сбое
                    logger.exception(f"Search facets failed: {type(ex).__name__}")
                    facets_available = False

            # 6. S2-E17: Category facets calculation
            cat_counter: Dict[str, int] = {}
            if has_full_facets:
                for item in facets_info.get("category_facets", []):
                    c_name = (item.get("name") or "").strip()
                    if c_name:
                        cat_counter[c_name] = cat_counter.get(c_name, 0) + int(item.get("count", 0))
            else:
                for pr in product_results:
                    c_name = (pr.product.category or "").strip()
                    if c_name:
                        cat_counter[c_name] = cat_counter.get(c_name, 0) + 1

            try:
                from domain.category import TaxonomyMapper
                from repositories.category_repo import CategoryRepository
                t_mapper = (CategoryRepository(self.search_repo.conn).get_mapper()
                            if hasattr(self.search_repo, 'conn') else TaxonomyMapper())
            except Exception:
                t_mapper = None

            # Если задана категория, фасеты категорий ограничиваются только ее веткой (дети или сиблинги)
            allowed_cat_ids = None
            if category and t_mapper:
                curr_cat = t_mapper.map_category(category) or t_mapper.get_by_id(category)
                if curr_cat:
                    target_root_id = curr_cat.parent_id if curr_cat.parent_id else curr_cat.id
                    allowed_cat_ids = set(t_mapper.get_subtree_ids(target_root_id))

            # Разные сырые категории магазинов одной категории таксономии — одно значение фасета.
            # Несопоставленная категория остаётся как есть: это значение понимает фильтр.
            category_by_id: Dict[str, Dict[str, Any]] = {}
            for c_name, c_count in cat_counter.items():
                matched = t_mapper.map_category(c_name) if t_mapper else None
                facet_id = matched.id if matched else c_name
                if allowed_cat_ids is not None and matched and matched.id not in allowed_cat_ids:
                    continue
                entry = category_by_id.setdefault(facet_id, {
                    "id": facet_id,
                    "name": matched.name if matched else c_name,
                    "icon": matched.icon if matched else "📦",
                    "count": 0,
                })
                entry["count"] += c_count
            category_facets = sorted(category_by_id.values(), key=lambda x: -x["count"])

            # 7. S2-E18: Brand facets calculation
            from domain.query_parser import BRAND_MAPPINGS
            brand_counter: Dict[str, int] = {}
            if has_full_facets:
                for item in facets_info.get("brand_facets", []):
                    b_name = (item.get("brand") or "").strip()
                    if b_name:
                        canon_b = BRAND_MAPPINGS.get(b_name.lower(), b_name)
                        brand_counter[canon_b] = brand_counter.get(canon_b, 0) + int(item.get("count", 0))
            else:
                for pr in product_results:
                    b_name = (pr.product.brand or "").strip()
                    if b_name:
                        canon_b = BRAND_MAPPINGS.get(b_name.lower(), b_name)
                        brand_counter[canon_b] = brand_counter.get(canon_b, 0) + 1

            brand_facets = [
                {"brand": b, "count": cnt}
                for b, cnt in sorted(brand_counter.items(), key=lambda x: -x[1])
            ]

            # 8. Dynamic attribute facets
            attribute_facets = []
            if hasattr(self.search_repo, "attribute_facets"):
                try:
                    schema = {}
                    if category and hasattr(self.search_repo, "conn"):
                        try:
                            from repositories.category_repo import CategoryRepository
                            cat_repo = CategoryRepository(self.search_repo.conn)
                            cat = cat_repo.get_by_id(category) or cat_repo.get_mapper().map_category(category)
                            if cat and hasattr(cat, "attributes_schema") and cat.attributes_schema:
                                schema = dict(cat.attributes_schema)
                        except Exception:
                            pass
                    search_args = {
                        "query": query,
                        "city": target_city,
                        "condition": target_condition,
                        "channel_type": target_channel,
                        "seller_type": seller_type,
                        "seller_id": seller_id,
                        "installment": installment,
                        "gift": gift,
                        "in_stock": in_stock,
                        "delivery": delivery,
                        "min_price": target_min_price,
                        "max_price": target_max_price,
                        "category": category,
                        "brand": target_brand,
                        "model": model,
                        "kind": kind,
                    }
                    attribute_facets = self.search_repo.attribute_facets(search_args, schema) or []
                except Exception as ex:
                    logger.exception(f"Search attribute facets failed: {type(ex).__name__}")

            exact_total_products = count_info.get("total_products") if "total_products" in count_info else len(product_results)
            total_pages = max(1, (exact_total_products + limit_val - 1) // limit_val) if exact_total_products > 0 else 1
            has_next = (page_val < total_pages)
            has_prev = (page_val > 1)

            return SearchResponse2(
                query=query,
                parsed_query=parsed,
                items=product_results[:limit_val],
                total_products=exact_total_products,
                total_offers=total_offers_count,
                latency_ms=round(latency_ms, 2),
                outcome=outcome,
                category_facets=category_facets,
                brand_facets=brand_facets,
                model_facets=facets_info.get("model_facets", []),
                kind_facets=facets_info.get("kind_facets", []),
                seller_facets=facets_info.get("seller_facets", []),
                city_facets=facets_info.get("city_facets", []),
                attribute_facets=attribute_facets,
                facets_available=facets_available,
                page=page_val,
                page_size=limit_val,
                total_pages=total_pages,
                has_next=has_next,
                has_prev=has_prev,
            )

        except Exception as e:
            # C-E11-05 / A07: Catch database/internal operational errors, log without raw user query PII
            logger.exception(f"SearchCore execution error: {type(e).__name__} (len={len(query) if query else 0})")
            latency_ms = (time.perf_counter() - start_time) * 1000.0
            fallback_parsed = parsed or QueryParser.parse(query)
            return SearchResponse2(
                query=query,
                parsed_query=fallback_parsed,
                items=[],
                total_products=0,
                total_offers=0,
                latency_ms=round(latency_ms, 2),
                outcome=SearchOutcome.ERROR,
            )

    def _family_options(self, product: CanonicalProduct, city: Optional[str]) -> List[Dict[str, Any]]:
        current_family = family_id(product)
        if current_family is None or not hasattr(self.search_repo, "get_family_products"):
            return []
        siblings = self.search_repo.get_family_products(current_family, city=city)
        return build_family_options(product.id, product.category, product.title, siblings)

    def get_canonical_product(
        self,
        product_id: str,
        city: Optional[str] = None,
        now: Optional[datetime.datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieves canonical product details with grouped sellers, channels, and verified offers.
        Follows domain purchasability and trust provenance rules (S2-E18, S2-E28).
        """
        now = utc_now(now)
        if not hasattr(self.search_repo, "get_canonical_product_detail"):
            return None

        detail = self.search_repo.get_canonical_product_detail(product_id, city=city)
        if not detail or not detail.get("product"):
            return None

        product: CanonicalProduct = detail["product"]
        offer_rows = detail.get("offers", [])

        aggregated_offers: List[AggregatedOffer] = []
        for od in offer_rows:
            obs_at = od.get("observed_at")
            exp_at = od.get("expires_at")
            cond = od.get("condition") or Condition.NEW
            avail = od.get("availability") or "in_stock"
            active = bool(od.get("is_active", 1))

            off_obj = Offer(
                id=od["id"],
                product_id=od["product_id"],
                seller_id=od["seller_id"],
                channel_id=od["channel_id"],
                external_sku=od.get("external_sku", ""),
                url=od["url"],
                image_url=od.get("image_url"),
                price=float(od["price"]),
                old_price=float(od["old_price"]) if od.get("old_price") is not None else None,
                condition=cond,
                availability=avail,
                city=od.get("city", "Казахстан"),
                observed_at=obs_at,
                expires_at=exp_at,
                is_active=active,
            )
            # Channel and seller models
            ch_model = Channel(
                id=od["channel_id"],
                seller_id=od["seller_id"],
                channel_type=od.get("channel_type") or ChannelType.WEBSITE,
                name=od.get("channel_name", "Web"),
                base_url=od["url"],
                is_active=True,
            )
            meta = {}
            if isinstance(od.get("seller_metadata_json"), str):
                try:
                    meta = json.loads(od["seller_metadata_json"])
                except Exception:
                    pass
            s_model = Seller(
                id=od["seller_id"],
                name=od["seller_name"],
                slug=od["seller_slug"],
                rating=float(od["seller_rating"]) if od.get("seller_rating") is not None else None,
                domain=od.get("seller_domain"),
                phone=od.get("seller_phone"),
                bin=od.get("seller_bin"),
                legal_name=od.get("seller_legal_name"),
                metadata=meta,
                is_active=True,
            )

            is_purch = offer_purchasable(od, now=now)
            fresh_status = offer_freshness(od, now=now).legacy_freshness
            prov = build_offer_provenance(od, now=now)
            pay = offer_payment_terms(od)
            basis = offer_price_basis(od)

            agg = AggregatedOffer(
                id=od["id"],
                product_id=od["product_id"],
                seller_id=od["seller_id"],
                seller_name=od["seller_name"],
                seller_slug=od["seller_slug"],
                seller_rating=float(od["seller_rating"]) if od.get("seller_rating") is not None else None,
                channel_id=od["channel_id"],
                channel_type=od.get("channel_type", "website"),
                channel_name=od.get("channel_name", "Web"),
                price=float(od["price"]),
                old_price=float(od["old_price"]) if od.get("old_price") is not None else None,
                condition=cond,
                availability=avail,
                city=od.get("city", "Казахстан"),
                url=od["url"],
                image_url=od.get("image_url") or product.image_url,
                observed_at=obs_at,
                freshness=fresh_status,
                is_purchasable=is_purch,
                expires_at=exp_at,
                is_private_seller=bool(od.get("is_private_seller")),
                installment_available=pay.get("installment_available", False),
                installment_months=pay.get("installment_months"),
                credit_available=pay.get("credit_available", False),
                **offer_delivery_terms(od),
                is_unit_price=basis.get("is_unit_price", False),
                quantity=basis.get("quantity"),
                unit=basis.get("unit"),
                source=ch_model.channel_type,
                published_at=od.get("published_at"),
                provenance=prov.to_dict(),
                seller_domain=s_model.domain,
                seller_phone=s_model.phone,
                seller_bin=s_model.bin,
                seller_legal_name=s_model.legal_name,
                seller_metadata_json=od.get("seller_metadata_json"),
                title=od.get("offer_title"),
                options=offer_options(od.get("offer_title")),
            )
            aggregated_offers.append(agg)

        mark_gifts(aggregated_offers)

        # Sort offers: purchasable first, then price ASC
        aggregated_offers.sort(key=lambda o: (not o.is_purchasable, o.price))
        purchasable_offers = [o for o in aggregated_offers if o.is_purchasable]

        # Group by seller
        seller_map: Dict[str, List[AggregatedOffer]] = {}
        for o in aggregated_offers:
            seller_map.setdefault(o.seller_id, []).append(o)

        seller_groups: List[SellerOffersGroup] = []
        for s_id, s_offers in seller_map.items():
            s_offers.sort(key=lambda x: (not x.is_purchasable, x.price))
            s_first = s_offers[0]
            s_channels = list(dict.fromkeys(x.channel_type for x in s_offers if x.channel_type))
            s_purch = [x for x in s_offers if x.is_purchasable]
            s_best = s_purch[0] if s_purch else s_first

            s_new = [x for x in s_offers if x.condition == Condition.NEW]
            s_used = [x for x in s_offers if x.condition in (Condition.USED, Condition.REFURBISHED, Condition.OPEN_BOX)]
            s_new_p = [x for x in s_purch if x.condition == Condition.NEW]
            s_used_p = [x for x in s_purch if x.condition in (Condition.USED, Condition.REFURBISHED, Condition.OPEN_BOX)]

            sg_min_price = min(x.price for x in s_purch) if s_purch else None
            sg_max_price = max(x.price for x in s_purch) if s_purch else None
            best_np = min(x.price for x in s_new_p) if s_new_p else None
            best_up = min(x.price for x in s_used_p) if s_used_p else None

            s_obj = Seller(
                id=s_id,
                name=s_first.seller_name,
                slug=s_first.seller_slug,
                domain=s_first.seller_domain,
                phone=s_first.seller_phone,
                bin=s_first.seller_bin,
                legal_name=s_first.seller_legal_name,
            )
            s_prov = build_seller_provenance(
                s_obj,
                channels=[Channel(id=x.channel_id, seller_id=s_id, channel_type=x.channel_type, name=x.channel_name, base_url=x.url) for x in s_offers],
                offers=[{"observed_at": x.observed_at} for x in s_offers],
                now=now,
            )

            seller_groups.append(SellerOffersGroup(
                seller_id=s_id,
                seller_name=s_first.seller_name,
                seller_slug=s_first.seller_slug,
                seller_rating=s_first.seller_rating,
                is_private_seller=s_first.is_private_seller,
                min_price=sg_min_price,
                max_price=sg_max_price,
                offers_count=len(s_offers),
                channels_count=len(s_channels),
                channels_available=s_channels,
                best_offer=s_best,
                offers=s_offers,
                has_new=bool(s_new),
                has_used=bool(s_used),
                best_new_price=best_np,
                best_used_price=best_up,
                facts=list(s_prov.fact_labels),
                provenance=s_prov.to_dict(),
            ))

        seller_groups.sort(key=lambda s: (
            not (s.best_offer and s.best_offer.is_purchasable and s.min_price is not None),
            s.min_price if s.min_price is not None else float("inf"),
        ))

        new_purchasable = [o for o in purchasable_offers if o.condition == Condition.NEW]
        used_purchasable = [o for o in purchasable_offers if o.condition in (Condition.USED, Condition.REFURBISHED, Condition.OPEN_BOX)]

        min_p = min(o.price for o in purchasable_offers) if purchasable_offers else None
        max_p = max(o.price for o in purchasable_offers) if purchasable_offers else None
        best_off = purchasable_offers[0] if purchasable_offers else (aggregated_offers[0] if aggregated_offers else None)
        best_new_p = min(o.price for o in new_purchasable) if new_purchasable else None
        best_used_p = min(o.price for o in used_purchasable) if used_purchasable else None

        cond_breakdown = build_condition_breakdown(product.id, product.title, aggregated_offers).to_dict()

        # Load history report
        history_dict = None
        try:
            from repositories.history_repo import HistoryRepository
            hist_repo = HistoryRepository(self.search_repo.conn)
            hist_report = hist_repo.get_product_price_history(product.id, limit_per_series=200)
            if hist_report:
                history_dict = hist_report.to_dict()
        except Exception as ex:
            logger.warning(f"Price history lookup error for {product.id}: {ex}")

        # Resolve category taxonomy breadcrumbs
        cat_breadcrumbs = []
        try:
            from domain.category import TaxonomyMapper
            from repositories.category_repo import CategoryRepository
            mapper = (CategoryRepository(self.search_repo.conn).get_mapper()
                      if hasattr(self.search_repo, 'conn') else TaxonomyMapper())
            raw_c = product.category or ""
            target_cat = mapper.map_category(raw_c) or mapper.get_by_id(raw_c)
            if target_cat:
                cat_breadcrumbs = mapper.get_breadcrumbs(target_cat.id)
            elif raw_c:
                cat_breadcrumbs = [{"id": raw_c, "name": raw_c, "icon": "📦"}]
        except Exception as ex:
            logger.warning(f"Breadcrumb resolution error for {product.id}: {ex}")
            if product.category:
                cat_breadcrumbs = [{"id": product.category, "name": product.category, "icon": "📦"}]

        return {
            "ok": True,
            "product": product.to_dict(),
            "breadcrumbs": cat_breadcrumbs,
            "min_price": min_p,
            "max_price": max_p,
            "best_new_price": best_new_p,
            "best_used_price": best_used_p,
            "sellers_count": len(seller_groups),
            "offers_count": len(aggregated_offers),
            "has_purchasable_offers": bool(purchasable_offers),
            "best_offer": best_off.to_dict() if best_off else None,
            "sellers": [s.to_dict() for s in seller_groups],
            "offers": [o.to_dict() for o in aggregated_offers],
            "has_gift": any(o.gift is not None for o in aggregated_offers),
            "options": self._family_options(product, city) + build_options(aggregated_offers),
            "condition_breakdown": cond_breakdown,
            "history": history_dict,
        }
