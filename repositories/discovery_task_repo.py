"""
Repository for Adaptive Discovery Tasks (S2-E14, KZ Price Hunter 2.0).
Manages persistence, query deduplication, status updates, and candidate linking.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from domain.adaptive_discovery import (
    AdaptiveDiscoveryEngine,
    DiscoveryTask,
    DiscoveryTaskStatus,
)
from domain.models import utc_now_iso
from domain.search_analytics import QueryDemandSignal
from repositories.base import BaseRepository

logger = logging.getLogger("kz_price_hunter.repositories.discovery_task")


class DiscoveryTaskRepository(BaseRepository):
    """
    CRUD and operational workflow queries for DiscoveryTask instances.
    """

    def save_task(self, task: DiscoveryTask) -> DiscoveryTask:
        sql = """
        INSERT INTO discovery_tasks (
            id, signal_query, normalized_query, city, search_count,
            success_rate, priority_score, status, search_queries,
            discovered_candidates_count, notes, created_at, updated_at
        ) VALUES (
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?
        )
        ON CONFLICT(id) DO UPDATE SET
            signal_query = excluded.signal_query,
            normalized_query = excluded.normalized_query,
            city = excluded.city,
            search_count = excluded.search_count,
            success_rate = excluded.success_rate,
            priority_score = excluded.priority_score,
            status = excluded.status,
            search_queries = excluded.search_queries,
            discovered_candidates_count = excluded.discovered_candidates_count,
            notes = excluded.notes,
            updated_at = excluded.updated_at
        """
        now = utc_now_iso()
        params = (
            task.id,
            task.signal_query,
            task.normalized_query,
            task.city,
            task.search_count,
            task.success_rate,
            task.priority_score,
            task.status,
            json.dumps(task.search_queries, ensure_ascii=False),
            task.discovered_candidates_count,
            task.notes,
            task.created_at or now,
            now,
        )
        self.execute(sql, params)
        task.updated_at = now
        return task

    def get_task_by_id(self, task_id: str) -> Optional[DiscoveryTask]:
        row = self.fetchone("SELECT * FROM discovery_tasks WHERE id = ?", (task_id,))
        return DiscoveryTask.from_row(row) if row else None

    def find_active_task_for_query(
        self,
        normalized_query: str,
        city: Optional[str] = None,
    ) -> Optional[DiscoveryTask]:
        """
        Finds an existing PENDING or RUNNING task for the same query/city to avoid duplicate work.
        """
        if city:
            sql = """
            SELECT * FROM discovery_tasks
            WHERE normalized_query = ? AND city = ? AND status IN (?, ?)
            ORDER BY priority_score DESC, created_at DESC
            LIMIT 1
            """
            row = self.fetchone(sql, (normalized_query, city, DiscoveryTaskStatus.PENDING, DiscoveryTaskStatus.RUNNING))
        else:
            sql = """
            SELECT * FROM discovery_tasks
            WHERE normalized_query = ? AND (city IS NULL OR city = 'Все') AND status IN (?, ?)
            ORDER BY priority_score DESC, created_at DESC
            LIMIT 1
            """
            row = self.fetchone(sql, (normalized_query, DiscoveryTaskStatus.PENDING, DiscoveryTaskStatus.RUNNING))

        return DiscoveryTask.from_row(row) if row else None

    def find_tasks(
        self,
        status: Optional[str] = None,
        city: Optional[str] = None,
        min_priority: float = 0.0,
        limit: int = 50,
    ) -> List[DiscoveryTask]:
        conditions = ["priority_score >= ?"]
        params: List[Any] = [min_priority]

        if status:
            conditions.append("status = ?")
            params.append(status)

        if city:
            conditions.append("city = ?")
            params.append(city)

        where_clause = f"WHERE {' AND '.join(conditions)}"
        sql = f"""
        SELECT * FROM discovery_tasks
        {where_clause}
        ORDER BY priority_score DESC, created_at DESC
        LIMIT ?
        """
        params.append(limit)

        rows = self.fetchall(sql, params)
        return [DiscoveryTask.from_row(r) for r in rows]

    def update_status(
        self,
        task_id: str,
        new_status: str,
        notes: Optional[str] = None,
        candidates_delta: int = 0,
    ) -> bool:
        if new_status not in DiscoveryTaskStatus.ALL:
            raise ValueError(f"Invalid discovery task status: {new_status}")

        now = utc_now_iso()
        if notes is not None:
            sql = """
            UPDATE discovery_tasks
            SET status = ?, notes = ?, discovered_candidates_count = discovered_candidates_count + ?, updated_at = ?
            WHERE id = ?
            """
            params = (new_status, notes, candidates_delta, now, task_id)
        else:
            sql = """
            UPDATE discovery_tasks
            SET status = ?, discovered_candidates_count = discovered_candidates_count + ?, updated_at = ?
            WHERE id = ?
            """
            params = (new_status, candidates_delta, now, task_id)

        cur = self.execute(sql, params)
        return cur.rowcount > 0

    def link_candidate(self, task_id: str, candidate_id: str) -> bool:
        """
        Links a newly discovered seller candidate to this discovery task.
        Increments task candidate counter atomically.
        """
        now = utc_now_iso()
        # Update candidate
        cand_sql = "UPDATE seller_candidates SET discovery_task_id = ?, updated_at = ? WHERE id = ?"
        c_cur = self.execute(cand_sql, (task_id, now, candidate_id))

        if c_cur.rowcount > 0:
            # Increment task counter
            task_sql = """
            UPDATE discovery_tasks
            SET discovered_candidates_count = discovered_candidates_count + 1, updated_at = ?
            WHERE id = ?
            """
            self.execute(task_sql, (now, task_id))
            return True
        return False

    def generate_tasks_from_signals(
        self,
        signals: List[QueryDemandSignal],
    ) -> List[DiscoveryTask]:
        """
        Deduplicates incoming signals against existing active tasks,
        constructs new DiscoveryTask instances, and persists them.
        """
        created_tasks: List[DiscoveryTask] = []

        for sig in signals:
            if not sig.needs_discovery:
                continue

            # Check if active task already exists
            existing = self.find_active_task_for_query(sig.normalized_query, sig.city)
            if existing:
                # Update existing task demand numbers if increased
                if sig.search_count > existing.search_count:
                    new_priority = AdaptiveDiscoveryEngine.calculate_priority(sig.search_count, sig.success_rate)
                    existing.search_count = sig.search_count
                    existing.success_rate = sig.success_rate
                    existing.priority_score = new_priority
                    self.save_task(existing)
                continue

            # Create new task
            task = AdaptiveDiscoveryEngine.task_from_signal(sig)
            self.save_task(task)
            created_tasks.append(task)

        return created_tasks

    def get_tasks_summary(self) -> Dict[str, Any]:
        """Returns aggregate overview of discovery tasks."""
        sql = """
        SELECT
            COUNT(*) as total_tasks,
            COALESCE(SUM(CASE WHEN status = 'PENDING' THEN 1 ELSE 0 END), 0) as pending_tasks,
            COALESCE(SUM(CASE WHEN status = 'RUNNING' THEN 1 ELSE 0 END), 0) as running_tasks,
            COALESCE(SUM(CASE WHEN status = 'COMPLETED' THEN 1 ELSE 0 END), 0) as completed_tasks,
            COALESCE(SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END), 0) as failed_tasks,
            COALESCE(SUM(discovered_candidates_count), 0) as total_candidates_found,
            ROUND(AVG(priority_score), 2) as avg_priority
        FROM discovery_tasks
        """
        row = self.fetchone(sql)
        if not row:
            return {
                "total_tasks": 0,
                "pending_tasks": 0,
                "running_tasks": 0,
                "completed_tasks": 0,
                "failed_tasks": 0,
                "total_candidates_found": 0,
                "avg_priority": 0.0,
            }

        return {
            "total_tasks": int(row["total_tasks"]),
            "pending_tasks": int(row["pending_tasks"]),
            "running_tasks": int(row["running_tasks"]),
            "completed_tasks": int(row["completed_tasks"]),
            "failed_tasks": int(row["failed_tasks"]),
            "total_candidates_found": int(row["total_candidates_found"]),
            "avg_priority": float(row["avg_priority"] or 0.0),
        }
