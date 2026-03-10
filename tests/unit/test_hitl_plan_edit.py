"""Tests for P1.7 — HITL Plan Review Edit (Sequence Editing).

Covers:
- Edit action applies agent_sequence from hitl_response.edits
- Edit action applies delivery_type from hitl_response.edits
- Validation: only valid execution agents allowed
- Validation: delivery_type validated via validate_delivery_type()
- Empty sequence is valid (consulting → packager)
- Audit trail: _original_sequence, _plan_edit_applied
- Index reset: current_sequence_index = 0 after edit
- Edit with no edits dict → approve-like behavior
- Edit with partial edits (only sequence, only delivery_type)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from src.core.state import create_initial_state, update_state

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _project_ctx(**overrides: Any) -> dict[str, Any]:
    base = {
        "project_id": "proj-edit-001",
        "job_id": "job-edit-001",
        "platform": "freelancer",
        "client": {"name": "Test Client"},
        "requirements": "Build a REST API",
        "budget": 1000.0,
        "deadline": datetime(2026, 4, 1, tzinfo=UTC),
    }
    base.update(overrides)
    return base


def _make_state(**overrides: Any) -> dict[str, Any]:
    state = create_initial_state(project=_project_ctx())
    defaults = {
        "agent_sequence": ["dev", "content", "design"],
        "delivery_type": "files",
        "current_sequence_index": 0,
        "status": "paused",
        "requires_hitl": True,
        "hitl_request_id": "hitl-plan-001",
    }
    defaults.update(overrides)
    state = update_state(state, **defaults)
    return state


# ===========================================================================
# 1. Sequence Editing
# ===========================================================================


class TestPlanEditSequence:
    """Edit action applies agent_sequence changes."""

    def test_edit_replaces_agent_sequence(self):
        """Edit with new agent_sequence replaces the existing one."""
        from src.core.graph import _apply_plan_review

        state = _make_state()
        result = _apply_plan_review(
            state,
            action="edit",
            hitl_response={"edits": {"agent_sequence": ["dev", "design"]}},
            thread_id="t-001",
        )

        assert result["agent_sequence"] == ["dev", "design"]

    def test_edit_resets_sequence_index(self):
        """After edit, current_sequence_index should be 0."""
        from src.core.graph import _apply_plan_review

        state = _make_state(current_sequence_index=2)
        result = _apply_plan_review(
            state,
            action="edit",
            hitl_response={"edits": {"agent_sequence": ["content"]}},
            thread_id="t-002",
        )

        assert result["current_sequence_index"] == 0

    def test_edit_empty_sequence_valid(self):
        """Empty sequence is valid — consulting project, skip to packager."""
        from src.core.graph import _apply_plan_review

        state = _make_state()
        result = _apply_plan_review(
            state,
            action="edit",
            hitl_response={"edits": {"agent_sequence": []}},
            thread_id="t-003",
        )

        assert result["agent_sequence"] == []
        assert result["status"] == "active"

    def test_edit_filters_invalid_agents(self):
        """Invalid agent names are removed from the sequence."""
        from src.core.graph import _apply_plan_review

        state = _make_state()
        result = _apply_plan_review(
            state,
            action="edit",
            hitl_response={"edits": {"agent_sequence": ["dev", "hacker", "design", "invalid"]}},
            thread_id="t-004",
        )

        assert result["agent_sequence"] == ["dev", "design"]

    def test_edit_preserves_agent_order(self):
        """Agent order from edits is preserved."""
        from src.core.graph import _apply_plan_review

        state = _make_state()
        result = _apply_plan_review(
            state,
            action="edit",
            hitl_response={"edits": {"agent_sequence": ["design", "dev", "content"]}},
            thread_id="t-005",
        )

        assert result["agent_sequence"] == ["design", "dev", "content"]


# ===========================================================================
# 2. Delivery Type Editing
# ===========================================================================


class TestPlanEditDeliveryType:
    """Edit action applies delivery_type changes."""

    def test_edit_changes_delivery_type(self):
        """Edit with new delivery_type updates state."""
        from src.core.graph import _apply_plan_review

        state = _make_state(delivery_type="files")
        result = _apply_plan_review(
            state,
            action="edit",
            hitl_response={"edits": {"delivery_type": "credentials"}},
            thread_id="t-010",
        )

        assert result["delivery_type"] == "credentials"

    def test_edit_invalid_delivery_type_falls_back(self):
        """Invalid delivery_type falls back to 'files' via validate_delivery_type."""
        from src.core.graph import _apply_plan_review

        state = _make_state(delivery_type="credentials")
        result = _apply_plan_review(
            state,
            action="edit",
            hitl_response={"edits": {"delivery_type": "invalid_type"}},
            thread_id="t-011",
        )

        assert result["delivery_type"] == "files"

    def test_edit_delivery_type_only_no_sequence_change(self):
        """Edit with only delivery_type preserves existing agent_sequence."""
        from src.core.graph import _apply_plan_review

        state = _make_state(agent_sequence=["dev", "content", "design"])
        result = _apply_plan_review(
            state,
            action="edit",
            hitl_response={"edits": {"delivery_type": "deploy"}},
            thread_id="t-012",
        )

        assert result["delivery_type"] == "deploy"
        assert result["agent_sequence"] == ["dev", "content", "design"]


# ===========================================================================
# 3. Audit Trail
# ===========================================================================


class TestPlanEditAudit:
    """Edit action creates proper audit trail."""

    def test_edit_stores_original_sequence(self):
        """Original sequence is saved in artifacts for audit."""
        from src.core.graph import _apply_plan_review

        state = _make_state(agent_sequence=["dev", "content", "design"])
        result = _apply_plan_review(
            state,
            action="edit",
            hitl_response={"edits": {"agent_sequence": ["dev"]}},
            thread_id="t-020",
        )

        artifacts = result.get("artifacts", {})
        original = artifacts.get("_original_sequence")
        assert original is not None
        # Should contain the pre-edit sequence
        assert original == [["dev", "content", "design"]]

    def test_edit_sets_plan_edit_applied_flag(self):
        """_plan_edit_applied flag is set in artifacts."""
        from src.core.graph import _apply_plan_review

        state = _make_state()
        result = _apply_plan_review(
            state,
            action="edit",
            hitl_response={"edits": {"agent_sequence": ["dev"]}},
            thread_id="t-021",
        )

        artifacts = result.get("artifacts", {})
        assert artifacts.get("_plan_edit_applied") == [True]

    def test_edit_stores_raw_edits(self):
        """Raw edits dict is stored in artifacts.hitl_edits."""
        from src.core.graph import _apply_plan_review

        edits = {"agent_sequence": ["content"], "delivery_type": "instructions"}
        state = _make_state()
        result = _apply_plan_review(
            state,
            action="edit",
            hitl_response={"edits": edits},
            thread_id="t-022",
        )

        artifacts = result.get("artifacts", {})
        assert artifacts.get("hitl_edits") == [edits]


# ===========================================================================
# 4. State Transitions
# ===========================================================================


class TestPlanEditStateTransition:
    """Edit action sets correct state transitions."""

    def test_edit_sets_status_active(self):
        """After edit, status should be active."""
        from src.core.graph import _apply_plan_review

        state = _make_state(status="paused")
        result = _apply_plan_review(
            state,
            action="edit",
            hitl_response={"edits": {"agent_sequence": ["dev"]}},
            thread_id="t-030",
        )

        assert result["status"] == "active"

    def test_edit_clears_hitl_fields(self):
        """After edit, HITL fields should be cleared."""
        from src.core.graph import _apply_plan_review

        state = _make_state()
        result = _apply_plan_review(
            state,
            action="edit",
            hitl_response={"edits": {"agent_sequence": ["dev"]}},
            thread_id="t-031",
        )

        assert result["requires_hitl"] is False
        assert result["hitl_request_id"] is None

    def test_edit_without_edits_dict_behaves_like_approve(self):
        """Edit with empty/missing edits should still activate (approve-like)."""
        from src.core.graph import _apply_plan_review

        state = _make_state(agent_sequence=["dev", "content"])
        result = _apply_plan_review(
            state,
            action="edit",
            hitl_response={},
            thread_id="t-032",
        )

        assert result["status"] == "active"
        assert result["requires_hitl"] is False
        # Original sequence preserved
        assert result["agent_sequence"] == ["dev", "content"]

    def test_edit_sets_hitl_type_artifact(self):
        """_hitl_type artifact is set to plan_review for routing."""
        from src.core.graph import _apply_plan_review

        state = _make_state()
        result = _apply_plan_review(
            state,
            action="edit",
            hitl_response={"edits": {"agent_sequence": ["dev"]}},
            thread_id="t-033",
        )

        artifacts = result.get("artifacts", {})
        assert artifacts.get("_hitl_type") == "plan_review"

    def test_edit_combined_sequence_and_delivery_type(self):
        """Edit with both sequence and delivery_type changes."""
        from src.core.graph import _apply_plan_review

        state = _make_state(
            agent_sequence=["dev", "content", "design"],
            delivery_type="files",
        )
        result = _apply_plan_review(
            state,
            action="edit",
            hitl_response={
                "edits": {
                    "agent_sequence": ["dev"],
                    "delivery_type": "credentials",
                }
            },
            thread_id="t-034",
        )

        assert result["agent_sequence"] == ["dev"]
        assert result["delivery_type"] == "credentials"
        assert result["status"] == "active"
        assert result["current_sequence_index"] == 0
