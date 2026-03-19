"""Unit tests for src.notifications.channels.

Covers TelegramChannel, WebSocketChannel, and EmailChannel.
All network/SMTP calls are mocked.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.notifications.channels import (
    EmailChannel,
    TelegramChannel,
    WebSocketChannel,
    _escape_html,
    format_email_html,
    format_telegram_batch,
    format_telegram_message,
)
from src.notifications.models import Notification, Severity

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_notification(severity: Severity = Severity.ERROR, **kwargs) -> Notification:
    defaults = {
        "title": "Test alert",
        "message": "Something happened",
        "source": "test_source",
        "agent_name": "scout",
    }
    defaults.update(kwargs)
    return Notification(severity=severity, **defaults)


# ---------------------------------------------------------------------------
# HTML escaping
# ---------------------------------------------------------------------------


class TestEscapeHtml:
    def test_escapes_ampersand(self):
        assert _escape_html("a & b") == "a &amp; b"

    def test_escapes_angle_brackets(self):
        assert _escape_html("<script>") == "&lt;script&gt;"

    def test_no_escape_needed(self):
        assert _escape_html("hello world") == "hello world"

    def test_combined(self):
        assert _escape_html("x<&>y") == "x&lt;&amp;&gt;y"


# ---------------------------------------------------------------------------
# format_telegram_message
# ---------------------------------------------------------------------------


class TestFormatTelegramMessage:
    def test_contains_severity(self):
        n = _make_notification(Severity.CRITICAL)
        text = format_telegram_message(n)
        assert "[CRITICAL]" in text

    def test_contains_title(self):
        n = _make_notification(title="DB down")
        text = format_telegram_message(n)
        assert "DB down" in text

    def test_contains_source(self):
        n = _make_notification(source="heartbeat")
        text = format_telegram_message(n)
        assert "heartbeat" in text

    def test_contains_agent_name(self):
        n = _make_notification(agent_name="bid")
        text = format_telegram_message(n)
        assert "bid" in text

    def test_long_message_truncated(self):
        n = _make_notification(message="x" * 600)
        text = format_telegram_message(n)
        assert "..." in text
        # Should not contain more than 500 chars of the message
        assert "x" * 501 not in text

    def test_contains_notification_id(self):
        n = _make_notification()
        text = format_telegram_message(n)
        assert n.id in text

    def test_escapes_html_in_title(self):
        n = _make_notification(title="x<script>y")
        text = format_telegram_message(n)
        assert "&lt;script&gt;" in text
        assert "<script>" not in text


# ---------------------------------------------------------------------------
# format_telegram_batch
# ---------------------------------------------------------------------------


class TestFormatTelegramBatch:
    def test_batch_contains_count(self):
        ns = [_make_notification(Severity.WARNING, title=f"w{i}") for i in range(3)]
        text = format_telegram_batch(ns)
        assert "3 alerts" in text

    def test_batch_contains_all_titles(self):
        ns = [
            _make_notification(Severity.WARNING, title="warn1"),
            _make_notification(Severity.ERROR, title="err1"),
        ]
        text = format_telegram_batch(ns)
        assert "warn1" in text
        assert "err1" in text

    def test_batch_shows_severity_counts(self):
        ns = [
            _make_notification(Severity.WARNING),
            _make_notification(Severity.WARNING),
            _make_notification(Severity.ERROR),
        ]
        text = format_telegram_batch(ns)
        assert "2 warning" in text
        assert "1 error" in text


# ---------------------------------------------------------------------------
# TelegramChannel
# ---------------------------------------------------------------------------


class TestTelegramChannel:
    def test_init_requires_token(self):
        with pytest.raises(ValueError, match="token"):
            TelegramChannel(token="", chat_id="123")

    def test_init_requires_chat_id(self):
        with pytest.raises(ValueError, match="chat_id"):
            TelegramChannel(token="tok", chat_id="")

    @patch("src.notifications.channels.httpx.AsyncClient")
    async def test_send_success(self, mock_client_cls: MagicMock):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        ch = TelegramChannel(token="tok123", chat_id="456")
        n = _make_notification(Severity.ERROR)
        result = await ch.send(n)

        assert result is True
        mock_client.post.assert_awaited_once()
        call_args = mock_client.post.call_args
        assert "tok123" in call_args[0][0]
        payload = call_args[1]["json"]
        assert payload["chat_id"] == "456"
        assert payload["parse_mode"] == "HTML"

    @patch("src.notifications.channels.httpx.AsyncClient")
    async def test_send_api_not_ok(self, mock_client_cls: MagicMock):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": False, "description": "chat not found"}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        ch = TelegramChannel(token="tok", chat_id="456")
        result = await ch.send(_make_notification())

        assert result is False

    @patch("src.notifications.channels.httpx.AsyncClient")
    async def test_send_http_error(self, mock_client_cls: MagicMock):
        import httpx

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("timeout"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        ch = TelegramChannel(token="tok", chat_id="456")
        result = await ch.send(_make_notification())

        assert result is False

    @patch("src.notifications.channels.httpx.AsyncClient")
    async def test_send_batch_single(self, mock_client_cls: MagicMock):
        """A batch of 1 should call send() directly."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        ch = TelegramChannel(token="tok", chat_id="456")
        result = await ch.send_batch([_make_notification()])

        assert result is True
        # Should send the individual message, not a batch summary.
        call_text = mock_client.post.call_args[1]["json"]["text"]
        assert "[ERROR]" in call_text

    @patch("src.notifications.channels.httpx.AsyncClient")
    async def test_send_batch_multiple(self, mock_client_cls: MagicMock):
        """A batch of >1 should send a batch summary."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True}
        mock_resp.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        ch = TelegramChannel(token="tok", chat_id="456")
        ns = [_make_notification(title=f"n{i}") for i in range(3)]
        result = await ch.send_batch(ns)

        assert result is True
        call_text = mock_client.post.call_args[1]["json"]["text"]
        assert "Batch Summary" in call_text

    async def test_send_batch_empty(self):
        ch = TelegramChannel(token="tok", chat_id="456")
        result = await ch.send_batch([])
        assert result is True


# ---------------------------------------------------------------------------
# WebSocketChannel
# ---------------------------------------------------------------------------


class TestWebSocketChannel:
    async def test_send_no_plugin_returns_false(self):
        ch = WebSocketChannel(channels_plugin=None)
        n = _make_notification()
        result = await ch.send(n)
        assert result is False

    async def test_send_publishes_json(self):
        mock_plugin = MagicMock()
        ch = WebSocketChannel(channels_plugin=mock_plugin)
        n = _make_notification()

        result = await ch.send(n)

        assert result is True
        mock_plugin.publish.assert_called_once()
        call_args = mock_plugin.publish.call_args
        payload = json.loads(call_args[0][0])
        assert payload["severity"] == "error"
        assert payload["title"] == "Test alert"
        assert call_args[0][1] == ["notification"]

    async def test_send_os_error_returns_false(self):
        mock_plugin = MagicMock()
        mock_plugin.publish.side_effect = OSError("broken pipe")
        ch = WebSocketChannel(channels_plugin=mock_plugin)

        result = await ch.send(_make_notification())
        assert result is False

    async def test_send_connection_error_returns_false(self):
        mock_plugin = MagicMock()
        mock_plugin.publish.side_effect = ConnectionError("refused")
        ch = WebSocketChannel(channels_plugin=mock_plugin)

        result = await ch.send(_make_notification())
        assert result is False


# ---------------------------------------------------------------------------
# EmailChannel
# ---------------------------------------------------------------------------


class TestEmailChannel:
    def test_configured_false_when_empty(self):
        ch = EmailChannel(smtp_host="", smtp_port=587)
        assert ch.configured is False

    def test_configured_true_when_set(self):
        ch = EmailChannel(
            smtp_host="smtp.example.com",
            from_addr="noreply@example.com",
            to_addr="admin@example.com",
        )
        assert ch.configured is True

    async def test_send_batch_not_configured_returns_false(self):
        ch = EmailChannel(smtp_host="")
        result = await ch.send_batch([_make_notification()])
        assert result is False

    async def test_send_batch_empty_returns_true(self):
        ch = EmailChannel(smtp_host="smtp.example.com", from_addr="a@b.c", to_addr="d@e.f")
        result = await ch.send_batch([])
        assert result is True

    @patch("src.notifications.channels.aiosmtplib", create=True)
    async def test_send_batch_success(self, mock_aiosmtplib: MagicMock):
        mock_aiosmtplib.send = AsyncMock()

        ch = EmailChannel(
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_user="user",
            smtp_password="pass",
            from_addr="noreply@example.com",
            to_addr="admin@example.com",
        )
        ns = [_make_notification(Severity.WARNING, title=f"w{i}") for i in range(2)]

        with patch.dict("sys.modules", {"aiosmtplib": mock_aiosmtplib}):
            result = await ch.send_batch(ns)

        assert result is True

    async def test_send_delegates_to_send_batch(self):
        """send() should call send_batch([notification])."""
        ch = EmailChannel(smtp_host="")
        # Not configured, so returns False — but verifies the delegation path.
        result = await ch.send(_make_notification())
        assert result is False


# ---------------------------------------------------------------------------
# format_email_html
# ---------------------------------------------------------------------------


class TestFormatEmailHtml:
    def test_contains_notification_count(self):
        ns = [_make_notification() for _ in range(5)]
        html = format_email_html(ns)
        assert "5 notifications" in html

    def test_contains_severity(self):
        ns = [_make_notification(Severity.CRITICAL)]
        html = format_email_html(ns)
        assert "CRITICAL" in html

    def test_contains_title(self):
        ns = [_make_notification(title="DB connection lost")]
        html = format_email_html(ns)
        assert "DB connection lost" in html

    def test_escapes_html(self):
        ns = [_make_notification(title="<script>alert(1)</script>")]
        html = format_email_html(ns)
        assert "&lt;script&gt;" in html
        assert "<script>alert" not in html

    def test_sorted_by_severity(self):
        ns = [
            _make_notification(Severity.INFO, title="info_item"),
            _make_notification(Severity.CRITICAL, title="critical_item"),
        ]
        html = format_email_html(ns)
        # CRITICAL should appear before INFO in the output.
        crit_pos = html.index("CRITICAL")
        info_pos = html.index("INFO")
        assert crit_pos < info_pos
