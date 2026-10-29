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

from domain.freshness_v2 import offer_freshness
from domain.models import CanonicalProduct, Condition
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
    # Условия оплаты — только сохранённые в предложении; тип площадки их не доказывает (V05)
    installment_available: bool = False
    installment_months: Optional[int] = None
    credit_available: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


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
class ProductSearchResult:
    """A canonical product aggregating multiple seller offers across channels."""
    product: CanonicalProduct
    offers: List[AggregatedOffer] = field(default_factory=list)
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

    def to_dict(self) -> Dict[str, Any]:
        res = asdict(self)
        res["product"] = self.product.to_dict()
        res["offers"] = [o.to_dict() for o in self.offers]
        res["best_offer"] = self.best_offer.to_dict() if self.best_offer else None
        return res


@dataclass
class SearchResponse2:
    """High-level response containing aggregated multi-merchant search results."""
    query: str
    parsed_query: ParsedQuery
    items: List[ProductSearchResult] = field(default_factory=list)
    total_products: int = 0
    total_offers: int = 0
    latency_ms: float = 0.0
    outcome: str = SearchOutcome.NOT_FOUND

    def to_dict(self) -> Dict[str, Any]:
        res = asdict(self)
        res["parsed_query"] = self.parsed_query.to_dict()
        res["items"] = [item.to_dict() for item in self.items]
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
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
        sort_by: str = "relevance",
        limit: int = 50,
        now: Optional[datetime.datetime] = None,
    ) -> SearchResponse2:
        """
        Executes a 2.0 multi-merchant search query with error handling.
        """
        start_time = time.perf_counter()
        parsed: Optional[ParsedQuery] = None
        # Один момент времени для SQL-отбора и итоговой доступности (R09)
        now = utc_now(now)

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

            # 2. Search canonical products via repository
            raw_items = self.search_repo.search_products(
                search_text=parsed.clean_query,
                brand=parsed.detected_brand,
                city=target_city,
                condition=target_condition,
                channel_type=target_channel,
                min_price=target_min_price,
                max_price=target_max_price,
                sort_by=sort_by,
                limit=limit,
                now=now,
            )

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
                        **offer_payment_terms(od),
                    )
                    aggregated_offers.append(agg_off)

                if not aggregated_offers:
                    continue

                # Sort offers by purchasable first, then price ASC
                aggregated_offers.sort(key=lambda o: (not o.is_purchasable, o.price))

                unique_sellers = {o.seller_id for o in aggregated_offers}
                purchasable_offers = [o for o in aggregated_offers if o.is_purchasable]

                if purchasable_offers:
                    p_prices = [o.price for o in purchasable_offers]
                    min_p = min(p_prices)
                    max_p = max(p_prices)
                    best_off = purchasable_offers[0]
                    conditions = sorted(list({o.condition for o in purchasable_offers}))
                    has_purchasable = True
                else:
                    # C-E11-03: No purchasable offers (all out-of-stock or stale)
                    min_p = None
                    max_p = None
                    best_off = None
                    conditions = []
                    has_purchasable = False

                all_conditions = {o.condition for o in aggregated_offers}
                has_new = Condition.NEW in all_conditions
                has_used = any(c in (Condition.USED, Condition.REFURBISHED, Condition.OPEN_BOX) for c in all_conditions)

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

            return SearchResponse2(
                query=query,
                parsed_query=parsed,
                items=product_results[:limit],
                total_products=len(product_results),
                total_offers=total_offers_count,
                latency_ms=round(latency_ms, 2),
                outcome=outcome,
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
