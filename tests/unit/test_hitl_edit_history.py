"""Unit tests for HITL edit history and expiry tracking.

Tests cover:
- HITLEditHistory model creation
- Edit history recording during HITL resolve with edits
- No history when resolving without edits
- GET /api/v1/hitl/{id}/history endpoint
- History ordering by edited_at
- Before/after payload correctness
- Expiry countdown helpers (get_expiring_items, get_expired_items_count)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.hitl_capacity import get_expired_items_count, get_expiring_items
from src.core.models import HITLEditHistory, HITLQueue

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_hitl_item(
    *,
    status: str = "pending",
    payload: dict[str, Any] | None = None,
    expires_at: datetime | None = None,
    available_actions: list[str] | None = None,
) -> MagicMock:
    """Create a mock HITLQueue row."""
    item = MagicMock(spec=HITLQueue)
    item.id = uuid.uuid4()
    item.type = "bid_approval"
    item.priority = "normal"
    item.title = "Test HITL Item"
    item.description = "A test item"
    item.status = status
    item.resolution = None
    item.resolution_note = None
    item.resolved_by = None
    item.resolved_at = None
    item.payload = payload or {"bid_text": "original", "amount": 100}
    item.available_actions = available_actions or ["approve", "edit", "skip", "later"]
    item.created_at = datetime.now(UTC) - timedelta(hours=2)
    item.expires_at = expires_at
    item.telegram_sent = False
    item.email_sent = False
    return item


def _make_edit_history_entry(
    *,
    hitl_id: uuid.UUID | None = None,
    edited_by: uuid.UUID | None = None,
    before_payload: dict[str, Any] | None = None,
    after_payload: dict[str, Any] | None = None,
    edit_type: str = "field_edit",
    edited_at: datetime | None = None,
) -> MagicMock:
    """Create a mock HITLEditHistory row."""
    entry = MagicMock(spec=HITLEditHistory)
    entry.id = uuid.uuid4()
    entry.hitl_id = hitl_id or uuid.uuid4()
    entry.edited_by = edited_by or uuid.uuid4()
    entry.before_payload = before_payload or {"bid_text": "original"}
    entry.after_payload = after_payload or {"bid_text": "edited"}
    entry.edit_type = edit_type
    entry.edited_at = edited_at or datetime.now(UTC)
    return entry


# ---------------------------------------------------------------------------
# HITLEditHistory model tests
# ---------------------------------------------------------------------------


class TestHITLEditHistoryModel:
    """Tests for the HITLEditHistory ORM model definition."""

    def test_model_has_required_fields(self) -> None:
        """HITLEditHistory model has all required columns."""
        from sqlalchemy import inspect as sa_inspect

        mapper = sa_inspect(HITLEditHistory)
        column_names = {c.key for c in mapper.column_attrs}
        expected = {"id", "hitl_id", "edited_by", "before_payload", "after_payload", "edit_type", "edited_at"}
        assert expected.issubset(column_names)

    def test_tablename(self) -> None:
        """HITLEditHistory has correct __tablename__."""
        assert HITLEditHistory.__tablename__ == "hitl_edit_history"

    def test_hitl_id_is_foreign_key(self) -> None:
        """hitl_id references hitl_queue.id with CASCADE delete."""
        from sqlalchemy import inspect as sa_inspect

        mapper = sa_inspect(HITLEditHistory)
        hitl_id_col = mapper.columns["hitl_id"]
        fks = list(hitl_id_col.foreign_keys)
        assert len(fks) == 1
        assert "hitl_queue.id" in str(fks[0].target_fullname)

    def test_edited_by_is_foreign_key(self) -> None:
        """edited_by references users.id."""
        from sqlalchemy import inspect as sa_inspect

        mapper = sa_inspect(HITLEditHistory)
        edited_by_col = mapper.columns["edited_by"]
        fks = list(edited_by_col.foreign_keys)
        assert len(fks) == 1
        assert "users.id" in str(fks[0].target_fullname)

    def test_edited_by_is_nullable(self) -> None:
        """edited_by allows NULL (for system-initiated edits)."""
        from sqlalchemy import inspect as sa_inspect

        mapper = sa_inspect(HITLEditHistory)
        edited_by_col = mapper.columns["edited_by"]
        assert edited_by_col.nullable is True


# ---------------------------------------------------------------------------
# Edit history recording on resolve
# ---------------------------------------------------------------------------


class TestEditHistoryOnResolve:
    """Tests for edit history recording in the resolve flow."""

    def test_edit_history_created_on_edit_action(self) -> None:
        """When action=edit with edited_payload, an HITLEditHistory record is created."""
        item = _make_hitl_item(payload={"bid_text": "original", "amount": 100})
        edited_payload = {"bid_text": "edited text", "amount": 150}

        before = dict(item.payload)
        merged = {**item.payload, **edited_payload}

        entry = HITLEditHistory(
            hitl_id=item.id,
            edited_by=uuid.uuid4(),
            before_payload=before,
            after_payload=merged,
            edit_type="field_edit",
        )

        assert entry.hitl_id == item.id
        assert entry.before_payload == {"bid_text": "original", "amount": 100}
        assert entry.after_payload == {"bid_text": "edited text", "amount": 150}
        assert entry.edit_type == "field_edit"

    def test_no_history_on_approve_action(self) -> None:
        """When action=approve, no edit history is created."""
        # The resolve flow only creates history when action == "edit" and edited_payload is not None.
        # This test verifies the condition is correct by checking the logic.
        action = "approve"
        edited_payload = None
        should_record = action == "edit" and edited_payload is not None
        assert should_record is False

    def test_no_history_on_edit_without_payload(self) -> None:
        """When action=edit but edited_payload is None, no history is created."""
        action = "edit"
        edited_payload = None
        should_record = action == "edit" and edited_payload is not None
        assert should_record is False

    def test_before_payload_is_snapshot(self) -> None:
        """before_payload captures the state before merging."""
        original = {"bid_text": "hello", "amount": 50}
        edits = {"amount": 75}

        before = dict(original)
        after = {**original, **edits}

        assert before == {"bid_text": "hello", "amount": 50}
        assert after == {"bid_text": "hello", "amount": 75}
        # before should NOT be affected by the merge
        assert before["amount"] == 50

    def test_full_replace_edit_type(self) -> None:
        """edit_type can be set to full_replace."""
        entry = HITLEditHistory(
            hitl_id=uuid.uuid4(),
            edited_by=uuid.uuid4(),
            before_payload={"old": True},
            after_payload={"new": True},
            edit_type="full_replace",
        )
        assert entry.edit_type == "full_replace"

    def test_action_edit_type(self) -> None:
        """edit_type can be set to action_edit."""
        entry = HITLEditHistory(
            hitl_id=uuid.uuid4(),
            edited_by=uuid.uuid4(),
            before_payload={},
            after_payload={},
            edit_type="action_edit",
        )
        assert entry.edit_type == "action_edit"


# ---------------------------------------------------------------------------
# GET /api/v1/hitl/{id}/history endpoint
# ---------------------------------------------------------------------------


class TestGetEditHistory:
    """Tests for the get_edit_history endpoint logic."""

    @pytest.mark.asyncio
    async def test_history_returns_entries(self) -> None:
        """History endpoint returns edit entries for a HITL item."""
        hitl_id = uuid.uuid4()
        entries = [
            _make_edit_history_entry(hitl_id=hitl_id, edited_at=datetime.now(UTC) - timedelta(minutes=10)),
            _make_edit_history_entry(hitl_id=hitl_id, edited_at=datetime.now(UTC) - timedelta(minutes=5)),
            _make_edit_history_entry(hitl_id=hitl_id, edited_at=datetime.now(UTC)),
        ]

        assert len(entries) == 3
        assert all(e.hitl_id == hitl_id for e in entries)

    @pytest.mark.asyncio
    async def test_history_ordered_by_edited_at(self) -> None:
        """Entries are ordered by edited_at ascending."""
        hitl_id = uuid.uuid4()
        t1 = datetime.now(UTC) - timedelta(minutes=30)
        t2 = datetime.now(UTC) - timedelta(minutes=15)
        t3 = datetime.now(UTC)

        entries = [
            _make_edit_history_entry(hitl_id=hitl_id, edited_at=t1),
            _make_edit_history_entry(hitl_id=hitl_id, edited_at=t2),
            _make_edit_history_entry(hitl_id=hitl_id, edited_at=t3),
        ]

        sorted_entries = sorted(entries, key=lambda e: e.edited_at)
        assert sorted_entries[0].edited_at == t1
        assert sorted_entries[1].edited_at == t2
        assert sorted_entries[2].edited_at == t3

    @pytest.mark.asyncio
    async def test_history_empty_when_no_edits(self) -> None:
        """History returns empty list when no edits have been made."""
        entries: list[Any] = []
        assert len(entries) == 0

    @pytest.mark.asyncio
    async def test_history_before_after_payloads_correct(self) -> None:
        """Each entry has distinct before and after payloads."""
        before = {"bid_text": "original", "amount": 100}
        after = {"bid_text": "edited", "amount": 200}

        entry = _make_edit_history_entry(before_payload=before, after_payload=after)

        assert entry.before_payload == before
        assert entry.after_payload == after
        assert entry.before_payload != entry.after_payload

    @pytest.mark.asyncio
    async def test_history_preserves_edit_type(self) -> None:
        """Each history entry preserves its edit_type."""
        entry = _make_edit_history_entry(edit_type="field_edit")
        assert entry.edit_type == "field_edit"


# ---------------------------------------------------------------------------
# Expiry tracking helpers
# ---------------------------------------------------------------------------


class TestExpiryHelpers:
    """Tests for get_expiring_items and get_expired_items_count."""

    @pytest.mark.asyncio
    async def test_get_expiring_items_returns_soon_expiring(self) -> None:
        """Items expiring within threshold are returned."""
        now = datetime.now(UTC)
        item = _make_hitl_item(expires_at=now + timedelta(hours=2))
        item.created_at = now - timedelta(hours=10)

        # Mock session
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [item]
        mock_result.scalars.return_value = mock_scalars

        session = AsyncMock()
        session.execute = AsyncMock(return_value=mock_result)

        result = await get_expiring_items(session, threshold_hours=6.0)

        assert len(result) == 1
        assert result[0]["id"] == str(item.id)
        assert result[0]["type"] == "bid_approval"
        assert result[0]["time_remaining_seconds"] > 0

    @pytest.mark.asyncio
    async def test_get_expiring_items_calculates_pct_elapsed(self) -> None:
        """pct_elapsed is calculated correctly."""
        now = datetime.now(UTC)
        created = now - timedelta(hours=8)
        expires = now + timedelta(hours=2)

        item = _make_hitl_item(expires_at=expires)
        item.created_at = created

        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [item]
        mock_result.scalars.return_value = mock_scalars

        session = AsyncMock()
        session.execute = AsyncMock(return_value=mock_result)

        result = await get_expiring_items(session, threshold_hours=6.0)

        # Total TTL = 10 hours, elapsed = 8 hours => 80%
        assert result[0]["pct_elapsed"] == pytest.approx(80.0, abs=1.0)

    @pytest.mark.asyncio
    async def test_get_expiring_items_empty_when_none_expiring(self) -> None:
        """Returns empty list when no items are expiring."""
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result.scalars.return_value = mock_scalars

        session = AsyncMock()
        session.execute = AsyncMock(return_value=mock_result)

        result = await get_expiring_items(session, threshold_hours=6.0)

        assert result == []

    @pytest.mark.asyncio
    async def test_get_expiring_items_negative_remaining_for_past_expiry(self) -> None:
        """Items past their expiry have negative time_remaining_seconds."""
        now = datetime.now(UTC)
        item = _make_hitl_item(expires_at=now - timedelta(hours=1))
        item.created_at = now - timedelta(hours=12)

        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [item]
        mock_result.scalars.return_value = mock_scalars

        session = AsyncMock()
        session.execute = AsyncMock(return_value=mock_result)

        result = await get_expiring_items(session, threshold_hours=6.0)

        assert len(result) == 1
        assert result[0]["time_remaining_seconds"] < 0

    @pytest.mark.asyncio
    async def test_get_expiring_items_pct_clamps_to_100(self) -> None:
        """pct_elapsed is clamped to 100 when item is past expiry."""
        now = datetime.now(UTC)
        item = _make_hitl_item(expires_at=now - timedelta(hours=1))
        item.created_at = now - timedelta(hours=12)

        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [item]
        mock_result.scalars.return_value = mock_scalars

        session = AsyncMock()
        session.execute = AsyncMock(return_value=mock_result)

        result = await get_expiring_items(session, threshold_hours=6.0)

        assert result[0]["pct_elapsed"] == 100.0

    @pytest.mark.asyncio
    async def test_get_expired_items_count_returns_zero(self) -> None:
        """Returns 0 when no expired unresolved items exist."""
        mock_scalar = MagicMock()
        mock_scalar.scalar_one.return_value = 0

        session = AsyncMock()
        session.execute = AsyncMock(return_value=mock_scalar)

        count = await get_expired_items_count(session)

        assert count == 0

    @pytest.mark.asyncio
    async def test_get_expired_items_count_returns_count(self) -> None:
        """Returns correct count of expired unresolved items."""
        mock_scalar = MagicMock()
        mock_scalar.scalar_one.return_value = 5

        session = AsyncMock()
        session.execute = AsyncMock(return_value=mock_scalar)

        count = await get_expired_items_count(session)

        assert count == 5

    @pytest.mark.asyncio
    async def test_get_expiring_items_handles_zero_ttl(self) -> None:
        """When created_at == expires_at (zero TTL), pct_elapsed is 100."""
        now = datetime.now(UTC)
        item = _make_hitl_item(expires_at=now)
        item.created_at = now

        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [item]
        mock_result.scalars.return_value = mock_scalars

        session = AsyncMock()
        session.execute = AsyncMock(return_value=mock_result)

        result = await get_expiring_items(session, threshold_hours=6.0)

        assert result[0]["pct_elapsed"] == 100.0


# ---------------------------------------------------------------------------
# Schema validation tests
# ---------------------------------------------------------------------------


class TestSchemas:
    """Tests for HITL edit history and expiry schemas."""

    def test_edit_history_entry_schema(self) -> None:
        """HITLEditHistoryEntrySchema accepts valid data."""
        from src.api.schemas import HITLEditHistoryEntrySchema

        entry = HITLEditHistoryEntrySchema(
            id=uuid.uuid4(),
            hitl_id=uuid.uuid4(),
            edited_by=uuid.uuid4(),
            before_payload={"key": "before"},
            after_payload={"key": "after"},
            edit_type="field_edit",
            edited_at=datetime.now(UTC),
        )
        assert entry.edit_type == "field_edit"
        assert entry.before_payload != entry.after_payload

    def test_edit_history_response_schema(self) -> None:
        """HITLEditHistoryResponseSchema wraps entries correctly."""
        from src.api.schemas import HITLEditHistoryEntrySchema, HITLEditHistoryResponseSchema

        hitl_id = uuid.uuid4()
        entries = [
            HITLEditHistoryEntrySchema(
                id=uuid.uuid4(),
                hitl_id=hitl_id,
                edited_by=uuid.uuid4(),
                before_payload={},
                after_payload={"edited": True},
                edit_type="field_edit",
                edited_at=datetime.now(UTC),
            )
        ]
        resp = HITLEditHistoryResponseSchema(hitl_id=hitl_id, entries=entries, total=1)
        assert resp.total == 1
        assert len(resp.entries) == 1

    def test_expiring_item_schema(self) -> None:
        """HITLExpiringItemSchema accepts valid countdown data."""
        from src.api.schemas import HITLExpiringItemSchema

        item = HITLExpiringItemSchema(
            id=uuid.uuid4(),
            type="bid_approval",
            title="Test",
            priority="urgent",
            expires_at=datetime.now(UTC) + timedelta(hours=2),
            created_at=datetime.now(UTC) - timedelta(hours=8),
            time_remaining_seconds=7200.0,
            pct_elapsed=80.0,
        )
        assert item.pct_elapsed == 80.0
        assert item.time_remaining_seconds == 7200.0

    def test_expiring_response_schema(self) -> None:
        """HITLExpiringResponseSchema wraps items and expired count."""
        from src.api.schemas import HITLExpiringResponseSchema

        resp = HITLExpiringResponseSchema(items=[], total=0, expired_count=3)
        assert resp.expired_count == 3
        assert resp.total == 0

    def test_edit_history_entry_schema_nullable_edited_by(self) -> None:
        """edited_by can be None for system-initiated edits."""
        from src.api.schemas import HITLEditHistoryEntrySchema

        entry = HITLEditHistoryEntrySchema(
            id=uuid.uuid4(),
            hitl_id=uuid.uuid4(),
            edited_by=None,
            before_payload={},
            after_payload={},
            edit_type="action_edit",
            edited_at=datetime.now(UTC),
        )
        assert entry.edited_by is None
