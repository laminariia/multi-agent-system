"""Unit tests for src.api.routes.negotiations -- NegotiationController.

Tests cover: GET /negotiations list, GET /negotiations/{bid_id},
GET /negotiations/{bid_id}/messages with pagination, POST messages,
POST take-over/release transitions, GET analytics, and error cases.

We test route handlers directly via ``.fn()`` to avoid needing a full app
instance, following the established pattern from test_deals.py.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from litestar.exceptions import NotFoundException

from src.api.routes.negotiations import NegotiationController
from src.core.exceptions import MASException

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_negotiation(
    *,
    bid_id: uuid.UUID | None = None,
    state: str = "qualifying",
    previous_state: str | None = None,
    state_version: int = 1,
    state_reason: str | None = None,
    original_amount: Decimal | None = Decimal("1000"),
    current_amount: Decimal | None = Decimal("900"),
    final_amount: Decimal | None = None,
    rounds: int = 2,
    followup_count: int = 0,
    last_followup_at: datetime | None = None,
    outcome: str | None = None,
    history: list | None = None,
    metadata_json: dict | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    resolved_at: datetime | None = None,
) -> MagicMock:
    neg = MagicMock()
    neg.id = uuid.uuid4()
    neg.bid_id = bid_id or uuid.uuid4()
    neg.state = state
    neg.previous_state = previous_state
    neg.state_version = state_version
    neg.state_reason = state_reason
    neg.original_amount = original_amount
    neg.current_amount = current_amount
    neg.final_amount = final_amount
    neg.rounds = rounds
    neg.followup_count = followup_count
    neg.last_followup_at = last_followup_at
    neg.outcome = outcome
    neg.history = history or []
    neg.metadata_json = metadata_json
    neg.created_at = created_at or datetime.now(UTC)
    neg.updated_at = updated_at or datetime.now(UTC)
    neg.resolved_at = resolved_at
    return neg


def _make_bid(
    *,
    bid_id: uuid.UUID | None = None,
    job_id: uuid.UUID | None = None,
    bid_amount: Decimal = Decimal("500"),
    status: str = "sent",
) -> MagicMock:
    bid = MagicMock()
    bid.id = bid_id or uuid.uuid4()
    bid.job_id = job_id or uuid.uuid4()
    bid.bid_amount = bid_amount
    bid.status = status
    return bid


def _make_job(
    *,
    job_id: uuid.UUID | None = None,
    title: str = "Build Landing Page",
    platform: str = "freelancer",
) -> MagicMock:
    job = MagicMock()
    job.id = job_id or uuid.uuid4()
    job.title = title
    job.platform = platform
    return job


def _make_message(
    *,
    bid_id: uuid.UUID | None = None,
    direction: str = "inbound",
    sender: str = "client",
    content: str = "Hello",
    platform: str | None = "freelancer",
    external_id: str | None = None,
    auto_generated: bool = False,
    hitl_reviewed: bool = False,
    message_type: str | None = None,
    metadata_json: dict | None = None,
    created_at: datetime | None = None,
) -> MagicMock:
    msg = MagicMock()
    msg.id = uuid.uuid4()
    msg.bid_id = bid_id or uuid.uuid4()
    msg.direction = direction
    msg.sender = sender
    msg.content = content
    msg.platform = platform
    msg.external_id = external_id
    msg.auto_generated = auto_generated
    msg.hitl_reviewed = hitl_reviewed
    msg.message_type = message_type
    msg.metadata_json = metadata_json
    msg.created_at = created_at or datetime.now(UTC)
    return msg


def _make_request(user_id: uuid.UUID | None = None) -> MagicMock:
    request = MagicMock()
    request.user = MagicMock()
    request.user.id = user_id or uuid.uuid4()
    return request


def _make_channels() -> AsyncMock:
    return AsyncMock()


def _ctrl() -> NegotiationController:
    return NegotiationController(owner=MagicMock())


# ---------------------------------------------------------------------------
# Tests: GET /negotiations (list)
# ---------------------------------------------------------------------------


class TestListNegotiations:
    """Test list_negotiations endpoint logic."""

    async def test_returns_items_and_total(self):
        ctrl = _ctrl()
        bid_id = uuid.uuid4()
        job_id = uuid.uuid4()
        neg = _make_negotiation(bid_id=bid_id)
        bid = _make_bid(bid_id=bid_id, job_id=job_id)
        job = _make_job(job_id=job_id)

        session = AsyncMock()
        call_count = 0

        async def _execute(stmt, *a, **kw):
            nonlocal call_count
            call_count += 1
            res = MagicMock()
            if call_count == 1:
                res.scalar_one.return_value = 1
            elif call_count == 2:
                scalars_mock = MagicMock()
                unique_mock = MagicMock()
                unique_mock.all.return_value = [neg]
                scalars_mock.unique.return_value = unique_mock
                res.scalars.return_value = scalars_mock
            elif call_count == 3:
                res.scalar_one_or_none.return_value = bid
            elif call_count == 4:
                res.scalar_one_or_none.return_value = job
            return res

        session.execute = AsyncMock(side_effect=_execute)

        result = await NegotiationController.list_negotiations.fn(
            ctrl,
            db_session=session,
            negotiation_state=None,
            platform=None,
            search=None,
            sort_by=None,
            limit=20,
            offset=0,
        )

        assert result["total"] == 1
        assert len(result["items"]) == 1
        assert result["items"][0]["state"] == "qualifying"

    async def test_returns_empty_when_no_negotiations(self):
        ctrl = _ctrl()
        session = AsyncMock()
        call_count = 0

        async def _execute(stmt, *a, **kw):
            nonlocal call_count
            call_count += 1
            res = MagicMock()
            if call_count == 1:
                res.scalar_one.return_value = 0
            else:
                scalars_mock = MagicMock()
                unique_mock = MagicMock()
                unique_mock.all.return_value = []
                scalars_mock.unique.return_value = unique_mock
                res.scalars.return_value = scalars_mock
            return res

        session.execute = AsyncMock(side_effect=_execute)

        result = await NegotiationController.list_negotiations.fn(
            ctrl,
            db_session=session,
            negotiation_state=None,
            platform=None,
            search=None,
            sort_by=None,
            limit=20,
            offset=0,
        )

        assert result["total"] == 0
        assert result["items"] == []


# ---------------------------------------------------------------------------
# Tests: GET /negotiations/{bid_id}
# ---------------------------------------------------------------------------


class TestGetNegotiation:
    """Test get_negotiation endpoint logic."""

    async def test_returns_negotiation_detail(self):
        ctrl = _ctrl()
        bid_id = uuid.uuid4()
        neg = _make_negotiation(bid_id=bid_id, state="negotiating", rounds=3)
        bid = _make_bid(bid_id=bid_id)
        job = _make_job()

        session = AsyncMock()
        call_count = 0

        async def _execute(stmt, *a, **kw):
            nonlocal call_count
            call_count += 1
            res = MagicMock()
            if call_count == 1:
                res.scalar_one_or_none.return_value = neg
            elif call_count == 2:
                res.scalar_one.return_value = 5
            elif call_count == 3:
                res.scalar_one_or_none.return_value = bid
            elif call_count == 4:
                res.scalar_one_or_none.return_value = job
            return res

        session.execute = AsyncMock(side_effect=_execute)

        result = await NegotiationController.get_negotiation.fn(
            ctrl,
            bid_id=bid_id,
            db_session=session,
        )

        assert result["state"] == "negotiating"
        assert result["rounds"] == 3
        assert result["message_count"] == 5
        assert "bid" in result

    async def test_not_found_raises(self):
        ctrl = _ctrl()
        session = AsyncMock()
        res = MagicMock()
        res.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=res)

        with pytest.raises(NotFoundException):
            await NegotiationController.get_negotiation.fn(
                ctrl,
                bid_id=uuid.uuid4(),
                db_session=session,
            )


# ---------------------------------------------------------------------------
# Tests: GET /negotiations/{bid_id}/messages
# ---------------------------------------------------------------------------


class TestGetMessages:
    """Test get_messages endpoint with pagination."""

    async def test_returns_messages_with_total(self):
        ctrl = _ctrl()
        bid_id = uuid.uuid4()
        neg = _make_negotiation(bid_id=bid_id)
        msg1 = _make_message(bid_id=bid_id, content="Hello")
        msg2 = _make_message(bid_id=bid_id, content="Hi back", direction="outbound", sender="ai")

        session = AsyncMock()
        call_count = 0

        async def _execute(stmt, *a, **kw):
            nonlocal call_count
            call_count += 1
            res = MagicMock()
            if call_count == 1:
                res.scalar_one_or_none.return_value = neg
            elif call_count == 2:
                res.scalar_one.return_value = 2
            elif call_count == 3:
                scalars_mock = MagicMock()
                scalars_mock.all.return_value = [msg1, msg2]
                res.scalars.return_value = scalars_mock
            return res

        session.execute = AsyncMock(side_effect=_execute)

        result = await NegotiationController.get_messages.fn(
            ctrl,
            bid_id=bid_id,
            db_session=session,
            direction=None,
            limit=50,
            offset=0,
        )

        assert result["total"] == 2
        assert len(result["messages"]) == 2
        assert result["messages"][0]["content"] == "Hello"

    async def test_messages_not_found_raises(self):
        ctrl = _ctrl()
        session = AsyncMock()
        res = MagicMock()
        res.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=res)

        with pytest.raises(NotFoundException):
            await NegotiationController.get_messages.fn(
                ctrl,
                bid_id=uuid.uuid4(),
                db_session=session,
                direction=None,
                limit=50,
                offset=0,
            )

    async def test_messages_with_direction_filter(self):
        ctrl = _ctrl()
        bid_id = uuid.uuid4()
        neg = _make_negotiation(bid_id=bid_id)
        msg = _make_message(bid_id=bid_id, direction="inbound")

        session = AsyncMock()
        call_count = 0

        async def _execute(stmt, *a, **kw):
            nonlocal call_count
            call_count += 1
            res = MagicMock()
            if call_count == 1:
                res.scalar_one_or_none.return_value = neg
            elif call_count == 2:
                res.scalar_one.return_value = 1
            elif call_count == 3:
                scalars_mock = MagicMock()
                scalars_mock.all.return_value = [msg]
                res.scalars.return_value = scalars_mock
            return res

        session.execute = AsyncMock(side_effect=_execute)

        result = await NegotiationController.get_messages.fn(
            ctrl,
            bid_id=bid_id,
            db_session=session,
            direction="inbound",
            limit=50,
            offset=0,
        )

        assert result["total"] == 1


# ---------------------------------------------------------------------------
# Tests: POST /negotiations/{bid_id}/messages
# ---------------------------------------------------------------------------


class TestSendMessage:
    """Test operator send_message endpoint."""

    async def test_creates_outbound_operator_message(self):
        from src.api.schemas import NegotiationSendMessageSchema

        ctrl = _ctrl()
        bid_id = uuid.uuid4()
        neg = _make_negotiation(bid_id=bid_id)
        bid = _make_bid(bid_id=bid_id)
        job = _make_job()

        session = AsyncMock()
        call_count = 0

        async def _execute(stmt, *a, **kw):
            nonlocal call_count
            call_count += 1
            res = MagicMock()
            if call_count == 1:
                res.scalar_one_or_none.return_value = neg
            elif call_count == 2:
                res.scalar_one_or_none.return_value = bid
            elif call_count == 3:
                res.scalar_one_or_none.return_value = job
            return res

        session.execute = AsyncMock(side_effect=_execute)
        session.add = MagicMock()
        session.flush = AsyncMock()

        data = NegotiationSendMessageSchema(content="Hello from operator")
        request = _make_request()
        channels = _make_channels()

        result = await NegotiationController.send_message.fn(
            ctrl,
            bid_id=bid_id,
            data=data,
            request=request,
            db_session=session,
            channels=channels,
        )

        assert result["direction"] == "outbound"
        assert result["sender"] == "operator"
        assert result["content"] == "Hello from operator"
        session.add.assert_called_once()

    async def test_send_message_not_found_raises(self):
        from src.api.schemas import NegotiationSendMessageSchema

        ctrl = _ctrl()
        session = AsyncMock()
        res = MagicMock()
        res.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=res)

        data = NegotiationSendMessageSchema(content="test")
        request = _make_request()
        channels = _make_channels()

        with pytest.raises(NotFoundException):
            await NegotiationController.send_message.fn(
                ctrl,
                bid_id=uuid.uuid4(),
                data=data,
                request=request,
                db_session=session,
                channels=channels,
            )


# ---------------------------------------------------------------------------
# Tests: POST /negotiations/{bid_id}/take-over
# ---------------------------------------------------------------------------


class TestTakeOver:
    """Test operator take-over transition."""

    async def test_take_over_sets_operator_override(self):
        ctrl = _ctrl()
        bid_id = uuid.uuid4()
        neg = _make_negotiation(bid_id=bid_id, state="qualifying")

        session = AsyncMock()
        res = MagicMock()
        res.scalar_one_or_none.return_value = neg
        session.execute = AsyncMock(return_value=res)
        session.flush = AsyncMock()

        request = _make_request()
        channels = _make_channels()

        result = await NegotiationController.take_over.fn(
            ctrl,
            bid_id=bid_id,
            request=request,
            db_session=session,
            channels=channels,
        )

        assert result["state"] == "operator_override"
        assert result["previous_state"] == "qualifying"

    async def test_take_over_already_override_raises(self):
        ctrl = _ctrl()
        bid_id = uuid.uuid4()
        neg = _make_negotiation(bid_id=bid_id, state="operator_override")

        session = AsyncMock()
        res = MagicMock()
        res.scalar_one_or_none.return_value = neg
        session.execute = AsyncMock(return_value=res)

        request = _make_request()
        channels = _make_channels()

        with pytest.raises(MASException, match="already under operator control"):
            await NegotiationController.take_over.fn(
                ctrl,
                bid_id=bid_id,
                request=request,
                db_session=session,
                channels=channels,
            )

    async def test_take_over_not_found_raises(self):
        ctrl = _ctrl()
        session = AsyncMock()
        res = MagicMock()
        res.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=res)

        request = _make_request()
        channels = _make_channels()

        with pytest.raises(NotFoundException):
            await NegotiationController.take_over.fn(
                ctrl,
                bid_id=uuid.uuid4(),
                request=request,
                db_session=session,
                channels=channels,
            )


# ---------------------------------------------------------------------------
# Tests: POST /negotiations/{bid_id}/release
# ---------------------------------------------------------------------------


class TestRelease:
    """Test operator release transition."""

    async def test_release_restores_previous_state(self):
        ctrl = _ctrl()
        bid_id = uuid.uuid4()
        neg = _make_negotiation(
            bid_id=bid_id,
            state="operator_override",
            previous_state="qualifying",
        )

        session = AsyncMock()
        res = MagicMock()
        res.scalar_one_or_none.return_value = neg
        session.execute = AsyncMock(return_value=res)
        session.flush = AsyncMock()

        request = _make_request()
        channels = _make_channels()

        result = await NegotiationController.release.fn(
            ctrl,
            bid_id=bid_id,
            request=request,
            db_session=session,
            channels=channels,
        )

        assert result["state"] == "qualifying"
        assert result["previous_state"] == "operator_override"

    async def test_release_with_target_state(self):
        from src.api.schemas import NegotiationReleaseSchema

        ctrl = _ctrl()
        bid_id = uuid.uuid4()
        neg = _make_negotiation(
            bid_id=bid_id,
            state="operator_override",
            previous_state="qualifying",
        )

        session = AsyncMock()
        res = MagicMock()
        res.scalar_one_or_none.return_value = neg
        session.execute = AsyncMock(return_value=res)
        session.flush = AsyncMock()

        request = _make_request()
        channels = _make_channels()
        data = NegotiationReleaseSchema(target_state="proposing")

        result = await NegotiationController.release.fn(
            ctrl,
            bid_id=bid_id,
            request=request,
            db_session=session,
            channels=channels,
            data=data,
        )

        assert result["state"] == "proposing"

    async def test_release_defaults_to_qualifying_when_no_previous(self):
        ctrl = _ctrl()
        bid_id = uuid.uuid4()
        neg = _make_negotiation(
            bid_id=bid_id,
            state="operator_override",
            previous_state=None,
        )

        session = AsyncMock()
        res = MagicMock()
        res.scalar_one_or_none.return_value = neg
        session.execute = AsyncMock(return_value=res)
        session.flush = AsyncMock()

        request = _make_request()
        channels = _make_channels()

        result = await NegotiationController.release.fn(
            ctrl,
            bid_id=bid_id,
            request=request,
            db_session=session,
            channels=channels,
        )

        assert result["state"] == "qualifying"

    async def test_release_not_override_raises(self):
        ctrl = _ctrl()
        bid_id = uuid.uuid4()
        neg = _make_negotiation(bid_id=bid_id, state="qualifying")

        session = AsyncMock()
        res = MagicMock()
        res.scalar_one_or_none.return_value = neg
        session.execute = AsyncMock(return_value=res)

        request = _make_request()
        channels = _make_channels()

        with pytest.raises(MASException, match="not under operator control"):
            await NegotiationController.release.fn(
                ctrl,
                bid_id=bid_id,
                request=request,
                db_session=session,
                channels=channels,
            )

    async def test_release_not_found_raises(self):
        ctrl = _ctrl()
        session = AsyncMock()
        res = MagicMock()
        res.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=res)

        request = _make_request()
        channels = _make_channels()

        with pytest.raises(NotFoundException):
            await NegotiationController.release.fn(
                ctrl,
                bid_id=uuid.uuid4(),
                request=request,
                db_session=session,
                channels=channels,
            )


# ---------------------------------------------------------------------------
# Tests: GET /negotiations/analytics
# ---------------------------------------------------------------------------


class TestAnalytics:
    """Test analytics endpoint logic."""

    async def test_returns_analytics_structure(self):
        ctrl = _ctrl()
        session = AsyncMock()
        call_count = 0

        async def _execute(stmt, *a, **kw):
            nonlocal call_count
            call_count += 1
            res = MagicMock()
            if call_count == 1:
                res.scalar_one.return_value = 10
            elif call_count == 2:
                res.all.return_value = [("qualifying", 5), ("won", 3), ("lost", 2)]
            elif call_count == 3:
                res.all.return_value = [("freelancer", 7), ("kwork", 3)]
            elif call_count == 4:
                res.scalar_one.return_value = 2.5
            elif call_count == 5:
                res.scalar_one.return_value = 3
            elif call_count == 6:
                res.scalar_one.return_value = 432000.0  # 5 days in seconds
            elif call_count == 7:
                res.scalar_one.return_value = 4
            elif call_count == 8:
                res.scalar_one.return_value = 1
            return res

        session.execute = AsyncMock(side_effect=_execute)

        result = await NegotiationController.analytics.fn(
            ctrl,
            db_session=session,
            days=30,
        )

        assert result["period_days"] == 30
        assert result["total"] == 10
        assert result["by_state"]["qualifying"] == 5
        assert result["by_platform"]["freelancer"] == 7
        assert result["avg_rounds"] == 2.5
        assert result["conversion_rate"] == 30.0
        assert result["avg_time_to_close_days"] == 5.0
        assert result["followup_effectiveness"] == 25.0
        assert result["won"] == 3

    async def test_analytics_zero_total(self):
        ctrl = _ctrl()
        session = AsyncMock()
        call_count = 0

        async def _execute(stmt, *a, **kw):
            nonlocal call_count
            call_count += 1
            res = MagicMock()
            if call_count == 1:
                res.scalar_one.return_value = 0
            elif call_count == 2:
                res.all.return_value = []
            elif call_count == 3:
                res.all.return_value = []
            elif call_count == 4:
                res.scalar_one.return_value = None
            elif call_count == 5:
                res.scalar_one.return_value = 0
            elif call_count == 6:
                res.scalar_one.return_value = None
            elif call_count == 7:
                res.scalar_one.return_value = 0
            elif call_count == 8:
                res.scalar_one.return_value = 0
            return res

        session.execute = AsyncMock(side_effect=_execute)

        result = await NegotiationController.analytics.fn(ctrl, db_session=session, days=30)

        assert result["total"] == 0
        assert result["conversion_rate"] == 0.0
        assert result["avg_rounds"] == 0.0
        assert result["avg_time_to_close_days"] == 0.0
        assert result["followup_effectiveness"] == 0.0


# ---------------------------------------------------------------------------
# Tests: WebSocket publish guard
# ---------------------------------------------------------------------------


class TestWebSocketGuard:
    """Test that WebSocket publish failures do not break endpoints."""

    async def test_take_over_survives_ws_error(self):
        ctrl = _ctrl()
        bid_id = uuid.uuid4()
        neg = _make_negotiation(bid_id=bid_id, state="qualifying")

        session = AsyncMock()
        res = MagicMock()
        res.scalar_one_or_none.return_value = neg
        session.execute = AsyncMock(return_value=res)
        session.flush = AsyncMock()

        request = _make_request()
        channels = _make_channels()

        with patch("src.api.routes.negotiations.publish_event", side_effect=OSError("ws down")):
            result = await NegotiationController.take_over.fn(
                ctrl,
                bid_id=bid_id,
                request=request,
                db_session=session,
                channels=channels,
            )

        assert result["state"] == "operator_override"

    async def test_send_message_survives_ws_error(self):
        from src.api.schemas import NegotiationSendMessageSchema

        ctrl = _ctrl()
        bid_id = uuid.uuid4()
        neg = _make_negotiation(bid_id=bid_id)
        bid = _make_bid(bid_id=bid_id)
        job = _make_job()

        session = AsyncMock()
        call_count = 0

        async def _execute(stmt, *a, **kw):
            nonlocal call_count
            call_count += 1
            res = MagicMock()
            if call_count == 1:
                res.scalar_one_or_none.return_value = neg
            elif call_count == 2:
                res.scalar_one_or_none.return_value = bid
            elif call_count == 3:
                res.scalar_one_or_none.return_value = job
            return res

        session.execute = AsyncMock(side_effect=_execute)
        session.add = MagicMock()
        session.flush = AsyncMock()

        data = NegotiationSendMessageSchema(content="test")
        request = _make_request()
        channels = _make_channels()

        with patch("src.api.routes.negotiations.publish_event", side_effect=OSError("ws down")):
            result = await NegotiationController.send_message.fn(
                ctrl,
                bid_id=bid_id,
                data=data,
                request=request,
                db_session=session,
                channels=channels,
            )

        assert result["content"] == "test"
