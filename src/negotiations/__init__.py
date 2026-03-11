"""Negotiation Engine — automated client negotiation management."""

from src.negotiations.engine import NegotiationEngine
from src.negotiations.models import Negotiation
from src.negotiations.state_machine import (
    HITL_REQUIRED_TRANSITIONS,
    VALID_TRANSITIONS,
    InvalidTransitionError,
    NegotiationState,
    NegotiationStateMachine,
)

__all__ = [
    "HITL_REQUIRED_TRANSITIONS",
    "InvalidTransitionError",
    "Negotiation",
    "NegotiationEngine",
    "NegotiationState",
    "NegotiationStateMachine",
    "VALID_TRANSITIONS",
]
