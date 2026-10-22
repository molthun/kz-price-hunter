"""
Search Analytics Repository for KZ Price Hunter 2.0 (Search Quality KPI & Demand Signals).
Persists anonymized search events and calculates Search Success Rate (SSR).
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any, Dict, List, Optional

from domain.search_analytics import (
    QueryDemandSignal,
    SearchEvent,
    SearchQualitySummary,
)
from domain.search_core import SearchOutcome
from repositories.base import BaseRepository

logger = logging.getLogger("kz_price_hunter.repositories.search_analytics")


class SearchAnalyticsRepository(BaseRepository):
    """Repository for logging search telemetry and aggregating search quality metrics."""

    RARE_PLACEHOLDER = "[rare]"

    def _text_allowed(self, event: SearchEvent) -> bool:
        """Можно ли хранить текст запроса: только начиная с третьего повтора (правило P06).

        У правила приватности проекта две половины. Первую — вырезание телефонов, почты и адресов —
        новый контур уже переиспользует через `is_sensitive`. Вторая не менее важна: единичный
        запрос опознаёт человека и без телефона, поэтому текст редкого запроса не хранится вовсе.
        Счётчик повторов ведётся по хешу в той же таблице `search_query_seen`, что и в P06, —
        дублировать механизм не нужно.

        Если счётчик недоступен (база без legacy-таблиц), считаем запрос редким: осторожный
        ответ при неизвестности, а не хранение текста «на всякий случай».
        """
        import search_analytics as sa

        # Ключ считаем от нормализованной формы v2, а не от исходной строки: она инвариантна
        # к перестановке слов, поэтому «iphone 16» и «16 iphone» — один и тот же запрос.
        # С ключом по исходному тексту перестановки копились бы порознь и порога не достигали.
        key = sa.query_key(event.normalized_query or event.query, event.city)
        try:
            self.execute(
                """INSERT INTO search_query_seen (query_key, seen, last_seen) VALUES (?, 1, ?)
                   ON CONFLICT(query_key) DO UPDATE SET seen = seen + 1,
                                                        last_seen = excluded.last_seen""",
                (key, event.created_at),
            )
            row = self.fetchone("SELECT seen FROM search_query_seen WHERE query_key = ?", (key,))
        except sqlite3.Error:
            return False
        return bool(row) and row["seen"] >= sa.MIN_OCCURRENCES_TO_STORE_TEXT

    def record_event(self, event: SearchEvent) -> None:
        """Records an anonymized search interaction."""
        if event.query != "[redacted]" and not self._text_allowed(event):
            # Текст редкого запроса не хранится ни в исходном, ни в нормализованном виде:
            # нормализация меняет регистр и порядок слов, но текст остаётся читаемым.
            event.query = self.RARE_PLACEHOLDER
            event.normalized_query = self.RARE_PLACEHOLDER
        sql = """
        INSERT INTO search_events (
            id, query, normalized_query, city, condition, channel_type,
            outcome, total_products, total_offers, latency_ms, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        try:
            self.execute(
                sql,
                (
                    event.id,
                    event.query,
                    event.normalized_query,
                    event.city,
                    event.condition,
                    event.channel_type,
                    event.outcome,
                    event.total_products,
                    event.total_offers,
                    event.latency_ms,
                    event.created_at,
                ),
            )
            self.commit()
        except sqlite3.Error as e:
            logger.warning("Failed to record search event: %s", e)

    def get_quality_summary(
        self,
        since_iso: Optional[str] = None,
        city: Optional[str] = None,
    ) -> SearchQualitySummary:
        """
        Computes the Search Quality Summary and Search Success Rate (SSR).
        """
        conditions = []
        params: List[Any] = []

        if since_iso:
            conditions.append("created_at >= ?")
            params.append(since_iso)
        if city:
            conditions.append("city = ?")
            params.append(city)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        sql = f"""
        SELECT
            COUNT(*) as total_searches,
            COALESCE(SUM(CASE WHEN outcome = '{SearchOutcome.FOUND}' THEN 1 ELSE 0 END), 0) as found_count,
            COALESCE(SUM(CASE WHEN outcome = '{SearchOutcome.WEAK_MATCH}' THEN 1 ELSE 0 END), 0) as weak_match_count,
            COALESCE(SUM(CASE WHEN outcome = '{SearchOutcome.NOT_FOUND}' THEN 1 ELSE 0 END), 0) as not_found_count,
            COALESCE(SUM(CASE WHEN outcome = '{SearchOutcome.ERROR}' THEN 1 ELSE 0 END), 0) as error_count,
            COALESCE(AVG(latency_ms), 0.0) as avg_latency_ms,
            MIN(created_at) as period_start,
            MAX(created_at) as period_end
        FROM search_events
        {where_clause}
        """

        row = self.fetchone(sql, params)
        if not row or row["total_searches"] == 0:
            return SearchQualitySummary(
                total_searches=0,
                found_count=0,
                weak_match_count=0,
                not_found_count=0,
                error_count=0,
                search_success_rate=0.0,
                avg_latency_ms=0.0,
                period_start=since_iso,
                period_end=None,
            )

        total = int(row["total_searches"])
        found = int(row["found_count"])
        weak = int(row["weak_match_count"])
        not_found = int(row["not_found_count"])
        error = int(row["error_count"])
        answered = found + weak + not_found
        ssr = round((found * 100.0 / answered), 2) if answered > 0 else 0.0
        err_rate = round((error * 100.0 / total), 2) if total > 0 else 0.0

        return SearchQualitySummary(
            total_searches=total,
            answered_count=answered,
            found_count=found,
            weak_match_count=weak,
            not_found_count=not_found,
            error_count=error,
            search_success_rate=ssr,
            error_rate=err_rate,
            avg_latency_ms=round(float(row["avg_latency_ms"]), 2),
            period_start=row["period_start"],
            period_end=row["period_end"],
        )

    def get_top_queries(
        self,
        limit: int = 50,
        since_iso: Optional[str] = None,
        city: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Returns top queries with search frequency and success rate.
        Excludes sensitive redacted queries to preserve user privacy (C-E13-01).
        """
        conditions = ["normalized_query != ''", "normalized_query != '[redacted]'",
                      "normalized_query != '[rare]'"]
        params: List[Any] = []

        if since_iso:
            conditions.append("created_at >= ?")
            params.append(since_iso)
        if city:
            conditions.append("city = ?")
            params.append(city)

        where_clause = f"WHERE {' AND '.join(conditions)}"

        sql = f"""
        SELECT
            normalized_query,
            CASE WHEN query = '[redacted]' THEN normalized_query ELSE MIN(query) END as sample_query,
            COUNT(*) as search_count,
            COALESCE(SUM(CASE WHEN outcome = '{SearchOutcome.FOUND}' THEN 1 ELSE 0 END), 0) as found_count,
            COALESCE(SUM(CASE WHEN outcome = '{SearchOutcome.ERROR}' THEN 1 ELSE 0 END), 0) as error_count,
            COALESCE(SUM(CASE WHEN outcome IN ('{SearchOutcome.FOUND}', '{SearchOutcome.WEAK_MATCH}', '{SearchOutcome.NOT_FOUND}') THEN 1 ELSE 0 END), 0) as answered_count,
            ROUND(AVG(total_products), 1) as avg_products,
            ROUND(AVG(latency_ms), 1) as avg_latency_ms
        FROM search_events
        {where_clause}
        GROUP BY normalized_query
        ORDER BY search_count DESC, normalized_query ASC
        LIMIT ?
        """
        params.append(limit)

        rows = self.fetchall(sql, params)
        results = []
        for r in rows:
            cnt = int(r["search_count"])
            fnd = int(r["found_count"])
            ans = int(r["answered_count"])
            rate = round((fnd * 100.0 / ans), 1) if ans > 0 else 0.0
            results.append({
                "normalized_query": r["normalized_query"],
                "sample_query": r["sample_query"],
                "search_count": cnt,
                "found_count": fnd,
                "answered_count": ans,
                "success_rate": rate,
                "avg_products": float(r["avg_products"] or 0.0),
                "avg_latency_ms": float(r["avg_latency_ms"] or 0.0),
            })
        return results

    def get_discovery_signals(
        self,
        min_searches: int = 5,
        max_success_rate: float = 70.0,
        limit: int = 50,
        since_iso: Optional[str] = None,
        city: Optional[str] = None,
    ) -> List[QueryDemandSignal]:
        """
        Extracts high-demand, low-fulfillment queries to feed into Adaptive Discovery (S2-E14).
        """
        top_queries = self.get_top_queries(
            limit=limit * 2,
            since_iso=since_iso,
            city=city,
        )

        signals: List[QueryDemandSignal] = []
        for q in top_queries:
            if q["search_count"] >= min_searches and q["success_rate"] <= max_success_rate:
                signals.append(
                    QueryDemandSignal(
                        normalized_query=q["normalized_query"],
                        sample_query=q["sample_query"],
                        search_count=q["search_count"],
                        success_rate=q["success_rate"],
                        city=city,
                        needs_discovery=True,
                    )
                )
                if len(signals) >= limit:
                    break

        return signals

    def count_events(self) -> int:
        """Returns total number of recorded search events."""
        row = self.fetchone("SELECT COUNT(*) as cnt FROM search_events")
        return int(row["cnt"]) if row else 0

    def purge_old_events(self, days: Optional[int] = None) -> int:
        """Удаляет события поиска старше срока хранения.

        Срок берётся из общей политики (`search_analytics.RETENTION_DAYS`), а не задаётся здесь
        своим числом: два независимых срока в одном проекте разъезжаются молча.

        По расписанию очистку выполняет `database.prune_search_analytics` — она чистит и эту
        таблицу, и счётчик повторов, и агрегаты legacy-контура одной операцией. Этот метод
        оставлен для точечных вызовов и проверок; он не заменяет общий путь.
        """
        import search_analytics as sa

        days = days if days is not None else sa.RETENTION_DAYS
        sql = "DELETE FROM search_events WHERE julianday(created_at) < julianday('now') - ?"
        cur = self.execute(sql, (days,))
        self.commit()
        return cur.rowcount
