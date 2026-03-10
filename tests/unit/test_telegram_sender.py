"""Unit tests for Telegram DM sender — src/enrichment/telegram_sender.py.

Covers TelegramDMSender configuration, DM sending (success, flood wait,
privacy restricted), hourly rate limiting, and bulk send_approved_telegram_dms.
"""

from __future__ import annotations

import time
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.enrichment.telegram_sender import (
    TelegramDMSender,
    _check_hourly_rate_limit,
    _reset_hourly_counter,
    send_approved_telegram_dms,
)

# ===========================================================================
# Helpers
# ===========================================================================


def _make_sender(
    *,
    api_id: int | None = None,
    api_hash: str | None = None,
    session_string: str | None = None,
    max_dms_per_hour: int = 5,
    min_interval_seconds: int = 0,
) -> TelegramDMSender:
    """Create a TelegramDMSender with settings load bypassed."""
    with patch("src.enrichment.telegram_sender.TelegramDMSender.__init__", return_value=None):
        sender = TelegramDMSender.__new__(TelegramDMSender)
    sender.api_id = api_id
    sender.api_hash = api_hash
    sender.session_string = session_string
    sender.max_dms_per_hour = max_dms_per_hour
    sender.min_interval_seconds = min_interval_seconds
    sender._client = None
    return sender


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """Reset module-level rate limit counters before each test."""
    _reset_hourly_counter()
    yield
    _reset_hourly_counter()


# ===========================================================================
# TelegramDMSender — configuration
# ===========================================================================


class TestSenderConfiguration:
    """TelegramDMSender.is_configured reflects credential availability."""

    def test_sender_not_configured_no_args(self):
        """Sender with no credentials is not configured."""
        sender = _make_sender()
        assert sender.is_configured is False

    def test_sender_not_configured_partial_args(self):
        """Sender with only api_id is not configured."""
        sender = _make_sender(api_id=123)
        assert sender.is_configured is False

    def test_sender_not_configured_missing_session(self):
        """Sender without session_string is not configured."""
        sender = _make_sender(api_id=123, api_hash="abc")
        assert sender.is_configured is False

    def test_sender_configured_all_args(self):
        """Sender with all three credentials is configured."""
        sender = _make_sender(api_id=123, api_hash="abc", session_string="session123")
        assert sender.is_configured is True


# ===========================================================================
# TelegramDMSender.send_dm — not connected
# ===========================================================================


class TestSendDmNotConnected:
    """send_dm returns False when client is not connected."""

    @pytest.mark.asyncio
    async def test_send_dm_returns_false_when_not_connected(self):
        sender = _make_sender(api_id=123, api_hash="abc", session_string="s")
        # _client is None by default
        result = await sender.send_dm("username", "Hello!")
        assert result is False


# ===========================================================================
# TelegramDMSender.send_dm — success
# ===========================================================================


class TestSendDmSuccess:
    """send_dm returns True when message is delivered successfully."""

    @pytest.mark.asyncio
    async def test_send_dm_success(self):
        sender = _make_sender(
            api_id=123, api_hash="abc", session_string="s",
            max_dms_per_hour=10, min_interval_seconds=0,
        )
        mock_client = AsyncMock()
        mock_client.send_message = AsyncMock(return_value=MagicMock())
        sender._client = mock_client

        # Mock telethon error imports inside send_dm
        mock_flood = type("FloodWaitError", (Exception,), {"seconds": 30})
        mock_privacy = type("UserPrivacyRestrictedError", (Exception,), {})

        with patch.dict("sys.modules", {
            "telethon": MagicMock(),
            "telethon.errors": MagicMock(
                FloodWaitError=mock_flood,
                UserPrivacyRestrictedError=mock_privacy,
            ),
        }):
            result = await sender.send_dm("testuser", "Hello!")

        assert result is True
        mock_client.send_message.assert_awaited_once_with("testuser", "Hello!")


# ===========================================================================
# TelegramDMSender.send_dm — FloodWaitError
# ===========================================================================


class TestSendDmFloodWait:
    """send_dm handles FloodWaitError with retry behavior."""

    @pytest.mark.asyncio
    async def test_send_dm_flood_wait_retry_success(self):
        """On FloodWaitError, sender waits then retries. If retry succeeds, returns True."""
        sender = _make_sender(
            api_id=123, api_hash="abc", session_string="s",
            max_dms_per_hour=10, min_interval_seconds=0,
        )

        # Create exception types that match what `from telethon.errors import` returns
        FloodWaitError = type("FloodWaitError", (Exception,), {})
        UserPrivacyRestrictedError = type("UserPrivacyRestrictedError", (Exception,), {})

        flood_err = FloodWaitError("flood")
        flood_err.seconds = 2  # type: ignore[attr-defined]

        mock_client = AsyncMock()
        # First call raises flood, retry (second call) succeeds
        mock_client.send_message = AsyncMock(
            side_effect=[flood_err, MagicMock()],
        )
        sender._client = mock_client

        mock_errors_module = MagicMock()
        mock_errors_module.FloodWaitError = FloodWaitError
        mock_errors_module.UserPrivacyRestrictedError = UserPrivacyRestrictedError

        with patch.dict("sys.modules", {
            "telethon": MagicMock(),
            "telethon.errors": mock_errors_module,
        }), patch("src.enrichment.telegram_sender.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await sender.send_dm("testuser", "Hello!")

        assert result is True
        # Should have slept for the flood wait duration
        mock_sleep.assert_awaited()
        assert mock_client.send_message.await_count == 2

    @pytest.mark.asyncio
    async def test_send_dm_flood_wait_retry_fails(self):
        """On FloodWaitError, if retry also fails, returns 'flood_wait'."""
        sender = _make_sender(
            api_id=123, api_hash="abc", session_string="s",
            max_dms_per_hour=10, min_interval_seconds=0,
        )

        FloodWaitError = type("FloodWaitError", (Exception,), {})
        UserPrivacyRestrictedError = type("UserPrivacyRestrictedError", (Exception,), {})

        flood_err = FloodWaitError("flood")
        flood_err.seconds = 2  # type: ignore[attr-defined]

        mock_client = AsyncMock()
        # Both first call and retry raise errors
        mock_client.send_message = AsyncMock(
            side_effect=[flood_err, RuntimeError("still failing")],
        )
        sender._client = mock_client

        mock_errors_module = MagicMock()
        mock_errors_module.FloodWaitError = FloodWaitError
        mock_errors_module.UserPrivacyRestrictedError = UserPrivacyRestrictedError

        with patch.dict("sys.modules", {
            "telethon": MagicMock(),
            "telethon.errors": mock_errors_module,
        }), patch("src.enrichment.telegram_sender.asyncio.sleep", new_callable=AsyncMock):
            result = await sender.send_dm("testuser", "Hello!")

        assert result == "flood_wait"


# ===========================================================================
# TelegramDMSender.send_dm — UserPrivacyRestrictedError
# ===========================================================================


class TestSendDmPrivacyRestricted:
    """send_dm returns 'privacy_restricted' when user blocks DMs."""

    @pytest.mark.asyncio
    async def test_send_dm_privacy_restricted(self):
        sender = _make_sender(
            api_id=123, api_hash="abc", session_string="s",
            max_dms_per_hour=10, min_interval_seconds=0,
        )

        # Create proper exception types
        UserPrivacyRestrictedError = type("UserPrivacyRestrictedError", (Exception,), {})
        FloodWaitError = type("FloodWaitError", (Exception,), {})

        privacy_err = UserPrivacyRestrictedError("User privacy restricted")

        mock_client = AsyncMock()
        mock_client.send_message = AsyncMock(side_effect=privacy_err)
        sender._client = mock_client

        # Patch the telethon.errors import inside send_dm
        mock_errors_module = MagicMock()
        mock_errors_module.FloodWaitError = FloodWaitError
        mock_errors_module.UserPrivacyRestrictedError = UserPrivacyRestrictedError

        with patch.dict("sys.modules", {
            "telethon": MagicMock(),
            "telethon.errors": mock_errors_module,
        }):
            result = await sender.send_dm("private_user", "Hello!")

        assert result == "privacy_restricted"


# ===========================================================================
# Hourly rate limit
# ===========================================================================


class TestHourlyRateLimit:
    """_check_hourly_rate_limit enforces max_per_hour."""

    def test_allows_up_to_max(self):
        """Should allow exactly max_per_hour sends."""
        max_per_hour = 3
        results = [_check_hourly_rate_limit(max_per_hour) for _ in range(max_per_hour)]
        assert all(results), "All sends within the limit should be allowed"

    def test_blocks_after_max(self):
        """Should block sends after max_per_hour is reached."""
        max_per_hour = 3
        for _ in range(max_per_hour):
            _check_hourly_rate_limit(max_per_hour)

        result = _check_hourly_rate_limit(max_per_hour)
        assert result is False, "Send after limit should be blocked"

    def test_reset_restores_limit(self):
        """After _reset_hourly_counter, sends are allowed again."""
        max_per_hour = 2
        for _ in range(max_per_hour):
            _check_hourly_rate_limit(max_per_hour)
        assert _check_hourly_rate_limit(max_per_hour) is False

        _reset_hourly_counter()
        assert _check_hourly_rate_limit(max_per_hour) is True

    def test_resets_after_hour_window(self):
        """Counter resets after the 3600s window passes."""
        import src.enrichment.telegram_sender as mod

        max_per_hour = 1
        _check_hourly_rate_limit(max_per_hour)
        assert _check_hourly_rate_limit(max_per_hour) is False

        # Simulate time passing by setting the reset time far in the past
        mod._hourly_reset_time = time.monotonic() - 3601
        assert _check_hourly_rate_limit(max_per_hour) is True

    def test_single_send_increments_counter(self):
        """A single allowed send increments the counter."""
        import src.enrichment.telegram_sender as mod

        _check_hourly_rate_limit(10)
        assert mod._hourly_count == 1


# ===========================================================================
# send_approved_telegram_dms — not configured
# ===========================================================================


class TestSendApprovedNotConfigured:
    """send_approved_telegram_dms returns zero stats when not configured."""

    @pytest.mark.asyncio
    async def test_returns_zero_stats(self):
        mock_session = AsyncMock()
        campaign_id = uuid.uuid4()

        with patch(
            "src.enrichment.telegram_sender.TelegramDMSender",
        ) as MockSender:
            instance = MagicMock()
            instance.is_configured = False
            MockSender.return_value = instance

            stats = await send_approved_telegram_dms(campaign_id, mock_session)

        assert stats == {"sent": 0, "failed": 0, "privacy_restricted": 0, "rate_limited": 0}

    @pytest.mark.asyncio
    async def test_does_not_call_connect(self):
        """When not configured, connect() should not be called."""
        mock_session = AsyncMock()
        campaign_id = uuid.uuid4()

        with patch(
            "src.enrichment.telegram_sender.TelegramDMSender",
        ) as MockSender:
            instance = MagicMock()
            instance.is_configured = False
            instance.connect = AsyncMock()
            MockSender.return_value = instance

            await send_approved_telegram_dms(campaign_id, mock_session)

        instance.connect.assert_not_awaited()


# ===========================================================================
# send_approved_telegram_dms — success with leads
# ===========================================================================


class TestSendApprovedSuccess:
    """send_approved_telegram_dms processes campaign leads correctly."""

    @pytest.mark.asyncio
    async def test_sends_to_approved_leads(self):
        campaign_id = uuid.uuid4()
        lead_id = uuid.uuid4()

        # Mock lead and campaign_lead
        mock_lead = MagicMock()
        mock_lead.id = lead_id
        mock_lead.telegram_username = "testuser"

        mock_campaign_lead = MagicMock()
        mock_campaign_lead.campaign_id = campaign_id
        mock_campaign_lead.lead_id = lead_id
        mock_campaign_lead.personalized_body = "Hello from our studio!"
        mock_campaign_lead.status = "approved"
        mock_campaign_lead.channel_type = "telegram"

        # Mock db session — execute returns mock result for both select and update
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [(mock_campaign_lead, mock_lead)]
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        # Mock sender
        with patch(
            "src.enrichment.telegram_sender.TelegramDMSender",
        ) as MockSender:
            instance = AsyncMock()
            instance.is_configured = True
            instance.connect = AsyncMock()
            instance.disconnect = AsyncMock()
            instance.send_dm = AsyncMock(return_value=True)
            MockSender.return_value = instance

            stats = await send_approved_telegram_dms(campaign_id, mock_session)

        assert stats["sent"] == 1
        assert stats["failed"] == 0
        instance.send_dm.assert_awaited_once_with("testuser", "Hello from our studio!")
        instance.disconnect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_handles_lead_without_username(self):
        """Leads without telegram_username increment the failed counter."""
        campaign_id = uuid.uuid4()

        mock_lead = MagicMock()
        mock_lead.id = uuid.uuid4()
        mock_lead.telegram_username = None  # No username

        mock_campaign_lead = MagicMock()
        mock_campaign_lead.campaign_id = campaign_id
        mock_campaign_lead.lead_id = mock_lead.id
        mock_campaign_lead.personalized_body = "Hello!"

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [(mock_campaign_lead, mock_lead)]
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        with patch(
            "src.enrichment.telegram_sender.TelegramDMSender",
        ) as MockSender:
            instance = AsyncMock()
            instance.is_configured = True
            instance.connect = AsyncMock()
            instance.disconnect = AsyncMock()
            instance.send_dm = AsyncMock()
            MockSender.return_value = instance

            stats = await send_approved_telegram_dms(campaign_id, mock_session)

        assert stats["failed"] == 1
        assert stats["sent"] == 0
        instance.send_dm.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_stops_on_flood_wait(self):
        """Bulk sender stops processing when it gets a flood_wait."""
        campaign_id = uuid.uuid4()

        leads_data = []
        for i in range(3):
            lead = MagicMock()
            lead.id = uuid.uuid4()
            lead.telegram_username = f"user{i}"
            cl = MagicMock()
            cl.campaign_id = campaign_id
            cl.lead_id = lead.id
            cl.personalized_body = f"Message {i}"
            leads_data.append((cl, lead))

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = leads_data
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        with patch(
            "src.enrichment.telegram_sender.TelegramDMSender",
        ) as MockSender:
            instance = AsyncMock()
            instance.is_configured = True
            instance.connect = AsyncMock()
            instance.disconnect = AsyncMock()
            # First send succeeds, second gets rate limited
            instance.send_dm = AsyncMock(side_effect=[True, "flood_wait", True])
            MockSender.return_value = instance

            stats = await send_approved_telegram_dms(campaign_id, mock_session)

        assert stats["sent"] == 1
        assert stats["rate_limited"] == 1
        # Should have stopped after flood_wait, not sent the third
        assert instance.send_dm.await_count == 2

    @pytest.mark.asyncio
    async def test_privacy_restricted_increments_counter(self):
        """Privacy restricted results update the lead status to failed."""
        campaign_id = uuid.uuid4()

        mock_lead = MagicMock()
        mock_lead.id = uuid.uuid4()
        mock_lead.telegram_username = "private_user"

        mock_cl = MagicMock()
        mock_cl.campaign_id = campaign_id
        mock_cl.lead_id = mock_lead.id
        mock_cl.personalized_body = "Hello!"

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = [(mock_cl, mock_lead)]
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        with patch(
            "src.enrichment.telegram_sender.TelegramDMSender",
        ) as MockSender:
            instance = AsyncMock()
            instance.is_configured = True
            instance.connect = AsyncMock()
            instance.disconnect = AsyncMock()
            instance.send_dm = AsyncMock(return_value="privacy_restricted")
            MockSender.return_value = instance

            stats = await send_approved_telegram_dms(campaign_id, mock_session)

        assert stats["privacy_restricted"] == 1
        assert stats["sent"] == 0

    @pytest.mark.asyncio
    async def test_disconnect_called_on_error(self):
        """disconnect() is called even when an error occurs during processing."""
        campaign_id = uuid.uuid4()
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=RuntimeError("DB error"))

        with patch(
            "src.enrichment.telegram_sender.TelegramDMSender",
        ) as MockSender:
            instance = AsyncMock()
            instance.is_configured = True
            instance.connect = AsyncMock()
            instance.disconnect = AsyncMock()
            MockSender.return_value = instance

            with pytest.raises(RuntimeError, match="DB error"):
                await send_approved_telegram_dms(campaign_id, mock_session)

        instance.disconnect.assert_awaited_once()


# ===========================================================================
# send_approved_telegram_dms — empty campaign
# ===========================================================================


class TestSendApprovedEmptyCampaign:
    """send_approved_telegram_dms with no matching leads returns zero stats."""

    @pytest.mark.asyncio
    async def test_no_leads_returns_zero_stats(self):
        campaign_id = uuid.uuid4()

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        with patch(
            "src.enrichment.telegram_sender.TelegramDMSender",
        ) as MockSender:
            instance = AsyncMock()
            instance.is_configured = True
            instance.connect = AsyncMock()
            instance.disconnect = AsyncMock()
            MockSender.return_value = instance

            stats = await send_approved_telegram_dms(campaign_id, mock_session)

        assert stats == {"sent": 0, "failed": 0, "privacy_restricted": 0, "rate_limited": 0}
        instance.send_dm.assert_not_awaited()
