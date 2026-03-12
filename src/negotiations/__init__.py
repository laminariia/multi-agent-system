"""Negotiation Engine — automated client negotiation management."""

from src.negotiations.engine import NegotiationEngine
from src.negotiations.models import Negotiation
from src.negotiations.sales_conversation import (
    SALES_TRANSITIONS,
    ClientMessage,
    ConceptData,
    SalesAction,
    SalesConversationEngine,
    SalesStage,
)
from src.negotiations.state_machine import (
    HITL_REQUIRED_TRANSITIONS,
    VALID_TRANSITIONS,
    InvalidTransitionError,
    NegotiationState,
    NegotiationStateMachine,
)

__all__ = [
    "ClientMessage",
    "ConceptData",
    "HITL_REQUIRED_TRANSITIONS",
    "InvalidTransitionError",
    "Negotiation",
    "NegotiationEngine",
    "NegotiationState",
    "NegotiationStateMachine",
    "SALES_TRANSITIONS",
    "SalesAction",
    "SalesConversationEngine",
    "SalesStage",
    "VALID_TRANSITIONS",
]
