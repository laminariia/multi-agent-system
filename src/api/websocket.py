"""WebSocket handler using Litestar ChannelsPlugin.

Events flow:
    Server -> Client: agent:heartbeat, agent:log, hitl:new, hitl:resolved,
                      project:update, notification
    Client -> Server: auth (token), subscribe:project, subscribe:agent

Authentication is performed via a ``{ "type": "auth", "token": "Bearer ..." }``
message sent by the client immediately after the WebSocket connection opens.
Until authenticated, the server will not forward any events.
"""
from __future__ import annotations

import json
from typing import Any

import structlog
from litestar import WebSocket, websocket
from litestar.channels import ChannelsPlugin
from litestar.exceptions import WebSocketDisconnect
from litestar.security.jwt import Token

from src.core.config import get_settings

logger = structlog.get_logger(__name__)

# Channel names used throughout the system.  Backend publishers (agents,
# HITL resolver, etc.) write to these channels; the WebSocket handler
# subscribes connected clients to them.
CHANNEL_AGENT_HEARTBEAT = "agent:heartbeat"
CHANNEL_AGENT_LOG = "agent:log"
CHANNEL_HITL_NEW = "hitl:new"
CHANNEL_HITL_RESOLVED = "hitl:resolved"
CHANNEL_PROJECT_UPDATE = "project:update"
CHANNEL_NOTIFICATION = "notification"
CHANNEL_ORCH_STATUS = "orch:status"
CHANNEL_ORCH_GOAL = "orch:goal"
CHANNEL_ORCH_LOG = "orch:log"

# All broadcast channels every authenticated client receives by default
_DEFAULT_CHANNELS = [
    CHANNEL_AGENT_HEARTBEAT,
    CHANNEL_AGENT_LOG,
    CHANNEL_HITL_NEW,
    CHANNEL_HITL_RESOLVED,
    CHANNEL_NOTIFICATION,
    CHANNEL_ORCH_STATUS,
    CHANNEL_ORCH_GOAL,
    CHANNEL_ORCH_LOG,
]


@websocket("/ws/events", exclude_from_auth=True)
async def ws_handler(socket: WebSocket, channels: ChannelsPlugin) -> None:
    """Main WebSocket endpoint at ``/ws/events``.

    Lifecycle:
        1. Accept connection.
        2. Wait for an ``auth`` message carrying a JWT.
        3. Validate the token; subscribe to default channels.
        4. Forward backend channel messages to the client.
        5. Listen for client ``subscribe:project`` / ``subscribe:agent``
           messages and subscribe to the corresponding channels.
    """
    await socket.accept()
    authenticated = False
    user_id: str | None = None
    subscribed_channels: set[str] = set()

    try:
        while True:
            raw = await socket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                await _send_error(socket, "Invalid JSON")
                continue

            msg_type = message.get("type", "")

            # ── Auth handshake ──────────────────────────────────────
            if msg_type == "auth":
                token_str = message.get("token", "")
                if token_str.startswith("Bearer "):
                    token_str = token_str[7:]

                settings = get_settings()
                try:
                    payload = Token.decode(
                        encoded_token=token_str,
                        secret=settings.JWT_SECRET_KEY,
                        algorithm="HS256",
                    )
                    user_id = payload.sub
                    authenticated = True

                    # Subscribe to default channels
                    for ch in _DEFAULT_CHANNELS:
                        if ch not in subscribed_channels:
                            channels.subscribe(socket, [ch])
                            subscribed_channels.add(ch)

                    await socket.send_json({
                        "type": "auth:ok",
                        "user_id": user_id,
                        "subscribed": list(subscribed_channels),
                    })
                    logger.info("ws.authenticated", user_id=user_id)

                except Exception as exc:
                    await _send_error(socket, f"Authentication failed: {exc}")
                    logger.warning("ws.auth_failed", error=str(exc))
                continue

            # Everything below requires auth
            if not authenticated:
                await _send_error(socket, "Not authenticated. Send auth message first.")
                continue

            # ── Subscribe to a specific project channel ─────────────
            if msg_type == "subscribe:project":
                project_id = message.get("project_id", "")
                if project_id:
                    channel_name = f"project:{project_id}"
                    if channel_name not in subscribed_channels:
                        channels.subscribe(socket, [channel_name])
                        subscribed_channels.add(channel_name)
                    await socket.send_json({
                        "type": "subscribed",
                        "channel": channel_name,
                    })
                    logger.info("ws.subscribed", user_id=user_id, channel=channel_name)
                continue

            # ── Subscribe to a specific agent channel ───────────────
            if msg_type == "subscribe:agent":
                agent_name = message.get("agent", "")
                if agent_name:
                    channel_name = f"agent:{agent_name}"
                    if channel_name not in subscribed_channels:
                        channels.subscribe(socket, [channel_name])
                        subscribed_channels.add(channel_name)
                    await socket.send_json({
                        "type": "subscribed",
                        "channel": channel_name,
                    })
                    logger.info("ws.subscribed", user_id=user_id, channel=channel_name)
                continue

            # ── Unrecognised message ────────────────────────────────
            await _send_error(socket, f"Unknown message type: {msg_type}")

    except WebSocketDisconnect:
        logger.info("ws.disconnected", user_id=user_id)
    except Exception as exc:
        logger.error("ws.error", user_id=user_id, error=str(exc))
    finally:
        # ChannelsPlugin automatically cleans up subscriptions when the
        # socket disconnects, but we explicitly unsubscribe for clarity.
        for ch in subscribed_channels:
            try:
                channels.unsubscribe(socket, [ch])
            except Exception:
                logger.debug("ws_unsubscribe_cleanup_failed", channel=ch)


async def _send_error(socket: WebSocket, message: str) -> None:
    """Send a structured error frame to the client."""
    await socket.send_json({"type": "error", "message": message})


# ---------------------------------------------------------------------------
# Helper: publish events from backend code
# ---------------------------------------------------------------------------


async def publish_event(channels: ChannelsPlugin, channel: str, data: dict[str, Any]) -> None:
    """Publish a JSON event to a ChannelsPlugin channel.

    This is the entry point that agents and other backend services use to
    push real-time events to connected WebSocket clients.

    Args:
        channels: The ChannelsPlugin instance (can be obtained via DI).
        channel: Target channel name (e.g. ``"hitl:new"``).
        data: Event payload serialisable to JSON.
    """
    payload = json.dumps(data, default=str)
    await channels.publish(payload, [channel])
    logger.debug("ws.publish", channel=channel, data_keys=list(data.keys()))
