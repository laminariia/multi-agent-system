"""Unit tests for Negotiation Engine (src/negotiations/engine.py).

Tests cover: create negotiation, get negotiation, transition,
get active, mark stale, edge cases.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.negotiations.engine import NegotiationEngine
from src.negotiations.models import Negotiation
from src.negotiations.state_machine import InvalidTransitionError

# ---------------------------------------------------------------------------
# Create negotiation
# ---------------------------------------------------------------------------


class TestCreateNegotiation:
    """Create a new negotiation from a bid."""

    def test_creates_with_defaults(self):
        engine = NegotiationEngine()
        neg = engine.create_negotiation(bid_id="bid-123", platform="freelancer")

        assert isinstance(neg, Negotiation)
        assert neg.bid_id == "bid-123"
        assert neg.platform == "freelancer"
        assert neg.state == "initial"
        assert neg.messages == []
        assert neg.id is not None

    def test_creates_with_custom_initial_state(self):
        engine = NegotiationEngine()
        neg = engine.create_negotiation(
            bid_id="bid-456",
            platform="fl_ru",
            initial_state="qualifying",
        )

        assert neg.state == "qualifying"

    def test_assigns_unique_ids(self):
        engine = NegotiationEngine()
        n1 = engine.create_negotiation(bid_id="bid-1", platform="kwork")
        n2 = engine.create_negotiation(bid_id="bid-2", platform="kwork")

        assert n1.id != n2.id

    def test_sets_created_at(self):
        engine = NegotiationEngine()
        before = datetime.now(UTC)
        neg = engine.create_negotiation(bid_id="bid-1", platform="freelancer")
        after = datetime.now(UTC)

        assert before <= neg.created_at <= after

    def test_sets_updated_at_equal_to_created_at(self):
        engine = NegotiationEngine()
        neg = engine.create_negotiation(bid_id="bid-1", platform="freelancer")

        assert neg.updated_at == neg.created_at


# ---------------------------------------------------------------------------
# Get negotiation
# ---------------------------------------------------------------------------


class TestGetNegotiation:
    """Retrieve a negotiation by ID."""

    def test_returns_existing(self):
        engine = NegotiationEngine()
        neg = engine.create_negotiation(bid_id="bid-1", platform="freelancer")

        found = engine.get_negotiation(neg.id)
        assert found is not None
        assert found.id == neg.id
        assert found.bid_id == "bid-1"

    def test_returns_none_for_missing(self):
        engine = NegotiationEngine()
        assert engine.get_negotiation("nonexistent-id") is None


# ---------------------------------------------------------------------------
# Transition
# ---------------------------------------------------------------------------


class TestTransition:
    """Execute state transitions on negotiations."""

    def test_valid_transition_returns_true(self):
        engine = NegotiationEngine()
        neg = engine.create_negotiation(bid_id="bid-1", platform="freelancer")

        result = engine.transition(neg.id, "qualifying", reason="client question")
        assert result is True

    def test_updates_state(self):
        engine = NegotiationEngine()
        neg = engine.create_negotiation(bid_id="bid-1", platform="freelancer")

        engine.transition(neg.id, "qualifying", reason="client question")

        updated = engine.get_negotiation(neg.id)
        assert updated is not None
        assert updated.state == "qualifying"

    def test_updates_updated_at(self):
        engine = NegotiationEngine()
        neg = engine.create_negotiation(bid_id="bid-1", platform="freelancer")
        original_updated = neg.updated_at

        engine.transition(neg.id, "qualifying", reason="test")

        updated = engine.get_negotiation(neg.id)
        assert updated is not None
        assert updated.updated_at >= original_updated

    def test_invalid_transition_raises(self):
        engine = NegotiationEngine()
        neg = engine.create_negotiation(bid_id="bid-1", platform="freelancer")

        with pytest.raises(InvalidTransitionError):
            engine.transition(neg.id, "closing", reason="skip")

    def test_missing_negotiation_returns_false(self):
        engine = NegotiationEngine()
        result = engine.transition("nonexistent", "qualifying", reason="test")
        assert result is False

    def test_records_actor(self):
        engine = NegotiationEngine()
        neg = engine.create_negotiation(bid_id="bid-1", platform="freelancer")

        engine.transition(neg.id, "qualifying", reason="test", actor="operator")

        updated = engine.get_negotiation(neg.id)
        assert updated is not None
        last_entry = updated.state_machine.history[-1]
        assert last_entry["actor"] == "operator"

    def test_preserves_history_across_transitions(self):
        engine = NegotiationEngine()
        neg = engine.create_negotiation(bid_id="bid-1", platform="freelancer")

        engine.transition(neg.id, "qualifying", reason="question")
        engine.transition(neg.id, "proposing", reason="answered")
        engine.transition(neg.id, "closing", reason="agreed")

        updated = engine.get_negotiation(neg.id)
        assert updated is not None
        assert len(updated.state_machine.history) == 3


# ---------------------------------------------------------------------------
# Get active
# ---------------------------------------------------------------------------


class TestGetActive:
    """Retrieve all non-terminal negotiations."""

    def test_returns_active_negotiations(self):
        engine = NegotiationEngine()
        engine.create_negotiation(bid_id="bid-1", platform="freelancer")
        engine.create_negotiation(bid_id="bid-2", platform="kwork")

        active = engine.get_active()
        assert len(active) == 2

    def test_excludes_terminal(self):
        engine = NegotiationEngine()
        n1 = engine.create_negotiation(bid_id="bid-1", platform="freelancer")
        engine.create_negotiation(bid_id="bid-2", platform="kwork")

        engine.transition(n1.id, "accepted", reason="hired")

        active = engine.get_active()
        assert len(active) == 1
        assert active[0].bid_id == "bid-2"

    def test_returns_empty_when_all_terminal(self):
        engine = NegotiationEngine()
        n1 = engine.create_negotiation(bid_id="bid-1", platform="freelancer")

        engine.transition(n1.id, "declined", reason="rejected")

        active = engine.get_active()
        assert active == []


# ---------------------------------------------------------------------------
# Mark stale
# ---------------------------------------------------------------------------


class TestMarkStale:
    """Identify and mark stale negotiations."""

    def test_marks_old_negotiations_stale(self):
        engine = NegotiationEngine()
        neg = engine.create_negotiation(bid_id="bid-1", platform="freelancer")

        # Manually backdate updated_at
        stored = engine.get_negotiation(neg.id)
        assert stored is not None
        stored.updated_at = datetime.now(UTC) - timedelta(days=15)

        count = engine.mark_stale(threshold_days=14)
        assert count == 1

        updated = engine.get_negotiation(neg.id)
        assert updated is not None
        assert updated.state == "stale"

    def test_skips_recently_updated(self):
        engine = NegotiationEngine()
        engine.create_negotiation(bid_id="bid-1", platform="freelancer")

        count = engine.mark_stale(threshold_days=14)
        assert count == 0

    def test_skips_terminal_states(self):
        engine = NegotiationEngine()
        neg = engine.create_negotiation(bid_id="bid-1", platform="freelancer")
        engine.transition(neg.id, "accepted", reason="hired")

        stored = engine.get_negotiation(neg.id)
        assert stored is not None
        stored.updated_at = datetime.now(UTC) - timedelta(days=30)

        count = engine.mark_stale(threshold_days=14)
        assert count == 0

    def test_skips_already_stale(self):
        engine = NegotiationEngine()
        neg = engine.create_negotiation(bid_id="bid-1", platform="freelancer")
        engine.transition(neg.id, "stale", reason="no response")

        stored = engine.get_negotiation(neg.id)
        assert stored is not None
        stored.updated_at = datetime.now(UTC) - timedelta(days=30)

        count = engine.mark_stale(threshold_days=14)
        assert count == 0

    def test_default_threshold_is_14_days(self):
        engine = NegotiationEngine()
        neg = engine.create_negotiation(bid_id="bid-1", platform="freelancer")

        stored = engine.get_negotiation(neg.id)
        assert stored is not None
        stored.updated_at = datetime.now(UTC) - timedelta(days=13)

        count = engine.mark_stale()
        assert count == 0

        stored.updated_at = datetime.now(UTC) - timedelta(days=15)
        count = engine.mark_stale()
        assert count == 1


# ---------------------------------------------------------------------------
# Negotiation model
# ---------------------------------------------------------------------------


class TestNegotiationModel:
    """Verify Negotiation dataclass."""

    def test_has_required_fields(self):
        engine = NegotiationEngine()
        neg = engine.create_negotiation(bid_id="bid-1", platform="freelancer")

        assert hasattr(neg, "id")
        assert hasattr(neg, "bid_id")
        assert hasattr(neg, "platform")
        assert hasattr(neg, "state")
        assert hasattr(neg, "state_machine")
        assert hasattr(neg, "messages")
        assert hasattr(neg, "created_at")
        assert hasattr(neg, "updated_at")

    def test_state_reflects_state_machine(self):
        engine = NegotiationEngine()
        neg = engine.create_negotiation(bid_id="bid-1", platform="freelancer")

        engine.transition(neg.id, "qualifying", reason="test")

        updated = engine.get_negotiation(neg.id)
        assert updated is not None
        assert updated.state == updated.state_machine.current_state
