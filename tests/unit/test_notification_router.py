"""Unit tests for src.notifications.router.NotificationRouter.

Covers the deterministic severity-based routing table:
    critical → Telegram + dashboard
    error    → Telegram + dashboard
    warning  → batch + dashboard
    info     → dashboard only

All channels are mocked — no network or Valkey calls.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.notifications.batch import BatchCollector
from src.notifications.channels import (
    EmailChannel,
    TelegramChannel,
    WebSocketChannel,
)
from src.notifications.models import Notification, Severity
from src.notifications.router import NotificationRouter

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_telegram() -> AsyncMock:
    """Mock TelegramChannel."""
    ch = AsyncMock(spec=TelegramChannel)
    ch.send = AsyncMock(return_value=True)
    ch.send_batch = AsyncMock(return_value=True)
    return ch


@pytest.fixture()
def mock_websocket() -> AsyncMock:
    """Mock WebSocketChannel."""
    ch = AsyncMock(spec=WebSocketChannel)
    ch.send = AsyncMock(return_value=True)
    return ch


@pytest.fixture()
def mock_email() -> AsyncMock:
    """Mock EmailChannel."""
    ch = AsyncMock(spec=EmailChannel)
    ch.send_batch = AsyncMock(return_value=True)
    return ch


@pytest.fixture()
def mock_batch() -> AsyncMock:
    """Mock BatchCollector."""
    b = AsyncMock(spec=BatchCollector)
    b.add = AsyncMock(return_value=1)
    b.flush = AsyncMock(return_value=[])
    b.size = AsyncMock(return_value=0)
    b.is_window_elapsed = AsyncMock(return_value=False)
    return b


@pytest.fixture()
def router(
    mock_telegram: AsyncMock,
    mock_websocket: AsyncMock,
    mock_email: AsyncMock,
    mock_batch: AsyncMock,
) -> NotificationRouter:
    """Fully-wired router with all channels mocked."""
    return NotificationRouter(
        telegram=mock_telegram,
        websocket=mock_websocket,
        email=mock_email,
        batch=mock_batch,
    )


def _make_notification(severity: Severity, **kwargs) -> Notification:
    """Helper to create a test notification."""
    defaults = {
        "title": f"Test {severity.value}",
        "message": f"Test message for {severity.value}",
        "source": "test",
    }
    defaults.update(kwargs)
    return Notification(severity=severity, **defaults)


# ---------------------------------------------------------------------------
# Routing: CRITICAL
# ---------------------------------------------------------------------------


class TestCriticalRouting:
    """critical → Telegram + dashboard."""

    async def test_critical_sends_telegram(self, router: NotificationRouter, mock_telegram: AsyncMock):
        n = _make_notification(Severity.CRITICAL)
        results = await router.notify(n)
        mock_telegram.send.assert_awaited_once_with(n)
        assert results["telegram"] is True

    async def test_critical_sends_websocket(self, router: NotificationRouter, mock_websocket: AsyncMock):
        n = _make_notification(Severity.CRITICAL)
        results = await router.notify(n)
        mock_websocket.send.assert_awaited_once_with(n)
        assert results["websocket"] is True

    async def test_critical_does_not_batch(self, router: NotificationRouter, mock_batch: AsyncMock):
        n = _make_notification(Severity.CRITICAL)
        results = await router.notify(n)
        mock_batch.add.assert_not_awaited()
        assert "batched" not in results


# ---------------------------------------------------------------------------
# Routing: ERROR
# ---------------------------------------------------------------------------


class TestErrorRouting:
    """error → Telegram + dashboard."""

    async def test_error_sends_telegram(self, router: NotificationRouter, mock_telegram: AsyncMock):
        n = _make_notification(Severity.ERROR)
        results = await router.notify(n)
        mock_telegram.send.assert_awaited_once_with(n)
        assert results["telegram"] is True

    async def test_error_sends_websocket(self, router: NotificationRouter, mock_websocket: AsyncMock):
        n = _make_notification(Severity.ERROR)
        results = await router.notify(n)
        mock_websocket.send.assert_awaited_once_with(n)
        assert results["websocket"] is True

    async def test_error_does_not_batch(self, router: NotificationRouter, mock_batch: AsyncMock):
        n = _make_notification(Severity.ERROR)
        results = await router.notify(n)
        mock_batch.add.assert_not_awaited()
        assert "batched" not in results


# ---------------------------------------------------------------------------
# Routing: WARNING
# ---------------------------------------------------------------------------


class TestWarningRouting:
    """warning → batch + dashboard."""

    async def test_warning_batched(self, router: NotificationRouter, mock_batch: AsyncMock):
        n = _make_notification(Severity.WARNING)
        results = await router.notify(n)
        mock_batch.add.assert_awaited_once_with(n)
        assert results["batched"] is True

    async def test_warning_sends_websocket(self, router: NotificationRouter, mock_websocket: AsyncMock):
        n = _make_notification(Severity.WARNING)
        results = await router.notify(n)
        mock_websocket.send.assert_awaited_once_with(n)
        assert results["websocket"] is True

    async def test_warning_does_not_send_telegram_immediately(
        self, router: NotificationRouter, mock_telegram: AsyncMock
    ):
        n = _make_notification(Severity.WARNING)
        await router.notify(n)
        mock_telegram.send.assert_not_awaited()


# ---------------------------------------------------------------------------
# Routing: INFO
# ---------------------------------------------------------------------------


class TestInfoRouting:
    """info → dashboard only."""

    async def test_info_sends_websocket(self, router: NotificationRouter, mock_websocket: AsyncMock):
        n = _make_notification(Severity.INFO)
        results = await router.notify(n)
        mock_websocket.send.assert_awaited_once_with(n)
        assert results["websocket"] is True

    async def test_info_does_not_send_telegram(self, router: NotificationRouter, mock_telegram: AsyncMock):
        n = _make_notification(Severity.INFO)
        await router.notify(n)
        mock_telegram.send.assert_not_awaited()

    async def test_info_does_not_batch(self, router: NotificationRouter, mock_batch: AsyncMock):
        n = _make_notification(Severity.INFO)
        results = await router.notify(n)
        mock_batch.add.assert_not_awaited()
        assert "batched" not in results


# ---------------------------------------------------------------------------
# Flush warnings
# ---------------------------------------------------------------------------


class TestFlushWarnings:
    """flush_warnings() drains batch and sends via Telegram + Email."""

    async def test_flush_sends_batch_to_telegram_and_email(
        self,
        router: NotificationRouter,
        mock_batch: AsyncMock,
        mock_telegram: AsyncMock,
        mock_email: AsyncMock,
    ):
        warnings = [
            _make_notification(Severity.WARNING, title="warn1"),
            _make_notification(Severity.WARNING, title="warn2"),
        ]
        mock_batch.flush.return_value = warnings

        results = await router.flush_warnings()

        assert results["count"] == 2
        mock_telegram.send_batch.assert_awaited_once_with(warnings)
        mock_email.send_batch.assert_awaited_once_with(warnings)
        assert results["telegram"] is True
        assert results["email"] is True

    async def test_flush_empty_returns_zero(self, router: NotificationRouter, mock_batch: AsyncMock):
        mock_batch.flush.return_value = []
        results = await router.flush_warnings()
        assert results["count"] == 0

    async def test_flush_telegram_error_does_not_block_email(
        self,
        router: NotificationRouter,
        mock_batch: AsyncMock,
        mock_telegram: AsyncMock,
        mock_email: AsyncMock,
    ):
        warnings = [_make_notification(Severity.WARNING)]
        mock_batch.flush.return_value = warnings
        mock_telegram.send_batch.side_effect = RuntimeError("network")

        results = await router.flush_warnings()

        assert results["telegram"] is False
        mock_email.send_batch.assert_awaited_once()
        assert results["email"] is True

    async def test_flush_email_error_does_not_block_telegram(
        self,
        router: NotificationRouter,
        mock_batch: AsyncMock,
        mock_telegram: AsyncMock,
        mock_email: AsyncMock,
    ):
        warnings = [_make_notification(Severity.WARNING)]
        mock_batch.flush.return_value = warnings
        mock_email.send_batch.side_effect = RuntimeError("smtp down")

        results = await router.flush_warnings()

        assert results["email"] is False
        mock_telegram.send_batch.assert_awaited_once()
        assert results["telegram"] is True


# ---------------------------------------------------------------------------
# flush_if_ready
# ---------------------------------------------------------------------------


class TestFlushIfReady:
    """flush_if_ready() checks window before flushing."""

    async def test_not_ready_returns_empty(self, router: NotificationRouter, mock_batch: AsyncMock):
        mock_batch.is_window_elapsed.return_value = False
        results = await router.flush_if_ready()
        assert results["count"] == 0
        mock_batch.flush.assert_not_awaited()

    async def test_ready_but_empty_queue_returns_empty(self, router: NotificationRouter, mock_batch: AsyncMock):
        mock_batch.is_window_elapsed.return_value = True
        mock_batch.size.return_value = 0
        results = await router.flush_if_ready()
        assert results["count"] == 0

    async def test_ready_with_items_flushes(
        self,
        router: NotificationRouter,
        mock_batch: AsyncMock,
        mock_telegram: AsyncMock,
    ):
        mock_batch.is_window_elapsed.return_value = True
        mock_batch.size.return_value = 3
        warnings = [_make_notification(Severity.WARNING) for _ in range(3)]
        mock_batch.flush.return_value = warnings

        results = await router.flush_if_ready()

        assert results["count"] == 3
        mock_telegram.send_batch.assert_awaited_once()


# ---------------------------------------------------------------------------
# Graceful degradation
# ---------------------------------------------------------------------------


class TestGracefulDegradation:
    """Router works even when channels are None (partial config)."""

    async def test_no_telegram_critical_still_works(self):
        ws = AsyncMock(spec=WebSocketChannel)
        ws.send = AsyncMock(return_value=True)
        router = NotificationRouter(websocket=ws)

        n = _make_notification(Severity.CRITICAL)
        results = await router.notify(n)

        assert results["telegram"] is False
        assert results["websocket"] is True

    async def test_no_websocket_error_still_works(self):
        tg = AsyncMock(spec=TelegramChannel)
        tg.send = AsyncMock(return_value=True)
        router = NotificationRouter(telegram=tg)

        n = _make_notification(Severity.ERROR)
        results = await router.notify(n)

        assert results["telegram"] is True
        assert results["websocket"] is False

    async def test_no_batch_warning_returns_false(self):
        ws = AsyncMock(spec=WebSocketChannel)
        ws.send = AsyncMock(return_value=True)
        router = NotificationRouter(websocket=ws)

        n = _make_notification(Severity.WARNING)
        results = await router.notify(n)

        assert results["batched"] is False

    async def test_no_channels_at_all(self):
        router = NotificationRouter()
        n = _make_notification(Severity.CRITICAL)
        results = await router.notify(n)

        assert results["websocket"] is False
        assert results["telegram"] is False

    async def test_flush_no_batch_returns_empty(self):
        router = NotificationRouter()
        results = await router.flush_warnings()
        assert results["count"] == 0

    async def test_flush_if_ready_no_batch_returns_empty(self):
        router = NotificationRouter()
        results = await router.flush_if_ready()
        assert results["count"] == 0


# ---------------------------------------------------------------------------
# Channel error isolation
# ---------------------------------------------------------------------------


class TestErrorIsolation:
    """One channel's failure must not affect others."""

    async def test_telegram_exception_does_not_crash_routing(
        self, router: NotificationRouter, mock_telegram: AsyncMock, mock_websocket: AsyncMock
    ):
        mock_telegram.send.side_effect = ConnectionError("network")
        n = _make_notification(Severity.CRITICAL)

        results = await router.notify(n)

        assert results["telegram"] is False
        mock_websocket.send.assert_awaited_once()
        assert results["websocket"] is True

    async def test_websocket_exception_does_not_crash_routing(
        self, router: NotificationRouter, mock_telegram: AsyncMock, mock_websocket: AsyncMock
    ):
        mock_websocket.send.side_effect = OSError("broken pipe")
        n = _make_notification(Severity.CRITICAL)

        results = await router.notify(n)

        assert results["websocket"] is False
        mock_telegram.send.assert_awaited_once()
        assert results["telegram"] is True

    async def test_batch_exception_does_not_crash_routing(
        self, router: NotificationRouter, mock_batch: AsyncMock, mock_websocket: AsyncMock
    ):
        mock_batch.add.side_effect = RuntimeError("valkey down")
        n = _make_notification(Severity.WARNING)

        results = await router.notify(n)

        assert results["batched"] is False
        mock_websocket.send.assert_awaited_once()
        assert results["websocket"] is True
