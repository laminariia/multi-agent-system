"""Unit tests for src.core.state — AgentState helpers.

Covers: create_initial_state, update_state, append_error, increment_retry,
mark_hitl_required, transition_to_agent, mark_completed, mark_failed.
"""

from __future__ import annotations

from datetime import UTC, datetime

from src.core.state import (
    AgentState,
    ProjectContext,
    append_error,
    create_initial_state,
    increment_retry,
    mark_completed,
    mark_failed,
    mark_hitl_required,
    transition_to_agent,
    update_state,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _project() -> ProjectContext:
    return ProjectContext(
        project_id="proj-001",
        job_id="job-001",
        platform="freelancer",
        client={"name": "Test"},
        requirements="Build page",
        budget=500.0,
        deadline=datetime(2026, 6, 1, tzinfo=UTC),
    )


def _state(**overrides) -> AgentState:
    s = create_initial_state(project=_project(), first_agent="scout", thread_id="t-001")
    if overrides:
        s = update_state(s, **overrides)
    return s


# ---------------------------------------------------------------------------
# create_initial_state
# ---------------------------------------------------------------------------


def test_create_initial_state_defaults():
    """create_initial_state should set all expected fields."""
    s = create_initial_state(project=_project(), first_agent="scout")
    assert s["current_agent"] == "scout"
    assert s["status"] == "active"
    assert s["requires_hitl"] is False
    assert s["hitl_request_id"] is None
    assert s["retry_count"] == 0
    assert s["errors"] == []
    assert s["artifacts"] == {}
    assert s["messages"] == []
    assert s["next_agent"] is None
    assert s["current_task"] is None


def test_create_initial_state_auto_thread_id():
    """Thread ID should be auto-generated when not provided."""
    s = create_initial_state(project=_project())
    assert s["thread_id"]  # non-empty
    assert len(s["thread_id"]) == 32  # uuid4 hex


def test_create_initial_state_custom_thread_id():
    """Custom thread ID should be used when provided."""
    s = create_initial_state(project=_project(), thread_id="custom-123")
    assert s["thread_id"] == "custom-123"


def test_create_initial_state_timestamps():
    """created_at and updated_at should be set to current time."""
    before = datetime.now(tz=UTC)
    s = create_initial_state(project=_project())
    after = datetime.now(tz=UTC)
    assert before <= s["created_at"] <= after
    assert before <= s["updated_at"] <= after


def test_create_initial_state_project_preserved():
    """Project context should be stored exactly as provided."""
    p = _project()
    s = create_initial_state(project=p)
    assert s["project"]["project_id"] == "proj-001"
    assert s["project"]["budget"] == 500.0


# ---------------------------------------------------------------------------
# update_state
# ---------------------------------------------------------------------------


def test_update_state_merges_overrides():
    """update_state should merge new values into the state."""
    s = _state()
    s2 = update_state(s, status="paused", requires_hitl=True)
    assert s2["status"] == "paused"
    assert s2["requires_hitl"] is True


def test_update_state_does_not_mutate_original():
    """update_state should return a new dict, not mutate the original."""
    s = _state()
    original_status = s["status"]
    s2 = update_state(s, status="failed")
    assert s["status"] == original_status  # unchanged
    assert s2["status"] == "failed"


def test_update_state_bumps_updated_at():
    """update_state should update the updated_at timestamp."""
    s = _state()
    old_ts = s["updated_at"]
    s2 = update_state(s, status="completed")
    assert s2["updated_at"] >= old_ts


def test_update_state_refreshes_checkpoint_id():
    """update_state should generate a new checkpoint ID by default."""
    s = _state()
    old_cp = s["mas_checkpoint_id"]
    s2 = update_state(s, status="active")
    assert s2["mas_checkpoint_id"] != old_cp


def test_update_state_preserves_checkpoint_when_explicit():
    """update_state should keep explicit checkpoint ID if provided."""
    s = _state()
    s2 = update_state(s, mas_checkpoint_id="explicit-cp")
    assert s2["mas_checkpoint_id"] == "explicit-cp"


# ---------------------------------------------------------------------------
# append_error
# ---------------------------------------------------------------------------


def test_append_error_adds_to_list():
    """append_error should add a new error message."""
    s = _state()
    s2 = append_error(s, "Something went wrong")
    assert "Something went wrong" in s2["errors"]


def test_append_error_preserves_existing():
    """append_error should not remove existing errors."""
    s = _state(errors=["Error 1"])
    s2 = append_error(s, "Error 2")
    assert "Error 1" in s2["errors"]
    assert "Error 2" in s2["errors"]
    assert len(s2["errors"]) == 2


def test_append_error_does_not_mutate():
    """append_error should not mutate the original state."""
    s = _state()
    append_error(s, "New error")
    assert s["errors"] == []


# ---------------------------------------------------------------------------
# increment_retry
# ---------------------------------------------------------------------------


def test_increment_retry():
    """increment_retry should increase retry_count by 1."""
    s = _state()
    assert s["retry_count"] == 0
    s2 = increment_retry(s)
    assert s2["retry_count"] == 1
    s3 = increment_retry(s2)
    assert s3["retry_count"] == 2


def test_increment_retry_does_not_mutate():
    """increment_retry should not mutate the original state."""
    s = _state()
    increment_retry(s)
    assert s["retry_count"] == 0


# ---------------------------------------------------------------------------
# mark_hitl_required
# ---------------------------------------------------------------------------


def test_mark_hitl_required_sets_flags():
    """mark_hitl_required should set requires_hitl and hitl_request_id."""
    s = _state()
    s2 = mark_hitl_required(s, request_id="hitl-001")
    assert s2["requires_hitl"] is True
    assert s2["hitl_request_id"] == "hitl-001"
    assert s2["status"] == "paused"


def test_mark_hitl_required_with_reason():
    """mark_hitl_required should append reason to errors when provided."""
    s = _state()
    s2 = mark_hitl_required(s, request_id="hitl-002", reason="Bid too high")
    assert any("HITL: Bid too high" in e for e in s2["errors"])


def test_mark_hitl_required_no_reason():
    """mark_hitl_required without reason should not add to errors."""
    s = _state()
    s2 = mark_hitl_required(s, request_id="hitl-003")
    assert s2["errors"] == []


# ---------------------------------------------------------------------------
# transition_to_agent
# ---------------------------------------------------------------------------


def test_transition_to_agent():
    """transition_to_agent should set next_agent and current_agent."""
    s = _state()
    s2 = transition_to_agent(s, "bid")
    assert s2["next_agent"] == "bid"
    assert s2["current_agent"] == "bid"
    assert s2["current_task"] is None


# ---------------------------------------------------------------------------
# mark_completed
# ---------------------------------------------------------------------------


def test_mark_completed():
    """mark_completed should set status=completed and clear next_agent."""
    s = _state(next_agent="bid")
    s2 = mark_completed(s)
    assert s2["status"] == "completed"
    assert s2["next_agent"] is None


# ---------------------------------------------------------------------------
# mark_failed
# ---------------------------------------------------------------------------


def test_mark_failed():
    """mark_failed should set status=failed and append reason."""
    s = _state()
    s2 = mark_failed(s, "LLM API unavailable")
    assert s2["status"] == "failed"
    assert s2["next_agent"] is None
    assert "LLM API unavailable" in s2["errors"]


def test_mark_failed_preserves_existing_errors():
    """mark_failed should preserve existing errors."""
    s = _state(errors=["Previous error"])
    s2 = mark_failed(s, "New failure")
    assert "Previous error" in s2["errors"]
    assert "New failure" in s2["errors"]
