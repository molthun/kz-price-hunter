"""
Adaptive Discovery & Query Demand Generator (S2-E14, KZ Price Hunter 2.0).
Transforms unsatisfied search demand signals (QueryDemandSignal from S2-E13)
into targeted merchant discovery and catalog acquisition tasks.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from domain.models import utc_now_iso
from domain.search_analytics import QueryDemandSignal


class DiscoveryTaskStatus:
    """Lifecycle states of an adaptive discovery acquisition task."""
    PENDING = "PENDING"          # Generated, awaiting crawler/operator dispatch
    RUNNING = "RUNNING"          # Discovery crawler currently executing
    COMPLETED = "COMPLETED"      # Candidates found and linked
    FAILED = "FAILED"            # Discovery source error or zero candidates found
    CANCELLED = "CANCELLED"      # Obsolete or manually rejected

    ALL = {PENDING, RUNNING, COMPLETED, FAILED, CANCELLED}


class TargetSearchChannel:
    """Channels targeted by discovery query generation."""
    GOOGLE = "google"
    KASPI = "kaspi"
    TWOGIS = "2gis"
    INSTAGRAM = "instagram"
    TELEGRAM = "telegram"
    WEB = "web"

    ALL = {GOOGLE, KASPI, TWOGIS, INSTAGRAM, TELEGRAM, WEB}


@dataclass
class GeneratedSearchQuery:
    """A search engine/channel query generated for catalog/merchant discovery."""
    channel: str
    query: str
    intent: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DiscoveryTask:
    """
    A targeted task to discover new merchants or channels based on unsatisfied query demand.
    """
    id: str = field(default_factory=lambda: f"dtask_{uuid.uuid4().hex[:12]}")
    signal_query: str = ""
    normalized_query: str = ""
    city: Optional[str] = None
    search_count: int = 0
    success_rate: float = 0.0
    priority_score: float = 0.0
    status: str = DiscoveryTaskStatus.PENDING
    search_queries: List[Dict[str, str]] = field(default_factory=list)
    discovered_candidates_count: int = 0
    notes: Optional[str] = None
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Any) -> DiscoveryTask:
        queries_val = row["search_queries"]
        if isinstance(queries_val, str):
            try:
                queries_list = json.loads(queries_val)
            except Exception:
                queries_list = []
        elif isinstance(queries_val, list):
            queries_list = queries_val
        else:
            queries_list = []

        return cls(
            id=row["id"],
            signal_query=row["signal_query"],
            normalized_query=row["normalized_query"],
            city=row["city"],
            search_count=int(row["search_count"]),
            success_rate=float(row["success_rate"]),
            priority_score=float(row["priority_score"]),
            status=row["status"],
            search_queries=queries_list,
            discovered_candidates_count=int(row.get("discovered_candidates_count", 0)),
            notes=row.get("notes"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class SearchPatternGenerator:
    """
    Generates tailored query strings across search engines and platforms
    to find merchants selling the requested product/category in Kazakhstan.
    """

    @classmethod
    def generate_queries(cls, query: str, city: Optional[str] = None) -> List[GeneratedSearchQuery]:
        clean_q = re.sub(r"[^\w\s-]", " ", query).strip()
        if not clean_q:
            return []

        city_part = f" {city}" if city and city != "Все" else ""
        city_kz = f" {city} Казахстан" if city and city != "Все" else " Казахстан"

        queries: List[GeneratedSearchQuery] = []

        # 1. Google / Web Search queries
        queries.append(GeneratedSearchQuery(
            channel=TargetSearchChannel.GOOGLE,
            query=f"купить {clean_q}{city_part}",
            intent="commercial_purchase",
        ))
        queries.append(GeneratedSearchQuery(
            channel=TargetSearchChannel.GOOGLE,
            query=f"интернет магазин {clean_q}{city_kz}",
            intent="merchant_catalog",
        ))
        queries.append(GeneratedSearchQuery(
            channel=TargetSearchChannel.GOOGLE,
            query=f"{clean_q} цена в наличии{city_part} сайт",
            intent="direct_retailer",
        ))

        # 2. Kaspi / Marketplace queries
        queries.append(GeneratedSearchQuery(
            channel=TargetSearchChannel.KASPI,
            query=f"{clean_q}",
            intent="marketplace_merchants",
        ))

        # 3. 2GIS / Local business search
        if city and city != "Все":
            queries.append(GeneratedSearchQuery(
                channel=TargetSearchChannel.TWOGIS,
                query=f"{clean_q} {city}",
                intent="local_shops",
            ))

        # 4. Social & Messenger queries
        loc = city if city and city != "Все" else "Алматы"
        queries.append(GeneratedSearchQuery(
            channel=TargetSearchChannel.INSTAGRAM,
            query=f"{clean_q} {loc} instagram",
            intent="social_storefront",
        ))
        queries.append(GeneratedSearchQuery(
            channel=TargetSearchChannel.TELEGRAM,
            query=f"{clean_q} канал kz",
            intent="messenger_catalog",
        ))

        return queries


class AdaptiveDiscoveryEngine:
    """
    Engine that evaluates search demand signals and constructs actionable DiscoveryTasks.
    """

    @staticmethod
    def calculate_priority(search_count: int, success_rate: float) -> float:
        """
        Calculates priority score:
        High search count + low success rate = maximum priority.
        Range: ~0.0 to 100.0+
        """
        demand_factor = max(1, search_count)
        # Deficit factor: 100% fail -> 1.0, 50% success -> 0.5, 100% success -> 0.0
        deficit_factor = max(0.0, 1.0 - (max(0.0, min(100.0, success_rate)) / 100.0))
        raw_score = demand_factor * deficit_factor
        return round(raw_score, 2)

    @classmethod
    def task_from_signal(cls, signal: QueryDemandSignal) -> DiscoveryTask:
        """Constructs a DiscoveryTask from an unmet QueryDemandSignal."""
        priority = cls.calculate_priority(signal.search_count, signal.success_rate)
        generated_queries = SearchPatternGenerator.generate_queries(
            query=signal.sample_query or signal.normalized_query,
            city=signal.city,
        )

        return DiscoveryTask(
            signal_query=signal.sample_query or signal.normalized_query,
            normalized_query=signal.normalized_query,
            city=signal.city,
            search_count=signal.search_count,
            success_rate=signal.success_rate,
            priority_score=priority,
            status=DiscoveryTaskStatus.PENDING,
            search_queries=[q.to_dict() for q in generated_queries],
            discovered_candidates_count=0,
            notes=f"Generated from search demand signal (searches: {signal.search_count}, SSR: {signal.success_rate}%)",
        )
