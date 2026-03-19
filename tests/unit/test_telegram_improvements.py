"""Tests for Telegram bot improvements: webhook mode, rate limiting, quiet hours.

C8:  Webhook mode (polling/webhook switch, webhook registration)
H12: Rate limiting on commands (Valkey-backed, per-user per-command)
H13: Quiet hours (notification suppression 22:00-08:00, queue + deliver)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ============================================================================
# C8: Telegram Webhook Mode
# ============================================================================


class TestWebhookRoute:
    """Tests for the Litestar POST /api/v1/telegram/webhook endpoint."""

    @pytest.fixture()
    def _mock_settings(self) -> MagicMock:
        settings = MagicMock()
        settings.TELEGRAM_BOT_TOKEN = "test-token-123"
        settings.TELEGRAM_WEBHOOK_SECRET = "webhook-secret-456"
        return settings

    @pytest.fixture()
    def _mock_bot_app(self) -> AsyncMock:
        app = AsyncMock()
        app.update_queue = AsyncMock()
        app.update_queue.put = AsyncMock()
        return app

    @pytest.mark.asyncio()
    async def test_webhook_rejects_missing_secret_header(self) -> None:
        """Webhook endpoint must reject requests without valid secret header."""
        from src.api.routes.telegram_webhook import _validate_webhook_secret

        result = _validate_webhook_secret(
            header_secret=None,
            expected_secret="my-secret",
        )
        assert result is False

    @pytest.mark.asyncio()
    async def test_webhook_rejects_wrong_secret(self) -> None:
        """Webhook endpoint must reject requests with wrong secret."""
        from src.api.routes.telegram_webhook import _validate_webhook_secret

        result = _validate_webhook_secret(
            header_secret="wrong-secret",
            expected_secret="correct-secret",
        )
        assert result is False

    @pytest.mark.asyncio()
    async def test_webhook_accepts_correct_secret(self) -> None:
        """Webhook endpoint must accept requests with correct secret."""
        from src.api.routes.telegram_webhook import _validate_webhook_secret

        result = _validate_webhook_secret(
            header_secret="correct-secret",
            expected_secret="correct-secret",
        )
        assert result is True

    @pytest.mark.asyncio()
    async def test_webhook_accepts_empty_secret_when_not_configured(self) -> None:
        """When no secret is configured, all requests are accepted (dev mode)."""
        from src.api.routes.telegram_webhook import _validate_webhook_secret

        result = _validate_webhook_secret(
            header_secret=None,
            expected_secret="",
        )
        assert result is True

    @pytest.mark.asyncio()
    async def test_parse_telegram_update_valid(self) -> None:
        """Valid Telegram update JSON should be parsed successfully."""
        from src.api.routes.telegram_webhook import _parse_telegram_update

        payload = {
            "update_id": 12345,
            "message": {
                "message_id": 1,
                "date": 1234567890,
                "chat": {"id": 100, "type": "private"},
                "text": "/start",
            },
        }
        update = _parse_telegram_update(payload)
        assert update is not None
        assert update["update_id"] == 12345

    @pytest.mark.asyncio()
    async def test_parse_telegram_update_invalid(self) -> None:
        """Invalid payload (missing update_id) returns None."""
        from src.api.routes.telegram_webhook import _parse_telegram_update

        result = _parse_telegram_update({})
        assert result is None

    @pytest.mark.asyncio()
    async def test_parse_telegram_update_callback_query(self) -> None:
        """Callback query updates are also valid."""
        from src.api.routes.telegram_webhook import _parse_telegram_update

        payload = {
            "update_id": 67890,
            "callback_query": {
                "id": "abc",
                "chat_instance": "xyz",
                "data": "hitl:approve:deadbeef",
            },
        }
        update = _parse_telegram_update(payload)
        assert update is not None
        assert update["update_id"] == 67890


class TestWebhookMode:
    """Tests for the webhook mode switch in handler.py."""

    @pytest.mark.asyncio()
    async def test_create_bot_with_polling_mode(self) -> None:
        """Default mode should be polling."""
        from src.bot.handler import get_bot_mode

        with patch("src.bot.handler.get_settings") as mock_gs:
            mock_gs.return_value = MagicMock(
                TELEGRAM_BOT_TOKEN="test-token",
                TELEGRAM_WEBHOOK_URL="",
                TELEGRAM_CHAT_ID=None,
            )
            mode = get_bot_mode()
            assert mode == "polling"

    @pytest.mark.asyncio()
    async def test_create_bot_with_webhook_mode(self) -> None:
        """When TELEGRAM_WEBHOOK_URL is set, mode should be webhook."""
        from src.bot.handler import get_bot_mode

        with patch("src.bot.handler.get_settings") as mock_gs:
            mock_gs.return_value = MagicMock(
                TELEGRAM_BOT_TOKEN="test-token",
                TELEGRAM_WEBHOOK_URL="https://example.com/api/v1/telegram/webhook",
                TELEGRAM_CHAT_ID=None,
            )
            mode = get_bot_mode()
            assert mode == "webhook"

    @pytest.mark.asyncio()
    async def test_build_webhook_url(self) -> None:
        """Webhook URL should be built from settings."""
        from src.bot.handler import build_webhook_url

        url = build_webhook_url("https://api.example.com")
        assert url == "https://api.example.com/api/v1/telegram/webhook"

    @pytest.mark.asyncio()
    async def test_build_webhook_url_strips_trailing_slash(self) -> None:
        """Trailing slash on base URL should be stripped."""
        from src.bot.handler import build_webhook_url

        url = build_webhook_url("https://api.example.com/")
        assert url == "https://api.example.com/api/v1/telegram/webhook"

    @pytest.mark.asyncio()
    async def test_register_webhook(self) -> None:
        """register_webhook should call set_webhook on the bot."""
        from src.bot.handler import register_webhook

        mock_bot = AsyncMock()
        mock_bot.set_webhook = AsyncMock(return_value=True)

        result = await register_webhook(
            mock_bot,
            webhook_url="https://api.example.com/api/v1/telegram/webhook",
            secret_token="my-secret",
            max_connections=100,
        )
        assert result is True
        mock_bot.set_webhook.assert_awaited_once_with(
            url="https://api.example.com/api/v1/telegram/webhook",
            secret_token="my-secret",
            max_connections=100,
            allowed_updates=["message", "callback_query"],
        )

    @pytest.mark.asyncio()
    async def test_register_webhook_failure_returns_false(self) -> None:
        """If set_webhook raises, register_webhook should return False."""
        from src.bot.handler import register_webhook

        mock_bot = AsyncMock()
        mock_bot.set_webhook = AsyncMock(side_effect=RuntimeError("network error"))

        result = await register_webhook(
            mock_bot,
            webhook_url="https://api.example.com/hook",
            secret_token="",
            max_connections=40,
        )
        assert result is False

    @pytest.mark.asyncio()
    async def test_unregister_webhook(self) -> None:
        """unregister_webhook should call delete_webhook."""
        from src.bot.handler import unregister_webhook

        mock_bot = AsyncMock()
        mock_bot.delete_webhook = AsyncMock(return_value=True)

        result = await unregister_webhook(mock_bot)
        assert result is True
        mock_bot.delete_webhook.assert_awaited_once()


# ============================================================================
# H12: Telegram Rate Limiting on Commands
# ============================================================================


class TestTelegramRateLimiter:
    """Tests for Valkey-backed per-user per-command rate limiting."""

    @pytest.fixture()
    def mock_valkey(self) -> AsyncMock:
        valkey = AsyncMock()
        valkey.incr = AsyncMock(return_value=1)
        valkey.expire = AsyncMock(return_value=True)
        valkey.get = AsyncMock(return_value=None)
        return valkey

    @pytest.mark.asyncio()
    async def test_first_command_allowed(self, mock_valkey: AsyncMock) -> None:
        """First command from a user should always be allowed."""
        from src.bot.rate_limiter import TelegramRateLimiter

        limiter = TelegramRateLimiter(mock_valkey)
        allowed = await limiter.check(user_id=12345, command="status")
        assert allowed is True

    @pytest.mark.asyncio()
    async def test_general_limit_30_per_minute(self, mock_valkey: AsyncMock) -> None:
        """General commands should be limited to 30/min per user (per spec)."""
        from src.bot.rate_limiter import TelegramRateLimiter

        limiter = TelegramRateLimiter(mock_valkey)

        # Simulate 31st request
        mock_valkey.incr = AsyncMock(return_value=31)
        allowed = await limiter.check(user_id=12345, command="status")
        assert allowed is False

    @pytest.mark.asyncio()
    async def test_scan_limit_5_per_minute(self, mock_valkey: AsyncMock) -> None:
        """Scan command should have a stricter limit of 5/min."""
        from src.bot.rate_limiter import TelegramRateLimiter

        limiter = TelegramRateLimiter(mock_valkey)

        # 5th request should be OK
        mock_valkey.incr = AsyncMock(return_value=5)
        allowed = await limiter.check(user_id=12345, command="scan")
        assert allowed is True

        # 6th should be blocked
        mock_valkey.incr = AsyncMock(return_value=6)
        allowed = await limiter.check(user_id=12345, command="scan")
        assert allowed is False

    @pytest.mark.asyncio()
    async def test_different_users_independent(self, mock_valkey: AsyncMock) -> None:
        """Rate limits should be per-user, not global."""
        from src.bot.rate_limiter import TelegramRateLimiter

        limiter = TelegramRateLimiter(mock_valkey)

        # Each call gets a fresh counter
        mock_valkey.incr = AsyncMock(return_value=1)
        assert await limiter.check(user_id=111, command="status") is True
        assert await limiter.check(user_id=222, command="status") is True

        # Verify different keys were used
        calls = mock_valkey.incr.call_args_list
        keys = [call.args[0] for call in calls]
        assert keys[0] != keys[1]
        assert "111" in keys[0]
        assert "222" in keys[1]

    @pytest.mark.asyncio()
    async def test_ttl_set_on_first_request(self, mock_valkey: AsyncMock) -> None:
        """TTL should be set when counter == 1 (first request in window)."""
        from src.bot.rate_limiter import TelegramRateLimiter

        limiter = TelegramRateLimiter(mock_valkey)
        mock_valkey.incr = AsyncMock(return_value=1)

        await limiter.check(user_id=12345, command="status")
        mock_valkey.expire.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_ttl_not_set_on_subsequent_requests(self, mock_valkey: AsyncMock) -> None:
        """TTL should NOT be re-set on subsequent requests in same window."""
        from src.bot.rate_limiter import TelegramRateLimiter

        limiter = TelegramRateLimiter(mock_valkey)
        mock_valkey.incr = AsyncMock(return_value=5)

        await limiter.check(user_id=12345, command="status")
        mock_valkey.expire.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_key_format(self, mock_valkey: AsyncMock) -> None:
        """Valkey key should include user_id and command."""
        from src.bot.rate_limiter import TelegramRateLimiter

        limiter = TelegramRateLimiter(mock_valkey)
        mock_valkey.incr = AsyncMock(return_value=1)

        await limiter.check(user_id=99999, command="scan")
        key = mock_valkey.incr.call_args.args[0]
        assert "99999" in key
        assert "scan" in key
        assert key.startswith("tg_rate:")

    @pytest.mark.asyncio()
    async def test_throttle_message(self, mock_valkey: AsyncMock) -> None:
        """When rate limited, get_throttle_message should return a message."""
        from src.bot.rate_limiter import TelegramRateLimiter

        limiter = TelegramRateLimiter(mock_valkey)
        msg = limiter.get_throttle_message("status")
        assert "rate limit" in msg.lower() or "too many" in msg.lower()
        assert msg  # non-empty

    @pytest.mark.asyncio()
    async def test_throttle_message_scan_specific(self, mock_valkey: AsyncMock) -> None:
        """Scan throttle message should mention the lower limit."""
        from src.bot.rate_limiter import TelegramRateLimiter

        limiter = TelegramRateLimiter(mock_valkey)
        msg = limiter.get_throttle_message("scan")
        assert "5" in msg

    @pytest.mark.asyncio()
    async def test_custom_limits(self, mock_valkey: AsyncMock) -> None:
        """Custom limits should override defaults."""
        from src.bot.rate_limiter import TelegramRateLimiter

        custom_limits = {"status": 3, "scan": 1}
        limiter = TelegramRateLimiter(mock_valkey, command_limits=custom_limits)

        # 4th status request should be blocked with custom limit of 3
        mock_valkey.incr = AsyncMock(return_value=4)
        allowed = await limiter.check(user_id=12345, command="status")
        assert allowed is False

    @pytest.mark.asyncio()
    async def test_decrement_on_over_limit(self, mock_valkey: AsyncMock) -> None:
        """When over limit, counter should be decremented to stay accurate."""
        from src.bot.rate_limiter import TelegramRateLimiter

        limiter = TelegramRateLimiter(mock_valkey)
        mock_valkey.incr = AsyncMock(return_value=31)
        mock_valkey.decr = AsyncMock(return_value=30)

        await limiter.check(user_id=12345, command="status")
        mock_valkey.decr.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_default_limit_for_unknown_command(self, mock_valkey: AsyncMock) -> None:
        """Unknown commands should use the general limit (10/min)."""
        from src.bot.rate_limiter import (
            DEFAULT_GENERAL_LIMIT,
            TelegramRateLimiter,
        )

        limiter = TelegramRateLimiter(mock_valkey)

        # At the general limit boundary
        mock_valkey.incr = AsyncMock(return_value=DEFAULT_GENERAL_LIMIT)
        allowed = await limiter.check(user_id=12345, command="unknown_cmd")
        assert allowed is True

        mock_valkey.incr = AsyncMock(return_value=DEFAULT_GENERAL_LIMIT + 1)
        allowed = await limiter.check(user_id=12345, command="unknown_cmd")
        assert allowed is False


# ============================================================================
# H13: Quiet Hours
# ============================================================================


class TestQuietHours:
    """Tests for notification suppression during quiet hours."""

    @pytest.mark.asyncio()
    async def test_default_quiet_hours_23_to_8(self) -> None:
        """Default quiet hours should be 23:00-08:00 (per spec)."""
        from src.bot.quiet_hours import QuietHoursManager

        mgr = QuietHoursManager()
        assert mgr.start_hour == 23
        assert mgr.end_hour == 8

    @pytest.mark.asyncio()
    async def test_is_quiet_at_23(self) -> None:
        """23:00 should be within quiet hours (22:00-08:00)."""
        from src.bot.quiet_hours import QuietHoursManager

        mgr = QuietHoursManager(start_hour=22, end_hour=8)
        dt = datetime(2026, 3, 17, 23, 0, tzinfo=UTC)
        assert mgr.is_quiet(dt) is True

    @pytest.mark.asyncio()
    async def test_is_quiet_at_midnight(self) -> None:
        """00:00 should be within quiet hours."""
        from src.bot.quiet_hours import QuietHoursManager

        mgr = QuietHoursManager(start_hour=22, end_hour=8)
        dt = datetime(2026, 3, 17, 0, 0, tzinfo=UTC)
        assert mgr.is_quiet(dt) is True

    @pytest.mark.asyncio()
    async def test_is_quiet_at_7(self) -> None:
        """07:00 should be within quiet hours (before end 08:00)."""
        from src.bot.quiet_hours import QuietHoursManager

        mgr = QuietHoursManager(start_hour=22, end_hour=8)
        dt = datetime(2026, 3, 17, 7, 30, tzinfo=UTC)
        assert mgr.is_quiet(dt) is True

    @pytest.mark.asyncio()
    async def test_not_quiet_at_8(self) -> None:
        """08:00 should NOT be in quiet hours (end is 08:00 exclusive)."""
        from src.bot.quiet_hours import QuietHoursManager

        mgr = QuietHoursManager(start_hour=22, end_hour=8)
        dt = datetime(2026, 3, 17, 8, 0, tzinfo=UTC)
        assert mgr.is_quiet(dt) is False

    @pytest.mark.asyncio()
    async def test_not_quiet_at_14(self) -> None:
        """14:00 should NOT be in quiet hours."""
        from src.bot.quiet_hours import QuietHoursManager

        mgr = QuietHoursManager(start_hour=22, end_hour=8)
        dt = datetime(2026, 3, 17, 14, 0, tzinfo=UTC)
        assert mgr.is_quiet(dt) is False

    @pytest.mark.asyncio()
    async def test_not_quiet_at_21(self) -> None:
        """21:00 should NOT be in quiet hours (before start 22:00)."""
        from src.bot.quiet_hours import QuietHoursManager

        mgr = QuietHoursManager(start_hour=22, end_hour=8)
        dt = datetime(2026, 3, 17, 21, 59, tzinfo=UTC)
        assert mgr.is_quiet(dt) is False

    @pytest.mark.asyncio()
    async def test_is_quiet_at_22(self) -> None:
        """22:00 should be in quiet hours (start is 22:00 inclusive)."""
        from src.bot.quiet_hours import QuietHoursManager

        mgr = QuietHoursManager(start_hour=22, end_hour=8)
        dt = datetime(2026, 3, 17, 22, 0, tzinfo=UTC)
        assert mgr.is_quiet(dt) is True

    @pytest.mark.asyncio()
    async def test_custom_quiet_hours(self) -> None:
        """Custom quiet hours (23:00-06:00) should work."""
        from src.bot.quiet_hours import QuietHoursManager

        mgr = QuietHoursManager(start_hour=23, end_hour=6)
        assert mgr.is_quiet(datetime(2026, 3, 17, 23, 30, tzinfo=UTC)) is True
        assert mgr.is_quiet(datetime(2026, 3, 17, 3, 0, tzinfo=UTC)) is True
        assert mgr.is_quiet(datetime(2026, 3, 17, 6, 0, tzinfo=UTC)) is False
        assert mgr.is_quiet(datetime(2026, 3, 17, 22, 0, tzinfo=UTC)) is False

    @pytest.mark.asyncio()
    async def test_non_overnight_quiet_hours(self) -> None:
        """Non-overnight quiet hours (e.g., 13:00-15:00) should also work."""
        from src.bot.quiet_hours import QuietHoursManager

        mgr = QuietHoursManager(start_hour=13, end_hour=15)
        assert mgr.is_quiet(datetime(2026, 3, 17, 13, 30, tzinfo=UTC)) is True
        assert mgr.is_quiet(datetime(2026, 3, 17, 14, 59, tzinfo=UTC)) is True
        assert mgr.is_quiet(datetime(2026, 3, 17, 15, 0, tzinfo=UTC)) is False
        assert mgr.is_quiet(datetime(2026, 3, 17, 12, 0, tzinfo=UTC)) is False

    @pytest.mark.asyncio()
    async def test_urgent_notifications_bypass_quiet_hours(self) -> None:
        """Urgent notifications should be sent even during quiet hours."""
        from src.bot.quiet_hours import QuietHoursManager

        mgr = QuietHoursManager(start_hour=22, end_hour=8)
        dt_quiet = datetime(2026, 3, 17, 23, 0, tzinfo=UTC)
        assert mgr.should_deliver(dt_quiet, priority="urgent") is True

    @pytest.mark.asyncio()
    async def test_normal_notifications_suppressed_in_quiet(self) -> None:
        """Normal priority notifications should be suppressed during quiet hours."""
        from src.bot.quiet_hours import QuietHoursManager

        mgr = QuietHoursManager(start_hour=22, end_hour=8)
        dt_quiet = datetime(2026, 3, 17, 23, 0, tzinfo=UTC)
        assert mgr.should_deliver(dt_quiet, priority="normal") is False

    @pytest.mark.asyncio()
    async def test_normal_notifications_delivered_outside_quiet(self) -> None:
        """Normal priority notifications should be sent outside quiet hours."""
        from src.bot.quiet_hours import QuietHoursManager

        mgr = QuietHoursManager(start_hour=22, end_hour=8)
        dt_active = datetime(2026, 3, 17, 14, 0, tzinfo=UTC)
        assert mgr.should_deliver(dt_active, priority="normal") is True

    @pytest.mark.asyncio()
    async def test_queue_notification(self) -> None:
        """Notifications should be queued during quiet hours."""
        from src.bot.quiet_hours import QuietHoursManager

        mock_valkey = AsyncMock()
        mock_valkey.rpush = AsyncMock(return_value=1)

        mgr = QuietHoursManager(start_hour=22, end_hour=8, valkey=mock_valkey)

        notification = {
            "chat_id": 12345,
            "text": "Hello",
            "priority": "normal",
        }
        await mgr.queue_notification(notification)

        mock_valkey.rpush.assert_awaited_once()
        key = mock_valkey.rpush.call_args.args[0]
        assert "quiet_hours" in key

    @pytest.mark.asyncio()
    async def test_deliver_queued_notifications(self) -> None:
        """Queued notifications should be delivered after quiet hours end."""
        import json

        from src.bot.quiet_hours import QuietHoursManager

        notification_data = json.dumps(
            {
                "chat_id": 12345,
                "text": "Delayed notification",
                "priority": "normal",
            }
        )

        mock_valkey = AsyncMock()
        # LPOP returns items one at a time, then None when empty
        mock_valkey.lpop = AsyncMock(side_effect=[notification_data, None])

        mock_notifier = AsyncMock()
        mock_notifier.send_message = AsyncMock(return_value={"ok": True})

        mgr = QuietHoursManager(start_hour=23, end_hour=8, valkey=mock_valkey)

        delivered = await mgr.deliver_queued(mock_notifier)
        assert delivered == 1
        mock_notifier.send_message.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_deliver_queued_empty_queue(self) -> None:
        """No-op when the queue is empty."""
        from src.bot.quiet_hours import QuietHoursManager

        mock_valkey = AsyncMock()
        # LPOP returns None immediately when queue is empty
        mock_valkey.lpop = AsyncMock(return_value=None)

        mock_notifier = AsyncMock()

        mgr = QuietHoursManager(start_hour=23, end_hour=8, valkey=mock_valkey)
        delivered = await mgr.deliver_queued(mock_notifier)
        assert delivered == 0
        mock_notifier.send_message.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_next_delivery_time_during_quiet(self) -> None:
        """During quiet hours, next delivery should be at end_hour."""
        from src.bot.quiet_hours import QuietHoursManager

        mgr = QuietHoursManager(start_hour=22, end_hour=8)

        # At 23:00 on March 17 -- next delivery at 08:00 March 18
        dt = datetime(2026, 3, 17, 23, 0, tzinfo=UTC)
        next_delivery = mgr.next_delivery_time(dt)
        assert next_delivery.hour == 8
        assert next_delivery.minute == 0
        assert next_delivery.day == 18

    @pytest.mark.asyncio()
    async def test_next_delivery_time_after_midnight(self) -> None:
        """At 03:00 in quiet hours, next delivery is same day at 08:00."""
        from src.bot.quiet_hours import QuietHoursManager

        mgr = QuietHoursManager(start_hour=22, end_hour=8)

        dt = datetime(2026, 3, 17, 3, 0, tzinfo=UTC)
        next_delivery = mgr.next_delivery_time(dt)
        assert next_delivery.hour == 8
        assert next_delivery.minute == 0
        assert next_delivery.day == 17

    @pytest.mark.asyncio()
    async def test_next_delivery_time_outside_quiet(self) -> None:
        """Outside quiet hours, next delivery is now (no delay)."""
        from src.bot.quiet_hours import QuietHoursManager

        mgr = QuietHoursManager(start_hour=22, end_hour=8)
        dt = datetime(2026, 3, 17, 14, 0, tzinfo=UTC)
        next_delivery = mgr.next_delivery_time(dt)
        # Should return the same time (no delay)
        assert next_delivery == dt

    @pytest.mark.asyncio()
    async def test_per_user_override_enabled(self) -> None:
        """Per-user quiet hours override should work."""
        from src.bot.quiet_hours import QuietHoursManager

        mgr = QuietHoursManager(start_hour=22, end_hour=8)

        # User has custom quiet hours disabled
        user_override = {"quiet_hours_enabled": False}
        dt_quiet = datetime(2026, 3, 17, 23, 0, tzinfo=UTC)
        assert mgr.should_deliver(dt_quiet, priority="normal", user_override=user_override) is True

    @pytest.mark.asyncio()
    async def test_per_user_override_custom_hours(self) -> None:
        """Per-user custom quiet hours should take precedence."""
        from src.bot.quiet_hours import QuietHoursManager

        mgr = QuietHoursManager(start_hour=22, end_hour=8)

        # User has custom quiet hours 20:00-06:00
        user_override = {
            "quiet_hours_enabled": True,
            "quiet_hours_start": 20,
            "quiet_hours_end": 6,
        }
        # 21:00 is within user's quiet hours but not global
        dt = datetime(2026, 3, 17, 21, 0, tzinfo=UTC)
        assert mgr.should_deliver(dt, priority="normal", user_override=user_override) is False

        # 07:00 is outside user's quiet hours but within global
        dt2 = datetime(2026, 3, 17, 7, 0, tzinfo=UTC)
        assert mgr.should_deliver(dt2, priority="normal", user_override=user_override) is True

    @pytest.mark.asyncio()
    async def test_from_env_vars(self) -> None:
        """QuietHoursManager should read from environment variables."""
        from src.bot.quiet_hours import QuietHoursManager

        with patch.dict(
            "os.environ",
            {
                "TELEGRAM_QUIET_HOURS_START": "23",
                "TELEGRAM_QUIET_HOURS_END": "7",
            },
        ):
            mgr = QuietHoursManager.from_env()
            assert mgr.start_hour == 23
            assert mgr.end_hour == 7

    @pytest.mark.asyncio()
    async def test_from_env_defaults(self) -> None:
        """Default env values should be 23 and 8 (per spec)."""
        from src.bot.quiet_hours import QuietHoursManager

        with patch.dict("os.environ", {}, clear=True):
            mgr = QuietHoursManager.from_env()
            assert mgr.start_hour == 23
            assert mgr.end_hour == 8

    @pytest.mark.asyncio()
    async def test_invalid_hours_clamped(self) -> None:
        """Hours outside 0-23 should raise ValueError."""
        from src.bot.quiet_hours import QuietHoursManager

        with pytest.raises(ValueError, match="hour"):
            QuietHoursManager(start_hour=25, end_hour=8)

        with pytest.raises(ValueError, match="hour"):
            QuietHoursManager(start_hour=22, end_hour=-1)

    @pytest.mark.asyncio()
    async def test_disabled_quiet_hours(self) -> None:
        """When start == end, quiet hours are effectively disabled."""
        from src.bot.quiet_hours import QuietHoursManager

        mgr = QuietHoursManager(start_hour=0, end_hour=0)
        # Should never be quiet
        dt = datetime(2026, 3, 17, 3, 0, tzinfo=UTC)
        assert mgr.is_quiet(dt) is False


# ============================================================================
# Integration: Rate limiter + command handler interaction
# ============================================================================


class TestRateLimiterIntegration:
    """Tests for rate limiter integration with the bot command flow."""

    @pytest.fixture()
    def mock_valkey(self) -> AsyncMock:
        valkey = AsyncMock()
        valkey.incr = AsyncMock(return_value=1)
        valkey.expire = AsyncMock(return_value=True)
        valkey.decr = AsyncMock(return_value=0)
        return valkey

    @pytest.mark.asyncio()
    async def test_rate_limited_decorator_allows(self, mock_valkey: AsyncMock) -> None:
        """rate_limited decorator should allow when under limit."""
        from src.bot.rate_limiter import TelegramRateLimiter, rate_limited

        limiter = TelegramRateLimiter(mock_valkey)

        @rate_limited(limiter, command_name="test")
        async def my_handler(update: Any, context: Any) -> str:
            return "ok"

        mock_update = MagicMock()
        mock_update.effective_user = MagicMock(id=12345)
        mock_update.effective_message = MagicMock()
        mock_update.effective_message.reply_text = AsyncMock()
        mock_context = MagicMock()

        result = await my_handler(mock_update, mock_context)
        assert result == "ok"

    @pytest.mark.asyncio()
    async def test_rate_limited_decorator_blocks(self, mock_valkey: AsyncMock) -> None:
        """rate_limited decorator should block when over limit."""
        from src.bot.rate_limiter import TelegramRateLimiter, rate_limited

        mock_valkey.incr = AsyncMock(return_value=31)
        limiter = TelegramRateLimiter(mock_valkey)

        @rate_limited(limiter, command_name="test")
        async def my_handler(update: Any, context: Any) -> str:
            return "ok"

        mock_update = MagicMock()
        mock_update.effective_user = MagicMock(id=12345)
        mock_update.effective_message = MagicMock()
        mock_update.effective_message.reply_text = AsyncMock()
        mock_context = MagicMock()

        result = await my_handler(mock_update, mock_context)
        assert result is None
        mock_update.effective_message.reply_text.assert_awaited_once()


# ============================================================================
# Integration: Quiet hours + TelegramNotifier
# ============================================================================


class TestQuietHoursNotifierIntegration:
    """Tests for quiet hours integration with TelegramNotifier."""

    @pytest.mark.asyncio()
    async def test_notify_during_quiet_queues(self) -> None:
        """Notifications during quiet hours should be queued, not sent."""

        from src.bot.quiet_hours import QuietHoursManager

        mock_valkey = AsyncMock()
        mock_valkey.rpush = AsyncMock(return_value=1)

        mgr = QuietHoursManager(start_hour=22, end_hour=8, valkey=mock_valkey)

        dt_quiet = datetime(2026, 3, 17, 23, 0, tzinfo=UTC)
        notification = {"chat_id": 100, "text": "Hello", "priority": "normal"}

        should_send = mgr.should_deliver(dt_quiet, priority="normal")
        assert should_send is False

        # Queue it instead
        await mgr.queue_notification(notification)
        mock_valkey.rpush.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_notify_urgent_during_quiet_sends(self) -> None:
        """Urgent notifications during quiet hours should be sent immediately."""
        from src.bot.quiet_hours import QuietHoursManager

        mgr = QuietHoursManager(start_hour=22, end_hour=8)
        dt_quiet = datetime(2026, 3, 17, 23, 0, tzinfo=UTC)

        should_send = mgr.should_deliver(dt_quiet, priority="urgent")
        assert should_send is True
