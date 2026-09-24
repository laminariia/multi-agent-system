"""Unit tests for Negotiation State Machine (src/negotiations/state_machine.py).

Tests cover: all 9 states, 18 valid transitions, invalid transitions,
terminal states, HITL-required transitions, operator override, history,
and edge cases.
"""

from __future__ import annotations

import pytest

from src.negotiations.state_machine import (
    HITL_REQUIRED_TRANSITIONS,
    VALID_TRANSITIONS,
    InvalidTransitionError,
    NegotiationState,
    NegotiationStateMachine,
)

# ---------------------------------------------------------------------------
# NegotiationState enum
# ---------------------------------------------------------------------------


class TestNegotiationState:
    """Verify all 9 states exist per spec."""

    def test_initial(self):
        assert NegotiationState.INITIAL == "initial"

    def test_qualifying(self):
        assert NegotiationState.QUALIFYING == "qualifying"

    def test_proposing(self):
        assert NegotiationState.PROPOSING == "proposing"

    def test_negotiating(self):
        assert NegotiationState.NEGOTIATING == "negotiating"

    def test_closing(self):
        assert NegotiationState.CLOSING == "closing"

    def test_accepted(self):
        assert NegotiationState.ACCEPTED == "accepted"

    def test_declined(self):
        assert NegotiationState.DECLINED == "declined"

    def test_stale(self):
        assert NegotiationState.STALE == "stale"

    def test_operator_override(self):
        assert NegotiationState.OPERATOR_OVERRIDE == "operator_override"

    def test_total_count(self):
        assert len(NegotiationState) == 9


# ---------------------------------------------------------------------------
# VALID_TRANSITIONS
# ---------------------------------------------------------------------------


class TestValidTransitions:
    """Verify transition matrix matches spec."""

    def test_initial_transitions(self):
        targets = VALID_TRANSITIONS["initial"]
        assert "qualifying" in targets
        assert "negotiating" in targets
        assert "accepted" in targets
        assert "declined" in targets
        assert "stale" in targets
        assert "operator_override" in targets

    def test_qualifying_transitions(self):
        targets = VALID_TRANSITIONS["qualifying"]
        assert "proposing" in targets
        assert "negotiating" in targets
        assert "accepted" in targets
        assert "declined" in targets
        assert "operator_override" in targets

    def test_proposing_transitions(self):
        targets = VALID_TRANSITIONS["proposing"]
        assert "closing" in targets
        assert "negotiating" in targets
        assert "declined" in targets
        assert "operator_override" in targets

    def test_negotiating_transitions(self):
        targets = VALID_TRANSITIONS["negotiating"]
        assert "closing" in targets
        assert "declined" in targets
        assert "operator_override" in targets

    def test_closing_transitions(self):
        targets = VALID_TRANSITIONS["closing"]
        assert "accepted" in targets
        assert "negotiating" in targets
        assert "operator_override" in targets

    def test_stale_transitions(self):
        targets = VALID_TRANSITIONS["stale"]
        assert "qualifying" in targets
        assert "declined" in targets
        assert "operator_override" in targets

    def test_operator_override_can_go_anywhere(self):
        targets = VALID_TRANSITIONS["operator_override"]
        for state in NegotiationState:
            if state != NegotiationState.OPERATOR_OVERRIDE:
                assert state.value in targets

    def test_terminal_states_have_no_transitions(self):
        """Accepted and declined are terminal — no outgoing transitions except operator_override."""
        assert "accepted" not in VALID_TRANSITIONS or VALID_TRANSITIONS.get("accepted") == {"operator_override"}
        assert "declined" not in VALID_TRANSITIONS or VALID_TRANSITIONS.get("declined") == {"operator_override"}


# ---------------------------------------------------------------------------
# NegotiationStateMachine — basic operations
# ---------------------------------------------------------------------------


class TestStateMachineBasic:
    """Basic state machine operations."""

    def test_initial_state_default(self):
        sm = NegotiationStateMachine()
        assert sm.current_state == "initial"

    def test_initial_state_custom(self):
        sm = NegotiationStateMachine(current_state="qualifying")
        assert sm.current_state == "qualifying"

    def test_can_transition_valid(self):
        sm = NegotiationStateMachine()
        assert sm.can_transition("qualifying") is True

    def test_can_transition_invalid(self):
        sm = NegotiationStateMachine()
        assert sm.can_transition("closing") is False

    def test_transition_updates_state(self):
        sm = NegotiationStateMachine()
        sm.transition("qualifying", reason="client asked question")
        assert sm.current_state == "qualifying"

    def test_transition_invalid_raises(self):
        sm = NegotiationStateMachine()
        with pytest.raises(InvalidTransitionError):
            sm.transition("closing", reason="skip ahead")

    def test_empty_history_on_init(self):
        sm = NegotiationStateMachine()
        assert sm.history == []


# ---------------------------------------------------------------------------
# Transition history
# ---------------------------------------------------------------------------


class TestStateMachineHistory:
    """Transition history tracking."""

    def test_records_transition(self):
        sm = NegotiationStateMachine()
        sm.transition("qualifying", reason="client question", actor="system")

        assert len(sm.history) == 1
        entry = sm.history[0]
        assert entry["from"] == "initial"
        assert entry["to"] == "qualifying"
        assert entry["reason"] == "client question"
        assert entry["actor"] == "system"
        assert "timestamp" in entry

    def test_multiple_transitions(self):
        sm = NegotiationStateMachine()
        sm.transition("qualifying", reason="question")
        sm.transition("proposing", reason="answered all questions")
        sm.transition("closing", reason="client agreed")
        sm.transition("accepted", reason="final confirmation")

        assert len(sm.history) == 4
        assert sm.current_state == "accepted"
        assert sm.history[0]["to"] == "qualifying"
        assert sm.history[3]["to"] == "accepted"

    def test_default_actor_is_system(self):
        sm = NegotiationStateMachine()
        sm.transition("qualifying", reason="test")
        assert sm.history[0]["actor"] == "system"


# ---------------------------------------------------------------------------
# Terminal states
# ---------------------------------------------------------------------------


class TestTerminalStates:
    """Accepted and declined are terminal states."""

    def test_accepted_is_terminal(self):
        sm = NegotiationStateMachine(current_state="closing")
        sm.transition("accepted", reason="confirmed")
        assert sm.is_terminal is True

    def test_declined_is_terminal(self):
        sm = NegotiationStateMachine(current_state="initial")
        sm.transition("declined", reason="client refused")
        assert sm.is_terminal is True

    def test_non_terminal_states(self):
        for state in ["initial", "qualifying", "proposing", "negotiating", "closing", "stale"]:
            sm = NegotiationStateMachine(current_state=state)
            assert sm.is_terminal is False

    def test_terminal_allows_operator_override(self):
        sm = NegotiationStateMachine(current_state="accepted")
        assert sm.can_transition("operator_override") is True


# ---------------------------------------------------------------------------
# Operator override
# ---------------------------------------------------------------------------


class TestOperatorOverride:
    """Operator override special transitions."""

    def test_any_state_to_operator_override(self):
        for state in ["initial", "qualifying", "proposing", "negotiating", "closing", "stale"]:
            sm = NegotiationStateMachine(current_state=state)
            assert sm.can_transition("operator_override") is True

    def test_operator_override_to_any_state(self):
        sm = NegotiationStateMachine(current_state="operator_override")
        for target in ["initial", "qualifying", "proposing", "negotiating", "closing", "accepted", "declined", "stale"]:
            assert sm.can_transition(target) is True

    def test_operator_override_round_trip(self):
        sm = NegotiationStateMachine(current_state="qualifying")
        sm.transition("operator_override", reason="operator took control", actor="operator")
        sm.transition("proposing", reason="operator set new state", actor="operator")
        assert sm.current_state == "proposing"
        assert len(sm.history) == 2


# ---------------------------------------------------------------------------
# HITL-required transitions
# ---------------------------------------------------------------------------


class TestHITLRequiredTransitions:
    """Certain transitions require HITL approval per spec."""

    def test_initial_to_negotiating_requires_hitl(self):
        assert ("initial", "negotiating") in HITL_REQUIRED_TRANSITIONS

    def test_qualifying_to_negotiating_requires_hitl(self):
        assert ("qualifying", "negotiating") in HITL_REQUIRED_TRANSITIONS

    def test_proposing_to_negotiating_requires_hitl(self):
        assert ("proposing", "negotiating") in HITL_REQUIRED_TRANSITIONS

    def test_closing_to_negotiating_requires_hitl(self):
        assert ("closing", "negotiating") in HITL_REQUIRED_TRANSITIONS

    def test_requires_hitl_method(self):
        sm = NegotiationStateMachine()
        assert sm.requires_hitl("negotiating") is True
        assert sm.requires_hitl("qualifying") is False


# ---------------------------------------------------------------------------
# Full negotiation flows
# ---------------------------------------------------------------------------


class TestNegotiationFlows:
    """End-to-end negotiation scenarios."""

    def test_happy_path_quick_hire(self):
        sm = NegotiationStateMachine()
        sm.transition("accepted", reason="client hired directly")
        assert sm.current_state == "accepted"
        assert sm.is_terminal is True

    def test_full_negotiation_flow(self):
        sm = NegotiationStateMachine()
        sm.transition("qualifying", reason="client asked question")
        sm.transition("proposing", reason="answered questions")
        sm.transition("negotiating", reason="client counter-offered")
        sm.transition("closing", reason="agreed on terms")
        sm.transition("accepted", reason="final confirmation")
        assert sm.current_state == "accepted"
        assert len(sm.history) == 5

    def test_stale_and_return(self):
        sm = NegotiationStateMachine()
        sm.transition("stale", reason="no response 14 days")
        sm.transition("qualifying", reason="client returned")
        assert sm.current_state == "qualifying"

    def test_rejection_from_qualifying(self):
        sm = NegotiationStateMachine()
        sm.transition("qualifying", reason="client asked question")
        sm.transition("declined", reason="client chose another freelancer")
        assert sm.is_terminal is True

    def test_stale_auto_decline(self):
        sm = NegotiationStateMachine()
        sm.transition("stale", reason="no response")
        sm.transition("declined", reason="auto-archive 14 days")
        assert sm.is_terminal is True

    def test_cannot_skip_to_closing_from_initial(self):
        sm = NegotiationStateMachine()
        with pytest.raises(InvalidTransitionError):
            sm.transition("closing", reason="skip")
