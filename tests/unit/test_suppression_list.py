"""Unit tests for SuppressionList (src/enrichment/suppression.py).

Tests cover: suppress/unsuppress, is_suppressed check, bulk_check,
idempotent suppression, expiration cleanup, and CAN-SPAM/GDPR reasons.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.enrichment.suppression import SuppressionList

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_session() -> AsyncMock:
    """Create a mock DB session with async context manager support."""
    session = AsyncMock()
    session.add = MagicMock()
    session.delete = MagicMock()
    session.commit = AsyncMock()
    session.flush = AsyncMock()
    return session


# ---------------------------------------------------------------------------
# Suppress
# ---------------------------------------------------------------------------


class TestSuppressionListSuppress:
    """Adding emails to the suppression list."""

    async def test_suppress_email(self):
        session = _mock_session()
        # No existing entry
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=mock_result)

        sl = SuppressionList(session)
        await sl.suppress("bad@example.com", reason="hard_bounce", source="bounce_handler")

        session.add.assert_called_once()
        added = session.add.call_args[0][0]
        assert added.email == "bad@example.com"
        assert added.reason == "hard_bounce"
        assert added.source == "bounce_handler"

    async def test_suppress_normalizes_email(self):
        session = _mock_session()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=mock_result)

        sl = SuppressionList(session)
        await sl.suppress("BAD@Example.COM", reason="manual", source="admin")

        added = session.add.call_args[0][0]
        assert added.email == "bad@example.com"

    async def test_duplicate_suppress_is_idempotent(self):
        session = _mock_session()
        existing = MagicMock()
        existing.reason = "hard_bounce"
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = existing
        session.execute = AsyncMock(return_value=mock_result)

        sl = SuppressionList(session)
        await sl.suppress("dup@example.com", reason="hard_bounce", source="bounce_handler")

        # Should NOT add a new entry
        session.add.assert_not_called()

    async def test_suppress_with_expiration(self):
        session = _mock_session()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=mock_result)

        expires = datetime.now(UTC) + timedelta(days=7)
        sl = SuppressionList(session)
        await sl.suppress("soft@example.com", reason="soft_bounce", source="bounce_handler", expires_at=expires)

        added = session.add.call_args[0][0]
        assert added.expires_at == expires

    async def test_gdpr_erasure_reason(self):
        session = _mock_session()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=mock_result)

        sl = SuppressionList(session)
        await sl.suppress("gdpr@example.com", reason="gdpr_erasure", source="user_request")

        added = session.add.call_args[0][0]
        assert added.reason == "gdpr_erasure"
        assert added.source == "user_request"


# ---------------------------------------------------------------------------
# Is suppressed
# ---------------------------------------------------------------------------


class TestSuppressionListCheck:
    """Checking if an email is suppressed."""

    async def test_suppressed_returns_true(self):
        session = _mock_session()
        entry = MagicMock()
        entry.expires_at = None  # permanent
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = entry
        session.execute = AsyncMock(return_value=mock_result)

        sl = SuppressionList(session)
        assert await sl.is_suppressed("bad@example.com") is True

    async def test_not_suppressed_returns_false(self):
        session = _mock_session()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=mock_result)

        sl = SuppressionList(session)
        assert await sl.is_suppressed("good@example.com") is False

    async def test_expired_suppression_returns_false(self):
        session = _mock_session()
        entry = MagicMock()
        entry.expires_at = datetime.now(UTC) - timedelta(hours=1)  # expired
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = entry
        session.execute = AsyncMock(return_value=mock_result)

        sl = SuppressionList(session)
        assert await sl.is_suppressed("expired@example.com") is False

    async def test_check_normalizes_email(self):
        """Verify that is_suppressed normalizes to lowercase before query."""
        session = _mock_session()
        entry = MagicMock()
        entry.expires_at = None  # permanent
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = entry
        session.execute = AsyncMock(return_value=mock_result)

        sl = SuppressionList(session)
        # Mixed-case input should still find the suppressed entry
        result = await sl.is_suppressed("UPPER@EXAMPLE.COM")
        assert result is True
        session.execute.assert_called_once()


# ---------------------------------------------------------------------------
# Bulk check
# ---------------------------------------------------------------------------


class TestSuppressionListBulkCheck:
    """Bulk checking multiple emails."""

    async def test_bulk_check_returns_suppressed_set(self):
        session = _mock_session()

        mock_scalars = MagicMock()
        mock_scalars.all.return_value = ["bad1@example.com", "bad2@example.com"]
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        session.execute = AsyncMock(return_value=mock_result)

        sl = SuppressionList(session)
        result = await sl.bulk_check(["bad1@example.com", "bad2@example.com", "good@example.com"])

        assert isinstance(result, set)
        assert "bad1@example.com" in result
        assert "bad2@example.com" in result
        assert "good@example.com" not in result

    async def test_bulk_check_empty_input(self):
        session = _mock_session()
        sl = SuppressionList(session)
        result = await sl.bulk_check([])
        assert result == set()


# ---------------------------------------------------------------------------
# Unsuppress
# ---------------------------------------------------------------------------


class TestSuppressionListUnsuppress:
    """Admin override to remove suppression."""

    async def test_unsuppress_removes_entry(self):
        session = _mock_session()
        entry = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = entry
        session.execute = AsyncMock(return_value=mock_result)

        sl = SuppressionList(session)
        await sl.unsuppress("forgiven@example.com")

        session.delete.assert_called_once_with(entry)

    async def test_unsuppress_nonexistent_is_noop(self):
        session = _mock_session()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=mock_result)

        sl = SuppressionList(session)
        await sl.unsuppress("never@example.com")

        session.delete.assert_not_called()


# ---------------------------------------------------------------------------
# Cleanup expired
# ---------------------------------------------------------------------------


class TestSuppressionListCleanup:
    """Removing expired temporary suppressions."""

    async def test_cleanup_returns_count(self):
        session = _mock_session()
        mock_result = MagicMock()
        mock_result.rowcount = 5
        session.execute = AsyncMock(return_value=mock_result)

        sl = SuppressionList(session)
        removed = await sl.cleanup_expired()
        assert removed == 5

    async def test_cleanup_zero_when_none_expired(self):
        session = _mock_session()
        mock_result = MagicMock()
        mock_result.rowcount = 0
        session.execute = AsyncMock(return_value=mock_result)

        sl = SuppressionList(session)
        removed = await sl.cleanup_expired()
        assert removed == 0
