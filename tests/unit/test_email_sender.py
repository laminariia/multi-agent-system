"""Unit tests for the EmailSender and send_approved_emails."""

from __future__ import annotations

import uuid
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.enrichment.email_sender import (
    EmailSender,
    _check_and_increment_rate_limit,
    _reset_daily_counter,
    send_approved_emails,
)

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    """Reset the module-level daily counter before each test."""
    _reset_daily_counter()
    yield
    _reset_daily_counter()


def _make_sender(**overrides):
    """Create an EmailSender with test defaults, bypassing Settings."""
    defaults = {
        "smtp_host": "smtp.test.com",
        "smtp_port": 587,
        "smtp_user": "user@test.com",
        "smtp_password": "secret",  # noqa: S106
        "smtp_from": "from@test.com",
        "max_emails_per_day": 50,
    }
    defaults.update(overrides)
    with patch("src.core.config.get_settings", side_effect=Exception("no settings")):
        return EmailSender(**defaults)


# ============================================================================
# EmailSender.__init__ tests
# ============================================================================


def test_email_sender_init_with_explicit_config():
    """EmailSender stores explicit config values."""
    sender = _make_sender(smtp_host="mail.example.com", smtp_port=465)
    assert sender.smtp_host == "mail.example.com"
    assert sender.smtp_port == 465
    assert sender.smtp_user == "user@test.com"
    assert sender.smtp_password == "secret"  # noqa: S105
    assert sender.smtp_from == "from@test.com"
    assert sender.max_emails_per_day == 50


def test_email_sender_init_from_settings():
    """EmailSender reads config from Settings when no overrides given."""
    mock_settings = MagicMock()
    mock_settings.SMTP_HOST = "settings-host.com"
    mock_settings.SMTP_PORT = 465
    mock_settings.SMTP_USER = "settings-user"
    mock_settings.SMTP_PASSWORD = "settings-pass"  # noqa: S105
    mock_settings.SMTP_FROM = "settings@example.com"
    mock_settings.MAX_EMAILS_PER_DAY = 100

    with patch("src.core.config.get_settings", return_value=mock_settings):
        sender = EmailSender()

    assert sender.smtp_host == "settings-host.com"
    assert sender.smtp_port == 465
    assert sender.smtp_user == "settings-user"
    assert sender.smtp_password == "settings-pass"  # noqa: S105
    assert sender.smtp_from == "settings@example.com"
    assert sender.max_emails_per_day == 100


def test_email_sender_init_settings_fallback():
    """EmailSender gracefully handles Settings import failure."""
    with patch("src.core.config.get_settings", side_effect=Exception("boom")):
        sender = EmailSender()

    # Falls back to empty/default values.
    assert sender.smtp_host == ""
    assert sender.smtp_port == 587
    assert sender.smtp_from == ""
    assert sender.max_emails_per_day == 50


# ============================================================================
# is_configured property
# ============================================================================


def test_is_configured_true():
    """is_configured returns True when host and from are set."""
    sender = _make_sender()
    assert sender.is_configured is True


def test_is_configured_false_no_host():
    """is_configured returns False when SMTP host is empty."""
    sender = _make_sender(smtp_host="")
    assert sender.is_configured is False


def test_is_configured_false_no_from():
    """is_configured returns False when from address is empty."""
    sender = _make_sender(smtp_from="")
    assert sender.is_configured is False


# ============================================================================
# send_email tests
# ============================================================================


@pytest.mark.asyncio
async def test_send_email_success():
    """send_email returns True on successful SMTP send."""
    sender = _make_sender()

    with patch("aiosmtplib.send", new_callable=AsyncMock) as mock_send:
        result = await sender.send_email(
            to="recipient@example.com",
            subject="Test Subject",
            body="Test body content",
        )

    assert result is True
    mock_send.assert_awaited_once()
    # Verify the call kwargs.
    call_kwargs = mock_send.call_args
    assert call_kwargs.kwargs["hostname"] == "smtp.test.com"
    assert call_kwargs.kwargs["port"] == 587
    assert call_kwargs.kwargs["start_tls"] is True


@pytest.mark.asyncio
async def test_send_email_with_custom_from():
    """send_email uses the custom from_addr when provided."""
    sender = _make_sender()

    with patch("aiosmtplib.send", new_callable=AsyncMock) as mock_send:
        result = await sender.send_email(
            to="recipient@example.com",
            subject="Test",
            body="Body",
            from_addr="custom@sender.com",
        )

    assert result is True
    # Verify the MIMEMultipart message has the custom From.
    msg = mock_send.call_args.args[0]
    assert msg["From"] == "custom@sender.com"


@pytest.mark.asyncio
async def test_send_email_failure_returns_false():
    """send_email returns False when SMTP raises an exception."""
    sender = _make_sender()

    with patch("aiosmtplib.send", new_callable=AsyncMock, side_effect=ConnectionRefusedError("refused")):
        result = await sender.send_email(
            to="recipient@example.com",
            subject="Test",
            body="Body",
        )

    assert result is False


@pytest.mark.asyncio
async def test_send_email_no_smtp_host():
    """send_email returns False when SMTP host is not configured."""
    sender = _make_sender(smtp_host="")
    result = await sender.send_email(
        to="recipient@example.com",
        subject="Test",
        body="Body",
    )
    assert result is False


@pytest.mark.asyncio
async def test_send_email_no_from_address():
    """send_email returns False when no from address is available."""
    sender = _make_sender(smtp_from="")
    result = await sender.send_email(
        to="recipient@example.com",
        subject="Test",
        body="Body",
    )
    assert result is False


@pytest.mark.asyncio
async def test_send_email_no_recipient():
    """send_email returns False when recipient is empty."""
    sender = _make_sender()
    result = await sender.send_email(to="", subject="Test", body="Body")
    assert result is False


# ============================================================================
# Rate limiting tests
# ============================================================================


@pytest.mark.asyncio
async def test_rate_limiting_enforced():
    """send_email enforces the daily rate limit."""
    sender = _make_sender(max_emails_per_day=3)

    with patch("aiosmtplib.send", new_callable=AsyncMock):
        r1 = await sender.send_email("a@x.com", "S", "B")
        r2 = await sender.send_email("b@x.com", "S", "B")
        r3 = await sender.send_email("c@x.com", "S", "B")
        r4 = await sender.send_email("d@x.com", "S", "B")

    assert r1 is True
    assert r2 is True
    assert r3 is True
    assert r4 is False  # Rate limited


def test_rate_limit_check_and_increment():
    """_check_and_increment_rate_limit counts correctly."""
    assert _check_and_increment_rate_limit(2) is True
    assert _check_and_increment_rate_limit(2) is True
    assert _check_and_increment_rate_limit(2) is False


def test_rate_limit_resets_on_new_day():
    """Daily counter resets when the date changes."""
    # Fill up the counter.
    _check_and_increment_rate_limit(1)
    assert _check_and_increment_rate_limit(1) is False

    # Simulate day change by patching date.today.
    import src.enrichment.email_sender as mod

    mod._daily_date = date(2020, 1, 1)  # Old date

    # Now it should be allowed again (today != 2020-01-01).
    assert _check_and_increment_rate_limit(1) is True


def test_reset_daily_counter():
    """_reset_daily_counter clears state."""
    _check_and_increment_rate_limit(10)
    _check_and_increment_rate_limit(10)
    _reset_daily_counter()
    # After reset, should be allowed.
    assert _check_and_increment_rate_limit(1) is True


# ============================================================================
# send_approved_emails tests
# ============================================================================


@pytest.mark.asyncio
async def test_send_approved_emails_success():
    """send_approved_emails sends approved emails and updates status."""
    campaign_id = uuid.uuid4()

    # Mock lead and campaign_lead objects.
    mock_lead = MagicMock()
    mock_lead.id = uuid.uuid4()
    mock_lead.email = "lead@business.com"

    mock_cl = MagicMock()
    mock_cl.campaign_id = campaign_id
    mock_cl.lead_id = mock_lead.id
    mock_cl.personalized_subject = "Website for Your Biz"
    mock_cl.personalized_body = "Hi, we build websites..."
    mock_cl.status = "approved"

    # Mock DB session -- first execute returns query results,
    # subsequent calls are update statements.
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = [(mock_cl, mock_lead)]
    # Note: warmup select(WarmupRecord) raises ArgumentError (dataclass, not ORM)
    # so the warmup execute() is never called. Side-effect starts from main query.
    mock_session.execute = AsyncMock(side_effect=[mock_result, MagicMock(), MagicMock()])
    mock_session.commit = AsyncMock()

    sender_instance = _make_sender()

    with (
        patch("src.enrichment.email_sender.EmailSender", return_value=sender_instance),
        patch("aiosmtplib.send", new_callable=AsyncMock),
        patch("src.enrichment.suppression.SuppressionList") as mock_sl_cls,
    ):
        mock_sl_cls.return_value.is_suppressed = AsyncMock(return_value=False)
        stats = await send_approved_emails(campaign_id, mock_session)

    assert stats["sent"] == 1
    assert stats["failed"] == 0


@pytest.mark.asyncio
async def test_send_approved_emails_smtp_not_configured():
    """send_approved_emails returns empty stats when SMTP is not configured."""
    sender = _make_sender(smtp_host="")

    with patch("src.enrichment.email_sender.EmailSender", return_value=sender):
        stats = await send_approved_emails(uuid.uuid4(), AsyncMock())

    assert stats == {"sent": 0, "failed": 0, "rate_limited": 0, "bounced": 0, "suppressed": 0}


@pytest.mark.asyncio
async def test_send_approved_emails_lead_without_email():
    """send_approved_emails counts leads without email as failed."""
    campaign_id = uuid.uuid4()

    mock_lead = MagicMock()
    mock_lead.id = uuid.uuid4()
    mock_lead.email = None  # No email

    mock_cl = MagicMock()
    mock_cl.campaign_id = campaign_id
    mock_cl.lead_id = mock_lead.id
    mock_cl.status = "approved"

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = [(mock_cl, mock_lead)]
    # Warmup select(WarmupRecord) raises before execute (dataclass), skip in side_effect.
    mock_session.execute = AsyncMock(side_effect=[mock_result, MagicMock()])
    mock_session.commit = AsyncMock()

    sender = _make_sender()
    with (
        patch("src.enrichment.email_sender.EmailSender", return_value=sender),
        patch("src.enrichment.suppression.SuppressionList") as mock_sl_cls,
    ):
        mock_sl_cls.return_value.is_suppressed = AsyncMock(return_value=False)
        stats = await send_approved_emails(campaign_id, mock_session)

    assert stats["failed"] == 1
    assert stats["sent"] == 0


@pytest.mark.asyncio
async def test_send_approved_emails_empty_campaign():
    """send_approved_emails handles empty campaign (no approved leads)."""
    campaign_id = uuid.uuid4()

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = []  # No approved leads
    mock_warmup_result = MagicMock()
    mock_warmup_result.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(side_effect=[mock_warmup_result, mock_result, MagicMock()])
    mock_session.commit = AsyncMock()

    sender = _make_sender()
    with (
        patch("src.enrichment.email_sender.EmailSender", return_value=sender),
        patch("src.enrichment.suppression.SuppressionList") as mock_sl_cls,
    ):
        mock_sl_cls.return_value.is_suppressed = AsyncMock(return_value=False)
        stats = await send_approved_emails(campaign_id, mock_session)

    assert stats == {"sent": 0, "failed": 0, "rate_limited": 0, "bounced": 0, "suppressed": 0}


# ============================================================================
# Settings integration tests
# ============================================================================


def test_settings_has_smtp_fields():
    """Settings class includes all SMTP configuration fields."""
    from src.core.config import Settings

    fields = Settings.model_fields
    assert "SMTP_HOST" in fields
    assert "SMTP_PORT" in fields
    assert "SMTP_USER" in fields
    assert "SMTP_PASSWORD" in fields
    assert "SMTP_FROM" in fields
    assert "MAX_EMAILS_PER_DAY" in fields


def test_settings_smtp_defaults():
    """Settings SMTP fields have correct default values."""
    from src.core.config import Settings

    assert Settings.model_fields["SMTP_HOST"].default == ""
    assert Settings.model_fields["SMTP_PORT"].default == 587
    assert Settings.model_fields["SMTP_USER"].default == ""
    assert Settings.model_fields["SMTP_PASSWORD"].default == ""
    assert Settings.model_fields["SMTP_FROM"].default == ""
    assert Settings.model_fields["MAX_EMAILS_PER_DAY"].default == 50


def test_env_example_has_smtp_entries():
    """The .env.example file includes SMTP configuration entries."""
    import pathlib

    env_example = pathlib.Path(__file__).resolve().parents[2] / ".env.example"
    content = env_example.read_text(encoding="utf-8")

    assert "SMTP_HOST" in content
    assert "SMTP_PORT" in content
    assert "SMTP_USER" in content
    assert "SMTP_PASSWORD" in content
    assert "SMTP_FROM" in content
    assert "MAX_EMAILS_PER_DAY" in content
