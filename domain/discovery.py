"""
Domain entities and lifecycle states for Seller Discovery (KZ Price Hunter 2.0).
Manages discovery pipeline from finding new candidate merchants to profiling,
identity matching, and catalog approval.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from domain.models import utc_now_iso


class CandidateStatus:
    """
    Lifecycle status of a newly discovered merchant candidate.
    Strictly follows Stage 5 in SEARCH_PLATFORM_PLAN.md.
    """
    DISCOVERED = "DISCOVERED"                # Found from crawler/discovery source
    PROFILED = "PROFILED"                    # Digital footprint & catalog channels identified
    IDENTITY_MATCHED = "IDENTITY_MATCHED"    # Matched with existing seller in Seller Graph
    CATALOG_FOUND = "CATALOG_FOUND"          # Catalog endpoint/feed confirmed
    REVIEW_REQUIRED = "REVIEW_REQUIRED"      # Ambiguous match, sent to review queue
    APPROVED = "APPROVED"                    # Approved as a distinct or merged merchant
    INDEXED = "INDEXED"                      # Actively collected in catalog runs
    REJECTED = "REJECTED"                    # Invalid, spam, or duplicate

    ALL = {
        DISCOVERED,
        PROFILED,
        IDENTITY_MATCHED,
        CATALOG_FOUND,
        REVIEW_REQUIRED,
        APPROVED,
        INDEXED,
        REJECTED,
    }


# Разрешённые переходы жизненного цикла кандидата.
#
# Без явной таблицы статус можно было записать любой строкой, в том числе выдуманной, и
# перескочить проверки — например, сразу в INDEXED, минуя модерацию (C-E05-04).
# REJECTED достижим почти отовсюду: отказаться от кандидата можно на любом шаге.
ALLOWED_TRANSITIONS = {
    CandidateStatus.DISCOVERED: {CandidateStatus.PROFILED, CandidateStatus.IDENTITY_MATCHED,
                                 CandidateStatus.REVIEW_REQUIRED, CandidateStatus.CATALOG_FOUND,
                                 CandidateStatus.REJECTED},
    CandidateStatus.PROFILED: {CandidateStatus.IDENTITY_MATCHED, CandidateStatus.REVIEW_REQUIRED,
                               CandidateStatus.CATALOG_FOUND, CandidateStatus.REJECTED},
    CandidateStatus.CATALOG_FOUND: {CandidateStatus.IDENTITY_MATCHED, CandidateStatus.REVIEW_REQUIRED,
                                    CandidateStatus.APPROVED, CandidateStatus.REJECTED},
    CandidateStatus.IDENTITY_MATCHED: {CandidateStatus.APPROVED, CandidateStatus.REVIEW_REQUIRED,
                                       CandidateStatus.REJECTED},
    CandidateStatus.REVIEW_REQUIRED: {CandidateStatus.IDENTITY_MATCHED, CandidateStatus.APPROVED,
                                      CandidateStatus.REJECTED},
    CandidateStatus.APPROVED: {CandidateStatus.INDEXED, CandidateStatus.REJECTED},
    CandidateStatus.INDEXED: {CandidateStatus.REJECTED},
    CandidateStatus.REJECTED: set(),
}


def transition_allowed(current: str, new: str) -> bool:
    """Разрешён ли переход. Неизвестный статус не разрешён никогда."""
    if new not in CandidateStatus.ALL or current not in CandidateStatus.ALL:
        return False
    if current == new:
        return True  # повторная запись того же статуса безвредна и идемпотентна
    return new in ALLOWED_TRANSITIONS.get(current, set())


class DiscoverySource:
    SEARCH_ENGINE = "search_engine"
    MAPS = "maps"
    MARKETPLACE_SELLER = "marketplace_seller"
    WEBSITE = "website"
    INSTAGRAM = "instagram"
    TELEGRAM = "telegram"
    SELF_REGISTRATION = "self_registration"

    ALL = {
        SEARCH_ENGINE,
        MAPS,
        MARKETPLACE_SELLER,
        WEBSITE,
        INSTAGRAM,
        TELEGRAM,
        SELF_REGISTRATION,
    }


@dataclass
class SellerCandidate:
    """
    Represents a newly discovered prospective seller before full catalog integration.
    """
    id: str
    name: str
    source_type: str
    source_url: Optional[str] = None
    status: str = CandidateStatus.DISCOVERED
    matched_seller_id: Optional[str] = None
    phone: Optional[str] = None
    domain: Optional[str] = None
    bin: Optional[str] = None
    legal_name: Optional[str] = None
    marketplace_seller_id: Optional[str] = None
    city: Optional[str] = None
    raw_metadata: Dict[str, Any] = field(default_factory=dict)
    notes: str = ""
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Any) -> SellerCandidate:
        d = dict(row)
        meta = d.get("raw_metadata") or d.get("metadata_json") or "{}"
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        return cls(
            id=d["id"],
            name=d["name"],
            source_type=d["source_type"],
            source_url=d.get("source_url"),
            status=d.get("status") or CandidateStatus.DISCOVERED,
            matched_seller_id=d.get("matched_seller_id"),
            phone=d.get("phone"),
            domain=d.get("domain"),
            bin=d.get("bin"),
            legal_name=d.get("legal_name"),
            marketplace_seller_id=d.get("marketplace_seller_id"),
            city=d.get("city"),
            raw_metadata=meta,
            notes=d.get("notes") or "",
            created_at=d.get("created_at") or utc_now_iso(),
            updated_at=d.get("updated_at") or utc_now_iso(),
        )
