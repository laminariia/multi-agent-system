"""Unit tests for HITL viewing locks (soft lock via Valkey).

Tests the viewing lock endpoints added to ``HITLController``:
- ``POST /api/v1/hitl/{hitl_id}/viewing`` — acquire soft lock
- ``DELETE /api/v1/hitl/{hitl_id}/viewing`` — release lock

Also tests that ``GET /api/v1/hitl/{hitl_id}`` returns ``locked_by`` field.

Lock implementation uses Valkey SET NX EX with key
``mas:hitl_lock:{hitl_id}``, value = user_id, TTL = 300s.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.api.routes.hitl import HITLController
from src.api.schemas import HITLViewingLockResponseSchema

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _create_mock_request(
    user_id: uuid.UUID | None = None,
    email: str = "operator@example.com",
    name: str = "Operator",
) -> MagicMock:
    """Create a mock Litestar Request with a user attached."""
    if user_id is None:
        user_id = uuid.uuid4()

    mock_request = MagicMock()
    mock_request.user = MagicMock()
    mock_request.user.id = user_id
    mock_request.user.email = email
    mock_request.user.name = name
    return mock_request


def _create_mock_hitl_item(
    item_id: uuid.UUID | None = None,
    item_type: str = "bid_approval",
    status: str = "pending",
) -> MagicMock:
    """Create a mock HITLQueue ORM row."""
    if item_id is None:
        item_id = uuid.uuid4()

    mock_item = MagicMock()
    mock_item.id = item_id
    mock_item.type = item_type
    mock_item.priority = "normal"
    mock_item.title = f"Test {item_type}"
    mock_item.description = "Test description"
    mock_item.expires_at = datetime.now(UTC) + timedelta(hours=1)
    mock_item.payload = {"thread_id": "t-123"}
    mock_item.available_actions = ["approve", "reject", "edit", "skip", "later"]
    mock_item.created_at = datetime.now(UTC)
    mock_item.status = status
    mock_item.resolution = None
    mock_item.resolution_note = None
    mock_item.resolved_by = None
    mock_item.resolved_at = None
    return mock_item


def _create_mock_db_session(item: MagicMock | None = None) -> AsyncMock:
    """Create a mock AsyncSession; optional item returned from scalar_one_or_none."""
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = item
    session.execute.return_value = result
    return session


# ---------------------------------------------------------------------------
# POST /api/v1/hitl/{hitl_id}/viewing — acquire lock
# ---------------------------------------------------------------------------


class TestAcquireViewingLock:
    """Tests for acquiring a HITL viewing lock."""

    @pytest.mark.asyncio
    async def test_acquire_lock_success(self) -> None:
        """Should acquire lock when no one else is viewing."""
        hitl_id = uuid.uuid4()
        user_id = uuid.uuid4()
        mock_request = _create_mock_request(user_id=user_id)
        mock_valkey = AsyncMock()
        mock_valkey.set = AsyncMock(return_value=True)  # NX succeeded

        item = _create_mock_hitl_item(item_id=hitl_id)
        db_session = _create_mock_db_session(item=item)

        result = await HITLController.acquire_viewing_lock.fn(
            self=None,
            hitl_id=hitl_id,
            request=mock_request,
            db_session=db_session,
            valkey=mock_valkey,
        )

        assert isinstance(result, HITLViewingLockResponseSchema)
        assert result.locked is True
        assert result.locked_by == str(user_id)

        # Verify Valkey SET NX EX was called correctly
        mock_valkey.set.assert_called_once_with(
            f"mas:hitl_lock:{hitl_id}",
            str(user_id),
            nx=True,
            ex=300,
        )

    @pytest.mark.asyncio
    async def test_acquire_lock_already_held_by_another(self) -> None:
        """Should return locked=False when another operator holds the lock."""
        hitl_id = uuid.uuid4()
        user_id = uuid.uuid4()
        other_user_id = uuid.uuid4()
        mock_request = _create_mock_request(user_id=user_id)
        mock_valkey = AsyncMock()
        mock_valkey.set = AsyncMock(return_value=None)  # NX failed
        mock_valkey.get = AsyncMock(return_value=str(other_user_id).encode())

        item = _create_mock_hitl_item(item_id=hitl_id)
        db_session = _create_mock_db_session(item=item)

        result = await HITLController.acquire_viewing_lock.fn(
            self=None,
            hitl_id=hitl_id,
            request=mock_request,
            db_session=db_session,
            valkey=mock_valkey,
        )

        assert result.locked is False
        assert result.locked_by == str(other_user_id)

    @pytest.mark.asyncio
    async def test_acquire_lock_already_held_by_self(self) -> None:
        """Should return locked=True when the same operator already holds the lock."""
        hitl_id = uuid.uuid4()
        user_id = uuid.uuid4()
        mock_request = _create_mock_request(user_id=user_id)
        mock_valkey = AsyncMock()
        mock_valkey.set = AsyncMock(return_value=None)  # NX failed
        mock_valkey.get = AsyncMock(return_value=str(user_id).encode())
        # Refresh TTL should succeed
        mock_valkey.expire = AsyncMock(return_value=True)

        item = _create_mock_hitl_item(item_id=hitl_id)
        db_session = _create_mock_db_session(item=item)

        result = await HITLController.acquire_viewing_lock.fn(
            self=None,
            hitl_id=hitl_id,
            request=mock_request,
            db_session=db_session,
            valkey=mock_valkey,
        )

        assert result.locked is True
        assert result.locked_by == str(user_id)
        # TTL should be refreshed
        mock_valkey.expire.assert_called_once_with(
            f"mas:hitl_lock:{hitl_id}",
            300,
        )

    @pytest.mark.asyncio
    async def test_acquire_lock_hitl_not_found(self) -> None:
        """Should raise NotFoundException when HITL item does not exist."""
        from litestar.exceptions import NotFoundException

        hitl_id = uuid.uuid4()
        mock_request = _create_mock_request()
        mock_valkey = AsyncMock()

        db_session = _create_mock_db_session(item=None)

        with pytest.raises(NotFoundException):
            await HITLController.acquire_viewing_lock.fn(
                self=None,
                hitl_id=hitl_id,
                request=mock_request,
                db_session=db_session,
                valkey=mock_valkey,
            )

    @pytest.mark.asyncio
    async def test_acquire_lock_uses_correct_key_format(self) -> None:
        """Should use key format mas:hitl_lock:{hitl_id}."""
        hitl_id = uuid.uuid4()
        user_id = uuid.uuid4()
        mock_request = _create_mock_request(user_id=user_id)
        mock_valkey = AsyncMock()
        mock_valkey.set = AsyncMock(return_value=True)

        item = _create_mock_hitl_item(item_id=hitl_id)
        db_session = _create_mock_db_session(item=item)

        await HITLController.acquire_viewing_lock.fn(
            self=None,
            hitl_id=hitl_id,
            request=mock_request,
            db_session=db_session,
            valkey=mock_valkey,
        )

        key_arg = mock_valkey.set.call_args[0][0]
        assert key_arg == f"mas:hitl_lock:{hitl_id}"

    @pytest.mark.asyncio
    async def test_acquire_lock_ttl_is_300_seconds(self) -> None:
        """Should set TTL to 300 seconds (5 minutes)."""
        hitl_id = uuid.uuid4()
        mock_request = _create_mock_request()
        mock_valkey = AsyncMock()
        mock_valkey.set = AsyncMock(return_value=True)

        item = _create_mock_hitl_item(item_id=hitl_id)
        db_session = _create_mock_db_session(item=item)

        await HITLController.acquire_viewing_lock.fn(
            self=None,
            hitl_id=hitl_id,
            request=mock_request,
            db_session=db_session,
            valkey=mock_valkey,
        )

        call_kwargs = mock_valkey.set.call_args
        assert call_kwargs[1]["ex"] == 300


# ---------------------------------------------------------------------------
# DELETE /api/v1/hitl/{hitl_id}/viewing — release lock
# ---------------------------------------------------------------------------


class TestReleaseViewingLock:
    """Tests for releasing a HITL viewing lock."""

    @pytest.mark.asyncio
    async def test_release_lock_success(self) -> None:
        """Should release lock when current user is the owner."""
        hitl_id = uuid.uuid4()
        user_id = uuid.uuid4()
        mock_request = _create_mock_request(user_id=user_id)
        mock_valkey = AsyncMock()
        mock_valkey.get = AsyncMock(return_value=str(user_id).encode())
        mock_valkey.delete = AsyncMock(return_value=1)

        result = await HITLController.release_viewing_lock.fn(
            self=None,
            hitl_id=hitl_id,
            request=mock_request,
            valkey=mock_valkey,
        )

        assert isinstance(result, HITLViewingLockResponseSchema)
        assert result.locked is False
        assert result.locked_by is None
        mock_valkey.delete.assert_called_once_with(f"mas:hitl_lock:{hitl_id}")

    @pytest.mark.asyncio
    async def test_release_lock_not_owner(self) -> None:
        """Should NOT release lock when another user holds it."""
        from src.core.exceptions import MASException

        hitl_id = uuid.uuid4()
        user_id = uuid.uuid4()
        other_user_id = uuid.uuid4()
        mock_request = _create_mock_request(user_id=user_id)
        mock_valkey = AsyncMock()
        mock_valkey.get = AsyncMock(return_value=str(other_user_id).encode())

        with pytest.raises(MASException, match="held by another"):
            await HITLController.release_viewing_lock.fn(
                self=None,
                hitl_id=hitl_id,
                request=mock_request,
                valkey=mock_valkey,
            )

        # Should NOT delete the key
        mock_valkey.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_release_lock_already_released(self) -> None:
        """Should succeed silently when lock is not held (idempotent)."""
        hitl_id = uuid.uuid4()
        mock_request = _create_mock_request()
        mock_valkey = AsyncMock()
        mock_valkey.get = AsyncMock(return_value=None)  # No lock exists

        result = await HITLController.release_viewing_lock.fn(
            self=None,
            hitl_id=hitl_id,
            request=mock_request,
            valkey=mock_valkey,
        )

        assert result.locked is False
        assert result.locked_by is None


# ---------------------------------------------------------------------------
# GET /api/v1/hitl/{hitl_id} — detail with locked_by field
# ---------------------------------------------------------------------------


class TestGetHITLDetailWithLock:
    """Tests that GET detail endpoint returns locked_by field."""

    @pytest.mark.asyncio
    async def test_detail_shows_locked_by_when_locked(self) -> None:
        """Should include locked_by user_id when a lock exists."""
        hitl_id = uuid.uuid4()
        locker_id = uuid.uuid4()

        item = _create_mock_hitl_item(item_id=hitl_id)
        db_session = _create_mock_db_session(item=item)

        mock_valkey = AsyncMock()
        mock_valkey.get = AsyncMock(return_value=str(locker_id).encode())

        result = await HITLController.get_detail.fn(
            self=None,
            hitl_id=hitl_id,
            db_session=db_session,
            valkey=mock_valkey,
        )

        assert result.locked_by == str(locker_id)
        mock_valkey.get.assert_called_once_with(f"mas:hitl_lock:{hitl_id}")

    @pytest.mark.asyncio
    async def test_detail_shows_locked_by_none_when_unlocked(self) -> None:
        """Should return locked_by=None when no lock exists."""
        hitl_id = uuid.uuid4()

        item = _create_mock_hitl_item(item_id=hitl_id)
        db_session = _create_mock_db_session(item=item)

        mock_valkey = AsyncMock()
        mock_valkey.get = AsyncMock(return_value=None)

        result = await HITLController.get_detail.fn(
            self=None,
            hitl_id=hitl_id,
            db_session=db_session,
            valkey=mock_valkey,
        )

        assert result.locked_by is None

    @pytest.mark.asyncio
    async def test_detail_not_found(self) -> None:
        """Should raise NotFoundException for unknown HITL id."""
        from litestar.exceptions import NotFoundException

        hitl_id = uuid.uuid4()
        db_session = _create_mock_db_session(item=None)
        mock_valkey = AsyncMock()

        with pytest.raises(NotFoundException):
            await HITLController.get_detail.fn(
                self=None,
                hitl_id=hitl_id,
                db_session=db_session,
                valkey=mock_valkey,
            )

    @pytest.mark.asyncio
    async def test_detail_returns_all_hitl_fields(self) -> None:
        """Should return standard HITL fields alongside locked_by."""
        hitl_id = uuid.uuid4()
        item = _create_mock_hitl_item(item_id=hitl_id, item_type="code_review")
        db_session = _create_mock_db_session(item=item)

        mock_valkey = AsyncMock()
        mock_valkey.get = AsyncMock(return_value=None)

        result = await HITLController.get_detail.fn(
            self=None,
            hitl_id=hitl_id,
            db_session=db_session,
            valkey=mock_valkey,
        )

        assert result.id == hitl_id
        assert result.type == "code_review"
        assert result.priority == "normal"
        assert result.title == "Test code_review"
        assert result.locked_by is None


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestViewingLockEdgeCases:
    """Edge-case tests for the viewing lock system."""

    @pytest.mark.asyncio
    async def test_valkey_error_on_acquire_is_not_fatal(self) -> None:
        """Should return locked=False gracefully when Valkey is unreachable."""
        hitl_id = uuid.uuid4()
        mock_request = _create_mock_request()
        mock_valkey = AsyncMock()
        mock_valkey.set = AsyncMock(side_effect=ConnectionError("Valkey down"))

        item = _create_mock_hitl_item(item_id=hitl_id)
        db_session = _create_mock_db_session(item=item)

        result = await HITLController.acquire_viewing_lock.fn(
            self=None,
            hitl_id=hitl_id,
            request=mock_request,
            db_session=db_session,
            valkey=mock_valkey,
        )

        # Should degrade gracefully, not crash
        assert result.locked is False
        assert result.locked_by is None

    @pytest.mark.asyncio
    async def test_valkey_error_on_release_is_not_fatal(self) -> None:
        """Should return locked=False gracefully when Valkey fails during release."""
        hitl_id = uuid.uuid4()
        mock_request = _create_mock_request()
        mock_valkey = AsyncMock()
        mock_valkey.get = AsyncMock(side_effect=ConnectionError("Valkey down"))

        result = await HITLController.release_viewing_lock.fn(
            self=None,
            hitl_id=hitl_id,
            request=mock_request,
            valkey=mock_valkey,
        )

        assert result.locked is False
        assert result.locked_by is None

    @pytest.mark.asyncio
    async def test_valkey_error_on_detail_returns_none_locked_by(self) -> None:
        """Should return locked_by=None when Valkey fails during detail fetch."""
        hitl_id = uuid.uuid4()
        item = _create_mock_hitl_item(item_id=hitl_id)
        db_session = _create_mock_db_session(item=item)

        mock_valkey = AsyncMock()
        mock_valkey.get = AsyncMock(side_effect=ConnectionError("Valkey down"))

        result = await HITLController.get_detail.fn(
            self=None,
            hitl_id=hitl_id,
            db_session=db_session,
            valkey=mock_valkey,
        )

        assert result.locked_by is None
