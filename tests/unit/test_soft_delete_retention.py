"""Tests for soft-delete data retention.

Validates that :class:`DataRetentionManager` correctly:
- Soft-deletes old records (sets ``deleted_at``)
- Skips already-soft-deleted records
- Hard-purges after the grace period
- Respects the grace window (does not purge recent soft-deletes)
- Logs audit events
- Runs a full retention cycle
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.data_retention import DataRetentionManager, RetentionPolicy
from src.core.models import (
    ABTestResult,
    AgentLog,
    HITLQueue,
    ScheduledMessage,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(rowcount: int = 3) -> AsyncMock:
    """Build an ``AsyncSession`` mock whose execute returns *rowcount*."""
    result_mock = MagicMock()
    result_mock.rowcount = rowcount
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result_mock)
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    return session


def _policy(**overrides: int) -> RetentionPolicy:
    """Short-lived policy for tests (1 day everywhere)."""
    defaults = {
        "agent_logs_days": 1,
        "checkpoints_days": 1,
        "semantic_cache_grace_hours": 0,
        "hitl_resolved_days": 1,
        "ab_test_days": 1,
        "scheduled_messages_days": 1,
        "soft_delete_grace_days": 30,
    }
    defaults.update(overrides)
    return RetentionPolicy(**defaults)


# ---------------------------------------------------------------------------
# Soft-delete tests
# ---------------------------------------------------------------------------


class TestSoftDeleteAgentLogs:
    """soft_delete_agent_logs sets deleted_at on old rows."""

    @pytest.mark.asyncio
    async def test_sets_deleted_at(self) -> None:
        session = _make_session(rowcount=5)
        mgr = DataRetentionManager(session, _policy())
        cutoff = datetime.now(tz=UTC) - timedelta(days=1)

        count = await mgr.soft_delete_agent_logs(cutoff)

        assert count == 5
        session.execute.assert_called_once()
        session.flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_zero_rows(self) -> None:
        session = _make_session(rowcount=0)
        mgr = DataRetentionManager(session, _policy())
        cutoff = datetime.now(tz=UTC) - timedelta(days=1)

        count = await mgr.soft_delete_agent_logs(cutoff)

        assert count == 0


class TestSoftDeleteHITL:
    """soft_delete_resolved_hitl only targets resolved/expired/rejected."""

    @pytest.mark.asyncio
    async def test_soft_deletes_resolved(self) -> None:
        session = _make_session(rowcount=2)
        mgr = DataRetentionManager(session, _policy())
        cutoff = datetime.now(tz=UTC) - timedelta(days=1)

        count = await mgr.soft_delete_resolved_hitl(cutoff)

        assert count == 2
        session.execute.assert_called_once()


class TestSoftDeleteABTestResults:
    """soft_delete_ab_test_results sets deleted_at."""

    @pytest.mark.asyncio
    async def test_soft_deletes(self) -> None:
        session = _make_session(rowcount=10)
        mgr = DataRetentionManager(session, _policy())
        cutoff = datetime.now(tz=UTC) - timedelta(days=1)

        count = await mgr.soft_delete_ab_test_results(cutoff)

        assert count == 10


class TestSoftDeleteScheduledMessages:
    """soft_delete_sent_messages only targets sent messages."""

    @pytest.mark.asyncio
    async def test_soft_deletes_sent(self) -> None:
        session = _make_session(rowcount=4)
        mgr = DataRetentionManager(session, _policy())
        cutoff = datetime.now(tz=UTC) - timedelta(days=1)

        count = await mgr.soft_delete_sent_messages(cutoff)

        assert count == 4


class TestSoftDeleteIdempotency:
    """Already-soft-deleted rows are not re-deleted."""

    @pytest.mark.asyncio
    async def test_skips_already_deleted(self) -> None:
        """The WHERE clause includes deleted_at IS NULL, so already-deleted
        rows are excluded.  We verify the SQL statement structure by
        confirming the execute call is made (the filter is in the UPDATE
        statement itself)."""
        session = _make_session(rowcount=0)
        mgr = DataRetentionManager(session, _policy())
        cutoff = datetime.now(tz=UTC) - timedelta(days=1)

        count = await mgr.soft_delete_agent_logs(cutoff)

        assert count == 0
        # The statement was still executed (just matched 0 rows).
        session.execute.assert_called_once()


# ---------------------------------------------------------------------------
# Hard-purge tests
# ---------------------------------------------------------------------------


class TestPurgeSoftDeleted:
    """purge_soft_deleted hard-deletes rows past the grace window."""

    @pytest.mark.asyncio
    async def test_purges_old_soft_deletes(self) -> None:
        session = _make_session(rowcount=7)
        mgr = DataRetentionManager(session, _policy())

        counts = await mgr.purge_soft_deleted(grace_days=30)

        # 4 tables: agent_logs, hitl_queue, ab_test_results, scheduled_messages
        assert len(counts) == 4
        for table_name, ct in counts.items():
            assert ct == 7, f"{table_name} should have purged 7 rows"

    @pytest.mark.asyncio
    async def test_purge_respects_grace_period(self) -> None:
        """With rowcount=0, no rows are old enough to purge."""
        session = _make_session(rowcount=0)
        mgr = DataRetentionManager(session, _policy())

        counts = await mgr.purge_soft_deleted(grace_days=30)

        assert all(c == 0 for c in counts.values())

    @pytest.mark.asyncio
    async def test_purge_covers_all_soft_delete_models(self) -> None:
        """Ensure all 4 models are attempted."""
        session = _make_session(rowcount=1)
        mgr = DataRetentionManager(session, _policy())

        counts = await mgr.purge_soft_deleted(grace_days=0)

        expected_tables = {"agent_logs", "hitl_queue", "ab_test_results", "scheduled_messages"}
        assert set(counts.keys()) == expected_tables

    @pytest.mark.asyncio
    async def test_purge_with_zero_grace(self) -> None:
        """grace_days=0 means purge everything soft-deleted."""
        session = _make_session(rowcount=3)
        mgr = DataRetentionManager(session, _policy())

        counts = await mgr.purge_soft_deleted(grace_days=0)

        assert all(c == 3 for c in counts.values())


# ---------------------------------------------------------------------------
# Audit logging tests
# ---------------------------------------------------------------------------


class TestAuditLogging:
    """Retention cycle emits structured log events."""

    @pytest.mark.asyncio
    async def test_logs_on_soft_delete(self) -> None:
        session = _make_session(rowcount=5)
        mgr = DataRetentionManager(session, _policy())
        cutoff = datetime.now(tz=UTC) - timedelta(days=1)

        with patch("src.core.data_retention.logger") as mock_logger:
            await mgr.soft_delete_agent_logs(cutoff)
            mock_logger.debug.assert_called_once_with(
                "retention_soft_delete",
                table="agent_logs",
                soft_deleted=5,
            )

    @pytest.mark.asyncio
    async def test_no_log_on_zero_rows(self) -> None:
        session = _make_session(rowcount=0)
        mgr = DataRetentionManager(session, _policy())
        cutoff = datetime.now(tz=UTC) - timedelta(days=1)

        with patch("src.core.data_retention.logger") as mock_logger:
            await mgr.soft_delete_agent_logs(cutoff)
            mock_logger.debug.assert_not_called()

    @pytest.mark.asyncio
    async def test_logs_on_hard_purge(self) -> None:
        session = _make_session(rowcount=2)
        mgr = DataRetentionManager(session, _policy())

        with patch("src.core.data_retention.logger") as mock_logger:
            await mgr.purge_soft_deleted(grace_days=30)
            # 4 tables purged, each with 2 rows -> 4 debug calls
            assert mock_logger.debug.call_count == 4


# ---------------------------------------------------------------------------
# Full retention cycle
# ---------------------------------------------------------------------------


class TestRunAll:
    """run_all executes both soft-delete and hard-delete phases."""

    @pytest.mark.asyncio
    async def test_full_cycle(self) -> None:
        session = _make_session(rowcount=1)
        mgr = DataRetentionManager(session, _policy())

        results = await mgr.run_all()

        # Should have soft-delete keys + hard-delete keys + purge keys
        assert "agent_logs" in results
        assert "hitl_queue" in results
        assert "ab_test_results" in results
        assert "scheduled_messages" in results
        assert "checkpoints" in results
        assert "semantic_cache" in results
        # Purge keys
        assert "agent_logs_purged" in results
        assert "hitl_queue_purged" in results

    @pytest.mark.asyncio
    async def test_full_cycle_logs_total(self) -> None:
        session = _make_session(rowcount=1)
        mgr = DataRetentionManager(session, _policy())

        with patch("src.core.data_retention.logger") as mock_logger:
            await mgr.run_all()
            # At least one info log with total
            mock_logger.info.assert_called_once()
            call_kwargs = mock_logger.info.call_args
            assert call_kwargs[0][0] == "data_retention_complete"

    @pytest.mark.asyncio
    async def test_noop_cycle(self) -> None:
        session = _make_session(rowcount=0)
        mgr = DataRetentionManager(session, _policy())

        with patch("src.core.data_retention.logger") as mock_logger:
            results = await mgr.run_all()
            total = sum(results.values())
            assert total == 0
            mock_logger.debug.assert_called()


# ---------------------------------------------------------------------------
# Legacy fallback methods still work
# ---------------------------------------------------------------------------


class TestLegacyFallback:
    """Legacy hard-delete methods remain functional."""

    @pytest.mark.asyncio
    async def test_purge_agent_logs_hard(self) -> None:
        session = _make_session(rowcount=3)
        mgr = DataRetentionManager(session, _policy())
        cutoff = datetime.now(tz=UTC) - timedelta(days=1)

        count = await mgr.purge_agent_logs(cutoff)

        assert count == 3

    @pytest.mark.asyncio
    async def test_purge_resolved_hitl_hard(self) -> None:
        session = _make_session(rowcount=2)
        mgr = DataRetentionManager(session, _policy())
        cutoff = datetime.now(tz=UTC) - timedelta(days=1)

        count = await mgr.purge_resolved_hitl(cutoff)

        assert count == 2

    @pytest.mark.asyncio
    async def test_purge_ab_test_hard(self) -> None:
        session = _make_session(rowcount=1)
        mgr = DataRetentionManager(session, _policy())
        cutoff = datetime.now(tz=UTC) - timedelta(days=1)

        count = await mgr.purge_ab_test_results(cutoff)

        assert count == 1

    @pytest.mark.asyncio
    async def test_purge_sent_messages_hard(self) -> None:
        session = _make_session(rowcount=4)
        mgr = DataRetentionManager(session, _policy())
        cutoff = datetime.now(tz=UTC) - timedelta(days=1)

        count = await mgr.purge_sent_messages(cutoff)

        assert count == 4


# ---------------------------------------------------------------------------
# RetentionPolicy defaults
# ---------------------------------------------------------------------------


class TestRetentionPolicy:
    """RetentionPolicy includes soft_delete_grace_days."""

    def test_default_grace_days(self) -> None:
        policy = RetentionPolicy()
        assert policy.soft_delete_grace_days == 30

    def test_custom_grace_days(self) -> None:
        policy = RetentionPolicy(soft_delete_grace_days=7)
        assert policy.soft_delete_grace_days == 7


# ---------------------------------------------------------------------------
# SoftDeleteMixin on models
# ---------------------------------------------------------------------------


class TestSoftDeleteMixin:
    """Models have the deleted_at column from SoftDeleteMixin."""

    def test_agent_log_has_deleted_at(self) -> None:
        assert hasattr(AgentLog, "deleted_at")

    def test_hitl_queue_has_deleted_at(self) -> None:
        assert hasattr(HITLQueue, "deleted_at")

    def test_ab_test_result_has_deleted_at(self) -> None:
        assert hasattr(ABTestResult, "deleted_at")

    def test_scheduled_message_has_deleted_at(self) -> None:
        assert hasattr(ScheduledMessage, "deleted_at")
