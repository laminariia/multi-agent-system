"""Touch Sequence Manager — automated follow-up system for unresponsive leads.

Manages a series of touch points (follow-ups) when leads don't respond
to initial contact. Multi-channel strategy: Telegram → Email → alternative.

Spec: docs/Full_work/specs/sales-agent-spec.md §Touch Sequence Manager
Plan: docs/plans/2026-03-11-touch-sequence-plan.md
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
