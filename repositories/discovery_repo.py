"""
DiscoveryRepository (S2-E29, KZ Price Hunter 2.0).

Unified repository layer for merchant discovery, adaptive task queue,
seller candidates pipeline, and social source catalog.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from domain.adaptive_discovery import DiscoveryTask, DiscoveryTaskStatus
from domain.discovery import CandidateStatus, SellerCandidate
from domain.social_connector import SocialSource
from repositories.base import BaseRepository
from repositories.candidate_repo import CandidateRepository
from repositories.discovery_task_repo import DiscoveryTaskRepository
from repositories.social_source_repo import SocialSourceRepository

logger = logging.getLogger("kz_price_hunter.repositories.discovery")


class DiscoveryRepository(BaseRepository):
    """
    Unified Discovery repository abstracting tasks, candidates, and social sources.
    """

    def __init__(self, conn):
        super().__init__(conn)
        self.task_repo = DiscoveryTaskRepository(conn)
        self.candidate_repo = CandidateRepository(conn)
        self.social_repo = SocialSourceRepository(conn)

    # -------------------------------------------------------------------------
    # 1. Discovery Tasks (Adaptive Search Signals)
    # -------------------------------------------------------------------------

    def create_or_update_task(
        self,
        signal_query: str,
        city: Optional[str] = None,
        search_count: int = 1,
        success_rate: float = 0.0,
        priority_score: Optional[float] = None,
        notes: Optional[str] = None,
    ) -> DiscoveryTask:
        """
        Creates or updates an adaptive discovery task based on search demand signal (SP30-07).
        Dedupes active (PENDING or RUNNING) tasks for the query and city, accumulating demand.
        """
        clean_q = signal_query.strip()
        if not clean_q:
            raise ValueError("Поисковый запрос не может быть пустым")

        import re
        from domain.adaptive_discovery import DiscoveryTask, DiscoveryTaskStatus, SearchPatternGenerator

        norm_q = re.sub(r"\s+", " ", clean_q.lower()).strip()
        existing = self.task_repo.find_active_task_for_query(norm_q, city=city)
        if existing:
            existing.search_count += max(0, int(search_count))
            existing.success_rate = float(success_rate)
            if priority_score is not None and priority_score > 0:
                existing.priority_score = float(priority_score)
            else:
                existing.priority_score += float(search_count) * (1.0 - float(success_rate))
            if notes:
                existing.notes = (existing.notes + "; " + notes) if existing.notes else notes
            return self.task_repo.save_task(existing)

        eff_priority = (
            priority_score
            if (priority_score is not None and priority_score > 0)
            else max(1.0, float(search_count) * (1.0 - float(success_rate)))
        )
        search_queries = [
            q.to_dict() for q in SearchPatternGenerator.generate_queries(clean_q, city=city)
        ]
        task = DiscoveryTask(
            signal_query=clean_q,
            normalized_query=norm_q,
            city=city,
            search_count=int(search_count),
            success_rate=float(success_rate),
            priority_score=float(eff_priority),
            status=DiscoveryTaskStatus.PENDING,
            search_queries=search_queries,
            notes=notes or "",
        )
        return self.task_repo.save_task(task)

    def get_task_by_id(self, task_id: str) -> Optional[DiscoveryTask]:
        """Retrieves a discovery task by primary ID."""
        return self.task_repo.get_task_by_id(task_id)

    def list_tasks(
        self,
        status: Optional[str] = None,
        city: Optional[str] = None,
        limit: int = 50,
    ) -> List[DiscoveryTask]:
        """Lists discovery tasks sorted by priority score descending."""
        return self.task_repo.find_tasks(status=status, city=city, limit=limit)

    def update_task_status(
        self,
        task_id: str,
        status: str,
        notes: Optional[str] = None,
    ) -> bool:
        """Updates the lifecycle status of a discovery task."""
        return self.task_repo.update_status(task_id, new_status=status, notes=notes)

    # -------------------------------------------------------------------------
    # 2. Seller Candidates Pipeline
    # -------------------------------------------------------------------------

    def save_candidate(self, candidate: SellerCandidate) -> None:
        """Saves or updates a candidate seller discovered in the pipeline."""
        self.candidate_repo.save_candidate(candidate)

    def get_candidate_by_id(self, candidate_id: str) -> Optional[SellerCandidate]:
        """Retrieves a candidate by ID."""
        return self.candidate_repo.get_candidate_by_id(candidate_id)

    def list_candidates(
        self,
        status: Optional[str] = None,
        source_type: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[SellerCandidate]:
        """Lists candidate sellers filtered by status or source type."""
        return self.candidate_repo.find_candidates(
            status=status, source_type=source_type, limit=limit, offset=offset
        )

    def update_candidate_status(
        self,
        candidate_id: str,
        status: str,
        matched_seller_id: Optional[str] = None,
    ) -> bool:
        """Updates the status of a seller candidate (DISCOVERED, IN_REVIEW, ONBOARDED, REJECTED)."""
        return self.candidate_repo.update_candidate_status(
            candidate_id, new_status=status, matched_seller_id=matched_seller_id
        )

    # -------------------------------------------------------------------------
    # 3. Social Sources Registry
    # -------------------------------------------------------------------------

    def get_social_source(self, platform: str, handle: str) -> Optional[SocialSource]:
        """Looks up a social content source by platform and handle."""
        return self.social_repo.get_by_handle(platform, handle)

    def save_social_source(self, source: SocialSource) -> SocialSource:
        """Registers or updates a social content source."""
        return self.social_repo.save_source(source)

    def list_social_sources(self, enabled_only: bool = True) -> List[SocialSource]:
        """Lists registered social sources."""
        return self.social_repo.list_sources(enabled_only=enabled_only)

    # -------------------------------------------------------------------------
    # 4. Aggregated Discovery Stats & Funnel
    # -------------------------------------------------------------------------

    def get_discovery_stats(self) -> Dict[str, Any]:
        """Returns comprehensive stats for the Discovery Center (Stage 23/29)."""
        task_sql = """
        SELECT status, COUNT(*) as cnt
        FROM discovery_tasks
        GROUP BY status
        """
        task_counts = {r["status"]: int(r["cnt"]) for r in self.fetchall(task_sql)}

        cand_sql = """
        SELECT status, COUNT(*) as cnt
        FROM seller_candidates
        GROUP BY status
        """
        cand_counts = {r["status"]: int(r["cnt"]) for r in self.fetchall(cand_sql)}

        sources_sql = """
        SELECT platform, COUNT(*) as total, SUM(CASE WHEN enabled = 1 THEN 1 ELSE 0 END) as active
        FROM social_sources
        GROUP BY platform
        """
        sources = [
            {
                "platform": r["platform"],
                "total": int(r["total"]),
                "active": int(r["active"] or 0),
            }
            for r in self.fetchall(sources_sql)
        ]

        # Согласование всех реальных статусов задач и кандидатов (SP30-07)
        task_in_progress = task_counts.get("RUNNING", 0) + task_counts.get("IN_PROGRESS", 0)
        cand_in_review = (
            cand_counts.get("REVIEW_REQUIRED", 0)
            + cand_counts.get("IN_REVIEW", 0)
            + cand_counts.get("IDENTITY_MATCHED", 0)
        )
        cand_onboarded = (
            cand_counts.get("APPROVED", 0)
            + cand_counts.get("INDEXED", 0)
            + cand_counts.get("ONBOARDED", 0)
        )

        return {
            "tasks": {
                "total": sum(task_counts.values()),
                "pending": task_counts.get("PENDING", 0),
                "in_progress": task_in_progress,
                "running": task_in_progress,
                "completed": task_counts.get("COMPLETED", 0),
                "failed": task_counts.get("FAILED", 0),
                "cancelled": task_counts.get("CANCELLED", 0),
            },
            "candidates": {
                "total": sum(cand_counts.values()),
                "discovered": cand_counts.get("DISCOVERED", 0),
                "profiled": cand_counts.get("PROFILED", 0),
                "in_review": cand_in_review,
                "review_required": cand_in_review,
                "approved": cand_counts.get("APPROVED", 0),
                "indexed": cand_counts.get("INDEXED", 0),
                "onboarded": cand_onboarded,
                "rejected": cand_counts.get("REJECTED", 0),
            },
            "social_sources": sources,
        }
