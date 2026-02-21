"""Extended unit tests for email_sender module.

Tests:
- HTML email generation (multipart with text + html)
- Stagger timing between sends
- Daily rate limit enforcement
- Unsubscribe headers
- send_approved_emails updates campaign stats
- Bounce code handling
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.enrichment.email_sender import (
    HARD_BOUNCE_CODES,
    SOFT_BOUNCE_CODES,
    EmailSender,
    _check_and_increment_rate_limit,
    _classify_bounce,
    _reset_daily_counter,
    send_approved_emails,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    """Reset the module-level daily rate limit counter before each test."""
    _reset_daily_counter()
    yield
    _reset_daily_counter()


def _make_sender(**overrides: Any) -> EmailSender:
    """Create an EmailSender with test SMTP config."""
    defaults = {
        "smtp_host": "smtp.test.com",
        "smtp_port": 587,
        "smtp_user": "test@test.com",
        "smtp_password": "secret",
        "smtp_from": "noreply@test.com",
        "max_emails_per_day": 50,
    }
    defaults.update(overrides)
    with patch("src.core.config.get_settings", side_effect=Exception("no settings")):
        return EmailSender(**defaults)


# ---------------------------------------------------------------------------
# Tests: is_configured
# ---------------------------------------------------------------------------


async def test_sender_is_configured_when_host_and_from_set():
    """EmailSender.is_configured returns True when SMTP host and from are set."""
    sender = _make_sender()
    assert sender.is_configured is True


async def test_sender_not_configured_without_host():
    """EmailSender.is_configured returns False without SMTP host."""
    sender = _make_sender(smtp_host="")
    assert sender.is_configured is False


async def test_sender_not_configured_without_from():
    """EmailSender.is_configured returns False without from address."""
    sender = _make_sender(smtp_from="")
    assert sender.is_configured is False


# ---------------------------------------------------------------------------
# Tests: send_email basic
# ---------------------------------------------------------------------------


async def test_send_email_success():
    """send_email returns True on successful SMTP send."""
    sender = _make_sender()
    mock_send = AsyncMock(return_value={})

    with patch("aiosmtplib.send", mock_send):
        result = await sender.send_email("lead@example.com", "Test Subject", "Test Body")

    assert result is True
    mock_send.assert_awaited_once()


async def test_send_email_failure_returns_false():
    """send_email returns False on SMTP error (never raises)."""
    sender = _make_sender()
    mock_send = AsyncMock(side_effect=Exception("SMTP connection refused"))

    with patch("aiosmtplib.send", mock_send):
        result = await sender.send_email("lead@example.com", "Subject", "Body")

    assert result is False


async def test_send_email_no_recipient_returns_false():
    """send_email returns False when recipient is empty."""
    sender = _make_sender()
    result = await sender.send_email("", "Subject", "Body")
    assert result is False


async def test_send_email_no_host_returns_false():
    """send_email returns False when SMTP host is not configured."""
    sender = _make_sender(smtp_host="")
    result = await sender.send_email("lead@example.com", "Subject", "Body")
    assert result is False


async def test_send_email_no_from_returns_false():
    """send_email returns False when from address is not configured."""
    sender = _make_sender(smtp_from="")
    result = await sender.send_email("lead@example.com", "Subject", "Body")
    assert result is False


# ---------------------------------------------------------------------------
# Tests: MIMEMultipart message construction
# ---------------------------------------------------------------------------


async def test_send_email_constructs_mime_message():
    """send_email creates a MIMEMultipart message with correct headers."""
    sender = _make_sender()
    captured_msg = None

    async def capture_send(msg, **kwargs):
        nonlocal captured_msg
        captured_msg = msg

    with patch("aiosmtplib.send", side_effect=capture_send):
        await sender.send_email("lead@example.com", "Test Subject", "Test Body")

    assert captured_msg is not None
    assert captured_msg["To"] == "lead@example.com"
    assert captured_msg["Subject"] == "Test Subject"
    assert captured_msg["From"] == "noreply@test.com"


async def test_send_email_custom_from_addr():
    """send_email uses custom from_addr when provided."""
    sender = _make_sender()
    captured_msg = None

    async def capture_send(msg, **kwargs):
        nonlocal captured_msg
        captured_msg = msg

    with patch("aiosmtplib.send", side_effect=capture_send):
        await sender.send_email(
            "lead@example.com", "Subject", "Body",
            from_addr="custom@example.com",
        )

    assert captured_msg["From"] == "custom@example.com"


# ---------------------------------------------------------------------------
# Tests: Unsubscribe headers
# ---------------------------------------------------------------------------


async def test_send_email_has_unsubscribe_headers():
    """send_email includes List-Unsubscribe headers (RFC 8058)."""
    sender = _make_sender()
    captured_msg = None

    async def capture_send(msg, **kwargs):
        nonlocal captured_msg
        captured_msg = msg

    with patch("aiosmtplib.send", side_effect=capture_send):
        await sender.send_email("lead@example.com", "Subject", "Body")

    assert captured_msg["List-Unsubscribe"] is not None
    assert "unsubscribe" in captured_msg["List-Unsubscribe"].lower()
    assert captured_msg["List-Unsubscribe-Post"] is not None


# ---------------------------------------------------------------------------
# Tests: HTML email
# ---------------------------------------------------------------------------


async def test_send_email_includes_html_when_enabled():
    """send_email generates multipart with HTML part when html_enabled."""
    sender = _make_sender()
    assert sender.html_enabled is True
    captured_msg = None

    async def capture_send(msg, **kwargs):
        nonlocal captured_msg
        captured_msg = msg

    with patch("aiosmtplib.send", side_effect=capture_send):
        await sender.send_email("lead@example.com", "Subject", "Hello World")

    # Should have 2 parts: plain + html
    payloads = captured_msg.get_payload()
    assert len(payloads) == 2
    plain_part = payloads[0]
    html_part = payloads[1]
    assert plain_part.get_content_type() == "text/plain"
    assert html_part.get_content_type() == "text/html"
    assert "Hello World" in html_part.get_payload(decode=True).decode()


# ---------------------------------------------------------------------------
# Tests: Bounce classification
# ---------------------------------------------------------------------------


async def test_classify_bounce_hard_bounce_codes():
    """_classify_bounce returns 'hard_bounce' for 550-553 codes."""
    import aiosmtplib

    for code in HARD_BOUNCE_CODES:
        exc = aiosmtplib.SMTPResponseException(code, f"Error {code}")
        assert _classify_bounce(exc) == "hard_bounce"


async def test_classify_bounce_soft_bounce_codes():
    """_classify_bounce returns 'soft_bounce' for 421/450-452 codes."""
    import aiosmtplib

    for code in SOFT_BOUNCE_CODES:
        exc = aiosmtplib.SMTPResponseException(code, f"Error {code}")
        assert _classify_bounce(exc) == "soft_bounce"


async def test_classify_bounce_unknown_code():
    """_classify_bounce returns None for non-bounce SMTP errors."""
    import aiosmtplib

    exc = aiosmtplib.SMTPResponseException(500, "Internal error")
    assert _classify_bounce(exc) is None


async def test_classify_bounce_non_smtp_exception():
    """_classify_bounce returns None for non-SMTP exceptions."""
    exc = ConnectionError("Connection refused")
    assert _classify_bounce(exc) is None


async def test_send_email_returns_hard_bounce():
    """send_email returns 'hard_bounce' string on 550 SMTP error."""
    import aiosmtplib

    sender = _make_sender()
    exc = aiosmtplib.SMTPResponseException(550, "Mailbox not found")

    with patch("aiosmtplib.send", side_effect=exc):
        result = await sender.send_email("bad@example.com", "Subject", "Body")

    assert result == "hard_bounce"


async def test_send_email_returns_soft_bounce():
    """send_email returns 'soft_bounce' string on 450 SMTP error."""
    import aiosmtplib

    sender = _make_sender()
    exc = aiosmtplib.SMTPResponseException(450, "Try again later")

    with patch("aiosmtplib.send", side_effect=exc):
        result = await sender.send_email("temp@example.com", "Subject", "Body")

    assert result == "soft_bounce"


# ---------------------------------------------------------------------------
# Tests: Daily rate limit
# ---------------------------------------------------------------------------


async def test_rate_limit_allows_within_limit():
    """Rate limit allows sends within the daily limit."""
    sender = _make_sender(max_emails_per_day=3)
    mock_send = AsyncMock(return_value={})

    with patch("aiosmtplib.send", mock_send):
        r1 = await sender.send_email("a@test.com", "S1", "B1")
        r2 = await sender.send_email("b@test.com", "S2", "B2")
        r3 = await sender.send_email("c@test.com", "S3", "B3")

    assert r1 is True
    assert r2 is True
    assert r3 is True
    assert mock_send.await_count == 3


async def test_rate_limit_blocks_after_limit():
    """Rate limit blocks sends after daily limit is reached."""
    sender = _make_sender(max_emails_per_day=2)
    mock_send = AsyncMock(return_value={})

    with patch("aiosmtplib.send", mock_send):
        r1 = await sender.send_email("a@test.com", "S1", "B1")
        r2 = await sender.send_email("b@test.com", "S2", "B2")
        r3 = await sender.send_email("c@test.com", "S3", "B3")  # should be rate limited

    assert r1 is True
    assert r2 is True
    assert r3 is False  # rate limited
    assert mock_send.await_count == 2


async def test_rate_limit_check_and_increment():
    """_check_and_increment_rate_limit works correctly."""
    assert _check_and_increment_rate_limit(3) is True
    assert _check_and_increment_rate_limit(3) is True
    assert _check_and_increment_rate_limit(3) is True
    assert _check_and_increment_rate_limit(3) is False  # limit reached


async def test_rate_limit_resets_after_reset():
    """_reset_daily_counter allows sends again."""
    _check_and_increment_rate_limit(1)
    assert _check_and_increment_rate_limit(1) is False  # limit reached
    _reset_daily_counter()
    assert _check_and_increment_rate_limit(1) is True  # reset allows again


# ---------------------------------------------------------------------------
# Tests: send_approved_emails
# ---------------------------------------------------------------------------


_IS_CONFIGURED = "src.enrichment.email_sender.EmailSender.is_configured"
_SEND_EMAIL = "src.enrichment.email_sender.EmailSender.send_email"


def _prop_true():
    return property(lambda self: True)


def _prop_false():
    return property(lambda self: False)


async def test_send_approved_emails_returns_stats():
    """send_approved_emails returns sent/failed/rate_limited counts."""
    campaign_id = uuid.uuid4()
    lead_id = uuid.uuid4()

    # Create mock campaign lead and lead
    mock_cl = MagicMock()
    mock_cl.campaign_id = campaign_id
    mock_cl.lead_id = lead_id
    mock_cl.personalized_subject = "Subject for Lead"
    mock_cl.personalized_body = "Body for Lead"

    mock_lead = MagicMock()
    mock_lead.id = lead_id
    mock_lead.email = "lead@example.com"

    # Mock DB session
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = [(mock_cl, mock_lead)]
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.commit = AsyncMock()

    with patch(_IS_CONFIGURED, new_callable=_prop_true), \
         patch(_SEND_EMAIL, new_callable=AsyncMock, return_value=True):
        stats = await send_approved_emails(campaign_id, mock_session)

    assert stats["sent"] == 1
    assert stats["failed"] == 0


async def test_send_approved_emails_unconfigured_smtp():
    """send_approved_emails returns empty stats when SMTP not configured."""
    mock_session = AsyncMock()

    with patch(_IS_CONFIGURED, new_callable=_prop_false):
        stats = await send_approved_emails(uuid.uuid4(), mock_session)

    assert stats == {"sent": 0, "failed": 0, "rate_limited": 0, "bounced": 0}


async def test_send_approved_emails_no_email_on_lead():
    """send_approved_emails counts lead without email as failed."""
    campaign_id = uuid.uuid4()
    lead_id = uuid.uuid4()

    mock_cl = MagicMock()
    mock_cl.campaign_id = campaign_id
    mock_cl.lead_id = lead_id
    mock_cl.personalized_subject = "Subject"
    mock_cl.personalized_body = "Body"

    mock_lead = MagicMock()
    mock_lead.id = lead_id
    mock_lead.email = None  # No email

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = [(mock_cl, mock_lead)]
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.commit = AsyncMock()

    with patch(_IS_CONFIGURED, new_callable=_prop_true), \
         patch(_SEND_EMAIL, new_callable=AsyncMock):
        stats = await send_approved_emails(campaign_id, mock_session)

    assert stats["failed"] == 1
    assert stats["sent"] == 0


async def test_send_approved_emails_smtp_failure_counts_as_failed():
    """send_approved_emails counts SMTP failures correctly."""
    campaign_id = uuid.uuid4()
    lead_id = uuid.uuid4()

    mock_cl = MagicMock()
    mock_cl.campaign_id = campaign_id
    mock_cl.lead_id = lead_id
    mock_cl.personalized_subject = "Subject"
    mock_cl.personalized_body = "Body"

    mock_lead = MagicMock()
    mock_lead.id = lead_id
    mock_lead.email = "lead@test.com"

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = [(mock_cl, mock_lead)]
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.commit = AsyncMock()

    with patch(_IS_CONFIGURED, new_callable=_prop_true), \
         patch(_SEND_EMAIL, new_callable=AsyncMock, return_value=False):
        stats = await send_approved_emails(campaign_id, mock_session)

    assert stats["failed"] == 1
    assert stats["sent"] == 0
