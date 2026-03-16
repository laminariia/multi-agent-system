"""Unit tests for src.core.data_retention."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.data_retention import DataRetentionManager, RetentionPolicy, _default_policy


class TestRetentionPolicy:
    """RetentionPolicy dataclass tests."""

    def test_defaults(self):
        p = RetentionPolicy()
        assert p.agent_logs_days == 90
        assert p.checkpoints_days == 30
        assert p.semantic_cache_grace_hours == 0
        assert p.hitl_resolved_days == 90
        assert p.ab_test_days == 180
        assert p.scheduled_messages_days == 30

    def test_custom(self):
        p = RetentionPolicy(agent_logs_days=7, checkpoints_days=3)
        assert p.agent_logs_days == 7
        assert p.checkpoints_days == 3

    def test_frozen(self):
        p = RetentionPolicy()
        with pytest.raises(AttributeError):
            p.agent_logs_days = 10  # type: ignore[misc]


class TestDefaultPolicy:
    """_default_policy() reads from config or falls back."""

    def test_fallback_on_import_error(self):
        with patch("src.core.config.get_settings", side_effect=ImportError):
            p = _default_policy()
        assert p.agent_logs_days == 90

    def test_reads_settings(self):
        mock_settings = MagicMock()
        mock_settings.RETENTION_AGENT_LOGS_DAYS = 14
        mock_settings.RETENTION_CHECKPOINTS_DAYS = 7
        mock_settings.RETENTION_CACHE_GRACE_HOURS = 2
        mock_settings.RETENTION_HITL_DAYS = 30
        mock_settings.RETENTION_AB_TEST_DAYS = 60
        mock_settings.RETENTION_MESSAGES_DAYS = 10
        with patch("src.core.config.get_settings", return_value=mock_settings):
            p = _default_policy()
        assert p.agent_logs_days == 14
        assert p.checkpoints_days == 7


def _mock_result(rowcount: int) -> MagicMock:
    r = MagicMock()
    r.rowcount = rowcount
    return r


@pytest.fixture()
def session() -> AsyncMock:
    s = AsyncMock()
    s.execute = AsyncMock(return_value=_mock_result(5))
    s.flush = AsyncMock()
    s.commit = AsyncMock()
    return s


@pytest.fixture()
def manager(session: AsyncMock) -> DataRetentionManager:
    return DataRetentionManager(session, policy=RetentionPolicy())


class TestPurgeAgentLogs:
    @pytest.mark.asyncio()
    async def test_deletes_old_logs(self, manager, session):
        count = await manager.purge_agent_logs(datetime.now(tz=UTC) - timedelta(days=90))
        assert count == 5
        session.execute.assert_called_once()
        session.flush.assert_called_once()

    @pytest.mark.asyncio()
    async def test_zero_when_none(self, session):
        session.execute = AsyncMock(return_value=_mock_result(0))
        mgr = DataRetentionManager(session)
        count = await mgr.purge_agent_logs(datetime.now(tz=UTC))
        assert count == 0


class TestPurgeCheckpoints:
    @pytest.mark.asyncio()
    async def test_deletes_both_tables(self, manager, session):
        session.execute = AsyncMock(side_effect=[_mock_result(3), _mock_result(2)])
        count = await manager.purge_checkpoints(datetime.now(tz=UTC) - timedelta(days=30))
        assert count == 5  # 3 + 2
        assert session.execute.call_count == 2


class TestPurgeExpiredCache:
    @pytest.mark.asyncio()
    async def test_deletes_expired(self, manager, session):
        count = await manager.purge_expired_cache()
        assert count == 5

    @pytest.mark.asyncio()
    async def test_grace_hours(self, manager, session):
        count = await manager.purge_expired_cache(grace_hours=24)
        assert count == 5


class TestPurgeResolvedHITL:
    @pytest.mark.asyncio()
    async def test_deletes_resolved(self, manager, session):
        count = await manager.purge_resolved_hitl(datetime.now(tz=UTC) - timedelta(days=90))
        assert count == 5


class TestPurgeABTestResults:
    @pytest.mark.asyncio()
    async def test_deletes_old(self, manager, session):
        count = await manager.purge_ab_test_results(datetime.now(tz=UTC) - timedelta(days=180))
        assert count == 5


class TestPurgeSentMessages:
    @pytest.mark.asyncio()
    async def test_deletes_sent(self, manager, session):
        count = await manager.purge_sent_messages(datetime.now(tz=UTC) - timedelta(days=30))
        assert count == 5


class TestRunAll:
    @pytest.mark.asyncio()
    async def test_orchestrates_all_purges(self, session):
        # Each purge returns different counts
        call_count = 0

        async def mock_execute(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            counts = [10, 3, 2, 5, 20, 1, 0, 8]  # Extra for checkpoints (2 calls)
            idx = min(call_count - 1, len(counts) - 1)
            return _mock_result(counts[idx])

        session.execute = mock_execute
        mgr = DataRetentionManager(session, policy=RetentionPolicy())
        results = await mgr.run_all()

        assert "agent_logs" in results
        assert "checkpoints" in results
        assert "semantic_cache" in results
        assert "hitl_queue" in results
        assert "ab_test_results" in results
        assert "scheduled_messages" in results
        assert sum(results.values()) > 0

    @pytest.mark.asyncio()
    async def test_noop_when_nothing_to_delete(self, session):
        session.execute = AsyncMock(return_value=_mock_result(0))
        mgr = DataRetentionManager(session, policy=RetentionPolicy())
        results = await mgr.run_all()
        assert all(v == 0 for v in results.values())
