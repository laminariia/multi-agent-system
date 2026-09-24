"""Unit tests for email bounce detection and enforcement.

Tests:
- Hard bounces (550-553): auto-add to suppression list
- Soft bounces (421, 450-452): retry with exponential backoff (max 3)
- Bounce stats tracking for warmup integration
- send_approved_emails integration with suppression on hard bounce
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
    _reset_daily_counter,
    send_approved_emails,
)

# ============================================================================
# Fixtures
# ============================================================================


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
        "smtp_password": "secret",  # noqa: S106
        "smtp_from": "noreply@test.com",
        "max_emails_per_day": 50,
    }
    defaults.update(overrides)
    with patch("src.core.config.get_settings", side_effect=Exception("no settings")):
        return EmailSender(**defaults)


def _make_smtp_exc(code: int, message: str = "SMTP Error"):
    """Create an aiosmtplib.SMTPResponseException for testing."""
    import aiosmtplib

    return aiosmtplib.SMTPResponseException(code, message)


# ============================================================================
# Tests: Hard bounce auto-suppression in send_email
# ============================================================================


@pytest.mark.asyncio
async def test_hard_bounce_auto_suppresses_email():
    """send_email auto-adds recipient to suppression list on hard bounce (550)."""
    sender = _make_sender()
    exc = _make_smtp_exc(550, "Mailbox not found")

    mock_suppress = AsyncMock()

    with (
        patch("aiosmtplib.send", side_effect=exc),
        patch("src.enrichment.email_sender._auto_suppress_hard_bounce", mock_suppress),
    ):
        result = await sender.send_email("bad@example.com", "Subject", "Body")

    assert result == "hard_bounce"
    mock_suppress.assert_awaited_once_with("bad@example.com")


@pytest.mark.asyncio
async def test_hard_bounce_suppression_called_for_all_hard_codes():
    """Auto-suppression triggers for all hard bounce codes (550, 551, 552, 553)."""
    sender = _make_sender()
    mock_suppress = AsyncMock()

    for code in HARD_BOUNCE_CODES:
        _reset_daily_counter()
        exc = _make_smtp_exc(code, f"Hard bounce {code}")

        with (
            patch("aiosmtplib.send", side_effect=exc),
            patch(
                "src.enrichment.email_sender._auto_suppress_hard_bounce",
                mock_suppress,
            ),
        ):
            result = await sender.send_email(f"bad{code}@example.com", "Subject", "Body")

        assert result == "hard_bounce", f"Expected hard_bounce for code {code}"
        mock_suppress.assert_awaited_with(f"bad{code}@example.com")


@pytest.mark.asyncio
async def test_soft_bounce_does_not_trigger_suppression():
    """Soft bounces (421, 450-452) do NOT auto-suppress -- they are retried."""
    sender = _make_sender()
    mock_suppress = AsyncMock()

    for code in SOFT_BOUNCE_CODES:
        _reset_daily_counter()
        exc = _make_smtp_exc(code, f"Soft bounce {code}")

        with (
            patch("aiosmtplib.send", side_effect=exc),
            patch(
                "src.enrichment.email_sender._auto_suppress_hard_bounce",
                mock_suppress,
            ),
        ):
            result = await sender.send_email(f"temp{code}@example.com", "Subject", "Body")

        assert result == "soft_bounce", f"Expected soft_bounce for code {code}"
        mock_suppress.assert_not_awaited()
        mock_suppress.reset_mock()


@pytest.mark.asyncio
async def test_generic_failure_does_not_trigger_suppression():
    """Generic failures (ConnectionError, etc.) do NOT trigger suppression."""
    sender = _make_sender()
    mock_suppress = AsyncMock()

    with (
        patch("aiosmtplib.send", side_effect=ConnectionError("refused")),
        patch(
            "src.enrichment.email_sender._auto_suppress_hard_bounce",
            mock_suppress,
        ),
    ):
        result = await sender.send_email("ok@example.com", "Subject", "Body")

    assert result is False
    mock_suppress.assert_not_awaited()


# ============================================================================
# Tests: _auto_suppress_hard_bounce implementation
# ============================================================================


def _mock_db_session():
    """Create a mock AsyncSession with context manager support."""
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


@pytest.mark.asyncio
async def test_auto_suppress_hard_bounce_creates_suppression_entry():
    """_auto_suppress_hard_bounce adds email to suppression list via DB session."""
    from src.enrichment.email_sender import _auto_suppress_hard_bounce

    mock_session = _mock_db_session()
    mock_suppress = AsyncMock()

    with (
        patch("src.enrichment.email_sender._get_db_session", return_value=mock_session),
        patch("src.enrichment.suppression.SuppressionList.suppress", mock_suppress),
    ):
        await _auto_suppress_hard_bounce("bounced@example.com")

    mock_suppress.assert_awaited_once()


@pytest.mark.asyncio
async def test_auto_suppress_hard_bounce_uses_correct_reason():
    """_auto_suppress_hard_bounce stores reason='hard_bounce' and source='bounce_handler'."""
    from src.enrichment.email_sender import _auto_suppress_hard_bounce

    mock_session = _mock_db_session()
    mock_suppress = AsyncMock()

    with (
        patch("src.enrichment.email_sender._get_db_session", return_value=mock_session),
        patch("src.enrichment.suppression.SuppressionList.suppress", mock_suppress),
    ):
        await _auto_suppress_hard_bounce("bad@example.com")

    mock_suppress.assert_awaited_once_with(
        "bad@example.com",
        reason="hard_bounce",
        source="bounce_handler",
    )


@pytest.mark.asyncio
async def test_auto_suppress_hard_bounce_commits_session():
    """_auto_suppress_hard_bounce commits the DB session after suppression."""
    from src.enrichment.email_sender import _auto_suppress_hard_bounce

    mock_session = _mock_db_session()
    mock_suppress = AsyncMock()

    with (
        patch("src.enrichment.email_sender._get_db_session", return_value=mock_session),
        patch("src.enrichment.suppression.SuppressionList.suppress", mock_suppress),
    ):
        await _auto_suppress_hard_bounce("bad@example.com")

    mock_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_auto_suppress_hard_bounce_handles_db_error_gracefully():
    """_auto_suppress_hard_bounce logs and swallows DB errors (never raises)."""
    from src.enrichment.email_sender import _auto_suppress_hard_bounce

    with patch("src.enrichment.email_sender._get_db_session", side_effect=Exception("DB down")):
        # Should NOT raise -- fail-safe design.
        await _auto_suppress_hard_bounce("bad@example.com")


# ============================================================================
# Tests: Soft bounce retry with exponential backoff
# ============================================================================


@pytest.mark.asyncio
async def test_soft_bounce_retries_before_returning():
    """send_email retries up to 3 times on soft bounce before returning 'soft_bounce'."""
    sender = _make_sender()
    exc = _make_smtp_exc(450, "Try again later")

    with (
        patch("aiosmtplib.send", side_effect=exc) as mock_send,
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        result = await sender.send_email("temp@example.com", "Subject", "Body")

    assert result == "soft_bounce"
    # 1 initial + 3 retries = 4 total calls
    assert mock_send.await_count == 4


@pytest.mark.asyncio
async def test_soft_bounce_succeeds_on_retry():
    """send_email returns True if SMTP succeeds on a retry after soft bounce."""
    sender = _make_sender()
    exc = _make_smtp_exc(421, "Service not available")

    # First 2 calls raise soft bounce, third call succeeds
    with (
        patch(
            "aiosmtplib.send",
            side_effect=[exc, exc, None],
        ) as mock_send,
        patch("asyncio.sleep", new_callable=AsyncMock),
    ):
        result = await sender.send_email("temp@example.com", "Subject", "Body")

    assert result is True
    assert mock_send.await_count == 3  # 1 initial + 2 retries, then success


@pytest.mark.asyncio
async def test_soft_bounce_exponential_backoff_delays():
    """Soft bounce retries use exponential backoff (2s, 4s, 8s)."""
    sender = _make_sender()
    exc = _make_smtp_exc(451, "Try again later")

    with patch("aiosmtplib.send", side_effect=exc), patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await sender.send_email("temp@example.com", "Subject", "Body")

    # Verify exponential backoff: 2^1=2, 2^2=4, 2^3=8
    assert mock_sleep.await_count == 3
    delays = [c.args[0] for c in mock_sleep.await_args_list]
    assert delays == [2, 4, 8]


@pytest.mark.asyncio
async def test_soft_bounce_all_codes_retry():
    """All soft bounce codes (421, 450, 451, 452) trigger retry logic."""
    for code in SOFT_BOUNCE_CODES:
        _reset_daily_counter()
        sender = _make_sender()
        exc = _make_smtp_exc(code, f"Soft bounce {code}")

        with patch("aiosmtplib.send", side_effect=exc) as mock_send, patch("asyncio.sleep", new_callable=AsyncMock):
            result = await sender.send_email(f"retry{code}@example.com", "Subject", "Body")

        assert result == "soft_bounce", f"Expected soft_bounce for code {code}"
        assert mock_send.await_count == 4, f"Expected 4 attempts for code {code}"


@pytest.mark.asyncio
async def test_soft_bounce_retry_does_not_recount_rate_limit():
    """Soft bounce retries do NOT consume additional rate limit quota."""
    sender = _make_sender(max_emails_per_day=2)
    exc = _make_smtp_exc(450, "Try again later")

    with patch("aiosmtplib.send", side_effect=exc), patch("asyncio.sleep", new_callable=AsyncMock):
        # First email -- soft bounce with retries (should consume 1 rate limit slot)
        r1 = await sender.send_email("a@example.com", "S", "B")
        assert r1 == "soft_bounce"

    # Second email should still be allowed (rate limit = 2)
    with patch("aiosmtplib.send", new_callable=AsyncMock):
        r2 = await sender.send_email("b@example.com", "S", "B")
        assert r2 is True


@pytest.mark.asyncio
async def test_soft_bounce_hard_bounce_on_retry_suppresses():
    """If a retry attempt gets a hard bounce, it should suppress the email."""
    sender = _make_sender()
    soft_exc = _make_smtp_exc(421, "Service not available")
    hard_exc = _make_smtp_exc(550, "Mailbox not found")

    mock_suppress = AsyncMock()

    with (
        patch("aiosmtplib.send", side_effect=[soft_exc, hard_exc]),
        patch("asyncio.sleep", new_callable=AsyncMock),
        patch("src.enrichment.email_sender._auto_suppress_hard_bounce", mock_suppress),
    ):
        result = await sender.send_email("bad@example.com", "Subject", "Body")

    assert result == "hard_bounce"
    mock_suppress.assert_awaited_once_with("bad@example.com")


# ============================================================================
# Tests: Bounce stats tracking
# ============================================================================


@pytest.mark.asyncio
async def test_bounce_stats_tracks_hard_bounces():
    """BounceStats increments hard bounce counter."""
    from src.enrichment.email_sender import BounceStats

    stats = BounceStats()
    stats.record_hard_bounce("a@test.com")
    stats.record_hard_bounce("b@test.com")

    assert stats.hard_bounce_count == 2
    assert stats.total_bounces == 2
    assert "a@test.com" in stats.hard_bounced_emails
    assert "b@test.com" in stats.hard_bounced_emails


@pytest.mark.asyncio
async def test_bounce_stats_tracks_soft_bounces():
    """BounceStats increments soft bounce counter."""
    from src.enrichment.email_sender import BounceStats

    stats = BounceStats()
    stats.record_soft_bounce("c@test.com")

    assert stats.soft_bounce_count == 1
    assert stats.total_bounces == 1


@pytest.mark.asyncio
async def test_bounce_stats_tracks_total_sent():
    """BounceStats tracks total emails sent for rate calculation."""
    from src.enrichment.email_sender import BounceStats

    stats = BounceStats()
    stats.record_sent()
    stats.record_sent()
    stats.record_sent()
    stats.record_hard_bounce("a@test.com")

    assert stats.total_sent == 3
    assert stats.hard_bounce_count == 1


@pytest.mark.asyncio
async def test_bounce_stats_bounce_rate():
    """BounceStats.bounce_rate returns correct percentage."""
    from src.enrichment.email_sender import BounceStats

    stats = BounceStats()
    # 10 sent, 1 bounced = 10% bounce rate
    for _ in range(10):
        stats.record_sent()
    stats.record_hard_bounce("x@test.com")

    assert stats.bounce_rate == pytest.approx(0.1)


@pytest.mark.asyncio
async def test_bounce_stats_bounce_rate_zero_sent():
    """BounceStats.bounce_rate returns 0.0 when nothing sent."""
    from src.enrichment.email_sender import BounceStats

    stats = BounceStats()
    assert stats.bounce_rate == 0.0


@pytest.mark.asyncio
async def test_bounce_stats_reset():
    """BounceStats.reset clears all counters."""
    from src.enrichment.email_sender import BounceStats

    stats = BounceStats()
    stats.record_sent()
    stats.record_hard_bounce("x@test.com")
    stats.record_soft_bounce("y@test.com")
    stats.reset()

    assert stats.total_sent == 0
    assert stats.hard_bounce_count == 0
    assert stats.soft_bounce_count == 0
    assert stats.total_bounces == 0
    assert len(stats.hard_bounced_emails) == 0


@pytest.mark.asyncio
async def test_bounce_stats_exceeds_warmup_threshold():
    """BounceStats.exceeds_threshold returns True when bounce rate >= 5%."""
    from src.enrichment.email_sender import BounceStats

    stats = BounceStats()
    for _ in range(20):
        stats.record_sent()
    stats.record_hard_bounce("a@test.com")  # 1/20 = 5%

    assert stats.exceeds_threshold(0.05) is True
    assert stats.exceeds_threshold(0.06) is False


@pytest.mark.asyncio
async def test_bounce_stats_as_dict():
    """BounceStats.as_dict returns a serializable summary."""
    from src.enrichment.email_sender import BounceStats

    stats = BounceStats()
    stats.record_sent()
    stats.record_sent()
    stats.record_hard_bounce("x@test.com")
    stats.record_soft_bounce("y@test.com")

    d = stats.as_dict()
    assert d["total_sent"] == 2
    assert d["hard_bounce_count"] == 1
    assert d["soft_bounce_count"] == 1
    assert d["total_bounces"] == 2
    assert "bounce_rate" in d


# ============================================================================
# Tests: Module-level bounce stats instance
# ============================================================================


@pytest.mark.asyncio
async def test_module_bounce_stats_updated_on_send():
    """Module-level bounce_stats is updated when send_email succeeds."""
    from src.enrichment import email_sender as mod

    mod._bounce_stats.reset()
    sender = _make_sender()

    with patch("aiosmtplib.send", new_callable=AsyncMock):
        await sender.send_email("ok@example.com", "Subject", "Body")

    assert mod._bounce_stats.total_sent == 1


@pytest.mark.asyncio
async def test_module_bounce_stats_updated_on_hard_bounce():
    """Module-level bounce_stats records hard bounce."""
    from src.enrichment import email_sender as mod

    mod._bounce_stats.reset()
    sender = _make_sender()
    exc = _make_smtp_exc(550, "Mailbox not found")

    with (
        patch("aiosmtplib.send", side_effect=exc),
        patch("src.enrichment.email_sender._auto_suppress_hard_bounce", AsyncMock()),
    ):
        await sender.send_email("bad@example.com", "Subject", "Body")

    assert mod._bounce_stats.hard_bounce_count == 1
    assert "bad@example.com" in mod._bounce_stats.hard_bounced_emails


@pytest.mark.asyncio
async def test_module_bounce_stats_updated_on_soft_bounce():
    """Module-level bounce_stats records soft bounce after retries exhausted."""
    from src.enrichment import email_sender as mod

    mod._bounce_stats.reset()
    sender = _make_sender()
    exc = _make_smtp_exc(450, "Try again later")

    with patch("aiosmtplib.send", side_effect=exc), patch("asyncio.sleep", new_callable=AsyncMock):
        await sender.send_email("temp@example.com", "Subject", "Body")

    assert mod._bounce_stats.soft_bounce_count == 1


# ============================================================================
# Tests: send_approved_emails with hard bounce suppression
# ============================================================================


@pytest.mark.asyncio
async def test_send_approved_emails_hard_bounce_auto_suppresses():
    """send_approved_emails auto-suppresses hard-bounced emails via SuppressionList."""
    campaign_id = uuid.uuid4()
    lead_id = uuid.uuid4()

    mock_cl = MagicMock()
    mock_cl.campaign_id = campaign_id
    mock_cl.lead_id = lead_id
    mock_cl.personalized_subject = "Subject"
    mock_cl.personalized_body = "Body"

    mock_lead = MagicMock()
    mock_lead.id = lead_id
    mock_lead.email = "hard@bounce.com"

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = [(mock_cl, mock_lead)]
    # Warmup select(WarmupRecord) raises before execute (dataclass), skip in side_effect.
    mock_session.execute = AsyncMock(side_effect=[mock_result, MagicMock(), MagicMock(), MagicMock()])
    mock_session.commit = AsyncMock()

    exc = _make_smtp_exc(550, "Mailbox not found")
    mock_suppress_fn = AsyncMock()
    configured_sender = _make_sender()

    with (
        patch("aiosmtplib.send", side_effect=exc),
        patch("asyncio.sleep", new_callable=AsyncMock),
        patch("src.enrichment.email_sender._auto_suppress_hard_bounce", mock_suppress_fn),
        patch("src.enrichment.email_sender.EmailSender", return_value=configured_sender),
        patch("src.enrichment.suppression.SuppressionList") as mock_sl_cls,
    ):
        mock_sl_cls.return_value.is_suppressed = AsyncMock(return_value=False)
        stats = await send_approved_emails(campaign_id, mock_session)

    assert stats["bounced"] == 1
    mock_suppress_fn.assert_awaited_once_with("hard@bounce.com")


@pytest.mark.asyncio
async def test_send_approved_emails_soft_bounce_retries():
    """send_approved_emails gets soft_bounce only after retries are exhausted."""
    campaign_id = uuid.uuid4()
    lead_id = uuid.uuid4()

    mock_cl = MagicMock()
    mock_cl.campaign_id = campaign_id
    mock_cl.lead_id = lead_id
    mock_cl.personalized_subject = "Subject"
    mock_cl.personalized_body = "Body"

    mock_lead = MagicMock()
    mock_lead.id = lead_id
    mock_lead.email = "soft@bounce.com"

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.all.return_value = [(mock_cl, mock_lead)]
    # Warmup select(WarmupRecord) raises before execute (dataclass), skip in side_effect.
    mock_session.execute = AsyncMock(side_effect=[mock_result, MagicMock(), MagicMock()])
    mock_session.commit = AsyncMock()

    exc = _make_smtp_exc(450, "Try again later")
    configured_sender = _make_sender()

    with (
        patch("aiosmtplib.send", side_effect=exc),
        patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        patch("src.enrichment.email_sender.EmailSender", return_value=configured_sender),
        patch("src.enrichment.suppression.SuppressionList") as mock_sl_cls,
    ):
        mock_sl_cls.return_value.is_suppressed = AsyncMock(return_value=False)
        stats = await send_approved_emails(campaign_id, mock_session)

    assert stats["bounced"] == 1
    # Exponential backoff delays: 2, 4, 8
    assert mock_sleep.await_count >= 3


# ============================================================================
# Tests: get_bounce_stats accessor
# ============================================================================


def test_get_bounce_stats_returns_module_instance():
    """get_bounce_stats returns the module-level BounceStats instance."""
    from src.enrichment.email_sender import _bounce_stats, get_bounce_stats

    result = get_bounce_stats()
    assert result is _bounce_stats
