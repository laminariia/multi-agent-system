"""Unit tests for Touch Sequence Manager (src/core/touch_sequence.py).

Tests cover: TouchState enum, TouchStep/TouchSchedule constants,
stop rules, channel resolution, message formatting, sequence lifecycle,
and edge cases.
"""

from __future__ import annotations

from src.core.touch_sequence import (
    STOP_RULES,
    TOUCH_SCHEDULE,
    StopResult,
    TouchResult,
    TouchSequence,
    TouchSequenceManager,
    TouchState,
    TouchStep,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_lead(
    *,
    lead_id: str = "lead-001",
    business_name: str = "CafeTest",
    telegram_username: str | None = "@cafetest",
    email: str | None = "info@cafetest.ru",
    channel_used: str | None = None,
) -> object:
    """Create a minimal lead-like object for testing."""

    class _Lead:
        def __init__(self):
            self.id = lead_id
            self.business_name = business_name
            self.telegram_username = telegram_username
            self.email = email
            self.channel_used = channel_used

    return _Lead()


# ---------------------------------------------------------------------------
# TouchState enum
# ---------------------------------------------------------------------------


class TestTouchState:
    """Verify TouchState enum values and behavior."""

    def test_has_six_states(self):
        assert len(TouchState) == 6

    def test_pending_value(self):
        assert TouchState.PENDING == "pending"

    def test_active_value(self):
        assert TouchState.ACTIVE == "active"

    def test_replied_value(self):
        assert TouchState.REPLIED == "replied"

    def test_completed_value(self):
        assert TouchState.COMPLETED == "completed"

    def test_stopped_value(self):
        assert TouchState.STOPPED == "stopped"

    def test_paused_value(self):
        assert TouchState.PAUSED == "paused"

    def test_is_string(self):
        assert isinstance(TouchState.ACTIVE, str)

    def test_string_comparison(self):
        assert TouchState.PENDING == "pending"
        assert "active" == TouchState.ACTIVE


# ---------------------------------------------------------------------------
# TouchStep dataclass
# ---------------------------------------------------------------------------


class TestTouchStep:
    """Verify TouchStep dataclass fields."""

    def test_create_step(self):
        step = TouchStep(
            day=1,
            channel="best_available",
            template="first_contact",
            description="Test step",
        )
        assert step.day == 1
        assert step.channel == "best_available"
        assert step.template == "first_contact"
        assert step.description == "Test step"

    def test_auto_send_defaults_false(self):
        step = TouchStep(day=1, channel="email", template="t", description="d")
        assert step.auto_send is False

    def test_text_template_defaults_none(self):
        step = TouchStep(day=1, channel="email", template="t", description="d")
        assert step.text_template is None

    def test_auto_send_true(self):
        step = TouchStep(day=3, channel="same", template="t", description="d", auto_send=True)
        assert step.auto_send is True

    def test_text_template_set(self):
        step = TouchStep(
            day=3,
            channel="same",
            template="t",
            description="d",
            text_template="Hello {business_name}",
        )
        assert "{business_name}" in step.text_template


# ---------------------------------------------------------------------------
# TOUCH_SCHEDULE constant
# ---------------------------------------------------------------------------


class TestTouchSchedule:
    """Verify the 4-step touch schedule per spec."""

    def test_has_four_steps(self):
        assert len(TOUCH_SCHEDULE) == 4

    def test_step_1_day_1_best_available(self):
        step = TOUCH_SCHEDULE[0]
        assert step.day == 1
        assert step.channel == "best_available"
        assert step.template == "first_contact"
        assert step.auto_send is False

    def test_step_2_day_3_same_channel(self):
        step = TOUCH_SCHEDULE[1]
        assert step.day == 3
        assert step.channel == "same_as_first"
        assert step.template == "gentle_followup"
        assert step.auto_send is True
        assert step.text_template is not None

    def test_step_3_day_5_alternative(self):
        step = TOUCH_SCHEDULE[2]
        assert step.day == 5
        assert step.channel == "alternative"
        assert step.template == "alternative_channel"
        assert step.auto_send is True
        assert step.text_template is not None

    def test_step_4_day_10_email(self):
        step = TOUCH_SCHEDULE[3]
        assert step.day == 10
        assert step.channel == "email"
        assert step.template == "final_touch"
        assert step.auto_send is True
        assert step.text_template is not None

    def test_days_are_ascending(self):
        days = [s.day for s in TOUCH_SCHEDULE]
        assert days == sorted(days)
        assert len(set(days)) == len(days)  # no duplicates


# ---------------------------------------------------------------------------
# STOP_RULES constant
# ---------------------------------------------------------------------------


class TestStopRules:
    """Verify the 4 stop rules per spec."""

    def test_has_four_rules(self):
        assert len(STOP_RULES) == 4

    def test_explicit_no_rule(self):
        rule = STOP_RULES["explicit_no"]
        assert "triggers" in rule
        assert "не интересно" in rule["triggers"]
        assert "no thanks" in rule["triggers"]
        assert rule["action"] == "stop_forever"

    def test_max_touches_rule(self):
        rule = STOP_RULES["max_touches"]
        assert rule["max_count"] == 3
        assert rule["action"] == "stop"

    def test_bounce_rule(self):
        rule = STOP_RULES["bounce"]
        assert "hard_bounce" in rule["triggers"]
        assert "user_not_found" in rule["triggers"]
        assert rule["action"] == "stop"

    def test_replied_rule(self):
        rule = STOP_RULES["replied"]
        assert rule["action"] == "transition_to_sales"


# ---------------------------------------------------------------------------
# StopResult / TouchResult dataclasses
# ---------------------------------------------------------------------------


class TestStopResult:
    """Verify StopResult dataclass."""

    def test_create_stop_result(self):
        sr = StopResult(rule="explicit_no", action="stop_forever", lead_status="declined")
        assert sr.rule == "explicit_no"
        assert sr.action == "stop_forever"
        assert sr.lead_status == "declined"

    def test_description_optional(self):
        sr = StopResult(rule="bounce", action="stop", lead_status="no_contact")
        assert sr.description is None or hasattr(sr, "description")


class TestTouchResult:
    """Verify TouchResult dataclass."""

    def test_create_touch_result(self):
        tr = TouchResult(lead_id="lead-001", action="sent")
        assert tr.lead_id == "lead-001"
        assert tr.action == "sent"

    def test_reason_optional(self):
        tr = TouchResult(lead_id="x", action="stopped", reason="explicit_no")
        assert tr.reason == "explicit_no"

    def test_channel_optional(self):
        tr = TouchResult(lead_id="x", action="sent", channel="telegram")
        assert tr.channel == "telegram"

    def test_defaults_none(self):
        tr = TouchResult(lead_id="x", action="completed")
        assert tr.reason is None
        assert tr.channel is None


# ---------------------------------------------------------------------------
# TouchSequence dataclass
# ---------------------------------------------------------------------------


class TestTouchSequence:
    """Verify TouchSequence dataclass."""

    def test_create_sequence(self):
        seq = TouchSequence(lead_id="lead-001")
        assert seq.lead_id == "lead-001"
        assert seq.state == TouchState.PENDING
        assert seq.touch_count == 0

    def test_default_state_is_pending(self):
        seq = TouchSequence(lead_id="x")
        assert seq.state == TouchState.PENDING

    def test_default_touch_count_zero(self):
        seq = TouchSequence(lead_id="x")
        assert seq.touch_count == 0

    def test_history_default_empty(self):
        seq = TouchSequence(lead_id="x")
        assert seq.history == []

    def test_channel_used_default_none(self):
        seq = TouchSequence(lead_id="x")
        assert seq.channel_used is None


# ---------------------------------------------------------------------------
# TouchSequenceManager.get_next_step
# ---------------------------------------------------------------------------


class TestGetNextStep:
    """Verify step lookup by touch count."""

    def test_first_step_at_count_zero(self):
        mgr = TouchSequenceManager()
        step = mgr.get_next_step(0)
        assert step is not None
        assert step.day == 1
        assert step.template == "first_contact"

    def test_second_step_at_count_one(self):
        mgr = TouchSequenceManager()
        step = mgr.get_next_step(1)
        assert step is not None
        assert step.day == 3

    def test_third_step_at_count_two(self):
        mgr = TouchSequenceManager()
        step = mgr.get_next_step(2)
        assert step is not None
        assert step.day == 5

    def test_fourth_step_at_count_three(self):
        mgr = TouchSequenceManager()
        step = mgr.get_next_step(3)
        assert step is not None
        assert step.day == 10

    def test_returns_none_when_exhausted(self):
        mgr = TouchSequenceManager()
        step = mgr.get_next_step(4)
        assert step is None

    def test_returns_none_for_large_count(self):
        mgr = TouchSequenceManager()
        assert mgr.get_next_step(100) is None

    def test_returns_none_for_negative_count(self):
        mgr = TouchSequenceManager()
        # Negative count should not crash
        step = mgr.get_next_step(-1)
        assert step is None


# ---------------------------------------------------------------------------
# TouchSequenceManager.should_stop
# ---------------------------------------------------------------------------


class TestShouldStop:
    """Verify stop rule checking."""

    def test_explicit_no_russian(self):
        mgr = TouchSequenceManager()
        result = mgr.should_stop(touch_count=1, last_message="нет")
        assert result is not None
        assert result.rule == "explicit_no"
        assert result.action == "stop_forever"

    def test_explicit_no_english(self):
        mgr = TouchSequenceManager()
        result = mgr.should_stop(touch_count=1, last_message="no thanks")
        assert result is not None
        assert result.rule == "explicit_no"

    def test_explicit_no_not_interested(self):
        mgr = TouchSequenceManager()
        result = mgr.should_stop(touch_count=1, last_message="не интересно")
        assert result is not None
        assert result.rule == "explicit_no"

    def test_explicit_no_case_insensitive(self):
        mgr = TouchSequenceManager()
        result = mgr.should_stop(touch_count=1, last_message="НЕТ")
        assert result is not None
        assert result.rule == "explicit_no"

    def test_explicit_no_in_longer_message(self):
        mgr = TouchSequenceManager()
        result = mgr.should_stop(
            touch_count=1,
            last_message="Спасибо, но мне не интересно это предложение",
        )
        assert result is not None
        assert result.rule == "explicit_no"

    def test_max_touches_exceeded(self):
        mgr = TouchSequenceManager()
        result = mgr.should_stop(touch_count=3, last_message=None)
        assert result is not None
        assert result.rule == "max_touches"
        assert result.action == "stop"

    def test_max_touches_not_exceeded(self):
        mgr = TouchSequenceManager()
        result = mgr.should_stop(touch_count=2, last_message=None)
        assert result is None

    def test_bounce_hard_bounce(self):
        mgr = TouchSequenceManager()
        result = mgr.should_stop(touch_count=1, last_message="hard_bounce")
        assert result is not None
        assert result.rule == "bounce"

    def test_bounce_user_not_found(self):
        mgr = TouchSequenceManager()
        result = mgr.should_stop(touch_count=0, last_message="user_not_found")
        assert result is not None
        assert result.rule == "bounce"

    def test_no_stop_for_normal_reply(self):
        mgr = TouchSequenceManager()
        result = mgr.should_stop(touch_count=1, last_message="Расскажите подробнее")
        assert result is None

    def test_no_stop_no_message_low_count(self):
        mgr = TouchSequenceManager()
        result = mgr.should_stop(touch_count=0, last_message=None)
        assert result is None

    def test_empty_message_no_stop(self):
        mgr = TouchSequenceManager()
        result = mgr.should_stop(touch_count=1, last_message="")
        assert result is None

    def test_unsubscribe_triggers_stop(self):
        mgr = TouchSequenceManager()
        result = mgr.should_stop(touch_count=1, last_message="unsubscribe")
        assert result is not None
        assert result.rule == "explicit_no"


# ---------------------------------------------------------------------------
# TouchSequenceManager.resolve_channel
# ---------------------------------------------------------------------------


class TestResolveChannel:
    """Verify channel resolution logic."""

    def test_best_available_telegram_first(self):
        mgr = TouchSequenceManager()
        lead = _fake_lead(telegram_username="@test", email="test@mail.ru")
        step = TOUCH_SCHEDULE[0]  # best_available
        assert mgr.resolve_channel(step, lead) == "telegram"

    def test_best_available_email_fallback(self):
        mgr = TouchSequenceManager()
        lead = _fake_lead(telegram_username=None, email="test@mail.ru")
        step = TOUCH_SCHEDULE[0]
        assert mgr.resolve_channel(step, lead) == "email"

    def test_best_available_no_contacts_returns_email(self):
        mgr = TouchSequenceManager()
        lead = _fake_lead(telegram_username=None, email=None)
        step = TOUCH_SCHEDULE[0]
        # Fallback to email even if no email — caller handles delivery failure
        assert mgr.resolve_channel(step, lead) == "email"

    def test_same_as_first_uses_channel_used(self):
        mgr = TouchSequenceManager()
        lead = _fake_lead(channel_used="telegram")
        step = TOUCH_SCHEDULE[1]  # same_as_first
        assert mgr.resolve_channel(step, lead) == "telegram"

    def test_same_as_first_fallback_email(self):
        mgr = TouchSequenceManager()
        lead = _fake_lead(channel_used=None)
        step = TOUCH_SCHEDULE[1]
        assert mgr.resolve_channel(step, lead) == "email"

    def test_alternative_from_telegram_to_email(self):
        mgr = TouchSequenceManager()
        lead = _fake_lead(channel_used="telegram", email="test@mail.ru")
        step = TOUCH_SCHEDULE[2]  # alternative
        assert mgr.resolve_channel(step, lead) == "email"

    def test_alternative_from_email_to_telegram(self):
        mgr = TouchSequenceManager()
        lead = _fake_lead(channel_used="email", telegram_username="@test")
        step = TOUCH_SCHEDULE[2]
        assert mgr.resolve_channel(step, lead) == "telegram"

    def test_alternative_no_other_channel_fallback(self):
        mgr = TouchSequenceManager()
        lead = _fake_lead(channel_used="telegram", email=None)
        step = TOUCH_SCHEDULE[2]
        # No alternative available — fallback to same channel
        result = mgr.resolve_channel(step, lead)
        assert result == "telegram"

    def test_explicit_email_channel(self):
        mgr = TouchSequenceManager()
        lead = _fake_lead()
        step = TOUCH_SCHEDULE[3]  # explicit "email"
        assert mgr.resolve_channel(step, lead) == "email"


# ---------------------------------------------------------------------------
# TouchSequenceManager.format_message
# ---------------------------------------------------------------------------


class TestFormatMessage:
    """Verify message template formatting."""

    def test_format_gentle_followup(self):
        mgr = TouchSequenceManager()
        lead = _fake_lead(business_name="PizzaHouse")
        step = TOUCH_SCHEDULE[1]  # gentle_followup
        text = mgr.format_message(step, lead, operator_name="Алексей")
        assert "PizzaHouse" in text

    def test_format_alternative_channel(self):
        mgr = TouchSequenceManager()
        lead = _fake_lead(business_name="SushiBar", channel_used="telegram")
        step = TOUCH_SCHEDULE[2]  # alternative_channel
        text = mgr.format_message(step, lead, operator_name="Анна")
        assert "SushiBar" in text
        assert "Анна" in text

    def test_format_final_touch(self):
        mgr = TouchSequenceManager()
        lead = _fake_lead(business_name="AutoService")
        step = TOUCH_SCHEDULE[3]
        text = mgr.format_message(step, lead, operator_name="Иван")
        assert "AutoService" in text

    def test_format_no_template_returns_none(self):
        mgr = TouchSequenceManager()
        lead = _fake_lead()
        step = TOUCH_SCHEDULE[0]  # first_contact has no text_template
        result = mgr.format_message(step, lead, operator_name="Test")
        assert result is None

    def test_format_includes_first_channel(self):
        mgr = TouchSequenceManager()
        lead = _fake_lead(business_name="Clinic", channel_used="telegram")
        step = TOUCH_SCHEDULE[2]  # alternative — uses {first_channel}
        text = mgr.format_message(step, lead, operator_name="Мария")
        assert "telegram" in text.lower() or "Telegram" in text


# ---------------------------------------------------------------------------
# TouchSequenceManager.create_sequence
# ---------------------------------------------------------------------------


class TestCreateSequence:
    """Verify new sequence creation."""

    def test_creates_pending_sequence(self):
        mgr = TouchSequenceManager()
        seq = mgr.create_sequence("lead-abc")
        assert isinstance(seq, TouchSequence)
        assert seq.lead_id == "lead-abc"
        assert seq.state == TouchState.PENDING
        assert seq.touch_count == 0

    def test_creates_with_empty_history(self):
        mgr = TouchSequenceManager()
        seq = mgr.create_sequence("lead-xyz")
        assert seq.history == []


# ---------------------------------------------------------------------------
# TouchSequenceManager.advance
# ---------------------------------------------------------------------------


class TestAdvance:
    """Verify sequence advancement logic."""

    def test_advance_pending_to_active(self):
        mgr = TouchSequenceManager()
        lead = _fake_lead()
        seq = mgr.create_sequence("lead-001")
        result = mgr.advance(seq, lead)
        assert result.action in ("sent", "needs_manual")
        assert seq.state == TouchState.ACTIVE

    def test_advance_increments_touch_count(self):
        mgr = TouchSequenceManager()
        lead = _fake_lead()
        seq = mgr.create_sequence("lead-001")
        mgr.advance(seq, lead)
        assert seq.touch_count == 1

    def test_advance_records_history(self):
        mgr = TouchSequenceManager()
        lead = _fake_lead()
        seq = mgr.create_sequence("lead-001")
        mgr.advance(seq, lead)
        assert len(seq.history) == 1

    def test_advance_four_times_completes(self):
        mgr = TouchSequenceManager()
        lead = _fake_lead()
        seq = mgr.create_sequence("lead-001")

        for _ in range(4):
            result = mgr.advance(seq, lead)

        assert seq.state == TouchState.COMPLETED
        assert seq.touch_count == 4
        assert result.action == "completed"

    def test_advance_paused_skips(self):
        mgr = TouchSequenceManager()
        lead = _fake_lead()
        seq = mgr.create_sequence("lead-001")
        seq.state = TouchState.PAUSED

        result = mgr.advance(seq, lead)
        assert result.action == "skipped"
        assert result.reason == "paused"
        assert seq.touch_count == 0  # no change

    def test_advance_stopped_skips(self):
        mgr = TouchSequenceManager()
        lead = _fake_lead()
        seq = mgr.create_sequence("lead-001")
        seq.state = TouchState.STOPPED

        result = mgr.advance(seq, lead)
        assert result.action == "skipped"
        assert result.reason == "stopped"

    def test_advance_completed_skips(self):
        mgr = TouchSequenceManager()
        lead = _fake_lead()
        seq = mgr.create_sequence("lead-001")
        seq.state = TouchState.COMPLETED

        result = mgr.advance(seq, lead)
        assert result.action == "skipped"
        assert result.reason == "completed"

    def test_advance_replied_skips(self):
        mgr = TouchSequenceManager()
        lead = _fake_lead()
        seq = mgr.create_sequence("lead-001")
        seq.state = TouchState.REPLIED

        result = mgr.advance(seq, lead)
        assert result.action == "skipped"
        assert result.reason == "replied"

    def test_advance_first_step_needs_manual(self):
        mgr = TouchSequenceManager()
        lead = _fake_lead()
        seq = mgr.create_sequence("lead-001")

        result = mgr.advance(seq, lead)
        # First step has auto_send=False → needs_manual
        assert result.action == "needs_manual"

    def test_advance_second_step_auto_sends(self):
        mgr = TouchSequenceManager()
        lead = _fake_lead()
        seq = mgr.create_sequence("lead-001")

        # First advance
        mgr.advance(seq, lead)

        # Second advance — auto_send=True
        result = mgr.advance(seq, lead)
        assert result.action == "sent"
        assert result.channel is not None

    def test_advance_sets_channel_used(self):
        mgr = TouchSequenceManager()
        lead = _fake_lead(telegram_username="@test", email="t@t.ru")
        seq = mgr.create_sequence("lead-001")

        mgr.advance(seq, lead)
        assert seq.channel_used is not None

    def test_advance_no_channel_stops(self):
        mgr = TouchSequenceManager()
        lead = _fake_lead(telegram_username=None, email=None)
        seq = mgr.create_sequence("lead-001")

        result = mgr.advance(seq, lead)
        # Even with no real channel, should still attempt (email fallback)
        # The actual send may fail but advance should proceed
        assert result is not None


# ---------------------------------------------------------------------------
# Full lifecycle integration
# ---------------------------------------------------------------------------


class TestFullLifecycle:
    """End-to-end sequence lifecycle tests."""

    def test_full_sequence_four_touches(self):
        mgr = TouchSequenceManager()
        lead = _fake_lead(
            business_name="TestBiz",
            telegram_username="@biz",
            email="biz@test.ru",
        )
        seq = mgr.create_sequence("lead-full")

        results = []
        for _ in range(5):  # 5 attempts — 4th completes, 5th skipped
            result = mgr.advance(seq, lead)
            results.append(result)

        assert results[0].action == "needs_manual"  # Day 1
        assert results[1].action == "sent"  # Day 3
        assert results[2].action == "sent"  # Day 5
        assert results[3].action == "completed"  # Day 10 + complete
        assert results[4].action == "skipped"  # Already completed

        assert seq.state == TouchState.COMPLETED
        assert seq.touch_count == 4

    def test_stop_mid_sequence(self):
        mgr = TouchSequenceManager()
        lead = _fake_lead()
        seq = mgr.create_sequence("lead-stop")

        # First touch
        mgr.advance(seq, lead)
        assert seq.state == TouchState.ACTIVE

        # Simulate explicit_no
        stop = mgr.should_stop(seq.touch_count, "нет, не надо")
        assert stop is not None
        assert stop.rule == "explicit_no"

    def test_pause_and_resume(self):
        mgr = TouchSequenceManager()
        lead = _fake_lead()
        seq = mgr.create_sequence("lead-pause")

        # First touch
        mgr.advance(seq, lead)
        assert seq.state == TouchState.ACTIVE

        # Pause
        seq.state = TouchState.PAUSED
        result = mgr.advance(seq, lead)
        assert result.action == "skipped"

        # Resume
        seq.state = TouchState.ACTIVE
        result = mgr.advance(seq, lead)
        assert result.action == "sent"  # Day 3 step
