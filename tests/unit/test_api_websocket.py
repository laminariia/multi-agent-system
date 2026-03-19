"""Unit tests for the WebSocket handler.

Tests ``src.api.websocket`` — channel constants, ws_handler lifecycle,
authentication handshake, subscription management, and publish_event helper.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from litestar.exceptions import WebSocketDisconnect

from src.api.websocket import (
    _DEFAULT_CHANNELS,
    CHANNEL_AGENT_HEARTBEAT,
    CHANNEL_AGENT_LOG,
    CHANNEL_HITL_NEW,
    CHANNEL_HITL_RESOLVED,
    CHANNEL_NOTIFICATION,
    CHANNEL_PROJECT_UPDATE,
    _send_error,
    publish_event,
    ws_handler,
)

# ---------------------------------------------------------------------------
# Channel constants
# ---------------------------------------------------------------------------


class TestChannelConstants:
    """Tests for channel name constants."""

    def test_default_channels_contains_expected(self) -> None:
        assert CHANNEL_AGENT_HEARTBEAT in _DEFAULT_CHANNELS
        assert CHANNEL_AGENT_LOG in _DEFAULT_CHANNELS
        assert CHANNEL_HITL_NEW in _DEFAULT_CHANNELS
        assert CHANNEL_HITL_RESOLVED in _DEFAULT_CHANNELS
        assert CHANNEL_NOTIFICATION in _DEFAULT_CHANNELS

    def test_project_update_in_defaults(self) -> None:
        """Project updates auto-subscribed for authenticated users (H15 fix)."""
        assert CHANNEL_PROJECT_UPDATE in _DEFAULT_CHANNELS

    def test_default_channels_count(self) -> None:
        assert len(_DEFAULT_CHANNELS) == 10


# ---------------------------------------------------------------------------
# _send_error helper
# ---------------------------------------------------------------------------


class TestSendError:
    """Tests for the _send_error helper."""

    @pytest.mark.asyncio
    async def test_sends_error_json(self) -> None:
        mock_socket = AsyncMock()
        await _send_error(mock_socket, "Something went wrong")
        mock_socket.send_json.assert_awaited_once_with(
            {
                "type": "error",
                "message": "Something went wrong",
            }
        )

    @pytest.mark.asyncio
    async def test_sends_empty_error_message(self) -> None:
        mock_socket = AsyncMock()
        await _send_error(mock_socket, "")
        mock_socket.send_json.assert_awaited_once_with(
            {
                "type": "error",
                "message": "",
            }
        )


# ---------------------------------------------------------------------------
# publish_event helper
# ---------------------------------------------------------------------------


class TestPublishEvent:
    """Tests for the publish_event helper."""

    @pytest.mark.asyncio
    async def test_publishes_json_to_channel(self) -> None:
        mock_channels = AsyncMock()
        mock_channels.publish = MagicMock()
        data = {"hitl_id": "abc-123", "action": "approve"}

        await publish_event(mock_channels, "hitl:resolved", data)

        mock_channels.publish.assert_called_once()
        call_args = mock_channels.publish.call_args
        # First arg is the JSON string
        payload = json.loads(call_args[0][0])
        assert payload["hitl_id"] == "abc-123"
        assert payload["action"] == "approve"
        # Second arg is the channel list
        assert call_args[0][1] == ["hitl:resolved"]

    @pytest.mark.asyncio
    async def test_serializes_non_string_values(self) -> None:
        """publish_event uses default=str for non-JSON-native types."""
        mock_channels = AsyncMock()
        from datetime import UTC, datetime

        data = {"timestamp": datetime(2026, 1, 1, tzinfo=UTC)}

        await publish_event(mock_channels, "notification", data)

        call_args = mock_channels.publish.call_args
        payload = json.loads(call_args[0][0])
        assert "2026" in payload["timestamp"]


# ---------------------------------------------------------------------------
# ws_handler
# ---------------------------------------------------------------------------


class TestWsHandler:
    """Tests for the main WebSocket handler."""

    @pytest.mark.asyncio
    async def test_accepts_connection(self) -> None:
        """Handler should accept the WebSocket connection immediately."""
        mock_socket = AsyncMock()
        mock_channels = AsyncMock()

        # Disconnect after accept
        mock_socket.receive_text = AsyncMock(side_effect=WebSocketDisconnect)

        await ws_handler.fn(socket=mock_socket, channels=mock_channels)

        mock_socket.accept.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_invalid_json_sends_error(self) -> None:
        mock_socket = AsyncMock()
        mock_channels = AsyncMock()

        mock_socket.receive_text = AsyncMock(side_effect=["not valid json", WebSocketDisconnect])

        await ws_handler.fn(socket=mock_socket, channels=mock_channels)

        # Should have sent an error about invalid JSON
        mock_socket.send_json.assert_any_call(
            {
                "type": "error",
                "message": "Invalid JSON",
            }
        )

    @pytest.mark.asyncio
    async def test_auth_with_valid_token(self) -> None:
        mock_socket = AsyncMock()
        mock_channels = AsyncMock()
        mock_channels.subscribe = AsyncMock()

        mock_token = MagicMock()
        mock_token.sub = "user-123"

        auth_msg = json.dumps({"type": "auth", "token": "Bearer test-jwt"})  # noqa: S106

        mock_socket.receive_text = AsyncMock(side_effect=[auth_msg, WebSocketDisconnect])

        with patch("src.api.websocket.get_settings") as mock_settings:
            mock_settings.return_value.JWT_SECRET_KEY = "test-secret"  # noqa: S105
            with patch("src.api.websocket.Token.decode", return_value=mock_token):
                await ws_handler.fn(socket=mock_socket, channels=mock_channels)

        # Should have sent auth:ok
        calls = mock_socket.send_json.call_args_list
        auth_ok_calls = [c for c in calls if c[0][0].get("type") == "auth:ok"]
        assert len(auth_ok_calls) == 1
        assert auth_ok_calls[0][0][0]["user_id"] == "user-123"

    @pytest.mark.asyncio
    async def test_auth_subscribes_to_default_channels(self) -> None:
        mock_socket = AsyncMock()
        mock_channels = AsyncMock()
        mock_channels.subscribe = AsyncMock()

        mock_token = MagicMock()
        mock_token.sub = "user-123"

        auth_msg = json.dumps({"type": "auth", "token": "test-jwt"})  # noqa: S106

        mock_socket.receive_text = AsyncMock(side_effect=[auth_msg, WebSocketDisconnect])

        with patch("src.api.websocket.get_settings") as mock_settings:
            mock_settings.return_value.JWT_SECRET_KEY = "test-secret"  # noqa: S105
            with patch("src.api.websocket.Token.decode", return_value=mock_token):
                await ws_handler.fn(socket=mock_socket, channels=mock_channels)

        # Should have subscribed to all default channels
        assert mock_channels.subscribe.call_count == len(_DEFAULT_CHANNELS)

    @pytest.mark.asyncio
    async def test_auth_failed_sends_error(self) -> None:
        mock_socket = AsyncMock()
        mock_channels = AsyncMock()

        auth_msg = json.dumps({"type": "auth", "token": "bad-token"})  # noqa: S106

        mock_socket.receive_text = AsyncMock(side_effect=[auth_msg, WebSocketDisconnect])

        with patch("src.api.websocket.get_settings") as mock_settings:
            mock_settings.return_value.JWT_SECRET_KEY = "test-secret"  # noqa: S105
            with patch("src.api.websocket.Token.decode", side_effect=ValueError("bad token")):
                await ws_handler.fn(socket=mock_socket, channels=mock_channels)

        # Should have sent an error about auth failure
        calls = mock_socket.send_json.call_args_list
        error_calls = [c for c in calls if c[0][0].get("type") == "error"]
        assert len(error_calls) >= 1
        assert "Authentication failed" in error_calls[0][0][0]["message"]

    @pytest.mark.asyncio
    async def test_unauthenticated_message_rejected(self) -> None:
        mock_socket = AsyncMock()
        mock_channels = AsyncMock()

        # Try to subscribe without auth first
        sub_msg = json.dumps({"type": "subscribe:project", "project_id": "p1"})

        mock_socket.receive_text = AsyncMock(side_effect=[sub_msg, WebSocketDisconnect])

        await ws_handler.fn(socket=mock_socket, channels=mock_channels)

        # Should reject with "Not authenticated"
        calls = mock_socket.send_json.call_args_list
        error_calls = [c for c in calls if c[0][0].get("type") == "error"]
        assert any("Not authenticated" in c[0][0]["message"] for c in error_calls)

    @pytest.mark.asyncio
    async def test_subscribe_project_after_auth(self) -> None:
        mock_socket = AsyncMock()
        mock_channels = AsyncMock()
        mock_channels.subscribe = AsyncMock()

        mock_token = MagicMock()
        mock_token.sub = "user-123"

        auth_msg = json.dumps({"type": "auth", "token": "test-jwt"})  # noqa: S106
        sub_msg = json.dumps({"type": "subscribe:project", "project_id": "proj-42"})

        mock_socket.receive_text = AsyncMock(side_effect=[auth_msg, sub_msg, WebSocketDisconnect])

        with patch("src.api.websocket.get_settings") as mock_settings:
            mock_settings.return_value.JWT_SECRET_KEY = "test-secret"  # noqa: S105
            with patch("src.api.websocket.Token.decode", return_value=mock_token):
                await ws_handler.fn(socket=mock_socket, channels=mock_channels)

        # Should have subscribed to project channel
        sub_calls = mock_channels.subscribe.call_args_list
        project_subs = [c for c in sub_calls if any("project:proj-42" in ch for ch in c[0][1])]
        assert len(project_subs) == 1

        # Should have sent subscribed confirmation
        calls = mock_socket.send_json.call_args_list
        sub_oks = [c for c in calls if c[0][0].get("type") == "subscribed"]
        assert any(c[0][0].get("channel") == "project:proj-42" for c in sub_oks)

    @pytest.mark.asyncio
    async def test_subscribe_agent_after_auth(self) -> None:
        mock_socket = AsyncMock()
        mock_channels = AsyncMock()
        mock_channels.subscribe = AsyncMock()

        mock_token = MagicMock()
        mock_token.sub = "user-123"

        auth_msg = json.dumps({"type": "auth", "token": "test-jwt"})  # noqa: S106
        sub_msg = json.dumps({"type": "subscribe:agent", "agent": "scout"})

        mock_socket.receive_text = AsyncMock(side_effect=[auth_msg, sub_msg, WebSocketDisconnect])

        with patch("src.api.websocket.get_settings") as mock_settings:
            mock_settings.return_value.JWT_SECRET_KEY = "test-secret"  # noqa: S105
            with patch("src.api.websocket.Token.decode", return_value=mock_token):
                await ws_handler.fn(socket=mock_socket, channels=mock_channels)

        # Should have sent subscribed confirmation for agent channel
        calls = mock_socket.send_json.call_args_list
        sub_oks = [c for c in calls if c[0][0].get("type") == "subscribed"]
        assert any(c[0][0].get("channel") == "agent:scout" for c in sub_oks)

    @pytest.mark.asyncio
    async def test_unknown_message_type_sends_error(self) -> None:
        mock_socket = AsyncMock()
        mock_channels = AsyncMock()
        mock_channels.subscribe = AsyncMock()

        mock_token = MagicMock()
        mock_token.sub = "user-123"

        auth_msg = json.dumps({"type": "auth", "token": "test-jwt"})  # noqa: S106
        unknown_msg = json.dumps({"type": "some_unknown_type"})

        mock_socket.receive_text = AsyncMock(side_effect=[auth_msg, unknown_msg, WebSocketDisconnect])

        with patch("src.api.websocket.get_settings") as mock_settings:
            mock_settings.return_value.JWT_SECRET_KEY = "test-secret"  # noqa: S105
            with patch("src.api.websocket.Token.decode", return_value=mock_token):
                await ws_handler.fn(socket=mock_socket, channels=mock_channels)

        calls = mock_socket.send_json.call_args_list
        error_calls = [c for c in calls if c[0][0].get("type") == "error"]
        assert any("Unknown message type" in c[0][0]["message"] for c in error_calls)

    @pytest.mark.asyncio
    async def test_disconnect_unsubscribes_channels(self) -> None:
        mock_socket = AsyncMock()
        mock_channels = AsyncMock()
        mock_channels.subscribe = AsyncMock()
        mock_channels.unsubscribe = AsyncMock()

        mock_token = MagicMock()
        mock_token.sub = "user-123"

        auth_msg = json.dumps({"type": "auth", "token": "test-jwt"})  # noqa: S106

        mock_socket.receive_text = AsyncMock(side_effect=[auth_msg, WebSocketDisconnect])

        with patch("src.api.websocket.get_settings") as mock_settings:
            mock_settings.return_value.JWT_SECRET_KEY = "test-secret"  # noqa: S105
            with patch("src.api.websocket.Token.decode", return_value=mock_token):
                await ws_handler.fn(socket=mock_socket, channels=mock_channels)

        # Should have unsubscribed from all channels in finally block
        assert mock_channels.unsubscribe.call_count >= len(_DEFAULT_CHANNELS)

    @pytest.mark.asyncio
    async def test_auth_strips_bearer_prefix(self) -> None:
        """Token.decode should receive the raw JWT, not 'Bearer ...'."""
        mock_socket = AsyncMock()
        mock_channels = AsyncMock()
        mock_channels.subscribe = AsyncMock()

        mock_token = MagicMock()
        mock_token.sub = "user-456"

        auth_msg = json.dumps({"type": "auth", "token": "Bearer my.jwt.token"})  # noqa: S106

        mock_socket.receive_text = AsyncMock(side_effect=[auth_msg, WebSocketDisconnect])

        with patch("src.api.websocket.get_settings") as mock_settings:
            mock_settings.return_value.JWT_SECRET_KEY = "test-secret"  # noqa: S105
            with patch("src.api.websocket.Token.decode", return_value=mock_token) as mock_decode:
                await ws_handler.fn(socket=mock_socket, channels=mock_channels)

        # Should have been called with the JWT only, not "Bearer ..."
        mock_decode.assert_called_once()
        call_kwargs = mock_decode.call_args[1]
        assert call_kwargs["encoded_token"] == "my.jwt.token"  # noqa: S105

    @pytest.mark.asyncio
    async def test_duplicate_subscription_not_added(self) -> None:
        """Subscribing to the same project twice should only subscribe once."""
        mock_socket = AsyncMock()
        mock_channels = AsyncMock()
        mock_channels.subscribe = AsyncMock()

        mock_token = MagicMock()
        mock_token.sub = "user-123"

        auth_msg = json.dumps({"type": "auth", "token": "test-jwt"})  # noqa: S106
        sub_msg1 = json.dumps({"type": "subscribe:project", "project_id": "proj-1"})
        sub_msg2 = json.dumps({"type": "subscribe:project", "project_id": "proj-1"})

        mock_socket.receive_text = AsyncMock(side_effect=[auth_msg, sub_msg1, sub_msg2, WebSocketDisconnect])

        with patch("src.api.websocket.get_settings") as mock_settings:
            mock_settings.return_value.JWT_SECRET_KEY = "test-secret"  # noqa: S105
            with patch("src.api.websocket.Token.decode", return_value=mock_token):
                await ws_handler.fn(socket=mock_socket, channels=mock_channels)

        # Count subscribe calls for project:proj-1
        project_subs = [
            c for c in mock_channels.subscribe.call_args_list if any("project:proj-1" in ch for ch in c[0][1])
        ]
        assert len(project_subs) == 1  # Only subscribed once

    @pytest.mark.asyncio
    async def test_generic_exception_handled(self) -> None:
        """Handler should not crash on unexpected exceptions."""
        mock_socket = AsyncMock()
        mock_channels = AsyncMock()

        mock_socket.receive_text = AsyncMock(side_effect=RuntimeError("unexpected"))

        # Should not raise
        await ws_handler.fn(socket=mock_socket, channels=mock_channels)

        mock_socket.accept.assert_awaited_once()
