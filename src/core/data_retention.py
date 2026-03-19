"""Data retention -- periodic cleanup of stale database rows.

Supports a two-phase lifecycle:

1. **Soft delete** -- sets ``deleted_at`` on rows older than the retention
   period.  Soft-deleted rows are invisible to future retention runs but
   remain recoverable until the hard-purge phase.
2. **Hard purge** -- permanently removes rows whose ``deleted_at`` is older
   than a configurable grace window (default 30 days).

Tables without a ``deleted_at`` column (checkpoints, semantic_cache) still
use immediate hard DELETE.

Designed to be called from :class:`WorkerScheduler` every 6 hours.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import and_, delete, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.models import (
    ABTestResult,
    AgentLog,
    HITLQueue,
    ScheduledMessage,
    SemanticCache,
)

logger = structlog.get_logger(__name__)

# Batch size per DELETE statement -- keeps transactions short.
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
    # Grace period before hard-purging soft-deleted rows.
    soft_delete_grace_days: int = 30


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
            soft_delete_grace_days=getattr(s, "RETENTION_SOFT_DELETE_GRACE_DAYS", 30),
        )
    except Exception:  # noqa: BLE001
        return RetentionPolicy()


class DataRetentionManager:
    """Orchestrates cleanup across all high-volume tables.

    Uses **soft delete** for tables that have a ``deleted_at`` column
    (agent_logs, hitl_queue, ab_test_results, scheduled_messages) and
    hard DELETE for everything else (checkpoints, semantic_cache).

    Args:
        session: An ``AsyncSession`` for executing queries.
        policy: Retention periods.  Uses defaults from env when omitted.
    """

    # Models that support soft-delete (have a deleted_at column).
    _SOFT_DELETE_MODELS: dict[str, Any] = {
        "agent_logs": AgentLog,
        "hitl_queue": HITLQueue,
        "ab_test_results": ABTestResult,
        "scheduled_messages": ScheduledMessage,
    }

    def __init__(
        self,
        session: AsyncSession,
        policy: RetentionPolicy | None = None,
    ) -> None:
        self._session = session
        self._policy = policy or _default_policy()

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def run_all(self) -> dict[str, int]:
        """Execute full retention cycle: soft-delete + hard-delete + purge.

        Returns per-table counts of affected rows.
        """
        now = datetime.now(tz=UTC)
        results: dict[str, int] = {}

        # --- Soft-delete phase (tables with deleted_at) ---
        results["agent_logs"] = await self.soft_delete_agent_logs(
            now - timedelta(days=self._policy.agent_logs_days),
        )
        results["hitl_queue"] = await self.soft_delete_resolved_hitl(
            now - timedelta(days=self._policy.hitl_resolved_days),
        )
        results["ab_test_results"] = await self.soft_delete_ab_test_results(
            now - timedelta(days=self._policy.ab_test_days),
        )
        results["scheduled_messages"] = await self.soft_delete_sent_messages(
            now - timedelta(days=self._policy.scheduled_messages_days),
        )

        # --- Hard-delete phase (tables without deleted_at) ---
        results["checkpoints"] = await self.purge_checkpoints(
            now - timedelta(days=self._policy.checkpoints_days),
        )
        results["semantic_cache"] = await self.purge_expired_cache(
            grace_hours=self._policy.semantic_cache_grace_hours,
        )

        # --- Hard-purge soft-deleted rows past grace period ---
        purge_counts = await self.purge_soft_deleted(
            grace_days=self._policy.soft_delete_grace_days,
        )
        for table, count in purge_counts.items():
            results[f"{table}_purged"] = count

        total = sum(results.values())
        if total > 0:
            logger.info("data_retention_complete", total_affected=total, **results)
        else:
            logger.debug("data_retention_noop")

        return results

    # ------------------------------------------------------------------
    # Generic soft-delete helper
    # ------------------------------------------------------------------

    async def _soft_delete_old_records(
        self,
        model: Any,
        cutoff: datetime,
        *,
        extra_filter: Any | None = None,
        table_name: str = "",
    ) -> int:
        """Soft-delete records older than *cutoff* by setting ``deleted_at``.

        Only touches rows where ``deleted_at IS NULL`` (not already deleted).
        """
        now = datetime.now(tz=UTC)
        stmt = update(model).where(model.created_at < cutoff).where(model.deleted_at.is_(None))
        if extra_filter is not None:
            stmt = stmt.where(extra_filter)
        stmt = stmt.values(deleted_at=now)
        result = await self._session.execute(stmt)
        await self._session.flush()
        count = result.rowcount or 0
        if count:
            logger.debug(
                "retention_soft_delete",
                table=table_name or model.__tablename__,
                soft_deleted=count,
            )
        return count

    # ------------------------------------------------------------------
    # Per-table soft-delete methods
    # ------------------------------------------------------------------

    async def soft_delete_agent_logs(self, cutoff: datetime) -> int:
        """Soft-delete agent_logs older than *cutoff*."""
        return await self._soft_delete_old_records(
            AgentLog,
            cutoff,
            table_name="agent_logs",
        )

    async def soft_delete_resolved_hitl(self, cutoff: datetime) -> int:
        """Soft-delete resolved/expired HITL items older than *cutoff*."""
        return await self._soft_delete_old_records(
            HITLQueue,
            cutoff,
            extra_filter=HITLQueue.status.in_(("resolved", "expired", "rejected")),
            table_name="hitl_queue",
        )

    async def soft_delete_ab_test_results(self, cutoff: datetime) -> int:
        """Soft-delete A/B test results older than *cutoff*."""
        return await self._soft_delete_old_records(
            ABTestResult,
            cutoff,
            table_name="ab_test_results",
        )

    async def soft_delete_sent_messages(self, cutoff: datetime) -> int:
        """Soft-delete sent scheduled_messages older than *cutoff*."""
        return await self._soft_delete_old_records(
            ScheduledMessage,
            cutoff,
            extra_filter=ScheduledMessage.status.in_(("sent",)),
            table_name="scheduled_messages",
        )

    # ------------------------------------------------------------------
    # Hard-purge soft-deleted rows
    # ------------------------------------------------------------------

    async def purge_soft_deleted(self, grace_days: int = 30) -> dict[str, int]:
        """Hard-delete records whose ``deleted_at`` is older than *grace_days*.

        This permanently removes rows that were previously soft-deleted and
        have sat in the "deleted" state long enough.
        """
        cutoff = datetime.now(tz=UTC) - timedelta(days=grace_days)
        counts: dict[str, int] = {}
        for model_name, model in self._SOFT_DELETE_MODELS.items():
            stmt = delete(model).where(
                and_(
                    model.deleted_at.isnot(None),
                    model.deleted_at < cutoff,
                )
            )
            result = await self._session.execute(stmt)
            await self._session.flush()
            row_count = result.rowcount or 0
            counts[model_name] = row_count
            if row_count:
                logger.debug(
                    "retention_hard_purge",
                    table=model_name,
                    purged=row_count,
                )
        return counts

    # ------------------------------------------------------------------
    # Hard-delete methods (tables without soft-delete support)
    # ------------------------------------------------------------------

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
        # Main checkpoints -- skip active threads
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

    # ------------------------------------------------------------------
    # Legacy hard-delete methods (kept as fallback)
    # ------------------------------------------------------------------

    async def purge_agent_logs(self, cutoff: datetime) -> int:
        """Hard-delete agent_logs older than *cutoff* (legacy fallback)."""
        stmt = delete(AgentLog).where(AgentLog.created_at < cutoff)
        result = await self._session.execute(stmt)
        await self._session.flush()
        count = result.rowcount or 0
        if count:
            logger.debug("retention_purge", table="agent_logs", deleted=count)
        return count

    async def purge_resolved_hitl(self, cutoff: datetime) -> int:
        """Hard-delete resolved HITL items older than *cutoff* (legacy fallback)."""
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
        """Hard-delete A/B test results older than *cutoff* (legacy fallback)."""
        stmt = delete(ABTestResult).where(ABTestResult.created_at < cutoff)
        result = await self._session.execute(stmt)
        await self._session.flush()
        count = result.rowcount or 0
        if count:
            logger.debug("retention_purge", table="ab_test_results", deleted=count)
        return count

    async def purge_sent_messages(self, cutoff: datetime) -> int:
        """Hard-delete sent scheduled_messages older than *cutoff* (legacy fallback)."""
        stmt = delete(ScheduledMessage).where(
            and_(
                ScheduledMessage.status.in_(("sent",)),
                ScheduledMessage.created_at < cutoff,
            )
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        count = result.rowcount or 0
        if count:
            logger.debug("retention_purge", table="scheduled_messages", deleted=count)
        return count
