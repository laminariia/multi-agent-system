"""Unit tests for the HITL bulk-resolve endpoint.

Tests the ``HITLController.bulk_resolve`` route handler
(``POST /api/v1/hitl/bulk-resolve``) with mocked database dependencies.
We test handler functions directly via ``.fn()`` to avoid needing a full
app instance.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.routes.hitl import HITLController
from src.api.schemas import HITLBulkResolveRequestSchema, HITLBulkResolveResponseSchema

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _create_mock_hitl_item(
    item_id: uuid.UUID | None = None,
    item_type: str = "bid_approval",
    priority: str = "normal",
    status: str = "pending",
    resolution: str | None = None,
    expires_at: datetime | None = None,
    payload: dict | None = None,
    available_actions: list[str] | None = None,
) -> MagicMock:
    """Create a mock HITLQueue item."""
    if item_id is None:
        item_id = uuid.uuid4()
    if expires_at is None:
        expires_at = datetime.now(UTC) + timedelta(hours=1)
    if payload is None:
        payload = {"bid_text": "Hello"}
    if available_actions is None:
        available_actions = ["approve", "reject", "edit", "skip", "later"]

    mock_item = MagicMock()
    mock_item.id = item_id
    mock_item.type = item_type
    mock_item.priority = priority
    mock_item.title = f"Test {item_type}"
    mock_item.description = "Test description"
    mock_item.expires_at = expires_at
    mock_item.payload = payload
    mock_item.available_actions = available_actions
    mock_item.created_at = datetime.now(UTC)
    mock_item.status = status
    mock_item.resolution = resolution
    mock_item.resolution_note = None
    mock_item.resolved_by = None
    mock_item.resolved_at = None
    return mock_item


def _create_mock_db_session() -> AsyncMock:
    """Create a mock AsyncSession for database testing."""
    session = AsyncMock()
    return session


def _create_mock_request(user_id: uuid.UUID | None = None) -> MagicMock:
    """Create a mock Request with a user."""
    if user_id is None:
        user_id = uuid.uuid4()

    mock_request = MagicMock()
    mock_request.user = MagicMock()
    mock_request.user.id = user_id
    mock_request.user.email = "test@example.com"
    return mock_request


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBulkResolveSuccess:
    """Tests for successful bulk resolution of multiple HITL items."""

    @pytest.mark.asyncio
    async def test_bulk_resolve_success_all_pending(self) -> None:
        """Should resolve all 3 pending items with resolved=3, failed=0."""
        db_session = _create_mock_db_session()
        mock_request = _create_mock_request()

        id1, id2, id3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        item1 = _create_mock_hitl_item(item_id=id1)
        item2 = _create_mock_hitl_item(item_id=id2)
        item3 = _create_mock_hitl_item(item_id=id3)

        # Mock the single-query fetch: returns all 3 items
        query_result = MagicMock()
        query_result.scalars.return_value.all.return_value = [item1, item2, item3]
        db_session.execute.return_value = query_result

        data = HITLBulkResolveRequestSchema(
            ids=[id1, id2, id3],
            action="approve",
        )

        result = await HITLController.bulk_resolve.fn(
            self=None,
            data=data,
            request=mock_request,
            db_session=db_session,
            valkey=AsyncMock(),
            channels=AsyncMock(),
        )

        assert isinstance(result, HITLBulkResolveResponseSchema)
        assert result.resolved == 3
        assert result.failed == 0
        assert result.errors == []

        # Verify each item's status was updated
        for item in (item1, item2, item3):
            assert item.status == "resolved"
            assert item.resolution == "approve"
            assert item.resolved_by == mock_request.user.id
            assert item.resolved_at is not None


class TestBulkResolvePartialFailure:
    """Tests where some items fail during bulk resolution."""

    @pytest.mark.asyncio
    async def test_bulk_resolve_partial_failure_already_resolved(self) -> None:
        """Should resolve pending item, skip already-resolved item."""
        db_session = _create_mock_db_session()
        mock_request = _create_mock_request()

        id1 = uuid.uuid4()
        id2 = uuid.uuid4()
        item1 = _create_mock_hitl_item(item_id=id1, status="pending")
        item2 = _create_mock_hitl_item(item_id=id2, status="resolved", resolution="approve")

        query_result = MagicMock()
        query_result.scalars.return_value.all.return_value = [item1, item2]
        db_session.execute.return_value = query_result

        data = HITLBulkResolveRequestSchema(
            ids=[id1, id2],
            action="approve",
        )

        result = await HITLController.bulk_resolve.fn(
            self=None,
            data=data,
            request=mock_request,
            db_session=db_session,
            valkey=AsyncMock(),
            channels=AsyncMock(),
        )

        assert result.resolved == 1
        assert result.failed == 1
        assert len(result.errors) == 1
        assert result.errors[0]["id"] == str(id2)
        assert "Already resolved" in result.errors[0]["error"]


class TestBulkResolveExpired:
    """Tests for expired items in bulk resolution."""

    @pytest.mark.asyncio
    async def test_bulk_resolve_expired_item(self) -> None:
        """Should fail for expired item and mark its status as expired."""
        db_session = _create_mock_db_session()
        mock_request = _create_mock_request()

        item_id = uuid.uuid4()
        expired_time = datetime.now(UTC) - timedelta(hours=1)
        item = _create_mock_hitl_item(item_id=item_id, expires_at=expired_time)

        query_result = MagicMock()
        query_result.scalars.return_value.all.return_value = [item]
        db_session.execute.return_value = query_result

        data = HITLBulkResolveRequestSchema(
            ids=[item_id],
            action="approve",
        )

        result = await HITLController.bulk_resolve.fn(
            self=None,
            data=data,
            request=mock_request,
            db_session=db_session,
            valkey=AsyncMock(),
            channels=AsyncMock(),
        )

        assert result.resolved == 0
        assert result.failed == 1
        assert len(result.errors) == 1
        assert "Expired" in result.errors[0]["error"]
        # Item status should be set to "expired"
        assert item.status == "expired"


class TestBulkResolveInvalidAction:
    """Tests for invalid actions in bulk resolution."""

    @pytest.mark.asyncio
    async def test_bulk_resolve_invalid_action(self) -> None:
        """Should fail when action is not in available_actions."""
        db_session = _create_mock_db_session()
        mock_request = _create_mock_request()

        item_id = uuid.uuid4()
        item = _create_mock_hitl_item(
            item_id=item_id,
            available_actions=["approve"],
        )

        query_result = MagicMock()
        query_result.scalars.return_value.all.return_value = [item]
        db_session.execute.return_value = query_result

        data = HITLBulkResolveRequestSchema(
            ids=[item_id],
            action="reject",
        )

        result = await HITLController.bulk_resolve.fn(
            self=None,
            data=data,
            request=mock_request,
            db_session=db_session,
            valkey=AsyncMock(),
            channels=AsyncMock(),
        )

        assert result.resolved == 0
        assert result.failed == 1
        assert len(result.errors) == 1
        assert "not available" in result.errors[0]["error"]


class TestBulkResolveNotFound:
    """Tests for missing items in bulk resolution."""

    @pytest.mark.asyncio
    async def test_bulk_resolve_not_found(self) -> None:
        """Should report failure for IDs not found in DB."""
        db_session = _create_mock_db_session()
        mock_request = _create_mock_request()

        existing_id = uuid.uuid4()
        missing_id = uuid.uuid4()
        item = _create_mock_hitl_item(item_id=existing_id)

        # DB only returns the existing item
        query_result = MagicMock()
        query_result.scalars.return_value.all.return_value = [item]
        db_session.execute.return_value = query_result

        data = HITLBulkResolveRequestSchema(
            ids=[existing_id, missing_id],
            action="approve",
        )

        result = await HITLController.bulk_resolve.fn(
            self=None,
            data=data,
            request=mock_request,
            db_session=db_session,
            valkey=AsyncMock(),
            channels=AsyncMock(),
        )

        assert result.resolved == 1
        assert result.failed == 1
        assert len(result.errors) == 1
        assert result.errors[0]["id"] == str(missing_id)
        assert "Not found" in result.errors[0]["error"]

        # The existing item should be resolved
        assert item.status == "resolved"


class TestBulkResolveResumeTasks:
    """Tests that bulk resolve fires resume tasks for resumable items."""

    @pytest.mark.asyncio
    async def test_bulk_resolve_fires_resume_tasks(self) -> None:
        """Should create asyncio tasks for each resumable approved item."""
        db_session = _create_mock_db_session()
        mock_request = _create_mock_request()

        id1 = uuid.uuid4()
        id2 = uuid.uuid4()

        item1 = _create_mock_hitl_item(
            item_id=id1,
            item_type="bid_approval",
            payload={"thread_id": "t-111", "bid_id": "b-111"},
        )
        item2 = _create_mock_hitl_item(
            item_id=id2,
            item_type="bid_approval",
            payload={"thread_id": "t-222", "bid_id": "b-222"},
        )

        query_result = MagicMock()
        query_result.scalars.return_value.all.return_value = [item1, item2]
        db_session.execute.return_value = query_result

        data = HITLBulkResolveRequestSchema(
            ids=[id1, id2],
            action="approve",
        )

        mock_resume = AsyncMock()
        mock_task = MagicMock()

        with (
            patch("src.core.graph.resume_from_hitl", mock_resume),
            patch("asyncio.create_task", return_value=mock_task) as mock_create_task,
        ):
            result = await HITLController.bulk_resolve.fn(
                self=None,
                data=data,
                request=mock_request,
                db_session=db_session,
                valkey=AsyncMock(),
                channels=AsyncMock(),
            )

        assert result.resolved == 2
        # asyncio.create_task should have been called once per resumable item
        assert mock_create_task.call_count == 2


class TestBulkResolvePublishEvents:
    """Tests that bulk resolve publishes WebSocket and Valkey events."""

    @pytest.mark.asyncio
    async def test_bulk_resolve_publishes_events(self) -> None:
        """Should publish WebSocket event and Valkey notification for resolved items."""
        db_session = _create_mock_db_session()
        mock_request = _create_mock_request()
        mock_valkey = AsyncMock()
        mock_channels = AsyncMock()

        id1 = uuid.uuid4()
        item1 = _create_mock_hitl_item(item_id=id1)

        query_result = MagicMock()
        query_result.scalars.return_value.all.return_value = [item1]
        db_session.execute.return_value = query_result

        data = HITLBulkResolveRequestSchema(
            ids=[id1],
            action="approve",
        )

        with patch("src.api.routes.hitl.publish_event", new_callable=AsyncMock) as mock_publish:
            result = await HITLController.bulk_resolve.fn(
                self=None,
                data=data,
                request=mock_request,
                db_session=db_session,
                valkey=mock_valkey,
                channels=mock_channels,
            )

        assert result.resolved == 1

        # WebSocket publish_event should be called once with hitl:bulk_resolved
        mock_publish.assert_called_once()
        call_args = mock_publish.call_args
        assert call_args[0][1] == "hitl:resolved"  # CHANNEL_HITL_RESOLVED
        event_data = call_args[0][2]
        assert event_data["type"] == "hitl:bulk_resolved"
        assert event_data["data"]["resolved_count"] == 1
        assert event_data["data"]["action"] == "approve"

        # Valkey publish should be called once for the bot notification
        mock_valkey.publish.assert_called_once()
        valkey_args = mock_valkey.publish.call_args
        assert valkey_args[0][0] == "hitl:resolved:bot"

    @pytest.mark.asyncio
    async def test_bulk_resolve_no_events_when_all_failed(self) -> None:
        """Should NOT publish events when no items were resolved."""
        db_session = _create_mock_db_session()
        mock_request = _create_mock_request()
        mock_valkey = AsyncMock()
        mock_channels = AsyncMock()

        # All items already resolved
        id1 = uuid.uuid4()
        item1 = _create_mock_hitl_item(item_id=id1, status="resolved")

        query_result = MagicMock()
        query_result.scalars.return_value.all.return_value = [item1]
        db_session.execute.return_value = query_result

        data = HITLBulkResolveRequestSchema(
            ids=[id1],
            action="approve",
        )

        with patch("src.api.routes.hitl.publish_event", new_callable=AsyncMock) as mock_publish:
            result = await HITLController.bulk_resolve.fn(
                self=None,
                data=data,
                request=mock_request,
                db_session=db_session,
                valkey=mock_valkey,
                channels=mock_channels,
            )

        assert result.resolved == 0
        assert result.failed == 1

        # No events should be published when nothing was resolved
        mock_publish.assert_not_called()
        mock_valkey.publish.assert_not_called()
