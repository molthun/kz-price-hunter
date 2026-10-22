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

from domain.query_parser import (
    KZ_CITIES_MAP,
    ParsedQuery,
    QueryParser,
)

from domain.search_core import (
    AggregatedOffer,
    ProductSearchResult,
    SearchCore,
    SearchOutcome,
    SearchResponse2,
)

from domain.search_analytics import (
    QueryDemandSignal,
    SearchEvent,
    SearchQualitySummary,
    normalize_search_query,
)

from domain.adaptive_discovery import (
    AdaptiveDiscoveryEngine,
    DiscoveryTask,
    DiscoveryTaskStatus,
    GeneratedSearchQuery,
    SearchPatternGenerator,
    TargetSearchChannel,
)

__all__ = [
    "AdaptiveDiscoveryEngine",
    "AggregatedOffer",
    "Availability",
    "CandidateStatus",
    "CanonicalProduct",
    "CatalogFormat",
    "Channel",
    "ChannelSavings",
    "ChannelType",
    "Condition",
    "ConditionBreakdown",
    "ConditionFilter",
    "CrossSellerRanking",
    "DeliveryType",
    "DiscoverySource",
    "DiscoveryTask",
    "DiscoveryTaskStatus",
    "ExtractionTier",
    "GeneratedSearchQuery",
    "IdentityType",
    "KZ_CITIES_MAP",
    "MatchDecision",
    "Offer",
    "OfferPriceHistory",
    "ParsedQuery",
    "PaymentMethod",
    "ProductChannelMatrix",
    "ProductSearchResult",
    "QueryDemandSignal",
    "QueryParser",
    "SearchCore",
    "SearchEvent",
    "SearchOutcome",
    "SearchPatternGenerator",
    "SearchQualitySummary",
    "SearchResponse2",
    "Seller",
    "SellerCandidate",
    "SellerChannelPricing",
    "SellerIdentity",
    "SellerMatchResult",
    "SourceProfile",
    "SourceProfiler",
    "TargetSearchChannel",
    "build_channel_matrix",
    "build_condition_breakdown",
    "filter_offers_by_condition",
    "normalize_bin",
    "normalize_business_name",
    "normalize_domain",
    "normalize_phone",
    "normalize_search_query",
]



