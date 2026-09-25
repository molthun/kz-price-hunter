"""
Domain models and calculation logic for Channel-Aware Pricing (KZ Price Hunter 2.0).
Differentiates between:
1. Cross-Channel pricing: Same seller in different sales channels (Direct vs Kaspi vs Forte vs Halyk).
2. Cross-Seller pricing: Independent sellers competing on the same canonical product.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from domain.condition import (
    ConditionBreakdown,
    ConditionFilter,
    build_condition_breakdown,
    filter_offers_by_condition,
)
from domain.models import ChannelType, Condition, Offer, Seller, utc_now_iso



class PaymentMethod:
    CASH = "cash"
    CARD = "card"
    KASPI_QR = "kaspi_qr"
    KASPI_RED = "kaspi_red"
    INSTALLMENT = "installment"
    CREDIT = "credit"
    ONLINE = "online"

    ALL = {CASH, CARD, KASPI_QR, KASPI_RED, INSTALLMENT, CREDIT, ONLINE}


class DeliveryType:
    FREE = "free"
    PAID = "paid"
    PICKUP = "pickup"
    COURIER = "courier"
    POST = "post"

    ALL = {FREE, PAID, PICKUP, COURIER, POST}


@dataclass
class ChannelSavings:
    """
    Represents savings when buying directly from a seller instead of via marketplace.
    Example: 4mobile direct 499 000 ₸ vs 4mobile on Kaspi 539 000 ₸.
    """
    seller_id: str
    seller_name: str
    direct_channel_id: str
    direct_channel_type: str
    direct_price: float
    marketplace_channel_id: str
    marketplace_channel_type: str
    marketplace_price: float
    savings_amount: float
    savings_percent: float
    installment_months: Optional[int] = None
    tradeoff_note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SellerChannelPricing:
    """
    Price profile of a single seller across all their active channels for a product.
    """
    seller_id: str
    seller_slug: str
    seller_name: str
    min_price: float
    best_offer: Optional[Offer] = None
    direct_offer: Optional[Offer] = None
    marketplace_offers: List[Offer] = field(default_factory=list)
    channel_offers: Dict[str, Offer] = field(default_factory=dict)  # channel_type -> Offer
    cross_channel_savings: Optional[ChannelSavings] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "seller_id": self.seller_id,
            "seller_slug": self.seller_slug,
            "seller_name": self.seller_name,
            "min_price": self.min_price,
            "best_offer": self.best_offer.to_dict() if self.best_offer else None,
            "direct_offer": self.direct_offer.to_dict() if self.direct_offer else None,
            "marketplace_offers": [o.to_dict() for o in self.marketplace_offers],
            "channel_offers": {k: v.to_dict() for k, v in self.channel_offers.items()},
            "cross_channel_savings": self.cross_channel_savings.to_dict() if self.cross_channel_savings else None,
        }



@dataclass
class CrossSellerRanking:
    """
    Ranking of a distinct seller competing for a canonical product.
    """
    rank: int
    seller_id: str
    seller_name: str
    best_price: float
    best_channel_type: str
    price_difference_from_leader: float
    best_offer: Offer

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rank": self.rank,
            "seller_id": self.seller_id,
            "seller_name": self.seller_name,
            "best_price": self.best_price,
            "best_channel_type": self.best_channel_type,
            "price_difference_from_leader": self.price_difference_from_leader,
            "best_offer": self.best_offer.to_dict(),
        }


@dataclass
class ProductChannelMatrix:
    """
    Complete channel-aware price matrix for a product:
    Combines Cross-Channel seller profiles with Cross-Seller competitive rankings.
    Guarantees strict condition segregation (NEW vs USED vs OPEN_BOX vs REFURBISHED).
    """
    product_id: str
    canonical_key: str
    title: str
    condition: str
    city: str
    best_overall_price: float
    best_direct_price: Optional[float] = None
    best_marketplace_price: Optional[float] = None
    best_new_price: Optional[float] = None
    best_used_price: Optional[float] = None
    best_open_box_price: Optional[float] = None
    best_refurbished_price: Optional[float] = None
    condition_breakdown: Optional[ConditionBreakdown] = None
    sellers_pricing: List[SellerChannelPricing] = field(default_factory=list)
    cross_channel_opportunities: List[ChannelSavings] = field(default_factory=list)
    cross_seller_rankings: List[CrossSellerRanking] = field(default_factory=list)
    computed_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "product_id": self.product_id,
            "canonical_key": self.canonical_key,
            "title": self.title,
            "condition": self.condition,
            "city": self.city,
            "best_overall_price": self.best_overall_price,
            "best_direct_price": self.best_direct_price,
            "best_marketplace_price": self.best_marketplace_price,
            "best_new_price": self.best_new_price,
            "best_used_price": self.best_used_price,
            "best_open_box_price": self.best_open_box_price,
            "best_refurbished_price": self.best_refurbished_price,
            "condition_breakdown": self.condition_breakdown.to_dict() if self.condition_breakdown else None,
            "sellers_pricing": [s.to_dict() for s in self.sellers_pricing],
            "cross_channel_opportunities": [c.to_dict() for c in self.cross_channel_opportunities],
            "cross_seller_rankings": [r.to_dict() for r in self.cross_seller_rankings],
            "computed_at": self.computed_at,
        }


def build_channel_matrix(
    product_id: str,
    canonical_key: str,
    title: str,
    offers: List[Offer],
    sellers_map: Dict[str, Seller],
    condition: str = Condition.NEW,
    city: str = "Казахстан",
    channel_types: Optional[Dict[str, str]] = None,
) -> ProductChannelMatrix:
    """
    Builds the channel-aware pricing matrix from a list of active offers.
    Separates cross-channel price variations of a single merchant from
    cross-seller market competition.
    Strictly isolates conditions so USED / OPEN_BOX offers never contaminate NEW prices.
    """
    breakdown = build_condition_breakdown(product_id, title, offers)

    # Filter active offers matching the requested condition tier
    filtered_offers = filter_offers_by_condition(offers, condition)

    if not filtered_offers:
        return ProductChannelMatrix(
            product_id=product_id,
            canonical_key=canonical_key,
            title=title,
            condition=condition,
            city=city,
            best_overall_price=0.0,
            best_new_price=breakdown.best_new_price,
            best_used_price=breakdown.best_used_price,
            best_open_box_price=breakdown.best_open_box_price,
            best_refurbished_price=breakdown.best_refurbished_price,
            condition_breakdown=breakdown,
        )

    # 1. Group offers by seller_id
    offers_by_seller: Dict[str, List[Offer]] = {}
    for off in filtered_offers:
        offers_by_seller.setdefault(off.seller_id, []).append(off)

    # Тип канала берём из таблицы channels, когда он передан: это точный источник.
    # Разбор идентификатора остаётся запасным путём для вызовов без базы.
    types = channel_types or {}

    def channel_type_of(channel_id: str) -> str:
        known = types.get(channel_id)
        return known if known else ChannelType.from_channel_id(channel_id)

    direct_channel_types = {ChannelType.DIRECT, ChannelType.WEBSITE, ChannelType.PHYSICAL_STORE}
    marketplace_channel_types = {ChannelType.KASPI, ChannelType.FORTE, ChannelType.HALYK, ChannelType.MARKETPLACE}

    sellers_pricing: List[SellerChannelPricing] = []
    cross_channel_opportunities: List[ChannelSavings] = []

    best_overall_price = min(o.price for o in filtered_offers)
    direct_prices = [o.price for o in filtered_offers if (channel_type_of(o.channel_id) in direct_channel_types)]
    mkt_prices = [o.price for o in filtered_offers if (channel_type_of(o.channel_id) in marketplace_channel_types)]

    best_direct = min(direct_prices) if direct_prices else None
    best_mkt = min(mkt_prices) if mkt_prices else None


    # 2. Process each seller's channels
    for seller_id, s_offers in offers_by_seller.items():
        seller = sellers_map.get(seller_id)
        s_name = seller.name if seller else f"Продавец {seller_id}"
        s_slug = seller.slug if seller else seller_id

        # Sort offers by price ascending
        s_offers.sort(key=lambda x: x.price)
        min_p = s_offers[0].price

        direct_off: Optional[Offer] = None
        mkt_offs: List[Offer] = []
        chan_offers: Dict[str, Offer] = {}

        for off in s_offers:
            ctype = channel_type_of(off.channel_id)
            if ctype not in chan_offers or off.price < chan_offers[ctype].price:
                chan_offers[ctype] = off

            if ctype in direct_channel_types:
                if direct_off is None or off.price < direct_off.price:
                    direct_off = off
            elif ctype in marketplace_channel_types:
                mkt_offs.append(off)

        # Calculate cross-channel savings if seller sells both direct and on marketplace
        s_savings: Optional[ChannelSavings] = None
        if direct_off and mkt_offs:
            # Pick marketplace offer with lowest price or largest volume
            mkt_offs.sort(key=lambda x: x.price)
            cheapest_mkt = mkt_offs[0]

            if direct_off.price < cheapest_mkt.price:
                diff = cheapest_mkt.price - direct_off.price
                pct = round((diff / cheapest_mkt.price) * 100, 1)

                mkt_ctype = channel_type_of(cheapest_mkt.channel_id)
                note = f"Экономия {int(diff):,} ₸ ({pct}%) при покупке напрямую вместо {mkt_ctype.capitalize()}"
                if cheapest_mkt.installment_months:
                    note += f" (в рассрочку на {cheapest_mkt.installment_months} мес.)"

                s_savings = ChannelSavings(
                    seller_id=seller_id,
                    seller_name=s_name,
                    direct_channel_id=direct_off.channel_id,
                    direct_channel_type=channel_type_of(direct_off.channel_id),
                    direct_price=direct_off.price,
                    marketplace_channel_id=cheapest_mkt.channel_id,
                    marketplace_channel_type=mkt_ctype,
                    marketplace_price=cheapest_mkt.price,
                    savings_amount=diff,
                    savings_percent=pct,
                    installment_months=cheapest_mkt.installment_months,
                    tradeoff_note=note,
                )
                cross_channel_opportunities.append(s_savings)

        best_seller_offer = s_offers[0]

        sp = SellerChannelPricing(
            seller_id=seller_id,
            seller_slug=s_slug,
            seller_name=s_name,
            min_price=min_p,
            best_offer=best_seller_offer,
            direct_offer=direct_off,
            marketplace_offers=mkt_offs,
            channel_offers=chan_offers,
            cross_channel_savings=s_savings,
        )
        sellers_pricing.append(sp)

    # 3. Build Cross-Seller competitive rankings (sorted by min_price)
    sellers_pricing.sort(key=lambda x: x.min_price)
    cross_seller_rankings: List[CrossSellerRanking] = []

    leader_price = sellers_pricing[0].min_price if sellers_pricing else 0.0
    for idx, sp in enumerate(sellers_pricing, 1):
        best_off = sp.best_offer or sp.direct_offer or (sp.marketplace_offers[0] if sp.marketplace_offers else None)
        diff = sp.min_price - leader_price
        cross_seller_rankings.append(
            CrossSellerRanking(
                rank=idx,
                seller_id=sp.seller_id,
                seller_name=sp.seller_name,
                best_price=sp.min_price,
                best_channel_type=channel_type_of(best_off.channel_id) if best_off else ChannelType.UNKNOWN,
                price_difference_from_leader=diff,
                best_offer=best_off,
            )
        )

    # Sort cross channel savings by highest savings amount
    cross_channel_opportunities.sort(key=lambda x: x.savings_amount, reverse=True)

    return ProductChannelMatrix(
        product_id=product_id,
        canonical_key=canonical_key,
        title=title,
        condition=condition,
        city=city,
        best_overall_price=best_overall_price,
        best_direct_price=best_direct,
        best_marketplace_price=best_mkt,
        best_new_price=breakdown.best_new_price,
        best_used_price=breakdown.best_used_price,
        best_open_box_price=breakdown.best_open_box_price,
        best_refurbished_price=breakdown.best_refurbished_price,
        condition_breakdown=breakdown,
        sellers_pricing=sellers_pricing,
        cross_channel_opportunities=cross_channel_opportunities,
        cross_seller_rankings=cross_seller_rankings,
    )

