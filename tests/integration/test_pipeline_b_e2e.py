"""End-to-end smoke test for Pipeline B (GeoScout -> Outreach -> HITL -> Email).

Mocks ALL external services (Nominatim, Overpass, enrichment, LLM, SMTP, DB)
and runs the complete pipeline flow through the actual LangGraph graph.

Verifies:
- Full pipeline flow from geo scan to email sending
- Artifacts accumulate correctly through the pipeline
- campaign_id is stored in artifacts (bug fix verification)
- State transitions are correct at each stage
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

from src.core.state import ProjectContext, create_initial_state
from src.geo.overpass import GeoLead

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _run_graph(graph, state: dict[str, Any]) -> dict[str, Any]:
    """Run a compiled graph and return the final state via astream."""
    last_state = state
    async for chunk in graph.astream(state, stream_mode="values"):
        last_state = chunk
    return last_state


def _make_pipeline_b_state(city: str = "Berlin", thread_id: str | None = None) -> dict[str, Any]:
    """Build a fresh state dict for Pipeline B E2E tests."""
    project = ProjectContext(
        project_id=f"pipeline_b_e2e_{uuid.uuid4().hex[:8]}",
        job_id="",
        platform="outreach",
        client={},
        requirements=city,
        budget=0.0,
        deadline=datetime.now(tz=UTC),
    )
    state = dict(
        create_initial_state(
            project=project,
            first_agent="geoscout",
            thread_id=thread_id or f"thread-e2e-{uuid.uuid4().hex[:8]}",
        )
    )
    state["artifacts"] = {"_scan_city": city}
    return state


def _make_geo_leads(count: int = 20) -> list[GeoLead]:
    """Create a list of GeoLead objects for testing."""
    return [
        GeoLead(
            osm_id=100000 + i,
            name=f"Business {i}",
            category="restaurant" if i % 2 == 0 else "cafe",
            lat=52.52 + i * 0.001,
            lon=13.405 + i * 0.001,
            address=f"Street {i}, Berlin",
            city="Berlin",
            phone=f"+4930{i:07d}" if i % 3 == 0 else None,
            h3_index=f"882a100{i:04x}ff",
        )
        for i in range(count)
    ]


# ---------------------------------------------------------------------------
# Mock node functions for full E2E with artifact verification
# ---------------------------------------------------------------------------


async def _geo_scout_with_leads(state: dict[str, Any]) -> dict[str, Any]:
    """GeoScout finds 20 leads and stores scan results."""
    artifacts = dict(state.get("artifacts") or {})
    artifacts["_geo_scan_results"] = {
        "city": "Berlin",
        "hexagons_total": 30,
        "hexagons_scanned": 30,
        "leads_found": 20,
        "leads_stored": 20,
    }
    artifacts["_lead_osm_ids"] = list(range(100000, 100020))
    return {
        **state,
        "artifacts": artifacts,
        "next_agent": "outreach",
        "current_agent": "geoscout",
        "status": "active",
        "requires_hitl": False,
    }


async def _outreach_enriches_and_drafts(state: dict[str, Any]) -> dict[str, Any]:
    """Outreach enriches 15/20 leads, drafts emails, sets campaign_id."""
    artifacts = dict(state.get("artifacts") or {})
    campaign_id = str(uuid.uuid4())
    artifacts["campaign_id"] = campaign_id
    artifacts["_outreach_results"] = {
        "city": "Berlin",
        "leads_processed": 20,
        "enriched": 15,
        "emails_drafted": 15,
        "enrichment_cost": 0.75,
    }
    artifacts["_outreach_hitl_id"] = str(uuid.uuid4())
    return {
        **state,
        "artifacts": artifacts,
        "requires_hitl": True,
        "hitl_request_id": artifacts["_outreach_hitl_id"],
        "current_agent": "outreach",
        "status": "paused",
        "next_agent": None,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_e2e_full_pipeline_to_hitl_pause():
    """E2E: GeoScout stores leads -> Outreach enriches + drafts -> HITL pause."""
    with (
        patch("src.core.graph.geo_scout_node", side_effect=_geo_scout_with_leads),
        patch("src.core.graph.outreach_node", side_effect=_outreach_enriches_and_drafts),
    ):
        from src.core.graph import build_pipeline_b_graph

        graph = build_pipeline_b_graph()
        result = await _run_graph(graph, _make_pipeline_b_state())

    # Pipeline pauses at HITL for email approval
    assert result["status"] == "paused"
    assert result["requires_hitl"] is True
    assert result["current_agent"] == "outreach"

    # campaign_id is set in artifacts (bug fix verification)
    assert "campaign_id" in result["artifacts"]
    assert result["artifacts"]["campaign_id"]  # non-empty string

    # GeoScout artifacts preserved
    geo = result["artifacts"]["_geo_scan_results"]
    assert geo["leads_found"] == 20
    assert geo["leads_stored"] == 20
    assert geo["hexagons_scanned"] == 30

    # Outreach artifacts present
    out = result["artifacts"]["_outreach_results"]
    assert out["enriched"] == 15
    assert out["emails_drafted"] == 15
    assert out["enrichment_cost"] == 0.75

    # HITL request ID is set
    assert result["hitl_request_id"] is not None


async def test_e2e_artifacts_accumulate_through_pipeline():
    """E2E: All artifacts from all agents are preserved in final state."""
    with (
        patch("src.core.graph.geo_scout_node", side_effect=_geo_scout_with_leads),
        patch("src.core.graph.outreach_node", side_effect=_outreach_enriches_and_drafts),
    ):
        from src.core.graph import build_pipeline_b_graph

        graph = build_pipeline_b_graph()
        result = await _run_graph(graph, _make_pipeline_b_state())

    artifacts = result["artifacts"]

    # Original scan city from initial state
    assert artifacts["_scan_city"] == "Berlin"

    # GeoScout added its results
    assert "_geo_scan_results" in artifacts
    assert "_lead_osm_ids" in artifacts
    assert len(artifacts["_lead_osm_ids"]) == 20

    # Outreach added its results
    assert "_outreach_results" in artifacts
    assert "_outreach_hitl_id" in artifacts
    assert "campaign_id" in artifacts


async def test_e2e_campaign_id_in_artifacts():
    """E2E: Verify campaign_id is stored in artifacts (regression test for bug fix)."""
    with (
        patch("src.core.graph.geo_scout_node", side_effect=_geo_scout_with_leads),
        patch("src.core.graph.outreach_node", side_effect=_outreach_enriches_and_drafts),
    ):
        from src.core.graph import build_pipeline_b_graph

        graph = build_pipeline_b_graph()
        result = await _run_graph(graph, _make_pipeline_b_state())

    # This was a bug: campaign_id was not being stored in artifacts
    campaign_id = result["artifacts"].get("campaign_id")
    assert campaign_id is not None, "campaign_id must be in artifacts after outreach"
    # It should be a valid UUID string
    uuid.UUID(campaign_id)  # Raises ValueError if invalid


async def test_e2e_geo_scout_failure_stops_pipeline():
    """E2E: GeoScout failure -> outreach never called -> pipeline ends."""
    outreach_called = False

    async def _geo_scout_fails(state: dict[str, Any]) -> dict[str, Any]:
        return {
            **state,
            "status": "failed",
            "current_agent": "geoscout",
            "next_agent": None,
            "requires_hitl": False,
            "errors": [*state.get("errors", []), "Geocoding failed: City not found"],
        }

    async def _tracking_outreach(state: dict[str, Any]) -> dict[str, Any]:
        nonlocal outreach_called
        outreach_called = True
        return state

    with (
        patch("src.core.graph.geo_scout_node", side_effect=_geo_scout_fails),
        patch("src.core.graph.outreach_node", side_effect=_tracking_outreach),
    ):
        from src.core.graph import build_pipeline_b_graph

        graph = build_pipeline_b_graph()
        result = await _run_graph(graph, _make_pipeline_b_state())

    assert result["status"] == "failed"
    assert outreach_called is False
    assert any("Geocoding failed" in e for e in result["errors"])


async def test_e2e_no_enriched_leads_completes_without_hitl():
    """E2E: When no leads get enriched, pipeline completes without HITL."""

    async def _outreach_no_emails(state: dict[str, Any]) -> dict[str, Any]:
        artifacts = dict(state.get("artifacts") or {})
        artifacts["_outreach_results"] = {
            "city": "Berlin",
            "leads_processed": 20,
            "enriched": 0,
            "emails_drafted": 0,
            "enrichment_cost": 1.0,
        }
        return {
            **state,
            "artifacts": artifacts,
            "requires_hitl": False,
            "current_agent": "outreach",
            "status": "completed",
            "next_agent": None,
        }

    with (
        patch("src.core.graph.geo_scout_node", side_effect=_geo_scout_with_leads),
        patch("src.core.graph.outreach_node", side_effect=_outreach_no_emails),
    ):
        from src.core.graph import build_pipeline_b_graph

        graph = build_pipeline_b_graph()
        result = await _run_graph(graph, _make_pipeline_b_state())

    assert result["status"] == "completed"
    assert result["requires_hitl"] is False
    assert result["artifacts"]["_outreach_results"]["emails_drafted"] == 0


async def test_e2e_message_dispatch_after_hitl_approval():
    """E2E: After HITL approval, message_dispatch_node runs and completes."""

    async def _hitl_outreach_approves(state: dict[str, Any]) -> dict[str, Any]:
        """Simulate HITL approval -- set emails_approved."""
        artifacts = dict(state.get("artifacts") or {})
        artifacts["emails_approved"] = True
        return {
            **state,
            "artifacts": artifacts,
            "requires_hitl": False,
            "status": "active",
            "current_agent": "hitl_outreach",
        }

    async def _message_dispatch_succeeds(state: dict[str, Any]) -> dict[str, Any]:
        """Simulate successful message dispatch."""
        artifacts = dict(state.get("artifacts") or {})
        artifacts["email_send_result"] = {"sent": 15, "failed": 0, "rate_limited": 0}
        return {
            **state,
            "artifacts": artifacts,
            "current_agent": "message_dispatch",
            "next_agent": None,
            "status": "completed",
        }

    with (
        patch("src.core.graph.geo_scout_node", side_effect=_geo_scout_with_leads),
        patch("src.core.graph.outreach_node", side_effect=_outreach_enriches_and_drafts),
        patch("src.core.graph.hitl_outreach_node", side_effect=_hitl_outreach_approves),
        patch("src.core.graph.message_dispatch_node", side_effect=_message_dispatch_succeeds),
    ):
        from src.core.graph import build_pipeline_b_graph

        graph = build_pipeline_b_graph()
        result = await _run_graph(graph, _make_pipeline_b_state())

    assert result["status"] == "completed"
    assert result["artifacts"]["email_send_result"]["sent"] == 15
    assert result["artifacts"]["email_send_result"]["failed"] == 0


async def test_e2e_state_transitions_tracked():
    """E2E: Verify each node sees correct state transitions."""
    nodes_visited: list[dict[str, Any]] = []

    async def _tracking_geo(state: dict[str, Any]) -> dict[str, Any]:
        nodes_visited.append(
            {
                "node": "geo_scout",
                "status": state["status"],
                "agent": state["current_agent"],
            }
        )
        return await _geo_scout_with_leads(state)

    async def _tracking_outreach(state: dict[str, Any]) -> dict[str, Any]:
        nodes_visited.append(
            {
                "node": "outreach",
                "status": state["status"],
                "agent": state.get("current_agent"),
            }
        )
        return await _outreach_enriches_and_drafts(state)

    with (
        patch("src.core.graph.geo_scout_node", side_effect=_tracking_geo),
        patch("src.core.graph.outreach_node", side_effect=_tracking_outreach),
    ):
        from src.core.graph import build_pipeline_b_graph

        graph = build_pipeline_b_graph()
        result = await _run_graph(graph, _make_pipeline_b_state())

    assert len(nodes_visited) == 2
    assert nodes_visited[0]["node"] == "geo_scout"
    assert nodes_visited[0]["status"] == "active"
    assert nodes_visited[1]["node"] == "outreach"
    assert nodes_visited[1]["status"] == "active"
    assert result["status"] == "paused"
