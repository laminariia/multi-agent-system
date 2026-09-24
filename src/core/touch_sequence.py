"""Touch Sequence Manager — automated follow-up system for unresponsive leads.

Manages a series of touch points (follow-ups) when leads don't respond
to initial contact. Multi-channel strategy: Telegram → Email → alternative.

Spec: docs/Full_work/specs/sales-agent-spec.md §Touch Sequence Manager
Plan: docs/plans/2026-03-11-touch-sequence-plan.md
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Enums & Constants
# ---------------------------------------------------------------------------


class TouchState(StrEnum):
    """Touch sequence lifecycle states."""

    PENDING = "pending"
    ACTIVE = "active"
    REPLIED = "replied"
    COMPLETED = "completed"
    STOPPED = "stopped"
    PAUSED = "paused"


_TERMINAL_STATES = frozenset({TouchState.REPLIED, TouchState.COMPLETED, TouchState.STOPPED})
_SKIP_STATES = _TERMINAL_STATES | {TouchState.PAUSED}


@dataclass(slots=True)
class TouchStep:
    """A single step in the touch schedule."""

    day: int
    channel: str
    template: str
    description: str
    auto_send: bool = False
    text_template: str | None = None


TOUCH_SCHEDULE: list[TouchStep] = [
    TouchStep(
        day=1,
        channel="best_available",
        template="first_contact",
        description="Первое касание через лучший канал",
        auto_send=False,
    ),
    TouchStep(
        day=3,
        channel="same_as_first",
        template="gentle_followup",
        description="Мягкий follow-up через тот же канал",
        auto_send=True,
        text_template=(
            "Привет! Написал пару дней назад по поводу {business_name}. Может есть вопросы? Буду рад помочь."
        ),
    ),
    TouchStep(
        day=5,
        channel="alternative",
        template="alternative_channel",
        description="Попытка через альтернативный канал",
        auto_send=True,
        text_template=(
            "Добрый день! Это {operator_name}. Пытался связаться в {first_channel}. "
            "Заметил, что у {business_name} есть потенциал для роста через сайт — "
            "хотел бы обсудить, если интересно."
        ),
    ),
    TouchStep(
        day=10,
        channel="email",
        template="final_touch",
        description="Финальное касание — без давления",
        auto_send=True,
        text_template=(
            "Привет! Это финальное сообщение. Если вам интересна цифровизация "
            "{business_name} — всегда рад вернуться к разговору. Удачи!"
        ),
    ),
]


STOP_RULES: dict[str, dict[str, Any]] = {
    "explicit_no": {
        "triggers": ["не интересно", "нет", "not interested", "no thanks", "unsubscribe"],
        "action": "stop_forever",
        "lead_status": "declined",
        "description": "Клиент явно отказал — НИКОГДА больше не писать",
    },
    "max_touches": {
        "max_count": 3,
        "action": "stop",
        "lead_status": "no_response",
        "description": "Исчерпаны все попытки",
    },
    "bounce": {
        "triggers": ["hard_bounce", "privacy_restricted", "user_not_found"],
        "action": "stop",
        "lead_status": "no_contact",
        "description": "Технически невозможно связаться",
    },
    "replied": {
        "action": "transition_to_sales",
        "description": "Клиент ответил → передать SalesAgent",
    },
}


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class StopResult:
    """Result of a stop rule check."""

    rule: str
    action: str
    lead_status: str = ""
    description: str | None = None


@dataclass(slots=True)
class TouchResult:
    """Result of a touch sequence advance operation."""

    lead_id: str
    action: str
    reason: str | None = None
    channel: str | None = None


@dataclass(slots=True)
class TouchSequence:
    """In-memory representation of a lead's touch sequence."""

    lead_id: str
    state: TouchState = TouchState.PENDING
    touch_count: int = 0
    channel_used: str | None = None
    history: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------


class TouchSequenceManager:
    """Manages touch sequences for leads without response.

    Core logic for the follow-up system. DB integration and cron
    scheduling are handled by the caller (API layer / worker).
    """

    def get_next_step(self, touch_count: int) -> TouchStep | None:
        """Return the next touch step for the given count, or None if exhausted."""
        if touch_count < 0 or touch_count >= len(TOUCH_SCHEDULE):
            return None
        return TOUCH_SCHEDULE[touch_count]

    def should_stop(self, touch_count: int, last_message: str | None) -> StopResult | None:
        """Check stop rules against current state. Returns StopResult or None."""
        if last_message:
            normalized = last_message.lower().strip()

            # Check explicit_no triggers
            rule = STOP_RULES["explicit_no"]
            for trigger in rule["triggers"]:
                if trigger in normalized:
                    return StopResult(
                        rule="explicit_no",
                        action=rule["action"],
                        lead_status=rule["lead_status"],
                        description=rule["description"],
                    )

            # Check bounce triggers
            rule = STOP_RULES["bounce"]
            for trigger in rule["triggers"]:
                if trigger in normalized:
                    return StopResult(
                        rule="bounce",
                        action=rule["action"],
                        lead_status=rule["lead_status"],
                        description=rule["description"],
                    )

        # Check max_touches
        rule = STOP_RULES["max_touches"]
        if touch_count >= rule["max_count"]:
            return StopResult(
                rule="max_touches",
                action=rule["action"],
                lead_status=rule["lead_status"],
                description=rule["description"],
            )

        return None

    def resolve_channel(self, step: TouchStep, lead: Any) -> str:
        """Determine the communication channel for this step."""
        channel_type = step.channel

        if channel_type == "best_available":
            if getattr(lead, "telegram_username", None):
                return "telegram"
            return "email"

        if channel_type == "same_as_first":
            return getattr(lead, "channel_used", None) or "email"

        if channel_type == "alternative":
            used = getattr(lead, "channel_used", None)
            if used == "telegram" and getattr(lead, "email", None):
                return "email"
            if used == "email" and getattr(lead, "telegram_username", None):
                return "telegram"
            return used or "email"

        # Explicit channel name (e.g. "email")
        return channel_type

    def format_message(self, step: TouchStep, lead: Any, operator_name: str) -> str | None:
        """Format the touch message template with lead data. Returns None if no template."""
        if not step.text_template:
            return None

        return step.text_template.format(
            business_name=getattr(lead, "business_name", ""),
            operator_name=operator_name,
            first_channel=getattr(lead, "channel_used", None) or "Telegram",
        )

    def create_sequence(self, lead_id: str) -> TouchSequence:
        """Create a new touch sequence in PENDING state."""
        return TouchSequence(lead_id=lead_id)

    def advance(self, sequence: TouchSequence, lead: Any) -> TouchResult:
        """Advance the sequence to the next touch step.

        Handles state transitions, channel resolution, and history recording.
        Returns TouchResult describing what happened.
        """
        # Skip terminal and paused states
        if sequence.state in _SKIP_STATES:
            return TouchResult(
                lead_id=sequence.lead_id,
                action="skipped",
                reason=str(sequence.state),
            )

        # Get next step
        step = self.get_next_step(sequence.touch_count)
        if step is None:
            sequence.state = TouchState.COMPLETED
            return TouchResult(lead_id=sequence.lead_id, action="completed")

        # Resolve channel
        channel = self.resolve_channel(step, lead)

        # Activate sequence on first touch
        if sequence.state == TouchState.PENDING:
            sequence.state = TouchState.ACTIVE

        # Record in history
        sequence.history.append(
            {
                "step_index": sequence.touch_count,
                "template": step.template,
                "channel": channel,
                "day": step.day,
            }
        )

        # Set channel_used on first touch
        if sequence.channel_used is None:
            sequence.channel_used = channel

        # Increment touch count
        sequence.touch_count += 1

        # Check if this was the last step
        if sequence.touch_count >= len(TOUCH_SCHEDULE):
            sequence.state = TouchState.COMPLETED
            logger.info(
                "touch.sequence_completed",
                lead_id=sequence.lead_id,
                total_touches=sequence.touch_count,
            )
            return TouchResult(
                lead_id=sequence.lead_id,
                action="completed",
                channel=channel,
            )

        # Determine action based on auto_send
        if step.auto_send:
            action = "sent"
        else:
            action = "needs_manual"

        logger.info(
            "touch.advanced",
            lead_id=sequence.lead_id,
            step=step.template,
            channel=channel,
            action=action,
            touch_count=sequence.touch_count,
        )

        return TouchResult(
            lead_id=sequence.lead_id,
            action=action,
            channel=channel,
        )

    # ------------------------------------------------------------------
    # DB-persistence helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _sequence_from_lead(lead: Any) -> TouchSequence:
        """Build an in-memory TouchSequence from a Lead ORM instance."""
        state_raw = getattr(lead, "touch_state", None) or TouchState.PENDING
        try:
            state = TouchState(state_raw)
        except ValueError:
            state = TouchState.PENDING

        return TouchSequence(
            lead_id=str(lead.id),
            state=state,
            touch_count=getattr(lead, "touch_count", 0) or 0,
            channel_used=getattr(lead, "channel_used", None),
        )

    @staticmethod
    def _compute_next_touch_at(touch_count: int) -> datetime | None:
        """Compute ``next_touch_at`` based on next step's day offset."""
        if touch_count < 0 or touch_count >= len(TOUCH_SCHEDULE):
            return None
        step = TOUCH_SCHEDULE[touch_count]
        # Previous step day (or 0 for first step)
        prev_day = TOUCH_SCHEDULE[touch_count - 1].day if touch_count > 0 else 0
        delta_days = step.day - prev_day
        return datetime.now(UTC) + timedelta(days=max(delta_days, 1))

    async def check_and_execute(self) -> list[TouchResult]:
        """Cron entry point: find due leads and advance their touch sequences.

        Queries leads where ``touch_state`` IN ('pending', 'active') AND
        ``next_touch_at <= now()``.  For each lead, advances the sequence,
        persists a ``TouchHistory`` record, and updates the lead fields.

        Returns a list of TouchResult objects describing what happened.
        """
        from datetime import UTC  # noqa: PLC0415
        from datetime import datetime as dt

        from sqlalchemy import select  # noqa: PLC0415

        from src.core.database import get_db_session  # noqa: PLC0415
        from src.core.models import Lead  # noqa: PLC0415

        results: list[TouchResult] = []
        now = dt.now(UTC)

        async with get_db_session() as session:
            # Find leads due for a touch
            stmt = (
                select(Lead)
                .where(
                    Lead.touch_state.in_([TouchState.PENDING, TouchState.ACTIVE]),
                    Lead.next_touch_at <= now,
                )
                .with_for_update(skip_locked=True)
                .limit(100)
            )
            rows = await session.execute(stmt)
            leads = rows.scalars().all()

            for lead in leads:
                try:
                    result = await self._process_single_lead(session, lead, now)
                    results.append(result)
                except Exception:  # noqa: BLE001
                    logger.exception("touch.lead_error", lead_id=str(lead.id))

        logger.info("touch.check_and_execute_done", processed=len(results))
        return results

    async def _process_single_lead(
        self,
        session: Any,
        lead: Any,
        now: Any,
    ) -> TouchResult:
        """Process a single lead: advance, persist, apply stop rules."""
        from src.core.models import TouchHistory  # noqa: PLC0415

        sequence = self._sequence_from_lead(lead)

        # Check stop rules before advancing
        last_msg: str | None = None
        if hasattr(lead, "touch_history") and lead.touch_history:
            # Use the most recent touch history error_message as signal
            latest = max(lead.touch_history, key=lambda th: th.created_at)
            last_msg = latest.error_message

        stop = self.should_stop(sequence.touch_count, last_msg)
        if stop is not None:
            # Apply stop rule
            if stop.action == "transition_to_sales":
                lead.touch_state = TouchState.REPLIED
                lead.status = "replied"
            else:
                lead.touch_state = TouchState.STOPPED
                if stop.lead_status:
                    lead.status = stop.lead_status
            lead.next_touch_at = None

            logger.info(
                "touch.stopped",
                lead_id=str(lead.id),
                rule=stop.rule,
                action=stop.action,
            )
            return TouchResult(
                lead_id=str(lead.id),
                action="stopped",
                reason=stop.rule,
            )

        # Advance the sequence
        result = self.advance(sequence, lead)

        # Get the step that was just executed for history
        executed_step_index = sequence.touch_count - 1
        step = self.get_next_step(executed_step_index)

        # Format message content
        content: str | None = None
        if step is not None:
            content = self.format_message(step, lead, operator_name="MAS")

        # Save TouchHistory record
        touch_record = TouchHistory(
            lead_id=lead.id,
            step_index=executed_step_index,
            template=step.template if step else "unknown",
            channel=result.channel or "email",
            content=content,
            status="sent" if result.action == "sent" else "pending",
        )
        session.add(touch_record)

        # Update lead fields
        lead.touch_state = str(sequence.state)
        lead.touch_count = sequence.touch_count
        lead.last_contacted_at = now
        if sequence.channel_used:
            lead.channel_used = sequence.channel_used

        # Compute next_touch_at for the following step
        if sequence.state in _TERMINAL_STATES:
            lead.next_touch_at = None
        else:
            lead.next_touch_at = self._compute_next_touch_at(sequence.touch_count)

        await session.flush()

        logger.info(
            "touch.persisted",
            lead_id=str(lead.id),
            action=result.action,
            touch_count=sequence.touch_count,
            next_touch_at=str(lead.next_touch_at) if lead.next_touch_at else None,
        )
        return result


# ---------------------------------------------------------------------------
# Module-level APScheduler entry point
# ---------------------------------------------------------------------------

_manager = TouchSequenceManager()


async def check_and_execute_touches() -> list[TouchResult]:
    """APScheduler-compatible entry point (every 30min).

    Usage with APScheduler::

        scheduler.add_job(
            check_and_execute_touches,
            trigger="interval",
            minutes=30,
            id="touch_sequence_check",
        )
    """
    return await _manager.check_and_execute()
