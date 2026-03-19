"""Notification data models.

Defines the :class:`Severity` enum and the :class:`Notification` dataclass that
flow through the notification routing system.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class Severity(StrEnum):
    """Alert severity levels controlling routing behaviour.

    Routing rules:
        * ``CRITICAL`` — Telegram immediately + dashboard
        * ``ERROR``    — Telegram immediately + dashboard
        * ``WARNING``  — Batched (30-min window) + dashboard
        * ``INFO``     — Dashboard only
    """

    CRITICAL = "critical"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


# Ordered from most to least severe for comparison helpers.
_SEVERITY_ORDER: dict[Severity, int] = {
    Severity.CRITICAL: 4,
    Severity.ERROR: 3,
    Severity.WARNING: 2,
    Severity.INFO: 1,
}


@dataclass(frozen=True, slots=True)
class Notification:
    """Immutable notification payload routed through the system.

    Attributes:
        id: Unique identifier (auto-generated UUID4).
        severity: Alert severity controlling channel routing.
        title: Short human-readable summary (max ~120 chars recommended).
        message: Detailed description of the event.
        source: Originating subsystem (e.g. ``"heartbeat"``, ``"circuit_breaker"``).
        agent_name: Agent that triggered the notification, if applicable.
        thread_id: LangGraph thread ID, if applicable.
        metadata: Arbitrary key-value data attached to the notification.
        created_at: UTC timestamp of creation.
    """

    severity: Severity
    title: str
    message: str
    source: str = ""
    agent_name: str = ""
    thread_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-friendly dictionary."""
        return {
            "id": self.id,
            "severity": self.severity.value,
            "title": self.title,
            "message": self.message,
            "source": self.source,
            "agent_name": self.agent_name,
            "thread_id": self.thread_id,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
        }

    @property
    def severity_order(self) -> int:
        """Numeric severity for comparison (higher = more severe)."""
        return _SEVERITY_ORDER.get(self.severity, 0)
