"""Tests for P1.3 Delivery Throttling — Execution Cloaking.

Covers:
- ScheduledMessage DB model
- Packager delivery guard (min_delivery_at enforcement)
- Bid prompt hardening (real_hours guardrail)
- Scheduled message generation
- Message dispatcher worker task
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest  # noqa: F401 — used as class-level pytestmark

# ===========================================================================
# 1. ScheduledMessage DB Model
# ===========================================================================


class TestScheduledMessageModel:
    """ScheduledMessage ORM model has correct schema."""

    def test_model_has_required_columns(self) -> None:
        """Model should have all required columns."""
        from src.core.models import ScheduledMessage

        # Check tablename
        assert ScheduledMessage.__tablename__ == "scheduled_messages"

        # Check column names exist
        mapper = ScheduledMessage.__table__.columns
        expected = {
            "id",
            "project_id",
            "thread_id",
            "send_at",
            "content",
            "channel",
            "status",
            "created_at",
            "sent_at",
        }
        assert expected.issubset({c.name for c in mapper})

    def test_model_instantiation(self) -> None:
        """Model should be instantiable with all fields."""
        from src.core.models import ScheduledMessage

        now = datetime.now(tz=UTC)
        msg = ScheduledMessage(
            id=uuid.uuid4(),
            project_id="proj-1",
            thread_id="thread-1",
            send_at=now + timedelta(hours=2),
            content="Progress update: backend ready",
            channel="platform",
            status="pending",
        )
        assert msg.project_id == "proj-1"
        assert msg.channel == "platform"
        assert msg.status == "pending"
        assert msg.sent_at is None

    def test_status_has_server_default_pending(self) -> None:
        """Column should have server_default 'pending'."""
        from src.core.models import ScheduledMessage

        col = ScheduledMessage.__table__.columns["status"]
        assert str(col.server_default.arg) == "pending"


# ===========================================================================
# 2. Packager Delivery Guard
# ===========================================================================


class TestPackagerDeliveryGuard:
    """Packager enforces min_delivery_at before final delivery."""

    pytestmark = pytest.mark.asyncio

    def _make_packager(self):
        """Create a PackagerAgent with mocked dependencies."""
        from unittest.mock import AsyncMock, MagicMock

        from src.agents.packager import PackagerAgent

        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = (
            '{"delivery_id":"d-1","project_id":"p-1",'
            '"delivery_type":"files","files_count":3,'
            '"includes":["dev"],"delivery_message":"Ready",'
            '"readme_content":"# README","missing_artifacts":[],'
            '"quality_notes":"Good","requires_hitl":true}'
        )
        mock_llm.call = AsyncMock(return_value=(mock_response, MagicMock()))

        return PackagerAgent(
            llm_client=mock_llm,
            heartbeat=MagicMock(),
            loop_detector=MagicMock(),
        )

    def _make_state(self, *, min_delivery_at=None):
        """Build a minimal state for packager tests."""
        from src.core.state import ProjectContext, create_initial_state

        project = ProjectContext(
            project_id="test-throttle",
            job_id="j-1",
            platform="freelancer",
            client={},
            requirements="Build a landing page",
            budget=500,
            deadline=None,
        )
        state = create_initial_state(project=project, first_agent="packager")
        state["artifacts"] = {
            "dev": ['{"code": "index.html"}'],
            "planner": ['{"phases": []}'],
        }
        state["min_delivery_at"] = min_delivery_at
        state["proposed_days"] = 5
        state["thread_id"] = "thread-test"
        state["delivery_type"] = "files"
        state["agent_sequence"] = ["dev"]
        state["current_sequence_index"] = 1
        return state

    async def test_blocks_early_delivery(self) -> None:
        """Should create delivery_hold HITL when now < min_delivery_at."""
        from unittest.mock import AsyncMock, patch

        agent = self._make_packager()
        future = datetime.now(tz=UTC) + timedelta(days=2)
        state = self._make_state(min_delivery_at=future)

        with patch("src.agents.packager.get_db_session") as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await agent._execute(state)

        assert result["requires_hitl"] is True
        assert result["status"] == "paused"
        # Check that delivery_hold type was used
        hitl_call = mock_session.add.call_args_list[0][0][0]
        assert hitl_call.type == "delivery_hold"
        assert "deliver_now" in hitl_call.available_actions

    async def test_allows_past_min_delivery(self) -> None:
        """Should proceed with normal final_review when min_delivery_at is past."""
        from unittest.mock import AsyncMock, patch

        agent = self._make_packager()
        past = datetime.now(tz=UTC) - timedelta(days=1)
        state = self._make_state(min_delivery_at=past)

        with patch("src.agents.packager.get_db_session") as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await agent._execute(state)

        assert result["requires_hitl"] is True
        # Normal flow — final_review, not delivery_hold
        hitl_call = mock_session.add.call_args_list[0][0][0]
        assert hitl_call.type == "final_review"

    async def test_allows_none_min_delivery(self) -> None:
        """Should proceed normally when min_delivery_at is None."""
        from unittest.mock import AsyncMock, patch

        agent = self._make_packager()
        state = self._make_state(min_delivery_at=None)

        with patch("src.agents.packager.get_db_session") as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await agent._execute(state)

        assert result["requires_hitl"] is True
        hitl_call = mock_session.add.call_args_list[0][0][0]
        assert hitl_call.type == "final_review"

    async def test_delivery_hold_has_wait_action(self) -> None:
        """delivery_hold HITL should have deliver_now and wait actions."""
        from unittest.mock import AsyncMock, patch

        agent = self._make_packager()
        future = datetime.now(tz=UTC) + timedelta(days=3)
        state = self._make_state(min_delivery_at=future)

        with patch("src.agents.packager.get_db_session") as mock_db:
            mock_session = AsyncMock()
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            await agent._execute(state)

        hitl_call = mock_session.add.call_args_list[0][0][0]
        assert set(hitl_call.available_actions) == {"deliver_now", "wait"}


# ===========================================================================
# 3. Bid Prompt Hardening
# ===========================================================================


class TestBidPromptHardening:
    """Bid prompt contains guardrail against real_hours exposure."""

    def test_bid_prompt_contains_execution_time_guardrail(self) -> None:
        """Prompt should warn against using AI execution time."""
        from src.prompts.bid import BID_SYSTEM_PROMPT

        prompt_lower = BID_SYSTEM_PROMPT.lower()
        assert (
            "real_hours" in prompt_lower
            or "ai execution time" in prompt_lower
            or "actual ai processing" in prompt_lower
        )


# ===========================================================================
# 4. Scheduled Message Generation
# ===========================================================================


class TestDeliveryScheduler:
    """generate_scheduled_messages creates progress updates for delivery window."""

    def test_generates_messages_for_delivery_window(self) -> None:
        """Should generate messages spread across the delivery window."""
        from src.core.delivery_scheduler import generate_scheduled_messages

        now = datetime.now(tz=UTC)
        min_delivery_at = now + timedelta(days=3)

        messages = generate_scheduled_messages(
            project_id="proj-1",
            thread_id="thread-1",
            min_delivery_at=min_delivery_at,
        )

        assert len(messages) >= 2
        assert all(m.project_id == "proj-1" for m in messages)
        assert all(m.status == "pending" for m in messages)
        # Chronologically ordered
        send_times = [m.send_at for m in messages]
        assert send_times == sorted(send_times)
        # All before min_delivery_at
        assert all(m.send_at <= min_delivery_at for m in messages)

    def test_short_window_generates_fewer_messages(self) -> None:
        """Short delivery window should produce fewer messages than long."""
        from src.core.delivery_scheduler import generate_scheduled_messages

        now = datetime.now(tz=UTC)
        short_msgs = generate_scheduled_messages("p1", "t1", now + timedelta(hours=6))
        long_msgs = generate_scheduled_messages("p1", "t1", now + timedelta(days=5))

        assert len(short_msgs) <= len(long_msgs)

    def test_messages_have_content(self) -> None:
        """Each message should have non-empty content."""
        from src.core.delivery_scheduler import generate_scheduled_messages

        now = datetime.now(tz=UTC)
        messages = generate_scheduled_messages("p1", "t1", now + timedelta(days=2))

        assert all(m.content and len(m.content) > 10 for m in messages)

    def test_channel_defaults_to_platform(self) -> None:
        """Default channel should be 'platform'."""
        from src.core.delivery_scheduler import generate_scheduled_messages

        now = datetime.now(tz=UTC)
        messages = generate_scheduled_messages("p1", "t1", now + timedelta(days=2))

        assert all(m.channel == "platform" for m in messages)

    def test_custom_channel(self) -> None:
        """Should accept custom channel."""
        from src.core.delivery_scheduler import generate_scheduled_messages

        now = datetime.now(tz=UTC)
        messages = generate_scheduled_messages(
            "p1",
            "t1",
            now + timedelta(days=2),
            channel="email",
        )

        assert all(m.channel == "email" for m in messages)


# ===========================================================================
# 5. Message Dispatcher Worker Task
# ===========================================================================


class TestMessageDispatcher:
    """dispatch_scheduled_messages sends due messages."""

    pytestmark = pytest.mark.asyncio

    async def test_dispatches_due_messages(self) -> None:
        """Should query and dispatch messages where send_at <= now."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from src.worker.tasks import dispatch_scheduled_messages

        past = datetime.now(tz=UTC) - timedelta(hours=1)
        mock_msg = MagicMock()
        mock_msg.id = uuid.uuid4()
        mock_msg.project_id = "proj-1"
        mock_msg.thread_id = "thread-1"
        mock_msg.content = "Progress update"
        mock_msg.channel = "platform"
        mock_msg.send_at = past
        mock_msg.status = "pending"

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_msg]

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("src.worker.tasks.get_db_session") as mock_db:
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            count = await dispatch_scheduled_messages()

        assert count == 1
        assert mock_msg.status == "sent"
        assert mock_msg.sent_at is not None

    async def test_skips_when_no_due_messages(self) -> None:
        """Should return 0 when no messages are due."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from src.worker.tasks import dispatch_scheduled_messages

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch("src.worker.tasks.get_db_session") as mock_db:
            mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

            count = await dispatch_scheduled_messages()

        assert count == 0
