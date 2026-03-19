"""Property-based tests for AgentState invariants.

Verifies that state creation, updates, and transitions always maintain
core invariants: valid status enum, valid agent names, monotonic timestamps,
required fields present, and correct type shapes.
"""

from __future__ import annotations

from datetime import UTC

from hypothesis import given, settings
from hypothesis import strategies as st

from src.core.state import (
    append_error,
    clear_failure,
    create_initial_state,
    increment_retry,
    mark_completed,
    mark_failed,
    mark_hitl_required,
    transition_to_agent,
    update_state,
    validate_delivery_type,
)

from .conftest import (
    VALID_DELIVERY_TYPES,
    VALID_STATUSES,
    agent_name_strategy,
    agent_state_strategy,
    delivery_type_strategy,
    nonempty_text_strategy,
    project_context_strategy,
    status_strategy,
    thread_id_strategy,
    unicode_text_strategy,
)

# ---------------------------------------------------------------------------
# create_initial_state invariants
# ---------------------------------------------------------------------------


class TestCreateInitialState:
    """Invariants for create_initial_state factory."""

    @given(project=project_context_strategy(), first_agent=agent_name_strategy)
    @settings(max_examples=200)
    def test_status_always_active(self, project: dict, first_agent: str) -> None:
        """Newly created state always starts with status='active'."""
        state = create_initial_state(project=project, first_agent=first_agent)
        assert state["status"] == "active"

    @given(project=project_context_strategy(), first_agent=agent_name_strategy)
    @settings(max_examples=200)
    def test_current_agent_matches_first_agent(self, project: dict, first_agent: str) -> None:
        """current_agent is always set to the provided first_agent."""
        state = create_initial_state(project=project, first_agent=first_agent)
        assert state["current_agent"] == first_agent

    @given(project=project_context_strategy())
    @settings(max_examples=200)
    def test_default_first_agent_is_scout(self, project: dict) -> None:
        """When first_agent is not specified, it defaults to 'scout'."""
        state = create_initial_state(project=project)
        assert state["current_agent"] == "scout"

    @given(project=project_context_strategy(), first_agent=agent_name_strategy)
    @settings(max_examples=200)
    def test_timestamps_are_utc(self, project: dict, first_agent: str) -> None:
        """created_at and updated_at are always timezone-aware UTC."""
        state = create_initial_state(project=project, first_agent=first_agent)
        assert state["created_at"].tzinfo is not None
        assert state["updated_at"].tzinfo is not None

    @given(project=project_context_strategy(), first_agent=agent_name_strategy)
    @settings(max_examples=200)
    def test_created_at_equals_updated_at_initially(self, project: dict, first_agent: str) -> None:
        """On creation, created_at and updated_at are the same."""
        state = create_initial_state(project=project, first_agent=first_agent)
        assert state["created_at"] == state["updated_at"]

    @given(project=project_context_strategy(), first_agent=agent_name_strategy)
    @settings(max_examples=200)
    def test_no_errors_initially(self, project: dict, first_agent: str) -> None:
        """Fresh state has empty error list."""
        state = create_initial_state(project=project, first_agent=first_agent)
        assert state["errors"] == []

    @given(project=project_context_strategy(), first_agent=agent_name_strategy)
    @settings(max_examples=200)
    def test_no_hitl_initially(self, project: dict, first_agent: str) -> None:
        """Fresh state does not require HITL."""
        state = create_initial_state(project=project, first_agent=first_agent)
        assert state["requires_hitl"] is False
        assert state["hitl_request_id"] is None

    @given(project=project_context_strategy(), first_agent=agent_name_strategy)
    @settings(max_examples=200)
    def test_retry_count_zero_initially(self, project: dict, first_agent: str) -> None:
        """Fresh state has retry_count=0."""
        state = create_initial_state(project=project, first_agent=first_agent)
        assert state["retry_count"] == 0

    @given(
        project=project_context_strategy(),
        first_agent=agent_name_strategy,
        tid=thread_id_strategy,
    )
    @settings(max_examples=200)
    def test_thread_id_override_honored(self, project: dict, first_agent: str, tid: str) -> None:
        """When thread_id is provided, it is used exactly."""
        state = create_initial_state(project=project, first_agent=first_agent, thread_id=tid)
        assert state["thread_id"] == tid

    @given(project=project_context_strategy(), first_agent=agent_name_strategy)
    @settings(max_examples=200)
    def test_all_required_fields_present(self, project: dict, first_agent: str) -> None:
        """Every field declared in AgentState TypedDict is present."""
        state = create_initial_state(project=project, first_agent=first_agent)
        required_fields = {
            "thread_id",
            "mas_checkpoint_id",
            "project",
            "current_agent",
            "current_task",
            "artifacts",
            "messages",
            "next_agent",
            "requires_hitl",
            "hitl_request_id",
            "agent_sequence",
            "current_sequence_index",
            "delivery_type",
            "revision_target",
            "revision_severity",
            "real_hours",
            "proposed_days",
            "min_delivery_at",
            "scheduled_messages",
            "failed_agent",
            "failure_reason",
            "recovery_attempted",
            "skipped_agents",
            "retry_count",
            "errors",
            "created_at",
            "updated_at",
            "status",
        }
        for field in required_fields:
            assert field in state, f"Missing field: {field}"

    @given(project=project_context_strategy(), first_agent=agent_name_strategy)
    @settings(max_examples=200)
    def test_delivery_type_always_valid(self, project: dict, first_agent: str) -> None:
        """Initial delivery_type is always a valid value."""
        state = create_initial_state(project=project, first_agent=first_agent)
        assert state["delivery_type"] in VALID_DELIVERY_TYPES


# ---------------------------------------------------------------------------
# update_state invariants
# ---------------------------------------------------------------------------


class TestUpdateState:
    """Invariants for update_state helper."""

    @given(state=agent_state_strategy(), new_status=status_strategy)
    @settings(max_examples=200)
    def test_status_always_in_enum(self, state: dict, new_status: str) -> None:
        """Status is always one of the valid enum values after update."""
        result = update_state(state, status=new_status)
        assert result["status"] in VALID_STATUSES

    @given(state=agent_state_strategy())
    @settings(max_examples=200)
    def test_updated_at_bumped(self, state: dict) -> None:
        """updated_at is always a valid datetime after update_state call."""
        from datetime import datetime

        before = datetime.now(UTC)
        result = update_state(state, retry_count=state["retry_count"])
        after = datetime.now(UTC)
        assert before <= result["updated_at"] <= after

    @given(state=agent_state_strategy())
    @settings(max_examples=200)
    def test_created_at_never_changes(self, state: dict) -> None:
        """created_at is never modified by update_state."""
        result = update_state(state, status="active")
        assert result["created_at"] == state["created_at"]

    @given(state=agent_state_strategy())
    @settings(max_examples=200)
    def test_checkpoint_id_refreshed(self, state: dict) -> None:
        """mas_checkpoint_id changes on every state update."""
        result = update_state(state, status=state["status"])
        assert result["mas_checkpoint_id"] != state["mas_checkpoint_id"]

    @given(state=agent_state_strategy())
    @settings(max_examples=200)
    def test_update_is_non_mutating(self, state: dict) -> None:
        """update_state returns a new dict; original is not modified."""
        original_status = state["status"]
        original_id = state["mas_checkpoint_id"]
        _ = update_state(state, status="failed")
        assert state["status"] == original_status
        assert state["mas_checkpoint_id"] == original_id


# ---------------------------------------------------------------------------
# State transition helpers
# ---------------------------------------------------------------------------


class TestStateTransitionHelpers:
    """Invariants for transition_to_agent, mark_completed, mark_failed, etc."""

    @given(state=agent_state_strategy(), target=agent_name_strategy)
    @settings(max_examples=200)
    def test_transition_sets_current_and_next(self, state: dict, target: str) -> None:
        """transition_to_agent sets both current_agent and next_agent."""
        result = transition_to_agent(state, target)
        assert result["current_agent"] == target
        assert result["next_agent"] == target

    @given(state=agent_state_strategy())
    @settings(max_examples=200)
    def test_mark_completed_is_terminal(self, state: dict) -> None:
        """mark_completed sets status=completed and next_agent=None."""
        result = mark_completed(state)
        assert result["status"] == "completed"
        assert result["next_agent"] is None

    @given(state=agent_state_strategy(), reason=nonempty_text_strategy)
    @settings(max_examples=200)
    def test_mark_failed_appends_error(self, state: dict, reason: str) -> None:
        """mark_failed sets status=failed and appends the reason to errors."""
        result = mark_failed(state, reason)
        assert result["status"] == "failed"
        assert result["next_agent"] is None
        assert reason in result["errors"]

    @given(state=agent_state_strategy(), error_msg=nonempty_text_strategy)
    @settings(max_examples=200)
    def test_append_error_grows_list(self, state: dict, error_msg: str) -> None:
        """append_error adds exactly one error to the list."""
        result = append_error(state, error_msg)
        assert len(result["errors"]) == len(state["errors"]) + 1
        assert result["errors"][-1] == error_msg

    @given(state=agent_state_strategy())
    @settings(max_examples=200)
    def test_increment_retry_adds_one(self, state: dict) -> None:
        """increment_retry increases retry_count by exactly 1."""
        result = increment_retry(state)
        assert result["retry_count"] == state["retry_count"] + 1

    @given(
        state=agent_state_strategy(),
        request_id=thread_id_strategy,
    )
    @settings(max_examples=200)
    def test_mark_hitl_pauses_state(self, state: dict, request_id: str) -> None:
        """mark_hitl_required sets requires_hitl=True and status=paused."""
        result = mark_hitl_required(state, request_id=request_id)
        assert result["requires_hitl"] is True
        assert result["status"] == "paused"
        assert result["hitl_request_id"] == request_id

    @given(state=agent_state_strategy())
    @settings(max_examples=200)
    def test_clear_failure_resets_to_active(self, state: dict) -> None:
        """clear_failure resets failure fields and sets status=active."""
        # First mark failed
        failed = update_state(
            state,
            failed_agent="dev",
            failure_reason="timeout",
            status="failed",
            requires_hitl=True,
        )
        result = clear_failure(failed)
        assert result["status"] == "active"
        assert result["failed_agent"] is None
        assert result["failure_reason"] is None
        assert result["requires_hitl"] is False
        assert result["hitl_request_id"] is None


# ---------------------------------------------------------------------------
# validate_delivery_type
# ---------------------------------------------------------------------------


class TestValidateDeliveryType:
    """Invariants for validate_delivery_type."""

    @given(dt=delivery_type_strategy)
    @settings(max_examples=200)
    def test_valid_types_pass_through(self, dt: str) -> None:
        """All valid delivery types are returned unchanged."""
        assert validate_delivery_type(dt) == dt

    @given(dt=unicode_text_strategy.filter(lambda s: s not in VALID_DELIVERY_TYPES))
    @settings(max_examples=200)
    def test_invalid_types_fall_back_to_files(self, dt: str) -> None:
        """Any invalid delivery type falls back to 'files'."""
        assert validate_delivery_type(dt) == "files"

    @given(dt=st.text(min_size=0, max_size=500))
    @settings(max_examples=200)
    def test_never_returns_invalid_value(self, dt: str) -> None:
        """Output is always a member of VALID_DELIVERY_TYPES."""
        result = validate_delivery_type(dt)
        assert result in VALID_DELIVERY_TYPES
