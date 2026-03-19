"""Email sender for Pipeline B approved outreach emails.

Uses ``aiosmtplib`` for async SMTP delivery with TLS.  Includes rate
limiting (default 50 emails/day), HTML email support, staggered sending,
List-Unsubscribe headers (RFC 8058), bounce detection with automatic
suppression of hard bounces and exponential-backoff retry for soft bounces.

Usage::

    sender = EmailSender()
    ok = await sender.send_email("lead@example.com", "Subject", "Body")

    # After HITL approval, send all approved campaign emails:
    stats = await send_approved_emails(campaign_id, db_session)

    # Check bounce statistics (for warmup integration):
    from src.enrichment.email_sender import get_bounce_stats
    bs = get_bounce_stats()
    print(bs.bounce_rate, bs.hard_bounce_count)
"""

from __future__ import annotations

import asyncio
import hashlib
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

# Maximum retries for soft bounces (exponential backoff: 2s, 4s, 8s).
_SOFT_BOUNCE_MAX_RETRIES = 3
_SOFT_BOUNCE_BASE_DELAY = 2  # seconds


# ---------------------------------------------------------------------------
# Bounce statistics tracker (for warmup integration)
# ---------------------------------------------------------------------------


class BounceStats:
    """Track email bounce statistics for warmup health monitoring.

    This is a lightweight in-memory counter.  The :class:`WarmupManager`
    reads these stats to decide whether to pause warmup when the bounce
    rate exceeds the red line (5%).

    Thread-safety: NOT thread-safe -- designed for single-event-loop use.
    """

    def __init__(self) -> None:
        self.total_sent: int = 0
        self.hard_bounce_count: int = 0
        self.soft_bounce_count: int = 0
        self.hard_bounced_emails: set[str] = set()

    @property
    def total_bounces(self) -> int:
        """Total bounces (hard + soft)."""
        return self.hard_bounce_count + self.soft_bounce_count

    @property
    def bounce_rate(self) -> float:
        """Bounce rate as a fraction (0.0 - 1.0).  Returns 0.0 if nothing sent."""
        if self.total_sent == 0:
            return 0.0
        return self.total_bounces / self.total_sent

    def record_sent(self) -> None:
        """Record a successful email send."""
        self.total_sent += 1

    def record_hard_bounce(self, email: str) -> None:
        """Record a hard bounce for the given email."""
        self.hard_bounce_count += 1
        self.hard_bounced_emails.add(email.strip().lower())

    def record_soft_bounce(self, email: str) -> None:
        """Record a soft bounce (after retries exhausted)."""
        self.soft_bounce_count += 1

    def exceeds_threshold(self, threshold: float) -> bool:
        """Return ``True`` if the bounce rate >= *threshold*."""
        return self.bounce_rate >= threshold

    def reset(self) -> None:
        """Reset all counters (for testing or daily reset)."""
        self.total_sent = 0
        self.hard_bounce_count = 0
        self.soft_bounce_count = 0
        self.hard_bounced_emails = set()

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable summary."""
        return {
            "total_sent": self.total_sent,
            "hard_bounce_count": self.hard_bounce_count,
            "soft_bounce_count": self.soft_bounce_count,
            "total_bounces": self.total_bounces,
            "bounce_rate": round(self.bounce_rate, 4),
        }


# Module-level singleton for cross-call bounce tracking.
_bounce_stats = BounceStats()


def get_bounce_stats() -> BounceStats:
    """Return the module-level :class:`BounceStats` instance."""
    return _bounce_stats


# ---------------------------------------------------------------------------
# Hard-bounce auto-suppression
# ---------------------------------------------------------------------------


async def _auto_suppress_hard_bounce(email: str) -> None:
    """Add a hard-bounced email to the suppression list.

    Uses its own DB session so the suppression is persisted independently
    of the caller's transaction.  Errors are logged and swallowed --
    suppression failure must never break email delivery flow.

    Also notifies the :class:`WarmupManager` (if available) about the
    bounce so warmup statistics stay accurate and auto-pause triggers
    correctly.
    """
    try:
        from src.enrichment.suppression import SuppressionList  # noqa: PLC0415

        session = _get_db_session()
        async with session:
            sl = SuppressionList(session)
            await sl.suppress(
                email,
                reason="hard_bounce",
                source="bounce_handler",
            )
            await session.commit()

        logger.info("bounce.auto_suppressed", email=email)

    except Exception:  # noqa: BLE001
        logger.error(
            "bounce.auto_suppress_failed",
            email=email,
            exc_info=True,
        )

    # Notify warmup manager about the bounce (best-effort).
    try:
        from src.enrichment.warmup import WarmupManager  # noqa: PLC0415

        warmup_session = _get_db_session()
        async with warmup_session:
            mgr = WarmupManager(session=warmup_session)
            # Try to find an active warmup record to update.
            # If no warmup is active, this is a no-op.
            from sqlalchemy import select  # noqa: PLC0415

            from src.enrichment.warmup import WarmupRecord  # noqa: PLC0415

            result = await warmup_session.execute(
                select(WarmupRecord).where(WarmupRecord.is_paused.is_(False))  # type: ignore[attr-defined]
            )
            record = result.scalar_one_or_none()
            if record is not None:
                await mgr.record_send(record, bounced=True)
                await warmup_session.commit()
                logger.debug("bounce.warmup_notified", email=email)

    except Exception:  # noqa: BLE001
        # Warmup notification is best-effort -- never block on failure.
        logger.debug("bounce.warmup_notify_skipped", email=email)


def _get_db_session() -> Any:
    """Obtain an async DB session for bounce suppression.

    Imports lazily to avoid circular imports and import-time DB access.
    Returns an ``AsyncSession`` from the shared session factory.
    """
    from src.core.database import async_session_factory  # noqa: PLC0415

    return async_session_factory()


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
# Template personalization
# ---------------------------------------------------------------------------

# Supported placeholders for template personalization.
_TEMPLATE_PLACEHOLDERS = frozenset(
    {
        "first_name",
        "last_name",
        "company",
        "industry",
        "city",
        "role",
        "website",
    }
)


def personalize_template(
    template: str,
    context: dict[str, str],
    *,
    fallback: str = "",
) -> str:
    """Replace ``{{placeholder}}`` tokens in *template* with values from *context*.

    Supported placeholders: ``first_name``, ``last_name``, ``company``,
    ``industry``, ``city``, ``role``, ``website``.

    Unknown placeholders are left as-is (they may be literal text).
    Missing keys receive *fallback* (empty string by default).

    Parameters
    ----------
    template:
        The template string with ``{{placeholder}}`` tokens.
    context:
        Mapping of placeholder name to replacement value.
    fallback:
        Value to use when a recognized placeholder has no matching
        key in *context*.

    Returns
    -------
    str
        The personalized string with all recognized placeholders replaced.
    """
    result = template
    for key in _TEMPLATE_PLACEHOLDERS:
        token = "{{" + key + "}}"
        if token in result:
            value = context.get(key) or fallback
            result = result.replace(token, value)
    return result


def select_ab_variant(
    variants: list[str],
    *,
    lead_id: str = "",
) -> str:
    """Select one variant from a list for A/B testing.

    Uses a deterministic hash of *lead_id* so the same lead always
    sees the same variant (consistent experience across retries).
    When *lead_id* is empty or only one variant exists, returns the
    first variant.

    Parameters
    ----------
    variants:
        List of 1--5 subject-line (or body) variants.
    lead_id:
        Unique lead identifier used for deterministic bucketing.

    Returns
    -------
    str
        The selected variant string.

    Raises
    ------
    ValueError
        If *variants* is empty.
    """
    if not variants:
        raise ValueError("variants list must not be empty")

    if len(variants) == 1 or not lead_id:
        return variants[0]

    # Deterministic bucket via SHA-256 -- stable across process restarts
    # and across different Python versions / interpreters (unlike builtin
    # hash() which is randomized by PYTHONHASHSEED).
    bucket = int(hashlib.sha256(lead_id.encode()).hexdigest(), 16) % len(variants)
    return variants[bucket]


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

    .. note:: Multi-worker limitation

       The ``_daily_count`` / ``_daily_date`` state is stored in-process
       memory.  In a multi-worker deployment (e.g. multiple Gunicorn
       workers or multiple Railway containers) each worker maintains its
       own independent counter.  This means the effective daily limit
       becomes ``max_per_day * num_workers``.  For strict global rate
       limiting, migrate this counter to Valkey (see ``llm_rate_limiter``
       for the pattern).
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

        Bounce enforcement:

        - **Hard bounce** (550-553): returns ``"hard_bounce"`` and
          auto-adds the recipient to the suppression list via
          :func:`_auto_suppress_hard_bounce`.
        - **Soft bounce** (421, 450-452): retries up to
          ``_SOFT_BOUNCE_MAX_RETRIES`` times with exponential backoff
          (2 s, 4 s, 8 s).  If all retries fail, returns ``"soft_bounce"``.
          If a retry succeeds, returns ``True``.

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
            ``"soft_bounce"`` on temporary rejection (421/450-452) after
            retries exhausted.
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

        # Rate limit check (consumed once -- retries do NOT re-count).
        if not _check_and_increment_rate_limit(self.max_emails_per_day):
            logger.warning(
                "email_rate_limited",
                max_per_day=self.max_emails_per_day,
                to=to,
            )
            return False

        import aiosmtplib  # noqa: PLC0415

        msg = self._build_message(to, subject, body, sender)

        # Attempt initial send + up to _SOFT_BOUNCE_MAX_RETRIES retries
        # on soft bounce.  Hard bounces and generic errors break immediately.
        max_attempts = 1 + _SOFT_BOUNCE_MAX_RETRIES
        last_exc: Exception | None = None

        for attempt in range(max_attempts):
            try:
                await aiosmtplib.send(
                    msg,
                    hostname=self.smtp_host,
                    port=self.smtp_port,
                    username=self.smtp_user or None,
                    password=self.smtp_password or None,
                    start_tls=True,
                )

                logger.info("email_sent", to=to, subject=subject)
                _bounce_stats.record_sent()
                return True

            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                bounce_type = _classify_bounce(exc)

                if bounce_type == "hard_bounce":
                    logger.warning(
                        "email_hard_bounce",
                        to=to,
                        subject=subject,
                        smtp_code=getattr(exc, "code", None),
                        error=str(exc),
                    )
                    _bounce_stats.record_hard_bounce(to)
                    await _auto_suppress_hard_bounce(to)
                    return "hard_bounce"

                if bounce_type == "soft_bounce":
                    retry_num = attempt + 1
                    if retry_num < max_attempts:
                        delay = _SOFT_BOUNCE_BASE_DELAY**retry_num
                        logger.warning(
                            "email_soft_bounce_retry",
                            to=to,
                            attempt=retry_num,
                            max_attempts=max_attempts,
                            delay_seconds=delay,
                            smtp_code=getattr(exc, "code", None),
                            error=str(exc),
                        )
                        await asyncio.sleep(delay)
                        continue

                    # All retries exhausted.
                    logger.warning(
                        "email_soft_bounce",
                        to=to,
                        subject=subject,
                        smtp_code=getattr(exc, "code", None),
                        error=str(exc),
                        retries_exhausted=True,
                    )
                    _bounce_stats.record_soft_bounce(to)
                    return "soft_bounce"

                # Non-bounce error -- do not retry.
                logger.error(
                    "email_send_failed",
                    to=to,
                    subject=subject,
                    error=str(exc),
                    exc_info=True,
                )
                return False

        # Should not reach here, but safety net.
        logger.error("email_send_exhausted", to=to, error=str(last_exc))
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
        ``{"sent": N, "failed": M, "rate_limited": R, "bounced": B, "suppressed": S}`` counts.
    """
    from sqlalchemy import select, update  # noqa: PLC0415

    from src.core.models import CampaignLead, EmailCampaign, Lead  # noqa: PLC0415
    from src.enrichment.suppression import SuppressionList  # noqa: PLC0415

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
    stats: dict[str, int] = {"sent": 0, "failed": 0, "rate_limited": 0, "bounced": 0, "suppressed": 0}

    if not sender.is_configured:
        logger.warning("send_approved_emails_skipped", reason="SMTP not configured")
        return stats

    # Production gate: if warmup is active but not yet production-ready,
    # enforce the warmup daily send limit.
    warmup_daily_cap: int | None = None
    try:
        from src.enrichment.warmup import WarmupManager, WarmupRecord  # noqa: PLC0415

        warmup_result = await db_session.execute(
            select(WarmupRecord).limit(1)  # type: ignore[arg-type]
        )
        warmup_record = warmup_result.scalar_one_or_none()
        if warmup_record is not None:
            warmup_mgr = WarmupManager(session=db_session)
            if not await warmup_mgr.is_production_ready(warmup_record):
                warmup_daily_cap = warmup_mgr.get_daily_limit(warmup_record)
                logger.info(
                    "send_approved_emails.warmup_gate",
                    warmup_daily_cap=warmup_daily_cap,
                    stage=warmup_record.stage.value if hasattr(warmup_record, "stage") else "unknown",
                )
    except Exception:  # noqa: BLE001
        # Warmup check is best-effort -- proceed without cap on error.
        logger.debug("send_approved_emails.warmup_check_skipped", exc_info=True)

    # Build suppression list checker for pre-send filtering.
    suppression = SuppressionList(db_session)

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

    warmup_sent_count = 0

    for idx, (campaign_lead, lead) in enumerate(rows, 1):
        if not lead.email:
            logger.warning("send_approved_email_no_address", lead_id=str(lead.id))
            stats["failed"] += 1
            continue

        # Suppression check: skip emails on the suppression list.
        if await suppression.is_suppressed(lead.email):
            logger.info(
                "send_approved_email_suppressed",
                email=lead.email,
                lead_id=str(lead.id),
            )
            await db_session.execute(
                update(CampaignLead)
                .where(
                    CampaignLead.campaign_id == campaign_lead.campaign_id,
                    CampaignLead.lead_id == campaign_lead.lead_id,
                )
                .values(status="suppressed")
            )
            stats["suppressed"] += 1
            continue

        # Production gate: enforce warmup daily cap.
        if warmup_daily_cap is not None and warmup_sent_count >= warmup_daily_cap:
            logger.warning(
                "send_approved_email_warmup_capped",
                email=lead.email,
                warmup_daily_cap=warmup_daily_cap,
                warmup_sent_count=warmup_sent_count,
            )
            stats["rate_limited"] += 1
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
            warmup_sent_count += 1

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
            await db_session.execute(update(Lead).where(Lead.id == campaign_lead.lead_id).values(status="no_contact"))
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
