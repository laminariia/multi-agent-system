"""Email sender for Pipeline B approved outreach emails.

Uses ``aiosmtplib`` for async SMTP delivery with TLS.  Includes rate
limiting (default 50 emails/day), HTML email support, staggered sending,
List-Unsubscribe headers (RFC 8058), and bounce detection.

Usage::

    sender = EmailSender()
    ok = await sender.send_email("lead@example.com", "Subject", "Body")

    # After HITL approval, send all approved campaign emails:
    stats = await send_approved_emails(campaign_id, db_session)
"""

from __future__ import annotations

import asyncio
import html
import random
import uuid
from datetime import UTC, date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Bounce classification
# ---------------------------------------------------------------------------

HARD_BOUNCE_CODES = {550, 551, 552, 553}
SOFT_BOUNCE_CODES = {421, 450, 451, 452}

# ---------------------------------------------------------------------------
# HTML email template
# ---------------------------------------------------------------------------

_HTML_WRAPPER = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  body {{ margin: 0; padding: 0; font-size: 16px; line-height: 1.5; color: #333; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; }}
  .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
  .footer {{ margin-top: 30px; padding-top: 15px; border-top: 1px solid #eee; font-size: 12px; color: #999; }}
</style>
</head>
<body>
<div class="container">
{body}
<div class="footer">
{footer}
</div>
</div>
</body>
</html>"""

_UNSUBSCRIBE_FOOTER_TEXT = (
    "\n\n---\nIf you no longer wish to receive these emails, "
    "reply with 'unsubscribe' or click the unsubscribe link in your email client."
)

_UNSUBSCRIBE_FOOTER_HTML = (
    '<p style="font-size:12px;color:#999;">If you no longer wish to receive these emails, '
    "reply with 'unsubscribe' or click the unsubscribe link in your email client.</p>"
)


def _plain_to_html(text_body: str) -> str:
    """Convert plain text body to simple HTML paragraphs."""
    escaped = html.escape(text_body)
    paragraphs = escaped.split("\n\n")
    html_parts = []
    for p in paragraphs:
        lines = p.replace("\n", "<br>\n")
        html_parts.append(f"<p>{lines}</p>")
    return "\n".join(html_parts)


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
        self.html_enabled: bool = getattr(settings, "EMAIL_HTML_ENABLED", True) if settings else True

    @property
    def is_configured(self) -> bool:
        """Return ``True`` if SMTP host and from address are set."""
        return bool(self.smtp_host) and bool(self.smtp_from)

    def _build_message(
        self,
        to: str,
        subject: str,
        body: str,
        from_addr: str,
    ) -> MIMEMultipart:
        """Build a MIME message with plain text, optional HTML, and unsubscribe headers."""
        msg = MIMEMultipart("alternative")
        msg["From"] = from_addr
        msg["To"] = to
        msg["Subject"] = subject

        # List-Unsubscribe headers (RFC 8058)
        msg["List-Unsubscribe"] = f"<mailto:{from_addr}?subject=unsubscribe>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

        # Plain text part (always present, with unsubscribe footer)
        plain_body = body + _UNSUBSCRIBE_FOOTER_TEXT
        msg.attach(MIMEText(plain_body, "plain", "utf-8"))

        # HTML part (if enabled)
        if self.html_enabled:
            html_body = _HTML_WRAPPER.format(
                body=_plain_to_html(body),
                footer=_UNSUBSCRIBE_FOOTER_HTML,
            )
            msg.attach(MIMEText(html_body, "html", "utf-8"))

        return msg

    async def send_email(
        self,
        to: str,
        subject: str,
        body: str,
        from_addr: str | None = None,
    ) -> bool | str:
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
        bool | str
            ``True`` if the email was sent successfully, ``False`` on generic
            failure, ``"hard_bounce"`` on permanent rejection (550-553),
            ``"soft_bounce"`` on temporary rejection (421/450-452).
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

            msg = self._build_message(to, subject, body, sender)

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
            # Detect bounce type from SMTP response codes
            bounce_type = _classify_bounce(exc)
            if bounce_type:
                logger.warning(
                    f"email_{bounce_type}",
                    to=to,
                    subject=subject,
                    smtp_code=getattr(exc, "code", None),
                    error=str(exc),
                )
                return bounce_type

            logger.error(
                "email_send_failed",
                to=to,
                subject=subject,
                error=str(exc),
                exc_info=True,
            )
            return False


def _classify_bounce(exc: Exception) -> str | None:
    """Classify an SMTP exception as hard_bounce, soft_bounce, or None."""
    try:
        import aiosmtplib  # noqa: PLC0415

        if isinstance(exc, aiosmtplib.SMTPResponseException):
            code = exc.code
            if code in HARD_BOUNCE_CODES:
                return "hard_bounce"
            if code in SOFT_BOUNCE_CODES:
                return "soft_bounce"
    except ImportError:
        pass
    return None


# ---------------------------------------------------------------------------
# Bulk send for approved campaign emails
# ---------------------------------------------------------------------------


async def send_approved_emails(
    campaign_id: str | uuid.UUID,
    db_session: Any,
) -> dict[str, int]:
    """Send all approved emails for a campaign with staggered delivery.

    Queries ``CampaignLead`` records with ``status='approved'`` for the
    given campaign, sends each via :class:`EmailSender`, and updates
    status to ``'sent'``, ``'failed'``, or ``'bounced'``.

    Includes configurable random delay between sends (EMAIL_STAGGER_MIN_SECONDS
    / EMAIL_STAGGER_MAX_SECONDS) to avoid triggering spam filters.

    Parameters
    ----------
    campaign_id:
        UUID of the :class:`EmailCampaign`.
    db_session:
        An async SQLAlchemy session (``AsyncSession``).

    Returns
    -------
    dict
        ``{"sent": N, "failed": M, "rate_limited": R, "bounced": B}`` counts.
    """
    from sqlalchemy import select, update  # noqa: PLC0415

    from src.core.models import CampaignLead, EmailCampaign, Lead  # noqa: PLC0415

    # Load stagger settings
    try:
        from src.core.config import get_settings  # noqa: PLC0415

        settings = get_settings()
        stagger_min = settings.EMAIL_STAGGER_MIN_SECONDS
        stagger_max = settings.EMAIL_STAGGER_MAX_SECONDS
    except Exception:  # noqa: BLE001
        stagger_min = 30
        stagger_max = 60

    sender = EmailSender()
    stats: dict[str, int] = {"sent": 0, "failed": 0, "rate_limited": 0, "bounced": 0}

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
    total = len(rows)

    for idx, (campaign_lead, lead) in enumerate(rows, 1):
        if not lead.email:
            logger.warning("send_approved_email_no_address", lead_id=str(lead.id))
            stats["failed"] += 1
            continue

        result_code = await sender.send_email(
            to=lead.email,
            subject=campaign_lead.personalized_subject or "",
            body=campaign_lead.personalized_body or "",
        )

        if result_code is True:
            await db_session.execute(
                update(CampaignLead)
                .where(
                    CampaignLead.campaign_id == campaign_lead.campaign_id,
                    CampaignLead.lead_id == campaign_lead.lead_id,
                )
                .values(status="sent", sent_at=datetime.now(UTC))
            )
            stats["sent"] += 1

        elif result_code == "hard_bounce":
            # Hard bounce: mark campaign lead as bounced, mark lead as no_contact
            await db_session.execute(
                update(CampaignLead)
                .where(
                    CampaignLead.campaign_id == campaign_lead.campaign_id,
                    CampaignLead.lead_id == campaign_lead.lead_id,
                )
                .values(status="bounced")
            )
            await db_session.execute(
                update(Lead)
                .where(Lead.id == campaign_lead.lead_id)
                .values(status="no_contact")
            )
            stats["bounced"] += 1

        elif result_code == "soft_bounce":
            # Soft bounce: mark as failed for retry
            await db_session.execute(
                update(CampaignLead)
                .where(
                    CampaignLead.campaign_id == campaign_lead.campaign_id,
                    CampaignLead.lead_id == campaign_lead.lead_id,
                )
                .values(status="failed")
            )
            stats["bounced"] += 1

        else:
            # Generic failure or rate limited
            await db_session.execute(
                update(CampaignLead)
                .where(
                    CampaignLead.campaign_id == campaign_lead.campaign_id,
                    CampaignLead.lead_id == campaign_lead.lead_id,
                )
                .values(status="failed")
            )
            stats["failed"] += 1

        # Staggered delivery: wait between sends (skip delay after last email)
        if idx < total:
            delay = random.uniform(stagger_min, stagger_max)  # noqa: S311
            logger.info(
                "email_stagger_progress",
                sent=f"{idx}/{total}",
                next_delay_seconds=round(delay, 1),
                campaign_id=str(campaign_id),
            )
            await asyncio.sleep(delay)

    # Update campaign-level stats
    await db_session.execute(
        update(EmailCampaign)
        .where(EmailCampaign.id == campaign_id)
        .values(
            sent_count=EmailCampaign.sent_count + stats["sent"],
            bounce_count=EmailCampaign.bounce_count + stats["bounced"],
        )
    )

    await db_session.commit()

    logger.info(
        "send_approved_emails_complete",
        campaign_id=str(campaign_id),
        **stats,
    )
    return stats
