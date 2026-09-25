"""Unit tests for HITL Capacity Management (src/core/hitl_capacity.py).

Tests cover: capacity check under/at limit, stale expiration,
configurable max pending, and HITLCapacityExceeded exception.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.hitl_capacity import (
    HITLCapacityExceeded,
    check_hitl_capacity,
    expire_stale_hitl,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_session(pending_count: int = 0) -> AsyncMock:
    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one.return_value = pending_count
    session.execute = AsyncMock(return_value=mock_result)
    return session


# ---------------------------------------------------------------------------
# Capacity check
# ---------------------------------------------------------------------------


class TestCheckHITLCapacity:
    """Verify capacity gating on pending HITL items."""

    async def test_under_limit_passes(self):
        session = _mock_session(pending_count=50)
        # Should not raise
        await check_hitl_capacity(session, max_pending=100)

    async def test_at_limit_raises(self):
        session = _mock_session(pending_count=100)
        with pytest.raises(HITLCapacityExceeded, match="100"):
            await check_hitl_capacity(session, max_pending=100)

    async def test_over_limit_raises(self):
        session = _mock_session(pending_count=150)
        with pytest.raises(HITLCapacityExceeded):
            await check_hitl_capacity(session, max_pending=100)

    async def test_zero_pending_passes(self):
        session = _mock_session(pending_count=0)
        await check_hitl_capacity(session, max_pending=100)

    async def test_configurable_limit(self):
        session = _mock_session(pending_count=10)
        with pytest.raises(HITLCapacityExceeded):
            await check_hitl_capacity(session, max_pending=5)

    async def test_default_max_pending(self):
        """Default max_pending should be 100."""
        session = _mock_session(pending_count=99)
        # Should not raise with default
        await check_hitl_capacity(session)

    async def test_capacity_exceeded_is_mas_exception(self):
        from src.core.exceptions import MASException

        assert issubclass(HITLCapacityExceeded, MASException)


# ---------------------------------------------------------------------------
# Expire stale
# ---------------------------------------------------------------------------


class TestExpireStaleHITL:
    """Verify stale pending items are marked expired."""

    async def test_expires_stale_items(self):
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.rowcount = 3
        session.execute = AsyncMock(return_value=mock_result)

        count = await expire_stale_hitl(session, timeout_minutes=60)
        assert count == 3

    async def test_zero_when_none_stale(self):
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.rowcount = 0
        session.execute = AsyncMock(return_value=mock_result)

        count = await expire_stale_hitl(session, timeout_minutes=60)
        assert count == 0

    async def test_custom_timeout(self):
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.rowcount = 1
        session.execute = AsyncMock(return_value=mock_result)

        count = await expire_stale_hitl(session, timeout_minutes=5)
        assert count == 1
        # Verify execute was called (the UPDATE query)
        session.execute.assert_called_once()

    async def test_commits_after_expiry(self):
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.rowcount = 2
        session.execute = AsyncMock(return_value=mock_result)

        await expire_stale_hitl(session, timeout_minutes=60)
        session.flush.assert_called_once()

    async def test_no_commit_when_zero(self):
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.rowcount = 0
        session.execute = AsyncMock(return_value=mock_result)

        await expire_stale_hitl(session, timeout_minutes=60)
        session.flush.assert_not_called()
