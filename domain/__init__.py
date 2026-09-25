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

from domain.channel_pricing import (
    ChannelSavings,
    CrossSellerRanking,
    DeliveryType,
    PaymentMethod,
    ProductChannelMatrix,
    SellerChannelPricing,
    build_channel_matrix,
)

from domain.condition import (
    ConditionBreakdown,
    ConditionFilter,
    build_condition_breakdown,
    filter_offers_by_condition,
)

from domain.discovery import (
    CandidateStatus,
    DiscoverySource,
    SellerCandidate,
)

from domain.source_profiler import (
    CatalogFormat,
    ExtractionTier,
    SourceProfile,
    SourceProfiler,
)

__all__ = [
    "Availability",
    "CandidateStatus",
    "CanonicalProduct",
    "CatalogFormat",
    "Channel",
    "ChannelType",
    "Condition",
    "ConditionBreakdown",
    "ConditionFilter",
    "DiscoverySource",
    "ExtractionTier",
    "Offer",
    "OfferPriceHistory",
    "Seller",
    "SellerCandidate",
    "SourceProfile",
    "SourceProfiler",
    "IdentityType",
    "MatchDecision",
    "SellerIdentity",
    "SellerMatchResult",
    "ChannelSavings",
    "CrossSellerRanking",
    "DeliveryType",
    "PaymentMethod",
    "ProductChannelMatrix",
    "SellerChannelPricing",
    "build_channel_matrix",
    "build_condition_breakdown",
    "filter_offers_by_condition",
    "normalize_bin",
    "normalize_business_name",
    "normalize_domain",
    "normalize_phone",
]


