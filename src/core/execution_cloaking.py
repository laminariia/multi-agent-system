"""Execution Cloaking -- Time-Value Arbitrage system.

Masks AI execution speed to maintain client trust by presenting
realistic human-like delivery timelines.

Three components:
1. **Double Estimation** -- AI real_hours vs market human_days, present
   human_days / 2..3 to client.
2. **Delivery Throttling** -- hold delivery until >= 70% of proposed
   timeline elapsed.  Minimum 24 hours.
3. **Dispatch Loop** -- cron-driven query of ``ScheduledMessage`` rows
   where ``send_at <= now()``; marks them ``sent``.

Spec: docs/Full_work/dev-cycle-spec.md  Phase 4.5
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import structlog

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Constants (from spec)
# ---------------------------------------------------------------------------

#: Minimum delivery window -- spec says "never propose less than 24 hours".
MIN_DELIVERY_HOURS: int = 24

#: Packager will not deliver before ``created_at + proposed_days * ratio``.
DELIVERY_THROTTLE_RATIO: float = 0.7

#: ``proposed_days = human_days / divisor``.  Divisor range 2..3 per spec
#: ("human_days / 2..3 => e.g. 10 days -> 3-5 days proposed").
MARKET_RATE_DIVISOR_MIN: int = 2
MARKET_RATE_DIVISOR_MAX: int = 3

#: Working hours per day for human-equivalent conversion.
_HOURS_PER_DAY: int = 8

# ---------------------------------------------------------------------------
# Progress message templates (rotated based on position in window)
# ---------------------------------------------------------------------------

_PROGRESS_TEMPLATES: list[str] = [
    "Started working on the project. Reviewing requirements and setting up the environment.",
    "Making good progress -- core structure is taking shape.",
    "Backend logic is coming together. Running initial tests.",
    "Frontend components are being implemented. Iterating on the design.",
    "Integration work in progress. Connecting all the pieces.",
    "Running quality checks and fixing edge cases.",
    "Final polish -- cleaning up code and preparing deliverables.",
    "Almost ready. Doing a final review before delivery.",
]


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class CloakingConfig:
    """Tunable parameters for the cloaking system."""

    min_delivery_hours: int = MIN_DELIVERY_HOURS
    throttle_ratio: float = DELIVERY_THROTTLE_RATIO
    divisor_min: int = MARKET_RATE_DIVISOR_MIN
    divisor_max: int = MARKET_RATE_DIVISOR_MAX


@dataclass(slots=True)
class DoubleEstimate:
    """Result of the double-estimation step.

    Attributes:
        real_hours: Actual AI processing time (from Planner).
        human_days: Middle-developer market-rate estimate (from Bid Agent).
        proposed_days: What we propose to the client (``human_days / 2..3``).
    """

    real_hours: float
    human_days: int | float
    proposed_days: int

    @property
    def speedup_ratio(self) -> float:
        """How many times faster AI is vs the human estimate.

        ``(human_days * 8) / real_hours``.  Returns 0.0 when ``real_hours``
        is zero to avoid division by zero.
        """
        human_hours = float(self.human_days) * _HOURS_PER_DAY
        if self.real_hours <= 0:
            return 0.0
        return human_hours / self.real_hours

    @property
    def margin_percentage(self) -> float:
        """Time-value margin: ``(billable - real) / billable * 100``.

        Billable hours = ``proposed_days * 8``.  Returns 0.0 when
        ``proposed_days`` is zero.
        """
        billable = float(self.proposed_days) * _HOURS_PER_DAY
        if billable <= 0:
            return 0.0
        return (billable - self.real_hours) / billable * 100.0


@dataclass(slots=True)
class CloakingResult:
    """Full result of applying execution cloaking to a project."""

    estimate: DoubleEstimate
    min_delivery_at: datetime
    created_at: datetime
    scheduled_message_count: int

    @property
    def delivery_window_hours(self) -> float:
        """Hours between ``created_at`` and ``min_delivery_at``."""
        delta = self.min_delivery_at - self.created_at
        return delta.total_seconds() / 3600.0


@dataclass(slots=True)
class DeliveryHold:
    """Whether delivery should be held and how long remains."""

    is_held: bool
    min_delivery_at: datetime | None
    remaining_hours: float


@dataclass(slots=True)
class ScheduledMessageResult:
    """Result of a dispatch loop run."""

    dispatched_count: int
    messages: list[UUID] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Cover story (from spec -- technically true)
# ---------------------------------------------------------------------------

_COVER_STORY: str = (
    "У нас собственная проприетарная библиотека компонентов, "
    "CLI-инструменты и CI/CD пайплайны, которые мы собирали годами. "
    "Это позволяет пропускать рутину и фокусироваться на бизнес-логике."
)


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------


class ExecutionCloaking:
    """Unified coordinator for time-value arbitrage.

    Usage::

        ec = ExecutionCloaking()
        result = ec.apply(real_hours=2.0, human_days=10, created_at=now)
        # result.estimate.proposed_days -> 4
        # result.min_delivery_at -> now + 2.8 days

        # Or apply directly to LangGraph state dict:
        ec.apply_to_state(state, real_hours=2.0, human_days=10)

        # Check delivery hold at packaging time:
        hold = ec.check_delivery_hold(state["min_delivery_at"])
        if hold.is_held:
            ...  # create HITL delivery_hold entry

        # Dispatch due scheduled messages (called from cron):
        result = await ec.dispatch_due_messages(db_session)
    """

    def __init__(
        self,
        *,
        config: CloakingConfig | None = None,
        llm_client: Any | None = None,
    ) -> None:
        self.config = config or CloakingConfig()
        self._llm_client = llm_client

    # ------------------------------------------------------------------
    # LLM-generated progress messages (static templates as fallback)
    # ------------------------------------------------------------------

    async def _generate_progress_message(
        self,
        project_context: dict[str, Any],
        progress_pct: float,
    ) -> str:
        """Generate a context-aware progress message via LLM.

        Falls back to static ``_PROGRESS_TEMPLATES`` when the LLM client
        is unavailable or the call fails.

        Args:
            project_context: Dict with project details (title, category,
                tech_stack, etc.) for contextual message generation.
            progress_pct: Progress percentage 0.0-100.0 within the
                delivery window.

        Returns:
            A human-readable progress message string.
        """
        if self._llm_client is None:
            return self._fallback_progress_message(progress_pct)

        try:
            from langchain_core.messages import HumanMessage  # noqa: PLC0415

            title = project_context.get("title", "the project")
            category = project_context.get("category", "web development")
            tech_stack = project_context.get("tech_stack", "")

            prompt = (
                f"Generate a brief, natural progress update message for a client. "
                f"Project: {title} (category: {category}). "
                f"Tech stack: {tech_stack}. "
                f"Progress: {progress_pct:.0f}% complete. "
                f"Keep it under 2 sentences, professional, and specific to the "
                f"project type. Do not mention AI or automation."
            )

            response, _metrics = await self._llm_client.call(
                "packager",
                [HumanMessage(content=prompt)],
                temperature=0.8,
                max_tokens=150,
            )
            content = response.content.strip()
            if content and len(content) > 10:
                return content
        except Exception:
            logger.debug(
                "cloaking.llm_progress_fallback",
                reason="llm_call_failed",
                progress_pct=progress_pct,
            )

        return self._fallback_progress_message(progress_pct)

    @staticmethod
    def _fallback_progress_message(progress_pct: float) -> str:
        """Select a static template based on progress percentage.

        Args:
            progress_pct: Progress percentage 0.0-100.0.

        Returns:
            A static progress message from ``_PROGRESS_TEMPLATES``.
        """
        clamped = max(0.0, min(100.0, progress_pct))
        idx = int(clamped / 100.0 * (len(_PROGRESS_TEMPLATES) - 1))
        idx = max(0, min(idx, len(_PROGRESS_TEMPLATES) - 1))
        return _PROGRESS_TEMPLATES[idx]

    # ------------------------------------------------------------------
    # Component 1: Double Estimation
    # ------------------------------------------------------------------

    def compute_double_estimate(
        self,
        *,
        real_hours: float,
        human_days: int | float,
    ) -> DoubleEstimate:
        """Compute the double estimate: AI time vs market rate.

        Args:
            real_hours: Actual AI processing time from Planner.
            human_days: Middle-developer market-rate estimate from Bid Agent.

        Returns:
            ``DoubleEstimate`` with ``proposed_days`` = ``human_days / midpoint(divisor_min, divisor_max)``,
            clamped to a minimum of 1 day (24 hours).
        """
        # Clamp negative inputs
        real_hours = max(real_hours, 0.0)
        human_days_val = max(float(human_days), 0.0)
        human_days_int = max(int(human_days), 0) if human_days >= 0 else 0

        # Calculate proposed days using the midpoint of the divisor range
        divisor_mid = (self.config.divisor_min + self.config.divisor_max) / 2.0
        if divisor_mid <= 0:
            divisor_mid = 2.5  # safe fallback

        raw_proposed = human_days_val / divisor_mid
        # Enforce minimum 1 day (24 hours per spec)
        proposed_days = max(math.ceil(raw_proposed), 1)

        return DoubleEstimate(
            real_hours=real_hours,
            human_days=human_days_int if isinstance(human_days, int) else human_days_val,
            proposed_days=proposed_days,
        )

    # ------------------------------------------------------------------
    # Component 2: Delivery Throttling
    # ------------------------------------------------------------------

    def compute_min_delivery_at(
        self,
        *,
        proposed_days: int,
        created_at: datetime,
    ) -> datetime:
        """Calculate the earliest allowed delivery time.

        ``min_delivery_at = created_at + max(proposed_days * throttle_ratio, min_delivery_hours/24) days``

        Args:
            proposed_days: Number of days proposed to the client.
            created_at: When the project was created / bid submitted.

        Returns:
            Timezone-aware ``datetime`` (UTC).
        """
        # Ensure created_at is timezone-aware
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)

        throttled_days = max(proposed_days, 0) * self.config.throttle_ratio
        min_days = self.config.min_delivery_hours / 24.0

        # Use the larger of throttled days or the absolute minimum
        effective_days = max(throttled_days, min_days)

        return created_at + timedelta(days=effective_days)

    def check_delivery_hold(
        self,
        *,
        min_delivery_at: datetime | None,
    ) -> DeliveryHold:
        """Check whether delivery should be held.

        Args:
            min_delivery_at: The earliest allowed delivery time, or ``None``
                if no cloaking is active.

        Returns:
            ``DeliveryHold`` indicating whether the delivery is held and
            how many hours remain.
        """
        if min_delivery_at is None:
            return DeliveryHold(is_held=False, min_delivery_at=None, remaining_hours=0.0)

        # Handle naive datetimes by assuming UTC
        if min_delivery_at.tzinfo is None:
            min_delivery_at = min_delivery_at.replace(tzinfo=UTC)

        now = datetime.now(tz=UTC)
        if now >= min_delivery_at:
            return DeliveryHold(
                is_held=False,
                min_delivery_at=min_delivery_at,
                remaining_hours=0.0,
            )

        remaining = (min_delivery_at - now).total_seconds() / 3600.0
        return DeliveryHold(
            is_held=True,
            min_delivery_at=min_delivery_at,
            remaining_hours=remaining,
        )

    # ------------------------------------------------------------------
    # Full pipeline
    # ------------------------------------------------------------------

    def apply(
        self,
        *,
        real_hours: float,
        human_days: int | float,
        created_at: datetime | None = None,
    ) -> CloakingResult:
        """Run the full cloaking pipeline: estimate + throttle.

        Args:
            real_hours: Actual AI processing time.
            human_days: Market-rate human estimate.
            created_at: Project creation time (defaults to now).

        Returns:
            ``CloakingResult`` with estimate, min_delivery_at, and message count.
        """
        if created_at is None:
            created_at = datetime.now(tz=UTC)

        # Ensure timezone-aware
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)

        estimate = self.compute_double_estimate(
            real_hours=real_hours,
            human_days=human_days,
        )

        min_delivery_at = self.compute_min_delivery_at(
            proposed_days=estimate.proposed_days,
            created_at=created_at,
        )

        # Determine how many progress messages would be generated
        window_hours = (min_delivery_at - created_at).total_seconds() / 3600.0
        msg_count = max(2, min(int(window_hours / 8) + 1, len(_PROGRESS_TEMPLATES)))

        logger.info(
            "cloaking.applied",
            real_hours=estimate.real_hours,
            human_days=estimate.human_days,
            proposed_days=estimate.proposed_days,
            speedup_ratio=round(estimate.speedup_ratio, 1),
            margin_pct=round(estimate.margin_percentage, 1),
            min_delivery_at=min_delivery_at.isoformat(),
            window_hours=round(window_hours, 1),
            scheduled_messages=msg_count,
        )

        return CloakingResult(
            estimate=estimate,
            min_delivery_at=min_delivery_at,
            created_at=created_at,
            scheduled_message_count=msg_count,
        )

    # ------------------------------------------------------------------
    # State integration helpers
    # ------------------------------------------------------------------

    def apply_to_state(
        self,
        state: dict[str, Any],
        *,
        real_hours: float,
        human_days: int | float,
    ) -> CloakingResult:
        """Apply cloaking and write results directly into a LangGraph state dict.

        Mutates *state* in place (sets ``real_hours``, ``proposed_days``,
        ``min_delivery_at``).

        Args:
            state: The mutable LangGraph state dictionary.
            real_hours: Actual AI processing time.
            human_days: Market-rate human estimate.

        Returns:
            The ``CloakingResult`` for inspection.
        """
        created_at = state.get("created_at") or datetime.now(tz=UTC)
        result = self.apply(
            real_hours=real_hours,
            human_days=human_days,
            created_at=created_at,
        )

        state["real_hours"] = result.estimate.real_hours
        state["proposed_days"] = result.estimate.proposed_days
        state["min_delivery_at"] = result.min_delivery_at

        return result

    def check_delivery_hold_from_state(
        self,
        state: dict[str, Any],
    ) -> DeliveryHold:
        """Check delivery hold using the ``min_delivery_at`` from state.

        Args:
            state: The LangGraph state dictionary.

        Returns:
            ``DeliveryHold`` result.
        """
        return self.check_delivery_hold(
            min_delivery_at=state.get("min_delivery_at"),
        )

    # ------------------------------------------------------------------
    # Scheduled message generation
    # ------------------------------------------------------------------

    def generate_progress_messages(
        self,
        project_id: str,
        thread_id: str,
        min_delivery_at: datetime,
        *,
        channel: str = "platform",
    ) -> list[dict[str, Any]]:
        """Generate progress-update message dicts spread across the delivery window.

        Returns plain dicts (not ORM instances) so the caller can persist
        them however it chooses (ORM, raw SQL, etc.).

        Args:
            project_id: Project identifier.
            thread_id: LangGraph thread identifier.
            min_delivery_at: Earliest allowed delivery time.
            channel: Message channel (default ``"platform"``).

        Returns:
            List of message dicts with keys: ``project_id``, ``thread_id``,
            ``send_at``, ``content``, ``channel``, ``status``.
        """
        now = datetime.now(tz=UTC)

        # Ensure timezone-aware
        if min_delivery_at.tzinfo is None:
            min_delivery_at = min_delivery_at.replace(tzinfo=UTC)

        window = min_delivery_at - now
        total_hours = max(window.total_seconds() / 3600.0, 1.0)

        # ~1 message per 8 hours, minimum 2, maximum 8
        count = max(2, min(int(total_hours / 8) + 1, len(_PROGRESS_TEMPLATES)))

        # Spread messages evenly across window (excluding very start/end)
        interval = window / (count + 1)

        messages: list[dict[str, Any]] = []
        for i in range(count):
            send_at = now + interval * (i + 1)
            # Clamp to not exceed min_delivery_at
            if send_at > min_delivery_at:
                send_at = min_delivery_at - timedelta(minutes=5)

            template_idx = int(i * len(_PROGRESS_TEMPLATES) / count)
            content = _PROGRESS_TEMPLATES[template_idx]

            messages.append(
                {
                    "project_id": project_id,
                    "thread_id": thread_id,
                    "send_at": send_at,
                    "content": content,
                    "channel": channel,
                    "status": "pending",
                }
            )

        return messages

    # ------------------------------------------------------------------
    # Component 3: Dispatch Loop
    # ------------------------------------------------------------------

    async def dispatch_due_messages(
        self,
        session: Any,
        *,
        channels: Any | None = None,
    ) -> ScheduledMessageResult:
        """Query and dispatch scheduled messages where ``send_at <= now()``.

        Marks each matched message as ``status='sent'`` and sets ``sent_at``
        to the current UTC time.  Commits the session.

        When *channels* (a ``ChannelsPlugin`` instance) is provided, a
        WebSocket event is emitted for each dispatched message so the
        dashboard can display real-time progress updates.

        Args:
            session: An async SQLAlchemy session.
            channels: Optional ``ChannelsPlugin`` for WebSocket events.

        Returns:
            ``ScheduledMessageResult`` with count and list of dispatched message IDs.
        """
        from sqlalchemy import select  # noqa: PLC0415

        from src.core.models import ScheduledMessage  # noqa: PLC0415

        now = datetime.now(tz=UTC)

        stmt = (
            select(ScheduledMessage)
            .where(
                ScheduledMessage.status == "pending",
                ScheduledMessage.send_at <= now,
            )
            .order_by(ScheduledMessage.send_at)
        )

        result = await session.execute(stmt)
        due_messages = result.scalars().all()

        dispatched_ids: list[UUID] = []
        for msg in due_messages:
            msg.status = "sent"
            msg.sent_at = datetime.now(tz=UTC)
            dispatched_ids.append(msg.id)

            # Emit WebSocket event for real-time dashboard updates
            if channels is not None:
                await self._emit_dispatch_event(channels, msg)

        await session.commit()

        count = len(dispatched_ids)
        if count > 0:
            logger.info(
                "cloaking.dispatched",
                count=count,
                message_ids=[str(mid) for mid in dispatched_ids],
            )

        return ScheduledMessageResult(
            dispatched_count=count,
            messages=dispatched_ids,
        )

    @staticmethod
    async def _emit_dispatch_event(channels: Any, msg: Any) -> None:
        """Emit a WebSocket event when a scheduled message is dispatched.

        Uses the project WebSocket publish guard pattern -- wraps the call
        in try/except to prevent WebSocket disconnects from breaking the
        dispatch loop.

        Args:
            channels: A ``ChannelsPlugin`` instance.
            msg: The dispatched ``ScheduledMessage`` ORM object.
        """
        try:
            import json  # noqa: PLC0415

            event = {
                "type": "cloaking:message_dispatched",
                "message_id": str(msg.id),
                "project_id": str(getattr(msg, "project_id", "")),
                "content": str(getattr(msg, "content", "")),
                "sent_at": msg.sent_at.isoformat() if msg.sent_at else None,
            }
            payload = json.dumps(event, default=str)
            channels.publish(payload, ["cloaking:dispatched"])
        except (OSError, ConnectionError, Exception):
            logger.debug(
                "cloaking.ws_emit_failed",
                message_id=str(msg.id),
            )

    # ------------------------------------------------------------------
    # Cover story
    # ------------------------------------------------------------------

    @staticmethod
    def get_cover_story() -> str:
        """Return the cover story text for "how so fast?" questions.

        Per spec: "proprietary component library, CLI tools, and CI/CD
        pipelines built over years".  Technically true -- MAS is that tool.
        """
        return _COVER_STORY
