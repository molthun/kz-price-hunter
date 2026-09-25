"""
Repository for Seller Candidates and Source Profiles (KZ Price Hunter 2.0 Discovery).
Manages discovery pipeline state transitions and source catalog profiling.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Set

from domain.discovery import CandidateStatus, SellerCandidate, transition_allowed
from domain.models import Seller, utc_now_iso
from domain.seller_identity import (
    IdentityType,
    MatchDecision,
    get_registrable_domain,
    is_generic_business_name,
    is_weak_identity,
    normalize_bin,
    normalize_business_name,
    normalize_domain,
    normalize_phone,
)
from domain.source_profiler import SourceProfile, SourceProfiler
from repositories.base import BaseRepository
from repositories.seller_identity_repo import SellerIdentityRepository
from repositories.seller_repo import SellerRepository

logger = logging.getLogger("kz_price_hunter.repositories.candidate")


class CandidateRepository(BaseRepository):
    """
    CRUD and operational workflow operations for Seller Discovery candidates and catalog sources.
    """

    def __init__(self, conn):
        super().__init__(conn)
        self.seller_repo = SellerRepository(conn)
        self.identity_repo = SellerIdentityRepository(conn)

    def save_candidate(self, candidate: SellerCandidate) -> SellerCandidate:
        sql = """
        INSERT INTO seller_candidates (
            id, name, source_type, source_url, status, matched_seller_id,
            phone, domain, bin, legal_name, marketplace_seller_id, city,
            raw_metadata, notes, created_at, updated_at
        ) VALUES (
            ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?
        )
        ON CONFLICT(id) DO UPDATE SET
            name = excluded.name,
            source_type = excluded.source_type,
            source_url = COALESCE(excluded.source_url, seller_candidates.source_url),
            status = excluded.status,
            matched_seller_id = COALESCE(excluded.matched_seller_id, seller_candidates.matched_seller_id),
            phone = COALESCE(excluded.phone, seller_candidates.phone),
            domain = COALESCE(excluded.domain, seller_candidates.domain),
            bin = COALESCE(excluded.bin, seller_candidates.bin),
            legal_name = COALESCE(excluded.legal_name, seller_candidates.legal_name),
            marketplace_seller_id = COALESCE(excluded.marketplace_seller_id, seller_candidates.marketplace_seller_id),
            city = COALESCE(excluded.city, seller_candidates.city),
            raw_metadata = excluded.raw_metadata,
            notes = excluded.notes,
            updated_at = excluded.updated_at
        """
        now = utc_now_iso()
        params = (
            candidate.id,
            candidate.name,
            candidate.source_type,
            candidate.source_url,
            candidate.status,
            candidate.matched_seller_id,
            candidate.phone,
            candidate.domain,
            candidate.bin,
            candidate.legal_name,
            candidate.marketplace_seller_id,
            candidate.city,
            json.dumps(candidate.raw_metadata, ensure_ascii=False),
            candidate.notes,
            candidate.created_at or now,
            now,
        )
        self.execute(sql, params)
        candidate.updated_at = now
        return candidate

    def get_candidate_by_id(self, candidate_id: str) -> Optional[SellerCandidate]:
        row = self.fetchone("SELECT * FROM seller_candidates WHERE id = ?", (candidate_id,))
        return SellerCandidate.from_row(row) if row else None

    def find_candidates(
        self,
        status: Optional[str] = None,
        source_type: Optional[str] = None,
        limit: int = 50,
    ) -> List[SellerCandidate]:
        sql = "SELECT * FROM seller_candidates WHERE 1=1"
        params: list = []
        if status:
            sql += " AND status = ?"
            params.append(status)
        if source_type:
            sql += " AND source_type = ?"
            params.append(source_type)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = self.fetchall(sql, params)
        return [SellerCandidate.from_row(r) for r in rows]

    def update_candidate_status(
        self,
        candidate_id: str,
        new_status: str,
        matched_seller_id: Optional[str] = None,
        notes: str = "",
    ) -> bool:
        """Меняет статус кандидата, проверяя допустимость самого статуса и перехода.

        Раньше сюда можно было передать любую строку — например, INDEXED_WITHOUT_APPROVAL —
        и перескочить модерацию. Жизненный цикл, описанный в плане, обязан быть проверяемым,
        иначе он только описание (C-E05-04).
        """
        if new_status not in CandidateStatus.ALL:
            raise ValueError(f"Неизвестный статус кандидата: {new_status}")
        current = self.get_candidate_by_id(candidate_id)
        if not current:
            return False
        if not transition_allowed(current.status, new_status):
            raise ValueError(
                f"Недопустимый переход кандидата {candidate_id}: {current.status} -> {new_status}")

        sql = """
        UPDATE seller_candidates
        SET status = ?,
            matched_seller_id = COALESCE(?, matched_seller_id),
            notes = CASE WHEN ? != '' THEN ? ELSE notes END,
            updated_at = ?
        WHERE id = ?
        """
        now = utc_now_iso()
        cur = self.execute(sql, (new_status, matched_seller_id, notes, notes, now, candidate_id))
        return cur.rowcount > 0

    def resolve_candidate_review(self, queue_id: str, decision: str,
                                 actor: Optional[str] = None) -> bool:
        """Закрывает запись модерации и одновременно продвигает кандидата.

        Раньше решение закрывало только строку очереди: кандидат оставался REVIEW_REQUIRED без
        связи с продавцом, и заявленная цепочка REVIEW_REQUIRED -> APPROVED -> INDEXED не работала
        (C-E05-04). Обе записи меняются вместе, внутри SAVEPOINT: наполовину применённое решение
        хуже непринятого.
        """
        row = self.fetchone(
            """SELECT candidate_seller_id, matched_seller_id, status
               FROM seller_review_queue WHERE id = ?""", (queue_id,))
        if not row or row["status"] != "PENDING":
            return False

        candidate = self.get_candidate_by_id(row["candidate_seller_id"])
        if not candidate:
            return False

        self.execute("SAVEPOINT resolve_review")
        try:
            if not self.identity_repo.resolve_review(queue_id, decision, actor=actor):
                self.execute("ROLLBACK TO SAVEPOINT resolve_review")
                self.execute("RELEASE SAVEPOINT resolve_review")
                return False
            if decision == "APPROVED":
                self.update_candidate_status(
                    candidate.id, CandidateStatus.IDENTITY_MATCHED,
                    matched_seller_id=row["matched_seller_id"],
                    notes=f"Модерация подтвердила связь с продавцом {row['matched_seller_id']}"
                          + (f" (решение: {actor})" if actor else ""))
            else:
                self.update_candidate_status(
                    candidate.id, CandidateStatus.REJECTED,
                    notes="Модерация отклонила связь" + (f" (решение: {actor})" if actor else ""))
        except Exception:
            self.execute("ROLLBACK TO SAVEPOINT resolve_review")
            self.execute("RELEASE SAVEPOINT resolve_review")
            raise
        self.execute("RELEASE SAVEPOINT resolve_review")
        return True

    def save_source_profile(self, profile: SourceProfile) -> SourceProfile:
        sql = """
        INSERT INTO source_profiles (
            id, seller_id, candidate_id, source_url, domain,
            catalog_format, extraction_tier, observed, has_prices, has_availability,
            capabilities_json, confidence, notes, profiled_at
        ) VALUES (
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?
        )
        ON CONFLICT(id) DO UPDATE SET
            seller_id = COALESCE(excluded.seller_id, source_profiles.seller_id),
            candidate_id = COALESCE(excluded.candidate_id, source_profiles.candidate_id),
            source_url = excluded.source_url,
            domain = excluded.domain,
            catalog_format = excluded.catalog_format,
            extraction_tier = excluded.extraction_tier,
            observed = excluded.observed,
            has_prices = excluded.has_prices,
            has_availability = excluded.has_availability,
            capabilities_json = excluded.capabilities_json,
            confidence = excluded.confidence,
            notes = excluded.notes,
            profiled_at = excluded.profiled_at
        """
        now = utc_now_iso()
        params = (
            profile.id,
            profile.seller_id,
            profile.candidate_id,
            profile.source_url,
            profile.domain,
            profile.catalog_format,
            profile.extraction_tier,
            1 if profile.observed else 0,
            1 if profile.has_prices else 0,
            1 if profile.has_availability else 0,
            json.dumps(profile.capabilities, ensure_ascii=False),
            profile.confidence,
            profile.notes,
            profile.profiled_at or now,
        )
        self.execute(sql, params)
        return profile

    def get_source_profiles_for_candidate(self, candidate_id: str) -> List[SourceProfile]:
        rows = self.fetchall("SELECT * FROM source_profiles WHERE candidate_id = ?", (candidate_id,))
        return [SourceProfile.from_row(r) for r in rows]

    def get_source_profiles_for_seller(self, seller_id: str) -> List[SourceProfile]:
        rows = self.fetchall("SELECT * FROM source_profiles WHERE seller_id = ?", (seller_id,))
        return [SourceProfile.from_row(r) for r in rows]

    def _find_potential_sellers(self, candidate_dict: Dict[str, Any]) -> List[Seller]:
        """
        Locates candidate sellers from the database using known strong identity keys
        (phone, domain, bin, marketplace_seller_id) or distinctive name/slug matches.
        """
        candidate_seller_ids: Set[str] = set()

        phone = normalize_phone(candidate_dict.get("phone"))
        if phone and not is_weak_identity(IdentityType.PHONE, phone):
            for sid in self.identity_repo.find_sellers_by_identity(IdentityType.PHONE, phone):
                candidate_seller_ids.add(sid)
            rows = self.fetchall("SELECT id FROM sellers WHERE phone = ? AND is_active = 1", (phone,))
            for r in rows:
                candidate_seller_ids.add(r["id"])

        raw_domain = candidate_dict.get("domain")
        norm_dom = normalize_domain(raw_domain)
        reg_dom = get_registrable_domain(raw_domain)
        for dom in filter(None, [norm_dom, reg_dom]):
            if not is_weak_identity(IdentityType.DOMAIN, dom):
                for sid in self.identity_repo.find_sellers_by_identity(IdentityType.DOMAIN, dom):
                    candidate_seller_ids.add(sid)
                rows = self.fetchall("SELECT id FROM sellers WHERE domain LIKE ? AND is_active = 1", (f"%{dom}%",))
                for r in rows:
                    candidate_seller_ids.add(r["id"])

        bin_val = normalize_bin(candidate_dict.get("bin"))
        if bin_val:
            for sid in self.identity_repo.find_sellers_by_identity(IdentityType.BIN, bin_val):
                candidate_seller_ids.add(sid)
            rows = self.fetchall("SELECT id FROM sellers WHERE bin = ? AND is_active = 1", (bin_val,))
            for r in rows:
                candidate_seller_ids.add(r["id"])

        m_id = candidate_dict.get("marketplace_seller_id")
        if m_id:
            for sid in self.identity_repo.find_sellers_by_identity(IdentityType.MARKETPLACE_SELLER_ID, str(m_id).strip()):
                candidate_seller_ids.add(sid)

        name = candidate_dict.get("name")
        if name:
            norm_name = normalize_business_name(name)
            rows = self.fetchall(
                "SELECT id FROM sellers WHERE is_active = 1 AND (slug = ? OR lower(name) = ? OR lower(name) = ?)",
                (name.lower(), name.lower(), norm_name),
            )
            for r in rows:
                candidate_seller_ids.add(r["id"])

        sellers: List[Seller] = []
        seen = set()
        for sid in candidate_seller_ids:
            s = self.seller_repo.get_by_id(sid)
            if s and s.is_active and s.id not in seen:
                sellers.append(s)
                seen.add(s.id)

        # If no specific key matches, check active sellers
        if not sellers:
            all_sellers = self.seller_repo.list_sellers(active_only=True)
            if len(all_sellers) <= 200:
                sellers = all_sellers
            elif name:
                norm_name = normalize_business_name(name)
                tokens = [t for t in norm_name.split() if len(t) > 2 and not is_generic_business_name(t)]
                if tokens:
                    for s in all_sellers:
                        s_norm = normalize_business_name(s.name)
                        if any(tok in s_norm for tok in tokens):
                            sellers.append(s)

        return sellers

    def process_discovered_candidate(
        self,
        candidate: SellerCandidate,
        hints: Optional[Dict[str, Any]] = None,
    ) -> SellerCandidate:
        """
        Executes end-to-end processing of a discovered seller candidate:
        1. Persists candidate in DISCOVERED state.
        2. Profiles source URL (Tier 1-7, capabilities) if available.
        3. Attempts deterministic matching via SellerIdentityRepository / SellerMatcher.
        4. Updates status to IDENTITY_MATCHED, REVIEW_REQUIRED, or PROFILED/CATALOG_FOUND.
        """
        self.save_candidate(candidate)

        # 1. Profile catalog source if URL present
        profile: Optional[SourceProfile] = None
        if candidate.source_url:
            prof_id = f"prof_{candidate.id}"
            profile = SourceProfiler.profile_url(
                url=candidate.source_url,
                profile_id=prof_id,
                candidate_id=candidate.id,
                seller_id=candidate.matched_seller_id,
                hints=hints,
            )
            self.save_source_profile(profile)

        # 2. Match candidate against Seller Graph
        candidate_dict = {
            "name": candidate.name,
            "legal_name": candidate.legal_name,
            "phone": candidate.phone,
            "domain": candidate.domain or (profile.domain if profile else None),
            "bin": candidate.bin,
            "marketplace_seller_id": candidate.marketplace_seller_id,
        }

        best_match = None
        best_match_seller = None
        best_review = None
        best_review_seller = None

        potential_sellers = self._find_potential_sellers(candidate_dict)
        for seller in potential_sellers:
            match_res = self.identity_repo.match_candidate(candidate_dict, seller)
            if match_res.decision == MatchDecision.AUTO_MATCH:
                best_match = match_res
                best_match_seller = seller
                break
            elif match_res.decision == MatchDecision.REVIEW:
                if best_review is None or match_res.confidence > best_review.confidence:
                    best_review = match_res
                    best_review_seller = seller

        if best_match and best_match_seller:
            candidate.status = CandidateStatus.IDENTITY_MATCHED
            candidate.matched_seller_id = best_match_seller.id
            candidate.notes = f"Auto-matched with seller {best_match_seller.id}: {best_match.explanation}"
            if profile:
                profile.seller_id = best_match_seller.id
                self.save_source_profile(profile)
        elif best_review and best_review_seller:
            candidate.status = CandidateStatus.REVIEW_REQUIRED
            candidate.notes = f"Review required: {best_review.explanation}"
            self.identity_repo.add_to_review_queue(
                candidate_seller_id=candidate.id,
                matched_seller_id=best_review_seller.id,
                reason=best_review.explanation,
                confidence=best_review.confidence,
                details={"candidate_name": candidate.name, "source_type": candidate.source_type},
            )
        else:
            # REJECT / distinct entity
            # CATALOG_FOUND означает «каталог найден», а не «предположен по адресу»: профиль,
            # полученный догадкой (observed=False), оставляем в PROFILED. Иначе обычная визитка
            # или парковочная страница прошла бы дальше как пригодный источник (C-E05-01).
            if profile and profile.observed and profile.extraction_tier <= 4:
                candidate.status = CandidateStatus.CATALOG_FOUND
                candidate.notes = f"New candidate with actionable catalog ({profile.catalog_format}, Tier {profile.extraction_tier})"
            elif profile:
                candidate.status = CandidateStatus.PROFILED
                candidate.notes = (f"Предположение по адресу ({profile.catalog_format}, Tier "
                                   f"{profile.extraction_tier}); источник не открывался")
            else:
                candidate.status = CandidateStatus.PROFILED
                candidate.notes = "Profiled new merchant candidate without matching existing seller"

        self.save_candidate(candidate)
        return candidate
