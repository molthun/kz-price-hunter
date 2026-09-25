"""
Deterministic matching engine for Kazakhstan business entities (KZ Price Hunter 2.0).
Resolves whether two merchant profiles belong to the same real-world business entity.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from domain.models import Seller
from domain.seller_identity import (
    IdentityType,
    MatchDecision,
    SellerMatchResult,
    normalize_bin,
    normalize_business_name,
    normalize_domain,
    normalize_phone,
)

logger = logging.getLogger("kz_price_hunter.domain.seller_matcher")


class SellerMatcher:
    """
    Deterministic rule-based matcher for merchants.
    Guarantees zero False-Positive merges on common/generic business names.
    """

    # Домены площадок: на них торгуют тысячи разных продавцов, поэтому совпадение такого домена
    # не говорит ни о чём. Без этого списка «Магазин Айсулу» и «Бутик Данияра» на instagram.com
    # склеивались бы автоматически с уверенностью 0.98 — ровно то, что этап и запрещает.
    # Для таких площадок идентификатором продавца может быть только адрес конкретного аккаунта,
    # а не сам домен.
    PLATFORM_DOMAINS = {
        # соцсети и мессенджеры
        "instagram.com", "facebook.com", "t.me", "telegram.me", "vk.com", "wa.me", "whatsapp.com",
        "tiktok.com", "youtube.com", "pinterest.com",
        # маркетплейсы и агрегаторы Казахстана и СНГ
        "kaspi.kz", "halykmarket.kz", "market.forte.kz", "forte.kz", "wildberries.kz", "ozon.kz",
        "satu.kz", "olx.kz", "krisha.kz", "market.yandex.kz", "aliexpress.com",
        # карты и справочники
        "2gis.kz", "2gis.com", "google.com", "maps.google.com",
        # конструкторы сайтов и бесплатные хостинги
        "wixsite.com", "wix.com", "tilda.ws", "tilda.cc", "shopify.com", "blogspot.com",
        "wordpress.com", "ucoz.ru", "webnode.com", "site123.me",
    }

    @classmethod
    def is_platform_domain(cls, domain: Optional[str]) -> bool:
        """Домен площадки, а не продавца. Поддомены площадки — тоже площадка."""
        if not domain:
            return False
        return any(domain == p or domain.endswith("." + p) for p in cls.PLATFORM_DOMAINS)

    # Generic or highly collision-prone business words that must never merge on name alone
    GENERIC_NAMES = {
        "мир мебели",
        "планета мебели",
        "дом мебели",
        "мебель",
        "стройматериалы",
        "продукты",
        "электроника",
        "зоомаркет",
        "зоотовары",
        "аптека",
        "цветы",
        "одежда",
        "обувь",
        "автозапчасти",
        "техномаркет",
        "комфорт",
    }

    @classmethod
    def match_candidate_against_seller(
        cls,
        candidate: Dict[str, Any],
        seller: Seller,
        existing_identities: Optional[List[Dict[str, str]]] = None,
    ) -> SellerMatchResult:
        """
        Evaluates match between a candidate dict and an existing Seller record.
        """
        cand_bin = normalize_bin(candidate.get("bin"))
        cand_phone = normalize_phone(candidate.get("phone"))
        cand_domain = normalize_domain(candidate.get("domain") or candidate.get("url"))
        if cls.is_platform_domain(cand_domain):
            cand_domain = None
        cand_mkt_id = (candidate.get("marketplace_seller_id") or "").strip()
        cand_name = normalize_business_name(candidate.get("name") or candidate.get("shop"))
        cand_legal = normalize_business_name(candidate.get("legal_name"))

        seller_bin = normalize_bin(seller.bin)
        seller_phone = normalize_phone(seller.phone)
        seller_domain = normalize_domain(seller.domain)
        if cls.is_platform_domain(seller_domain):
            seller_domain = None
        seller_name = normalize_business_name(seller.name)
        seller_legal = normalize_business_name(seller.legal_name)

        # Build sets of existing identities
        known_bins: Set[str] = {seller_bin} if seller_bin else set()
        known_phones: Set[str] = {seller_phone} if seller_phone else set()
        known_domains: Set[str] = {seller_domain} if seller_domain else set()
        known_mkt_ids: Set[str] = set()

        if existing_identities:
            for ident in existing_identities:
                itype = ident.get("identity_type")
                ival = ident.get("identity_value")
                if not ival:
                    continue
                if itype == IdentityType.BIN:
                    norm = normalize_bin(ival)
                    if norm:
                        known_bins.add(norm)
                elif itype == IdentityType.PHONE:
                    norm = normalize_phone(ival)
                    if norm:
                        known_phones.add(norm)
                elif itype == IdentityType.DOMAIN:
                    norm = normalize_domain(ival)
                    if norm and not cls.is_platform_domain(norm):
                        known_domains.add(norm)
                elif itype == IdentityType.MARKETPLACE_SELLER_ID:
                    known_mkt_ids.add(ival.strip())

        # -------------------------------------------------------------------
        # 1. Conflict Detection (Strict rejection)
        # -------------------------------------------------------------------
        conflicts: List[Tuple[str, str, str]] = []

        # Conflict in BIN: different official tax registration numbers mean different legal entities
        if cand_bin and known_bins and (cand_bin not in known_bins):
            conflicts.append((IdentityType.BIN, cand_bin, next(iter(known_bins))))
            return SellerMatchResult(
                decision=MatchDecision.REJECT,
                target_seller_id=seller.id,
                confidence=0.0,
                conflicting_identities=conflicts,
                explanation=f"BIN conflict: candidate {cand_bin} does not match known {known_bins}",
            )

        # Conflict in distinct primary domains when names differ or are generic
        if cand_domain and known_domains and (cand_domain not in known_domains):
            # If names are generic or distinct, different domains indicate different entities
            if cand_name in cls.GENERIC_NAMES or seller_name in cls.GENERIC_NAMES:
                conflicts.append((IdentityType.DOMAIN, cand_domain, next(iter(known_domains))))
                return SellerMatchResult(
                    decision=MatchDecision.REJECT,
                    target_seller_id=seller.id,
                    confidence=0.0,
                    conflicting_identities=conflicts,
                    explanation=f"Domain conflict on generic name: {cand_domain} vs {known_domains}",
                )

        # -------------------------------------------------------------------
        # 2. Strong Key Matching (AUTO_MATCH)
        # -------------------------------------------------------------------
        matched: List[Tuple[str, str]] = []

        # Match by BIN
        if cand_bin and cand_bin in known_bins:
            matched.append((IdentityType.BIN, cand_bin))
            return SellerMatchResult(
                decision=MatchDecision.AUTO_MATCH,
                target_seller_id=seller.id,
                confidence=1.0,
                matched_identities=matched,
                explanation=f"Exact BIN match: {cand_bin}",
            )

        # Match by Domain
        if cand_domain and cand_domain in known_domains:
            matched.append((IdentityType.DOMAIN, cand_domain))
            return SellerMatchResult(
                decision=MatchDecision.AUTO_MATCH,
                target_seller_id=seller.id,
                confidence=0.98,
                matched_identities=matched,
                explanation=f"Exact domain match: {cand_domain}",
            )

        # Match by Phone
        if cand_phone and cand_phone in known_phones:
            matched.append((IdentityType.PHONE, cand_phone))
            return SellerMatchResult(
                decision=MatchDecision.AUTO_MATCH,
                target_seller_id=seller.id,
                confidence=0.98,
                matched_identities=matched,
                explanation=f"Exact phone match: {cand_phone}",
            )

        # Match by Marketplace Seller ID
        if cand_mkt_id and cand_mkt_id in known_mkt_ids:
            matched.append((IdentityType.MARKETPLACE_SELLER_ID, cand_mkt_id))
            return SellerMatchResult(
                decision=MatchDecision.AUTO_MATCH,
                target_seller_id=seller.id,
                confidence=0.95,
                matched_identities=matched,
                explanation=f"Exact marketplace seller id match: {cand_mkt_id}",
            )

        # -------------------------------------------------------------------
        # 3. Name Similarity / REVIEW (Never auto-match without strong keys!)
        # -------------------------------------------------------------------
        # Exact match on legal name or distinctive brand name
        distinctive_name_match = False
        if cand_name and seller_name and cand_name not in cls.GENERIC_NAMES:
            if cand_name == seller_name:
                distinctive_name_match = True

        if cand_legal and seller_legal and cand_legal == seller_legal:
            distinctive_name_match = True

        if distinctive_name_match:
            # Different phones without strong match -> REJECT
            if cand_phone and known_phones and cand_phone not in known_phones:
                conflicts.append((IdentityType.PHONE, cand_phone, next(iter(known_phones))))
                return SellerMatchResult(
                    decision=MatchDecision.REJECT,
                    target_seller_id=seller.id,
                    confidence=0.0,
                    conflicting_identities=conflicts,
                    explanation=f"Different phones for same name ({cand_phone} vs {known_phones})",
                )

            # Similar distinctive name, but no strong verification: Queue for REVIEW
            return SellerMatchResult(
                decision=MatchDecision.REVIEW,
                target_seller_id=seller.id,
                confidence=0.65,
                explanation=f"Distinctive name match '{cand_name}' without strong identity confirmation",
            )

        # -------------------------------------------------------------------
        # 4. No Match (REJECT / Distinct entity)
        # -------------------------------------------------------------------
        return SellerMatchResult(
            decision=MatchDecision.REJECT,
            target_seller_id=None,
            confidence=0.0,
            explanation="No matching strong or distinctive identities found",
        )
