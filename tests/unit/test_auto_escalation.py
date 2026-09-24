"""Tests for automatic HITL escalation (src/core/auto_escalation).

Covers:
- Each escalation function creates correct HITL entries
- Threshold enforcement (below-threshold does NOT escalate)
- Duplicate prevention (second call is a no-op)
- Payload content correctness
- Campaign pause on bounce rate
- LLM failure tracking helper
- Fire-and-forget helper
- Reset/get helpers
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.auto_escalation import (
    _background_tasks,
    _consecutive_failures,
    _has_pending_escalation,
    escalate_agent_crash,
    escalate_bounce_rate,
    escalate_capacity_overflow,
    escalate_llm_failure,
    escalate_platform_ban,
    fire_and_forget_escalation,
    get_consecutive_failures,
    reset_failure_tracking,
    track_llm_result,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class FakeSession:
    """Lightweight fake async DB session for testing."""

    def __init__(self, *, has_duplicate: bool = False) -> None:
        self._added: list[Any] = []
        self._has_duplicate = has_duplicate
        self._execute_result = MagicMock()
        self._execute_result.scalar.return_value = 1 if has_duplicate else 0

    def add(self, obj: Any) -> None:
        self._added.append(obj)

    async def execute(self, stmt: Any) -> Any:
        return self._execute_result

    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def flush(self) -> None:
        pass

    @property
    def added_items(self) -> list[Any]:
        return self._added


@asynccontextmanager
async def fake_db_session(*, has_duplicate: bool = False):
    session = FakeSession(has_duplicate=has_duplicate)
    yield session


@pytest.fixture(autouse=True)
def _reset_tracking():
    """Reset module-level state between tests."""
    _consecutive_failures.clear()
    _background_tasks.clear()
    yield
    _consecutive_failures.clear()
    _background_tasks.clear()


# ---------------------------------------------------------------------------
# Platform ban escalation
# ---------------------------------------------------------------------------


class TestEscalatePlatformBan:
    """Tests for escalate_platform_ban."""

    @pytest.mark.asyncio
    async def test_creates_hitl_entry(self) -> None:
        with patch("src.core.auto_escalation.get_db_session", side_effect=lambda: fake_db_session()):
            result = await escalate_platform_ban("freelancer", account_id="acc-123", error_details="banned")

        assert result is not None
        # Should be a valid UUID string
        uuid.UUID(result)

    @pytest.mark.asyncio
    async def test_payload_contains_platform(self) -> None:
        captured_session: FakeSession | None = None

        @asynccontextmanager
        async def capture_session():
            nonlocal captured_session
            captured_session = FakeSession()
            yield captured_session

        with patch("src.core.auto_escalation.get_db_session", side_effect=capture_session):
            await escalate_platform_ban("kwork", account_id="acc-456", error_details="suspended")

        assert captured_session is not None
        assert len(captured_session.added_items) == 1
        hitl = captured_session.added_items[0]
        assert hitl.type == "alert"
        assert hitl.priority == "urgent"
        assert hitl.payload["platform"] == "kwork"
        assert hitl.payload["account_id"] == "acc-456"
        assert hitl.payload["escalation_source"] == "auto_escalation"
        assert "switch_account" in hitl.available_actions

    @pytest.mark.asyncio
    async def test_duplicate_prevention(self) -> None:
        with patch(
            "src.core.auto_escalation.get_db_session",
            side_effect=lambda: fake_db_session(has_duplicate=True),
        ):
            result = await escalate_platform_ban("freelancer")

        assert result is None

    @pytest.mark.asyncio
    async def test_error_details_truncated(self) -> None:
        captured_session: FakeSession | None = None

        @asynccontextmanager
        async def capture_session():
            nonlocal captured_session
            captured_session = FakeSession()
            yield captured_session

        long_error = "x" * 1000
        with patch("src.core.auto_escalation.get_db_session", side_effect=capture_session):
            await escalate_platform_ban("upwork", error_details=long_error)

        assert captured_session is not None
        hitl = captured_session.added_items[0]
        assert len(hitl.payload["error_details"]) == 500

    @pytest.mark.asyncio
    async def test_returns_none_on_db_error(self) -> None:
        @asynccontextmanager
        async def failing_session():
            raise RuntimeError("DB down")
            yield  # noqa: RET503 -- unreachable but required for generator

        with patch("src.core.auto_escalation.get_db_session", side_effect=failing_session):
            result = await escalate_platform_ban("freelancer")

        assert result is None


# ---------------------------------------------------------------------------
# LLM failure escalation
# ---------------------------------------------------------------------------


class TestEscalateLLMFailure:
    """Tests for escalate_llm_failure."""

    @pytest.mark.asyncio
    async def test_below_threshold_returns_none(self) -> None:
        result = await escalate_llm_failure("scout", 2, "timeout")
        assert result is None

    @pytest.mark.asyncio
    async def test_at_threshold_creates_entry(self) -> None:
        with patch("src.core.auto_escalation.get_db_session", side_effect=lambda: fake_db_session()):
            result = await escalate_llm_failure("scout", 3, "API error")

        assert result is not None
        uuid.UUID(result)

    @pytest.mark.asyncio
    async def test_payload_content(self) -> None:
        captured_session: FakeSession | None = None

        @asynccontextmanager
        async def capture_session():
            nonlocal captured_session
            captured_session = FakeSession()
            yield captured_session

        with patch("src.core.auto_escalation.get_db_session", side_effect=capture_session):
            await escalate_llm_failure("bid", 5, "rate limit")

        assert captured_session is not None
        hitl = captured_session.added_items[0]
        assert hitl.type == "alert"
        assert hitl.priority == "high"
        assert hitl.payload["agent_name"] == "bid"
        assert hitl.payload["consecutive_failures"] == 5
        assert "switch_model" in hitl.available_actions

    @pytest.mark.asyncio
    async def test_duplicate_prevention(self) -> None:
        with patch(
            "src.core.auto_escalation.get_db_session",
            side_effect=lambda: fake_db_session(has_duplicate=True),
        ):
            result = await escalate_llm_failure("scout", 3)

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_db_error(self) -> None:
        @asynccontextmanager
        async def failing_session():
            raise RuntimeError("DB down")
            yield  # noqa: RET503

        with patch("src.core.auto_escalation.get_db_session", side_effect=failing_session):
            result = await escalate_llm_failure("scout", 5)

        assert result is None


# ---------------------------------------------------------------------------
# Capacity overflow escalation
# ---------------------------------------------------------------------------


class TestEscalateCapacityOverflow:
    """Tests for escalate_capacity_overflow."""

    @pytest.mark.asyncio
    async def test_at_threshold_returns_none(self) -> None:
        result = await escalate_capacity_overflow(10, threshold=10)
        assert result is None

    @pytest.mark.asyncio
    async def test_below_threshold_returns_none(self) -> None:
        result = await escalate_capacity_overflow(5, threshold=10)
        assert result is None

    @pytest.mark.asyncio
    async def test_above_threshold_creates_entry(self) -> None:
        with patch("src.core.auto_escalation.get_db_session", side_effect=lambda: fake_db_session()):
            result = await escalate_capacity_overflow(11, threshold=10)

        assert result is not None
        uuid.UUID(result)

    @pytest.mark.asyncio
    async def test_payload_content(self) -> None:
        captured_session: FakeSession | None = None

        @asynccontextmanager
        async def capture_session():
            nonlocal captured_session
            captured_session = FakeSession()
            yield captured_session

        with patch("src.core.auto_escalation.get_db_session", side_effect=capture_session):
            await escalate_capacity_overflow(15, threshold=10)

        assert captured_session is not None
        hitl = captured_session.added_items[0]
        assert hitl.type == "capacity_warning"
        assert hitl.priority == "high"
        assert hitl.payload["active_project_count"] == 15
        assert hitl.payload["threshold"] == 10
        assert "pause_scout" in hitl.available_actions

    @pytest.mark.asyncio
    async def test_duplicate_prevention(self) -> None:
        with patch(
            "src.core.auto_escalation.get_db_session",
            side_effect=lambda: fake_db_session(has_duplicate=True),
        ):
            result = await escalate_capacity_overflow(20, threshold=10)

        assert result is None


# ---------------------------------------------------------------------------
# Agent crash escalation
# ---------------------------------------------------------------------------


class TestEscalateAgentCrash:
    """Tests for escalate_agent_crash."""

    @pytest.mark.asyncio
    async def test_below_threshold_returns_none(self) -> None:
        result = await escalate_agent_crash("dev", 2)
        assert result is None

    @pytest.mark.asyncio
    async def test_at_threshold_creates_entry(self) -> None:
        with patch("src.core.auto_escalation.get_db_session", side_effect=lambda: fake_db_session()):
            result = await escalate_agent_crash("dev", 3, thread_id="thread-1")

        assert result is not None
        uuid.UUID(result)

    @pytest.mark.asyncio
    async def test_payload_content(self) -> None:
        captured_session: FakeSession | None = None

        @asynccontextmanager
        async def capture_session():
            nonlocal captured_session
            captured_session = FakeSession()
            yield captured_session

        with patch("src.core.auto_escalation.get_db_session", side_effect=capture_session):
            await escalate_agent_crash("content", 5, thread_id="t-123", last_error="OOM")

        assert captured_session is not None
        hitl = captured_session.added_items[0]
        assert hitl.type == "agent_failure"
        assert hitl.priority == "urgent"
        assert hitl.payload["agent_name"] == "content"
        assert hitl.payload["crash_count"] == 5
        assert hitl.payload["thread_id"] == "t-123"
        assert "restart" in hitl.available_actions
        assert "manual_fix" in hitl.available_actions

    @pytest.mark.asyncio
    async def test_dedup_key_includes_thread(self) -> None:
        captured_session: FakeSession | None = None

        @asynccontextmanager
        async def capture_session():
            nonlocal captured_session
            captured_session = FakeSession()
            yield captured_session

        with patch("src.core.auto_escalation.get_db_session", side_effect=capture_session):
            await escalate_agent_crash("dev", 3, thread_id="thread-abc")

        assert captured_session is not None
        hitl = captured_session.added_items[0]
        assert hitl.payload["dedup_key"] == "dev:thread-abc"

    @pytest.mark.asyncio
    async def test_dedup_key_global_when_no_thread(self) -> None:
        captured_session: FakeSession | None = None

        @asynccontextmanager
        async def capture_session():
            nonlocal captured_session
            captured_session = FakeSession()
            yield captured_session

        with patch("src.core.auto_escalation.get_db_session", side_effect=capture_session):
            await escalate_agent_crash("dev", 3)

        assert captured_session is not None
        hitl = captured_session.added_items[0]
        assert hitl.payload["dedup_key"] == "dev:global"

    @pytest.mark.asyncio
    async def test_duplicate_prevention(self) -> None:
        with patch(
            "src.core.auto_escalation.get_db_session",
            side_effect=lambda: fake_db_session(has_duplicate=True),
        ):
            result = await escalate_agent_crash("dev", 5, thread_id="t-1")

        assert result is None


# ---------------------------------------------------------------------------
# Bounce rate escalation
# ---------------------------------------------------------------------------


class TestEscalateBounceRate:
    """Tests for escalate_bounce_rate."""

    @pytest.mark.asyncio
    async def test_at_threshold_returns_none(self) -> None:
        result = await escalate_bounce_rate("campaign-1", 0.10, threshold=0.10)
        assert result is None

    @pytest.mark.asyncio
    async def test_below_threshold_returns_none(self) -> None:
        result = await escalate_bounce_rate("campaign-1", 0.05, threshold=0.10)
        assert result is None

    @pytest.mark.asyncio
    async def test_above_threshold_creates_entry(self) -> None:
        with patch("src.core.auto_escalation.get_db_session", side_effect=lambda: fake_db_session()):
            result = await escalate_bounce_rate("campaign-1", 0.15, threshold=0.10)

        assert result is not None
        uuid.UUID(result)

    @pytest.mark.asyncio
    async def test_payload_content(self) -> None:
        captured_session: FakeSession | None = None

        @asynccontextmanager
        async def capture_session():
            nonlocal captured_session
            captured_session = FakeSession()
            yield captured_session

        with patch("src.core.auto_escalation.get_db_session", side_effect=capture_session):
            await escalate_bounce_rate("campaign-abc", 0.25, threshold=0.10)

        assert captured_session is not None
        hitl = captured_session.added_items[0]
        assert hitl.type == "alert"
        assert hitl.priority == "high"
        assert hitl.payload["campaign_id"] == "campaign-abc"
        assert hitl.payload["bounce_rate"] == 0.25
        assert hitl.payload["threshold"] == 0.10
        assert "resume_campaign" in hitl.available_actions
        assert "stop_campaign" in hitl.available_actions

    @pytest.mark.asyncio
    async def test_pauses_campaign(self) -> None:
        """Verify that the campaign status is set to 'paused'."""
        campaign_id = str(uuid.uuid4())
        fake_campaign = MagicMock()
        fake_campaign.status = "active"

        captured_session: FakeSession | None = None

        @asynccontextmanager
        async def capture_session():
            nonlocal captured_session
            captured_session = FakeSession()
            # Override execute to return fake campaign on second call
            call_count = 0
            original_execute = captured_session.execute

            async def custom_execute(stmt):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    # First call: duplicate check
                    return await original_execute(stmt)
                # Second call: campaign query
                result = MagicMock()
                result.scalars.return_value.first.return_value = fake_campaign
                return result

            captured_session.execute = custom_execute
            yield captured_session

        with patch("src.core.auto_escalation.get_db_session", side_effect=capture_session):
            await escalate_bounce_rate(campaign_id, 0.20, threshold=0.10)

        assert fake_campaign.status == "paused"

    @pytest.mark.asyncio
    async def test_invalid_campaign_uuid_still_creates_alert(self) -> None:
        """When campaign_id is not a valid UUID, alert is still created."""
        captured_session: FakeSession | None = None

        @asynccontextmanager
        async def capture_session():
            nonlocal captured_session
            captured_session = FakeSession()
            yield captured_session

        with patch("src.core.auto_escalation.get_db_session", side_effect=capture_session):
            result = await escalate_bounce_rate("not-a-uuid", 0.15, threshold=0.10)

        # Alert should still be created even though campaign pause was skipped
        assert result is not None
        assert len(captured_session.added_items) == 1

    @pytest.mark.asyncio
    async def test_duplicate_prevention(self) -> None:
        with patch(
            "src.core.auto_escalation.get_db_session",
            side_effect=lambda: fake_db_session(has_duplicate=True),
        ):
            result = await escalate_bounce_rate("campaign-1", 0.20)

        assert result is None


# ---------------------------------------------------------------------------
# LLM failure tracking
# ---------------------------------------------------------------------------


class TestTrackLLMResult:
    """Tests for track_llm_result and related helpers."""

    @pytest.mark.asyncio
    async def test_success_resets_counter(self) -> None:
        _consecutive_failures["scout"] = 5
        await track_llm_result("scout", success=True)
        assert get_consecutive_failures("scout") == 0

    @pytest.mark.asyncio
    async def test_failure_increments_counter(self) -> None:
        await track_llm_result("scout", success=False, error="timeout")
        assert get_consecutive_failures("scout") == 1

    @pytest.mark.asyncio
    async def test_two_failures_no_escalation(self) -> None:
        with patch("src.core.auto_escalation.escalate_llm_failure") as mock_escalate:
            await track_llm_result("scout", success=False, error="err1")
            await track_llm_result("scout", success=False, error="err2")
            # Under threshold -- no escalation triggered
            assert get_consecutive_failures("scout") == 2
            mock_escalate.assert_not_called()

    @pytest.mark.asyncio
    async def test_three_failures_triggers_escalation(self) -> None:
        mock_coro = AsyncMock(return_value="hitl-id")
        with patch("src.core.auto_escalation.escalate_llm_failure", mock_coro):
            await track_llm_result("bid", success=False, error="e1")
            await track_llm_result("bid", success=False, error="e2")
            await track_llm_result("bid", success=False, error="e3")

            # Allow fire-and-forget task to complete
            await asyncio.sleep(0.01)

        assert get_consecutive_failures("bid") == 3

    @pytest.mark.asyncio
    async def test_success_after_failures_resets(self) -> None:
        await track_llm_result("dev", success=False)
        await track_llm_result("dev", success=False)
        assert get_consecutive_failures("dev") == 2

        await track_llm_result("dev", success=True)
        assert get_consecutive_failures("dev") == 0

    def test_reset_failure_tracking(self) -> None:
        _consecutive_failures["a"] = 5
        _consecutive_failures["b"] = 3
        reset_failure_tracking()
        assert get_consecutive_failures("a") == 0
        assert get_consecutive_failures("b") == 0

    def test_get_consecutive_failures_unknown_agent(self) -> None:
        assert get_consecutive_failures("nonexistent") == 0


# ---------------------------------------------------------------------------
# Fire-and-forget helper
# ---------------------------------------------------------------------------


class TestFireAndForget:
    """Tests for fire_and_forget_escalation."""

    @pytest.mark.asyncio
    async def test_schedules_task(self) -> None:
        completed = False

        async def dummy_coro():
            nonlocal completed
            completed = True

        fire_and_forget_escalation(dummy_coro())
        await asyncio.sleep(0.05)
        assert completed

    @pytest.mark.asyncio
    async def test_task_reference_stored(self) -> None:
        async def slow_coro():
            await asyncio.sleep(0.5)

        initial_count = len(_background_tasks)
        fire_and_forget_escalation(slow_coro())

        assert len(_background_tasks) == initial_count + 1

    @pytest.mark.asyncio
    async def test_task_reference_cleaned_up_after_completion(self) -> None:
        async def fast_coro():
            pass

        fire_and_forget_escalation(fast_coro())
        await asyncio.sleep(0.05)
        # Task should be removed from set after completion
        assert len(_background_tasks) == 0


# ---------------------------------------------------------------------------
# Duplicate prevention helper
# ---------------------------------------------------------------------------


class TestHasPendingEscalation:
    """Tests for _has_pending_escalation."""

    @pytest.mark.asyncio
    async def test_returns_false_when_no_duplicate(self) -> None:
        session = FakeSession(has_duplicate=False)
        result = await _has_pending_escalation(session, "alert", "platform", "freelancer")
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_true_when_duplicate_exists(self) -> None:
        session = FakeSession(has_duplicate=True)
        result = await _has_pending_escalation(session, "alert", "platform", "freelancer")
        assert result is True


# ---------------------------------------------------------------------------
# Edge cases and resilience
# ---------------------------------------------------------------------------


class TestResilience:
    """Edge case and error resilience tests."""

    @pytest.mark.asyncio
    async def test_empty_error_details(self) -> None:
        """Escalation with empty strings should not crash."""
        with patch("src.core.auto_escalation.get_db_session", side_effect=lambda: fake_db_session()):
            result = await escalate_platform_ban("freelancer", account_id=None, error_details="")

        assert result is not None

    @pytest.mark.asyncio
    async def test_none_account_id(self) -> None:
        captured_session: FakeSession | None = None

        @asynccontextmanager
        async def capture_session():
            nonlocal captured_session
            captured_session = FakeSession()
            yield captured_session

        with patch("src.core.auto_escalation.get_db_session", side_effect=capture_session):
            await escalate_platform_ban("fl_ru", account_id=None)

        assert captured_session is not None
        hitl = captured_session.added_items[0]
        assert hitl.payload["account_id"] is None

    @pytest.mark.asyncio
    async def test_llm_failure_zero_failures(self) -> None:
        result = await escalate_llm_failure("scout", 0)
        assert result is None

    @pytest.mark.asyncio
    async def test_agent_crash_zero_crashes(self) -> None:
        result = await escalate_agent_crash("dev", 0)
        assert result is None

    @pytest.mark.asyncio
    async def test_bounce_rate_zero(self) -> None:
        result = await escalate_bounce_rate("campaign-1", 0.0)
        assert result is None

    @pytest.mark.asyncio
    async def test_capacity_overflow_zero(self) -> None:
        result = await escalate_capacity_overflow(0, threshold=10)
        assert result is None

    @pytest.mark.asyncio
    async def test_all_escalations_return_none_on_exception(self) -> None:
        """Every escalation function returns None on DB errors."""

        @asynccontextmanager
        async def failing_session():
            raise RuntimeError("DB unavailable")
            yield  # noqa: RET503

        with patch("src.core.auto_escalation.get_db_session", side_effect=failing_session):
            assert await escalate_platform_ban("x") is None
            assert await escalate_llm_failure("x", 5) is None
            assert await escalate_capacity_overflow(20) is None
            assert await escalate_agent_crash("x", 5) is None
            assert await escalate_bounce_rate("x", 0.5) is None

    @pytest.mark.asyncio
    async def test_detected_at_is_iso_format(self) -> None:
        captured_session: FakeSession | None = None

        @asynccontextmanager
        async def capture_session():
            nonlocal captured_session
            captured_session = FakeSession()
            yield captured_session

        with patch("src.core.auto_escalation.get_db_session", side_effect=capture_session):
            await escalate_platform_ban("freelancer")

        assert captured_session is not None
        hitl = captured_session.added_items[0]
        detected_at = hitl.payload["detected_at"]
        # Should be parseable as ISO datetime
        datetime.fromisoformat(detected_at)
