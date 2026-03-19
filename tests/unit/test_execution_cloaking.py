"""Unit tests for Execution Cloaking system (src/core/execution_cloaking.py).

Tests cover all 3 components per spec (dev-cycle-spec.md Phase 4.5):
1. Double Estimation — AI time vs market rate, present market rate
2. Delivery Throttling — delay by 70% of estimated time, min 24h
3. Dispatch Loop — ScheduledMessage send_at <= now() query + mark sent

Plan: docs/Full_work/dev-cycle-spec.md  Phase 4.5
Reference: src/core/touch_sequence.py (scheduling patterns)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.execution_cloaking import (
    DELIVERY_THROTTLE_RATIO,
    MARKET_RATE_DIVISOR_MAX,
    MARKET_RATE_DIVISOR_MIN,
    MIN_DELIVERY_HOURS,
    CloakingConfig,
    CloakingResult,
    DeliveryHold,
    DoubleEstimate,
    ExecutionCloaking,
    ScheduledMessageResult,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    """Verify module-level constants match the spec."""

    def test_min_delivery_hours_is_24(self):
        """Spec: minimum 24 hours -- never propose less."""
        assert MIN_DELIVERY_HOURS == 24

    def test_delivery_throttle_ratio_is_0_7(self):
        """Spec: min_delivery_at = created_at + proposed_days * 0.7."""
        assert DELIVERY_THROTTLE_RATIO == 0.7

    def test_market_rate_divisor_min(self):
        """Spec: human_days / 2..3 => divisor min is 2."""
        assert MARKET_RATE_DIVISOR_MIN == 2

    def test_market_rate_divisor_max(self):
        """Spec: human_days / 2..3 => divisor max is 3."""
        assert MARKET_RATE_DIVISOR_MAX == 3


# ---------------------------------------------------------------------------
# CloakingConfig dataclass
# ---------------------------------------------------------------------------


class TestCloakingConfig:
    """Verify CloakingConfig fields and defaults."""

    def test_default_min_delivery_hours(self):
        cfg = CloakingConfig()
        assert cfg.min_delivery_hours == MIN_DELIVERY_HOURS

    def test_default_throttle_ratio(self):
        cfg = CloakingConfig()
        assert cfg.throttle_ratio == DELIVERY_THROTTLE_RATIO

    def test_default_divisor_min(self):
        cfg = CloakingConfig()
        assert cfg.divisor_min == MARKET_RATE_DIVISOR_MIN

    def test_default_divisor_max(self):
        cfg = CloakingConfig()
        assert cfg.divisor_max == MARKET_RATE_DIVISOR_MAX

    def test_custom_values(self):
        cfg = CloakingConfig(
            min_delivery_hours=48,
            throttle_ratio=0.8,
            divisor_min=3,
            divisor_max=4,
        )
        assert cfg.min_delivery_hours == 48
        assert cfg.throttle_ratio == 0.8
        assert cfg.divisor_min == 3
        assert cfg.divisor_max == 4


# ---------------------------------------------------------------------------
# DoubleEstimate dataclass
# ---------------------------------------------------------------------------


class TestDoubleEstimate:
    """Verify DoubleEstimate fields."""

    def test_create_estimate(self):
        est = DoubleEstimate(
            real_hours=2.0,
            human_days=10,
            proposed_days=4,
        )
        assert est.real_hours == 2.0
        assert est.human_days == 10
        assert est.proposed_days == 4

    def test_speedup_ratio(self):
        est = DoubleEstimate(real_hours=2.0, human_days=10, proposed_days=4)
        # 10 days * 8 hours/day = 80 human hours, AI does it in 2 => 40x speedup
        assert est.speedup_ratio == pytest.approx(40.0)

    def test_speedup_ratio_zero_real_hours(self):
        est = DoubleEstimate(real_hours=0.0, human_days=10, proposed_days=4)
        # Avoid division by zero -- should return 0 safely
        assert est.speedup_ratio >= 0

    def test_margin_percentage(self):
        est = DoubleEstimate(real_hours=2.0, human_days=10, proposed_days=4)
        # proposed_days * 8 = 32 billable hours, real = 2 => margin ~93.75%
        assert est.margin_percentage > 90.0

    def test_margin_percentage_zero_proposed(self):
        est = DoubleEstimate(real_hours=2.0, human_days=0, proposed_days=0)
        assert est.margin_percentage == 0.0


# ---------------------------------------------------------------------------
# CloakingResult dataclass
# ---------------------------------------------------------------------------


class TestCloakingResult:
    """Verify CloakingResult fields."""

    def test_create_result(self):
        now = _utcnow()
        est = DoubleEstimate(real_hours=2.0, human_days=10, proposed_days=4)
        result = CloakingResult(
            estimate=est,
            min_delivery_at=now + timedelta(days=3),
            created_at=now,
            scheduled_message_count=3,
        )
        assert result.estimate.proposed_days == 4
        assert result.scheduled_message_count == 3

    def test_delivery_window_hours(self):
        now = _utcnow()
        result = CloakingResult(
            estimate=DoubleEstimate(real_hours=1.0, human_days=5, proposed_days=3),
            min_delivery_at=now + timedelta(hours=50),
            created_at=now,
            scheduled_message_count=2,
        )
        assert result.delivery_window_hours == pytest.approx(50.0, abs=0.1)


# ---------------------------------------------------------------------------
# DeliveryHold dataclass
# ---------------------------------------------------------------------------


class TestDeliveryHold:
    """Verify DeliveryHold fields."""

    def test_create_hold(self):
        now = _utcnow()
        future = now + timedelta(days=2)
        hold = DeliveryHold(
            is_held=True,
            min_delivery_at=future,
            remaining_hours=48.0,
        )
        assert hold.is_held is True
        assert hold.remaining_hours == pytest.approx(48.0)

    def test_not_held(self):
        hold = DeliveryHold(
            is_held=False,
            min_delivery_at=None,
            remaining_hours=0.0,
        )
        assert hold.is_held is False
        assert hold.remaining_hours == 0.0


# ---------------------------------------------------------------------------
# ScheduledMessageResult dataclass
# ---------------------------------------------------------------------------


class TestScheduledMessageResult:
    """Verify ScheduledMessageResult fields."""

    def test_create_result(self):
        msg_id = uuid.uuid4()
        result = ScheduledMessageResult(dispatched_count=1, messages=[msg_id])
        assert result.dispatched_count == 1
        assert msg_id in result.messages

    def test_empty_result(self):
        result = ScheduledMessageResult(dispatched_count=0, messages=[])
        assert result.dispatched_count == 0
        assert result.messages == []


# ---------------------------------------------------------------------------
# ExecutionCloaking.__init__
# ---------------------------------------------------------------------------


class TestInit:
    """Verify ExecutionCloaking construction."""

    def test_default_config(self):
        ec = ExecutionCloaking()
        assert ec.config.min_delivery_hours == MIN_DELIVERY_HOURS

    def test_custom_config(self):
        cfg = CloakingConfig(min_delivery_hours=48)
        ec = ExecutionCloaking(config=cfg)
        assert ec.config.min_delivery_hours == 48


# ---------------------------------------------------------------------------
# Component 1: Double Estimation
# ---------------------------------------------------------------------------


class TestDoubleEstimation:
    """Verify double estimation: AI time vs market rate."""

    def test_basic_estimation(self):
        ec = ExecutionCloaking()
        est = ec.compute_double_estimate(real_hours=2.0, human_days=10)
        assert est.real_hours == 2.0
        assert est.human_days == 10
        # proposed_days = human_days / divisor (2..3), so between 3 and 5
        assert 3 <= est.proposed_days <= 5

    def test_minimum_24_hours_enforced(self):
        """Spec: minimum 24 hours -- never propose less."""
        ec = ExecutionCloaking()
        est = ec.compute_double_estimate(real_hours=0.5, human_days=1)
        # human_days=1 / 3 = 0.33 days, but minimum is 1 day (24h)
        assert est.proposed_days >= 1

    def test_zero_real_hours(self):
        ec = ExecutionCloaking()
        est = ec.compute_double_estimate(real_hours=0.0, human_days=5)
        assert est.proposed_days >= 1
        assert est.real_hours == 0.0

    def test_zero_human_days(self):
        """Edge case: human_days=0 should still give at least 1 proposed day."""
        ec = ExecutionCloaking()
        est = ec.compute_double_estimate(real_hours=1.0, human_days=0)
        assert est.proposed_days >= 1

    def test_negative_real_hours_clamped(self):
        ec = ExecutionCloaking()
        est = ec.compute_double_estimate(real_hours=-5.0, human_days=10)
        assert est.real_hours == 0.0

    def test_negative_human_days_clamped(self):
        ec = ExecutionCloaking()
        est = ec.compute_double_estimate(real_hours=2.0, human_days=-3)
        assert est.human_days == 0
        assert est.proposed_days >= 1

    def test_large_project_estimation(self):
        """20 human_days project => 7-10 proposed days."""
        ec = ExecutionCloaking()
        est = ec.compute_double_estimate(real_hours=8.0, human_days=20)
        assert 6 <= est.proposed_days <= 10

    def test_small_project_estimation(self):
        """2 human_days project => 1 proposed day (minimum)."""
        ec = ExecutionCloaking()
        est = ec.compute_double_estimate(real_hours=0.5, human_days=2)
        assert est.proposed_days >= 1

    def test_custom_divisor_range(self):
        """Custom divisor should affect proposed_days."""
        cfg = CloakingConfig(divisor_min=4, divisor_max=5)
        ec = ExecutionCloaking(config=cfg)
        est = ec.compute_double_estimate(real_hours=2.0, human_days=20)
        # 20 / 5 = 4, 20 / 4 = 5
        assert 4 <= est.proposed_days <= 5

    def test_proposed_days_is_int(self):
        ec = ExecutionCloaking()
        est = ec.compute_double_estimate(real_hours=3.0, human_days=7)
        assert isinstance(est.proposed_days, int)

    def test_speedup_ratio_calculated(self):
        ec = ExecutionCloaking()
        est = ec.compute_double_estimate(real_hours=1.0, human_days=10)
        # 10 * 8 = 80 human hours / 1 real hour = 80x
        assert est.speedup_ratio == pytest.approx(80.0)


# ---------------------------------------------------------------------------
# Component 2: Delivery Throttling
# ---------------------------------------------------------------------------


class TestDeliveryThrottling:
    """Verify delivery throttling: delay by 70% of estimated time."""

    def test_compute_min_delivery_at(self):
        """min_delivery_at = created_at + proposed_days * 0.7."""
        ec = ExecutionCloaking()
        now = _utcnow()
        min_at = ec.compute_min_delivery_at(proposed_days=4, created_at=now)
        expected = now + timedelta(days=4 * 0.7)
        assert abs((min_at - expected).total_seconds()) < 1

    def test_minimum_24h_delivery(self):
        """Even 1-day proposal should enforce >= 24h."""
        ec = ExecutionCloaking()
        now = _utcnow()
        min_at = ec.compute_min_delivery_at(proposed_days=1, created_at=now)
        diff_hours = (min_at - now).total_seconds() / 3600
        assert diff_hours >= 24.0

    def test_zero_proposed_days_enforces_minimum(self):
        ec = ExecutionCloaking()
        now = _utcnow()
        min_at = ec.compute_min_delivery_at(proposed_days=0, created_at=now)
        diff_hours = (min_at - now).total_seconds() / 3600
        assert diff_hours >= 24.0

    def test_negative_proposed_days_enforces_minimum(self):
        ec = ExecutionCloaking()
        now = _utcnow()
        min_at = ec.compute_min_delivery_at(proposed_days=-5, created_at=now)
        diff_hours = (min_at - now).total_seconds() / 3600
        assert diff_hours >= 24.0

    def test_large_proposed_days(self):
        ec = ExecutionCloaking()
        now = _utcnow()
        min_at = ec.compute_min_delivery_at(proposed_days=30, created_at=now)
        expected = now + timedelta(days=30 * 0.7)
        assert abs((min_at - expected).total_seconds()) < 1

    def test_custom_throttle_ratio(self):
        cfg = CloakingConfig(throttle_ratio=0.5)
        ec = ExecutionCloaking(config=cfg)
        now = _utcnow()
        min_at = ec.compute_min_delivery_at(proposed_days=10, created_at=now)
        expected = now + timedelta(days=10 * 0.5)
        assert abs((min_at - expected).total_seconds()) < 1

    def test_check_delivery_hold_future(self):
        """Delivery should be held when min_delivery_at is in the future."""
        ec = ExecutionCloaking()
        future = _utcnow() + timedelta(days=2)
        hold = ec.check_delivery_hold(min_delivery_at=future)
        assert hold.is_held is True
        assert hold.remaining_hours > 0

    def test_check_delivery_hold_past(self):
        """Delivery should NOT be held when min_delivery_at is in the past."""
        ec = ExecutionCloaking()
        past = _utcnow() - timedelta(days=1)
        hold = ec.check_delivery_hold(min_delivery_at=past)
        assert hold.is_held is False
        assert hold.remaining_hours == 0.0

    def test_check_delivery_hold_none(self):
        """No min_delivery_at => not held."""
        ec = ExecutionCloaking()
        hold = ec.check_delivery_hold(min_delivery_at=None)
        assert hold.is_held is False
        assert hold.remaining_hours == 0.0

    def test_check_delivery_hold_remaining_accuracy(self):
        ec = ExecutionCloaking()
        future = _utcnow() + timedelta(hours=48)
        hold = ec.check_delivery_hold(min_delivery_at=future)
        assert hold.remaining_hours == pytest.approx(48.0, abs=0.1)

    def test_check_delivery_hold_naive_datetime(self):
        """Should handle naive datetimes by assuming UTC."""
        ec = ExecutionCloaking()
        future = datetime.utcnow() + timedelta(days=2)  # noqa: DTZ003
        hold = ec.check_delivery_hold(min_delivery_at=future)
        assert hold.is_held is True


# ---------------------------------------------------------------------------
# Full cloaking pipeline (compute_double_estimate + throttle)
# ---------------------------------------------------------------------------


class TestApplyCloaking:
    """Verify full cloaking pipeline via apply()."""

    def test_apply_returns_cloaking_result(self):
        ec = ExecutionCloaking()
        now = _utcnow()
        result = ec.apply(real_hours=2.0, human_days=10, created_at=now)
        assert isinstance(result, CloakingResult)
        assert result.estimate.real_hours == 2.0
        assert result.min_delivery_at > now

    def test_apply_min_delivery_at_uses_throttle(self):
        """min_delivery_at should be ~70% of proposed_days after created_at."""
        ec = ExecutionCloaking()
        now = _utcnow()
        result = ec.apply(real_hours=1.0, human_days=14, created_at=now)
        proposed = result.estimate.proposed_days
        expected_min = now + timedelta(days=proposed * 0.7)
        # Should be within 1 second of expected (24h minimum might override)
        diff = abs((result.min_delivery_at - expected_min).total_seconds())
        if proposed * 0.7 * 24 >= 24:
            assert diff < 2

    def test_apply_enforces_minimum_24h(self):
        ec = ExecutionCloaking()
        now = _utcnow()
        result = ec.apply(real_hours=0.1, human_days=1, created_at=now)
        diff_hours = (result.min_delivery_at - now).total_seconds() / 3600
        assert diff_hours >= 24.0

    def test_apply_default_created_at(self):
        """Should use current time if created_at not provided."""
        ec = ExecutionCloaking()
        before = _utcnow()
        result = ec.apply(real_hours=2.0, human_days=10)
        after = _utcnow()
        assert before <= result.created_at <= after

    def test_apply_zero_inputs(self):
        ec = ExecutionCloaking()
        result = ec.apply(real_hours=0.0, human_days=0)
        assert result.estimate.proposed_days >= 1
        diff_hours = (result.min_delivery_at - result.created_at).total_seconds() / 3600
        assert diff_hours >= 24.0


# ---------------------------------------------------------------------------
# State integration helpers
# ---------------------------------------------------------------------------


class TestStateHelpers:
    """Verify state dict integration methods."""

    def test_apply_to_state(self):
        """apply_to_state should set proposed_days, min_delivery_at, real_hours."""
        ec = ExecutionCloaking()
        now = _utcnow()
        state: dict = {
            "real_hours": None,
            "proposed_days": None,
            "min_delivery_at": None,
            "scheduled_messages": [],
            "created_at": now,
        }
        result = ec.apply_to_state(state, real_hours=2.0, human_days=10)
        assert state["real_hours"] == 2.0
        assert state["proposed_days"] >= 1
        assert state["min_delivery_at"] is not None
        assert isinstance(result, CloakingResult)

    def test_apply_to_state_preserves_other_fields(self):
        ec = ExecutionCloaking()
        now = _utcnow()
        state: dict = {
            "thread_id": "t-123",
            "status": "active",
            "real_hours": None,
            "proposed_days": None,
            "min_delivery_at": None,
            "scheduled_messages": [],
            "created_at": now,
        }
        ec.apply_to_state(state, real_hours=1.0, human_days=5)
        assert state["thread_id"] == "t-123"
        assert state["status"] == "active"

    def test_is_delivery_held_from_state(self):
        ec = ExecutionCloaking()
        future = _utcnow() + timedelta(days=3)
        state: dict = {"min_delivery_at": future}
        hold = ec.check_delivery_hold_from_state(state)
        assert hold.is_held is True

    def test_is_delivery_held_from_state_none(self):
        ec = ExecutionCloaking()
        state: dict = {"min_delivery_at": None}
        hold = ec.check_delivery_hold_from_state(state)
        assert hold.is_held is False

    def test_is_delivery_held_from_state_missing_key(self):
        ec = ExecutionCloaking()
        state: dict = {}
        hold = ec.check_delivery_hold_from_state(state)
        assert hold.is_held is False


# ---------------------------------------------------------------------------
# Component 3: Dispatch Loop
# ---------------------------------------------------------------------------


class TestDispatchLoop:
    """Verify dispatch loop: query ScheduledMessages where send_at <= now()."""

    pytestmark = pytest.mark.asyncio

    async def test_dispatch_due_messages(self):
        """Should mark due messages as sent and return count."""
        ec = ExecutionCloaking()

        past = _utcnow() - timedelta(hours=1)
        mock_msg1 = MagicMock()
        mock_msg1.id = uuid.uuid4()
        mock_msg1.project_id = "proj-1"
        mock_msg1.thread_id = "thread-1"
        mock_msg1.content = "Progress update"
        mock_msg1.channel = "platform"
        mock_msg1.send_at = past
        mock_msg1.status = "pending"

        mock_msg2 = MagicMock()
        mock_msg2.id = uuid.uuid4()
        mock_msg2.project_id = "proj-1"
        mock_msg2.thread_id = "thread-1"
        mock_msg2.content = "Backend ready"
        mock_msg2.channel = "platform"
        mock_msg2.send_at = past - timedelta(hours=2)
        mock_msg2.status = "pending"

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_msg1, mock_msg2]

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        result = await ec.dispatch_due_messages(mock_session)

        assert isinstance(result, ScheduledMessageResult)
        assert result.dispatched_count == 2
        assert mock_msg1.status == "sent"
        assert mock_msg2.status == "sent"
        assert mock_msg1.sent_at is not None
        assert mock_msg2.sent_at is not None

    async def test_dispatch_no_due_messages(self):
        """Should return 0 when no messages are due."""
        ec = ExecutionCloaking()

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        result = await ec.dispatch_due_messages(mock_session)

        assert result.dispatched_count == 0
        assert result.messages == []

    async def test_dispatch_marks_sent_at_timestamp(self):
        """sent_at should be set to approximately now."""
        ec = ExecutionCloaking()

        before = _utcnow()

        mock_msg = MagicMock()
        mock_msg.id = uuid.uuid4()
        mock_msg.status = "pending"
        mock_msg.send_at = _utcnow() - timedelta(minutes=5)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_msg]

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        await ec.dispatch_due_messages(mock_session)

        after = _utcnow()
        assert before <= mock_msg.sent_at <= after

    async def test_dispatch_commits_session(self):
        """Should commit the session after marking messages."""
        ec = ExecutionCloaking()

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        await ec.dispatch_due_messages(mock_session)

        mock_session.commit.assert_awaited_once()

    async def test_dispatch_returns_message_ids(self):
        """Result should contain ids of dispatched messages."""
        ec = ExecutionCloaking()

        msg_id = uuid.uuid4()
        mock_msg = MagicMock()
        mock_msg.id = msg_id
        mock_msg.status = "pending"
        mock_msg.send_at = _utcnow() - timedelta(minutes=1)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_msg]

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        result = await ec.dispatch_due_messages(mock_session)

        assert msg_id in result.messages

    async def test_dispatch_only_queries_pending(self):
        """Query should filter by status='pending'."""
        ec = ExecutionCloaking()

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        await ec.dispatch_due_messages(mock_session)

        mock_session.execute.assert_awaited_once()


# ---------------------------------------------------------------------------
# Scheduled message generation integration
# ---------------------------------------------------------------------------


class TestScheduledMessageGeneration:
    """Verify generate_progress_messages creates messages for delivery window."""

    def test_generates_messages(self):
        ec = ExecutionCloaking()
        now = _utcnow()
        min_delivery_at = now + timedelta(days=3)
        messages = ec.generate_progress_messages(
            project_id="proj-1",
            thread_id="thread-1",
            min_delivery_at=min_delivery_at,
        )
        assert len(messages) >= 2
        assert all(isinstance(m, dict) for m in messages)

    def test_messages_are_before_min_delivery(self):
        ec = ExecutionCloaking()
        now = _utcnow()
        min_delivery_at = now + timedelta(days=3)
        messages = ec.generate_progress_messages(
            project_id="proj-1",
            thread_id="thread-1",
            min_delivery_at=min_delivery_at,
        )
        for m in messages:
            assert m["send_at"] <= min_delivery_at

    def test_messages_are_chronological(self):
        ec = ExecutionCloaking()
        now = _utcnow()
        min_delivery_at = now + timedelta(days=5)
        messages = ec.generate_progress_messages(
            project_id="proj-1",
            thread_id="thread-1",
            min_delivery_at=min_delivery_at,
        )
        send_times = [m["send_at"] for m in messages]
        assert send_times == sorted(send_times)

    def test_messages_have_content(self):
        ec = ExecutionCloaking()
        now = _utcnow()
        messages = ec.generate_progress_messages(
            project_id="p1",
            thread_id="t1",
            min_delivery_at=now + timedelta(days=2),
        )
        assert all(m["content"] and len(m["content"]) > 10 for m in messages)

    def test_messages_have_required_fields(self):
        ec = ExecutionCloaking()
        now = _utcnow()
        messages = ec.generate_progress_messages(
            project_id="p1",
            thread_id="t1",
            min_delivery_at=now + timedelta(days=2),
        )
        required = {"project_id", "thread_id", "send_at", "content", "channel", "status"}
        for m in messages:
            assert required.issubset(m.keys())

    def test_messages_default_channel_platform(self):
        ec = ExecutionCloaking()
        now = _utcnow()
        messages = ec.generate_progress_messages(
            project_id="p1",
            thread_id="t1",
            min_delivery_at=now + timedelta(days=2),
        )
        assert all(m["channel"] == "platform" for m in messages)

    def test_messages_custom_channel(self):
        ec = ExecutionCloaking()
        now = _utcnow()
        messages = ec.generate_progress_messages(
            project_id="p1",
            thread_id="t1",
            min_delivery_at=now + timedelta(days=2),
            channel="email",
        )
        assert all(m["channel"] == "email" for m in messages)

    def test_messages_all_pending_status(self):
        ec = ExecutionCloaking()
        now = _utcnow()
        messages = ec.generate_progress_messages(
            project_id="p1",
            thread_id="t1",
            min_delivery_at=now + timedelta(days=2),
        )
        assert all(m["status"] == "pending" for m in messages)

    def test_short_window_fewer_messages(self):
        ec = ExecutionCloaking()
        now = _utcnow()
        short = ec.generate_progress_messages("p", "t", now + timedelta(hours=6))
        long = ec.generate_progress_messages("p", "t", now + timedelta(days=5))
        assert len(short) <= len(long)

    def test_minimum_two_messages(self):
        ec = ExecutionCloaking()
        now = _utcnow()
        messages = ec.generate_progress_messages("p", "t", now + timedelta(hours=2))
        assert len(messages) >= 2

    def test_maximum_eight_messages(self):
        ec = ExecutionCloaking()
        now = _utcnow()
        messages = ec.generate_progress_messages("p", "t", now + timedelta(days=30))
        assert len(messages) <= 8


# ---------------------------------------------------------------------------
# Cloaking cover story
# ---------------------------------------------------------------------------


class TestCoverStory:
    """Verify the cover story text per spec."""

    def test_get_cover_story_returns_string(self):
        ec = ExecutionCloaking()
        story = ec.get_cover_story()
        assert isinstance(story, str)
        assert len(story) > 50

    def test_cover_story_mentions_tools(self):
        """Spec: mention proprietary library, CLI tools, CI/CD pipelines."""
        ec = ExecutionCloaking()
        story = ec.get_cover_story().lower()
        assert "библиотек" in story or "library" in story or "component" in story

    def test_cover_story_no_ai_mention(self):
        """Cover story should NOT mention AI, ML, or automation directly."""
        ec = ExecutionCloaking()
        story = ec.get_cover_story().lower()
        assert "ai" not in story.split()
        assert "machine learning" not in story


# ---------------------------------------------------------------------------
# Edge cases and robustness
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases and robustness checks."""

    def test_very_large_real_hours(self):
        ec = ExecutionCloaking()
        est = ec.compute_double_estimate(real_hours=1000.0, human_days=500)
        assert est.proposed_days >= 1

    def test_fractional_human_days(self):
        """human_days can be fractional (e.g. 0.5 days)."""
        ec = ExecutionCloaking()
        est = ec.compute_double_estimate(real_hours=0.5, human_days=0.5)
        assert est.proposed_days >= 1

    def test_apply_idempotent_state(self):
        """Calling apply_to_state twice should overwrite cleanly."""
        ec = ExecutionCloaking()
        now = _utcnow()
        state: dict = {
            "real_hours": 5.0,
            "proposed_days": 10,
            "min_delivery_at": now + timedelta(days=7),
            "scheduled_messages": [],
            "created_at": now,
        }
        ec.apply_to_state(state, real_hours=2.0, human_days=8)
        assert state["real_hours"] == 2.0
        assert state["proposed_days"] != 10  # changed

    def test_compute_min_delivery_at_returns_aware_datetime(self):
        ec = ExecutionCloaking()
        now = _utcnow()
        result = ec.compute_min_delivery_at(proposed_days=5, created_at=now)
        assert result.tzinfo is not None

    def test_thread_safety_independent_instances(self):
        """Two ExecutionCloaking instances should not share state."""
        ec1 = ExecutionCloaking()
        ec2 = ExecutionCloaking(config=CloakingConfig(min_delivery_hours=48))
        assert ec1.config.min_delivery_hours != ec2.config.min_delivery_hours


# ---------------------------------------------------------------------------
# Logging verification
# ---------------------------------------------------------------------------


class TestLogging:
    """Verify structlog integration."""

    pytestmark = pytest.mark.asyncio

    def test_apply_logs_estimation(self):
        ec = ExecutionCloaking()
        with patch("src.core.execution_cloaking.logger") as mock_log:
            ec.apply(real_hours=2.0, human_days=10)
            mock_log.info.assert_called()

    async def test_dispatch_logs_count(self):
        ec = ExecutionCloaking()

        mock_result = MagicMock()
        mock_msg = MagicMock()
        mock_msg.id = uuid.uuid4()
        mock_msg.status = "pending"
        mock_msg.send_at = _utcnow() - timedelta(minutes=1)
        mock_result.scalars.return_value.all.return_value = [mock_msg]

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        with patch("src.core.execution_cloaking.logger") as mock_log:
            await ec.dispatch_due_messages(mock_session)
            mock_log.info.assert_called()


# ---------------------------------------------------------------------------
# LLM-generated progress messages (Issue 1)
# ---------------------------------------------------------------------------


class TestLLMProgressMessages:
    """Verify LLM-generated progress messages with static fallback."""

    pytestmark = pytest.mark.asyncio

    async def test_generate_progress_message_with_llm(self):
        """When LLM client is available, should use it for message generation."""
        mock_response = MagicMock()
        mock_response.content = "Working on the React dashboard components and connecting to the API."
        mock_metrics = MagicMock()

        mock_llm = AsyncMock()
        mock_llm.call = AsyncMock(return_value=(mock_response, mock_metrics))

        ec = ExecutionCloaking(llm_client=mock_llm)
        msg = await ec._generate_progress_message(
            project_context={"title": "Dashboard", "category": "frontend", "tech_stack": "React"},
            progress_pct=50.0,
        )

        assert len(msg) > 10
        assert msg == "Working on the React dashboard components and connecting to the API."
        mock_llm.call.assert_awaited_once()

    async def test_generate_progress_message_llm_fallback_on_error(self):
        """When LLM call fails, should fall back to static templates."""
        mock_llm = AsyncMock()
        mock_llm.call = AsyncMock(side_effect=Exception("LLM unavailable"))

        ec = ExecutionCloaking(llm_client=mock_llm)
        msg = await ec._generate_progress_message(
            project_context={"title": "API"},
            progress_pct=25.0,
        )

        assert len(msg) > 10
        # Should be one of the static templates
        from src.core.execution_cloaking import _PROGRESS_TEMPLATES

        assert msg in _PROGRESS_TEMPLATES

    async def test_generate_progress_message_no_llm_client(self):
        """When no LLM client is configured, should use static templates."""
        ec = ExecutionCloaking()  # no llm_client
        msg = await ec._generate_progress_message(
            project_context={"title": "Test"},
            progress_pct=0.0,
        )
        from src.core.execution_cloaking import _PROGRESS_TEMPLATES

        assert msg in _PROGRESS_TEMPLATES

    async def test_generate_progress_message_llm_empty_response(self):
        """When LLM returns empty/short content, fall back to static."""
        mock_response = MagicMock()
        mock_response.content = "OK"  # too short
        mock_metrics = MagicMock()

        mock_llm = AsyncMock()
        mock_llm.call = AsyncMock(return_value=(mock_response, mock_metrics))

        ec = ExecutionCloaking(llm_client=mock_llm)
        msg = await ec._generate_progress_message(
            project_context={"title": "Test"},
            progress_pct=50.0,
        )

        from src.core.execution_cloaking import _PROGRESS_TEMPLATES

        assert msg in _PROGRESS_TEMPLATES

    def test_fallback_progress_message_start(self):
        """At 0% progress, should return the first template."""
        from src.core.execution_cloaking import _PROGRESS_TEMPLATES

        msg = ExecutionCloaking._fallback_progress_message(0.0)
        assert msg == _PROGRESS_TEMPLATES[0]

    def test_fallback_progress_message_end(self):
        """At 100% progress, should return the last template."""
        from src.core.execution_cloaking import _PROGRESS_TEMPLATES

        msg = ExecutionCloaking._fallback_progress_message(100.0)
        assert msg == _PROGRESS_TEMPLATES[-1]

    def test_fallback_progress_message_mid(self):
        """At 50% progress, should return a middle template."""
        from src.core.execution_cloaking import _PROGRESS_TEMPLATES

        msg = ExecutionCloaking._fallback_progress_message(50.0)
        assert msg in _PROGRESS_TEMPLATES

    def test_fallback_progress_message_clamps_negative(self):
        """Negative progress should clamp to 0%."""
        from src.core.execution_cloaking import _PROGRESS_TEMPLATES

        msg = ExecutionCloaking._fallback_progress_message(-20.0)
        assert msg == _PROGRESS_TEMPLATES[0]

    def test_fallback_progress_message_clamps_over_100(self):
        """Progress > 100 should clamp to 100%."""
        from src.core.execution_cloaking import _PROGRESS_TEMPLATES

        msg = ExecutionCloaking._fallback_progress_message(200.0)
        assert msg == _PROGRESS_TEMPLATES[-1]

    def test_init_accepts_llm_client(self):
        """Constructor should accept optional llm_client."""
        mock_llm = MagicMock()
        ec = ExecutionCloaking(llm_client=mock_llm)
        assert ec._llm_client is mock_llm

    def test_init_llm_client_defaults_none(self):
        """Without llm_client, _llm_client should be None."""
        ec = ExecutionCloaking()
        assert ec._llm_client is None


# ---------------------------------------------------------------------------
# WebSocket dispatch event (Issue 6)
# ---------------------------------------------------------------------------


class TestDispatchWebSocketEvent:
    """Verify WebSocket event emission during dispatch."""

    pytestmark = pytest.mark.asyncio

    async def test_dispatch_emits_ws_event(self):
        """When channels is provided, should emit event for each dispatched message."""
        ec = ExecutionCloaking()

        mock_msg = MagicMock()
        mock_msg.id = uuid.uuid4()
        mock_msg.project_id = "proj-ws"
        mock_msg.content = "Progress update"
        mock_msg.status = "pending"
        mock_msg.send_at = _utcnow() - timedelta(minutes=1)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_msg]

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        mock_channels = MagicMock()

        await ec.dispatch_due_messages(mock_session, channels=mock_channels)

        mock_channels.publish.assert_called_once()
        call_args = mock_channels.publish.call_args
        assert "cloaking:dispatched" in call_args[0][1]

    async def test_dispatch_no_channels_no_error(self):
        """Without channels, dispatch should work normally without errors."""
        ec = ExecutionCloaking()

        mock_msg = MagicMock()
        mock_msg.id = uuid.uuid4()
        mock_msg.status = "pending"
        mock_msg.send_at = _utcnow() - timedelta(minutes=1)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_msg]

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        result = await ec.dispatch_due_messages(mock_session)
        assert result.dispatched_count == 1

    async def test_dispatch_ws_failure_does_not_break(self):
        """WebSocket failure should not break the dispatch loop."""
        ec = ExecutionCloaking()

        mock_msg = MagicMock()
        mock_msg.id = uuid.uuid4()
        mock_msg.project_id = "proj-fail"
        mock_msg.content = "Test"
        mock_msg.status = "pending"
        mock_msg.send_at = _utcnow() - timedelta(minutes=1)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_msg]

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        mock_channels = MagicMock()
        mock_channels.publish.side_effect = ConnectionError("WS disconnected")

        result = await ec.dispatch_due_messages(mock_session, channels=mock_channels)
        assert result.dispatched_count == 1
        assert mock_msg.status == "sent"

    async def test_dispatch_emits_multiple_events(self):
        """Each dispatched message should emit its own WS event."""
        ec = ExecutionCloaking()

        msgs = []
        for _ in range(3):
            m = MagicMock()
            m.id = uuid.uuid4()
            m.project_id = "proj-multi"
            m.content = "Progress"
            m.status = "pending"
            m.send_at = _utcnow() - timedelta(minutes=1)
            msgs.append(m)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = msgs

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        mock_channels = MagicMock()

        result = await ec.dispatch_due_messages(mock_session, channels=mock_channels)
        assert result.dispatched_count == 3
        assert mock_channels.publish.call_count == 3
