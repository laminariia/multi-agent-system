"""Integration tests for Pipeline B (GeoScout -> Outreach) graph flow.

These tests exercise the production ``build_pipeline_b_graph()`` with patched
agent node functions.  They verify that:

1. The Pipeline B graph routes correctly (geo_scout -> outreach -> hitl_outreach -> END)
2. State transitions carry correct artifacts through each step
3. Edge cases (no leads, enrichment failure, fallback template) are handled gracefully

All LLM calls, Overpass API, enrichment providers, and DB are mocked.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

from src.core.state import ProjectContext, create_initial_state

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _run_graph(graph, state: dict[str, Any]) -> dict[str, Any]:
    """Run a compiled graph and return the final state.

    Uses ``astream(stream_mode="values")`` instead of ``ainvoke`` to work
    around a LangGraph 1.0.8 issue where ``ainvoke`` returns the initial
    state instead of the final state for complex ``StateGraph(dict)`` graphs.
    """
    last_state = state
    async for chunk in graph.astream(state, stream_mode="values"):
        last_state = chunk
    return last_state


def _make_pipeline_b_state(city: str = "Berlin", thread_id: str | None = None) -> dict[str, Any]:
    """Build a fresh state dict for Pipeline B integration tests."""
    project = ProjectContext(
        project_id=f"pipeline_b_test_{uuid.uuid4().hex[:8]}",
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
        thread_id=thread_id or f"thread-pipb-{uuid.uuid4().hex[:8]}",
    ))
    state["artifacts"] = {"_scan_city": city}
    return state


# ---------------------------------------------------------------------------
# Mock node functions
# ---------------------------------------------------------------------------


async def _geo_scout_finds_leads(state: dict[str, Any]) -> dict[str, Any]:
    """GeoScout finds businesses and hands off to outreach."""
    artifacts = dict(state.get("artifacts") or {})
    artifacts["_geo_scan_results"] = {
        "city": "Berlin",
        "hexagons_total": 25,
        "hexagons_scanned": 25,
        "leads_found": 5,
        "leads_stored": 5,
    }
    artifacts["_lead_osm_ids"] = [100001, 100002, 100003, 100004, 100005]
    return {
        **state,
        "artifacts": artifacts,
        "next_agent": "outreach",
        "current_agent": "geoscout",
        "status": "active",
        "requires_hitl": False,
    }


async def _geo_scout_no_leads(state: dict[str, Any]) -> dict[str, Any]:
    """GeoScout scans but finds no businesses."""
    artifacts = dict(state.get("artifacts") or {})
    artifacts["_geo_scan_results"] = {
        "city": "Berlin",
        "hexagons_total": 25,
        "hexagons_scanned": 25,
        "leads_found": 0,
        "leads_stored": 0,
    }
    artifacts["_lead_osm_ids"] = []
    return {
        **state,
        "artifacts": artifacts,
        "next_agent": None,
        "current_agent": "geoscout",
        "status": "active",
        "requires_hitl": False,
    }


async def _geo_scout_fails(state: dict[str, Any]) -> dict[str, Any]:
    """GeoScout fails (e.g. geocoding error)."""
    return {
        **state,
        "status": "failed",
        "current_agent": "geoscout",
        "next_agent": None,
        "requires_hitl": False,
        "errors": [*state.get("errors", []), "Geocoding failed: City not found"],
    }


async def _outreach_drafts_emails(state: dict[str, Any]) -> dict[str, Any]:
    """Outreach enriches leads and drafts emails, requests HITL."""
    artifacts = dict(state.get("artifacts") or {})
    artifacts["_outreach_results"] = {
        "city": "Berlin",
        "leads_processed": 5,
        "enriched": 3,
        "emails_drafted": 3,
        "enrichment_cost": 0.03,
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


async def _outreach_no_enrichment(state: dict[str, Any]) -> dict[str, Any]:
    """Outreach processes leads but enrichment waterfall fails for all (no emails found)."""
    artifacts = dict(state.get("artifacts") or {})
    artifacts["_outreach_results"] = {
        "city": "Berlin",
        "leads_processed": 5,
        "enriched": 0,
        "emails_drafted": 0,
        "enrichment_cost": 0.0,
    }
    return {
        **state,
        "artifacts": artifacts,
        "requires_hitl": False,
        "current_agent": "outreach",
        "status": "completed",
        "next_agent": None,
    }


async def _outreach_fallback_template(state: dict[str, Any]) -> dict[str, Any]:
    """Outreach enriches leads but LLM fails, uses fallback template.

    Still requests HITL since emails were drafted (via fallback).
    """
    artifacts = dict(state.get("artifacts") or {})
    artifacts["_outreach_results"] = {
        "city": "Berlin",
        "leads_processed": 5,
        "enriched": 2,
        "emails_drafted": 2,
        "enrichment_cost": 0.02,
        "used_fallback_template": True,
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


async def test_pipeline_b_happy_path():
    """Pipeline B: GeoScout finds leads -> Outreach drafts emails -> HITL email approval."""
    patches = {
        "src.core.graph.geo_scout_node": _geo_scout_finds_leads,
        "src.core.graph.outreach_node": _outreach_drafts_emails,
    }
    with patch("src.core.graph.geo_scout_node", side_effect=patches["src.core.graph.geo_scout_node"]), \
         patch("src.core.graph.outreach_node", side_effect=patches["src.core.graph.outreach_node"]):
        from src.core.graph import build_pipeline_b_graph
        graph = build_pipeline_b_graph()
        result = await _run_graph(graph, _make_pipeline_b_state())

    # Pipeline pauses for HITL email approval
    assert result["status"] == "paused"
    assert result["requires_hitl"] is True
    assert result["current_agent"] == "outreach"

    # GeoScout artifacts are preserved
    assert "_geo_scan_results" in result["artifacts"]
    assert result["artifacts"]["_geo_scan_results"]["leads_found"] == 5

    # Outreach artifacts are present
    assert "_outreach_results" in result["artifacts"]
    assert result["artifacts"]["_outreach_results"]["emails_drafted"] == 3
    assert result["artifacts"]["_outreach_results"]["enriched"] == 3

    # HITL request ID is set
    assert result["hitl_request_id"] is not None


async def test_pipeline_b_no_leads_found():
    """Pipeline B: GeoScout scans but finds no businesses -> pipeline ends without outreach."""
    outreach_called = False

    async def tracking_outreach(state: dict[str, Any]) -> dict[str, Any]:
        nonlocal outreach_called
        outreach_called = True
        return state

    with patch("src.core.graph.geo_scout_node", side_effect=_geo_scout_no_leads), \
         patch("src.core.graph.outreach_node", side_effect=tracking_outreach):
        from src.core.graph import build_pipeline_b_graph
        graph = build_pipeline_b_graph()
        result = await _run_graph(graph, _make_pipeline_b_state())

    assert result["status"] == "active"
    assert result["next_agent"] is None
    assert result["requires_hitl"] is False
    assert outreach_called is False, "Outreach should NOT be called when GeoScout finds no leads"

    # Scan results still present
    assert "_geo_scan_results" in result["artifacts"]
    assert result["artifacts"]["_geo_scan_results"]["leads_found"] == 0


async def test_pipeline_b_geo_scout_failure():
    """Pipeline B: GeoScout fails (geocoding error) -> pipeline ends, outreach NOT called."""
    outreach_called = False

    async def tracking_outreach(state: dict[str, Any]) -> dict[str, Any]:
        nonlocal outreach_called
        outreach_called = True
        return state

    with patch("src.core.graph.geo_scout_node", side_effect=_geo_scout_fails), \
         patch("src.core.graph.outreach_node", side_effect=tracking_outreach):
        from src.core.graph import build_pipeline_b_graph
        graph = build_pipeline_b_graph()
        result = await _run_graph(graph, _make_pipeline_b_state())

    assert result["status"] == "failed"
    assert outreach_called is False, "Outreach should NOT be called when GeoScout fails"
    assert any("Geocoding failed" in e for e in result["errors"])


async def test_pipeline_b_enrichment_failure():
    """Pipeline B: Enrichment waterfall fails for all leads -> outreach completes without HITL."""
    with patch("src.core.graph.geo_scout_node", side_effect=_geo_scout_finds_leads), \
         patch("src.core.graph.outreach_node", side_effect=_outreach_no_enrichment):
        from src.core.graph import build_pipeline_b_graph
        graph = build_pipeline_b_graph()
        result = await _run_graph(graph, _make_pipeline_b_state())

    # No emails to approve, so pipeline completes (no HITL)
    assert result["status"] == "completed"
    assert result["requires_hitl"] is False

    # Both agents produced artifacts
    assert "_geo_scan_results" in result["artifacts"]
    assert "_outreach_results" in result["artifacts"]
    assert result["artifacts"]["_outreach_results"]["enriched"] == 0
    assert result["artifacts"]["_outreach_results"]["emails_drafted"] == 0


async def test_pipeline_b_fallback_template():
    """Pipeline B: LLM fails but outreach uses fallback template -> HITL still requested."""
    with patch("src.core.graph.geo_scout_node", side_effect=_geo_scout_finds_leads), \
         patch("src.core.graph.outreach_node", side_effect=_outreach_fallback_template):
        from src.core.graph import build_pipeline_b_graph
        graph = build_pipeline_b_graph()
        result = await _run_graph(graph, _make_pipeline_b_state())

    # Still pauses for HITL even with fallback templates
    assert result["status"] == "paused"
    assert result["requires_hitl"] is True
    assert result["artifacts"]["_outreach_results"]["emails_drafted"] == 2
    assert result["artifacts"]["_outreach_results"]["used_fallback_template"] is True


async def test_pipeline_b_state_transitions():
    """Pipeline B: Verify state transitions are correct at each step."""
    states_seen: list[dict[str, Any]] = []

    async def recording_geo_scout(state: dict[str, Any]) -> dict[str, Any]:
        states_seen.append({"node": "geo_scout", "status": state["status"], "agent": state["current_agent"]})
        return await _geo_scout_finds_leads(state)

    async def recording_outreach(state: dict[str, Any]) -> dict[str, Any]:
        states_seen.append({"node": "outreach", "status": state["status"], "agent": state.get("current_agent")})
        return await _outreach_drafts_emails(state)

    with patch("src.core.graph.geo_scout_node", side_effect=recording_geo_scout), \
         patch("src.core.graph.outreach_node", side_effect=recording_outreach):
        from src.core.graph import build_pipeline_b_graph
        graph = build_pipeline_b_graph()
        result = await _run_graph(graph, _make_pipeline_b_state())

    # Both nodes were visited
    assert len(states_seen) == 2
    assert states_seen[0]["node"] == "geo_scout"
    assert states_seen[1]["node"] == "outreach"

    # GeoScout receives active state
    assert states_seen[0]["status"] == "active"

    # Outreach receives active state (geo_scout set it)
    assert states_seen[1]["status"] == "active"

    # Final state is paused for HITL
    assert result["status"] == "paused"


async def test_pipeline_b_artifacts_accumulate():
    """Pipeline B: Artifacts from both agents are preserved through the pipeline."""
    with patch("src.core.graph.geo_scout_node", side_effect=_geo_scout_finds_leads), \
         patch("src.core.graph.outreach_node", side_effect=_outreach_drafts_emails):
        from src.core.graph import build_pipeline_b_graph
        graph = build_pipeline_b_graph()
        result = await _run_graph(graph, _make_pipeline_b_state())

    # Original scan city is preserved
    assert result["artifacts"]["_scan_city"] == "Berlin"

    # GeoScout artifacts
    scan_results = result["artifacts"]["_geo_scan_results"]
    assert scan_results["city"] == "Berlin"
    assert scan_results["hexagons_scanned"] == 25
    assert scan_results["leads_found"] == 5

    # Lead OSM IDs preserved
    assert len(result["artifacts"]["_lead_osm_ids"]) == 5

    # Outreach artifacts
    outreach_results = result["artifacts"]["_outreach_results"]
    assert outreach_results["city"] == "Berlin"
    assert outreach_results["leads_processed"] == 5
    assert outreach_results["enriched"] == 3
    assert outreach_results["emails_drafted"] == 3
    assert outreach_results["enrichment_cost"] == 0.03
