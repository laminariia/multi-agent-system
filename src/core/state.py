"""LangGraph AgentState definition and helpers.

Defines the canonical state schema that flows through the LangGraph StateGraph.
Matches the specification in ``docs/langgraph_state.md``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal, TypedDict

from langchain_core.messages import BaseMessage

# ---------------------------------------------------------------------------
# Sub-schemas
# ---------------------------------------------------------------------------

Platform = Literal["freelancer", "upwork", "flru", "kwork"]
Status = Literal["active", "paused", "completed", "failed"]


class ProjectContext(TypedDict):
    """Describes the freelance project associated with the current workflow."""

    project_id: str
    job_id: str
    platform: Platform
    client: dict[str, Any]
    requirements: str
    budget: float
    deadline: datetime


# ---------------------------------------------------------------------------
# Core state
# ---------------------------------------------------------------------------

class AgentState(TypedDict):
    """Full execution state for a multi-agent workflow instance.

    This TypedDict is the single schema consumed and produced by every node in
    the LangGraph StateGraph.  Fields are grouped by concern.
    """

    # Identity
    thread_id: str
    mas_checkpoint_id: str

    # Project context
    project: ProjectContext

    # Current execution
    current_agent: str
    current_task: dict[str, Any] | None

    # Artifacts produced by agents (keyed by agent name)
    artifacts: dict[str, list[str]]

    # Conversation / message history
    messages: list[BaseMessage]

    # Control flow
    next_agent: str | None
    requires_hitl: bool
    hitl_request_id: str | None

    # Error handling
    retry_count: int
    errors: list[str]

    # Metadata
    created_at: datetime
    updated_at: datetime
    status: Status


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------

def create_initial_state(
    *,
    project: ProjectContext,
    first_agent: str = "scout",
    thread_id: str | None = None,
) -> AgentState:
    """Build a fresh ``AgentState`` ready to be fed into the graph.

    Args:
        project: The project context for this workflow.
        first_agent: Name of the agent that will execute first.
        thread_id: Optional override; a UUID4 is generated when omitted.

    Returns:
        A fully-initialised ``AgentState`` dictionary.
    """
    now = datetime.now(tz=UTC)
    tid = thread_id or uuid.uuid4().hex
    return AgentState(
        thread_id=tid,
        mas_checkpoint_id=uuid.uuid4().hex,
        project=project,
        current_agent=first_agent,
        current_task=None,
        artifacts={},
        messages=[],
        next_agent=None,
        requires_hitl=False,
        hitl_request_id=None,
        retry_count=0,
        errors=[],
        created_at=now,
        updated_at=now,
        status="active",
    )


def update_state(
    state: AgentState,
    /,
    **overrides: Any,
) -> AgentState:
    """Return a *new* state dict with the supplied fields merged in.

    Always bumps ``updated_at`` to the current UTC time.

    Args:
        state: The current state to copy.
        **overrides: Key/value pairs to merge on top of *state*.

    Returns:
        A new ``AgentState`` dict (the original is not mutated).
    """
    merged: dict[str, Any] = {**state, **overrides}
    merged["updated_at"] = datetime.now(tz=UTC)
    # Ensure mas_checkpoint_id is refreshed on every state transition
    if "mas_checkpoint_id" not in overrides:
        merged["mas_checkpoint_id"] = uuid.uuid4().hex
    return AgentState(**merged)  # type: ignore[typeddict-item]


def append_error(state: AgentState, error_msg: str) -> AgentState:
    """Append an error message to the state's error list without mutating the original."""
    return update_state(state, errors=[*state["errors"], error_msg])


def increment_retry(state: AgentState) -> AgentState:
    """Increment the retry counter and bump ``updated_at``."""
    return update_state(state, retry_count=state["retry_count"] + 1)


def mark_hitl_required(state: AgentState, *, request_id: str, reason: str = "") -> AgentState:
    """Flag the state as requiring human-in-the-loop approval.

    Args:
        state: Current state.
        request_id: A unique identifier for this HITL request.
        reason: Human-readable reason (appended to errors for audit).
    """
    updates: dict[str, Any] = {
        "requires_hitl": True,
        "hitl_request_id": request_id,
        "status": "paused",
    }
    if reason:
        updates["errors"] = [*state["errors"], f"HITL: {reason}"]
    return update_state(state, **updates)


def transition_to_agent(state: AgentState, next_agent: str) -> AgentState:
    """Set up the state for transitioning to a different agent node."""
    return update_state(
        state,
        next_agent=next_agent,
        current_agent=next_agent,
        current_task=None,
    )


def mark_completed(state: AgentState) -> AgentState:
    """Mark the workflow as completed."""
    return update_state(state, status="completed", next_agent=None)


def mark_failed(state: AgentState, reason: str) -> AgentState:
    """Mark the workflow as failed with a reason."""
    return update_state(
        state,
        status="failed",
        next_agent=None,
        errors=[*state["errors"], reason],
    )
