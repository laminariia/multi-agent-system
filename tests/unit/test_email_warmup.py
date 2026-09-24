"""Comprehensive tests for the email warmup system.

Tests cover:
- WarmupStage enum and configuration
- WarmupManager stage progression (6 weeks)
- Daily send limits per stage
- Production readiness gate
- Bounce tracking and stage demotion
- record_send accounting
- Warmup template generation per stage
- Edge cases (boundary days, metric thresholds)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.enrichment.warmup import (
    BOUNCE_RED_LINE,
    STAGE_CONFIG,
    WarmupManager,
    WarmupRecord,
    WarmupStage,
)
from src.enrichment.warmup_templates import (
    WARMUP_TEMPLATES,
    get_warmup_template,
)

# ============================================================================
# Fixtures
# ============================================================================


def _make_record(**overrides: Any) -> WarmupRecord:
    """Create a WarmupRecord with sensible test defaults."""
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "domain": "outreach.example.com",
        "stage": WarmupStage.WEEK_1,
        "started_at": datetime.now(UTC),
        "stage_started_at": datetime.now(UTC),
        "total_sent": 0,
        "total_bounced": 0,
        "total_replies": 0,
        "daily_sent_today": 0,
        "last_send_date": None,
        "is_paused": False,
        "pause_reason": None,
    }
    defaults.update(overrides)
    return WarmupRecord(**defaults)


def _mock_session() -> AsyncMock:
    """Create a mock async SQLAlchemy session."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    return session


# ============================================================================
# WarmupStage enum
# ============================================================================


class TestWarmupStage:
    """Tests for the WarmupStage enum."""

    def test_stage_values(self):
        """WarmupStage has 7 stages: 6 weeks + production."""
        assert WarmupStage.WEEK_1 == "week_1"
        assert WarmupStage.WEEK_2 == "week_2"
        assert WarmupStage.WEEK_3 == "week_3"
        assert WarmupStage.WEEK_4 == "week_4"
        assert WarmupStage.WEEK_5 == "week_5"
        assert WarmupStage.WEEK_6 == "week_6"
        assert WarmupStage.PRODUCTION == "production"

    def test_stage_count(self):
        """There are exactly 7 stages."""
        assert len(WarmupStage) == 7

    def test_all_stages_have_config(self):
        """Every stage except PRODUCTION has a config entry."""
        for stage in WarmupStage:
            if stage != WarmupStage.PRODUCTION:
                assert stage in STAGE_CONFIG, f"Missing config for {stage}"


# ============================================================================
# STAGE_CONFIG
# ============================================================================


class TestStageConfig:
    """Tests for stage configuration constants."""

    def test_week_1_config(self):
        """Week 1: 5 emails/day, personal contacts."""
        cfg = STAGE_CONFIG[WarmupStage.WEEK_1]
        assert cfg["daily_limit"] == 5
        assert cfg["duration_days"] == 7
        assert cfg["audience"] == "personal"

    def test_week_2_config(self):
        """Week 2: 10 emails/day, personal + seed list."""
        cfg = STAGE_CONFIG[WarmupStage.WEEK_2]
        assert cfg["daily_limit"] == 10
        assert cfg["duration_days"] == 7
        assert cfg["audience"] == "personal_seed"

    def test_week_3_config(self):
        """Week 3: 15 emails/day, mixed."""
        cfg = STAGE_CONFIG[WarmupStage.WEEK_3]
        assert cfg["daily_limit"] == 15
        assert cfg["duration_days"] == 7
        assert cfg["audience"] == "mixed_warm"

    def test_week_4_config(self):
        """Week 4: 25 emails/day, mixed cold."""
        cfg = STAGE_CONFIG[WarmupStage.WEEK_4]
        assert cfg["daily_limit"] == 25
        assert cfg["duration_days"] == 7
        assert cfg["audience"] == "mixed_cold"

    def test_week_5_config(self):
        """Week 5: 35 emails/day, mostly cold."""
        cfg = STAGE_CONFIG[WarmupStage.WEEK_5]
        assert cfg["daily_limit"] == 35
        assert cfg["duration_days"] == 7
        assert cfg["audience"] == "mostly_cold"

    def test_week_6_config(self):
        """Week 6: 50 emails/day, full cold outreach."""
        cfg = STAGE_CONFIG[WarmupStage.WEEK_6]
        assert cfg["daily_limit"] == 50
        assert cfg["duration_days"] == 7
        assert cfg["audience"] == "cold_outreach"

    def test_limits_increase_monotonically(self):
        """Daily limits increase with each stage."""
        stages = [
            WarmupStage.WEEK_1,
            WarmupStage.WEEK_2,
            WarmupStage.WEEK_3,
            WarmupStage.WEEK_4,
            WarmupStage.WEEK_5,
            WarmupStage.WEEK_6,
        ]
        limits = [STAGE_CONFIG[s]["daily_limit"] for s in stages]
        for i in range(1, len(limits)):
            assert limits[i] > limits[i - 1], f"Limit did not increase at stage {i}"

    def test_bounce_red_line(self):
        """Bounce red line threshold is 5%."""
        assert BOUNCE_RED_LINE == 0.05


# ============================================================================
# WarmupManager — initialization
# ============================================================================


class TestWarmupManagerInit:
    """Tests for WarmupManager construction."""

    def test_init_with_session(self):
        """WarmupManager stores the session reference."""
        session = _mock_session()
        mgr = WarmupManager(session=session)
        assert mgr._session is session

    def test_init_with_domain(self):
        """WarmupManager stores the domain."""
        session = _mock_session()
        mgr = WarmupManager(session=session, domain="outreach.acme.com")
        assert mgr._domain == "outreach.acme.com"


# ============================================================================
# WarmupManager — is_production_ready
# ============================================================================


class TestIsProductionReady:
    """Tests for the production readiness gate."""

    @pytest.mark.asyncio
    async def test_production_ready_after_week_6(self):
        """Returns True when stage is PRODUCTION."""
        record = _make_record(stage=WarmupStage.PRODUCTION)
        session = _mock_session()
        mgr = WarmupManager(session=session, domain="test.com")

        result = await mgr.is_production_ready(record)
        assert result is True

    @pytest.mark.asyncio
    async def test_not_production_ready_during_warmup(self):
        """Returns False when still in warmup stages."""
        for stage in [
            WarmupStage.WEEK_1,
            WarmupStage.WEEK_2,
            WarmupStage.WEEK_3,
            WarmupStage.WEEK_4,
            WarmupStage.WEEK_5,
            WarmupStage.WEEK_6,
        ]:
            record = _make_record(stage=stage)
            session = _mock_session()
            mgr = WarmupManager(session=session, domain="test.com")
            result = await mgr.is_production_ready(record)
            assert result is False, f"Should not be production ready at {stage}"

    @pytest.mark.asyncio
    async def test_not_production_ready_when_paused(self):
        """Returns False when paused, even if at PRODUCTION stage."""
        record = _make_record(stage=WarmupStage.PRODUCTION, is_paused=True)
        session = _mock_session()
        mgr = WarmupManager(session=session, domain="test.com")
        result = await mgr.is_production_ready(record)
        assert result is False


# ============================================================================
# WarmupManager — get_daily_limit
# ============================================================================


class TestGetDailyLimit:
    """Tests for daily send limit per stage."""

    def test_week_1_limit(self):
        """Week 1 allows 5 emails per day."""
        record = _make_record(stage=WarmupStage.WEEK_1)
        mgr = WarmupManager(session=_mock_session(), domain="test.com")
        assert mgr.get_daily_limit(record) == 5

    def test_week_2_limit(self):
        """Week 2 allows 10 emails per day."""
        record = _make_record(stage=WarmupStage.WEEK_2)
        mgr = WarmupManager(session=_mock_session(), domain="test.com")
        assert mgr.get_daily_limit(record) == 10

    def test_week_3_limit(self):
        """Week 3 allows 15 emails per day."""
        record = _make_record(stage=WarmupStage.WEEK_3)
        mgr = WarmupManager(session=_mock_session(), domain="test.com")
        assert mgr.get_daily_limit(record) == 15

    def test_week_4_limit(self):
        """Week 4 allows 25 emails per day."""
        record = _make_record(stage=WarmupStage.WEEK_4)
        mgr = WarmupManager(session=_mock_session(), domain="test.com")
        assert mgr.get_daily_limit(record) == 25

    def test_week_5_limit(self):
        """Week 5 allows 35 emails per day."""
        record = _make_record(stage=WarmupStage.WEEK_5)
        mgr = WarmupManager(session=_mock_session(), domain="test.com")
        assert mgr.get_daily_limit(record) == 35

    def test_week_6_limit(self):
        """Week 6 allows 50 emails per day."""
        record = _make_record(stage=WarmupStage.WEEK_6)
        mgr = WarmupManager(session=_mock_session(), domain="test.com")
        assert mgr.get_daily_limit(record) == 50

    def test_production_limit(self):
        """Production stage allows 50 emails per day (same as week 6 default)."""
        record = _make_record(stage=WarmupStage.PRODUCTION)
        mgr = WarmupManager(session=_mock_session(), domain="test.com")
        assert mgr.get_daily_limit(record) == 50

    def test_paused_limit_is_zero(self):
        """Paused warmup returns 0 daily limit."""
        record = _make_record(stage=WarmupStage.WEEK_3, is_paused=True)
        mgr = WarmupManager(session=_mock_session(), domain="test.com")
        assert mgr.get_daily_limit(record) == 0


# ============================================================================
# WarmupManager — can_send_today
# ============================================================================


class TestCanSendToday:
    """Tests for the can_send_today check."""

    def test_can_send_when_under_limit(self):
        """Returns True when daily_sent_today < daily_limit."""
        record = _make_record(
            stage=WarmupStage.WEEK_1,
            daily_sent_today=3,
            last_send_date=datetime.now(UTC).date(),
        )
        mgr = WarmupManager(session=_mock_session(), domain="test.com")
        assert mgr.can_send_today(record) is True

    def test_cannot_send_when_at_limit(self):
        """Returns False when daily_sent_today >= daily_limit."""
        record = _make_record(
            stage=WarmupStage.WEEK_1,
            daily_sent_today=5,
            last_send_date=datetime.now(UTC).date(),
        )
        mgr = WarmupManager(session=_mock_session(), domain="test.com")
        assert mgr.can_send_today(record) is False

    def test_cannot_send_when_over_limit(self):
        """Returns False when daily_sent_today exceeds daily_limit."""
        record = _make_record(
            stage=WarmupStage.WEEK_1,
            daily_sent_today=10,
            last_send_date=datetime.now(UTC).date(),
        )
        mgr = WarmupManager(session=_mock_session(), domain="test.com")
        assert mgr.can_send_today(record) is False

    def test_resets_counter_on_new_day(self):
        """Returns True when last_send_date is yesterday (counter resets)."""
        yesterday = (datetime.now(UTC) - timedelta(days=1)).date()
        record = _make_record(
            stage=WarmupStage.WEEK_1,
            daily_sent_today=5,
            last_send_date=yesterday,
        )
        mgr = WarmupManager(session=_mock_session(), domain="test.com")
        assert mgr.can_send_today(record) is True

    def test_can_send_first_day_no_last_send(self):
        """Returns True on first day (last_send_date is None)."""
        record = _make_record(
            stage=WarmupStage.WEEK_1,
            daily_sent_today=0,
            last_send_date=None,
        )
        mgr = WarmupManager(session=_mock_session(), domain="test.com")
        assert mgr.can_send_today(record) is True

    def test_cannot_send_when_paused(self):
        """Returns False when warmup is paused."""
        record = _make_record(
            stage=WarmupStage.WEEK_3,
            daily_sent_today=0,
            is_paused=True,
        )
        mgr = WarmupManager(session=_mock_session(), domain="test.com")
        assert mgr.can_send_today(record) is False


# ============================================================================
# WarmupManager — record_send
# ============================================================================


class TestRecordSend:
    """Tests for recording email sends."""

    @pytest.mark.asyncio
    async def test_record_send_increments_counters(self):
        """record_send increments total_sent and daily_sent_today."""
        record = _make_record(total_sent=10, daily_sent_today=2)
        session = _mock_session()
        mgr = WarmupManager(session=session, domain="test.com")

        updated = await mgr.record_send(record, bounced=False)

        assert updated.total_sent == 11
        assert updated.daily_sent_today == 3

    @pytest.mark.asyncio
    async def test_record_send_updates_last_send_date(self):
        """record_send sets last_send_date to today."""
        record = _make_record(last_send_date=None)
        session = _mock_session()
        mgr = WarmupManager(session=session, domain="test.com")

        updated = await mgr.record_send(record, bounced=False)

        assert updated.last_send_date == datetime.now(UTC).date()

    @pytest.mark.asyncio
    async def test_record_send_bounce_increments_bounce_counter(self):
        """record_send with bounced=True increments total_bounced."""
        record = _make_record(total_bounced=1)
        session = _mock_session()
        mgr = WarmupManager(session=session, domain="test.com")

        updated = await mgr.record_send(record, bounced=True)

        assert updated.total_bounced == 2
        assert updated.total_sent == 1  # Still counts as sent

    @pytest.mark.asyncio
    async def test_record_send_reply_increments_reply_counter(self):
        """record_send with replied=True increments total_replies."""
        record = _make_record(total_replies=5)
        session = _mock_session()
        mgr = WarmupManager(session=session, domain="test.com")

        updated = await mgr.record_send(record, bounced=False, replied=True)

        assert updated.total_replies == 6

    @pytest.mark.asyncio
    async def test_record_send_resets_daily_on_new_day(self):
        """record_send resets daily counter when date changes."""
        yesterday = (datetime.now(UTC) - timedelta(days=1)).date()
        record = _make_record(daily_sent_today=5, last_send_date=yesterday)
        session = _mock_session()
        mgr = WarmupManager(session=session, domain="test.com")

        updated = await mgr.record_send(record, bounced=False)

        assert updated.daily_sent_today == 1  # Reset + 1
        assert updated.last_send_date == datetime.now(UTC).date()

    @pytest.mark.asyncio
    async def test_record_send_flushes_session(self):
        """record_send flushes the DB session."""
        record = _make_record()
        session = _mock_session()
        mgr = WarmupManager(session=session, domain="test.com")

        await mgr.record_send(record, bounced=False)

        session.flush.assert_awaited_once()


# ============================================================================
# WarmupManager — bounce rate and auto-pause
# ============================================================================


class TestBounceTracking:
    """Tests for bounce rate calculation and auto-pause."""

    def test_bounce_rate_zero_sends(self):
        """Bounce rate is 0.0 when no emails have been sent."""
        record = _make_record(total_sent=0, total_bounced=0)
        mgr = WarmupManager(session=_mock_session(), domain="test.com")
        assert mgr.get_bounce_rate(record) == 0.0

    def test_bounce_rate_calculation(self):
        """Bounce rate is total_bounced / total_sent."""
        record = _make_record(total_sent=100, total_bounced=3)
        mgr = WarmupManager(session=_mock_session(), domain="test.com")
        assert mgr.get_bounce_rate(record) == pytest.approx(0.03)

    def test_bounce_rate_at_red_line(self):
        """Bounce rate at exactly 5% hits the red line."""
        record = _make_record(total_sent=100, total_bounced=5)
        mgr = WarmupManager(session=_mock_session(), domain="test.com")
        assert mgr.get_bounce_rate(record) == pytest.approx(0.05)
        assert mgr.should_pause_for_bounces(record) is True

    def test_bounce_below_red_line(self):
        """Bounce rate below 5% does not trigger pause."""
        record = _make_record(total_sent=100, total_bounced=4)
        mgr = WarmupManager(session=_mock_session(), domain="test.com")
        assert mgr.should_pause_for_bounces(record) is False

    def test_bounce_above_red_line(self):
        """Bounce rate above 5% triggers pause."""
        record = _make_record(total_sent=100, total_bounced=6)
        mgr = WarmupManager(session=_mock_session(), domain="test.com")
        assert mgr.should_pause_for_bounces(record) is True

    @pytest.mark.asyncio
    async def test_auto_pause_on_high_bounce_rate(self):
        """record_send auto-pauses when bounce rate exceeds red line."""
        record = _make_record(total_sent=99, total_bounced=5)
        session = _mock_session()
        mgr = WarmupManager(session=session, domain="test.com")

        # This bounce brings us to 6/100 = 6% > 5% red line
        updated = await mgr.record_send(record, bounced=True)

        assert updated.is_paused is True
        assert updated.pause_reason == "bounce_rate_exceeded"

    @pytest.mark.asyncio
    async def test_no_auto_pause_below_threshold(self):
        """record_send does not auto-pause when bounce rate is acceptable."""
        record = _make_record(total_sent=99, total_bounced=2)
        session = _mock_session()
        mgr = WarmupManager(session=session, domain="test.com")

        updated = await mgr.record_send(record, bounced=True)

        assert updated.is_paused is False


# ============================================================================
# WarmupManager — advance_stage
# ============================================================================


class TestAdvanceStage:
    """Tests for stage advancement."""

    @pytest.mark.asyncio
    async def test_advance_week_1_to_week_2(self):
        """advance_stage moves from WEEK_1 to WEEK_2 after 7 days."""
        started = datetime.now(UTC) - timedelta(days=8)
        record = _make_record(
            stage=WarmupStage.WEEK_1,
            stage_started_at=started,
        )
        session = _mock_session()
        mgr = WarmupManager(session=session, domain="test.com")

        updated = await mgr.advance_stage(record)

        assert updated.stage == WarmupStage.WEEK_2

    @pytest.mark.asyncio
    async def test_advance_week_2_to_week_3(self):
        """advance_stage moves from WEEK_2 to WEEK_3 after 7 days."""
        started = datetime.now(UTC) - timedelta(days=8)
        record = _make_record(
            stage=WarmupStage.WEEK_2,
            stage_started_at=started,
        )
        session = _mock_session()
        mgr = WarmupManager(session=session, domain="test.com")

        updated = await mgr.advance_stage(record)

        assert updated.stage == WarmupStage.WEEK_3

    @pytest.mark.asyncio
    async def test_advance_week_6_to_production(self):
        """advance_stage moves from WEEK_6 to PRODUCTION after 7 days."""
        started = datetime.now(UTC) - timedelta(days=8)
        record = _make_record(
            stage=WarmupStage.WEEK_6,
            stage_started_at=started,
        )
        session = _mock_session()
        mgr = WarmupManager(session=session, domain="test.com")

        updated = await mgr.advance_stage(record)

        assert updated.stage == WarmupStage.PRODUCTION

    @pytest.mark.asyncio
    async def test_no_advance_before_duration(self):
        """advance_stage does not advance if duration not yet elapsed."""
        started = datetime.now(UTC) - timedelta(days=3)
        record = _make_record(
            stage=WarmupStage.WEEK_1,
            stage_started_at=started,
        )
        session = _mock_session()
        mgr = WarmupManager(session=session, domain="test.com")

        updated = await mgr.advance_stage(record)

        assert updated.stage == WarmupStage.WEEK_1  # Unchanged

    @pytest.mark.asyncio
    async def test_advance_at_exactly_7_days(self):
        """advance_stage advances at exactly 7 days (full duration completed)."""
        started = datetime.now(UTC) - timedelta(days=7)
        record = _make_record(
            stage=WarmupStage.WEEK_1,
            stage_started_at=started,
        )
        session = _mock_session()
        mgr = WarmupManager(session=session, domain="test.com")

        updated = await mgr.advance_stage(record)

        assert updated.stage == WarmupStage.WEEK_2

    @pytest.mark.asyncio
    async def test_no_advance_when_paused(self):
        """advance_stage does not advance when warmup is paused."""
        started = datetime.now(UTC) - timedelta(days=10)
        record = _make_record(
            stage=WarmupStage.WEEK_3,
            stage_started_at=started,
            is_paused=True,
        )
        session = _mock_session()
        mgr = WarmupManager(session=session, domain="test.com")

        updated = await mgr.advance_stage(record)

        assert updated.stage == WarmupStage.WEEK_3  # Not advanced

    @pytest.mark.asyncio
    async def test_no_advance_when_bounce_rate_high(self):
        """advance_stage does not advance when bounce rate exceeds red line."""
        started = datetime.now(UTC) - timedelta(days=10)
        record = _make_record(
            stage=WarmupStage.WEEK_2,
            stage_started_at=started,
            total_sent=100,
            total_bounced=6,  # 6% > 5%
        )
        session = _mock_session()
        mgr = WarmupManager(session=session, domain="test.com")

        updated = await mgr.advance_stage(record)

        assert updated.stage == WarmupStage.WEEK_2  # Not advanced

    @pytest.mark.asyncio
    async def test_advance_resets_stage_started_at(self):
        """advance_stage updates stage_started_at when advancing."""
        started = datetime.now(UTC) - timedelta(days=10)
        record = _make_record(
            stage=WarmupStage.WEEK_1,
            stage_started_at=started,
        )
        session = _mock_session()
        mgr = WarmupManager(session=session, domain="test.com")

        before = datetime.now(UTC)
        updated = await mgr.advance_stage(record)
        after = datetime.now(UTC)

        assert updated.stage_started_at >= before
        assert updated.stage_started_at <= after

    @pytest.mark.asyncio
    async def test_advance_production_stays_production(self):
        """advance_stage is a no-op when already at PRODUCTION."""
        started = datetime.now(UTC) - timedelta(days=100)
        record = _make_record(
            stage=WarmupStage.PRODUCTION,
            stage_started_at=started,
        )
        session = _mock_session()
        mgr = WarmupManager(session=session, domain="test.com")

        updated = await mgr.advance_stage(record)

        assert updated.stage == WarmupStage.PRODUCTION

    @pytest.mark.asyncio
    async def test_advance_flushes_session(self):
        """advance_stage flushes the DB session when stage changes."""
        started = datetime.now(UTC) - timedelta(days=8)
        record = _make_record(
            stage=WarmupStage.WEEK_1,
            stage_started_at=started,
        )
        session = _mock_session()
        mgr = WarmupManager(session=session, domain="test.com")

        await mgr.advance_stage(record)

        session.flush.assert_awaited()


# ============================================================================
# WarmupManager — full progression (6 stages)
# ============================================================================


class TestFullProgression:
    """Tests for full 6-week warmup progression."""

    @pytest.mark.asyncio
    async def test_full_6_week_progression(self):
        """Verify stage progression through all 6 weeks to production."""
        expected_order = [
            WarmupStage.WEEK_1,
            WarmupStage.WEEK_2,
            WarmupStage.WEEK_3,
            WarmupStage.WEEK_4,
            WarmupStage.WEEK_5,
            WarmupStage.WEEK_6,
            WarmupStage.PRODUCTION,
        ]

        session = _mock_session()
        mgr = WarmupManager(session=session, domain="test.com")

        record = _make_record(
            stage=WarmupStage.WEEK_1,
            stage_started_at=datetime.now(UTC) - timedelta(days=8),
        )

        stages_visited = [record.stage]

        for _ in range(6):  # 6 transitions: W1->W2->W3->W4->W5->W6->PROD
            record = await mgr.advance_stage(record)
            stages_visited.append(record.stage)
            # Simulate time passing for next stage
            record.stage_started_at = datetime.now(UTC) - timedelta(days=8)

        assert stages_visited == expected_order

    @pytest.mark.asyncio
    async def test_days_in_stage(self):
        """get_days_in_stage returns correct elapsed days."""
        started = datetime.now(UTC) - timedelta(days=5, hours=12)
        record = _make_record(stage_started_at=started)
        mgr = WarmupManager(session=_mock_session(), domain="test.com")

        days = mgr.get_days_in_stage(record)
        assert days == 5  # Floor of 5.5


# ============================================================================
# WarmupManager — pause / resume
# ============================================================================


class TestPauseResume:
    """Tests for pausing and resuming warmup."""

    @pytest.mark.asyncio
    async def test_pause_sets_flag(self):
        """pause_warmup sets is_paused=True and stores reason."""
        record = _make_record(is_paused=False)
        session = _mock_session()
        mgr = WarmupManager(session=session, domain="test.com")

        updated = await mgr.pause_warmup(record, reason="manual_review")

        assert updated.is_paused is True
        assert updated.pause_reason == "manual_review"

    @pytest.mark.asyncio
    async def test_resume_clears_flag(self):
        """resume_warmup clears is_paused and pause_reason."""
        record = _make_record(is_paused=True, pause_reason="bounce_rate_exceeded")
        session = _mock_session()
        mgr = WarmupManager(session=session, domain="test.com")

        updated = await mgr.resume_warmup(record)

        assert updated.is_paused is False
        assert updated.pause_reason is None

    @pytest.mark.asyncio
    async def test_resume_demotes_stage_after_bounce_pause(self):
        """resume_warmup demotes to previous stage after bounce-related pause."""
        record = _make_record(
            stage=WarmupStage.WEEK_4,
            is_paused=True,
            pause_reason="bounce_rate_exceeded",
        )
        session = _mock_session()
        mgr = WarmupManager(session=session, domain="test.com")

        updated = await mgr.resume_warmup(record)

        assert updated.stage == WarmupStage.WEEK_3  # Demoted
        assert updated.is_paused is False

    @pytest.mark.asyncio
    async def test_resume_demote_from_week_1_stays_week_1(self):
        """resume_warmup at WEEK_1 cannot demote further."""
        record = _make_record(
            stage=WarmupStage.WEEK_1,
            is_paused=True,
            pause_reason="bounce_rate_exceeded",
        )
        session = _mock_session()
        mgr = WarmupManager(session=session, domain="test.com")

        updated = await mgr.resume_warmup(record)

        assert updated.stage == WarmupStage.WEEK_1  # Stays at WEEK_1

    @pytest.mark.asyncio
    async def test_resume_no_demote_for_manual_pause(self):
        """resume_warmup does not demote for non-bounce pauses."""
        record = _make_record(
            stage=WarmupStage.WEEK_4,
            is_paused=True,
            pause_reason="manual_review",
        )
        session = _mock_session()
        mgr = WarmupManager(session=session, domain="test.com")

        updated = await mgr.resume_warmup(record)

        assert updated.stage == WarmupStage.WEEK_4  # No demotion


# ============================================================================
# WarmupManager — get_status
# ============================================================================


class TestGetStatus:
    """Tests for the warmup status summary."""

    def test_status_dict_contains_all_fields(self):
        """get_status returns a dict with all expected fields."""
        record = _make_record(
            stage=WarmupStage.WEEK_3,
            total_sent=45,
            total_bounced=1,
            total_replies=8,
            daily_sent_today=7,
        )
        mgr = WarmupManager(session=_mock_session(), domain="test.com")

        status = mgr.get_status(record)

        assert status["domain"] == "outreach.example.com"
        assert status["stage"] == "week_3"
        assert status["daily_limit"] == 15
        assert status["daily_sent_today"] == 7
        assert status["total_sent"] == 45
        assert status["total_bounced"] == 1
        assert status["total_replies"] == 8
        assert status["bounce_rate"] == pytest.approx(1 / 45)
        assert status["is_paused"] is False
        assert "is_production_ready" in status
        assert "days_in_stage" in status


# ============================================================================
# WarmupManager — create_warmup
# ============================================================================


class TestCreateWarmup:
    """Tests for creating a new warmup record."""

    @pytest.mark.asyncio
    async def test_create_warmup_initializes_record(self):
        """create_warmup creates a new WarmupRecord at WEEK_1."""
        session = _mock_session()
        mgr = WarmupManager(session=session, domain="outreach.acme.com")

        record = await mgr.create_warmup()

        assert record.domain == "outreach.acme.com"
        assert record.stage == WarmupStage.WEEK_1
        assert record.total_sent == 0
        assert record.total_bounced == 0
        assert record.total_replies == 0
        assert record.daily_sent_today == 0
        assert record.is_paused is False

    @pytest.mark.asyncio
    async def test_create_warmup_adds_to_session(self):
        """create_warmup adds the record to the DB session."""
        session = _mock_session()
        mgr = WarmupManager(session=session, domain="outreach.acme.com")

        await mgr.create_warmup()

        session.add.assert_called_once()
        session.flush.assert_awaited_once()


# ============================================================================
# Warmup Templates
# ============================================================================


class TestWarmupTemplates:
    """Tests for warmup email templates."""

    def test_templates_exist_for_all_warmup_stages(self):
        """WARMUP_TEMPLATES has entries for all 6 warmup stages."""
        for stage in [
            WarmupStage.WEEK_1,
            WarmupStage.WEEK_2,
            WarmupStage.WEEK_3,
            WarmupStage.WEEK_4,
            WarmupStage.WEEK_5,
            WarmupStage.WEEK_6,
        ]:
            assert stage in WARMUP_TEMPLATES, f"Missing template for {stage}"

    def test_each_stage_has_multiple_templates(self):
        """Each stage has at least 2 template variations."""
        for stage, templates in WARMUP_TEMPLATES.items():
            assert len(templates) >= 2, f"Stage {stage} needs at least 2 templates"

    def test_template_structure(self):
        """Each template has subject and body fields."""
        for stage, templates in WARMUP_TEMPLATES.items():
            for i, tmpl in enumerate(templates):
                assert "subject" in tmpl, f"Stage {stage} template {i} missing 'subject'"
                assert "body" in tmpl, f"Stage {stage} template {i} missing 'body'"
                assert len(tmpl["subject"]) > 0, f"Stage {stage} template {i} has empty subject"
                assert len(tmpl["body"]) > 0, f"Stage {stage} template {i} has empty body"

    def test_get_warmup_template_returns_valid(self):
        """get_warmup_template returns a dict with subject and body."""
        tmpl = get_warmup_template(WarmupStage.WEEK_1)
        assert "subject" in tmpl
        assert "body" in tmpl

    def test_get_warmup_template_all_stages(self):
        """get_warmup_template works for all warmup stages."""
        for stage in [
            WarmupStage.WEEK_1,
            WarmupStage.WEEK_2,
            WarmupStage.WEEK_3,
            WarmupStage.WEEK_4,
            WarmupStage.WEEK_5,
            WarmupStage.WEEK_6,
        ]:
            tmpl = get_warmup_template(stage)
            assert isinstance(tmpl, dict)
            assert len(tmpl["subject"]) > 0
            assert len(tmpl["body"]) > 0

    def test_get_warmup_template_production_raises(self):
        """get_warmup_template raises ValueError for PRODUCTION stage."""
        with pytest.raises(ValueError, match="No warmup templates"):
            get_warmup_template(WarmupStage.PRODUCTION)

    def test_early_stage_templates_are_personal(self):
        """Week 1-2 templates should be reply-worthy personal content."""
        for stage in [WarmupStage.WEEK_1, WarmupStage.WEEK_2]:
            templates = WARMUP_TEMPLATES[stage]
            for tmpl in templates:
                # Should not be overly salesy
                body_lower = tmpl["body"].lower()
                assert "buy" not in body_lower or "free trial" not in body_lower

    def test_later_stage_templates_are_outreach(self):
        """Week 5-6 templates contain business outreach language."""
        for stage in [WarmupStage.WEEK_5, WarmupStage.WEEK_6]:
            templates = WARMUP_TEMPLATES[stage]
            # At least one template should mention business/services/website
            bodies = " ".join(t["body"].lower() for t in templates)
            assert any(keyword in bodies for keyword in ["business", "service", "website", "project", "team"]), (
                f"Stage {stage} templates should contain outreach language"
            )

    def test_template_subjects_not_spammy(self):
        """Template subjects should avoid common spam trigger words."""
        spam_triggers = ["FREE", "URGENT", "ACT NOW", "LIMITED TIME", "!!!"]
        for stage, templates in WARMUP_TEMPLATES.items():
            for tmpl in templates:
                for trigger in spam_triggers:
                    assert trigger not in tmpl["subject"], f"Stage {stage} subject contains spam trigger: {trigger}"

    def test_template_personalization_placeholders(self):
        """Templates should use {{first_name}} or {{company}} placeholders for personalization."""
        for stage in [WarmupStage.WEEK_3, WarmupStage.WEEK_4, WarmupStage.WEEK_5, WarmupStage.WEEK_6]:
            templates = WARMUP_TEMPLATES[stage]
            has_placeholder = any(
                "{{first_name}}" in t["body"] or "{{company}}" in t["body"] or "{{city}}" in t["body"]
                for t in templates
            )
            assert has_placeholder, f"Stage {stage} should have personalization placeholders"


# ============================================================================
# WarmupRecord dataclass
# ============================================================================


class TestWarmupRecord:
    """Tests for the WarmupRecord data class."""

    def test_record_creation(self):
        """WarmupRecord stores all fields."""
        record = _make_record(
            domain="test.com",
            stage=WarmupStage.WEEK_3,
            total_sent=50,
        )
        assert record.domain == "test.com"
        assert record.stage == WarmupStage.WEEK_3
        assert record.total_sent == 50

    def test_record_defaults(self):
        """WarmupRecord has correct defaults via _make_record."""
        record = _make_record()
        assert record.total_sent == 0
        assert record.total_bounced == 0
        assert record.total_replies == 0
        assert record.daily_sent_today == 0
        assert record.is_paused is False
        assert record.pause_reason is None


# ============================================================================
# Edge cases
# ============================================================================


class TestEdgeCases:
    """Edge case tests."""

    @pytest.mark.asyncio
    async def test_record_send_at_exact_limit_blocks_next(self):
        """After sending up to the limit, next can_send_today returns False."""
        session = _mock_session()
        mgr = WarmupManager(session=session, domain="test.com")
        record = _make_record(
            stage=WarmupStage.WEEK_1,
            daily_sent_today=4,
            last_send_date=datetime.now(UTC).date(),
        )

        # Send the 5th (limit for WEEK_1)
        updated = await mgr.record_send(record, bounced=False)
        assert updated.daily_sent_today == 5
        assert mgr.can_send_today(updated) is False

    def test_bounce_rate_with_one_send(self):
        """Bounce rate works with a single send."""
        record = _make_record(total_sent=1, total_bounced=1)
        mgr = WarmupManager(session=_mock_session(), domain="test.com")
        assert mgr.get_bounce_rate(record) == 1.0

    @pytest.mark.asyncio
    async def test_advance_all_stages_sequentially(self):
        """Verify STAGE_ORDER ordering matches the expected sequence."""
        from src.enrichment.warmup import STAGE_ORDER

        expected = [
            WarmupStage.WEEK_1,
            WarmupStage.WEEK_2,
            WarmupStage.WEEK_3,
            WarmupStage.WEEK_4,
            WarmupStage.WEEK_5,
            WarmupStage.WEEK_6,
            WarmupStage.PRODUCTION,
        ]
        assert STAGE_ORDER == expected

    def test_get_daily_limit_unknown_stage_fallback(self):
        """Production stage returns the default 50 limit."""
        record = _make_record(stage=WarmupStage.PRODUCTION)
        mgr = WarmupManager(session=_mock_session(), domain="test.com")
        assert mgr.get_daily_limit(record) == 50
