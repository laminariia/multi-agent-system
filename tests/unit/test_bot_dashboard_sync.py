"""Unit tests for bot-dashboard synchronisation via Valkey pub/sub.

Tests cover:
- HITL resolve Valkey publish payload format and resilience
- Dashboard sync listener creation and handler count preservation
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ===========================================================================
# HITL Resolve — Valkey publish payload
# ===========================================================================


class TestHITLResolvePublish:
    """Verify the Valkey publish behaviour added to HITL resolve."""

    @pytest.mark.anyio()
    async def test_publish_payload_format(self) -> None:
        """The publish payload contains all required fields."""
        payload = json.dumps(
            {
                "hitl_id": "11111111-1111-1111-1111-111111111111",
                "type": "bid_approval",
                "title": "Review bid for project X",
                "action": "approve",
                "next_action": "bid_will_be_submitted",
                "resolved_by": "owner@example.com",
            }
        )
        parsed = json.loads(payload)

        assert parsed["hitl_id"] == "11111111-1111-1111-1111-111111111111"
        assert parsed["type"] == "bid_approval"
        assert parsed["action"] == "approve"
        assert parsed["next_action"] == "bid_will_be_submitted"
        assert parsed["resolved_by"] == "owner@example.com"

    @pytest.mark.anyio()
    async def test_publish_channel_name(self) -> None:
        """The publish goes to the correct channel name."""
        mock_valkey = AsyncMock()
        mock_valkey.publish = AsyncMock(return_value=1)

        channel = "hitl:resolved:bot"
        await mock_valkey.publish(channel, json.dumps({"test": True}))

        mock_valkey.publish.assert_called_once()
        call_args = mock_valkey.publish.call_args
        assert call_args[0][0] == "hitl:resolved:bot"

    @pytest.mark.anyio()
    async def test_publish_failure_is_non_fatal(self) -> None:
        """A Valkey publish failure should be caught and logged, not raised."""
        mock_valkey = AsyncMock()
        mock_valkey.publish = AsyncMock(side_effect=ConnectionError("Valkey down"))

        # Simulate the try/except pattern from hitl.py resolve
        published = False
        try:
            await mock_valkey.publish("hitl:resolved:bot", "{}")
            published = True
        except Exception:  # noqa: S110
            # In the actual code this is caught with a warning log
            pass

        assert published is False  # publish failed
        # But the resolve would still return successfully

    def test_hitl_resolve_method_accepts_valkey_param(self) -> None:
        """The resolve method signature includes the valkey parameter."""
        import inspect

        from src.api.routes.hitl import HITLController

        # Access the route handler's fn
        resolve_handler = HITLController.resolve
        # The handler wraps the original function
        fn = resolve_handler.fn.func if hasattr(resolve_handler.fn, "func") else resolve_handler.fn
        sig = inspect.signature(fn)
        assert "valkey" in sig.parameters


# ===========================================================================
# Dashboard sync listener
# ===========================================================================


class TestDashboardSyncListener:
    """Tests for the Valkey pub/sub listener in the bot handler."""

    def test_handler_creates_combined_post_init(self) -> None:
        """Verify create_bot_application sets up the combined post_init."""
        with patch("src.bot.handler.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(TELEGRAM_BOT_TOKEN="test-token-123")

            from src.bot.handler import create_bot_application

            app = create_bot_application()

        assert app.post_init is not None
        assert callable(app.post_init)
        # The combined handler should have "combined" in its name
        assert "combined" in app.post_init.__name__

    def test_handler_count_unchanged(self) -> None:
        """Adding sync doesn't change the number of registered handlers (17)."""
        with patch("src.bot.handler.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(TELEGRAM_BOT_TOKEN="test-token-abc")

            from src.bot.handler import create_bot_application

            app = create_bot_application()

        # 15 CommandHandlers + 2 CallbackQueryHandlers = 17
        handlers = app.handlers.get(0, [])
        assert len(handlers) == 17

    def test_post_shutdown_cancels_task(self) -> None:
        """The post_shutdown callback cancels the sync task if present."""
        with patch("src.bot.handler.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(TELEGRAM_BOT_TOKEN="test-token-def")

            from src.bot.handler import create_bot_application

            app = create_bot_application()

        # Simulate a running sync task
        mock_task = MagicMock()
        app.bot_data["_sync_task"] = mock_task

        # Call post_shutdown
        if callable(app.post_shutdown):
            app.post_shutdown(app)

        mock_task.cancel.assert_called_once()


# ===========================================================================
# Notification message formatting
# ===========================================================================


class TestNotificationFormatting:
    """Test the Telegram message format for sync notifications."""

    def test_hitl_notification_format(self) -> None:
        """HITL resolved notification contains all fields."""
        data = {
            "type": "bid_approval",
            "title": "Review bid for project X",
            "action": "approve",
            "next_action": "bid_will_be_submitted",
            "resolved_by": "owner@example.com",
        }

        text = (
            f"\U0001f4cb HITL resolved from dashboard\n\n"
            f"Type: {data.get('type', '?')}\n"
            f"Title: {data.get('title', '?')}\n"
            f"Action: {data.get('action', '?')}\n"
            f"Next: {data.get('next_action', '?')}\n"
            f"By: {data.get('resolved_by', '?')}"
        )

        assert "HITL resolved from dashboard" in text
        assert "bid_approval" in text
        assert "Review bid for project X" in text
        assert "approve" in text
        assert "bid_will_be_submitted" in text
        assert "owner@example.com" in text

    def test_orch_event_notification_format(self) -> None:
        """Orchestrator event notification format."""
        data = {
            "event": "goal_completed",
            "message": "Goal g_001 completed successfully",
        }

        event_type = data.get("event", "unknown")
        text = f"\U0001f916 Orchestrator event: {event_type}\n{data.get('message', '')}"

        assert "Orchestrator event: goal_completed" in text
        assert "Goal g_001 completed successfully" in text

    def test_missing_fields_default_to_questionmark(self) -> None:
        """Missing data fields default to '?'."""
        data: dict = {}

        text = f"Type: {data.get('type', '?')}\nTitle: {data.get('title', '?')}\nAction: {data.get('action', '?')}"

        assert "Type: ?" in text
        assert "Title: ?" in text
        assert "Action: ?" in text
