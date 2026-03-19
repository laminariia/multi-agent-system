"""Tests for multi-user Telegram notification dispatch.

Covers:
- notification_prefs: target resolution, quiet hours, role filtering, type filtering
- notifications: send_hitl_notification multi-user dispatch, fallback
- expiry reminders: detection and sending
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers: build mock User and HITLQueue objects
# ---------------------------------------------------------------------------


def _make_user(
    *,
    chat_id: int | None = 111222333,
    role: str = "owner",
    status: str = "active",
    settings: dict[str, Any] | None = None,
    user_id: uuid.UUID | None = None,
) -> MagicMock:
    user = MagicMock()
    user.id = user_id or uuid.uuid4()
    user.telegram_chat_id = chat_id
    user.role = role
    user.status = status
    user.settings = settings or {}
    return user


def _make_hitl_item(
    *,
    hitl_type: str = "bid_approval",
    priority: str = "normal",
    title: str = "Test HITL",
    description: str = "Test description",
    expires_at: datetime | None = None,
    created_at: datetime | None = None,
) -> MagicMock:
    item = MagicMock()
    item.id = uuid.uuid4()
    item.type = hitl_type
    item.priority = priority
    item.title = title
    item.description = description
    item.status = "pending"
    item.expires_at = expires_at
    item.created_at = created_at or datetime.now(UTC)
    item.payload = {}
    item.available_actions = ["approve", "reject"]
    return item


# ---------------------------------------------------------------------------
# Mock get_db_session context manager
# ---------------------------------------------------------------------------


def _mock_db_session(users: list[MagicMock]) -> AsyncMock:
    """Build a mock get_db_session that returns given users."""
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = users
    mock_result.all.return_value = [(u.telegram_chat_id,) for u in users]

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


# ===========================================================================
# Tests: notification_prefs._extract_prefs
# ===========================================================================


class TestExtractPrefs:
    def test_empty_settings(self) -> None:
        from src.bot.notification_prefs import _extract_prefs

        prefs = _extract_prefs({})
        assert prefs.enabled_types == ["all"]
        assert prefs.quiet_start is None
        assert prefs.quiet_end is None
        assert prefs.urgent_override is True

    def test_none_settings(self) -> None:
        from src.bot.notification_prefs import _extract_prefs

        prefs = _extract_prefs(None)
        assert prefs.enabled_types == ["all"]

    def test_custom_prefs(self) -> None:
        from src.bot.notification_prefs import _extract_prefs

        settings = {
            "notification_prefs": {
                "enabled_types": ["bid_approval", "alert"],
                "quiet_start": 22,
                "quiet_end": 8,
                "urgent_override": False,
            }
        }
        prefs = _extract_prefs(settings)
        assert prefs.enabled_types == ["bid_approval", "alert"]
        assert prefs.quiet_start == 22
        assert prefs.quiet_end == 8
        assert prefs.urgent_override is False


# ===========================================================================
# Tests: notification_prefs._is_in_quiet_hours
# ===========================================================================


class TestIsInQuietHours:
    def test_not_configured(self) -> None:
        from src.bot.notification_prefs import NotificationPrefs, _is_in_quiet_hours

        prefs = NotificationPrefs(user_id="u1", quiet_start=None, quiet_end=None)
        dt = datetime(2026, 3, 19, 23, 30, tzinfo=UTC)
        assert _is_in_quiet_hours(prefs, dt) is False

    def test_overnight_quiet_inside(self) -> None:
        from src.bot.notification_prefs import NotificationPrefs, _is_in_quiet_hours

        prefs = NotificationPrefs(user_id="u1", quiet_start=22, quiet_end=8)
        # 23:30 is inside 22-8 range
        dt = datetime(2026, 3, 19, 23, 30, tzinfo=UTC)
        assert _is_in_quiet_hours(prefs, dt) is True

    def test_overnight_quiet_outside(self) -> None:
        from src.bot.notification_prefs import NotificationPrefs, _is_in_quiet_hours

        prefs = NotificationPrefs(user_id="u1", quiet_start=22, quiet_end=8)
        # 12:00 is outside 22-8 range
        dt = datetime(2026, 3, 19, 12, 0, tzinfo=UTC)
        assert _is_in_quiet_hours(prefs, dt) is False

    def test_overnight_early_morning_inside(self) -> None:
        from src.bot.notification_prefs import NotificationPrefs, _is_in_quiet_hours

        prefs = NotificationPrefs(user_id="u1", quiet_start=22, quiet_end=8)
        # 3:00 AM is inside 22-8 range
        dt = datetime(2026, 3, 19, 3, 0, tzinfo=UTC)
        assert _is_in_quiet_hours(prefs, dt) is True

    def test_same_day_range_inside(self) -> None:
        from src.bot.notification_prefs import NotificationPrefs, _is_in_quiet_hours

        prefs = NotificationPrefs(user_id="u1", quiet_start=13, quiet_end=15)
        dt = datetime(2026, 3, 19, 14, 0, tzinfo=UTC)
        assert _is_in_quiet_hours(prefs, dt) is True

    def test_same_start_end_disabled(self) -> None:
        from src.bot.notification_prefs import NotificationPrefs, _is_in_quiet_hours

        prefs = NotificationPrefs(user_id="u1", quiet_start=10, quiet_end=10)
        dt = datetime(2026, 3, 19, 10, 0, tzinfo=UTC)
        assert _is_in_quiet_hours(prefs, dt) is False


# ===========================================================================
# Tests: notification_prefs._type_matches
# ===========================================================================


class TestTypeMatches:
    def test_all_matches_everything(self) -> None:
        from src.bot.notification_prefs import NotificationPrefs, _type_matches

        prefs = NotificationPrefs(user_id="u1", enabled_types=["all"])
        assert _type_matches(prefs, "bid_approval") is True
        assert _type_matches(prefs, "alert") is True

    def test_specific_type_match(self) -> None:
        from src.bot.notification_prefs import NotificationPrefs, _type_matches

        prefs = NotificationPrefs(user_id="u1", enabled_types=["bid_approval", "alert"])
        assert _type_matches(prefs, "bid_approval") is True
        assert _type_matches(prefs, "code_review") is False


# ===========================================================================
# Tests: get_notification_targets
# ===========================================================================


class TestGetNotificationTargets:
    @pytest.mark.anyio
    async def test_returns_active_users_with_chat_ids(self) -> None:
        from src.bot.notification_prefs import get_notification_targets

        users = [
            _make_user(chat_id=100, role="owner"),
            _make_user(chat_id=200, role="admin"),
        ]
        mock_ctx = _mock_db_session(users)

        with patch("src.core.database.get_db_session", return_value=mock_ctx):
            targets = await get_notification_targets("bid_approval")

        assert sorted(targets) == [100, 200]

    @pytest.mark.anyio
    async def test_filters_viewer_role(self) -> None:
        """Viewers should only get 'alert' and 'system' types."""
        from src.bot.notification_prefs import get_notification_targets

        users = [
            _make_user(chat_id=100, role="owner"),
            _make_user(chat_id=200, role="viewer"),
        ]
        mock_ctx = _mock_db_session(users)

        with patch("src.core.database.get_db_session", return_value=mock_ctx):
            targets = await get_notification_targets("bid_approval")

        assert targets == [100]

    @pytest.mark.anyio
    async def test_viewer_gets_alerts(self) -> None:
        from src.bot.notification_prefs import get_notification_targets

        users = [_make_user(chat_id=200, role="viewer")]
        mock_ctx = _mock_db_session(users)

        with patch("src.core.database.get_db_session", return_value=mock_ctx):
            targets = await get_notification_targets("alert")

        assert targets == [200]

    @pytest.mark.anyio
    async def test_type_filtering_via_prefs(self) -> None:
        from src.bot.notification_prefs import get_notification_targets

        users = [
            _make_user(
                chat_id=100,
                role="owner",
                settings={"notification_prefs": {"enabled_types": ["alert"]}},
            ),
        ]
        mock_ctx = _mock_db_session(users)

        with patch("src.core.database.get_db_session", return_value=mock_ctx):
            targets = await get_notification_targets("bid_approval")

        assert targets == []

    @pytest.mark.anyio
    async def test_quiet_hours_filtering(self) -> None:
        from src.bot.notification_prefs import get_notification_targets

        users = [
            _make_user(
                chat_id=100,
                role="owner",
                settings={"notification_prefs": {"quiet_start": 22, "quiet_end": 8}},
            ),
        ]
        mock_ctx = _mock_db_session(users)
        # 23:00 is inside 22-8 quiet range
        now = datetime(2026, 3, 19, 23, 0, tzinfo=UTC)

        with patch("src.core.database.get_db_session", return_value=mock_ctx):
            targets = await get_notification_targets("bid_approval", now=now)

        assert targets == []

    @pytest.mark.anyio
    async def test_urgent_overrides_quiet_hours(self) -> None:
        from src.bot.notification_prefs import get_notification_targets

        users = [
            _make_user(
                chat_id=100,
                role="owner",
                settings={
                    "notification_prefs": {
                        "quiet_start": 22,
                        "quiet_end": 8,
                        "urgent_override": True,
                    }
                },
            ),
        ]
        mock_ctx = _mock_db_session(users)
        now = datetime(2026, 3, 19, 23, 0, tzinfo=UTC)

        with patch("src.core.database.get_db_session", return_value=mock_ctx):
            targets = await get_notification_targets(
                "bid_approval",
                priority="urgent",
                now=now,
            )

        assert targets == [100]

    @pytest.mark.anyio
    async def test_urgent_no_override_still_blocked(self) -> None:
        from src.bot.notification_prefs import get_notification_targets

        users = [
            _make_user(
                chat_id=100,
                role="owner",
                settings={
                    "notification_prefs": {
                        "quiet_start": 22,
                        "quiet_end": 8,
                        "urgent_override": False,
                    }
                },
            ),
        ]
        mock_ctx = _mock_db_session(users)
        now = datetime(2026, 3, 19, 23, 0, tzinfo=UTC)

        with patch("src.core.database.get_db_session", return_value=mock_ctx):
            targets = await get_notification_targets(
                "bid_approval",
                priority="urgent",
                now=now,
            )

        assert targets == []

    @pytest.mark.anyio
    async def test_returns_empty_on_db_error(self) -> None:
        from src.bot.notification_prefs import get_notification_targets

        with patch(
            "src.core.database.get_db_session",
            side_effect=RuntimeError("db down"),
        ):
            targets = await get_notification_targets("bid_approval")

        assert targets == []

    @pytest.mark.anyio
    async def test_skips_none_chat_id(self) -> None:
        from src.bot.notification_prefs import get_notification_targets

        users = [
            _make_user(chat_id=None, role="owner"),
            _make_user(chat_id=100, role="owner"),
        ]
        mock_ctx = _mock_db_session(users)

        with patch("src.core.database.get_db_session", return_value=mock_ctx):
            targets = await get_notification_targets("bid_approval")

        assert targets == [100]

    @pytest.mark.anyio
    async def test_moderator_gets_hitl_actions(self) -> None:
        from src.bot.notification_prefs import get_notification_targets

        users = [_make_user(chat_id=300, role="moderator")]
        mock_ctx = _mock_db_session(users)

        with patch("src.core.database.get_db_session", return_value=mock_ctx):
            targets = await get_notification_targets("code_review")

        assert targets == [300]


# ===========================================================================
# Tests: get_all_linked_chat_ids
# ===========================================================================


class TestGetAllLinkedChatIds:
    @pytest.mark.anyio
    async def test_returns_chat_ids(self) -> None:
        from src.bot.notification_prefs import get_all_linked_chat_ids

        users = [
            _make_user(chat_id=100),
            _make_user(chat_id=200),
        ]
        mock_ctx = _mock_db_session(users)

        with patch("src.core.database.get_db_session", return_value=mock_ctx):
            ids = await get_all_linked_chat_ids()

        assert sorted(ids) == [100, 200]

    @pytest.mark.anyio
    async def test_returns_empty_on_error(self) -> None:
        from src.bot.notification_prefs import get_all_linked_chat_ids

        with patch(
            "src.core.database.get_db_session",
            side_effect=RuntimeError("db down"),
        ):
            ids = await get_all_linked_chat_ids()

        assert ids == []


# ===========================================================================
# Tests: TelegramNotifier.send_hitl_notification (multi-user)
# ===========================================================================


class TestSendHitlNotification:
    @pytest.mark.anyio
    async def test_sends_to_multiple_targets(self) -> None:
        from src.bot.notifications import TelegramNotifier

        settings = MagicMock()
        settings.TELEGRAM_BOT_TOKEN = "test-token"
        settings.TELEGRAM_CHAT_ID = None

        with (
            patch("src.bot.notifications.get_settings", return_value=settings),
            patch(
                "src.bot.notification_prefs.get_notification_targets",
                new_callable=AsyncMock,
                return_value=[100, 200, 300],
            ),
        ):
            notifier = TelegramNotifier(token="test-token")
            notifier.send_message = AsyncMock(return_value={"ok": True})

            sent = await notifier.send_hitl_notification(
                {
                    "type": "bid_approval",
                    "title": "Test bid",
                    "priority": "normal",
                    "id": str(uuid.uuid4()),
                }
            )

        assert sent == 3
        assert notifier.send_message.call_count == 3

    @pytest.mark.anyio
    async def test_fallback_to_telegram_chat_id(self) -> None:
        from src.bot.notifications import TelegramNotifier

        settings = MagicMock()
        settings.TELEGRAM_BOT_TOKEN = "test-token"
        settings.TELEGRAM_CHAT_ID = "999888"

        with (
            patch("src.bot.notifications.get_settings", return_value=settings),
            patch(
                "src.bot.notification_prefs.get_notification_targets",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            notifier = TelegramNotifier(token="test-token")
            notifier.send_message = AsyncMock(return_value={"ok": True})

            sent = await notifier.send_hitl_notification(
                {
                    "type": "bid_approval",
                    "title": "Test bid",
                }
            )

        assert sent == 1
        notifier.send_message.assert_called_once()
        call_args = notifier.send_message.call_args
        assert call_args.kwargs["chat_id"] == 999888

    @pytest.mark.anyio
    async def test_no_targets_no_fallback_returns_zero(self) -> None:
        from src.bot.notifications import TelegramNotifier

        settings = MagicMock()
        settings.TELEGRAM_BOT_TOKEN = "test-token"
        settings.TELEGRAM_CHAT_ID = None

        with (
            patch("src.bot.notifications.get_settings", return_value=settings),
            patch(
                "src.bot.notification_prefs.get_notification_targets",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            notifier = TelegramNotifier(token="test-token")
            notifier.send_message = AsyncMock()

            sent = await notifier.send_hitl_notification(
                {
                    "type": "bid_approval",
                    "title": "Test",
                }
            )

        assert sent == 0
        notifier.send_message.assert_not_called()

    @pytest.mark.anyio
    async def test_partial_failure_counts_successes(self) -> None:
        from src.bot.notifications import TelegramNotifier

        settings = MagicMock()
        settings.TELEGRAM_BOT_TOKEN = "test-token"
        settings.TELEGRAM_CHAT_ID = None

        with (
            patch("src.bot.notifications.get_settings", return_value=settings),
            patch(
                "src.bot.notification_prefs.get_notification_targets",
                new_callable=AsyncMock,
                return_value=[100, 200, 300],
            ),
        ):
            notifier = TelegramNotifier(token="test-token")
            # First succeeds, second fails, third succeeds
            notifier.send_message = AsyncMock(side_effect=[{"ok": True}, None, {"ok": True}])

            sent = await notifier.send_hitl_notification(
                {
                    "type": "bid_approval",
                    "title": "Test",
                    "id": str(uuid.uuid4()),
                }
            )

        assert sent == 2

    @pytest.mark.anyio
    async def test_exception_in_send_does_not_crash(self) -> None:
        from src.bot.notifications import TelegramNotifier

        settings = MagicMock()
        settings.TELEGRAM_BOT_TOKEN = "test-token"
        settings.TELEGRAM_CHAT_ID = None

        with (
            patch("src.bot.notifications.get_settings", return_value=settings),
            patch(
                "src.bot.notification_prefs.get_notification_targets",
                new_callable=AsyncMock,
                return_value=[100, 200],
            ),
        ):
            notifier = TelegramNotifier(token="test-token")
            notifier.send_message = AsyncMock(side_effect=[RuntimeError("network"), {"ok": True}])

            sent = await notifier.send_hitl_notification(
                {
                    "type": "alert",
                    "title": "Error alert",
                    "id": str(uuid.uuid4()),
                }
            )

        # Only second one succeeded
        assert sent == 1

    @pytest.mark.anyio
    async def test_invalid_fallback_chat_id(self) -> None:
        from src.bot.notifications import TelegramNotifier

        settings = MagicMock()
        settings.TELEGRAM_BOT_TOKEN = "test-token"
        settings.TELEGRAM_CHAT_ID = "not-a-number"

        with (
            patch("src.bot.notifications.get_settings", return_value=settings),
            patch(
                "src.bot.notification_prefs.get_notification_targets",
                new_callable=AsyncMock,
                return_value=[],
            ),
        ):
            notifier = TelegramNotifier(token="test-token")
            notifier.send_message = AsyncMock()

            sent = await notifier.send_hitl_notification(
                {
                    "type": "bid_approval",
                    "title": "Test",
                }
            )

        assert sent == 0


# ===========================================================================
# Tests: _format_hitl_dict_message
# ===========================================================================


class TestFormatHitlDictMessage:
    def test_basic_format(self) -> None:
        from src.bot.notifications import _format_hitl_dict_message

        text = _format_hitl_dict_message(
            {
                "type": "bid_approval",
                "title": "Review bid #42",
                "priority": "urgent",
                "id": "abc-123",
            }
        )

        assert "New HITL Item" in text
        assert "Review bid #42" in text
        assert "URGENT" in text
        assert "abc-123" in text

    def test_escapes_html(self) -> None:
        from src.bot.notifications import _format_hitl_dict_message

        text = _format_hitl_dict_message(
            {
                "type": "alert",
                "title": "<script>alert('xss')</script>",
            }
        )

        assert "<script>" not in text
        assert "&lt;script&gt;" in text

    def test_description_truncation(self) -> None:
        from src.bot.notifications import _format_hitl_dict_message

        long_desc = "x" * 300
        text = _format_hitl_dict_message(
            {
                "type": "bid_approval",
                "title": "Test",
                "description": long_desc,
            }
        )

        assert "..." in text

    def test_missing_fields_safe(self) -> None:
        from src.bot.notifications import _format_hitl_dict_message

        text = _format_hitl_dict_message({})
        assert "New HITL Item" in text
        assert "Untitled" in text


# ===========================================================================
# Tests: check_expiring_hitl_items
# ===========================================================================


class TestCheckExpiringHitlItems:
    @pytest.mark.anyio
    async def test_finds_expiring_items(self) -> None:
        from src.bot.notification_prefs import check_expiring_hitl_items

        now = datetime.now(UTC)
        item = _make_hitl_item(
            created_at=now - timedelta(hours=3),
            expires_at=now + timedelta(hours=1),
        )
        # 3 of 4 hours elapsed = 75% > 50% threshold

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [item]
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("src.core.database.get_db_session", return_value=mock_ctx):
            expiring = await check_expiring_hitl_items(threshold_percent=0.5)

        assert len(expiring) == 1
        assert expiring[0]["elapsed_percent"] >= 0.5

    @pytest.mark.anyio
    async def test_skips_items_below_threshold(self) -> None:
        from src.bot.notification_prefs import check_expiring_hitl_items

        now = datetime.now(UTC)
        item = _make_hitl_item(
            created_at=now - timedelta(minutes=10),
            expires_at=now + timedelta(hours=10),
        )
        # 10 min of ~10 hours elapsed = ~1.6% < 50% threshold

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [item]
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("src.core.database.get_db_session", return_value=mock_ctx):
            expiring = await check_expiring_hitl_items(threshold_percent=0.5)

        assert len(expiring) == 0

    @pytest.mark.anyio
    async def test_returns_empty_on_error(self) -> None:
        from src.bot.notification_prefs import check_expiring_hitl_items

        with patch(
            "src.core.database.get_db_session",
            side_effect=RuntimeError("db down"),
        ):
            expiring = await check_expiring_hitl_items()

        assert expiring == []


# ===========================================================================
# Tests: send_expiry_reminders
# ===========================================================================


class TestSendExpiryReminders:
    @pytest.mark.anyio
    async def test_sends_reminders(self) -> None:
        from src.bot.notification_prefs import send_expiry_reminders

        expiring_items = [
            {
                "id": str(uuid.uuid4()),
                "type": "bid_approval",
                "title": "Expiring bid",
                "expires_at": "2026-03-20T12:00:00+00:00",
                "elapsed_percent": 0.75,
                "priority": "normal",
            },
        ]

        mock_notifier = MagicMock()
        mock_notifier.send_message = AsyncMock(return_value={"ok": True})

        with (
            patch(
                "src.bot.notification_prefs.check_expiring_hitl_items",
                new_callable=AsyncMock,
                return_value=expiring_items,
            ),
            patch(
                "src.bot.notification_prefs.get_notification_targets",
                new_callable=AsyncMock,
                return_value=[100, 200],
            ),
            patch(
                "src.bot.notifications.TelegramNotifier",
                return_value=mock_notifier,
            ),
        ):
            sent = await send_expiry_reminders()

        assert sent == 2
        assert mock_notifier.send_message.call_count == 2

    @pytest.mark.anyio
    async def test_no_expiring_items(self) -> None:
        from src.bot.notification_prefs import send_expiry_reminders

        with patch(
            "src.bot.notification_prefs.check_expiring_hitl_items",
            new_callable=AsyncMock,
            return_value=[],
        ):
            sent = await send_expiry_reminders()

        assert sent == 0

    @pytest.mark.anyio
    async def test_returns_zero_on_error(self) -> None:
        from src.bot.notification_prefs import send_expiry_reminders

        with patch(
            "src.bot.notification_prefs.check_expiring_hitl_items",
            new_callable=AsyncMock,
            side_effect=RuntimeError("db down"),
        ):
            sent = await send_expiry_reminders()

        assert sent == 0


# ===========================================================================
# Tests: notify_new_hitl (backward compat -- single user)
# ===========================================================================


class TestNotifyNewHitlBackwardCompat:
    @pytest.mark.anyio
    async def test_single_user_still_works(self) -> None:
        from src.bot.notifications import TelegramNotifier

        user = _make_user(chat_id=555)
        hitl_item = _make_hitl_item()

        settings = MagicMock()
        settings.TELEGRAM_BOT_TOKEN = "test-token"

        with patch("src.bot.notifications.get_settings", return_value=settings):
            notifier = TelegramNotifier(token="test-token")
            notifier.send_message = AsyncMock(return_value={"ok": True})

            result = await notifier.notify_new_hitl(user, hitl_item)

        assert result is True
        notifier.send_message.assert_called_once()

    @pytest.mark.anyio
    async def test_skips_user_without_chat_id(self) -> None:
        from src.bot.notifications import TelegramNotifier

        user = _make_user(chat_id=None)
        hitl_item = _make_hitl_item()

        settings = MagicMock()
        settings.TELEGRAM_BOT_TOKEN = "test-token"

        with patch("src.bot.notifications.get_settings", return_value=settings):
            notifier = TelegramNotifier(token="test-token")
            notifier.send_message = AsyncMock()

            result = await notifier.notify_new_hitl(user, hitl_item)

        assert result is False
        notifier.send_message.assert_not_called()
