"""
Search Quality & Analytics (S2-E13, KZ Price Hunter 2.0).
Provides data structures and metrics for tracking Search Success Rate (SSR),
measuring search outcomes (FOUND, WEAK_MATCH, NOT_FOUND, ERROR),
and generating Query Demand Signals for Adaptive Discovery (S2-E14).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid

from domain.search_core import SearchOutcome


def normalize_search_query(query: str) -> str:
    """
    Normalizes a search query for aggregation:
    - Lowercase
    - Removes punctuation and extra whitespace
    - Sorts words for permutation invariance ('айфон 16' == '16 айфон')
    """
    if not query:
        return ""
    cleaned = re.sub(r"[^\w\s]", " ", query.lower(), flags=re.UNICODE)
    tokens = [t.strip() for t in cleaned.split() if t.strip()]
    tokens.sort()
    return " ".join(tokens)


@dataclass
class SearchEvent:
    """An anonymized search interaction record."""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    query: str = ""
    normalized_query: str = ""
    city: Optional[str] = None
    condition: Optional[str] = None
    channel_type: Optional[str] = None
    outcome: str = SearchOutcome.NOT_FOUND
    total_products: int = 0
    total_offers: int = 0
    latency_ms: float = 0.0
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self):
        import search_analytics as sa
        # P06/C-E13-01: Anonymize and redact sensitive personal text (phone, email, address, numbers)
        if sa.is_sensitive(self.query):
            self.query = "[redacted]"
            self.normalized_query = "[redacted]"
        else:
            clean_q = str(self.query or "").strip()[:sa.MAX_QUERY_CHARS]
            self.query = clean_q
            if not self.normalized_query and clean_q:
                self.normalized_query = normalize_search_query(clean_q)

        # C-E13-03: Canonicalize city through common city registry
        self.city = sa.canonical_city(self.city)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Any) -> SearchEvent:
        return cls(
            id=row["id"],
            query=row["query"],
            normalized_query=row["normalized_query"],
            city=row["city"],
            condition=row["condition"],
            channel_type=row["channel_type"],
            outcome=row["outcome"],
            total_products=int(row["total_products"]),
            total_offers=int(row["total_offers"]),
            latency_ms=float(row["latency_ms"]),
            created_at=row["created_at"],
        )


@dataclass
class SearchQualitySummary:
    """Aggregated Search Quality KPI across a time window."""
    total_searches: int = 0
    answered_count: int = 0
    found_count: int = 0
    weak_match_count: int = 0
    not_found_count: int = 0
    error_count: int = 0
    search_success_rate: float = 0.0
    error_rate: float = 0.0
    avg_latency_ms: float = 0.0
    period_start: Optional[str] = None
    period_end: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class QueryDemandSignal:
    """
    Identifies high-demand, low-fulfillment queries to guide Adaptive Discovery (S2-E14).
    If users frequently search for items that result in NOT_FOUND or WEAK_MATCH,
    this signal triggers Discovery to find new sellers/channels for this catalog segment.
    """
    normalized_query: str
    sample_query: str
    search_count: int
    success_rate: float
    city: Optional[str] = None
    needs_discovery: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
