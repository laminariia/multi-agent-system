"""Negotiation data models (not ORM — in-memory for Phase 2).

Spec: docs/Full_work/specs/negotiation-spec.md
Plan: docs/plans/2026-03-11-negotiation-engine-plan.md
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from src.negotiations.state_machine import NegotiationStateMachine


@dataclass
class Negotiation:
    """A single negotiation lifecycle instance."""

    id: str
    bid_id: str
    platform: str
    state_machine: NegotiationStateMachine
    messages: list[dict] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def state(self) -> str:
        """Current negotiation state (delegates to state machine)."""
        return self.state_machine.current_state
