"""Integration tests for Pipeline B HITL outreach approval actions.

Tests all 3 HITL actions (approve, reject, edit) using the production
``_apply_outreach_approval`` helper from ``src.core.graph``.

Verifies:
- Approve: sets ``emails_approved=True``, status="active"
- Reject: sets status="failed", error message added
- Edit: sets ``emails_approved=True`` + ``hitl_edits`` in artifacts
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from src.core.graph import _apply_outreach_approval
from src.core.state import ProjectContext, create_initial_state

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_paused_email_state(
    campaign_id: str | None = None,
    thread_id: str | None = None,
) -> dict[str, Any]:
    """Build a state dict that simulates a Pipeline B paused at HITL email."""
    project = ProjectContext(
        project_id=f"pipeline_b_hitl_{uuid.uuid4().hex[:8]}",
        job_id="",
        platform="outreach",
        client={},
        requirements="Berlin",
        budget=0.0,
        deadline=datetime.now(tz=UTC),
    )
    state = dict(create_initial_state(
        project=project,
        first_agent="geoscout",
        thread_id=thread_id or f"thread-hitl-{uuid.uuid4().hex[:8]}",
    ))
    cid = campaign_id or str(uuid.uuid4())
    state["artifacts"] = {
        "_scan_city": "Berlin",
        "_geo_scan_results": {
            "city": "Berlin",
            "hexagons_total": 25,
            "hexagons_scanned": 25,
            "leads_found": 10,
            "leads_stored": 10,
        },
        "_outreach_results": {
            "city": "Berlin",
            "leads_processed": 10,
            "enriched": 7,
            "emails_drafted": 7,
            "enrichment_cost": 0.35,
        },
        "_outreach_hitl_id": str(uuid.uuid4()),
        "campaign_id": cid,
    }
    state["status"] = "paused"
    state["requires_hitl"] = True
    state["current_agent"] = "hitl_outreach"
    state["hitl_request_id"] = state["artifacts"]["_outreach_hitl_id"]
    return state


# ---------------------------------------------------------------------------
# Tests: Approve
# ---------------------------------------------------------------------------


async def test_hitl_outreach_approve_sets_emails_approved():
    """Approve: artifacts['emails_approved'] is True."""
    state = _make_paused_email_state()
    tid = state["thread_id"]
    result = _apply_outreach_approval(state, "approve", {}, tid)

    assert result["artifacts"]["emails_approved"] is True


async def test_hitl_outreach_approve_status_active():
    """Approve: status transitions to 'active'."""
    state = _make_paused_email_state()
    tid = state["thread_id"]
    result = _apply_outreach_approval(state, "approve", {}, tid)

    assert result["status"] == "active"


async def test_hitl_outreach_approve_clears_hitl():
    """Approve: requires_hitl is False and hitl_request_id is None."""
    state = _make_paused_email_state()
    tid = state["thread_id"]
    result = _apply_outreach_approval(state, "approve", {}, tid)

    assert result["requires_hitl"] is False
    assert result["hitl_request_id"] is None


async def test_hitl_outreach_approve_preserves_campaign_id():
    """Approve: campaign_id is preserved in artifacts."""
    campaign_id = str(uuid.uuid4())
    state = _make_paused_email_state(campaign_id=campaign_id)
    tid = state["thread_id"]
    result = _apply_outreach_approval(state, "approve", {}, tid)

    assert result["artifacts"]["campaign_id"] == campaign_id


async def test_hitl_outreach_approve_preserves_all_artifacts():
    """Approve: all existing artifacts are preserved."""
    state = _make_paused_email_state()
    tid = state["thread_id"]
    result = _apply_outreach_approval(state, "approve", {}, tid)

    assert "_scan_city" in result["artifacts"]
    assert "_geo_scan_results" in result["artifacts"]
    assert "_outreach_results" in result["artifacts"]
    assert "_outreach_hitl_id" in result["artifacts"]


async def test_hitl_outreach_approve_sets_current_agent():
    """Approve: current_agent set to 'hitl_outreach'."""
    state = _make_paused_email_state()
    tid = state["thread_id"]
    result = _apply_outreach_approval(state, "approve", {}, tid)

    assert result["current_agent"] == "hitl_outreach"


# ---------------------------------------------------------------------------
# Tests: Reject
# ---------------------------------------------------------------------------


async def test_hitl_outreach_reject_status_failed():
    """Reject: status transitions to 'failed'."""
    state = _make_paused_email_state()
    tid = state["thread_id"]
    result = _apply_outreach_approval(state, "reject", {}, tid)

    assert result["status"] == "failed"


async def test_hitl_outreach_reject_clears_hitl():
    """Reject: requires_hitl is False and hitl_request_id is None."""
    state = _make_paused_email_state()
    tid = state["thread_id"]
    result = _apply_outreach_approval(state, "reject", {}, tid)

    assert result["requires_hitl"] is False
    assert result["hitl_request_id"] is None


async def test_hitl_outreach_reject_adds_error():
    """Reject: error message is added to errors list."""
    state = _make_paused_email_state()
    tid = state["thread_id"]
    result = _apply_outreach_approval(state, "reject", {}, tid)

    assert any("rejected" in e.lower() for e in result["errors"])


async def test_hitl_outreach_reject_no_emails_approved():
    """Reject: emails_approved is NOT set in artifacts."""
    state = _make_paused_email_state()
    tid = state["thread_id"]
    result = _apply_outreach_approval(state, "reject", {}, tid)

    assert "emails_approved" not in result["artifacts"]


# ---------------------------------------------------------------------------
# Tests: Edit
# ---------------------------------------------------------------------------


async def test_hitl_outreach_edit_sets_emails_approved():
    """Edit: artifacts['emails_approved'] is True (emails still get sent after edits)."""
    state = _make_paused_email_state()
    tid = state["thread_id"]
    edits = {"subject": "New subject line", "body": "Updated body text"}
    result = _apply_outreach_approval(state, "edit", {"edits": edits}, tid)

    assert result["artifacts"]["emails_approved"] is True


async def test_hitl_outreach_edit_stores_edits():
    """Edit: hitl_edits is stored in artifacts."""
    state = _make_paused_email_state()
    tid = state["thread_id"]
    edits = {"subject": "New subject line", "body": "Updated body text"}
    result = _apply_outreach_approval(state, "edit", {"edits": edits}, tid)

    assert "hitl_edits" in result["artifacts"]
    assert len(result["artifacts"]["hitl_edits"]) >= 1


async def test_hitl_outreach_edit_status_active():
    """Edit: status transitions to 'active'."""
    state = _make_paused_email_state()
    tid = state["thread_id"]
    edits = {"subject": "New subject"}
    result = _apply_outreach_approval(state, "edit", {"edits": edits}, tid)

    assert result["status"] == "active"


async def test_hitl_outreach_edit_clears_hitl():
    """Edit: requires_hitl is False and hitl_request_id is None."""
    state = _make_paused_email_state()
    tid = state["thread_id"]
    edits = {"subject": "New subject"}
    result = _apply_outreach_approval(state, "edit", {"edits": edits}, tid)

    assert result["requires_hitl"] is False
    assert result["hitl_request_id"] is None


async def test_hitl_outreach_edit_preserves_campaign_id():
    """Edit: campaign_id is preserved in artifacts."""
    campaign_id = str(uuid.uuid4())
    state = _make_paused_email_state(campaign_id=campaign_id)
    tid = state["thread_id"]
    edits = {"body": "Updated body"}
    result = _apply_outreach_approval(state, "edit", {"edits": edits}, tid)

    assert result["artifacts"]["campaign_id"] == campaign_id


async def test_hitl_outreach_edit_empty_edits():
    """Edit with empty edits dict: emails_approved is set, no hitl_edits key."""
    state = _make_paused_email_state()
    tid = state["thread_id"]
    result = _apply_outreach_approval(state, "edit", {"edits": {}}, tid)

    assert result["artifacts"]["emails_approved"] is True
    # Empty edits should not create hitl_edits
    assert "hitl_edits" not in result["artifacts"]


async def test_hitl_outreach_edit_list_edits():
    """Edit with list edits: stored as-is when already a list."""
    state = _make_paused_email_state()
    tid = state["thread_id"]
    edits_list = [{"lead_id": "1", "subject": "A"}, {"lead_id": "2", "subject": "B"}]
    result = _apply_outreach_approval(state, "edit", {"edits": edits_list}, tid)

    assert result["artifacts"]["hitl_edits"] == edits_list


# ---------------------------------------------------------------------------
# Tests: Unknown action (defensive)
# ---------------------------------------------------------------------------


async def test_hitl_outreach_unknown_action_fails_closed():
    """Unknown action: fail closed for safety (status='failed')."""
    state = _make_paused_email_state()
    tid = state["thread_id"]
    result = _apply_outreach_approval(state, "unknown_action", {}, tid)

    assert result["status"] == "failed"
    assert result["requires_hitl"] is False
    assert any("unknown" in e.lower() for e in result["errors"])
