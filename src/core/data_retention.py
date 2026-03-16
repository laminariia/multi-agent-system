"""Data retention — periodic cleanup of stale database rows.

Prevents unbounded growth of high-volume tables by deleting rows older than
configurable retention periods.  Uses batch DELETE with LIMIT to avoid
long-running transactions and lock contention.

Designed to be called from :class:`WorkerScheduler` every 6 hours.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import and_, delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.models import (
    AgentLog,
    HITLQueue,
    SemanticCache,
)

logger = structlog.get_logger(__name__)

# Batch size per DELETE statement — keeps transactions short.
_BATCH_SIZE = 10_000


@dataclass(frozen=True)
class RetentionPolicy:
    """Retention periods for each table (days, unless noted)."""

    agent_logs_days: int = 90
    checkpoints_days: int = 30
    semantic_cache_grace_hours: int = 0  # delete where expires_at < now() - grace
    hitl_resolved_days: int = 90
    ab_test_days: int = 180
    scheduled_messages_days: int = 30


def _default_policy() -> RetentionPolicy:
    """Build policy from environment settings if available."""
    try:
        from src.core.config import get_settings

        s = get_settings()
        return RetentionPolicy(
            agent_logs_days=getattr(s, "RETENTION_AGENT_LOGS_DAYS", 90),
            checkpoints_days=getattr(s, "RETENTION_CHECKPOINTS_DAYS", 30),
            semantic_cache_grace_hours=getattr(s, "RETENTION_CACHE_GRACE_HOURS", 0),
            hitl_resolved_days=getattr(s, "RETENTION_HITL_DAYS", 90),
            ab_test_days=getattr(s, "RETENTION_AB_TEST_DAYS", 180),
            scheduled_messages_days=getattr(s, "RETENTION_MESSAGES_DAYS", 30),
        )
    except Exception:  # noqa: BLE001
        return RetentionPolicy()


class DataRetentionManager:
    """Orchestrates cleanup across all high-volume tables.

    Args:
        session: An ``AsyncSession`` for executing DELETE queries.
        policy: Retention periods.  Uses defaults from env when omitted.
    """

    def __init__(
        self,
        session: AsyncSession,
        policy: RetentionPolicy | None = None,
    ) -> None:
        self._session = session
        self._policy = policy or _default_policy()

    async def run_all(self) -> dict[str, int]:
        """Execute all purge operations and return per-table deletion counts."""
        now = datetime.now(tz=UTC)
        results: dict[str, int] = {}

        results["agent_logs"] = await self.purge_agent_logs(
            now - timedelta(days=self._policy.agent_logs_days),
        )
        results["checkpoints"] = await self.purge_checkpoints(
            now - timedelta(days=self._policy.checkpoints_days),
        )
        results["semantic_cache"] = await self.purge_expired_cache(
            grace_hours=self._policy.semantic_cache_grace_hours,
        )
        results["hitl_queue"] = await self.purge_resolved_hitl(
            now - timedelta(days=self._policy.hitl_resolved_days),
        )
        results["ab_test_results"] = await self.purge_ab_test_results(
            now - timedelta(days=self._policy.ab_test_days),
        )
        results["scheduled_messages"] = await self.purge_sent_messages(
            now - timedelta(days=self._policy.scheduled_messages_days),
        )

        total = sum(results.values())
        if total > 0:
            logger.info("data_retention_complete", total_deleted=total, **results)
        else:
            logger.debug("data_retention_noop")

        return results

    # ------------------------------------------------------------------
    # Per-table purge methods
    # ------------------------------------------------------------------

    async def purge_agent_logs(self, cutoff: datetime) -> int:
        """Delete agent_logs older than *cutoff*."""
        stmt = delete(AgentLog).where(AgentLog.created_at < cutoff)
        result = await self._session.execute(stmt)
        await self._session.flush()
        count = result.rowcount or 0
        if count:
            logger.debug("retention_purge", table="agent_logs", deleted=count)
        return count

    async def purge_checkpoints(self, cutoff: datetime) -> int:
        """Delete langgraph checkpoints + history older than *cutoff*.

        Only deletes checkpoints for threads that are NOT active
        (status != 'active').
        """
        # History first (no FK constraint, append-only)
        r1 = await self._session.execute(
            text("DELETE FROM langgraph_checkpoint_history WHERE created_at < :cutoff"),
            {"cutoff": cutoff},
        )
        # Main checkpoints — skip active threads
        r2 = await self._session.execute(
            text(
                "DELETE FROM langgraph_checkpoints "
                "WHERE created_at < :cutoff "
                "AND (status IS NULL OR status != 'active')"
            ),
            {"cutoff": cutoff},
        )
        await self._session.flush()
        count = (r1.rowcount or 0) + (r2.rowcount or 0)
        if count:
            logger.debug(
                "retention_purge",
                table="checkpoints",
                deleted=count,
                history=r1.rowcount or 0,
                main=r2.rowcount or 0,
            )
        return count

    async def purge_expired_cache(self, *, grace_hours: int = 0) -> int:
        """Delete semantic_cache rows past their expiration."""
        cutoff = datetime.now(tz=UTC) - timedelta(hours=grace_hours)
        stmt = delete(SemanticCache).where(SemanticCache.expires_at < cutoff)
        result = await self._session.execute(stmt)
        await self._session.flush()
        count = result.rowcount or 0
        if count:
            logger.debug("retention_purge", table="semantic_cache", deleted=count)
        return count

    async def purge_resolved_hitl(self, cutoff: datetime) -> int:
        """Delete resolved/expired HITL items older than *cutoff*."""
        stmt = delete(HITLQueue).where(
            and_(
                HITLQueue.status.in_(("resolved", "expired", "rejected")),
                HITLQueue.created_at < cutoff,
            )
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        count = result.rowcount or 0
        if count:
            logger.debug("retention_purge", table="hitl_queue", deleted=count)
        return count

    async def purge_ab_test_results(self, cutoff: datetime) -> int:
        """Delete A/B test results older than *cutoff*."""
        stmt = text("DELETE FROM ab_test_results WHERE created_at < :cutoff")
        result = await self._session.execute(stmt, {"cutoff": cutoff})
        await self._session.flush()
        count = result.rowcount or 0
        if count:
            logger.debug("retention_purge", table="ab_test_results", deleted=count)
        return count

    async def purge_sent_messages(self, cutoff: datetime) -> int:
        """Delete sent scheduled_messages older than *cutoff*."""
        stmt = text("DELETE FROM scheduled_messages WHERE status = 'sent' AND sent_at < :cutoff")
        result = await self._session.execute(stmt, {"cutoff": cutoff})
        await self._session.flush()
        count = result.rowcount or 0
        if count:
            logger.debug("retention_purge", table="scheduled_messages", deleted=count)
        return count
