"""Email sender for Pipeline B approved outreach emails.

Uses ``aiosmtplib`` for async SMTP delivery with TLS.  Includes rate
limiting (default 50 emails/day) and graceful error handling -- never
raises, returns ``False`` on failure.

Usage::

    sender = EmailSender()
    ok = await sender.send_email("lead@example.com", "Subject", "Body")

    # After HITL approval, send all approved campaign emails:
    stats = await send_approved_emails(campaign_id, db_session)
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Rate-limit state (module-level, resets daily)
# ---------------------------------------------------------------------------

_daily_count: int = 0
_daily_date: date | None = None


def _reset_daily_counter() -> None:
    """Reset the daily counter (for testing or manual reset)."""
    global _daily_count, _daily_date  # noqa: PLW0603
    _daily_count = 0
    _daily_date = None


def _check_and_increment_rate_limit(max_per_day: int) -> bool:
    """Check the daily rate limit and increment if allowed.

    Returns ``True`` if the send is allowed, ``False`` if rate-limited.
    """
    global _daily_count, _daily_date  # noqa: PLW0603

    today = date.today()
    if _daily_date != today:
        _daily_count = 0
        _daily_date = today

    if _daily_count >= max_per_day:
        return False

    _daily_count += 1
    return True


# ---------------------------------------------------------------------------
# EmailSender
# ---------------------------------------------------------------------------


class EmailSender:
    """Async email sender with TLS and daily rate limiting.

    Reads SMTP configuration from :class:`~src.core.config.Settings` at
    init time.  All public methods are safe -- they log errors and return
    ``False`` rather than raising.

    Parameters
    ----------
    smtp_host:
        Override the SMTP host (otherwise read from Settings).
    smtp_port:
        Override the SMTP port (otherwise read from Settings).
    smtp_user:
        Override the SMTP username (otherwise read from Settings).
    smtp_password:
        Override the SMTP password (otherwise read from Settings).
    smtp_from:
        Override the default "From" address (otherwise read from Settings).
    max_emails_per_day:
        Override the daily rate limit (otherwise read from Settings).
    """

    def __init__(
        self,
        *,
        smtp_host: str | None = None,
        smtp_port: int | None = None,
        smtp_user: str | None = None,
        smtp_password: str | None = None,
        smtp_from: str | None = None,
        max_emails_per_day: int | None = None,
    ) -> None:
        # Load settings lazily to avoid import-time issues.
        try:
            from src.core.config import get_settings  # noqa: PLC0415

            settings = get_settings()
        except Exception:  # noqa: BLE001
            settings = None

        self.smtp_host: str = smtp_host if smtp_host is not None else getattr(settings, "SMTP_HOST", "")
        self.smtp_port: int = smtp_port if smtp_port is not None else getattr(settings, "SMTP_PORT", 587)
        self.smtp_user: str = smtp_user if smtp_user is not None else getattr(settings, "SMTP_USER", "")
        self.smtp_password: str = smtp_password if smtp_password is not None else getattr(settings, "SMTP_PASSWORD", "")
        self.smtp_from: str = smtp_from if smtp_from is not None else getattr(settings, "SMTP_FROM", "")
        self.max_emails_per_day: int = (
            max_emails_per_day if max_emails_per_day is not None else getattr(settings, "MAX_EMAILS_PER_DAY", 50)
        )

    @property
    def is_configured(self) -> bool:
        """Return ``True`` if SMTP host and from address are set."""
        return bool(self.smtp_host) and bool(self.smtp_from)

    async def send_email(
        self,
        to: str,
        subject: str,
        body: str,
        from_addr: str | None = None,
    ) -> bool:
        """Send a single email via SMTP with TLS.

        Parameters
        ----------
        to:
            Recipient email address.
        subject:
            Email subject line.
        body:
            Email body (plain text).
        from_addr:
            Override the sender address for this email.  Falls back to
            ``self.smtp_from``.

        Returns
        -------
        bool
            ``True`` if the email was sent successfully, ``False`` on any
            error (logged but never raised).
        """
        sender = from_addr or self.smtp_from

        if not self.smtp_host:
            logger.warning("email_send_skipped", reason="SMTP_HOST not configured")
            return False

        if not sender:
            logger.warning("email_send_skipped", reason="No from address configured")
            return False

        if not to:
            logger.warning("email_send_skipped", reason="No recipient address")
            return False

        # Rate limit check.
        if not _check_and_increment_rate_limit(self.max_emails_per_day):
            logger.warning(
                "email_rate_limited",
                max_per_day=self.max_emails_per_day,
                to=to,
            )
            return False

        try:
            import aiosmtplib  # noqa: PLC0415

            msg = MIMEMultipart()
            msg["From"] = sender
            msg["To"] = to
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain", "utf-8"))

            await aiosmtplib.send(
                msg,
                hostname=self.smtp_host,
                port=self.smtp_port,
                username=self.smtp_user or None,
                password=self.smtp_password or None,
                start_tls=True,
            )

            logger.info("email_sent", to=to, subject=subject)
            return True

        except Exception as exc:  # noqa: BLE001
            logger.error(
                "email_send_failed",
                to=to,
                subject=subject,
                error=str(exc),
                exc_info=True,
            )
            return False


# ---------------------------------------------------------------------------
# Bulk send for approved campaign emails
# ---------------------------------------------------------------------------


async def send_approved_emails(
    campaign_id: str | uuid.UUID,
    db_session: Any,
) -> dict[str, int]:
    """Send all approved emails for a campaign.

    Queries ``CampaignLead`` records with ``status='approved'`` for the
    given campaign, sends each via :class:`EmailSender`, and updates
    status to ``'sent'`` or ``'failed'``.

    Parameters
    ----------
    campaign_id:
        UUID of the :class:`EmailCampaign`.
    db_session:
        An async SQLAlchemy session (``AsyncSession``).

    Returns
    -------
    dict
        ``{"sent": N, "failed": M, "rate_limited": R}`` counts.
    """
    from sqlalchemy import select, update  # noqa: PLC0415

    from src.core.models import CampaignLead, Lead  # noqa: PLC0415

    sender = EmailSender()
    stats: dict[str, int] = {"sent": 0, "failed": 0, "rate_limited": 0}

    if not sender.is_configured:
        logger.warning("send_approved_emails_skipped", reason="SMTP not configured")
        return stats

    # Fetch approved campaign leads with their lead data.
    result = await db_session.execute(
        select(CampaignLead, Lead)
        .join(Lead, CampaignLead.lead_id == Lead.id)
        .where(
            CampaignLead.campaign_id == campaign_id,
            CampaignLead.status == "approved",
        )
    )
    rows = result.all()

    for campaign_lead, lead in rows:
        if not lead.email:
            logger.warning("send_approved_email_no_address", lead_id=str(lead.id))
            stats["failed"] += 1
            continue

        ok = await sender.send_email(
            to=lead.email,
            subject=campaign_lead.personalized_subject or "",
            body=campaign_lead.personalized_body or "",
        )

        if ok:
            await db_session.execute(
                update(CampaignLead)
                .where(
                    CampaignLead.campaign_id == campaign_lead.campaign_id,
                    CampaignLead.lead_id == campaign_lead.lead_id,
                )
                .values(status="sent", sent_at=datetime.now(UTC))
            )
            stats["sent"] += 1
        else:
            # Distinguish rate-limited vs failed.
            # If the sender returned False due to rate limit the counter
            # was not incremented, so remaining emails will also fail.
            await db_session.execute(
                update(CampaignLead)
                .where(
                    CampaignLead.campaign_id == campaign_lead.campaign_id,
                    CampaignLead.lead_id == campaign_lead.lead_id,
                )
                .values(status="failed")
            )
            stats["failed"] += 1

    await db_session.commit()

    logger.info(
        "send_approved_emails_complete",
        campaign_id=str(campaign_id),
        **stats,
    )
    return stats
