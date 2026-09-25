"""
Domain package for KZ Price Hunter 2.0 (Search Platform).
"""

from domain.models import (
    Availability,
    CanonicalProduct,
    Channel,
    ChannelType,
    Condition,
    Offer,
    OfferPriceHistory,
    Seller,
)

from domain.seller_identity import (
    IdentityType,
    MatchDecision,
    SellerIdentity,
    SellerMatchResult,
    normalize_bin,
    normalize_business_name,
    normalize_domain,
    normalize_phone,
)

from domain.seller_matcher import SellerMatcher

__all__ = [
    "Availability",
    "CanonicalProduct",
    "Channel",
    "ChannelType",
    "Condition",
    "Offer",
    "OfferPriceHistory",
    "Seller",
    "IdentityType",
    "MatchDecision",
    "SellerIdentity",
    "SellerMatchResult",
    "SellerMatcher",
    "normalize_bin",
    "normalize_business_name",
    "normalize_domain",
    "normalize_phone",
]
