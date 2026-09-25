"""
Deterministic matching engine for Kazakhstan business entities (KZ Price Hunter 2.0).
Resolves whether two merchant profiles belong to the same real-world business entity.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from domain.models import Seller
from domain.seller_identity import (
    PLATFORM_DOMAINS,
    IdentityType,
    MatchDecision,
    SellerMatchResult,
    get_registrable_domain,
    is_generic_business_name,
    is_platform_domain,
    is_weak_identity,
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

    # Списки площадок и правило слабого ключа живут в seller_identity.py: ими пользуется
    # и репозиторий. Здесь оставлены псевдонимы для читаемости правил ниже.
    PLATFORM_DOMAINS = PLATFORM_DOMAINS
    is_platform_domain = staticmethod(is_platform_domain)

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

    @staticmethod
    def _names_compatible(one: str, other: str) -> bool:
        """Продолжают ли названия друг друга.

        Совместимы, если одно содержит другое целиком: «4mobile» и «4mobile Kaspi» — один продавец
        в двух каналах. Несовместимы, если это просто разные названия на одном домене.
        Пустое название ничему не противоречит: отсутствие данных не должно блокировать совпадение
        по сильному ключу.
        """
        if not one or not other:
            return True
        return one in other or other in one

    @classmethod
    def match_candidate_against_seller(
        cls,
        candidate: Dict[str, Any],
        seller: Seller,
        existing_identities: Optional[List[Dict[str, str]]] = None,
        shared_values: Optional[Set[Tuple[str, str]]] = None,
    ) -> SellerMatchResult:
        """
        Evaluates match between a candidate dict and an existing Seller record.

        shared_values — пары (тип, значение), которые в базе уже встречаются у нескольких
        продавцов: общий колл-центр, общий хостинг. По форме такие значения не отличить от
        настоящих, поэтому частотный признак приходит снаружи, из репозитория.
        """
        shared = shared_values or set()

        def usable(identity_type: str, value: Optional[str]) -> Optional[str]:
            """Значение как сильный ключ — или None, если его делят многие продавцы."""
            if not value:
                return None
            if is_weak_identity(identity_type, value):
                return None
            if (identity_type, value) in shared:
                return None
            return value
        cand_bin = usable(IdentityType.BIN, normalize_bin(candidate.get("bin")))
        cand_phone = usable(IdentityType.PHONE, normalize_phone(candidate.get("phone")))
        cand_domain = usable(IdentityType.DOMAIN,
                             normalize_domain(candidate.get("domain") or candidate.get("url")))
        # Голый номер площадки ключом не считается: 42 на Kaspi и 42 на Forte — разные продавцы
        cand_mkt_id = usable(IdentityType.MARKETPLACE_SELLER_ID,
                             (candidate.get("marketplace_seller_id") or "").strip()) or ""
        cand_name = normalize_business_name(candidate.get("name") or candidate.get("shop"))
        cand_legal = normalize_business_name(candidate.get("legal_name"))

        seller_bin = usable(IdentityType.BIN, normalize_bin(seller.bin))
        seller_phone = usable(IdentityType.PHONE, normalize_phone(seller.phone))
        seller_domain = usable(IdentityType.DOMAIN, normalize_domain(seller.domain))
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
                    norm = usable(IdentityType.BIN, normalize_bin(ival))
                    if norm:
                        known_bins.add(norm)
                elif itype == IdentityType.PHONE:
                    norm = usable(IdentityType.PHONE, normalize_phone(ival))
                    if norm:
                        known_phones.add(norm)
                elif itype == IdentityType.DOMAIN:
                    norm = usable(IdentityType.DOMAIN, normalize_domain(ival))
                    if norm:
                        known_domains.add(norm)
                elif itype == IdentityType.MARKETPLACE_SELLER_ID:
                    norm = usable(IdentityType.MARKETPLACE_SELLER_ID, ival.strip())
                    if norm:
                        known_mkt_ids.add(norm)

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

        # Generic name detection (B2: dynamic generic check on Russian & Kazakh retail words)
        is_cand_generic = cand_name in cls.GENERIC_NAMES or is_generic_business_name(cand_name)
        is_seller_generic = seller_name in cls.GENERIC_NAMES or is_generic_business_name(seller_name)
        is_any_generic = is_cand_generic or is_seller_generic

        # Conflict in distinct primary domains when names differ or are generic
        if cand_domain and known_domains and (cand_domain not in known_domains):
            # If names are generic or distinct, different domains indicate different entities
            if is_any_generic:
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

        # Match by Domain (Exact or Registrable Domain for regional subdomains)
        domain_matched = False
        matched_dom_val = ""
        if cand_domain and cand_domain in known_domains:
            domain_matched = True
            matched_dom_val = cand_domain
        elif cand_domain and known_domains:
            cand_reg = get_registrable_domain(cand_domain)
            if cand_reg and not is_platform_domain(cand_reg):
                for kd in known_domains:
                    if get_registrable_domain(kd) == cand_reg:
                        domain_matched = True
                        matched_dom_val = f"{cand_domain}~{kd}"
                        break

        if domain_matched:
            matched.append((IdentityType.DOMAIN, matched_dom_val))
            # Один домен при несовместимых именах — это чаще бренд и его реселлер, чем один
            # продавец: «Apple Store Kazakhstan» и «Apple Reseller Almaty» оба ссылаются на
            # apple.com. Поэтому домена одного мало, если имена не продолжают друг друга
            if not cls._names_compatible(cand_name, seller_name):
                return SellerMatchResult(
                    decision=MatchDecision.REVIEW,
                    target_seller_id=seller.id,
                    confidence=0.6,
                    matched_identities=matched,
                    explanation=(f"Общий домен {matched_dom_val} при несовместимых названиях "
                                 f"'{cand_name}' и '{seller_name}': возможен бренд и его реселлер"),
                )
            return SellerMatchResult(
                decision=MatchDecision.AUTO_MATCH,
                target_seller_id=seller.id,
                confidence=0.98,
                matched_identities=matched,
                explanation=f"Exact or registrable domain match: {matched_dom_val}",
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
        if cand_name and seller_name and not is_any_generic:
            if cand_name == seller_name:
                distinctive_name_match = True

        if cand_legal and seller_legal and cand_legal == seller_legal and not is_any_generic:
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
