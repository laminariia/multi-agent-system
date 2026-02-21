"""Integration tests for email campaign lifecycle.

Tests the full campaign lifecycle through the Pipeline B graph:
- Create -> Draft -> HITL -> Send -> Complete
- Status transitions: draft -> active -> completed
- Campaign with 0 enriched leads (no HITL needed)
- Email sending node behavior after approval
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

from src.core.graph import (
    _route_after_hitl_email,
    _route_after_outreach,
    email_sending_node,
    hitl_email_node,
)
from src.core.state import ProjectContext, create_initial_state

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _run_graph(graph, state: dict[str, Any]) -> dict[str, Any]:
    """Run a compiled graph and return the final state."""
    last_state = state
    async for chunk in graph.astream(state, stream_mode="values"):
        last_state = chunk
    return last_state


def _make_campaign_state(
    city: str = "Berlin",
    campaign_id: str | None = None,
    emails_drafted: int = 5,
) -> dict[str, Any]:
    """Build a state dict for campaign lifecycle tests."""
    project = ProjectContext(
        project_id=f"campaign_test_{uuid.uuid4().hex[:8]}",
        job_id="",
        platform="outreach",
        client={},
        requirements=city,
        budget=0.0,
        deadline=datetime.now(tz=UTC),
    )
    state = dict(create_initial_state(
        project=project,
        first_agent="geoscout",
        thread_id=f"thread-camp-{uuid.uuid4().hex[:8]}",
    ))
    cid = campaign_id or str(uuid.uuid4())
    state["artifacts"] = {
        "_scan_city": city,
        "_geo_scan_results": {
            "city": city,
            "hexagons_total": 10,
            "hexagons_scanned": 10,
            "leads_found": 10,
            "leads_stored": 10,
        },
        "_outreach_results": {
            "city": city,
            "leads_processed": 10,
            "enriched": emails_drafted,
            "emails_drafted": emails_drafted,
            "enrichment_cost": 0.50,
        },
        "campaign_id": cid,
    }
    if emails_drafted > 0:
        state["artifacts"]["_outreach_hitl_id"] = str(uuid.uuid4())
    return state


# ---------------------------------------------------------------------------
# Tests: Full campaign lifecycle via graph
# ---------------------------------------------------------------------------


async def test_campaign_lifecycle_create_to_hitl():
    """Lifecycle: GeoScout -> Outreach creates campaign -> HITL pause."""

    async def _geo_scout_ok(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["_geo_scan_results"] = {
            "city": "Berlin", "hexagons_total": 10, "hexagons_scanned": 10,
            "leads_found": 5, "leads_stored": 5,
        }
        return {
            **state, "artifacts": artifacts,
            "next_agent": "outreach", "current_agent": "geoscout",
            "status": "active", "requires_hitl": False,
        }

    async def _outreach_creates_campaign(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        cid = str(uuid.uuid4())
        artifacts["campaign_id"] = cid
        artifacts["_outreach_results"] = {
            "city": "Berlin", "leads_processed": 5, "enriched": 3,
            "emails_drafted": 3, "enrichment_cost": 0.15,
        }
        artifacts["_outreach_hitl_id"] = str(uuid.uuid4())
        return {
            **state, "artifacts": artifacts,
            "requires_hitl": True,
            "hitl_request_id": artifacts["_outreach_hitl_id"],
            "current_agent": "outreach", "status": "paused", "next_agent": None,
        }

    with patch("src.core.graph.geo_scout_node", side_effect=_geo_scout_ok), \
         patch("src.core.graph.outreach_node", side_effect=_outreach_creates_campaign):
        from src.core.graph import build_pipeline_b_graph
        graph = build_pipeline_b_graph()
        result = await _run_graph(graph, _make_campaign_state(emails_drafted=0))

    assert result["status"] == "paused"
    assert result["requires_hitl"] is True
    assert "campaign_id" in result["artifacts"]


async def test_campaign_lifecycle_approve_and_send():
    """Lifecycle: HITL approve -> email_sending_node -> completed."""

    async def _geo_ok(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["_geo_scan_results"] = {"city": "Berlin", "leads_found": 5}
        return {
            **state, "artifacts": artifacts,
            "next_agent": "outreach", "current_agent": "geoscout",
            "status": "active",
        }

    async def _outreach_ok(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["campaign_id"] = str(uuid.uuid4())
        artifacts["_outreach_results"] = {"emails_drafted": 5}
        artifacts["_outreach_hitl_id"] = str(uuid.uuid4())
        return {
            **state, "artifacts": artifacts,
            "requires_hitl": True, "current_agent": "outreach",
            "status": "paused",
        }

    async def _hitl_approves(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["emails_approved"] = True
        return {
            **state, "artifacts": artifacts,
            "requires_hitl": False, "status": "active",
            "current_agent": "hitl_email",
        }

    async def _email_sends(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["email_send_result"] = {"sent": 5, "failed": 0}
        return {
            **state, "artifacts": artifacts,
            "current_agent": "email_sending",
            "status": "completed", "next_agent": None,
        }

    with patch("src.core.graph.geo_scout_node", side_effect=_geo_ok), \
         patch("src.core.graph.outreach_node", side_effect=_outreach_ok), \
         patch("src.core.graph.hitl_email_node", side_effect=_hitl_approves), \
         patch("src.core.graph.email_sending_node", side_effect=_email_sends):
        from src.core.graph import build_pipeline_b_graph
        graph = build_pipeline_b_graph()
        result = await _run_graph(graph, _make_campaign_state())

    assert result["status"] == "completed"
    assert result["artifacts"]["email_send_result"]["sent"] == 5


async def test_campaign_lifecycle_reject_ends():
    """Lifecycle: HITL reject -> pipeline ends with failed status."""

    async def _geo_ok(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["_geo_scan_results"] = {"city": "Berlin", "leads_found": 5}
        return {
            **state, "artifacts": artifacts,
            "next_agent": "outreach", "current_agent": "geoscout",
            "status": "active",
        }

    async def _outreach_ok(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["campaign_id"] = str(uuid.uuid4())
        artifacts["_outreach_results"] = {"emails_drafted": 5}
        return {
            **state, "artifacts": artifacts,
            "requires_hitl": True, "current_agent": "outreach",
            "status": "paused",
        }

    async def _hitl_rejects(state: dict[str, Any]) -> dict[str, Any]:
        return {
            **state,
            "requires_hitl": False, "status": "failed",
            "current_agent": "hitl_email",
            "errors": [*state.get("errors", []), "HITL: emails rejected"],
        }

    with patch("src.core.graph.geo_scout_node", side_effect=_geo_ok), \
         patch("src.core.graph.outreach_node", side_effect=_outreach_ok), \
         patch("src.core.graph.hitl_email_node", side_effect=_hitl_rejects):
        from src.core.graph import build_pipeline_b_graph
        graph = build_pipeline_b_graph()
        result = await _run_graph(graph, _make_campaign_state())

    assert result["status"] == "failed"


# ---------------------------------------------------------------------------
# Tests: Campaign with 0 enriched leads
# ---------------------------------------------------------------------------


async def test_campaign_zero_leads_no_hitl():
    """Campaign: 0 enriched leads -> completes without HITL."""

    async def _geo_ok(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["_geo_scan_results"] = {"city": "Berlin", "leads_found": 5}
        return {
            **state, "artifacts": artifacts,
            "next_agent": "outreach", "current_agent": "geoscout",
            "status": "active",
        }

    async def _outreach_no_enrichment(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["_outreach_results"] = {
            "city": "Berlin", "leads_processed": 5,
            "enriched": 0, "emails_drafted": 0, "enrichment_cost": 0.25,
        }
        return {
            **state, "artifacts": artifacts,
            "requires_hitl": False, "current_agent": "outreach",
            "status": "completed", "next_agent": None,
        }

    hitl_called = False

    async def _tracking_hitl(state: dict[str, Any]) -> dict[str, Any]:
        nonlocal hitl_called
        hitl_called = True
        return state

    with patch("src.core.graph.geo_scout_node", side_effect=_geo_ok), \
         patch("src.core.graph.outreach_node", side_effect=_outreach_no_enrichment), \
         patch("src.core.graph.hitl_email_node", side_effect=_tracking_hitl):
        from src.core.graph import build_pipeline_b_graph
        graph = build_pipeline_b_graph()
        result = await _run_graph(graph, _make_campaign_state(emails_drafted=0))

    assert result["status"] == "completed"
    assert result["requires_hitl"] is False
    assert hitl_called is False


# ---------------------------------------------------------------------------
# Tests: Status transitions
# ---------------------------------------------------------------------------


async def test_campaign_status_draft_to_active_to_completed():
    """Campaign status transitions: draft -> active (after HITL) -> completed."""
    statuses_seen: list[str] = []

    async def _geo_ok(state: dict[str, Any]) -> dict[str, Any]:
        statuses_seen.append(f"geo:{state['status']}")
        artifacts = dict(state.get("artifacts") or {})
        artifacts["_geo_scan_results"] = {"city": "Berlin"}
        return {
            **state, "artifacts": artifacts,
            "next_agent": "outreach", "current_agent": "geoscout",
            "status": "active",
        }

    async def _outreach_ok(state: dict[str, Any]) -> dict[str, Any]:
        statuses_seen.append(f"outreach:{state['status']}")
        artifacts = dict(state.get("artifacts") or {})
        artifacts["campaign_id"] = str(uuid.uuid4())
        artifacts["_outreach_results"] = {"emails_drafted": 3}
        return {
            **state, "artifacts": artifacts,
            "requires_hitl": True, "current_agent": "outreach",
            "status": "paused",
        }

    async def _hitl_approves(state: dict[str, Any]) -> dict[str, Any]:
        statuses_seen.append(f"hitl:{state['status']}")
        artifacts = dict(state.get("artifacts") or {})
        artifacts["emails_approved"] = True
        return {
            **state, "artifacts": artifacts,
            "requires_hitl": False, "status": "active",
            "current_agent": "hitl_email",
        }

    async def _email_sends(state: dict[str, Any]) -> dict[str, Any]:
        statuses_seen.append(f"email:{state['status']}")
        artifacts = dict(state.get("artifacts") or {})
        artifacts["email_send_result"] = {"sent": 3, "failed": 0}
        return {
            **state, "artifacts": artifacts,
            "current_agent": "email_sending",
            "status": "completed",
        }

    with patch("src.core.graph.geo_scout_node", side_effect=_geo_ok), \
         patch("src.core.graph.outreach_node", side_effect=_outreach_ok), \
         patch("src.core.graph.hitl_email_node", side_effect=_hitl_approves), \
         patch("src.core.graph.email_sending_node", side_effect=_email_sends):
        from src.core.graph import build_pipeline_b_graph
        graph = build_pipeline_b_graph()
        result = await _run_graph(graph, _make_campaign_state())

    # Verify status transitions
    assert statuses_seen[0].startswith("geo:active")
    assert statuses_seen[1].startswith("outreach:active")
    assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# Tests: Routing functions
# ---------------------------------------------------------------------------


async def test_route_after_outreach_hitl_when_emails_drafted():
    """_route_after_outreach routes to hitl_email when requires_hitl."""
    state = {"status": "paused", "requires_hitl": True}
    assert _route_after_outreach(state) == "hitl_email_node"


async def test_route_after_outreach_end_when_no_emails():
    """_route_after_outreach routes to END when no HITL needed."""
    state = {"status": "completed", "requires_hitl": False}
    from langgraph.graph import END
    assert _route_after_outreach(state) == END


async def test_route_after_outreach_end_on_failure():
    """_route_after_outreach routes to END on failure."""
    state = {"status": "failed"}
    from langgraph.graph import END
    assert _route_after_outreach(state) == END


async def test_route_after_hitl_email_send_when_approved():
    """_route_after_hitl_email routes to email_sending when approved."""
    state = {"status": "active", "artifacts": {"emails_approved": True}}
    assert _route_after_hitl_email(state) == "email_sending_node"


async def test_route_after_hitl_email_end_when_not_approved():
    """_route_after_hitl_email routes to END when not approved."""
    from langgraph.graph import END
    state = {"status": "active", "artifacts": {}}
    assert _route_after_hitl_email(state) == END


async def test_route_after_hitl_email_end_on_failure():
    """_route_after_hitl_email routes to END on failure."""
    from langgraph.graph import END
    state = {"status": "failed"}
    assert _route_after_hitl_email(state) == END


# ---------------------------------------------------------------------------
# Tests: email_sending_node
# ---------------------------------------------------------------------------


async def test_email_sending_node_skips_when_not_approved():
    """email_sending_node skips sending when emails_approved is not set."""
    state = _make_campaign_state()
    # emails_approved NOT set
    state["status"] = "active"

    result = await email_sending_node(state)

    assert result["status"] == "completed"
    assert result["current_agent"] == "email_sending"


async def test_email_sending_node_no_campaign_id():
    """email_sending_node handles missing campaign_id gracefully."""
    state = _make_campaign_state()
    state["artifacts"]["emails_approved"] = True
    del state["artifacts"]["campaign_id"]
    state["status"] = "active"

    result = await email_sending_node(state)

    assert result["status"] == "completed"
    assert result["artifacts"]["email_send_result"]["error"] == "no campaign_id"


async def test_hitl_email_node_pauses_when_active():
    """hitl_email_node pauses the workflow when status is not already paused."""
    state = _make_campaign_state()
    state["status"] = "active"  # Not yet paused

    result = await hitl_email_node(state)

    assert result["status"] == "paused"
    assert result["requires_hitl"] is True
    assert result["current_agent"] == "hitl_email"


async def test_hitl_email_node_no_op_when_already_paused():
    """hitl_email_node returns state unchanged when already paused."""
    state = _make_campaign_state()
    state["status"] = "paused"

    result = await hitl_email_node(state)

    assert result["status"] == "paused"
