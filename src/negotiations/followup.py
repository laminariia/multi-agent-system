"""Follow-up scheduler for negotiations with silent clients.

Runs as a cron job (APScheduler, every 30 min) outside LangGraph.
Checks all active negotiations for client silence and sends automated
follow-up messages on a 4-step escalating schedule.

Spec: docs/Full_work/specs/negotiation-spec.md  (Section "Follow-up Scheduler")
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.core.database import get_db_session
from src.core.models import Bid, ClientMessage, Job
from src.core.models import Negotiation as NegotiationORM

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Follow-up schedule -- spec-defined (section 4.3)
# ---------------------------------------------------------------------------

# States eligible for automated follow-up
_FOLLOWUP_ELIGIBLE_STATES: frozenset[str] = frozenset({"initial", "qualifying", "proposing"})

# Terminal or operator-controlled states -- never send follow-ups
_CANCEL_STATES: frozenset[str] = frozenset({"won", "lost", "stale", "accepted", "declined", "operator_override"})

MAX_FOLLOWUP_STEPS = 4


@dataclass(slots=True)
class FollowUpStep:
    """A single step in the follow-up escalation ladder."""

    days: int
    template: str
    auto_send: bool


@dataclass(slots=True)
class FollowUpResult:
    """Outcome of a single follow-up check for reporting."""

    bid_id: str
    step: str
    days_silent: int
    action: str  # "sent" | "archived" | "skipped"


FOLLOW_UP_SCHEDULE: list[FollowUpStep] = [
    FollowUpStep(days=2, template="gentle_reminder", auto_send=True),
    FollowUpStep(days=5, template="second_followup", auto_send=True),
    FollowUpStep(days=10, template="final_checkin", auto_send=True),
    FollowUpStep(days=14, template="archive_as_stale", auto_send=True),
]

FOLLOW_UP_TEMPLATES: dict[str, str | None] = {
    "gentle_reminder": ("Hi! Just checking in on our conversation about {job_title}. Would you like to proceed?"),
    "second_followup": (
        "Hello! I wanted to follow up on the {job_title} project. I'm still available if you're interested."
    ),
    "final_checkin": (
        "Hi, just a final check \u2014 are you still considering the {job_title} project? Let me know either way."
    ),
    "archive_as_stale": None,  # No message sent, just mark as stale
}


# ---------------------------------------------------------------------------
# FollowUpScheduler
# ---------------------------------------------------------------------------


class FollowUpScheduler:
    """Manages automated follow-up sequences for silent negotiations.

    Parameters
    ----------
    adapters:
        Optional mapping of platform name to adapter instance.
        Reserved for future use (sending follow-ups through platform APIs).
    """

    def __init__(self, adapters: dict[str, Any] | None = None) -> None:
        self._adapters: dict[str, Any] = adapters or {}

    # -- public entry point ---------------------------------------------------

    async def check_and_send(self) -> list[FollowUpResult]:
        """Main cron entry point -- runs every 30 minutes.

        Checks all active negotiations for silence and sends follow-ups
        according to the escalation schedule.

        Returns a list of :class:`FollowUpResult` for each action taken.
        """
        results: list[FollowUpResult] = []

        async with get_db_session() as session:
            rows = await self._get_eligible_negotiations(session)

            for neg, bid, job in rows:
                result = await self._process_negotiation(session, neg, bid, job)
                if result is not None:
                    results.append(result)

            # Session commits automatically via get_db_session context manager

        if results:
            logger.info(
                "followup_check_complete",
                total_actions=len(results),
                sent=sum(1 for r in results if r.action == "sent"),
                archived=sum(1 for r in results if r.action == "archived"),
            )

        return results

    # -- query helpers --------------------------------------------------------

    async def _get_eligible_negotiations(
        self,
        session: AsyncSession,
    ) -> list[tuple[NegotiationORM, Bid, Job | None]]:
        """Fetch negotiations in follow-up-eligible states with bids and jobs."""
        stmt = (
            select(NegotiationORM, Bid, Job)
            .join(Bid, NegotiationORM.bid_id == Bid.id)
            .outerjoin(Job, Bid.job_id == Job.id)
            .where(
                NegotiationORM.state.in_(list(_FOLLOWUP_ELIGIBLE_STATES)),
            )
            .options(joinedload(Bid.job))
        )
        result = await session.execute(stmt)
        return result.unique().all()  # type: ignore[return-value]

    async def _get_last_client_message(
        self,
        session: AsyncSession,
        bid_id: Any,
    ) -> ClientMessage | None:
        """Get the most recent inbound message from the client for a bid."""
        stmt = (
            select(ClientMessage)
            .where(
                and_(
                    ClientMessage.bid_id == bid_id,
                    ClientMessage.direction == "inbound",
                )
            )
            .order_by(ClientMessage.created_at.desc())
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_last_outbound_message(
        self,
        session: AsyncSession,
        bid_id: Any,
    ) -> ClientMessage | None:
        """Get the most recent outbound message for a bid."""
        stmt = (
            select(ClientMessage)
            .where(
                and_(
                    ClientMessage.bid_id == bid_id,
                    ClientMessage.direction == "outbound",
                )
            )
            .order_by(ClientMessage.created_at.desc())
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    # -- processing logic -----------------------------------------------------

    async def _process_negotiation(
        self,
        session: AsyncSession,
        neg: NegotiationORM,
        bid: Bid,
        job: Job | None,
    ) -> FollowUpResult | None:
        """Evaluate a single negotiation and execute follow-up if needed.

        Returns a :class:`FollowUpResult` if an action was taken, else None.
        """
        # Check cancel conditions first
        if self._should_cancel(neg):
            return None

        last_client_msg = await self._get_last_client_message(session, bid.id)
        last_outbound = await self._get_last_outbound_message(session, bid.id)

        # If client replied after our last outbound, skip -- conversation is active
        if last_client_msg and last_outbound:
            if last_client_msg.created_at > last_outbound.created_at:
                return None

        # Determine silence duration from the most recent reference point
        reference_time = self._get_reference_time(neg, last_outbound)
        days_silent = (datetime.now(UTC) - reference_time).days

        # Find the appropriate follow-up step
        step = self._get_next_step(days_silent, neg.followup_count)
        if step is None:
            return None

        # Execute the follow-up
        return await self._execute_followup(
            session,
            neg,
            bid,
            job,
            step,
            days_silent,
        )

    def _should_cancel(self, neg: NegotiationORM) -> bool:
        """Check whether follow-up should be cancelled for this negotiation.

        Cancel conditions:
        - Negotiation is in a terminal or operator-controlled state
        - Follow-up count already exhausted all steps
        """
        if neg.state in _CANCEL_STATES:
            return True
        if neg.followup_count >= MAX_FOLLOWUP_STEPS:
            return True
        return False

    @staticmethod
    def _get_reference_time(
        neg: NegotiationORM,
        last_outbound: ClientMessage | None,
    ) -> datetime:
        """Determine the reference time for measuring silence.

        Uses the last outbound message timestamp if available,
        otherwise falls back to the negotiation creation time.
        """
        if last_outbound is not None:
            return last_outbound.created_at
        return neg.created_at

    def _get_next_step(
        self,
        days_silent: int,
        followup_count: int,
    ) -> FollowUpStep | None:
        """Determine which follow-up step based on silence duration.

        Each step fires only once: ``followup_count`` tracks how many
        have been sent, so step *i* fires when ``days_silent >= step.days``
        and ``followup_count == i``.
        """
        for i, step in enumerate(FOLLOW_UP_SCHEDULE):
            if days_silent >= step.days and followup_count == i:
                return step
        return None

    # -- execution ------------------------------------------------------------

    async def _execute_followup(
        self,
        session: AsyncSession,
        neg: NegotiationORM,
        bid: Bid,
        job: Job | None,
        step: FollowUpStep,
        days_silent: int,
    ) -> FollowUpResult:
        """Send follow-up message or archive as stale."""
        bid_id_str = str(bid.id)

        if step.template == "archive_as_stale":
            return await self._archive_as_stale(session, neg, bid_id_str, days_silent)

        return await self._send_followup_message(
            session,
            neg,
            bid,
            job,
            step,
            days_silent,
        )

    async def _archive_as_stale(
        self,
        session: AsyncSession,
        neg: NegotiationORM,
        bid_id_str: str,
        days_silent: int,
    ) -> FollowUpResult:
        """Mark the negotiation as stale -- no message sent."""
        neg.previous_state = neg.state
        neg.state = "stale"
        neg.outcome = "stale"
        neg.resolved_at = datetime.now(UTC)
        neg.followup_count = neg.followup_count + 1

        logger.info(
            "negotiation_archived_stale",
            bid_id=bid_id_str,
            previous_state=neg.previous_state,
            days_silent=days_silent,
        )

        return FollowUpResult(
            bid_id=bid_id_str,
            step="archive_as_stale",
            days_silent=days_silent,
            action="archived",
        )

    async def _send_followup_message(
        self,
        session: AsyncSession,
        neg: NegotiationORM,
        bid: Bid,
        job: Job | None,
        step: FollowUpStep,
        days_silent: int,
    ) -> FollowUpResult:
        """Compose and persist a follow-up outbound message."""
        bid_id_str = str(bid.id)

        template_text = FOLLOW_UP_TEMPLATES.get(step.template)
        if not template_text:
            logger.warning(
                "followup_template_missing",
                template=step.template,
                bid_id=bid_id_str,
            )
            return FollowUpResult(
                bid_id=bid_id_str,
                step=step.template,
                days_silent=days_silent,
                action="skipped",
            )

        # Resolve job title for template interpolation
        job_title = job.title if job else "your project"
        message_text = template_text.format(job_title=job_title)

        # Persist outbound message
        cm = ClientMessage(
            bid_id=bid.id,
            direction="outbound",
            sender="ai",
            content=message_text,
            auto_generated=True,
            message_type="followup",
            hitl_reviewed=False,
        )
        session.add(cm)

        # Update negotiation counters
        neg.followup_count = neg.followup_count + 1
        neg.last_followup_at = datetime.now(UTC)

        logger.info(
            "followup_sent",
            bid_id=bid_id_str,
            step=step.template,
            count=neg.followup_count,
            days_silent=days_silent,
        )

        return FollowUpResult(
            bid_id=bid_id_str,
            step=step.template,
            days_silent=days_silent,
            action="sent",
        )


# ---------------------------------------------------------------------------
# Module-level convenience for APScheduler cron registration
# ---------------------------------------------------------------------------

_default_scheduler: FollowUpScheduler | None = None


def get_followup_scheduler() -> FollowUpScheduler:
    """Return the module-level singleton (created lazily)."""
    global _default_scheduler  # noqa: PLW0603
    if _default_scheduler is None:
        _default_scheduler = FollowUpScheduler()
    return _default_scheduler


async def check_and_send_followups() -> list[FollowUpResult]:
    """APScheduler-compatible cron function.

    Usage with APScheduler::

        scheduler.add_job(
            check_and_send_followups,
            "interval",
            seconds=1800,
            id="negotiation_followup",
        )
    """
    scheduler = get_followup_scheduler()
    return await scheduler.check_and_send()
