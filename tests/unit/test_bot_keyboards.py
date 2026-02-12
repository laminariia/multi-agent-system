"""Unit tests for src/bot/keyboards.py — Telegram inline keyboards & HITL callbacks."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram import Bot, Update
from telegram.ext import ContextTypes

from src.bot.keyboards import (
    _DEFAULT_BUTTONS,
    _TYPE_BUTTONS,
    _TYPE_EMOJI,
    _esc,
    _format_payload,
    button_callback,
    send_hitl_card,
)

pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")


# =============================================================================
# Module-level constant tests
# =============================================================================


def test_type_emoji_known_types():
    """All 5 HITL types have emoji mappings."""
    assert _TYPE_EMOJI["bid_approval"] == "\U0001f4dd"
    assert _TYPE_EMOJI["code_review"] == "\U0001f50d"
    assert _TYPE_EMOJI["delivery"] == "\U0001f4e6"
    assert _TYPE_EMOJI["revision"] == "\U0001f504"
    assert _TYPE_EMOJI["alert"] == "\u26a0\ufe0f"


def test_type_buttons_bid_approval():
    """bid_approval has 3 buttons: approve, skip, later."""
    buttons = _TYPE_BUTTONS["bid_approval"]
    assert len(buttons) == 3
    labels = [label for label, _ in buttons]
    actions = [action for _, action in buttons]
    assert "\u2705 Approve" in labels
    assert "\u274c Skip" in labels
    assert "\u23f8\ufe0f Later" in labels
    assert "approve" in actions
    assert "skip" in actions
    assert "later" in actions


def test_type_buttons_code_review():
    """code_review has 2 buttons: approve, skip."""
    buttons = _TYPE_BUTTONS["code_review"]
    assert len(buttons) == 2
    actions = [action for _, action in buttons]
    assert "approve" in actions
    assert "skip" in actions


def test_type_buttons_delivery():
    """delivery has 2 buttons: approve, later."""
    buttons = _TYPE_BUTTONS["delivery"]
    assert len(buttons) == 2
    actions = [action for _, action in buttons]
    assert "approve" in actions
    assert "later" in actions


def test_type_buttons_revision():
    """revision has 3 buttons: approve, skip, later."""
    buttons = _TYPE_BUTTONS["revision"]
    assert len(buttons) == 3
    actions = [action for _, action in buttons]
    assert "approve" in actions
    assert "skip" in actions
    assert "later" in actions


def test_type_buttons_alert():
    """alert has 2 buttons: Ack (approve), skip."""
    buttons = _TYPE_BUTTONS["alert"]
    assert len(buttons) == 2
    labels = [label for label, _ in buttons]
    actions = [action for _, action in buttons]
    assert "\u2705 Ack" in labels
    assert "approve" in actions
    assert "skip" in actions


def test_default_buttons():
    """Default buttons fallback has approve and skip."""
    assert len(_DEFAULT_BUTTONS) == 2
    actions = [action for _, action in _DEFAULT_BUTTONS]
    assert "approve" in actions
    assert "skip" in actions


# =============================================================================
# _esc helper
# =============================================================================


def test_esc_html_special_characters():
    """_esc escapes &, <, >."""
    assert _esc("A&B") == "A&amp;B"
    assert _esc("<script>") == "&lt;script&gt;"
    assert _esc("5 > 3 & 2 < 4") == "5 &gt; 3 &amp; 2 &lt; 4"


def test_esc_plain_text():
    """_esc returns plain text unchanged."""
    assert _esc("Hello World") == "Hello World"


def test_esc_empty_string():
    """_esc handles empty string."""
    assert _esc("") == ""


# =============================================================================
# _format_payload helper
# =============================================================================


def test_format_payload_empty_dict():
    """Empty dict returns empty list."""
    result = _format_payload({})
    assert result == []


def test_format_payload_single_item():
    """Single item formatted as bold key + value."""
    result = _format_payload({"job_id": 123})
    assert len(result) == 1
    assert "job_id" in result[0]
    assert "123" in result[0]
    assert "<b>" in result[0]


def test_format_payload_multiple_items():
    """Multiple items each get a line."""
    result = _format_payload({"job_id": 123, "platform": "freelancer"})
    assert len(result) == 2


def test_format_payload_max_items_truncation():
    """Respects max_items limit and appends '...'."""
    payload = {f"key{i}": f"value{i}" for i in range(10)}
    result = _format_payload(payload, max_items=3)
    assert len(result) == 4  # 3 items + "..."
    assert result[-1] == "  ..."


def test_format_payload_long_value_truncation():
    """Long values truncated at 100 chars."""
    long_value = "x" * 150
    result = _format_payload({"long": long_value})
    assert len(result) == 1
    assert "..." in result[0]
    # Check truncation (100 chars + "...")
    assert "xxx..." in result[0]


def test_format_payload_html_escaping():
    """Payload values are HTML-escaped."""
    result = _format_payload({"script": "<script>alert('XSS')</script>"})
    assert "&lt;script&gt;" in result[0]
    assert "&lt;/script&gt;" in result[0]


# =============================================================================
# send_hitl_card
# =============================================================================


@pytest.mark.asyncio
async def test_send_hitl_card_bid_approval_all_fields():
    """send_hitl_card sends bid_approval with all fields."""
    bot = MagicMock(spec=Bot)
    bot.send_message = AsyncMock()

    item_id = uuid.uuid4()
    item = MagicMock()
    item.id = item_id
    item.type = "bid_approval"
    item.title = "Test Bid"
    item.priority = "high"
    item.description = "Test description"
    item.payload = {"job_id": 123}
    item.available_actions = ["approve", "skip", "later"]
    item.expires_at = datetime(2026, 2, 10, 12, 0, 0, tzinfo=UTC)

    await send_hitl_card(bot, 123456789, item)

    bot.send_message.assert_awaited_once()
    call_kwargs = bot.send_message.call_args.kwargs
    assert call_kwargs["chat_id"] == 123456789
    assert "Test Bid" in call_kwargs["text"]
    assert "bid_approval" in call_kwargs["text"]
    assert "HIGH" in call_kwargs["text"]
    assert "Test description" in call_kwargs["text"]
    assert "job_id" in call_kwargs["text"]
    assert str(item_id) in call_kwargs["text"]
    assert "2026-02-10" in call_kwargs["text"]
    assert call_kwargs["parse_mode"] == "HTML"
    assert call_kwargs["reply_markup"] is not None
    # Check 3 buttons in markup
    markup = call_kwargs["reply_markup"]
    buttons = [btn for row in markup.inline_keyboard for btn in row]
    assert len(buttons) == 3


@pytest.mark.asyncio
async def test_send_hitl_card_unknown_type_uses_default_buttons():
    """Unknown type uses default emoji and default buttons."""
    bot = MagicMock(spec=Bot)
    bot.send_message = AsyncMock()

    item = MagicMock()
    item.id = uuid.uuid4()
    item.type = "unknown_type"
    item.title = "Test"
    item.priority = None
    item.description = None
    item.payload = None
    item.available_actions = ["approve", "skip"]
    item.expires_at = None

    await send_hitl_card(bot, 123456789, item)

    call_kwargs = bot.send_message.call_args.kwargs
    assert "\u2753" in call_kwargs["text"]  # Default emoji
    markup = call_kwargs["reply_markup"]
    buttons = [btn for row in markup.inline_keyboard for btn in row]
    assert len(buttons) == 2  # Default buttons


@pytest.mark.asyncio
async def test_send_hitl_card_no_description():
    """send_hitl_card handles missing description."""
    bot = MagicMock(spec=Bot)
    bot.send_message = AsyncMock()

    item = MagicMock()
    item.id = uuid.uuid4()
    item.type = "code_review"
    item.title = "Test"
    item.priority = "normal"
    item.description = None
    item.payload = None
    item.available_actions = ["approve", "skip"]
    item.expires_at = None

    await send_hitl_card(bot, 123456789, item)

    call_kwargs = bot.send_message.call_args.kwargs
    # Should not crash, just skip description
    assert "Test" in call_kwargs["text"]


@pytest.mark.asyncio
async def test_send_hitl_card_long_description_truncated():
    """Description longer than 300 chars is truncated with '...'."""
    bot = MagicMock(spec=Bot)
    bot.send_message = AsyncMock()

    long_desc = "x" * 400
    item = MagicMock()
    item.id = uuid.uuid4()
    item.type = "revision"
    item.title = "Test"
    item.priority = "low"
    item.description = long_desc
    item.payload = None
    item.available_actions = ["approve"]
    item.expires_at = None

    await send_hitl_card(bot, 123456789, item)

    call_kwargs = bot.send_message.call_args.kwargs
    # Check truncation marker
    assert "xxx..." in call_kwargs["text"]


@pytest.mark.asyncio
async def test_send_hitl_card_no_payload():
    """send_hitl_card handles missing payload."""
    bot = MagicMock(spec=Bot)
    bot.send_message = AsyncMock()

    item = MagicMock()
    item.id = uuid.uuid4()
    item.type = "alert"
    item.title = "Test"
    item.priority = "high"
    item.description = "Alert"
    item.payload = None
    item.available_actions = ["approve", "skip"]
    item.expires_at = None

    await send_hitl_card(bot, 123456789, item)

    call_kwargs = bot.send_message.call_args.kwargs
    # Should not include payload section
    assert "Test" in call_kwargs["text"]


@pytest.mark.asyncio
async def test_send_hitl_card_with_expires_at():
    """send_hitl_card includes expires_at timestamp."""
    bot = MagicMock(spec=Bot)
    bot.send_message = AsyncMock()

    item = MagicMock()
    item.id = uuid.uuid4()
    item.type = "delivery"
    item.title = "Test"
    item.priority = "normal"
    item.description = None
    item.payload = None
    item.available_actions = ["approve", "later"]
    item.expires_at = datetime(2026, 3, 15, 18, 30, 0, tzinfo=UTC)

    await send_hitl_card(bot, 123456789, item)

    call_kwargs = bot.send_message.call_args.kwargs
    assert "Expires:" in call_kwargs["text"]
    assert "2026-03-15" in call_kwargs["text"]


@pytest.mark.asyncio
async def test_send_hitl_card_available_actions_filtering():
    """Buttons filtered by available_actions."""
    bot = MagicMock(spec=Bot)
    bot.send_message = AsyncMock()

    item = MagicMock()
    item.id = uuid.uuid4()
    item.type = "bid_approval"
    item.title = "Test"
    item.priority = "normal"
    item.description = None
    item.payload = None
    item.available_actions = ["approve"]  # Only approve
    item.expires_at = None

    await send_hitl_card(bot, 123456789, item)

    call_kwargs = bot.send_message.call_args.kwargs
    markup = call_kwargs["reply_markup"]
    buttons = [btn for row in markup.inline_keyboard for btn in row]
    assert len(buttons) == 1
    assert "approve" in buttons[0].callback_data


@pytest.mark.asyncio
async def test_send_hitl_card_empty_available_actions_shows_all():
    """Empty available_actions list shows all type buttons."""
    bot = MagicMock(spec=Bot)
    bot.send_message = AsyncMock()

    item = MagicMock()
    item.id = uuid.uuid4()
    item.type = "code_review"
    item.title = "Test"
    item.priority = "normal"
    item.description = None
    item.payload = None
    item.available_actions = []  # Empty
    item.expires_at = None

    await send_hitl_card(bot, 123456789, item)

    call_kwargs = bot.send_message.call_args.kwargs
    markup = call_kwargs["reply_markup"]
    buttons = [btn for row in markup.inline_keyboard for btn in row]
    # Should show all code_review buttons (2)
    assert len(buttons) == 2


@pytest.mark.asyncio
async def test_send_hitl_card_callback_data_format():
    """Callback data uses hitl:action:uuid_hex format."""
    bot = MagicMock(spec=Bot)
    bot.send_message = AsyncMock()

    item_id = uuid.uuid4()
    item = MagicMock()
    item.id = item_id
    item.type = "bid_approval"
    item.title = "Test"
    item.priority = "normal"
    item.description = None
    item.payload = None
    item.available_actions = ["approve"]
    item.expires_at = None

    await send_hitl_card(bot, 123456789, item)

    call_kwargs = bot.send_message.call_args.kwargs
    markup = call_kwargs["reply_markup"]
    button = markup.inline_keyboard[0][0]
    expected = f"hitl:approve:{item_id.hex}"
    assert button.callback_data == expected


# =============================================================================
# button_callback
# =============================================================================


@pytest.mark.asyncio
async def test_button_callback_no_callback_query():
    """button_callback returns early if no callback_query."""
    update = MagicMock(spec=Update)
    update.callback_query = None
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    # Should return without error
    await button_callback(update, context)


@pytest.mark.asyncio
async def test_button_callback_bad_format():
    """button_callback rejects malformed callback data."""
    query = MagicMock()
    query.answer = AsyncMock()
    query.data = "bad:format"

    update = MagicMock(spec=Update)
    update.callback_query = query

    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    await button_callback(update, context)

    query.answer.assert_awaited()


@pytest.mark.asyncio
async def test_button_callback_invalid_uuid():
    """button_callback rejects invalid UUID hex."""
    query = MagicMock()
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.data = "hitl:approve:not_a_valid_hex"

    update = MagicMock(spec=Update)
    update.callback_query = query

    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    await button_callback(update, context)

    query.edit_message_text.assert_awaited_once()
    call_kwargs = query.edit_message_text.call_args.kwargs
    assert "Invalid item ID" in call_kwargs["text"]


@pytest.mark.asyncio
async def test_button_callback_user_not_linked():
    """button_callback rejects unlinked Telegram user."""
    item_id = uuid.uuid4()
    query = MagicMock()
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.data = f"hitl:approve:{item_id.hex}"

    effective_user = MagicMock()
    effective_user.id = 987654321

    update = MagicMock(spec=Update)
    update.callback_query = query
    update.effective_user = effective_user

    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    # Mock DB session to return None for user
    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_result)

    @asynccontextmanager
    async def mock_get_db() -> AsyncGenerator[MagicMock, None]:
        yield mock_session

    with patch("src.bot.keyboards.get_db_session", mock_get_db):
        await button_callback(update, context)

    query.edit_message_text.assert_awaited_once()
    call_kwargs = query.edit_message_text.call_args.kwargs
    assert "not linked" in call_kwargs["text"]


@pytest.mark.asyncio
async def test_button_callback_item_not_found():
    """button_callback reports item not found."""
    item_id = uuid.uuid4()
    query = MagicMock()
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.data = f"hitl:approve:{item_id.hex}"

    effective_user = MagicMock()
    effective_user.id = 987654321

    update = MagicMock(spec=Update)
    update.callback_query = query
    update.effective_user = effective_user

    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    # Mock DB: user exists (first session), item does not (second session)
    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = MagicMock(id=uuid.uuid4())

    item_result = MagicMock()
    item_result.scalar_one_or_none.return_value = None

    session_calls = 0

    @asynccontextmanager
    async def mock_get_db() -> AsyncGenerator[MagicMock, None]:
        nonlocal session_calls
        mock_session = MagicMock()
        if session_calls == 0:  # First session: user lookup
            mock_session.execute = AsyncMock(return_value=user_result)
        else:  # Second session: item lookup
            mock_session.execute = AsyncMock(return_value=item_result)
        session_calls += 1
        yield mock_session

    with patch("src.bot.keyboards.get_db_session", mock_get_db):
        await button_callback(update, context)

    query.edit_message_text.assert_awaited()
    call_text = query.edit_message_text.call_args.kwargs["text"]
    assert "not found" in call_text


@pytest.mark.asyncio
async def test_button_callback_item_not_pending():
    """button_callback rejects non-pending items."""
    item_id = uuid.uuid4()
    query = MagicMock()
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.data = f"hitl:approve:{item_id.hex}"

    effective_user = MagicMock()
    effective_user.id = 987654321

    update = MagicMock(spec=Update)
    update.callback_query = query
    update.effective_user = effective_user

    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    # Mock DB: user exists, item is resolved
    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = MagicMock(id=uuid.uuid4())

    item = MagicMock()
    item.status = "resolved"
    item.resolution = "approve"

    item_result = MagicMock()
    item_result.scalar_one_or_none.return_value = item

    session_calls = 0

    @asynccontextmanager
    async def mock_get_db() -> AsyncGenerator[MagicMock, None]:
        nonlocal session_calls
        mock_session = MagicMock()
        if session_calls == 0:  # First session: user lookup
            mock_session.execute = AsyncMock(return_value=user_result)
        else:  # Second session: item lookup
            mock_session.execute = AsyncMock(return_value=item_result)
        session_calls += 1
        yield mock_session

    with patch("src.bot.keyboards.get_db_session", mock_get_db):
        await button_callback(update, context)

    query.edit_message_text.assert_awaited()
    call_text = query.edit_message_text.call_args.kwargs["text"]
    assert "already" in call_text
    assert "resolved" in call_text


@pytest.mark.asyncio
async def test_button_callback_action_not_available():
    """button_callback rejects unavailable actions."""
    item_id = uuid.uuid4()
    query = MagicMock()
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.data = f"hitl:later:{item_id.hex}"  # 'later' not in available

    effective_user = MagicMock()
    effective_user.id = 987654321

    update = MagicMock(spec=Update)
    update.callback_query = query
    update.effective_user = effective_user

    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    # Mock DB: user exists, item pending, but 'later' not available
    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = MagicMock(id=uuid.uuid4())

    item = MagicMock()
    item.status = "pending"
    item.available_actions = ["approve", "skip"]  # 'later' missing

    item_result = MagicMock()
    item_result.scalar_one_or_none.return_value = item

    session_calls = 0

    @asynccontextmanager
    async def mock_get_db() -> AsyncGenerator[MagicMock, None]:
        nonlocal session_calls
        mock_session = MagicMock()
        if session_calls == 0:  # First session: user lookup
            mock_session.execute = AsyncMock(return_value=user_result)
        else:  # Second session: item lookup
            mock_session.execute = AsyncMock(return_value=item_result)
        session_calls += 1
        yield mock_session

    with patch("src.bot.keyboards.get_db_session", mock_get_db):
        await button_callback(update, context)

    query.edit_message_text.assert_awaited()
    call_text = query.edit_message_text.call_args.kwargs["text"]
    assert "not available" in call_text


@pytest.mark.asyncio
async def test_button_callback_successful_resolution():
    """button_callback resolves item successfully."""
    item_id = uuid.uuid4()
    query = MagicMock()
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.data = f"hitl:approve:{item_id.hex}"

    effective_user = MagicMock()
    effective_user.id = 987654321

    update = MagicMock(spec=Update)
    update.callback_query = query
    update.effective_user = effective_user

    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    # Mock DB: user exists, item pending, action available
    user_id = uuid.uuid4()
    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = MagicMock(id=user_id)

    item = MagicMock()
    item.status = "pending"
    item.available_actions = ["approve", "skip"]
    item.resolution = None
    item.resolved_by = None
    item.resolved_at = None

    item_result = MagicMock()
    item_result.scalar_one_or_none.return_value = item

    session_calls = 0

    @asynccontextmanager
    async def mock_get_db() -> AsyncGenerator[MagicMock, None]:
        nonlocal session_calls
        mock_session = MagicMock()
        if session_calls == 0:  # First session: user lookup
            mock_session.execute = AsyncMock(return_value=user_result)
        else:  # Second session: item lookup
            mock_session.execute = AsyncMock(return_value=item_result)
        session_calls += 1
        yield mock_session

    with patch("src.bot.keyboards.get_db_session", mock_get_db):
        await button_callback(update, context)

    # Check item was updated
    assert item.status == "resolved"
    assert item.resolution == "approve"
    assert item.resolved_by == user_id
    assert item.resolved_at is not None

    # Check confirmation message
    query.edit_message_text.assert_awaited()
    call_text = query.edit_message_text.call_args.kwargs["text"]
    assert "Resolved" in call_text
    assert "Approve" in call_text


@pytest.mark.asyncio
async def test_button_callback_empty_available_actions_allows_all():
    """button_callback with empty available_actions allows any action."""
    item_id = uuid.uuid4()
    query = MagicMock()
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.data = f"hitl:skip:{item_id.hex}"

    effective_user = MagicMock()
    effective_user.id = 987654321

    update = MagicMock(spec=Update)
    update.callback_query = query
    update.effective_user = effective_user

    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    # Mock DB: item with None available_actions
    user_id = uuid.uuid4()
    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = MagicMock(id=user_id)

    item = MagicMock()
    item.status = "pending"
    item.available_actions = None  # None means all allowed
    item.resolution = None
    item.resolved_by = None
    item.resolved_at = None

    item_result = MagicMock()
    item_result.scalar_one_or_none.return_value = item

    session_calls = 0

    @asynccontextmanager
    async def mock_get_db() -> AsyncGenerator[MagicMock, None]:
        nonlocal session_calls
        mock_session = MagicMock()
        if session_calls == 0:  # First session: user lookup
            mock_session.execute = AsyncMock(return_value=user_result)
        else:  # Second session: item lookup
            mock_session.execute = AsyncMock(return_value=item_result)
        session_calls += 1
        yield mock_session

    with patch("src.bot.keyboards.get_db_session", mock_get_db):
        await button_callback(update, context)

    # Should succeed
    assert item.status == "resolved"
    assert item.resolution == "skip"
