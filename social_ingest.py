"""Сбор контента из соцсетей и превращение его в наблюдения (S2-E06).

Один источник — один проход: получить посты коннектором, сохранить ContentObservation
(S2-E08), извлечь кандидатов-предложения. Ошибки источника не бросаются наружу:
они фиксируются в social_sources.last_status и возвращаются в IngestResult, чтобы
сбой одного канала не останавливал сбор остальных и не выглядел «пустым каталогом».
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from domain.social_connector import (
    ConnectorRateLimited,
    ConnectorUnavailable,
    SocialSource,
    build_connector,
    posts_to_observations,
)
from repositories.content_repo import ContentObservationRepository
from repositories.social_source_repo import SocialSourceRepository

logger = logging.getLogger("kz_price_hunter.social_ingest")


class IngestStatus:
    OK = "OK"
    EMPTY = "EMPTY"
    RATE_LIMITED = "RATE_LIMITED"
    ERROR = "ERROR"
    DISABLED = "DISABLED"
    ALL = {OK, EMPTY, RATE_LIMITED, ERROR, DISABLED}


@dataclass
class IngestResult:
    source_id: str
    platform: str
    handle: str
    status: str
    posts: int = 0
    observations: int = 0
    candidates: int = 0
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id,
            "platform": self.platform,
            "handle": self.handle,
            "status": self.status,
            "posts": self.posts,
            "observations": self.observations,
            "candidates": self.candidates,
            "error": self.error,
        }


def _ingest_error_text(exc: BaseException) -> str:
    """Текст ошибки без секретов: только тип и короткое сообщение коннектора."""
    if isinstance(exc, ConnectorRateLimited):
        return f"RATE_LIMITED: {exc}"
    if isinstance(exc, ConnectorUnavailable):
        return f"UNAVAILABLE: {exc}"
    return f"ERROR: {type(exc).__name__}"


def ingest_source(source: SocialSource,
                  connector: Optional[Any] = None,
                  conn: Optional[Any] = None,
                  now_iso: Optional[str] = None) -> IngestResult:
    """Один проход по источнику. Не бросает исключений коннектора — только фиксирует статус.

    События прохода несут source_id и seller_id источника (S2-E24, V07)."""
    from telemetry import correlation_context
    with correlation_context(source_id=source.id, seller_id=getattr(source, "seller_id", None) or None):
        return _ingest_source(source, connector=connector, conn=conn, now_iso=now_iso)


def _ingest_source(source: SocialSource,
                   connector: Optional[Any] = None,
                   conn: Optional[Any] = None,
                   now_iso: Optional[str] = None) -> IngestResult:
    result = IngestResult(
        source_id=source.id, platform=source.platform, handle=source.handle,
        status=IngestStatus.ERROR)
    if not source.enabled:
        result.status = IngestStatus.DISABLED
        return result

    owns_connection = conn is None
    if owns_connection:
        from database import get_connection
        conn = get_connection()
    try:
        sources = SocialSourceRepository(conn)
        observations_repo = ContentObservationRepository(conn)
        active_connector = connector or build_connector(source.platform)

        posts = active_connector.fetch(source.handle)
        result.posts = len(posts)
        observations = posts_to_observations(posts, source)
        candidate_count = 0
        for observation in observations:
            observations_repo.save_observation(observation)
            candidate_count += len(observations_repo.extract_candidates(observation))
        result.observations = len(observations)
        result.candidates = candidate_count

        if not posts:
            result.status = IngestStatus.EMPTY
            sources.mark_fetch(source.id, IngestStatus.EMPTY, at=now_iso)
        else:
            result.status = IngestStatus.OK
            sources.mark_fetch(source.id, IngestStatus.OK, at=now_iso)
        return result
    except ConnectorRateLimited as exc:
        result.status = IngestStatus.RATE_LIMITED
        result.error = str(exc)
        SocialSourceRepository(conn).mark_fetch(source.id, IngestStatus.RATE_LIMITED,
                                                result.error, at=now_iso)
        return result
    except ConnectorUnavailable as exc:
        result.status = IngestStatus.ERROR
        result.error = str(exc)
        SocialSourceRepository(conn).mark_fetch(source.id, IngestStatus.ERROR,
                                                result.error, at=now_iso)
        return result
    except Exception as exc:  # источник не должен ронять сбор остальных
        logger.warning("social ingest %s/%s failed: %s", source.platform, source.handle,
                       type(exc).__name__)
        result.status = IngestStatus.ERROR
        result.error = _ingest_error_text(exc)
        try:
            SocialSourceRepository(conn).mark_fetch(source.id, IngestStatus.ERROR,
                                                    result.error, at=now_iso)
        except Exception:
            pass
        return result
    finally:
        if owns_connection:
            conn.close()


async def ingest_due_sources(limit: int = 5) -> List[IngestResult]:
    """Последовательно обходит включённые источники; вызывать из фоновой задачи/CLI."""
    from database import get_connection
    conn = get_connection()
    try:
        sources = SocialSourceRepository(conn).list_sources(enabled_only=True)[:max(1, int(limit))]
    finally:
        conn.close()

    results: List[IngestResult] = []
    for source in sources:
        result = await asyncio.to_thread(ingest_source, source)
        results.append(result)
        # Пауза между источниками: не создаём очередь запросов к одной платформе.
        await asyncio.sleep(1.0)
    return results
