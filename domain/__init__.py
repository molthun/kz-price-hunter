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

from domain.social_content import (
    ContentObservation,
    ContentType,
    ExtractedOfferCandidate,
    SocialContentExtractor,
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

from domain.freshness_v2 import (
    FreshnessStateV2,
    OfferFreshnessResult,
    SourceFreshnessType,
    evaluate_offer_freshness,
    infer_source_freshness_type,
)

from domain.price_history_v2 import (
    PriceHistoryPoint,
    PriceHistorySeries,
    ProductPriceHistoryReport,
    build_price_history_series,
    make_price_series_key,
)

from domain.provenance import (
    OfferProvenance,
    SellerProvenance,
    build_offer_provenance,
    build_seller_provenance,
)

from domain.vision_extraction import (
    MIN_VISION_PUBLISH_CONFIDENCE,
    PRICE_NOT_DETERMINED,
    VisionCandidate,
    VisionExtractionEngine,
)

from domain.search_assistant import (
    SearchAssistant,
    SearchAssistantPlan,
    SearchAssistantResponse,
)

from domain.seller_onboarding import (
    OnboardingResult,
    SellerOnboardingSubmission,
    prepare_onboarding_candidate,
    validate_onboarding_submission,
)

from domain.monitoring_v2 import (
    MonitoringOverviewV2,
    build_monitoring_overview_v2,
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
    "ContentObservation",
    "ContentType",
    "CrossSellerRanking",
    "DeliveryType",
    "DiscoverySource",
    "DiscoveryTask",
    "DiscoveryTaskStatus",
    "ExtractedOfferCandidate",
    "ExtractionTier",
    "FreshnessStateV2",
    "GeneratedSearchQuery",
    "IdentityType",
    "KZ_CITIES_MAP",
    "MIN_VISION_PUBLISH_CONFIDENCE",
    "MatchDecision",
    "MonitoringOverviewV2",
    "Offer",
    "OfferFreshnessResult",
    "OfferPriceHistory",
    "OfferProvenance",
    "OnboardingResult",
    "PRICE_NOT_DETERMINED",
    "ParsedQuery",
    "PaymentMethod",
    "PriceHistoryPoint",
    "PriceHistorySeries",
    "ProductChannelMatrix",
    "ProductPriceHistoryReport",
    "ProductSearchResult",
    "QueryDemandSignal",
    "QueryParser",
    "SearchAssistant",
    "SearchAssistantPlan",
    "SearchAssistantResponse",
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
    "SellerOnboardingSubmission",
    "SellerProvenance",
    "SocialContentExtractor",
    "SourceFreshnessType",
    "SourceProfile",
    "SourceProfiler",
    "TargetSearchChannel",
    "VisionCandidate",
    "VisionExtractionEngine",
    "build_channel_matrix",
    "build_condition_breakdown",
    "build_monitoring_overview_v2",
    "build_offer_provenance",
    "build_price_history_series",
    "build_seller_provenance",
    "evaluate_offer_freshness",
    "filter_offers_by_condition",
    "infer_source_freshness_type",
    "make_price_series_key",
    "normalize_bin",
    "normalize_business_name",
    "normalize_domain",
    "normalize_phone",
    "normalize_search_query",
    "prepare_onboarding_candidate",
    "validate_onboarding_submission",
]
