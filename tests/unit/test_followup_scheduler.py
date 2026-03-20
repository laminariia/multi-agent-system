"""Unit tests for src.negotiations.followup -- FollowUpScheduler.

Tests cover: 4-step schedule, archive_as_stale, client reply skips,
cancel conditions, followup_count/last_followup_at updates, template
interpolation, and eligible negotiation query filtering.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.negotiations.followup import (
    _CANCEL_STATES,
    _FOLLOWUP_ELIGIBLE_STATES,
    FOLLOW_UP_SCHEDULE,
    FOLLOW_UP_TEMPLATES,
    MAX_FOLLOWUP_STEPS,
    FollowUpResult,
    FollowUpScheduler,
    FollowUpStep,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_negotiation(
    *,
    state: str = "qualifying",
    followup_count: int = 0,
    created_at: datetime | None = None,
) -> MagicMock:
    neg = MagicMock()
    neg.state = state
    neg.previous_state = None
    neg.outcome = None
    neg.resolved_at = None
    neg.followup_count = followup_count
    neg.last_followup_at = None
    neg.created_at = created_at or (datetime.now(UTC) - timedelta(days=10))
    neg.bid_id = uuid.uuid4()
    return neg


def _make_bid(*, bid_id: uuid.UUID | None = None) -> MagicMock:
    bid = MagicMock()
    bid.id = bid_id or uuid.uuid4()
    return bid


def _make_job(*, title: str = "React Landing Page") -> MagicMock:
    job = MagicMock()
    job.title = title
    return job


def _make_client_message(*, created_at: datetime, direction: str = "inbound") -> MagicMock:
    msg = MagicMock()
    msg.created_at = created_at
    msg.direction = direction
    return msg


# ---------------------------------------------------------------------------
# Tests: Schedule configuration
# ---------------------------------------------------------------------------


class TestScheduleConfig:
    """Test that the follow-up schedule is configured correctly."""

    def test_schedule_has_4_steps(self):
        assert len(FOLLOW_UP_SCHEDULE) == 4

    def test_step_days_are_2_5_10_14(self):
        days = [s.days for s in FOLLOW_UP_SCHEDULE]
        assert days == [2, 5, 10, 14]

    def test_last_step_is_archive(self):
        assert FOLLOW_UP_SCHEDULE[-1].template == "archive_as_stale"

    def test_templates_exist_for_all_steps(self):
        for step in FOLLOW_UP_SCHEDULE:
            assert step.template in FOLLOW_UP_TEMPLATES

    def test_archive_template_is_none(self):
        assert FOLLOW_UP_TEMPLATES["archive_as_stale"] is None

    def test_max_followup_steps_is_4(self):
        assert MAX_FOLLOWUP_STEPS == 4


# ---------------------------------------------------------------------------
# Tests: _should_cancel
# ---------------------------------------------------------------------------


class TestShouldCancel:
    """Test cancel conditions."""

    @pytest.mark.parametrize("state", list(_CANCEL_STATES))
    def test_terminal_state_cancels(self, state: str):
        scheduler = FollowUpScheduler()
        neg = _make_negotiation(state=state)
        assert scheduler._should_cancel(neg) is True

    def test_max_followup_count_cancels(self):
        scheduler = FollowUpScheduler()
        neg = _make_negotiation(followup_count=MAX_FOLLOWUP_STEPS)
        assert scheduler._should_cancel(neg) is True

    @pytest.mark.parametrize("state", list(_FOLLOWUP_ELIGIBLE_STATES))
    def test_eligible_state_does_not_cancel(self, state: str):
        scheduler = FollowUpScheduler()
        neg = _make_negotiation(state=state, followup_count=0)
        assert scheduler._should_cancel(neg) is False

    def test_operator_override_cancels(self):
        scheduler = FollowUpScheduler()
        neg = _make_negotiation(state="operator_override")
        assert scheduler._should_cancel(neg) is True


# ---------------------------------------------------------------------------
# Tests: _get_next_step
# ---------------------------------------------------------------------------


class TestGetNextStep:
    """Test step selection based on silence duration and followup_count."""

    def test_day_2_followup_0_returns_step_0(self):
        scheduler = FollowUpScheduler()
        step = scheduler._get_next_step(days_silent=2, followup_count=0)
        assert step is not None
        assert step.template == "gentle_reminder"

    def test_day_5_followup_1_returns_step_1(self):
        scheduler = FollowUpScheduler()
        step = scheduler._get_next_step(days_silent=5, followup_count=1)
        assert step is not None
        assert step.template == "second_followup"

    def test_day_10_followup_2_returns_step_2(self):
        scheduler = FollowUpScheduler()
        step = scheduler._get_next_step(days_silent=10, followup_count=2)
        assert step is not None
        assert step.template == "final_checkin"

    def test_day_14_followup_3_returns_archive(self):
        scheduler = FollowUpScheduler()
        step = scheduler._get_next_step(days_silent=14, followup_count=3)
        assert step is not None
        assert step.template == "archive_as_stale"

    def test_day_1_returns_none(self):
        scheduler = FollowUpScheduler()
        step = scheduler._get_next_step(days_silent=1, followup_count=0)
        assert step is None

    def test_already_sent_step_0_day_2_returns_none(self):
        scheduler = FollowUpScheduler()
        step = scheduler._get_next_step(days_silent=2, followup_count=1)
        # followup_count=1 means step 0 already sent, but day 2 < 5 for step 1
        assert step is None

    def test_day_3_followup_0_returns_step_0(self):
        """Days silent exceeds step threshold (>=2), so step 0 fires."""
        scheduler = FollowUpScheduler()
        step = scheduler._get_next_step(days_silent=3, followup_count=0)
        assert step is not None
        assert step.template == "gentle_reminder"


# ---------------------------------------------------------------------------
# Tests: _get_reference_time
# ---------------------------------------------------------------------------


class TestGetReferenceTime:
    """Test reference time selection for silence measurement."""

    def test_uses_outbound_message_time(self):
        neg = _make_negotiation()
        outbound_time = datetime.now(UTC) - timedelta(days=3)
        outbound = _make_client_message(created_at=outbound_time, direction="outbound")
        ref = FollowUpScheduler._get_reference_time(neg, outbound)
        assert ref == outbound_time

    def test_uses_neg_created_at_when_no_outbound(self):
        created = datetime.now(UTC) - timedelta(days=7)
        neg = _make_negotiation(created_at=created)
        ref = FollowUpScheduler._get_reference_time(neg, None)
        assert ref == created


# ---------------------------------------------------------------------------
# Tests: Archive as stale
# ---------------------------------------------------------------------------


class TestArchiveAsStale:
    """Test day 14 archiving behavior."""

    async def test_archive_sets_state_to_stale(self):
        scheduler = FollowUpScheduler()
        neg = _make_negotiation(state="qualifying", followup_count=3)
        bid = _make_bid()
        session = AsyncMock()

        result = await scheduler._archive_as_stale(session, neg, str(bid.id), 14)

        assert neg.state == "stale"
        assert neg.outcome == "stale"
        assert neg.resolved_at is not None
        assert neg.followup_count == 4
        assert isinstance(result, FollowUpResult)
        assert result.action == "archived"

    async def test_archive_preserves_previous_state(self):
        scheduler = FollowUpScheduler()
        neg = _make_negotiation(state="proposing", followup_count=3)
        bid = _make_bid()
        session = AsyncMock()

        await scheduler._archive_as_stale(session, neg, str(bid.id), 14)

        assert neg.previous_state == "proposing"


# ---------------------------------------------------------------------------
# Tests: Send followup message
# ---------------------------------------------------------------------------


class TestSendFollowupMessage:
    """Test follow-up message creation and counter updates."""

    async def test_sends_message_and_increments_count(self):
        scheduler = FollowUpScheduler()
        neg = _make_negotiation(followup_count=0)
        bid = _make_bid()
        job = _make_job(title="React App")
        step = FOLLOW_UP_SCHEDULE[0]  # gentle_reminder
        session = AsyncMock()
        session.add = MagicMock()

        result = await scheduler._send_followup_message(session, neg, bid, job, step, 2)

        assert isinstance(result, FollowUpResult)
        assert result.action == "sent"
        assert result.step == "gentle_reminder"
        assert neg.followup_count == 1
        assert neg.last_followup_at is not None
        session.add.assert_called_once()

    async def test_template_interpolation_with_job_title(self):
        scheduler = FollowUpScheduler()
        neg = _make_negotiation(followup_count=0)
        bid = _make_bid()
        job = _make_job(title="E-Commerce Site")
        step = FOLLOW_UP_SCHEDULE[0]
        session = AsyncMock()
        session.add = MagicMock()

        await scheduler._send_followup_message(session, neg, bid, job, step, 2)

        added_msg = session.add.call_args[0][0]
        assert "E-Commerce Site" in added_msg.content

    async def test_template_uses_fallback_when_no_job(self):
        scheduler = FollowUpScheduler()
        neg = _make_negotiation(followup_count=0)
        bid = _make_bid()
        step = FOLLOW_UP_SCHEDULE[0]
        session = AsyncMock()
        session.add = MagicMock()

        await scheduler._send_followup_message(session, neg, bid, None, step, 2)

        added_msg = session.add.call_args[0][0]
        assert "your project" in added_msg.content

    async def test_missing_template_returns_skipped(self):
        scheduler = FollowUpScheduler()
        neg = _make_negotiation(followup_count=0)
        bid = _make_bid()
        job = _make_job()
        # Custom step with a template not in FOLLOW_UP_TEMPLATES
        step = FollowUpStep(days=1, template="nonexistent_template", auto_send=True)
        session = AsyncMock()

        result = await scheduler._send_followup_message(session, neg, bid, job, step, 1)

        assert result.action == "skipped"


# ---------------------------------------------------------------------------
# Tests: Client reply since last outbound skips follow-up
# ---------------------------------------------------------------------------


class TestClientReplySkip:
    """Test that active conversation (client replied) skips follow-up."""

    async def test_client_replied_after_outbound_skips(self):
        scheduler = FollowUpScheduler()
        neg = _make_negotiation(state="qualifying", followup_count=0)
        bid = _make_bid()
        job = _make_job()
        session = AsyncMock()

        outbound = _make_client_message(
            created_at=datetime.now(UTC) - timedelta(days=3),
            direction="outbound",
        )
        client_reply = _make_client_message(
            created_at=datetime.now(UTC) - timedelta(days=1),
            direction="inbound",
        )

        # Mock the internal query methods directly to avoid SQL serialization issues
        scheduler._get_last_client_message = AsyncMock(return_value=client_reply)
        scheduler._get_last_outbound_message = AsyncMock(return_value=outbound)

        result = await scheduler._process_negotiation(session, neg, bid, job)

        # Should return None (skipped) because client replied after outbound
        assert result is None

    async def test_no_client_reply_proceeds_to_followup(self):
        scheduler = FollowUpScheduler()
        neg = _make_negotiation(
            state="qualifying",
            followup_count=0,
            created_at=datetime.now(UTC) - timedelta(days=3),
        )
        bid = _make_bid()
        job = _make_job()
        session = AsyncMock()
        session.add = MagicMock()

        outbound = _make_client_message(
            created_at=datetime.now(UTC) - timedelta(days=3),
            direction="outbound",
        )

        # No client reply at all
        scheduler._get_last_client_message = AsyncMock(return_value=None)
        scheduler._get_last_outbound_message = AsyncMock(return_value=outbound)

        result = await scheduler._process_negotiation(session, neg, bid, job)

        assert result is not None
        assert result.action == "sent"


# ---------------------------------------------------------------------------
# Tests: check_and_send integration
# ---------------------------------------------------------------------------


class TestCheckAndSend:
    """Test the main cron entry point."""

    async def test_returns_empty_when_no_eligible(self):
        scheduler = FollowUpScheduler()

        with patch("src.negotiations.followup.get_db_session") as mock_ctx:
            session = AsyncMock()
            result_mock = MagicMock()
            unique_mock = MagicMock()
            unique_mock.all.return_value = []
            result_mock.unique.return_value = unique_mock
            session.execute = AsyncMock(return_value=result_mock)
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            results = await scheduler.check_and_send()

        assert results == []


# ---------------------------------------------------------------------------
# Tests: Eligible states
# ---------------------------------------------------------------------------


class TestEligibleStates:
    """Test eligible and cancel state sets."""

    @pytest.mark.parametrize("state", ["initial", "qualifying", "proposing"])
    def test_eligible_states(self, state: str):
        assert state in _FOLLOWUP_ELIGIBLE_STATES

    @pytest.mark.parametrize("state", ["won", "lost", "stale", "accepted", "declined", "operator_override"])
    def test_cancel_states(self, state: str):
        assert state in _CANCEL_STATES

    def test_no_overlap_between_eligible_and_cancel(self):
        assert _FOLLOWUP_ELIGIBLE_STATES.isdisjoint(_CANCEL_STATES)
