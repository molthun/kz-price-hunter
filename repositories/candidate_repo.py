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
from repositories.channel_repo import ChannelRepository
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
        self.channel_repo = ChannelRepository(conn)

    def save_candidate(self, candidate: SellerCandidate) -> SellerCandidate:
        sql = """
        INSERT INTO seller_candidates (
            id, name, source_type, source_url, status, matched_seller_id,
            phone, domain, bin, legal_name, marketplace_seller_id, city, discovery_task_id,
            raw_metadata, notes, created_at, updated_at
        ) VALUES (
            ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?,
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
            discovery_task_id = COALESCE(excluded.discovery_task_id, seller_candidates.discovery_task_id),
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
            candidate.discovery_task_id,
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

    def submit_self_registration(self, submission: Any) -> Any:
        """
        Принимает заявку «Добавить магазин» (S2-E27):
        1. Профилирует переданные каналы (сайт, соцсети, маркетплейсы) с observed=False.
        2. Проверяет совпадения с уже известными продавцами в Seller Graph.
        3. Всегда ставит кандидата в очередь проверки (`REVIEW_REQUIRED`), чтобы саморегистрация
           не попадала в боевой индекс без подтверждения (`REVIEW_REQUIRED` -> `APPROVED` -> `INDEXED`).
        """
        from domain.seller_onboarding import OnboardingResult, prepare_onboarding_candidate

        candidate, profiles, channels_detected = prepare_onboarding_candidate(submission)
        self.save_candidate(candidate)
        for prof in profiles:
            self.save_source_profile(prof)

        candidate_dict = {
            "name": candidate.name,
            "legal_name": candidate.legal_name,
            "phone": candidate.phone,
            "domain": candidate.domain,
            "bin": candidate.bin,
            "marketplace_seller_id": candidate.marketplace_seller_id,
        }

        # Существующий продавец привязывается к заявке только по AUTO_MATCH. REVIEW — лишь
        # подсказка модератору: подтвердить её он может явно (seller_id в решении), иначе при
        # одобрении создаётся новый продавец (V01 аудита 6.5.0)
        matched_seller_id: Optional[str] = None
        suggested_seller_id: Optional[str] = None
        match_explanation = "Новый магазин — ожидает проверки модератором"
        best_confidence = 0.7

        potential_sellers = self._find_potential_sellers(candidate_dict)
        best_review = None
        for seller in potential_sellers:
            match_res = self.identity_repo.match_candidate(candidate_dict, seller)
            if match_res.decision == MatchDecision.AUTO_MATCH:
                matched_seller_id = suggested_seller_id = seller.id
                match_explanation = match_res.explanation
                best_confidence = match_res.confidence
                break
            if match_res.decision == MatchDecision.REVIEW and (
                    best_review is None or match_res.confidence > best_review.confidence):
                best_review = match_res
                suggested_seller_id = seller.id
                match_explanation = f"Возможно, это {seller.id}: {match_res.explanation}"
                best_confidence = match_res.confidence

        candidate.matched_seller_id = matched_seller_id
        candidate.status = CandidateStatus.REVIEW_REQUIRED
        candidate.notes = f"Саморегистрация S2-E27 ({match_explanation})"
        self.save_candidate(candidate)

        # В очереди — только реальный продавец-подсказка или ничего: придуманный по имени ID
        # мог совпасть с чужим продавцом
        queue_id = self.identity_repo.add_to_review_queue(
            candidate_seller_id=candidate.id,
            matched_seller_id=suggested_seller_id,
            reason=f"Заявка «Добавить магазин»: {match_explanation}",
            confidence=best_confidence,
            details={
                "candidate_name": candidate.name,
                "city": candidate.city,
                "source_type": candidate.source_type,
                "channels_detected": channels_detected,
            },
        )

        return OnboardingResult(
            candidate=candidate,
            profiles=profiles,
            channels_detected=channels_detected,
            matched_seller_id=matched_seller_id,
            match_explanation=match_explanation,
            review_queue_id=queue_id if isinstance(queue_id, str) else getattr(queue_id, "id", None),
        )

    def approve_onboarding_candidate(
        self,
        candidate_id: str,
        decision: str = "APPROVED",
        actor: Optional[str] = None,
        seller_id: Optional[str] = None,
    ) -> Optional[Seller]:
        """
        Подтверждает или отклоняет заявку магазина (S2-E27).
        При подтверждении атомарно переводит кандидата `REVIEW_REQUIRED` -> `APPROVED` -> `INDEXED`,
        создаёт или обновляет запись `Seller`, привязывает ключи идентичности и создаёт каналы (`Channel`).

        Существующий продавец дополняется только по подтверждённому сопоставлению: AUTO_MATCH при
        подаче заявки или явный `seller_id` модератора. Иначе создаётся новый продавец с ID от
        самой заявки — совпадение префикса домена или имени чужого продавца не перезаписывает (V01).
        Повторное одобрение возвращает того же продавца.
        """

        candidate = self.get_candidate_by_id(candidate_id)
        if not candidate:
            return None

        norm_dec = (decision or "").strip().upper()
        if norm_dec not in ("APPROVED", "REJECTED"):
            raise ValueError(f"Decision must be APPROVED or REJECTED, got {decision}")

        # Повторное одобрение уже подключённой заявки ничего не меняет и возвращает того же
        # продавца (V01): заявка в INDEXED обратно в APPROVED не переводится
        if norm_dec == "APPROVED" and candidate.status == CandidateStatus.INDEXED and candidate.matched_seller_id:
            if seller_id and seller_id != candidate.matched_seller_id:
                raise ValueError("Заявка уже подключена к другому продавцу")
            existing = self.seller_repo.get_by_id(candidate.matched_seller_id)
            if existing is not None:
                return existing

        self.execute("SAVEPOINT onboarding_decision")
        try:
            # Close any pending review queue items for this candidate
            pending_rows = self.fetchall(
                "SELECT id FROM seller_review_queue WHERE candidate_seller_id = ? AND status = 'PENDING'",
                (candidate_id,),
            )
            for prow in pending_rows:
                self.identity_repo.resolve_review(prow["id"], norm_dec, actor=actor)

            if norm_dec == "REJECTED":
                self.update_candidate_status(
                    candidate_id,
                    CandidateStatus.REJECTED,
                    notes="Заявка отклонена модератором" + (f" ({actor})" if actor else ""),
                )
                self.execute("RELEASE SAVEPOINT onboarding_decision")
                return None

            # 1. Create or update Seller
            seller: Optional[Seller] = None
            target_id = seller_id or candidate.matched_seller_id
            if target_id:
                seller = self.seller_repo.get_by_id(target_id)
                if seller is None and seller_id:
                    raise ValueError(f"Продавец {seller_id} не найден")

            if not seller:
                new_id = self._onboarded_seller_id(candidate.id)
                existing = self.seller_repo.get_by_id(new_id)
                if existing is not None and (existing.metadata or {}).get("onboarded_from") != candidate.id:
                    raise ValueError(f"ID {new_id} уже занят другим продавцом")
                seller = existing or Seller(
                    id=new_id,
                    slug=self._free_seller_slug(candidate),
                    name=candidate.name,
                    legal_name=candidate.legal_name,
                    bin=candidate.bin,
                    domain=candidate.domain,
                    phone=candidate.phone,
                    is_active=True,
                    metadata={"city": candidate.city, "onboarded_from": candidate.id},
                )
            else:
                seller.legal_name = seller.legal_name or candidate.legal_name
                seller.bin = seller.bin or candidate.bin
                seller.domain = seller.domain or candidate.domain
                seller.phone = seller.phone or candidate.phone
                seller.is_active = True

            self.seller_repo.save_or_update(seller)
            self.identity_repo.sync_identities_from_seller(seller, source="self_registration_approved")

            if candidate.marketplace_seller_id:
                self.identity_repo.add_identity_values(
                    seller.id,
                    IdentityType.MARKETPLACE_SELLER_ID,
                    candidate.marketplace_seller_id,
                    confidence=0.95,
                    source="self_registration_approved",
                )

            # 2. Register channels from candidate metadata
            meta = candidate.raw_metadata or {}
            channels_list = meta.get("channels_detected") or []
            for ch in channels_list:
                ch_type = ch.get("channel_type")
                ch_url = ch.get("url")
                ch_handle = ch.get("handle")
                if ch_type and ch_url:
                    self.channel_repo.get_or_create(
                        seller_id=seller.id,
                        channel_type=ch_type,
                        name=f"{seller.name} ({ch_type})",
                        external_store_id=ch_handle,
                        base_url=ch_url,
                    )

            # Link source profiles to seller
            for prof in self.get_source_profiles_for_candidate(candidate.id):
                prof.seller_id = seller.id
                self.save_source_profile(prof)

            # 3. Advance status: REVIEW_REQUIRED -> APPROVED -> INDEXED
            if candidate.status != CandidateStatus.APPROVED:
                self.update_candidate_status(
                    candidate.id,
                    CandidateStatus.APPROVED,
                    matched_seller_id=seller.id,
                    notes="Заявка одобрена" + (f" ({actor})" if actor else ""),
                )
            self.update_candidate_status(
                candidate.id,
                CandidateStatus.INDEXED,
                matched_seller_id=seller.id,
                notes="Магазин подключён к индексу" + (f" ({actor})" if actor else ""),
            )
            self.execute("RELEASE SAVEPOINT onboarding_decision")
            return seller
        except Exception:
            self.execute("ROLLBACK TO SAVEPOINT onboarding_decision")
            self.execute("RELEASE SAVEPOINT onboarding_decision")
            raise

    @staticmethod
    def _onboarded_seller_id(candidate_id: str) -> str:
        """ID продавца из заявки: производный от уникального ID заявки, а не от домена/имени."""
        suffix = candidate_id[len("cand_"):] if candidate_id.startswith("cand_") else candidate_id
        return f"seller_{suffix}"

    def _free_seller_slug(self, candidate: SellerCandidate) -> str:
        """Читаемый slug по домену целиком (example-com, а не example) или имени; занятый — с
        суффиксом от заявки. slug в таблице уникален, чужой занимать нельзя."""
        import hashlib
        import re

        base = re.sub(r"[^a-z0-9]+", "-", (candidate.domain or candidate.name or "").lower()).strip("-")
        base = base or "seller"
        slug = base
        if self.fetchone("SELECT 1 FROM sellers WHERE slug = ?", (slug,)):
            slug = f"{base}-{hashlib.sha256(candidate.id.encode()).hexdigest()[:8]}"
        return slug
