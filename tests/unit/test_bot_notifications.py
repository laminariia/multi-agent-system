"""Unit tests for Telegram notifications module."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.bot.notifications import (
    TelegramNotifier,
    _build_reply_markup,
    _esc,
)

# ---------------------------------------------------------------------------
# Test _esc helper
# ---------------------------------------------------------------------------


class TestEscapeHTML:
    """Tests for _esc() HTML escape helper."""

    def test_esc_ampersand(self) -> None:
        """Escape & character."""
        assert _esc("Bob & Alice") == "Bob &amp; Alice"

    def test_esc_less_than(self) -> None:
        """Escape < character."""
        assert _esc("x < 10") == "x &lt; 10"

    def test_esc_greater_than(self) -> None:
        """Escape > character."""
        assert _esc("x > 5") == "x &gt; 5"

    def test_esc_multiple_special_chars(self) -> None:
        """Escape multiple special characters."""
        result = _esc("Bob & Alice: <tag> is > 3")
        assert result == "Bob &amp; Alice: &lt;tag&gt; is &gt; 3"

    def test_esc_empty_string(self) -> None:
        """Handle empty string."""
        assert _esc("") == ""

    def test_esc_no_special_chars(self) -> None:
        """Handle text without special characters."""
        assert _esc("Hello World") == "Hello World"

    def test_esc_already_escaped(self) -> None:
        """Escape already escaped text (double-escaping)."""
        # This is expected behavior - escaping is not idempotent
        assert _esc("Bob &amp; Alice") == "Bob &amp;amp; Alice"


# ---------------------------------------------------------------------------
# Test _build_reply_markup helper
# ---------------------------------------------------------------------------


class TestBuildReplyMarkup:
    """Tests for _build_reply_markup() inline keyboard builder."""

    def test_bid_approval_buttons(self) -> None:
        """Returns correct buttons for bid_approval."""
        uuid_hex = uuid.uuid4().hex
        result = _build_reply_markup("bid_approval", uuid_hex)

        assert result is not None
        assert "inline_keyboard" in result
        assert len(result["inline_keyboard"]) == 1  # Single row
        buttons = result["inline_keyboard"][0]
        assert len(buttons) == 3

        assert buttons[0]["text"] == "✅ Approve"
        assert buttons[0]["callback_data"] == f"hitl:approve:{uuid_hex}"
        assert buttons[1]["text"] == "❌ Skip"
        assert buttons[1]["callback_data"] == f"hitl:skip:{uuid_hex}"
        assert buttons[2]["text"] == "⏸️ Later"
        assert buttons[2]["callback_data"] == f"hitl:later:{uuid_hex}"

    def test_code_review_buttons(self) -> None:
        """Returns correct buttons for code_review."""
        uuid_hex = uuid.uuid4().hex
        result = _build_reply_markup("code_review", uuid_hex)

        assert result is not None
        buttons = result["inline_keyboard"][0]
        assert len(buttons) == 2

        assert buttons[0]["text"] == "✅ Approve"
        assert buttons[1]["text"] == "❌ Skip"

    def test_delivery_buttons(self) -> None:
        """Returns correct buttons for delivery."""
        uuid_hex = uuid.uuid4().hex
        result = _build_reply_markup("delivery", uuid_hex)

        assert result is not None
        buttons = result["inline_keyboard"][0]
        assert len(buttons) == 2

        assert buttons[0]["text"] == "✅ Approve"
        assert buttons[1]["text"] == "⏸️ Later"

    def test_revision_buttons(self) -> None:
        """Returns correct buttons for revision."""
        uuid_hex = uuid.uuid4().hex
        result = _build_reply_markup("revision", uuid_hex)

        assert result is not None
        buttons = result["inline_keyboard"][0]
        assert len(buttons) == 3

        assert buttons[0]["text"] == "✅ Approve"
        assert buttons[1]["text"] == "❌ Skip"
        assert buttons[2]["text"] == "⏸️ Later"

    def test_alert_buttons(self) -> None:
        """Returns correct buttons for alert."""
        uuid_hex = uuid.uuid4().hex
        result = _build_reply_markup("alert", uuid_hex)

        assert result is not None
        buttons = result["inline_keyboard"][0]
        assert len(buttons) == 2

        assert buttons[0]["text"] == "✅ Ack"
        assert buttons[1]["text"] == "❌ Skip"

    def test_unknown_type_returns_none(self) -> None:
        """Returns None for unknown type."""
        result = _build_reply_markup("unknown_type", "abc123")
        assert result is None

    def test_uuid_substitution(self) -> None:
        """Substitutes uuid_hex into callback_data."""
        uuid_hex = "deadbeef1234567890abcdef12345678"
        result = _build_reply_markup("bid_approval", uuid_hex)

        assert result is not None
        buttons = result["inline_keyboard"][0]
        for btn in buttons:
            assert uuid_hex in btn["callback_data"]
            assert "{uuid_hex}" not in btn["callback_data"]


# ---------------------------------------------------------------------------
# Test TelegramNotifier.__init__
# ---------------------------------------------------------------------------


class TestTelegramNotifierInit:
    """Tests for TelegramNotifier initialization."""

    def test_init_with_provided_token(self) -> None:
        """Uses provided token."""
        token = "test-bot-token:12345"  # noqa: S105
        with patch("src.bot.notifications.get_settings"):
            notifier = TelegramNotifier(token=token)
            assert notifier._token == token

    def test_init_token_from_settings(self) -> None:
        """Gets token from settings if not provided."""
        settings_token = "settings-token:67890"  # noqa: S105
        mock_settings = MagicMock()
        mock_settings.TELEGRAM_BOT_TOKEN = settings_token

        with patch("src.bot.notifications.get_settings", return_value=mock_settings):
            notifier = TelegramNotifier()
            assert notifier._token == settings_token

    def test_init_no_token_raises_error(self) -> None:
        """Raises RuntimeError if no token configured."""
        mock_settings = MagicMock()
        mock_settings.TELEGRAM_BOT_TOKEN = None

        with patch("src.bot.notifications.get_settings", return_value=mock_settings):
            with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN is not configured"):
                TelegramNotifier()

    def test_init_empty_token_raises_error(self) -> None:
        """Raises RuntimeError if token is empty string."""
        mock_settings = MagicMock()
        mock_settings.TELEGRAM_BOT_TOKEN = ""

        with patch("src.bot.notifications.get_settings", return_value=mock_settings):
            with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN is not configured"):
                TelegramNotifier()

    def test_init_custom_timeout(self) -> None:
        """Sets custom timeout."""
        mock_settings = MagicMock()
        mock_settings.TELEGRAM_BOT_TOKEN = "test-token"  # noqa: S105

        with patch("src.bot.notifications.get_settings", return_value=mock_settings):
            notifier = TelegramNotifier(timeout=20.0)
            assert notifier._timeout == 20.0


# ---------------------------------------------------------------------------
# Test TelegramNotifier.send_message
# ---------------------------------------------------------------------------


class TestTelegramNotifierSendMessage:
    """Tests for TelegramNotifier.send_message()."""

    @pytest.fixture
    def notifier(self) -> TelegramNotifier:
        """Create a notifier instance for testing."""
        mock_settings = MagicMock()
        mock_settings.TELEGRAM_BOT_TOKEN = "test-bot-token:12345"  # noqa: S105

        with patch("src.bot.notifications.get_settings", return_value=mock_settings):
            return TelegramNotifier()

    @pytest.mark.asyncio
    async def test_send_message_success(self, notifier: TelegramNotifier) -> None:
        """Sends POST to correct URL with payload and returns response data."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True, "result": {"message_id": 123}}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client) as mock_async_client:
            mock_async_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_async_client.return_value.__aexit__ = AsyncMock()

            result = await notifier.send_message(
                chat_id=12345678,
                text="Hello World",
            )

        assert result == {"ok": True, "result": {"message_id": 123}}

        # Verify POST was called with correct URL and payload
        mock_client.post.assert_awaited_once()
        call_args = mock_client.post.call_args
        assert "sendMessage" in call_args[0][0]
        assert "test-bot-token:12345" in call_args[0][0]
        assert call_args[1]["json"]["chat_id"] == 12345678
        assert call_args[1]["json"]["text"] == "Hello World"
        assert call_args[1]["json"]["parse_mode"] == "HTML"

    @pytest.mark.asyncio
    async def test_send_message_with_reply_markup(self, notifier: TelegramNotifier) -> None:
        """Includes reply_markup in payload when provided."""
        reply_markup = {"inline_keyboard": [[{"text": "Click", "callback_data": "data"}]]}
        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True, "result": {}}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client) as mock_async_client:
            mock_async_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_async_client.return_value.__aexit__ = AsyncMock()

            await notifier.send_message(
                chat_id=12345678,
                text="Test",
                reply_markup=reply_markup,
            )

        call_args = mock_client.post.call_args
        assert call_args[1]["json"]["reply_markup"] == reply_markup

    @pytest.mark.asyncio
    async def test_send_message_http_error_returns_none(self, notifier: TelegramNotifier) -> None:
        """Returns None on HTTP error."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.HTTPStatusError(
            "404 Not Found",
            request=MagicMock(),
            response=MagicMock(),
        ))

        with patch("httpx.AsyncClient", return_value=mock_client) as mock_async_client:
            mock_async_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_async_client.return_value.__aexit__ = AsyncMock()

            result = await notifier.send_message(chat_id=12345678, text="Test")

        assert result is None

    @pytest.mark.asyncio
    async def test_send_message_api_ok_false_returns_none(self, notifier: TelegramNotifier) -> None:
        """Returns None when API returns ok=false."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "ok": False,
            "description": "Bad Request: chat not found",
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client) as mock_async_client:
            mock_async_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_async_client.return_value.__aexit__ = AsyncMock()

            result = await notifier.send_message(chat_id=12345678, text="Test")

        assert result is None

    @pytest.mark.asyncio
    async def test_send_message_network_error_returns_none(self, notifier: TelegramNotifier) -> None:
        """Returns None on network error."""
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

        with patch("httpx.AsyncClient", return_value=mock_client) as mock_async_client:
            mock_async_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_async_client.return_value.__aexit__ = AsyncMock()

            result = await notifier.send_message(chat_id=12345678, text="Test")

        assert result is None

    @pytest.mark.asyncio
    async def test_send_message_custom_parse_mode(self, notifier: TelegramNotifier) -> None:
        """Supports custom parse_mode."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True, "result": {}}
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client) as mock_async_client:
            mock_async_client.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_async_client.return_value.__aexit__ = AsyncMock()

            await notifier.send_message(
                chat_id=12345678,
                text="Test",
                parse_mode="Markdown",
            )

        call_args = mock_client.post.call_args
        assert call_args[1]["json"]["parse_mode"] == "Markdown"


# ---------------------------------------------------------------------------
# Test TelegramNotifier.notify_new_hitl
# ---------------------------------------------------------------------------


class TestTelegramNotifierNotifyNewHITL:
    """Tests for TelegramNotifier.notify_new_hitl()."""

    @pytest.fixture
    def notifier(self) -> TelegramNotifier:
        """Create a notifier instance for testing."""
        mock_settings = MagicMock()
        mock_settings.TELEGRAM_BOT_TOKEN = "test-bot-token:12345"  # noqa: S105

        with patch("src.bot.notifications.get_settings", return_value=mock_settings):
            return TelegramNotifier()

    @pytest.fixture
    def mock_user(self) -> MagicMock:
        """Create a mock user."""
        user = MagicMock()
        user.id = uuid.uuid4()
        user.telegram_chat_id = 12345678
        return user

    @pytest.fixture
    def mock_hitl(self) -> MagicMock:
        """Create a mock HITL item."""
        hitl = MagicMock()
        hitl.id = uuid.uuid4()
        hitl.type = "bid_approval"
        hitl.priority = "urgent"
        hitl.title = "New bid for project"
        hitl.description = "Test description for the project"
        hitl.expires_at = datetime.now(UTC) + timedelta(hours=1)
        return hitl

    @pytest.mark.asyncio
    async def test_notify_new_hitl_no_telegram_chat_id_returns_false(
        self,
        notifier: TelegramNotifier,
        mock_hitl: MagicMock,
    ) -> None:
        """Returns False if user has no telegram_chat_id."""
        user = MagicMock()
        user.id = uuid.uuid4()
        user.telegram_chat_id = None

        result = await notifier.notify_new_hitl(user, mock_hitl)

        assert result is False

    @pytest.mark.asyncio
    async def test_notify_new_hitl_sends_formatted_message(
        self,
        notifier: TelegramNotifier,
        mock_user: MagicMock,
        mock_hitl: MagicMock,
    ) -> None:
        """Sends formatted message with emoji, title, type, priority."""
        mock_response = {"ok": True, "result": {"message_id": 123}}

        with patch.object(notifier, "send_message", return_value=mock_response) as mock_send:
            result = await notifier.notify_new_hitl(mock_user, mock_hitl)

        assert result is True
        mock_send.assert_awaited_once()
        call_args = mock_send.call_args

        # Verify chat_id
        assert call_args[1]["chat_id"] == 12345678

        # Verify message text
        text = call_args[1]["text"]
        assert "📝" in text  # bid_approval emoji
        assert "<b>New HITL Item</b>" in text
        assert "New bid for project" in text
        assert "bid_approval" in text
        assert "URGENT" in text

    @pytest.mark.asyncio
    async def test_notify_new_hitl_includes_description_truncated(
        self,
        notifier: TelegramNotifier,
        mock_user: MagicMock,
        mock_hitl: MagicMock,
    ) -> None:
        """Includes description truncated to 200 chars."""
        mock_hitl.description = "A" * 250  # Long description
        mock_response = {"ok": True, "result": {"message_id": 123}}

        with patch.object(notifier, "send_message", return_value=mock_response) as mock_send:
            await notifier.notify_new_hitl(mock_user, mock_hitl)

        text = mock_send.call_args[1]["text"]
        assert ("A" * 200 + "...") in text
        assert ("A" * 250) not in text

    @pytest.mark.asyncio
    async def test_notify_new_hitl_short_description_no_truncation(
        self,
        notifier: TelegramNotifier,
        mock_user: MagicMock,
        mock_hitl: MagicMock,
    ) -> None:
        """Does not truncate short descriptions."""
        mock_hitl.description = "Short description"
        mock_response = {"ok": True, "result": {"message_id": 123}}

        with patch.object(notifier, "send_message", return_value=mock_response) as mock_send:
            await notifier.notify_new_hitl(mock_user, mock_hitl)

        text = mock_send.call_args[1]["text"]
        assert "Short description" in text
        assert "..." not in text

    @pytest.mark.asyncio
    async def test_notify_new_hitl_includes_inline_keyboard(
        self,
        notifier: TelegramNotifier,
        mock_user: MagicMock,
        mock_hitl: MagicMock,
    ) -> None:
        """Includes inline keyboard buttons."""
        mock_response = {"ok": True, "result": {"message_id": 123}}

        with patch.object(notifier, "send_message", return_value=mock_response) as mock_send:
            await notifier.notify_new_hitl(mock_user, mock_hitl)

        reply_markup = mock_send.call_args[1]["reply_markup"]
        assert reply_markup is not None
        assert "inline_keyboard" in reply_markup
        buttons = reply_markup["inline_keyboard"][0]
        assert len(buttons) == 3  # bid_approval has 3 buttons
        assert mock_hitl.id.hex in buttons[0]["callback_data"]

    @pytest.mark.asyncio
    async def test_notify_new_hitl_success_returns_true(
        self,
        notifier: TelegramNotifier,
        mock_user: MagicMock,
        mock_hitl: MagicMock,
    ) -> None:
        """Returns True on success."""
        mock_response = {"ok": True, "result": {"message_id": 123}}

        with patch.object(notifier, "send_message", return_value=mock_response):
            result = await notifier.notify_new_hitl(mock_user, mock_hitl)

        assert result is True

    @pytest.mark.asyncio
    async def test_notify_new_hitl_failure_returns_false(
        self,
        notifier: TelegramNotifier,
        mock_user: MagicMock,
        mock_hitl: MagicMock,
    ) -> None:
        """Returns False on failure."""
        with patch.object(notifier, "send_message", return_value=None):
            result = await notifier.notify_new_hitl(mock_user, mock_hitl)

        assert result is False

    @pytest.mark.asyncio
    async def test_notify_new_hitl_includes_expires_at(
        self,
        notifier: TelegramNotifier,
        mock_user: MagicMock,
        mock_hitl: MagicMock,
    ) -> None:
        """Includes expires_at timestamp."""
        expires = datetime(2026, 2, 10, 15, 30, 0, tzinfo=UTC)
        mock_hitl.expires_at = expires
        mock_response = {"ok": True, "result": {"message_id": 123}}

        with patch.object(notifier, "send_message", return_value=mock_response) as mock_send:
            await notifier.notify_new_hitl(mock_user, mock_hitl)

        text = mock_send.call_args[1]["text"]
        assert "Expires: 2026-02-10 15:30 UTC" in text

    @pytest.mark.asyncio
    async def test_notify_new_hitl_no_expires_at(
        self,
        notifier: TelegramNotifier,
        mock_user: MagicMock,
        mock_hitl: MagicMock,
    ) -> None:
        """Handles missing expires_at."""
        mock_hitl.expires_at = None
        mock_response = {"ok": True, "result": {"message_id": 123}}

        with patch.object(notifier, "send_message", return_value=mock_response) as mock_send:
            await notifier.notify_new_hitl(mock_user, mock_hitl)

        text = mock_send.call_args[1]["text"]
        assert "Expires:" not in text

    @pytest.mark.asyncio
    async def test_notify_new_hitl_escapes_html_in_title(
        self,
        notifier: TelegramNotifier,
        mock_user: MagicMock,
        mock_hitl: MagicMock,
    ) -> None:
        """Escapes HTML characters in title."""
        mock_hitl.title = "Project <script>alert('xss')</script>"
        mock_response = {"ok": True, "result": {"message_id": 123}}

        with patch.object(notifier, "send_message", return_value=mock_response) as mock_send:
            await notifier.notify_new_hitl(mock_user, mock_hitl)

        text = mock_send.call_args[1]["text"]
        assert "&lt;script&gt;" in text
        assert "<script>" not in text

    @pytest.mark.asyncio
    async def test_notify_new_hitl_different_types(
        self,
        notifier: TelegramNotifier,
        mock_user: MagicMock,
        mock_hitl: MagicMock,
    ) -> None:
        """Uses correct emoji and buttons for different HITL types."""
        mock_response = {"ok": True, "result": {"message_id": 123}}

        # Test code_review type
        mock_hitl.type = "code_review"
        with patch.object(notifier, "send_message", return_value=mock_response) as mock_send:
            await notifier.notify_new_hitl(mock_user, mock_hitl)

        text = mock_send.call_args[1]["text"]
        assert "🔍" in text  # code_review emoji
        reply_markup = mock_send.call_args[1]["reply_markup"]
        assert len(reply_markup["inline_keyboard"][0]) == 2  # code_review has 2 buttons

    @pytest.mark.asyncio
    async def test_notify_new_hitl_unknown_type_uses_default_emoji(
        self,
        notifier: TelegramNotifier,
        mock_user: MagicMock,
        mock_hitl: MagicMock,
    ) -> None:
        """Uses default emoji for unknown type."""
        mock_hitl.type = "unknown_type"
        mock_response = {"ok": True, "result": {"message_id": 123}}

        with patch.object(notifier, "send_message", return_value=mock_response) as mock_send:
            await notifier.notify_new_hitl(mock_user, mock_hitl)

        text = mock_send.call_args[1]["text"]
        assert "❓" in text  # Default emoji

    @pytest.mark.asyncio
    async def test_notify_new_hitl_includes_hitl_id(
        self,
        notifier: TelegramNotifier,
        mock_user: MagicMock,
        mock_hitl: MagicMock,
    ) -> None:
        """Includes HITL ID in message."""
        mock_response = {"ok": True, "result": {"message_id": 123}}

        with patch.object(notifier, "send_message", return_value=mock_response) as mock_send:
            await notifier.notify_new_hitl(mock_user, mock_hitl)

        text = mock_send.call_args[1]["text"]
        assert f"<code>{mock_hitl.id}</code>" in text
