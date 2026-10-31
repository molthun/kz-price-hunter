"""
TelemetryRepository (S2-E29, KZ Price Hunter 2.0).

Unified repository layer for search quality events, system telemetry,
and observability metrics.
"""

from __future__ import annotations

import datetime
import json
import logging
import sqlite3
import uuid
from typing import Any, Dict, List, Optional

from domain.models import utc_now_iso
from domain.search_analytics import SearchEvent, SearchQualitySummary
from domain.search_core import SearchOutcome
from repositories.base import BaseRepository
from repositories.search_analytics_repo import SearchAnalyticsRepository

logger = logging.getLogger("kz_price_hunter.repositories.telemetry")


class TelemetryRepository(BaseRepository):
    """
    Unified repository for application telemetry and search analytics.
    """

    def __init__(self, conn):
        super().__init__(conn)
        self.analytics_repo = SearchAnalyticsRepository(conn)

    # -------------------------------------------------------------------------
    # 1. Search Telemetry & Quality Analytics
    # -------------------------------------------------------------------------

    def record_search_event(self, event: SearchEvent) -> None:
        """Records an anonymized search query event."""
        self.analytics_repo.record_event(event)

    def get_search_quality_summary(
        self,
        since_iso: Optional[str] = None,
        city: Optional[str] = None,
    ) -> SearchQualitySummary:
        """Calculates Search Success Rate (SSR) and search quality breakdown."""
        return self.analytics_repo.get_quality_summary(since_iso=since_iso, city=city)

    def list_search_events(
        self,
        outcome: Optional[str] = None,
        city: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Lists recent search events."""
        conditions = []
        params: List[Any] = []
        if outcome:
            conditions.append("outcome = ?")
            params.append(outcome)
        if city:
            conditions.append("city = ?")
            params.append(city)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"""
        SELECT id, query, normalized_query, city, condition, channel_type,
               outcome, total_products, total_offers, latency_ms, created_at
        FROM search_events
        {where}
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
        """
        params.extend([max(1, limit), max(0, offset)])
        return self.fetchall(sql, params)

    def get_search_kpi(self, hours: int = 24) -> Dict[str, Any]:
        """Calculates search latency percentiles (p50, p95) and outcome distribution."""
        cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours)).isoformat()
        sql = """
        SELECT latency_ms, outcome
        FROM search_events
        WHERE created_at >= ?
        ORDER BY latency_ms ASC
        """
        rows = self.fetchall(sql, (cutoff,))
        total = len(rows)
        if total == 0:
            return {
                "total_searches": 0,
                "latency_p50_ms": 0.0,
                "latency_p95_ms": 0.0,
                "outcomes": {},
            }

        outcomes: Dict[str, int] = {}
        for r in rows:
            outcomes[r["outcome"]] = outcomes.get(r["outcome"], 0) + 1

        latencies = [float(r["latency_ms"]) for r in rows]
        p50_idx = int(0.50 * (total - 1))
        p95_idx = int(0.95 * (total - 1))

        return {
            "total_searches": total,
            "latency_p50_ms": round(latencies[p50_idx], 2),
            "latency_p95_ms": round(latencies[p95_idx], 2),
            "outcomes": outcomes,
        }

    # -------------------------------------------------------------------------
    # 2. System Telemetry Events
    # -------------------------------------------------------------------------

    def record_system_event(
        self,
        event_type: str,
        severity: str = "INFO",
        component: str = "core",
        message: str = "",
        data: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
        timestamp: Optional[str] = None,
    ) -> Optional[str]:
        """
        Records a structured application telemetry event (Fail-Open).
        Safe against schema differences in SQLite test environments.
        """
        ev_id = str(uuid.uuid4())
        ts = timestamp or utc_now_iso()
        data_json = json.dumps(data, ensure_ascii=False) if data else "{}"
        sql = """
        INSERT INTO telemetry_events (
            event_id, timestamp, type, severity, component,
            scan_id, message, data_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        try:
            self.execute(sql, (ev_id, ts, event_type, severity, component, correlation_id, message, data_json))
            self.commit()
            return ev_id
        except sqlite3.Error as e:
            logger.debug("TelemetryRepository: telemetry_events write skipped (%s)", e)
            return None

    def list_system_events(
        self,
        event_type: Optional[str] = None,
        severity: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Lists system telemetry events."""
        conditions = []
        params: List[Any] = []
        if event_type:
            conditions.append("type = ?")
            params.append(event_type)
        if severity:
            conditions.append("severity = ?")
            params.append(severity)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"""
        SELECT event_id, timestamp, type, severity, component, scan_id, message, data_json
        FROM telemetry_events
        {where}
        ORDER BY timestamp DESC
        LIMIT ?
        """
        try:
            params.append(max(1, limit))
            rows = self.fetchall(sql, params)
            for r in rows:
                if isinstance(r.get("data_json"), str):
                    try:
                        r["data"] = json.loads(r["data_json"])
                    except Exception:
                        r["data"] = {}
            return rows
        except sqlite3.Error as e:
            logger.debug("TelemetryRepository: telemetry_events read skipped (%s)", e)
            return []

    def get_telemetry_summary(self) -> Dict[str, Any]:
        """Returns summary of telemetry events across components and severities."""
        try:
            sql = """
            SELECT component, severity, COUNT(*) as cnt
            FROM telemetry_events
            GROUP BY component, severity
            """
            rows = self.fetchall(sql)
            summary: Dict[str, Dict[str, int]] = {}
            total = 0
            for r in rows:
                comp = r["component"]
                sev = r["severity"]
                c = int(r["cnt"])
                total += c
                summary.setdefault(comp, {})[sev] = c
            return {"total_events": total, "by_component": summary}
        except sqlite3.Error:
            return {"total_events": 0, "by_component": {}}
