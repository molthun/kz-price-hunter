"""
Domain entities and normalizers for Seller Identity and Seller Graph (KZ Price Hunter 2.0).
Provides deterministic normalization for phones, domains, BIN, and marketplace IDs in Kazakhstan.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlsplit


def utc_now_iso() -> str:
    """Return current UTC time in ISO format."""
    return datetime.now(timezone.utc).isoformat()


class IdentityType:
    PHONE = "phone"
    DOMAIN = "domain"
    BIN = "bin"
    MARKETPLACE_SELLER_ID = "marketplace_seller_id"
    SOCIAL = "social"
    LEGAL_NAME = "legal_name"

    ALL = {PHONE, DOMAIN, BIN, MARKETPLACE_SELLER_ID, SOCIAL, LEGAL_NAME}
    STRONG_KEYS = {PHONE, DOMAIN, BIN, MARKETPLACE_SELLER_ID}


class MatchDecision:
    AUTO_MATCH = "AUTO_MATCH"  # Exact match on strong verified keys without conflict
    REVIEW = "REVIEW"          # Ambiguous similarity, requires manual or AI review
    REJECT = "REJECT"          # Conflict of strong keys or completely distinct entities

    ALL = {AUTO_MATCH, REVIEW, REJECT}


@dataclass
class SellerIdentity:
    """A verified digital footprint / identity trait of a seller."""
    id: str
    seller_id: str
    identity_type: str
    identity_value: str
    confidence: float = 1.0
    source: Optional[str] = None
    created_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Any) -> SellerIdentity:
        d = dict(row)
        return cls(
            id=d["id"],
            seller_id=d["seller_id"],
            identity_type=d["identity_type"],
            identity_value=d["identity_value"],
            confidence=float(d.get("confidence") or 1.0),
            source=d.get("source"),
            created_at=d.get("created_at") or utc_now_iso(),
        )


@dataclass
class SellerMatchResult:
    """Result of attempting to match an incoming merchant candidate with known sellers."""
    decision: str
    target_seller_id: Optional[str] = None
    confidence: float = 0.0
    matched_identities: List[Tuple[str, str]] = field(default_factory=list)  # (type, value)
    conflicting_identities: List[Tuple[str, str, str]] = field(default_factory=list)  # (type, cand_val, exist_val)
    explanation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Deterministic Normalizers for Kazakhstan Business Entities
# ---------------------------------------------------------------------------

def normalize_phone(raw: Optional[str]) -> Optional[str]:
    """
    Normalizes a Kazakhstan (or international) phone number to E.164 standard.
    Examples:
      "+7 (701) 123-45-67" -> "+77011234567"
      "87011234567"        -> "+77011234567"
      "77011234567"        -> "+77011234567"
      "8 (727) 222-33-44"  -> "+77272223344"
    Returns None if raw is invalid or has wrong length.
    """
    if not raw:
        return None

    digits = re.sub(r"[^\d]", "", str(raw))
    if not digits:
        return None

    # Kazakhstan 11-digit numbers starting with 8 or 7
    if len(digits) == 11:
        if digits.startswith("8"):
            return f"+7{digits[1:]}"
        elif digits.startswith("7"):
            return f"+{digits}"
        else:
            return f"+{digits}"

    # 10-digit without country code (e.g. 7011234567)
    if len(digits) == 10 and digits.startswith("7"):
        return f"+7{digits}"

    # Valid international E.164: 10 to 15 digits
    if 10 <= len(digits) <= 15:
        return f"+{digits}"

    return None


def normalize_domain(raw: Optional[str]) -> Optional[str]:
    """
    Normalizes a website domain or URL into a canonical domain name.
    Examples:
      "https://www.shop.kz/catalog/phone" -> "shop.kz"
      "http://4mobile.kz:8080"            -> "4mobile.kz"
      "WWW.DNS-SHOP.KZ/"                  -> "dns-shop.kz"
    """
    if not raw:
        return None

    s = str(raw).strip().lower()
    if not s:
        return None

    # If scheme is missing, prepend // for urlsplit to parse netloc properly
    if not s.startswith("http://") and not s.startswith("https://") and not s.startswith("//"):
        s = "//" + s

    try:
        parts = urlsplit(s)
        netloc = parts.netloc or parts.path.split("/")[0]
    except Exception:
        return None

    # Strip port if present
    netloc = netloc.split(":")[0]

    # Strip leading www. or m.
    netloc = re.sub(r"^(?:www|m)\.", "", netloc)

    # Basic validity check: must have a dot and valid host characters
    if "." not in netloc or re.search(r"[^a-z0-9.\-]", netloc):
        return None

    # Strip trailing dots or dashes
    netloc = netloc.strip(".-")
    return netloc or None


def normalize_bin(raw: Optional[str]) -> Optional[str]:
    """
    Normalizes a Kazakhstan Business Identification Number (БИН / BIN).
    BIN must be exactly 12 digits.
    Returns None if invalid.
    """
    if not raw:
        return None

    digits = re.sub(r"[^\d]", "", str(raw))
    if len(digits) == 12:
        return digits
    return None


def normalize_business_name(raw: Optional[str]) -> str:
    """
    Normalizes a company or merchant name by stripping corporate suffixes
    (ТОО, ИП, АО, LLP, etc.), punctuation, and extra whitespace.
    Examples:
      'ТОО "Белый Ветер KZ"' -> "белый ветер kz"
      'ИП 4Mobile'           -> "4mobile"
      'АО "Technodom Оперейтор"' -> "technodom оперейтор"
    """
    if not raw:
        return ""

    s = str(raw).strip().lower()

    # Remove quotes
    s = re.sub(r'["\'«»“”„`]', "", s)

    # Remove legal forms in Russian, Kazakh and English
    legal_forms = [
        r"\bтоо\b", r"\bип\b", r"\bао\b", r"\bооо\b", r"\bзао\b", r"\bжао\b", r"\bжақ\b", r"\bжшс\b",
        r"\bllp\b", r"\bjsc\b", r"\bcorp\b", r"\bltd\b", r"\binc\b", r"\bчп\b",
    ]
    for form in legal_forms:
        s = re.sub(form, "", s)

    # Clean punctuation and redundant whitespace
    s = re.sub(r"[^\w\s\-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s
