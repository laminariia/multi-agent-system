"""Negotiation Engine — automated client negotiation management."""

from src.negotiations.classifier import (
    MESSAGE_TYPES,
    ClassificationResult,
    MessageClassifier,
)
from src.negotiations.counter_offer import CounterOfferEngine, CounterOfferPayload
from src.negotiations.engine import NegotiationEngine
from src.negotiations.followup import (
    FOLLOW_UP_SCHEDULE,
    FOLLOW_UP_TEMPLATES,
    FollowUpResult,
    FollowUpScheduler,
    FollowUpStep,
    check_and_send_followups,
    get_followup_scheduler,
)
from src.negotiations.models import Negotiation
from src.negotiations.poller import MessagePoller, poll_all_active_negotiations
from src.negotiations.response_generator import (
    AUTO_SEND_HANDLERS,
    GeneratedResponse,
    ResponseGenerator,
)
from src.negotiations.sales_conversation import (
    SALES_TRANSITIONS,
    ClientMessage,
    ConceptData,
    SalesAction,
    SalesConversationEngine,
    SalesStage,
)
from src.negotiations.scope_change import (
    ScopeChangeHandler,
    ScopeChangeOption,
    ScopeChangePayload,
)
from src.negotiations.state_machine import (
    HITL_REQUIRED_TRANSITIONS,
    VALID_TRANSITIONS,
    InvalidTransitionError,
    NegotiationState,
    NegotiationStateMachine,
)

__all__ = [
    "AUTO_SEND_HANDLERS",
    "ClassificationResult",
    "ClientMessage",
    "ConceptData",
    "CounterOfferEngine",
    "CounterOfferPayload",
    "FOLLOW_UP_SCHEDULE",
    "FOLLOW_UP_TEMPLATES",
    "FollowUpResult",
    "FollowUpScheduler",
    "FollowUpStep",
    "GeneratedResponse",
    "HITL_REQUIRED_TRANSITIONS",
    "InvalidTransitionError",
    "MESSAGE_TYPES",
    "MessageClassifier",
    "MessagePoller",
    "Negotiation",
    "NegotiationEngine",
    "NegotiationState",
    "NegotiationStateMachine",
    "ResponseGenerator",
    "SALES_TRANSITIONS",
    "SalesAction",
    "SalesConversationEngine",
    "SalesStage",
    "ScopeChangeHandler",
    "ScopeChangeOption",
    "ScopeChangePayload",
    "VALID_TRANSITIONS",
    "check_and_send_followups",
    "get_followup_scheduler",
    "poll_all_active_negotiations",
]
