"""Property-based tests for state machine transition invariants.

Verifies the NegotiationStateMachine transition matrix:
- Valid transitions always succeed
- Invalid transitions always raise InvalidTransitionError
- Terminal states have no outgoing transitions (except operator_override)
- History records every transition
- HITL-required transitions are a subset of valid transitions
"""

from __future__ import annotations

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from src.negotiations.state_machine import (
    HITL_REQUIRED_TRANSITIONS,
    VALID_TRANSITIONS,
    InvalidTransitionError,
    NegotiationState,
    NegotiationStateMachine,
)

from .conftest import (
    TERMINAL_NEGOTIATION_STATES,
    negotiation_state_strategy,
    non_terminal_negotiation_strategy,
)

# ---------------------------------------------------------------------------
# Transition matrix completeness
# ---------------------------------------------------------------------------


class TestTransitionMatrixCompleteness:
    """The transition matrix must cover all states."""

    @given(state=negotiation_state_strategy)
    @settings(max_examples=200)
    def test_every_state_has_transitions_entry(self, state: str) -> None:
        """Every defined state has an entry in VALID_TRANSITIONS."""
        assert state in VALID_TRANSITIONS

    @given(state=negotiation_state_strategy)
    @settings(max_examples=200)
    def test_transition_targets_are_valid_states(self, state: str) -> None:
        """All transition targets are members of NegotiationState."""
        valid_names = {s.value for s in NegotiationState}
        for target in VALID_TRANSITIONS[state]:
            assert target in valid_names, f"Unknown target '{target}' from state '{state}'"


# ---------------------------------------------------------------------------
# Valid transitions always succeed
# ---------------------------------------------------------------------------


class TestValidTransitions:
    """Valid transitions must execute without error."""

    @given(data=st.data())
    @settings(max_examples=200)
    def test_valid_transition_succeeds(self, data: st.DataObject) -> None:
        """A transition listed in VALID_TRANSITIONS always succeeds."""
        from_state = data.draw(negotiation_state_strategy)
        targets = list(VALID_TRANSITIONS.get(from_state, set()))
        assume(len(targets) > 0)

        target = data.draw(st.sampled_from(targets))
        sm = NegotiationStateMachine(current_state=from_state)
        sm.transition(target, reason="test", actor="hypothesis")

        assert sm.current_state == target

    @given(data=st.data())
    @settings(max_examples=200)
    def test_can_transition_returns_true_for_valid(self, data: st.DataObject) -> None:
        """can_transition returns True for any valid transition pair."""
        from_state = data.draw(negotiation_state_strategy)
        targets = list(VALID_TRANSITIONS.get(from_state, set()))
        assume(len(targets) > 0)

        target = data.draw(st.sampled_from(targets))
        sm = NegotiationStateMachine(current_state=from_state)
        assert sm.can_transition(target) is True


# ---------------------------------------------------------------------------
# Invalid transitions always fail
# ---------------------------------------------------------------------------


class TestInvalidTransitions:
    """Invalid transitions must raise InvalidTransitionError."""

    @given(
        from_state=negotiation_state_strategy,
        to_state=negotiation_state_strategy,
    )
    @settings(max_examples=200)
    def test_invalid_transition_raises(self, from_state: str, to_state: str) -> None:
        """A transition NOT in VALID_TRANSITIONS raises InvalidTransitionError."""
        allowed = VALID_TRANSITIONS.get(from_state, set())
        assume(to_state not in allowed)

        sm = NegotiationStateMachine(current_state=from_state)
        try:
            sm.transition(to_state, reason="test", actor="hypothesis")
            # If we get here, the transition was unexpectedly valid
            raise AssertionError(f"Expected InvalidTransitionError for {from_state} -> {to_state}")
        except InvalidTransitionError as exc:
            assert exc.from_state == from_state
            assert exc.to_state == to_state

    @given(
        from_state=negotiation_state_strategy,
        to_state=negotiation_state_strategy,
    )
    @settings(max_examples=200)
    def test_can_transition_returns_false_for_invalid(self, from_state: str, to_state: str) -> None:
        """can_transition returns False for any invalid transition pair."""
        allowed = VALID_TRANSITIONS.get(from_state, set())
        assume(to_state not in allowed)

        sm = NegotiationStateMachine(current_state=from_state)
        assert sm.can_transition(to_state) is False


# ---------------------------------------------------------------------------
# Terminal state invariants
# ---------------------------------------------------------------------------


class TestTerminalStates:
    """Terminal states have restricted outgoing transitions."""

    @given(terminal=st.sampled_from(list(TERMINAL_NEGOTIATION_STATES)))
    @settings(max_examples=200)
    def test_terminal_states_are_terminal(self, terminal: str) -> None:
        """Terminal states report is_terminal=True."""
        sm = NegotiationStateMachine(current_state=terminal)
        assert sm.is_terminal is True

    @given(non_terminal=non_terminal_negotiation_strategy)
    @settings(max_examples=200)
    def test_non_terminal_states_are_not_terminal(self, non_terminal: str) -> None:
        """Non-terminal states report is_terminal=False."""
        sm = NegotiationStateMachine(current_state=non_terminal)
        assert sm.is_terminal is False

    @given(terminal=st.sampled_from(list(TERMINAL_NEGOTIATION_STATES)))
    @settings(max_examples=200)
    def test_terminal_only_allows_operator_override(self, terminal: str) -> None:
        """Terminal states only allow transition to operator_override."""
        allowed = VALID_TRANSITIONS[terminal]
        assert allowed == {"operator_override"}


# ---------------------------------------------------------------------------
# History invariants
# ---------------------------------------------------------------------------


class TestHistoryInvariants:
    """History must faithfully record all transitions."""

    @given(data=st.data())
    @settings(max_examples=200)
    def test_history_records_transition(self, data: st.DataObject) -> None:
        """Each successful transition adds exactly one history entry."""
        from_state = data.draw(negotiation_state_strategy)
        targets = list(VALID_TRANSITIONS.get(from_state, set()))
        assume(len(targets) > 0)

        target = data.draw(st.sampled_from(targets))
        sm = NegotiationStateMachine(current_state=from_state)

        assert len(sm.history) == 0
        sm.transition(target, reason="test-reason", actor="test-actor")

        assert len(sm.history) == 1
        entry = sm.history[0]
        assert entry["from"] == from_state
        assert entry["to"] == target
        assert entry["reason"] == "test-reason"
        assert entry["actor"] == "test-actor"
        assert "timestamp" in entry

    @given(data=st.data())
    @settings(max_examples=200)
    def test_history_length_equals_transition_count(self, data: st.DataObject) -> None:
        """After N transitions, history has exactly N entries."""
        sm = NegotiationStateMachine(current_state="initial")
        transition_count = 0

        # Perform a sequence of valid transitions
        for _ in range(data.draw(st.integers(min_value=1, max_value=5))):
            targets = list(VALID_TRANSITIONS.get(sm.current_state, set()))
            if not targets:
                break
            target = data.draw(st.sampled_from(targets))
            sm.transition(target, reason="chain", actor="hypothesis")
            transition_count += 1

        assert len(sm.history) == transition_count

    @given(
        from_state=negotiation_state_strategy,
        to_state=negotiation_state_strategy,
    )
    @settings(max_examples=200)
    def test_failed_transition_does_not_record_history(self, from_state: str, to_state: str) -> None:
        """Failed transitions leave no history entry."""
        allowed = VALID_TRANSITIONS.get(from_state, set())
        assume(to_state not in allowed)

        sm = NegotiationStateMachine(current_state=from_state)
        try:
            sm.transition(to_state, reason="invalid", actor="test")
        except InvalidTransitionError:
            pass

        assert len(sm.history) == 0
        assert sm.current_state == from_state


# ---------------------------------------------------------------------------
# HITL-required transitions
# ---------------------------------------------------------------------------


class TestHITLRequiredTransitions:
    """HITL-required transitions are a subset of valid transitions."""

    @given(pair=st.sampled_from(list(HITL_REQUIRED_TRANSITIONS)))
    @settings(max_examples=200)
    def test_hitl_transitions_are_valid(self, pair: tuple[str, str]) -> None:
        """Every HITL-required transition is also a valid transition."""
        from_state, to_state = pair
        assert to_state in VALID_TRANSITIONS[from_state]

    @given(pair=st.sampled_from(list(HITL_REQUIRED_TRANSITIONS)))
    @settings(max_examples=200)
    def test_requires_hitl_returns_true(self, pair: tuple[str, str]) -> None:
        """requires_hitl returns True for all HITL-required transitions."""
        from_state, to_state = pair
        sm = NegotiationStateMachine(current_state=from_state)
        assert sm.requires_hitl(to_state) is True

    @given(data=st.data())
    @settings(max_examples=200)
    def test_non_hitl_transitions_return_false(self, data: st.DataObject) -> None:
        """requires_hitl returns False for transitions not in HITL_REQUIRED_TRANSITIONS."""
        from_state = data.draw(negotiation_state_strategy)
        targets = list(VALID_TRANSITIONS.get(from_state, set()))
        assume(len(targets) > 0)
        target = data.draw(st.sampled_from(targets))
        assume((from_state, target) not in HITL_REQUIRED_TRANSITIONS)

        sm = NegotiationStateMachine(current_state=from_state)
        assert sm.requires_hitl(target) is False


# ---------------------------------------------------------------------------
# Operator override invariants
# ---------------------------------------------------------------------------


class TestOperatorOverride:
    """operator_override can reach any state except itself."""

    @given(target=negotiation_state_strategy.filter(lambda s: s != "operator_override"))
    @settings(max_examples=200)
    def test_operator_override_can_reach_any_state(self, target: str) -> None:
        """From operator_override, any non-self state is reachable."""
        sm = NegotiationStateMachine(current_state="operator_override")
        assert sm.can_transition(target) is True

    def test_operator_override_cannot_self_loop(self) -> None:
        """operator_override cannot transition to itself."""
        sm = NegotiationStateMachine(current_state="operator_override")
        assert sm.can_transition("operator_override") is False


# ---------------------------------------------------------------------------
# Stale state invariants
# ---------------------------------------------------------------------------


class TestStaleStateTransitions:
    """Stale state has limited outgoing transitions."""

    @given(
        target=st.sampled_from(list(VALID_TRANSITIONS["stale"])),
    )
    @settings(max_examples=200)
    def test_stale_valid_transitions(self, target: str) -> None:
        """Stale state can only transition to qualifying, declined, or operator_override."""
        sm = NegotiationStateMachine(current_state="stale")
        sm.transition(target, reason="reactivation", actor="operator")
        assert sm.current_state == target
