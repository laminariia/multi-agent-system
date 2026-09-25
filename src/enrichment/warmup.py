"""Email warmup manager for 6-week domain reputation building.

Implements the warmup strategy from ``enrichment-spec.md``:

- Week 1: 5/day personal contacts
- Week 2: 10/day personal + seed list
- Week 3: 15/day mixed (70% personal, 30% warm)
- Week 4: 25/day mixed (50% personal, 50% cold verified)
- Week 5: 35/day (30% personal, 70% cold)
- Week 6: 50/day full cold outreach

After week 6, production is unlocked.  Bounce rate is monitored
continuously -- if it exceeds 5% (the red line), the warmup is
auto-paused and the operator must intervene.

Usage::

    mgr = WarmupManager(session=db, domain="outreach.acme.com")
    record = await mgr.create_warmup()

    if mgr.can_send_today(record):
        # ... send email ...
        record = await mgr.record_send(record, bounced=False)

    record = await mgr.advance_stage(record)

    if await mgr.is_production_ready(record):
        print("Production unlocked!")
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BOUNCE_RED_LINE: float = 0.05  # 5% -- stop & review threshold
PRODUCTION_DAILY_LIMIT: int = 50


class WarmupStage(enum.StrEnum):
    """Warmup phases from spec (6 weeks + production)."""

    WEEK_1 = "week_1"
    WEEK_2 = "week_2"
    WEEK_3 = "week_3"
    WEEK_4 = "week_4"
    WEEK_5 = "week_5"
    WEEK_6 = "week_6"
    PRODUCTION = "production"


# Ordered list for advancement logic.
STAGE_ORDER: list[WarmupStage] = [
    WarmupStage.WEEK_1,
    WarmupStage.WEEK_2,
    WarmupStage.WEEK_3,
    WarmupStage.WEEK_4,
    WarmupStage.WEEK_5,
    WarmupStage.WEEK_6,
    WarmupStage.PRODUCTION,
]

# Per-stage configuration (matches the spec table).
STAGE_CONFIG: dict[WarmupStage, dict[str, Any]] = {
    WarmupStage.WEEK_1: {
        "daily_limit": 5,
        "duration_days": 7,
        "audience": "personal",
        "description": "Personal contacts, reply-worthy content",
    },
    WarmupStage.WEEK_2: {
        "daily_limit": 10,
        "duration_days": 7,
        "audience": "personal_seed",
        "description": "Personal + seed list with test reply chains",
    },
    WarmupStage.WEEK_3: {
        "daily_limit": 15,
        "duration_days": 7,
        "audience": "mixed_warm",
        "description": "70% personal, 30% warm leads",
    },
    WarmupStage.WEEK_4: {
        "daily_limit": 25,
        "duration_days": 7,
        "audience": "mixed_cold",
        "description": "50% personal, 50% cold (verified)",
    },
    WarmupStage.WEEK_5: {
        "daily_limit": 35,
        "duration_days": 7,
        "audience": "mostly_cold",
        "description": "30% personal, 70% cold",
    },
    WarmupStage.WEEK_6: {
        "daily_limit": 50,
        "duration_days": 7,
        "audience": "cold_outreach",
        "description": "100% cold outreach (verified emails)",
    },
}


# ---------------------------------------------------------------------------
# WarmupRecord — lightweight data transfer object
# ---------------------------------------------------------------------------


@dataclass
class WarmupRecord:
    """In-memory representation of a warmup state row.

    This is intentionally a plain dataclass (not an ORM model) so that
    the warmup logic can be tested without a database.  The
    ``WarmupManager`` is responsible for persisting changes back to the
    DB via the session.
    """

    id: uuid.UUID
    domain: str
    stage: WarmupStage
    started_at: datetime
    stage_started_at: datetime
    total_sent: int = 0
    total_bounced: int = 0
    total_replies: int = 0
    daily_sent_today: int = 0
    last_send_date: date | None = None
    is_paused: bool = False
    pause_reason: str | None = None


# ---------------------------------------------------------------------------
# WarmupManager
# ---------------------------------------------------------------------------


class WarmupManager:
    """Manages the 6-week email warmup lifecycle.

    All mutating methods return the updated ``WarmupRecord`` and flush
    the session so changes are visible within the same transaction.

    Parameters
    ----------
    session:
        An async SQLAlchemy session (``AsyncSession``).
    domain:
        The outreach domain being warmed up (e.g. ``outreach.acme.com``).
    """

    def __init__(self, *, session: Any, domain: str = "") -> None:
        self._session = session
        self._domain = domain

    # ------------------------------------------------------------------
    # Read-only helpers
    # ------------------------------------------------------------------

    async def is_production_ready(self, record: WarmupRecord) -> bool:
        """Return ``True`` when the domain has completed warmup.

        Production is only unlocked when:
        - The stage is ``PRODUCTION``
        - The warmup is **not** paused
        """
        return record.stage == WarmupStage.PRODUCTION and not record.is_paused

    def get_daily_limit(self, record: WarmupRecord) -> int:
        """Return the maximum emails allowed today for the current stage.

        Returns 0 when the warmup is paused.
        """
        if record.is_paused:
            return 0

        cfg = STAGE_CONFIG.get(record.stage)
        if cfg is not None:
            return cfg["daily_limit"]

        # PRODUCTION stage — default limit.
        return PRODUCTION_DAILY_LIMIT

    def can_send_today(self, record: WarmupRecord) -> bool:
        """Return ``True`` if another email can be sent today."""
        if record.is_paused:
            return False

        limit = self.get_daily_limit(record)
        today = datetime.now(UTC).date()

        # If the last send was on a different day, the counter is stale
        # and will be reset on the next ``record_send`` call, so we
        # treat it as 0.
        if record.last_send_date is not None and record.last_send_date != today:
            return True

        return record.daily_sent_today < limit

    def get_bounce_rate(self, record: WarmupRecord) -> float:
        """Return the overall bounce rate (0.0 - 1.0)."""
        if record.total_sent == 0:
            return 0.0
        return record.total_bounced / record.total_sent

    def should_pause_for_bounces(self, record: WarmupRecord) -> bool:
        """Return ``True`` if the bounce rate has hit or exceeded the red line."""
        return self.get_bounce_rate(record) >= BOUNCE_RED_LINE

    def get_days_in_stage(self, record: WarmupRecord) -> int:
        """Return the number of complete days spent in the current stage."""
        elapsed = datetime.now(UTC) - record.stage_started_at
        return int(elapsed.total_seconds() // 86400)

    def get_status(self, record: WarmupRecord) -> dict[str, Any]:
        """Return a summary dict suitable for API responses / logging."""
        return {
            "domain": record.domain,
            "stage": record.stage.value,
            "daily_limit": self.get_daily_limit(record),
            "daily_sent_today": record.daily_sent_today,
            "total_sent": record.total_sent,
            "total_bounced": record.total_bounced,
            "total_replies": record.total_replies,
            "bounce_rate": self.get_bounce_rate(record),
            "is_paused": record.is_paused,
            "pause_reason": record.pause_reason,
            "is_production_ready": record.stage == WarmupStage.PRODUCTION and not record.is_paused,
            "days_in_stage": self.get_days_in_stage(record),
        }

    # ------------------------------------------------------------------
    # Mutating methods
    # ------------------------------------------------------------------

    async def create_warmup(self) -> WarmupRecord:
        """Create a new warmup record at ``WEEK_1`` and persist it.

        Returns the newly created ``WarmupRecord``.
        """
        now = datetime.now(UTC)
        record = WarmupRecord(
            id=uuid.uuid4(),
            domain=self._domain,
            stage=WarmupStage.WEEK_1,
            started_at=now,
            stage_started_at=now,
        )

        self._session.add(record)
        await self._session.flush()

        logger.info(
            "warmup.created",
            domain=self._domain,
            stage=record.stage.value,
        )
        return record

    async def record_send(
        self,
        record: WarmupRecord,
        *,
        bounced: bool = False,
        replied: bool = False,
    ) -> WarmupRecord:
        """Record an email send and update counters.

        If the bounce rate exceeds the red line after this send, the
        warmup is automatically paused.

        Parameters
        ----------
        record:
            The current warmup record.
        bounced:
            Whether the email bounced.
        replied:
            Whether the recipient replied (tracked for reputation).

        Returns
        -------
        WarmupRecord
            The updated record (same object, mutated in place).
        """
        today = datetime.now(UTC).date()

        # Reset daily counter if the date rolled over.
        if record.last_send_date is not None and record.last_send_date != today:
            record.daily_sent_today = 0

        record.total_sent += 1
        record.daily_sent_today += 1
        record.last_send_date = today

        if bounced:
            record.total_bounced += 1

        if replied:
            record.total_replies += 1

        # Auto-pause on high bounce rate.
        if self.should_pause_for_bounces(record):
            record.is_paused = True
            record.pause_reason = "bounce_rate_exceeded"
            logger.warning(
                "warmup.auto_paused",
                domain=record.domain,
                bounce_rate=self.get_bounce_rate(record),
                total_sent=record.total_sent,
                total_bounced=record.total_bounced,
            )

        await self._session.flush()

        logger.debug(
            "warmup.send_recorded",
            domain=record.domain,
            stage=record.stage.value,
            daily_sent=record.daily_sent_today,
            total_sent=record.total_sent,
            bounced=bounced,
        )
        return record

    async def advance_stage(self, record: WarmupRecord) -> WarmupRecord:
        """Attempt to advance the warmup to the next stage.

        Advancement requires:
        - The warmup is **not** paused.
        - The bounce rate is below the red line.
        - The current stage duration has fully elapsed (> ``duration_days``).
        - The current stage is not already ``PRODUCTION``.

        If any condition is not met, the record is returned unchanged.

        Returns
        -------
        WarmupRecord
            The (possibly updated) record.
        """
        # Already at production — nothing to do.
        if record.stage == WarmupStage.PRODUCTION:
            return record

        # Cannot advance while paused.
        if record.is_paused:
            logger.debug("warmup.advance_blocked", reason="paused", domain=record.domain)
            return record

        # Cannot advance with high bounce rate.
        if self.should_pause_for_bounces(record):
            logger.debug(
                "warmup.advance_blocked",
                reason="bounce_rate",
                bounce_rate=self.get_bounce_rate(record),
                domain=record.domain,
            )
            return record

        # Check duration.
        cfg = STAGE_CONFIG[record.stage]
        elapsed = datetime.now(UTC) - record.stage_started_at
        if elapsed <= timedelta(days=cfg["duration_days"]):
            logger.debug(
                "warmup.advance_blocked",
                reason="duration_not_elapsed",
                days_elapsed=self.get_days_in_stage(record),
                required=cfg["duration_days"],
                domain=record.domain,
            )
            return record

        # Determine next stage.
        current_idx = STAGE_ORDER.index(record.stage)
        next_stage = STAGE_ORDER[current_idx + 1]

        old_stage = record.stage
        record.stage = next_stage
        record.stage_started_at = datetime.now(UTC)

        await self._session.flush()

        logger.info(
            "warmup.stage_advanced",
            domain=record.domain,
            from_stage=old_stage.value,
            to_stage=next_stage.value,
        )
        return record

    async def pause_warmup(self, record: WarmupRecord, *, reason: str) -> WarmupRecord:
        """Pause the warmup with a given reason.

        Parameters
        ----------
        record:
            The warmup record to pause.
        reason:
            Human-readable reason (e.g. ``"manual_review"``,
            ``"bounce_rate_exceeded"``).

        Returns
        -------
        WarmupRecord
            The updated record.
        """
        record.is_paused = True
        record.pause_reason = reason

        await self._session.flush()

        logger.info("warmup.paused", domain=record.domain, reason=reason)
        return record

    async def resume_warmup(self, record: WarmupRecord) -> WarmupRecord:
        """Resume a paused warmup.

        If the pause was caused by a bounce rate issue
        (``pause_reason == "bounce_rate_exceeded"``), the stage is
        demoted to the previous level per the spec's recovery protocol:
        *"Immediately reduce volume to the previous week's level."*

        Returns
        -------
        WarmupRecord
            The updated record.
        """
        should_demote = record.pause_reason == "bounce_rate_exceeded"

        record.is_paused = False
        record.pause_reason = None

        if should_demote:
            current_idx = STAGE_ORDER.index(record.stage)
            if current_idx > 0:
                old_stage = record.stage
                record.stage = STAGE_ORDER[current_idx - 1]
                record.stage_started_at = datetime.now(UTC)
                logger.info(
                    "warmup.stage_demoted",
                    domain=record.domain,
                    from_stage=old_stage.value,
                    to_stage=record.stage.value,
                    reason="bounce_recovery",
                )

        await self._session.flush()

        logger.info("warmup.resumed", domain=record.domain, stage=record.stage.value)
        return record


# ---------------------------------------------------------------------------
# APScheduler integration
# ---------------------------------------------------------------------------


def get_scheduler_config() -> dict[str, Any]:
    """Return APScheduler job configuration for daily ``advance_stage`` calls.

    The returned dict can be passed directly to
    ``scheduler.add_job(**get_scheduler_config())`` to schedule a daily
    cron job that attempts to advance the warmup stage.

    The job runs once per day at 06:00 UTC (before most business hours)
    so that the new daily limits take effect at the start of the day.

    Returns
    -------
    dict
        APScheduler ``add_job`` kwargs with ``trigger``, ``id``,
        ``hour``, ``minute``, ``max_instances``, ``coalesce``,
        ``replace_existing``, and ``misfire_grace_time`` keys.

    Example
    -------
    ::

        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from src.enrichment.warmup import get_scheduler_config

        scheduler = AsyncIOScheduler()
        scheduler.add_job(advance_warmup_cron, **get_scheduler_config())
        scheduler.start()
    """
    return {
        "trigger": "cron",
        "id": "warmup_advance_stage",
        "hour": 6,
        "minute": 0,
        "max_instances": 1,
        "coalesce": True,
        "replace_existing": True,
        "misfire_grace_time": 3600,
    }
