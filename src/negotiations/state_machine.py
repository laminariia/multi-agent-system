"""Negotiation State Machine — pure state transition logic.

Implements the 9-state negotiation lifecycle per negotiation-spec.md.
No database, no I/O — just state validation, transition rules, and history.

Spec: docs/Full_work/specs/negotiation-spec.md
Plan: docs/plans/2026-03-11-negotiation-engine-plan.md
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum


class NegotiationState(StrEnum):
    """9 negotiation lifecycle states per spec."""

    INITIAL = "initial"
    QUALIFYING = "qualifying"
    PROPOSING = "proposing"
    NEGOTIATING = "negotiating"
    CLOSING = "closing"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    STALE = "stale"
    OPERATOR_OVERRIDE = "operator_override"


# ---------------------------------------------------------------------------
# Transition matrix — spec-defined valid state transitions
# ---------------------------------------------------------------------------

VALID_TRANSITIONS: dict[str, set[str]] = {
    "initial": {
        "qualifying",
        "negotiating",
        "accepted",
        "declined",
        "stale",
        "operator_override",
    },
    "qualifying": {
        "proposing",
        "negotiating",
        "accepted",
        "declined",
        "operator_override",
    },
    "proposing": {
        "closing",
        "negotiating",
        "declined",
        "operator_override",
    },
    "negotiating": {
        "closing",
        "declined",
        "operator_override",
    },
    "closing": {
        "accepted",
        "negotiating",
        "operator_override",
    },
    "accepted": {
        "operator_override",
    },
    "declined": {
        "operator_override",
    },
    "stale": {
        "qualifying",
        "declined",
        "operator_override",
    },
    "operator_override": {state.value for state in NegotiationState if state != NegotiationState.OPERATOR_OVERRIDE},
}

_TERMINAL_STATES: frozenset[str] = frozenset({"accepted", "declined"})

# ---------------------------------------------------------------------------
# HITL-required transitions — need human approval before executing
# ---------------------------------------------------------------------------

HITL_REQUIRED_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("initial", "negotiating"),
        ("qualifying", "negotiating"),
        ("proposing", "negotiating"),
        ("closing", "negotiating"),
    }
)


class InvalidTransitionError(Exception):
    """Raised when a state transition violates the transition matrix."""

    def __init__(self, from_state: str, to_state: str) -> None:
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(f"Invalid transition: {from_state} → {to_state}")


class NegotiationStateMachine:
    """Pure state machine for negotiation lifecycle.

    Parameters
    ----------
    current_state:
        Starting state. Defaults to ``"initial"``.
    """

    def __init__(self, current_state: str = "initial") -> None:
        self._current_state = current_state
        self._history: list[dict] = []

    @property
    def current_state(self) -> str:
        return self._current_state

    @property
    def history(self) -> list[dict]:
        return self._history

    @property
    def is_terminal(self) -> bool:
        """True if current state is terminal (accepted or declined)."""
        return self._current_state in _TERMINAL_STATES

    def can_transition(self, target: str) -> bool:
        """Check whether transitioning to *target* is valid."""
        allowed = VALID_TRANSITIONS.get(self._current_state, set())
        return target in allowed

    def requires_hitl(self, target: str) -> bool:
        """Check whether transitioning to *target* requires HITL approval."""
        return (self._current_state, target) in HITL_REQUIRED_TRANSITIONS

    def transition(
        self,
        target: str,
        *,
        reason: str,
        actor: str = "system",
    ) -> None:
        """Execute a state transition.

        Raises
        ------
        InvalidTransitionError
            If the transition is not in the valid transition matrix.
        """
        if not self.can_transition(target):
            raise InvalidTransitionError(self._current_state, target)

        entry = {
            "from": self._current_state,
            "to": target,
            "reason": reason,
            "actor": actor,
            "timestamp": datetime.now(UTC).isoformat(),
        }

        self._current_state = target
        self._history.append(entry)
