"""
Seller Self-Registration («Добавить магазин», S2-E27, KZ Price Hunter 2.0).

Позволяет магазину самостоятельно подать заявку на подключение:
- название, город, сайт, Instagram, Telegram, WhatsApp/телефон, ссылки на маркетплейсы, БИН/ИИН;
- нормализует ключи идентичности (телефон, домен, БИН, соцсети);
- профилирует переданные ссылки через `SourceProfiler` (observed=False до реальной проверки);
- ставит заявку в очередь проверки `CandidateStatus.REVIEW_REQUIRED` (`REVIEW_REQUIRED` -> `APPROVED` -> `INDEXED`).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from domain.discovery import CandidateStatus, DiscoverySource, SellerCandidate
from domain.models import ChannelType, utc_now_iso
from domain.query_parser import KZ_CITIES_MAP
from domain.seller_identity import (
    normalize_bin,
    normalize_domain,
    normalize_phone,
)
from domain.source_profiler import SourceProfile, SourceProfiler

HANDLE_CLEAN_RE = re.compile(r"^@+|/+", re.IGNORECASE)
KASPI_MERCHANT_URL_RE = re.compile(r"kaspi\.kz/shop/(?:info/merchant|c)/([^/?#]+)", re.IGNORECASE)


def _clean_social_handle(raw: Optional[str], platform: str) -> Optional[str]:
    if not raw or not str(raw).strip():
        return None
    val = str(raw).strip()
    if "://" in val or val.startswith("www."):
        parsed = urlparse(val if "://" in val else f"https://{val}")
        path = (parsed.path or "").strip("/")
        if not path:
            return None
        parts = [p for p in path.split("/") if p and p not in ("p", "reel", "stories", "s")]
        handle = parts[0] if parts else ""
    else:
        handle = val
    handle = HANDLE_CLEAN_RE.sub("", handle).strip()
    return handle.lower() if handle else None


def _extract_marketplace_seller_id(links: List[str], explicit_id: Optional[str] = None) -> Optional[str]:
    if explicit_id and str(explicit_id).strip():
        return str(explicit_id).strip()
    for link in links:
        m = KASPI_MERCHANT_URL_RE.search(str(link or ""))
        if m:
            return m.group(1).strip()
    return None


@dataclass
class SellerOnboardingSubmission:
    """Входные данные формы «Добавить магазин» (S2-E27)."""
    name: str
    city: str = "Алматы"
    website_url: Optional[str] = None
    instagram: Optional[str] = None
    telegram: Optional[str] = None
    whatsapp: Optional[str] = None
    phone: Optional[str] = None
    marketplace_links: List[str] = field(default_factory=list)
    marketplace_seller_id: Optional[str] = None
    bin: Optional[str] = None
    legal_name: Optional[str] = None
    contact_person: Optional[str] = None
    comment: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OnboardingResult:
    """Результат приёма и профилирования заявки на подключение магазина."""
    candidate: SellerCandidate
    profiles: List[SourceProfile] = field(default_factory=list)
    channels_detected: List[Dict[str, str]] = field(default_factory=list)
    matched_seller_id: Optional[str] = None
    match_explanation: str = ""
    review_queue_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate": self.candidate.to_dict(),
            "profiles": [p.to_dict() for p in self.profiles],
            "channels_detected": list(self.channels_detected),
            "matched_seller_id": self.matched_seller_id,
            "match_explanation": self.match_explanation,
            "review_queue_id": self.review_queue_id,
        }


def validate_onboarding_submission(submission: SellerOnboardingSubmission) -> Tuple[bool, List[str]]:
    """
    Проверяет заявку магазина:
    - непустое имя (>= 2 символов);
    - хотя бы один проверяемый канал или контакт (сайт, Instagram, Telegram, WhatsApp/телефон или ссылка на маркетплейс);
    - если указан БИН — ровно 12 цифр.
    """
    errors: List[str] = []
    name = (submission.name or "").strip()
    if len(name) < 2:
        errors.append("Укажите название магазина (не менее 2 символов).")

    has_website = bool((submission.website_url or "").strip())
    has_ig = bool(_clean_social_handle(submission.instagram, "instagram"))
    has_tg = bool(_clean_social_handle(submission.telegram, "telegram"))
    has_wa = bool(normalize_phone(submission.whatsapp or submission.phone))
    has_mp = bool([u for u in (submission.marketplace_links or []) if str(u).strip()])

    if not any([has_website, has_ig, has_tg, has_wa, has_mp]):
        errors.append(
            "Укажите хотя бы один канал или контакт магазина "
            "(сайт, Instagram, Telegram, WhatsApp/телефон или ссылку на маркетплейс)."
        )

    if submission.bin and str(submission.bin).strip():
        if not normalize_bin(submission.bin):
            errors.append("БИН/ИИН должен содержать ровно 12 цифр.")

    return (len(errors) == 0), errors


def prepare_onboarding_candidate(
    submission: SellerOnboardingSubmission,
    candidate_id: Optional[str] = None,
) -> Tuple[SellerCandidate, List[SourceProfile], List[Dict[str, str]]]:
    """
    Превращает заявку «Добавить магазин» в `SellerCandidate`, набор `SourceProfile`
    и список распознанных каналов (`channels_detected`).
    """
    ok, errors = validate_onboarding_submission(submission)
    if not ok:
        raise ValueError("; ".join(errors))

    cid = candidate_id or f"cand_self_{uuid.uuid4().hex[:10]}"
    raw_city = (submission.city or "Алматы").strip()
    norm_city = KZ_CITIES_MAP.get(raw_city.lower(), raw_city) or "Алматы"

    norm_phone = normalize_phone(submission.phone or submission.whatsapp)
    norm_wa = normalize_phone(submission.whatsapp or submission.phone)
    norm_dom = normalize_domain(submission.website_url) if submission.website_url else None
    norm_bin_val = normalize_bin(submission.bin) if submission.bin else None

    ig_handle = _clean_social_handle(submission.instagram, "instagram")
    tg_handle = _clean_social_handle(submission.telegram, "telegram")
    mp_links = [str(u).strip() for u in (submission.marketplace_links or []) if str(u).strip()]
    mp_seller_id = _extract_marketplace_seller_id(mp_links, submission.marketplace_seller_id)

    channels_detected: List[Dict[str, str]] = []
    urls_to_profile: List[str] = []

    if submission.website_url and str(submission.website_url).strip():
        w_url = str(submission.website_url).strip()
        if "://" not in w_url:
            w_url = f"https://{w_url}"
        urls_to_profile.append(w_url)
        channels_detected.append({
            "channel_type": ChannelType.WEBSITE,
            "url": w_url,
            "handle": norm_dom or w_url,
        })

    for mp_url in mp_links:
        u = mp_url if "://" in mp_url else f"https://{mp_url}"
        urls_to_profile.append(u)
        channels_detected.append({
            "channel_type": ChannelType.MARKETPLACE,
            "url": u,
            "handle": mp_seller_id or u,
        })

    if ig_handle:
        ig_url = f"https://instagram.com/{ig_handle}"
        urls_to_profile.append(ig_url)
        channels_detected.append({
            "channel_type": ChannelType.INSTAGRAM,
            "url": ig_url,
            "handle": f"@{ig_handle}",
        })

    if tg_handle:
        tg_url = f"https://t.me/{tg_handle}"
        urls_to_profile.append(tg_url)
        channels_detected.append({
            "channel_type": ChannelType.TELEGRAM,
            "url": tg_url,
            "handle": f"@{tg_handle}",
        })

    if norm_wa:
        wa_digits = re.sub(r"\D", "", norm_wa)
        wa_url = f"https://wa.me/{wa_digits}"
        channels_detected.append({
            "channel_type": ChannelType.WHATSAPP,
            "url": wa_url,
            "handle": norm_wa,
        })

    primary_url = urls_to_profile[0] if urls_to_profile else (
        channels_detected[0]["url"] if channels_detected else None
    )

    profiles: List[SourceProfile] = []
    for idx, url in enumerate(urls_to_profile):
        prof = SourceProfiler.profile_url(
            url=url,
            profile_id=f"prof_{cid}_{idx}",
            candidate_id=cid,
        )
        profiles.append(prof)

    now = utc_now_iso()
    candidate = SellerCandidate(
        id=cid,
        name=submission.name.strip(),
        source_type=DiscoverySource.SELF_REGISTRATION,
        source_url=primary_url,
        status=CandidateStatus.REVIEW_REQUIRED,
        phone=norm_phone,
        domain=norm_dom,
        bin=norm_bin_val,
        legal_name=(submission.legal_name or "").strip() or None,
        marketplace_seller_id=mp_seller_id,
        city=norm_city,
        raw_metadata={
            "instagram": f"@{ig_handle}" if ig_handle else None,
            "telegram": f"@{tg_handle}" if tg_handle else None,
            "whatsapp": norm_wa,
            "website_url": submission.website_url,
            "marketplace_links": mp_links,
            "channels_detected": channels_detected,
            "contact_person": submission.contact_person,
            "comment": submission.comment,
            "submitted_at": now,
        },
        notes="Заявка на подключение магазина (саморегистрация S2-E27)",
        created_at=now,
        updated_at=now,
    )

    return candidate, profiles, channels_detected
