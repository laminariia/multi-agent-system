"""Unit tests for src.negotiations.poller — MessagePoller.

Tests cover: poll_all_active, dedup by external_id, interval enforcement,
per-bid asyncio.Lock, error handling, save_outbound_message, empty negotiations,
and platform dispatch.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from src.negotiations.poller import (
    _TERMINAL_NEGOTIATION_STATES,
    POLLING_INTERVALS,
    MessagePoller,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bid(
    *,
    bid_id: uuid.UUID | None = None,
    platform_bid_id: str = "ext-bid-1",
    platform_thread_id: str = "thread-1",
    last_polled_at: datetime | None = None,
    client_telegram_id: int | None = None,
    job_id: uuid.UUID | None = None,
) -> MagicMock:
    bid = MagicMock()
    bid.id = bid_id or uuid.uuid4()
    bid.platform_bid_id = platform_bid_id
    bid.platform_thread_id = platform_thread_id
    bid.last_polled_at = last_polled_at
    bid.client_telegram_id = client_telegram_id
    bid.job_id = job_id or uuid.uuid4()
    return bid


def _make_job(*, platform: str = "freelancer") -> MagicMock:
    job = MagicMock()
    job.platform = platform
    return job


def _make_negotiation(*, state: str = "qualifying") -> MagicMock:
    neg = MagicMock()
    neg.state = state
    neg.bid_id = uuid.uuid4()
    return neg


def _make_session(
    rows: list[tuple[Any, Any, Any]] | None = None,
    existing_ids: set[str] | None = None,
) -> AsyncMock:
    """Return an AsyncMock session with execute() responses wired up."""
    session = AsyncMock()

    # _get_active_negotiations result
    result_mock = MagicMock()
    unique_mock = MagicMock()
    unique_mock.all.return_value = rows or []
    result_mock.unique.return_value = unique_mock
    session.execute = AsyncMock(return_value=result_mock)

    # For dedup queries (second+ execute calls), we track state
    _existing = existing_ids or set()
    _call_count = 0

    async def _execute_side_effect(stmt, *a, **kw):
        nonlocal _call_count
        _call_count += 1
        if _call_count == 1:
            # First call: _get_active_negotiations
            return result_mock
        # Subsequent calls: dedup check (scalar_one_or_none)
        dedup_result = MagicMock()
        dedup_result.scalar_one_or_none.return_value = None
        return dedup_result

    session.execute = AsyncMock(side_effect=_execute_side_effect)
    session.add = MagicMock()
    session.commit = AsyncMock()
    return session


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPollAllActive:
    """Tests for MessagePoller.poll_all_active."""

    async def test_returns_zero_when_no_active_negotiations(self):
        adapter = AsyncMock()
        poller = MessagePoller(adapters={"freelancer": adapter})

        with patch("src.negotiations.poller.get_db_session") as mock_ctx:
            session = _make_session(rows=[])
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await poller.poll_all_active()

        assert result == 0
        adapter.get_thread_messages.assert_not_awaited()

    async def test_skips_platform_without_adapter(self):
        poller = MessagePoller(adapters={})
        neg = _make_negotiation()
        bid = _make_bid()
        job = _make_job(platform="freelancer")

        with patch("src.negotiations.poller.get_db_session") as mock_ctx:
            session = _make_session(rows=[(neg, bid, job)])
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await poller.poll_all_active()

        assert result == 0

    async def test_interval_enforcement_skips_recent_poll(self):
        adapter = AsyncMock()
        poller = MessagePoller(adapters={"freelancer": adapter})
        neg = _make_negotiation()
        bid = _make_bid(last_polled_at=datetime.now(UTC) - timedelta(seconds=10))
        job = _make_job(platform="freelancer")

        with patch("src.negotiations.poller.get_db_session") as mock_ctx:
            session = _make_session(rows=[(neg, bid, job)])
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await poller.poll_all_active()

        assert result == 0
        adapter.get_thread_messages.assert_not_awaited()

    async def test_polls_when_interval_expired(self):
        adapter = AsyncMock()
        adapter.get_thread_messages = AsyncMock(return_value=[])
        poller = MessagePoller(adapters={"freelancer": adapter})
        neg = _make_negotiation()
        bid = _make_bid(last_polled_at=datetime.now(UTC) - timedelta(seconds=600))
        job = _make_job(platform="freelancer")

        with patch("src.negotiations.poller.get_db_session") as mock_ctx:
            session = _make_session(rows=[(neg, bid, job)])
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await poller.poll_all_active()

        assert result == 0
        adapter.get_thread_messages.assert_awaited_once()

    async def test_polls_when_never_polled(self):
        adapter = AsyncMock()
        adapter.get_thread_messages = AsyncMock(return_value=[])
        poller = MessagePoller(adapters={"freelancer": adapter})
        neg = _make_negotiation()
        bid = _make_bid(last_polled_at=None)
        job = _make_job(platform="freelancer")

        with patch("src.negotiations.poller.get_db_session") as mock_ctx:
            session = _make_session(rows=[(neg, bid, job)])
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await poller.poll_all_active()

        assert result == 0
        adapter.get_thread_messages.assert_awaited_once()

    async def test_per_bid_lock_prevents_parallel_polling(self):
        """If a lock is already held for a bid, that bid is skipped."""
        adapter = AsyncMock()
        adapter.get_thread_messages = AsyncMock(return_value=[])
        poller = MessagePoller(adapters={"freelancer": adapter})

        bid = _make_bid(last_polled_at=None)
        bid_key = str(bid.id)

        # Pre-acquire the lock
        lock = asyncio.Lock()
        await lock.acquire()
        poller._bid_locks[bid_key] = lock

        neg = _make_negotiation()
        job = _make_job(platform="freelancer")

        with patch("src.negotiations.poller.get_db_session") as mock_ctx:
            session = _make_session(rows=[(neg, bid, job)])
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await poller.poll_all_active()

        assert result == 0
        adapter.get_thread_messages.assert_not_awaited()
        lock.release()


class TestDedup:
    """Tests for external_id deduplication."""

    async def test_skips_message_with_existing_external_id(self):
        adapter = AsyncMock()
        adapter.get_thread_messages = AsyncMock(return_value=[{"external_id": "dup-1", "content": "Hello"}])
        poller = MessagePoller(adapters={"freelancer": adapter})

        bid = _make_bid(last_polled_at=None)
        neg = _make_negotiation()
        job = _make_job(platform="freelancer")

        session = AsyncMock()
        call_count = 0

        async def _execute_side(stmt, *a, **kw):
            nonlocal call_count
            call_count += 1
            res = MagicMock()
            if call_count == 1:
                unique = MagicMock()
                unique.all.return_value = [(neg, bid, job)]
                res.unique.return_value = unique
            else:
                # Dedup: message exists
                res.scalar_one_or_none.return_value = uuid.uuid4()
            return res

        session.execute = AsyncMock(side_effect=_execute_side)
        session.add = MagicMock()
        session.commit = AsyncMock()

        with patch("src.negotiations.poller.get_db_session") as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await poller.poll_all_active()

        assert result == 0
        session.add.assert_not_called()

    async def test_saves_new_message_when_no_dup(self):
        adapter = AsyncMock()
        adapter.get_thread_messages = AsyncMock(return_value=[{"external_id": "new-1", "content": "Hi there"}])
        poller = MessagePoller(adapters={"freelancer": adapter})

        bid = _make_bid(last_polled_at=None)
        neg = _make_negotiation()
        job = _make_job(platform="freelancer")

        session = AsyncMock()
        call_count = 0

        async def _execute_side(stmt, *a, **kw):
            nonlocal call_count
            call_count += 1
            res = MagicMock()
            if call_count == 1:
                unique = MagicMock()
                unique.all.return_value = [(neg, bid, job)]
                res.unique.return_value = unique
            else:
                res.scalar_one_or_none.return_value = None
            return res

        session.execute = AsyncMock(side_effect=_execute_side)
        session.add = MagicMock()
        session.commit = AsyncMock()

        with patch("src.negotiations.poller.get_db_session") as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await poller.poll_all_active()

        assert result == 1
        session.add.assert_called_once()

    async def test_skips_message_without_external_id(self):
        adapter = AsyncMock()
        adapter.get_thread_messages = AsyncMock(return_value=[{"content": "No id"}])
        poller = MessagePoller(adapters={"freelancer": adapter})

        bid = _make_bid(last_polled_at=None)
        neg = _make_negotiation()
        job = _make_job(platform="freelancer")

        session = AsyncMock()
        call_count = 0

        async def _execute_side(stmt, *a, **kw):
            nonlocal call_count
            call_count += 1
            res = MagicMock()
            if call_count == 1:
                unique = MagicMock()
                unique.all.return_value = [(neg, bid, job)]
                res.unique.return_value = unique
            else:
                res.scalar_one_or_none.return_value = None
            return res

        session.execute = AsyncMock(side_effect=_execute_side)
        session.add = MagicMock()
        session.commit = AsyncMock()

        with patch("src.negotiations.poller.get_db_session") as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await poller.poll_all_active()

        assert result == 0
        session.add.assert_not_called()


class TestErrorHandling:
    """Tests for adapter error resilience."""

    async def test_oserror_logged_not_raised(self):
        adapter = AsyncMock()
        adapter.get_thread_messages = AsyncMock(side_effect=OSError("connection failed"))
        poller = MessagePoller(adapters={"freelancer": adapter})

        bid = _make_bid(last_polled_at=None)
        neg = _make_negotiation()
        job = _make_job(platform="freelancer")

        session = AsyncMock()
        res = MagicMock()
        unique = MagicMock()
        unique.all.return_value = [(neg, bid, job)]
        res.unique.return_value = unique
        session.execute = AsyncMock(return_value=res)
        session.commit = AsyncMock()

        with patch("src.negotiations.poller.get_db_session") as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await poller.poll_all_active()

        assert result == 0

    async def test_unexpected_exception_logged_not_raised(self):
        adapter = AsyncMock()
        adapter.get_thread_messages = AsyncMock(side_effect=RuntimeError("boom"))
        poller = MessagePoller(adapters={"freelancer": adapter})

        bid = _make_bid(last_polled_at=None)
        neg = _make_negotiation()
        job = _make_job(platform="freelancer")

        session = AsyncMock()
        res = MagicMock()
        unique = MagicMock()
        unique.all.return_value = [(neg, bid, job)]
        res.unique.return_value = unique
        session.execute = AsyncMock(return_value=res)
        session.commit = AsyncMock()

        with patch("src.negotiations.poller.get_db_session") as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await poller.poll_all_active()

        assert result == 0

    async def test_connection_error_logged_not_raised(self):
        adapter = AsyncMock()
        adapter.get_thread_messages = AsyncMock(side_effect=ConnectionError("refused"))
        poller = MessagePoller(adapters={"freelancer": adapter})

        bid = _make_bid(last_polled_at=None)
        neg = _make_negotiation()
        job = _make_job(platform="freelancer")

        session = AsyncMock()
        res = MagicMock()
        unique = MagicMock()
        unique.all.return_value = [(neg, bid, job)]
        res.unique.return_value = unique
        session.execute = AsyncMock(return_value=res)
        session.commit = AsyncMock()

        with patch("src.negotiations.poller.get_db_session") as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await poller.poll_all_active()

        assert result == 0


class TestPlatformDispatch:
    """Tests for _fetch_messages dispatch to correct adapter method per platform."""

    async def test_freelancer_calls_get_thread_messages(self):
        adapter = AsyncMock()
        adapter.get_thread_messages = AsyncMock(return_value=[])
        poller = MessagePoller(adapters={"freelancer": adapter})

        bid = _make_bid(platform_thread_id="t-123", last_polled_at=None)
        result = await poller._fetch_messages(adapter, bid, "freelancer")

        adapter.get_thread_messages.assert_awaited_once()
        assert result == []

    async def test_freelancer_falls_back_to_platform_bid_id(self):
        adapter = AsyncMock()
        adapter.get_thread_messages = AsyncMock(return_value=[])
        poller = MessagePoller(adapters={"freelancer": adapter})

        bid = _make_bid(platform_thread_id=None, platform_bid_id="bid-456")
        await poller._fetch_messages(adapter, bid, "freelancer")

        call_kwargs = adapter.get_thread_messages.call_args
        assert call_kwargs.kwargs["thread_id"] == "bid-456"

    async def test_freelancer_returns_empty_if_no_thread_id(self):
        adapter = AsyncMock()
        poller = MessagePoller(adapters={"freelancer": adapter})

        bid = _make_bid(platform_thread_id=None, platform_bid_id=None)
        result = await poller._fetch_messages(adapter, bid, "freelancer")

        assert result == []

    async def test_kwork_calls_scrape_chat(self):
        adapter = AsyncMock()
        adapter.scrape_chat = AsyncMock(return_value=[])
        poller = MessagePoller(adapters={"kwork": adapter})

        bid = _make_bid(platform_bid_id="kwork-order-1")
        result = await poller._fetch_messages(adapter, bid, "kwork")

        adapter.scrape_chat.assert_awaited_once()
        assert result == []

    async def test_fl_ru_calls_scrape_inbox_thread(self):
        adapter = AsyncMock()
        adapter.scrape_inbox_thread = AsyncMock(return_value=[])
        poller = MessagePoller(adapters={"fl_ru": adapter})

        bid = _make_bid(platform_bid_id="fl-project-url")
        await poller._fetch_messages(adapter, bid, "fl_ru")

        adapter.scrape_inbox_thread.assert_awaited_once()

    async def test_telegram_calls_get_direct_messages(self):
        adapter = AsyncMock()
        adapter.get_direct_messages = AsyncMock(return_value=[])
        poller = MessagePoller(adapters={"telegram": adapter})

        bid = _make_bid(client_telegram_id=12345)
        await poller._fetch_messages(adapter, bid, "telegram")

        adapter.get_direct_messages.assert_awaited_once()

    async def test_unknown_platform_returns_empty(self):
        poller = MessagePoller(adapters={})
        bid = _make_bid()
        result = await poller._fetch_messages(AsyncMock(), bid, "myspace")
        assert result == []


class TestSaveOutboundMessage:
    """Tests for save_outbound_message helper."""

    async def test_creates_outbound_message_defaults(self):
        poller = MessagePoller()
        session = AsyncMock()
        session.add = MagicMock()

        bid_id = uuid.uuid4()
        cm = await poller.save_outbound_message(session, bid_id, "Hello client")

        session.add.assert_called_once()
        assert cm.direction == "outbound"
        assert cm.sender == "ai"
        assert cm.auto_generated is True
        assert cm.content == "Hello client"
        assert cm.bid_id == bid_id

    async def test_creates_operator_message(self):
        poller = MessagePoller()
        session = AsyncMock()
        session.add = MagicMock()

        bid_id = uuid.uuid4()
        cm = await poller.save_outbound_message(
            session,
            bid_id,
            "Manual reply",
            sender="operator",
            auto_generated=False,
            platform="freelancer",
        )

        assert cm.sender == "operator"
        assert cm.auto_generated is False
        assert cm.platform == "freelancer"


class TestPollingIntervals:
    """Tests for configuration constants."""

    def test_terminal_states_include_expected(self):
        assert "won" in _TERMINAL_NEGOTIATION_STATES
        assert "lost" in _TERMINAL_NEGOTIATION_STATES
        assert "stale" in _TERMINAL_NEGOTIATION_STATES
        assert "accepted" in _TERMINAL_NEGOTIATION_STATES
        assert "declined" in _TERMINAL_NEGOTIATION_STATES

    def test_active_states_not_terminal(self):
        assert "qualifying" not in _TERMINAL_NEGOTIATION_STATES
        assert "initial" not in _TERMINAL_NEGOTIATION_STATES

    def test_freelancer_interval_is_300(self):
        assert POLLING_INTERVALS["freelancer"] == 300

    def test_telegram_interval_is_zero(self):
        assert POLLING_INTERVALS["telegram"] == 0
