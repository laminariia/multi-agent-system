"""Delivery Scheduler — generates scheduled progress messages.

Part of P1.3 Execution Cloaking.  When a delivery is held (pipeline
completes before ``min_delivery_at``), this module creates a series of
progress-update messages spread across the remaining window so the client
sees realistic activity.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Progress message templates (rotated based on position in window)
# ---------------------------------------------------------------------------

_PROGRESS_TEMPLATES: list[str] = [
    "Started working on the project. Reviewing requirements and setting up the environment.",
    "Making good progress — core structure is taking shape.",
    "Backend logic is coming together. Running initial tests.",
    "Frontend components are being implemented. Iterating on the design.",
    "Integration work in progress. Connecting all the pieces.",
    "Running quality checks and fixing edge cases.",
    "Final polish — cleaning up code and preparing deliverables.",
    "Almost ready. Doing a final review before delivery.",
]


def generate_scheduled_messages(
    project_id: str,
    thread_id: str,
    min_delivery_at: datetime,
    *,
    channel: str = "platform",
) -> list:
    """Generate progress-update messages spread across the delivery window.

    Returns a list of ``ScheduledMessage`` model instances (not yet persisted).
    The caller is responsible for adding them to a DB session.
    """
    from src.core.models import ScheduledMessage  # noqa: PLC0415

    now = datetime.now(tz=UTC)

    # Ensure timezone-aware
    if min_delivery_at.tzinfo is None:
        min_delivery_at = min_delivery_at.replace(tzinfo=UTC)

    window = min_delivery_at - now
    total_hours = max(window.total_seconds() / 3600, 1)

    # Determine message count: ~1 per 8 hours, minimum 2, maximum 8
    count = max(2, min(int(total_hours / 8) + 1, len(_PROGRESS_TEMPLATES)))

    # Spread messages evenly across the window (excluding the very start/end)
    interval = window / (count + 1)

    messages: list[ScheduledMessage] = []
    for i in range(count):
        send_at = now + interval * (i + 1)
        # Clamp to not exceed min_delivery_at
        if send_at > min_delivery_at:
            send_at = min_delivery_at - timedelta(minutes=5)

        template_idx = int(i * len(_PROGRESS_TEMPLATES) / count)
        content = _PROGRESS_TEMPLATES[template_idx]

        msg = ScheduledMessage(
            id=uuid.uuid4(),
            project_id=project_id,
            thread_id=thread_id,
            send_at=send_at,
            content=content,
            channel=channel,
            status="pending",
        )
        messages.append(msg)

    return messages
