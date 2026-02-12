"""Unit tests for src/bot/commands.py."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.constants import ParseMode

from src.bot.commands import (
    _esc,
    _generate_link_code,
    approve_command,
    pending_command,
    require_linked_account,
    skip_command,
    start_command,
    stats_command,
    status_command,
)

pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")


# ---------------------------------------------------------------------------
# Test: _generate_link_code
# ---------------------------------------------------------------------------


def test_generate_link_code_default_length():
    """Test that link code has default length of 6."""
    code = _generate_link_code()
    assert len(code) == 6


def test_generate_link_code_custom_length():
    """Test that link code respects custom length."""
    code = _generate_link_code(length=10)
    assert len(code) == 10


def test_generate_link_code_character_set():
    """Test that link code contains only uppercase letters and digits."""
    code = _generate_link_code(length=100)
    valid_chars = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
    assert all(c in valid_chars for c in code)


def test_generate_link_code_uniqueness():
    """Test that multiple calls generate different codes."""
    codes = {_generate_link_code() for _ in range(100)}
    # With 36^6 possibilities, 100 codes should be unique
    assert len(codes) == 100


# ---------------------------------------------------------------------------
# Test: _esc
# ---------------------------------------------------------------------------


def test_esc_ampersand():
    """Test HTML escaping of ampersand."""
    assert _esc("foo&bar") == "foo&amp;bar"


def test_esc_less_than():
    """Test HTML escaping of less-than sign."""
    assert _esc("foo<bar") == "foo&lt;bar"


def test_esc_greater_than():
    """Test HTML escaping of greater-than sign."""
    assert _esc("foo>bar") == "foo&gt;bar"


def test_esc_combined():
    """Test HTML escaping of multiple special characters."""
    assert _esc("a&b<c>d") == "a&amp;b&lt;c&gt;d"


def test_esc_html_tag():
    """Test HTML escaping of a complete tag."""
    assert _esc("<script>alert('xss')</script>") == "&lt;script&gt;alert('xss')&lt;/script&gt;"


def test_esc_no_op():
    """Test that safe text is unchanged."""
    safe_text = "Hello World 123"
    assert _esc(safe_text) == safe_text


# ---------------------------------------------------------------------------
# Test: require_linked_account decorator
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_require_linked_account_user_found():
    """Test decorator when user is found in database."""
    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_user = MagicMock()
    mock_user.id = uuid.uuid4()
    mock_user.telegram_chat_id = 123456
    mock_result.scalar_one_or_none.return_value = mock_user
    mock_session.execute = AsyncMock(return_value=mock_result)

    @asynccontextmanager
    async def mock_get_db_session():
        yield mock_session

    with patch("src.bot.commands.get_db_session", side_effect=mock_get_db_session):
        # Create a simple handler to wrap
        handler_called = False

        @require_linked_account
        async def test_handler(update, context):
            nonlocal handler_called
            handler_called = True
            return "success"

        # Create mock update and context
        mock_update = MagicMock()
        mock_update.effective_user.id = 123456
        mock_update.effective_message.reply_text = AsyncMock()

        mock_context = MagicMock()
        mock_context.user_data = {}

        result = await test_handler(mock_update, mock_context)

        assert handler_called
        assert result == "success"
        assert mock_context.user_data["mas_user"] == mock_user
        mock_update.effective_message.reply_text.assert_not_called()


@pytest.mark.asyncio
async def test_require_linked_account_user_not_found():
    """Test decorator when user is not found in database."""
    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_result)

    @asynccontextmanager
    async def mock_get_db_session():
        yield mock_session

    with patch("src.bot.commands.get_db_session", side_effect=mock_get_db_session):
        handler_called = False

        @require_linked_account
        async def test_handler(update, context):
            nonlocal handler_called
            handler_called = True
            return "success"

        mock_update = MagicMock()
        mock_update.effective_user.id = 123456
        mock_update.effective_message.reply_text = AsyncMock()

        mock_context = MagicMock()
        mock_context.user_data = {}

        result = await test_handler(mock_update, mock_context)

        assert not handler_called
        assert result is None
        mock_update.effective_message.reply_text.assert_called_once()
        call_args = mock_update.effective_message.reply_text.call_args[0][0]
        assert "not linked" in call_args


# ---------------------------------------------------------------------------
# Test: start_command
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_command_already_linked():
    """Test /start when user is already linked."""
    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_user = MagicMock()
    mock_user.name = "John Doe"
    mock_user.email = "john@example.com"  # noqa: S105
    mock_result.scalar_one_or_none.return_value = mock_user
    mock_session.execute = AsyncMock(return_value=mock_result)

    @asynccontextmanager
    async def mock_get_db_session():
        yield mock_session

    with patch("src.bot.commands.get_db_session", side_effect=mock_get_db_session):
        mock_update = MagicMock()
        mock_update.effective_user.id = 123456
        mock_update.effective_message.reply_text = AsyncMock()

        mock_context = MagicMock()
        mock_context.args = []

        await start_command(mock_update, mock_context)

        mock_update.effective_message.reply_text.assert_called_once()
        call_args = mock_update.effective_message.reply_text.call_args
        assert "Welcome back" in call_args[0][0]
        assert "John Doe" in call_args[0][0]
        assert call_args[1]["parse_mode"] == ParseMode.HTML


@pytest.mark.asyncio
async def test_start_command_auto_link_success():
    """Test /start auto when owner exists."""
    mock_owner = MagicMock()
    mock_owner.id = uuid.uuid4()
    mock_owner.name = "Owner User"
    mock_owner.email = "owner@example.com"  # noqa: S105
    mock_owner.telegram_chat_id = None

    # First query returns None (no existing link), second returns owner
    call_count = 0

    def get_session_side_effect():
        nonlocal call_count
        call_count += 1
        mock_session = MagicMock()
        mock_result = MagicMock()

        if call_count == 1:
            # First call: check if already linked
            mock_result.scalar_one_or_none.return_value = None
        else:
            # Second call: find owner
            mock_result.scalar_one_or_none.return_value = mock_owner

        mock_session.execute = AsyncMock(return_value=mock_result)
        return mock_session

    @asynccontextmanager
    async def mock_get_db_session():
        yield get_session_side_effect()

    with patch("src.bot.commands.get_db_session", side_effect=mock_get_db_session):
        mock_update = MagicMock()
        mock_update.effective_user.id = 123456
        mock_update.effective_message.reply_text = AsyncMock()

        mock_context = MagicMock()
        mock_context.args = ["auto"]

        await start_command(mock_update, mock_context)

        assert mock_owner.telegram_chat_id == 123456
        mock_update.effective_message.reply_text.assert_called_once()
        call_args = mock_update.effective_message.reply_text.call_args
        assert "Auto-linked" in call_args[0][0]
        assert "Owner User" in call_args[0][0]


@pytest.mark.asyncio
async def test_start_command_auto_link_no_owner():
    """Test /start auto when no owner exists."""
    call_count = 0

    def get_session_side_effect():
        nonlocal call_count
        call_count += 1
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)
        return mock_session

    @asynccontextmanager
    async def mock_get_db_session():
        yield get_session_side_effect()

    with patch("src.bot.commands.get_db_session", side_effect=mock_get_db_session):
        mock_update = MagicMock()
        mock_update.effective_user.id = 123456
        mock_update.effective_message.reply_text = AsyncMock()

        mock_context = MagicMock()
        mock_context.args = ["auto"]

        await start_command(mock_update, mock_context)

        mock_update.effective_message.reply_text.assert_called_once()
        call_args = mock_update.effective_message.reply_text.call_args[0][0]
        assert "No owner account found" in call_args


@pytest.mark.asyncio
async def test_start_command_generate_link_code():
    """Test /start generates link code and stores in Valkey."""
    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_result)

    @asynccontextmanager
    async def mock_get_db_session():
        yield mock_session

    mock_valkey = MagicMock()
    mock_valkey.setex = AsyncMock()

    with (
        patch("src.bot.commands.get_db_session", side_effect=mock_get_db_session),
        patch("src.bot.commands.get_valkey", return_value=mock_valkey),
        patch("src.bot.commands._generate_link_code", return_value="ABC123"),
    ):
        mock_update = MagicMock()
        mock_update.effective_user.id = 123456
        mock_update.effective_message.reply_text = AsyncMock()

        mock_context = MagicMock()
        mock_context.args = []

        await start_command(mock_update, mock_context)

        mock_valkey.setex.assert_called_once_with("telegram_link:ABC123", 600, "123456")
        mock_update.effective_message.reply_text.assert_called_once()
        call_args = mock_update.effective_message.reply_text.call_args
        assert "ABC123" in call_args[0][0]
        assert "10 minutes" in call_args[0][0]


# ---------------------------------------------------------------------------
# Test: status_command
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_command_no_heartbeats():
    """Test /status when no heartbeats exist."""
    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_session.execute = AsyncMock(return_value=mock_result)

    @asynccontextmanager
    async def mock_get_db_session():
        yield mock_session

    with patch("src.bot.commands.get_db_session", side_effect=mock_get_db_session):
        mock_update = MagicMock()
        mock_update.effective_user.id = 123456
        mock_update.effective_message.reply_text = AsyncMock()

        mock_context = MagicMock()
        mock_context.user_data = {"mas_user": MagicMock()}

        await status_command(mock_update, mock_context)

        mock_update.effective_message.reply_text.assert_called_once()
        call_args = mock_update.effective_message.reply_text.call_args[0][0]
        assert "No agents have reported" in call_args


@pytest.mark.asyncio
async def test_status_command_with_agents():
    """Test /status with mix of healthy, idle, and dead agents."""
    now = datetime.now(UTC)
    healthy_time = now - timedelta(minutes=1)
    dead_time = now - timedelta(minutes=10)

    # Create mock heartbeats
    hb1 = MagicMock()
    hb1.agent_name = "scout"
    hb1.status = "working"
    hb1.last_heartbeat = healthy_time
    hb1.current_task = "searching jobs"

    hb2 = MagicMock()
    hb2.agent_name = "bid"
    hb2.status = "idle"
    hb2.last_heartbeat = healthy_time
    hb2.current_task = None

    hb3 = MagicMock()
    hb3.agent_name = "dev"
    hb3.status = "working"
    hb3.last_heartbeat = dead_time
    hb3.current_task = "generating code"

    async def execute_side_effect(stmt):
        mock_result = MagicMock()
        # Check if this is the heartbeat query or HITL count query
        stmt_str = str(stmt)
        if "agent_heartbeat" in stmt_str.lower() or not hasattr(stmt, "whereclause"):
            # Heartbeat query
            mock_result.scalars.return_value.all.return_value = [hb1, hb2, hb3]
        else:
            # HITL count query
            mock_result.scalar_one.return_value = 5
        return mock_result

    mock_session = MagicMock()
    mock_session.execute = AsyncMock(side_effect=execute_side_effect)

    @asynccontextmanager
    async def mock_get_db_session():
        yield mock_session

    with patch("src.bot.commands.get_db_session", side_effect=mock_get_db_session):
        mock_update = MagicMock()
        mock_update.effective_user.id = 123456
        mock_update.effective_message.reply_text = AsyncMock()

        mock_context = MagicMock()
        mock_context.user_data = {"mas_user": MagicMock()}

        await status_command(mock_update, mock_context)

        mock_update.effective_message.reply_text.assert_called_once()
        call_args = mock_update.effective_message.reply_text.call_args
        text = call_args[0][0]

        assert "Healthy: 2" in text
        assert "Working: 1" in text
        assert "Idle: 1" in text
        assert "Dead: 1" in text
        assert "Pending HITL items: 5" in text
        assert "scout" in text
        assert "searching jobs" in text
        assert call_args[1]["parse_mode"] == ParseMode.HTML


@pytest.mark.asyncio
async def test_status_command_zero_pending_hitl():
    """Test /status shows zero pending HITL items."""
    now = datetime.now(UTC)
    healthy_time = now - timedelta(minutes=1)

    hb = MagicMock()
    hb.agent_name = "scout"
    hb.status = "idle"
    hb.last_heartbeat = healthy_time
    hb.current_task = None

    call_count = 0

    def execute_side_effect(stmt):
        nonlocal call_count
        call_count += 1
        mock_result = MagicMock()

        if call_count == 1:
            mock_result.scalars.return_value.all.return_value = [hb]
        else:
            mock_result.scalar_one.return_value = 0

        return mock_result

    mock_session = MagicMock()
    mock_session.execute = AsyncMock(side_effect=execute_side_effect)

    @asynccontextmanager
    async def mock_get_db_session():
        yield mock_session

    with patch("src.bot.commands.get_db_session", side_effect=mock_get_db_session):
        mock_update = MagicMock()
        mock_update.effective_message.reply_text = AsyncMock()

        mock_context = MagicMock()
        mock_context.user_data = {"mas_user": MagicMock()}

        await status_command(mock_update, mock_context)

        call_args = mock_update.effective_message.reply_text.call_args[0][0]
        assert "Pending HITL items: 0" in call_args


# ---------------------------------------------------------------------------
# Test: pending_command
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pending_command_no_items():
    """Test /pending when no items exist."""
    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []
    mock_session.execute = AsyncMock(return_value=mock_result)

    @asynccontextmanager
    async def mock_get_db_session():
        yield mock_session

    with patch("src.bot.commands.get_db_session", side_effect=mock_get_db_session):
        mock_update = MagicMock()
        mock_update.effective_user.id = 123456
        mock_update.effective_message.reply_text = AsyncMock()

        mock_context = MagicMock()
        mock_context.user_data = {"mas_user": MagicMock()}

        await pending_command(mock_update, mock_context)

        mock_update.effective_message.reply_text.assert_called_once()
        call_args = mock_update.effective_message.reply_text.call_args[0][0]
        assert "No pending HITL items" in call_args


@pytest.mark.asyncio
async def test_pending_command_with_items():
    """Test /pending with items calls send_hitl_card."""
    item1 = MagicMock()
    item1.id = uuid.uuid4()
    item2 = MagicMock()
    item2.id = uuid.uuid4()

    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [item1, item2]
    mock_session.execute = AsyncMock(return_value=mock_result)

    @asynccontextmanager
    async def mock_get_db_session():
        yield mock_session

    mock_send_hitl_card = AsyncMock()

    with (
        patch("src.bot.commands.get_db_session", side_effect=mock_get_db_session),
        patch("src.bot.commands.send_hitl_card", mock_send_hitl_card),
    ):
        mock_update = MagicMock()
        mock_update.effective_user.id = 123456
        mock_update.effective_chat.id = 789
        mock_update.effective_message.reply_text = AsyncMock()

        mock_context = MagicMock()
        mock_context.user_data = {"mas_user": MagicMock()}
        mock_context.bot = MagicMock()

        await pending_command(mock_update, mock_context)

        # Should reply with count
        mock_update.effective_message.reply_text.assert_called_once()
        call_args = mock_update.effective_message.reply_text.call_args[0][0]
        assert "2 pending item(s)" in call_args

        # Should call send_hitl_card for each item
        assert mock_send_hitl_card.call_count == 2
        mock_send_hitl_card.assert_any_call(mock_context.bot, 789, item1)
        mock_send_hitl_card.assert_any_call(mock_context.bot, 789, item2)


@pytest.mark.asyncio
async def test_pending_command_limits_to_five():
    """Test /pending limits results to 5 items."""
    items = [MagicMock(id=uuid.uuid4()) for _ in range(5)]

    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = items
    mock_session.execute = AsyncMock(return_value=mock_result)

    @asynccontextmanager
    async def mock_get_db_session():
        yield mock_session

    mock_send_hitl_card = AsyncMock()

    with (
        patch("src.bot.commands.get_db_session", side_effect=mock_get_db_session),
        patch("src.bot.commands.send_hitl_card", mock_send_hitl_card),
    ):
        mock_update = MagicMock()
        mock_update.effective_chat.id = 789
        mock_update.effective_message.reply_text = AsyncMock()

        mock_context = MagicMock()
        mock_context.user_data = {"mas_user": MagicMock()}
        mock_context.bot = MagicMock()

        await pending_command(mock_update, mock_context)

        # Verify query was executed (limit enforced in SQL, not Python)
        assert mock_send_hitl_card.call_count == 5


# ---------------------------------------------------------------------------
# Test: stats_command
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stats_command_with_counts():
    """Test /stats displays pending, resolved, expired counts."""
    # Mock each query result separately
    mock_pending_result = MagicMock()
    mock_pending_result.scalar_one.return_value = 3

    mock_resolved_result = MagicMock()
    mock_resolved_result.scalar_one.return_value = 7

    mock_expired_result = MagicMock()
    mock_expired_result.scalar_one.return_value = 2

    # Mock user for decorator
    mock_user = MagicMock()
    mock_user.id = uuid.uuid4()
    mock_user_result = MagicMock()
    mock_user_result.scalar_one_or_none.return_value = mock_user

    session_count = 0

    def create_session():
        nonlocal session_count
        session_count += 1
        mock_session = MagicMock()

        if session_count == 1:
            # First session: decorator checks user
            mock_session.execute = AsyncMock(return_value=mock_user_result)
        else:
            # Second session: stats command queries
            mock_session.execute = AsyncMock(
                side_effect=[mock_pending_result, mock_resolved_result, mock_expired_result]
            )

        return mock_session

    @asynccontextmanager
    async def mock_get_db_session():
        yield create_session()

    with patch("src.bot.commands.get_db_session", side_effect=mock_get_db_session):
        mock_update = MagicMock()
        mock_update.effective_user.id = 123456
        mock_update.effective_message.reply_text = AsyncMock()

        mock_context = MagicMock()
        mock_context.user_data = {}

        await stats_command(mock_update, mock_context)

        mock_update.effective_message.reply_text.assert_called_once()
        call_args = mock_update.effective_message.reply_text.call_args
        text = call_args[0][0]

        assert "Pending:  3" in text
        assert "Resolved: 7" in text
        assert "Expired:  2" in text
        assert "Total:    12" in text
        assert call_args[1]["parse_mode"] == ParseMode.HTML


@pytest.mark.asyncio
async def test_stats_command_all_zero():
    """Test /stats with all counts at zero."""
    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one.return_value = 0
    mock_session.execute = AsyncMock(return_value=mock_result)

    @asynccontextmanager
    async def mock_get_db_session():
        yield mock_session

    with patch("src.bot.commands.get_db_session", side_effect=mock_get_db_session):
        mock_update = MagicMock()
        mock_update.effective_message.reply_text = AsyncMock()

        mock_context = MagicMock()
        mock_context.user_data = {"mas_user": MagicMock()}

        await stats_command(mock_update, mock_context)

        call_args = mock_update.effective_message.reply_text.call_args[0][0]
        assert "Pending:  0" in call_args
        assert "Total:    0" in call_args


# ---------------------------------------------------------------------------
# Test: approve_command / skip_command
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_command_delegates():
    """Test /approve delegates to _resolve_command."""
    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_user = MagicMock()
    mock_user.id = uuid.uuid4()
    mock_result.scalar_one_or_none.return_value = mock_user
    mock_session.execute = AsyncMock(return_value=mock_result)

    @asynccontextmanager
    async def mock_get_db_session():
        yield mock_session

    with (
        patch("src.bot.commands.get_db_session", side_effect=mock_get_db_session),
        patch("src.bot.commands._resolve_command", new_callable=AsyncMock) as mock_resolve,
    ):
        mock_update = MagicMock()
        mock_update.effective_user.id = 123456
        mock_update.effective_message.reply_text = AsyncMock()

        mock_context = MagicMock()
        mock_context.user_data = {}

        await approve_command(mock_update, mock_context)

        mock_resolve.assert_called_once_with(mock_update, mock_context, resolution="approve")


@pytest.mark.asyncio
async def test_skip_command_delegates():
    """Test /skip delegates to _resolve_command."""
    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_user = MagicMock()
    mock_user.id = uuid.uuid4()
    mock_result.scalar_one_or_none.return_value = mock_user
    mock_session.execute = AsyncMock(return_value=mock_result)

    @asynccontextmanager
    async def mock_get_db_session():
        yield mock_session

    with (
        patch("src.bot.commands.get_db_session", side_effect=mock_get_db_session),
        patch("src.bot.commands._resolve_command", new_callable=AsyncMock) as mock_resolve,
    ):
        mock_update = MagicMock()
        mock_update.effective_user.id = 123456
        mock_update.effective_message.reply_text = AsyncMock()

        mock_context = MagicMock()
        mock_context.user_data = {}

        await skip_command(mock_update, mock_context)

        mock_resolve.assert_called_once_with(mock_update, mock_context, resolution="skip")


# ---------------------------------------------------------------------------
# Test: _resolve_command
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_command_no_args():
    """Test _resolve_command without arguments shows usage."""
    from src.bot.commands import _resolve_command

    mock_update = MagicMock()
    mock_update.effective_message.reply_text = AsyncMock()

    mock_context = MagicMock()
    mock_context.args = []
    mock_context.user_data = {"mas_user": MagicMock()}

    await _resolve_command(mock_update, mock_context, resolution="approve")

    mock_update.effective_message.reply_text.assert_called_once()
    call_args = mock_update.effective_message.reply_text.call_args[0][0]
    assert "Usage:" in call_args
    assert "approve" in call_args


@pytest.mark.asyncio
async def test_resolve_command_invalid_uuid():
    """Test _resolve_command with invalid UUID format."""
    from src.bot.commands import _resolve_command

    mock_update = MagicMock()
    mock_update.effective_message.reply_text = AsyncMock()

    mock_context = MagicMock()
    mock_context.args = ["not-a-uuid"]
    mock_context.user_data = {"mas_user": MagicMock()}

    await _resolve_command(mock_update, mock_context, resolution="approve")

    mock_update.effective_message.reply_text.assert_called_once()
    call_args = mock_update.effective_message.reply_text.call_args
    assert "Invalid ID format" in call_args[0][0]
    assert call_args[1]["parse_mode"] == ParseMode.HTML


@pytest.mark.asyncio
async def test_resolve_command_item_not_found():
    """Test _resolve_command when HITL item not found."""
    from src.bot.commands import _resolve_command

    item_id = uuid.uuid4()
    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_result)

    @asynccontextmanager
    async def mock_get_db_session():
        yield mock_session

    with patch("src.bot.commands.get_db_session", side_effect=mock_get_db_session):
        mock_update = MagicMock()
        mock_update.effective_message.reply_text = AsyncMock()

        mock_context = MagicMock()
        mock_context.args = [str(item_id)]
        mock_context.user_data = {"mas_user": MagicMock()}

        await _resolve_command(mock_update, mock_context, resolution="approve")

        mock_update.effective_message.reply_text.assert_called_once()
        call_args = mock_update.effective_message.reply_text.call_args
        assert "not found" in call_args[0][0]
        assert str(item_id) in call_args[0][0]


@pytest.mark.asyncio
async def test_resolve_command_item_not_pending():
    """Test _resolve_command when item is not pending."""
    from src.bot.commands import _resolve_command

    item_id = uuid.uuid4()
    mock_item = MagicMock()
    mock_item.id = item_id
    mock_item.status = "resolved"
    mock_item.resolution = "approve"

    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_item
    mock_session.execute = AsyncMock(return_value=mock_result)

    @asynccontextmanager
    async def mock_get_db_session():
        yield mock_session

    with patch("src.bot.commands.get_db_session", side_effect=mock_get_db_session):
        mock_update = MagicMock()
        mock_update.effective_message.reply_text = AsyncMock()

        mock_context = MagicMock()
        mock_context.args = [str(item_id)]
        mock_context.user_data = {"mas_user": MagicMock()}

        await _resolve_command(mock_update, mock_context, resolution="skip")

        mock_update.effective_message.reply_text.assert_called_once()
        call_args = mock_update.effective_message.reply_text.call_args
        text = call_args[0][0]
        assert "already" in text
        assert "resolved" in text
        assert call_args[1]["parse_mode"] == ParseMode.HTML


@pytest.mark.asyncio
async def test_resolve_command_action_not_available():
    """Test _resolve_command when action is not in available_actions."""
    from src.bot.commands import _resolve_command

    item_id = uuid.uuid4()
    mock_item = MagicMock()
    mock_item.id = item_id
    mock_item.status = "pending"
    mock_item.available_actions = ["approve", "reject"]

    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_item
    mock_session.execute = AsyncMock(return_value=mock_result)

    @asynccontextmanager
    async def mock_get_db_session():
        yield mock_session

    with patch("src.bot.commands.get_db_session", side_effect=mock_get_db_session):
        mock_update = MagicMock()
        mock_update.effective_message.reply_text = AsyncMock()

        mock_context = MagicMock()
        mock_context.args = [str(item_id)]
        mock_context.user_data = {"mas_user": MagicMock()}

        await _resolve_command(mock_update, mock_context, resolution="skip")

        mock_update.effective_message.reply_text.assert_called_once()
        call_args = mock_update.effective_message.reply_text.call_args
        text = call_args[0][0]
        assert "not available" in text
        assert "skip" in text
        assert "approve, reject" in text


@pytest.mark.asyncio
async def test_resolve_command_success_approve():
    """Test _resolve_command success with approve action."""
    from src.bot.commands import _resolve_command

    item_id = uuid.uuid4()
    user_id = uuid.uuid4()

    mock_item = MagicMock()
    mock_item.id = item_id
    mock_item.status = "pending"
    mock_item.available_actions = ["approve", "skip"]
    mock_item.resolution = None
    mock_item.resolved_by = None
    mock_item.resolved_at = None

    mock_user = MagicMock()
    mock_user.id = user_id

    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_item
    mock_session.execute = AsyncMock(return_value=mock_result)

    @asynccontextmanager
    async def mock_get_db_session():
        yield mock_session

    with patch("src.bot.commands.get_db_session", side_effect=mock_get_db_session):
        mock_update = MagicMock()
        mock_update.effective_message.reply_text = AsyncMock()

        mock_context = MagicMock()
        mock_context.args = [str(item_id)]
        mock_context.user_data = {"mas_user": mock_user}

        await _resolve_command(mock_update, mock_context, resolution="approve")

        # Verify item was updated
        assert mock_item.status == "resolved"
        assert mock_item.resolution == "approve"
        assert mock_item.resolved_by == user_id
        assert mock_item.resolved_at is not None

        # Verify success message
        mock_update.effective_message.reply_text.assert_called_once()
        call_args = mock_update.effective_message.reply_text.call_args
        text = call_args[0][0]
        assert str(item_id) in text
        assert "approved" in text
        assert call_args[1]["parse_mode"] == ParseMode.HTML


@pytest.mark.asyncio
async def test_resolve_command_success_skip():
    """Test _resolve_command success with skip action."""
    from src.bot.commands import _resolve_command

    item_id = uuid.uuid4()
    user_id = uuid.uuid4()

    mock_item = MagicMock()
    mock_item.id = item_id
    mock_item.status = "pending"
    mock_item.available_actions = ["approve", "skip"]

    mock_user = MagicMock()
    mock_user.id = user_id

    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_item
    mock_session.execute = AsyncMock(return_value=mock_result)

    @asynccontextmanager
    async def mock_get_db_session():
        yield mock_session

    with patch("src.bot.commands.get_db_session", side_effect=mock_get_db_session):
        mock_update = MagicMock()
        mock_update.effective_message.reply_text = AsyncMock()

        mock_context = MagicMock()
        mock_context.args = [str(item_id)]
        mock_context.user_data = {"mas_user": mock_user}

        await _resolve_command(mock_update, mock_context, resolution="skip")

        assert mock_item.resolution == "skip"
        mock_update.effective_message.reply_text.assert_called_once()
        call_args = mock_update.effective_message.reply_text.call_args[0][0]
        # Message is: "⏭️ Item <code>...</code> — <b>skipd</b>."
        # The code appends 'd' to resolution, so 'skip' -> 'skipd' not 'skipped'
        assert "skipd" in call_args
