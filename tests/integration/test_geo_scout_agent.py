"""Integration tests for GeoScoutAgent.

Mocks Nominatim (geocode_city), Overpass (query_businesses), and database
to test the full GeoScout agent flow:
- Lead discovery and deduplication
- Empty results handling
- Rate limiting (max 50 hexagons)
- State updates and artifact generation
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.state import AgentState, ProjectContext, create_initial_state
from src.geo.h3_scanner import BoundingBox
from src.geo.overpass import GeoLead

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_geoscout_state(city: str = "Berlin") -> AgentState:
    """Build a state dict ready for GeoScout agent."""
    project = ProjectContext(
        project_id=f"geoscout_test_{uuid.uuid4().hex[:8]}",
        job_id="",
        platform="outreach",
        client={},
        requirements=city,
        budget=0.0,
        deadline=datetime.now(tz=UTC),
    )
    state = create_initial_state(
        project=project,
        first_agent="geoscout",
        thread_id=f"thread-gs-{uuid.uuid4().hex[:8]}",
    )
    state["artifacts"] = {"_scan_city": city}
    return state


def _make_leads(count: int, start_osm_id: int = 100000) -> list[GeoLead]:
    """Create a list of GeoLead objects."""
    return [
        GeoLead(
            osm_id=start_osm_id + i,
            name=f"Business {i}",
            category="restaurant" if i % 2 == 0 else "cafe",
            lat=52.52 + i * 0.001,
            lon=13.405 + i * 0.001,
            address=f"Street {i}",
            city="Berlin",
            phone=f"+4930{i:07d}" if i % 3 == 0 else None,
            h3_index=f"882a100{i:04x}ff",
        )
        for i in range(count)
    ]


def _mock_db_session():
    """Create a mock async DB session context manager."""
    session = AsyncMock()
    # execute().scalar_one_or_none() returns None (no duplicates by default)
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=mock_result)
    session.add = MagicMock()
    session.commit = AsyncMock()

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, session


# ---------------------------------------------------------------------------
# Tests: Happy path
# ---------------------------------------------------------------------------


async def test_geoscout_finds_leads_and_updates_state():
    """GeoScout: finds leads, stores them, and transitions to outreach."""
    state = _make_geoscout_state("Berlin")
    bbox = BoundingBox(min_lat=52.34, min_lon=13.09, max_lat=52.68, max_lon=13.76)
    leads = _make_leads(10)
    db_ctx, db_session = _mock_db_session()

    with (
        patch("src.agents.geo_scout.geocode_city", new_callable=AsyncMock, return_value=bbox),
        patch("src.agents.geo_scout.generate_hexagons", return_value=["hex1", "hex2", "hex3"]),
        patch("src.agents.geo_scout.hex_to_bbox", return_value=bbox),
        patch("src.agents.geo_scout.get_db_session", return_value=db_ctx),
    ):
        overpass_mock = AsyncMock()
        overpass_mock.query_businesses = AsyncMock(side_effect=[leads[:4], leads[4:7], leads[7:]])
        overpass_mock.close = AsyncMock()

        from src.agents.geo_scout import GeoScoutAgent

        agent = GeoScoutAgent(
            llm_client=AsyncMock(),
            heartbeat=AsyncMock(),
            loop_detector=MagicMock(check=MagicMock(return_value=False)),
            overpass_client=overpass_mock,
        )
        result = await agent._execute(state)

    assert result["next_agent"] == "outreach"
    assert result["current_agent"] == "geoscout"
    assert "_geo_scan_results" in result["artifacts"]
    assert result["artifacts"]["_geo_scan_results"]["leads_found"] == 10
    assert len(result["artifacts"]["_lead_osm_ids"]) == 10


async def test_geoscout_stores_correct_lead_fields():
    """GeoScout: Lead records are created with correct fields from GeoLead."""
    state = _make_geoscout_state("Berlin")
    bbox = BoundingBox(min_lat=52.34, min_lon=13.09, max_lat=52.68, max_lon=13.76)
    leads = [
        GeoLead(
            osm_id=999999,
            name="Test Restaurant",
            category="restaurant",
            lat=52.52,
            lon=13.405,
            address="Main St 1",
            city="Berlin",
            phone="+49301234567",
            h3_index="882a10001ff",
        )
    ]
    db_ctx, db_session = _mock_db_session()

    with (
        patch("src.agents.geo_scout.geocode_city", new_callable=AsyncMock, return_value=bbox),
        patch("src.agents.geo_scout.generate_hexagons", return_value=["hex1"]),
        patch("src.agents.geo_scout.hex_to_bbox", return_value=bbox),
        patch("src.agents.geo_scout.get_db_session", return_value=db_ctx),
    ):
        overpass_mock = AsyncMock()
        overpass_mock.query_businesses = AsyncMock(return_value=leads)
        overpass_mock.close = AsyncMock()

        from src.agents.geo_scout import GeoScoutAgent

        agent = GeoScoutAgent(
            llm_client=AsyncMock(),
            heartbeat=AsyncMock(),
            loop_detector=MagicMock(check=MagicMock(return_value=False)),
            overpass_client=overpass_mock,
        )
        await agent._execute(state)

    # Lead was added to session
    assert db_session.add.call_count == 1
    added_lead = db_session.add.call_args[0][0]
    assert added_lead.name == "Test Restaurant"
    assert added_lead.category == "restaurant"
    assert added_lead.osm_id == 999999
    assert added_lead.phone == "+49301234567"
    assert added_lead.h3_index == "882a10001ff"
    assert added_lead.city == "Berlin"
    assert added_lead.status == "new"


# ---------------------------------------------------------------------------
# Tests: Deduplication
# ---------------------------------------------------------------------------


async def test_geoscout_deduplicates_by_osm_id():
    """GeoScout: same osm_id from different hexagons is not duplicated."""
    state = _make_geoscout_state("Berlin")
    bbox = BoundingBox(min_lat=52.34, min_lon=13.09, max_lat=52.68, max_lon=13.76)

    # Same lead appears in both hexagons
    lead = GeoLead(
        osm_id=111111,
        name="Duplicate Cafe",
        category="cafe",
        lat=52.52,
        lon=13.405,
        h3_index="882a10001ff",
    )
    db_ctx, db_session = _mock_db_session()

    with (
        patch("src.agents.geo_scout.geocode_city", new_callable=AsyncMock, return_value=bbox),
        patch("src.agents.geo_scout.generate_hexagons", return_value=["hex1", "hex2"]),
        patch("src.agents.geo_scout.hex_to_bbox", return_value=bbox),
        patch("src.agents.geo_scout.get_db_session", return_value=db_ctx),
    ):
        overpass_mock = AsyncMock()
        overpass_mock.query_businesses = AsyncMock(return_value=[lead])
        overpass_mock.close = AsyncMock()

        from src.agents.geo_scout import GeoScoutAgent

        agent = GeoScoutAgent(
            llm_client=AsyncMock(),
            heartbeat=AsyncMock(),
            loop_detector=MagicMock(check=MagicMock(return_value=False)),
            overpass_client=overpass_mock,
        )
        result = await agent._execute(state)

    # Only 1 unique lead despite appearing in 2 hexagons
    assert result["artifacts"]["_geo_scan_results"]["leads_found"] == 1
    assert len(result["artifacts"]["_lead_osm_ids"]) == 1


async def test_geoscout_db_dedup_skips_existing():
    """GeoScout: existing lead in DB is skipped (osm_id unique constraint)."""
    state = _make_geoscout_state("Berlin")
    bbox = BoundingBox(min_lat=52.34, min_lon=13.09, max_lat=52.68, max_lon=13.76)
    leads = _make_leads(2)

    # DB session: first lead already exists, second does not
    session = AsyncMock()
    existing_mock = MagicMock()
    existing_mock.scalar_one_or_none.return_value = MagicMock()  # exists
    new_mock = MagicMock()
    new_mock.scalar_one_or_none.return_value = None  # does not exist

    session.execute = AsyncMock(side_effect=[existing_mock, new_mock])
    session.add = MagicMock()
    session.commit = AsyncMock()

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("src.agents.geo_scout.geocode_city", new_callable=AsyncMock, return_value=bbox),
        patch("src.agents.geo_scout.generate_hexagons", return_value=["hex1"]),
        patch("src.agents.geo_scout.hex_to_bbox", return_value=bbox),
        patch("src.agents.geo_scout.get_db_session", return_value=ctx),
    ):
        overpass_mock = AsyncMock()
        overpass_mock.query_businesses = AsyncMock(return_value=leads)
        overpass_mock.close = AsyncMock()

        from src.agents.geo_scout import GeoScoutAgent

        agent = GeoScoutAgent(
            llm_client=AsyncMock(),
            heartbeat=AsyncMock(),
            loop_detector=MagicMock(check=MagicMock(return_value=False)),
            overpass_client=overpass_mock,
        )
        result = await agent._execute(state)

    # Only 1 new lead stored (second one), first was skipped
    assert session.add.call_count == 1
    assert result["artifacts"]["_geo_scan_results"]["leads_stored"] == 1


# ---------------------------------------------------------------------------
# Tests: Empty results
# ---------------------------------------------------------------------------


async def test_geoscout_empty_results_goes_to_outreach():
    """GeoScout: no leads found still transitions to outreach."""
    state = _make_geoscout_state("Berlin")
    bbox = BoundingBox(min_lat=52.34, min_lon=13.09, max_lat=52.68, max_lon=13.76)
    db_ctx, db_session = _mock_db_session()

    with (
        patch("src.agents.geo_scout.geocode_city", new_callable=AsyncMock, return_value=bbox),
        patch("src.agents.geo_scout.generate_hexagons", return_value=["hex1"]),
        patch("src.agents.geo_scout.hex_to_bbox", return_value=bbox),
        patch("src.agents.geo_scout.get_db_session", return_value=db_ctx),
    ):
        overpass_mock = AsyncMock()
        overpass_mock.query_businesses = AsyncMock(return_value=[])
        overpass_mock.close = AsyncMock()

        from src.agents.geo_scout import GeoScoutAgent

        agent = GeoScoutAgent(
            llm_client=AsyncMock(),
            heartbeat=AsyncMock(),
            loop_detector=MagicMock(check=MagicMock(return_value=False)),
            overpass_client=overpass_mock,
        )
        result = await agent._execute(state)

    assert result["artifacts"]["_geo_scan_results"]["leads_found"] == 0
    assert result["next_agent"] == "outreach"


async def test_geoscout_no_city_fails():
    """GeoScout: missing city in state -> fails with error."""
    project = ProjectContext(
        project_id="test",
        job_id="",
        platform="outreach",
        client={},
        requirements="",
        budget=0.0,
        deadline=datetime.now(tz=UTC),
    )
    state = create_initial_state(
        project=project,
        first_agent="geoscout",
        thread_id="thread-no-city",
    )
    state["artifacts"] = {}

    from src.agents.geo_scout import GeoScoutAgent

    agent = GeoScoutAgent(
        llm_client=AsyncMock(),
        heartbeat=AsyncMock(),
        loop_detector=MagicMock(check=MagicMock(return_value=False)),
    )
    result = await agent._execute(state)

    assert result["status"] == "failed"
    assert any("No city" in e for e in result["errors"])


# ---------------------------------------------------------------------------
# Tests: Hexagon rate limiting
# ---------------------------------------------------------------------------


async def test_geoscout_truncates_hexagons_at_50():
    """GeoScout: limits hexagons to 50 per scan."""
    state = _make_geoscout_state("Berlin")
    bbox = BoundingBox(min_lat=52.34, min_lon=13.09, max_lat=52.68, max_lon=13.76)
    # Generate 80 hexagons
    hexagons = [f"hex_{i}" for i in range(80)]
    db_ctx, db_session = _mock_db_session()

    with (
        patch("src.agents.geo_scout.geocode_city", new_callable=AsyncMock, return_value=bbox),
        patch("src.agents.geo_scout.generate_hexagons", return_value=hexagons),
        patch("src.agents.geo_scout.hex_to_bbox", return_value=bbox),
        patch("src.agents.geo_scout.get_db_session", return_value=db_ctx),
    ):
        overpass_mock = AsyncMock()
        overpass_mock.query_businesses = AsyncMock(return_value=[])
        overpass_mock.close = AsyncMock()

        from src.agents.geo_scout import GeoScoutAgent

        agent = GeoScoutAgent(
            llm_client=AsyncMock(),
            heartbeat=AsyncMock(),
            loop_detector=MagicMock(check=MagicMock(return_value=False)),
            overpass_client=overpass_mock,
        )
        result = await agent._execute(state)

    # Overpass was called 50 times (truncated from 80)
    assert overpass_mock.query_businesses.await_count == 50
    # State records total vs scanned
    assert result["artifacts"]["_geo_scan_results"]["hexagons_total"] == 80
    assert result["artifacts"]["_geo_scan_results"]["hexagons_scanned"] == 50


# ---------------------------------------------------------------------------
# Tests: Error handling
# ---------------------------------------------------------------------------


async def test_geoscout_geocoding_error_fails():
    """GeoScout: geocoding ValueError -> status=failed."""
    state = _make_geoscout_state("NonexistentCity")

    with patch("src.agents.geo_scout.geocode_city", new_callable=AsyncMock, side_effect=ValueError("City not found")):
        from src.agents.geo_scout import GeoScoutAgent

        agent = GeoScoutAgent(
            llm_client=AsyncMock(),
            heartbeat=AsyncMock(),
            loop_detector=MagicMock(check=MagicMock(return_value=False)),
        )
        result = await agent._execute(state)

    assert result["status"] == "failed"
    assert any("Geocoding failed" in e for e in result["errors"])


async def test_geoscout_overpass_error_fails():
    """GeoScout: Overpass API error -> status=failed."""
    state = _make_geoscout_state("Berlin")
    bbox = BoundingBox(min_lat=52.34, min_lon=13.09, max_lat=52.68, max_lon=13.76)

    with (
        patch("src.agents.geo_scout.geocode_city", new_callable=AsyncMock, return_value=bbox),
        patch("src.agents.geo_scout.generate_hexagons", return_value=["hex1"]),
        patch("src.agents.geo_scout.hex_to_bbox", return_value=bbox),
    ):
        overpass_mock = AsyncMock()
        overpass_mock.query_businesses = AsyncMock(side_effect=RuntimeError("Overpass 429"))
        overpass_mock.close = AsyncMock()

        from src.agents.geo_scout import GeoScoutAgent

        agent = GeoScoutAgent(
            llm_client=AsyncMock(),
            heartbeat=AsyncMock(),
            loop_detector=MagicMock(check=MagicMock(return_value=False)),
            overpass_client=overpass_mock,
        )
        result = await agent._execute(state)

    assert result["status"] == "failed"
    assert any("Geo scan error" in e for e in result["errors"])
