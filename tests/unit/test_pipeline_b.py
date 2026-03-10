"""Unit tests for Pipeline B -- GeoScout, Outreach, routing, and API."""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.graph import END

from src.agents.geo_scout import GeoScoutAgent, geo_scout_node
from src.agents.outreach import OutreachAgent, _parse_email_json, outreach_node
from src.core.graph import (
    _route_after_geo_scout,
    _route_after_hitl_email,
    _route_after_hitl_outreach,
    _route_after_outreach,
    build_pipeline_b_graph,
    hitl_email_node,
    hitl_outreach_node,
)
from src.core.models import EmailCampaign, HITLQueue, Lead
from src.enrichment.waterfall import EnrichmentResult, EnrichmentWaterfall
from src.geo.h3_scanner import BoundingBox
from src.geo.overpass import GeoLead

# ===========================================================================
# Helpers
# ===========================================================================


def _make_state(**overrides) -> dict[str, Any]:
    """Create minimal test state."""
    base = {
        "thread_id": "test-thread",
        "mas_checkpoint_id": "test-cp",
        "project": {
            "project_id": "p1",
            "job_id": "",
            "platform": "outreach",
            "client": {},
            "requirements": "Berlin",
            "budget": 0,
            "deadline": datetime.now(UTC),
        },
        "current_agent": "geoscout",
        "current_task": None,
        "artifacts": {},
        "messages": [],
        "next_agent": None,
        "requires_hitl": False,
        "hitl_request_id": None,
        "retry_count": 0,
        "errors": [],
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
        "status": "active",
    }
    base.update(overrides)
    return base


# ===========================================================================
# GeoScoutAgent tests
# ===========================================================================


@pytest.mark.asyncio
async def test_geo_scout_execute_success():
    """GeoScout: _execute success — mock geocode, hexagons, Overpass."""
    mock_llm = MagicMock()
    mock_heartbeat = MagicMock()
    mock_loop_detector = MagicMock()
    mock_overpass = AsyncMock()

    bbox = BoundingBox(
        min_lat=52.3,
        max_lat=52.7,
        min_lon=13.1,
        max_lon=13.6,
    )
    geo_lead = GeoLead(
        osm_id=123,
        name="Test Cafe",
        category="cafe",
        lat=52.5,
        lon=13.4,
        h3_index="891f1d4d9dfffff",
        address="Test St",
        phone=None,
    )

    mock_overpass.query_businesses.return_value = [geo_lead]

    agent = GeoScoutAgent(
        llm_client=mock_llm,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
        overpass_client=mock_overpass,
    )

    state = _make_state(artifacts={"_scan_city": "Berlin"})

    with (
        patch("src.agents.geo_scout.geocode_city", return_value=bbox),
        patch("src.agents.geo_scout.generate_hexagons", return_value=["hex1", "hex2"]),
        patch("src.agents.geo_scout.hex_to_bbox", return_value=bbox),
        patch("src.agents.geo_scout.get_db_session") as mock_db,
    ):
        mock_session = AsyncMock()
        mock_session.execute.return_value.scalar_one_or_none.return_value = None
        mock_db.return_value.__aenter__.return_value = mock_session

        result = await agent._execute(state)  # noqa: SLF001

    assert result["status"] == "active"
    assert result["next_agent"] == "outreach"
    assert "_geo_scan_results" in result["artifacts"]
    # Each hex returns one lead, deduped by osm_id
    assert result["artifacts"]["_geo_scan_results"]["leads_found"] == 1
    assert result["artifacts"]["_lead_osm_ids"] == [123]


@pytest.mark.asyncio
async def test_geo_scout_execute_no_city():
    """GeoScout: _execute no city in state → fails."""
    mock_llm = MagicMock()
    mock_heartbeat = MagicMock()
    mock_loop_detector = MagicMock()

    agent = GeoScoutAgent(
        llm_client=mock_llm,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    state = _make_state(artifacts={}, project={"requirements": ""})
    result = await agent._execute(state)  # noqa: SLF001

    assert result["status"] == "failed"
    assert "No city specified" in result["errors"][-1]
    assert result["next_agent"] is None


@pytest.mark.asyncio
async def test_geo_scout_execute_geocoding_error():
    """GeoScout: _execute geocoding ValueError → fails gracefully."""
    mock_llm = MagicMock()
    mock_heartbeat = MagicMock()
    mock_loop_detector = MagicMock()

    agent = GeoScoutAgent(
        llm_client=mock_llm,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    state = _make_state(artifacts={"_scan_city": "InvalidCity"})

    with patch(
        "src.agents.geo_scout.geocode_city",
        side_effect=ValueError("City not found"),
    ):
        result = await agent._execute(state)  # noqa: SLF001

    assert result["status"] == "failed"
    assert "Geocoding failed" in result["errors"][-1]
    assert result["next_agent"] is None


@pytest.mark.asyncio
async def test_geo_scout_execute_general_exception():
    """GeoScout: _execute general exception → fails gracefully."""
    mock_llm = MagicMock()
    mock_heartbeat = MagicMock()
    mock_loop_detector = MagicMock()
    mock_overpass = AsyncMock()
    mock_overpass.query_businesses.side_effect = RuntimeError("Overpass error")

    agent = GeoScoutAgent(
        llm_client=mock_llm,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
        overpass_client=mock_overpass,
    )

    state = _make_state(artifacts={"_scan_city": "Berlin"})

    with (
        patch(
            "src.agents.geo_scout.geocode_city",
            return_value=BoundingBox(52.3, 52.7, 13.1, 13.6),
        ),
        patch("src.agents.geo_scout.generate_hexagons", return_value=["hex1"]),
        patch(
            "src.agents.geo_scout.hex_to_bbox",
            return_value=BoundingBox(52.3, 52.7, 13.1, 13.6),
        ),
    ):
        result = await agent._execute(state)  # noqa: SLF001

    assert result["status"] == "failed"
    assert "Geo scan error" in result["errors"][-1]
    assert result["next_agent"] is None


@pytest.mark.asyncio
async def test_geo_scout_store_leads():
    """GeoScout: _store_leads stores new leads, skips duplicates."""
    mock_llm = MagicMock()
    mock_heartbeat = MagicMock()
    mock_loop_detector = MagicMock()

    agent = GeoScoutAgent(
        llm_client=mock_llm,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    geo_leads = [
        GeoLead(123, "Cafe A", "cafe", 52.5, 13.4, "hex1", "Address A", None),
        GeoLead(124, "Cafe B", "cafe", 52.5, 13.4, "hex2", "Address B", None),
    ]

    mock_session = AsyncMock()
    # Mock two execute calls - each awaits and returns a result with scalar_one_or_none
    mock_exec_result_1 = MagicMock()
    mock_exec_result_1.scalar_one_or_none.return_value = None  # First lead: not found
    mock_exec_result_2 = MagicMock()
    mock_exec_result_2.scalar_one_or_none.return_value = MagicMock()  # Second lead: found (existing)

    # Mock execute to await properly
    async def mock_execute_impl(query):
        if mock_session.execute.call_count == 1:
            return mock_exec_result_1
        return mock_exec_result_2

    mock_session.execute = AsyncMock(side_effect=mock_execute_impl)

    with patch("src.agents.geo_scout.get_db_session") as mock_db:
        mock_db.return_value.__aenter__.return_value = mock_session
        stored_count = await agent._store_leads(geo_leads, "Berlin")  # noqa: SLF001

    assert stored_count == 1
    assert mock_session.add.call_count == 1


@pytest.mark.asyncio
async def test_geo_scout_city_from_artifacts():
    """GeoScout: city extracted from artifacts._scan_city."""
    mock_llm = MagicMock()
    mock_heartbeat = MagicMock()
    mock_loop_detector = MagicMock()
    mock_overpass = AsyncMock()
    mock_overpass.query_businesses.return_value = []

    agent = GeoScoutAgent(
        llm_client=mock_llm,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
        overpass_client=mock_overpass,
    )

    state = _make_state(artifacts={"_scan_city": "Munich"}, project={"requirements": "Berlin"})

    with (
        patch(
            "src.agents.geo_scout.geocode_city",
            return_value=BoundingBox(48.0, 48.2, 11.4, 11.8),
        ),
        patch("src.agents.geo_scout.generate_hexagons", return_value=[]),
        patch("src.agents.geo_scout.get_db_session") as mock_db,
    ):
        mock_session = AsyncMock()
        mock_db.return_value.__aenter__.return_value = mock_session
        result = await agent._execute(state)  # noqa: SLF001

    # Should use Munich from artifacts, not Berlin from project
    assert result["artifacts"]["_geo_scan_results"]["city"] == "Munich"


@pytest.mark.asyncio
async def test_geo_scout_city_fallback_to_project():
    """GeoScout: city falls back to project.requirements."""
    mock_llm = MagicMock()
    mock_heartbeat = MagicMock()
    mock_loop_detector = MagicMock()
    mock_overpass = AsyncMock()
    mock_overpass.query_businesses.return_value = []

    agent = GeoScoutAgent(
        llm_client=mock_llm,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
        overpass_client=mock_overpass,
    )

    state = _make_state(artifacts={}, project={"requirements": "Hamburg"})

    with (
        patch(
            "src.agents.geo_scout.geocode_city",
            return_value=BoundingBox(53.4, 53.7, 9.8, 10.2),
        ),
        patch("src.agents.geo_scout.generate_hexagons", return_value=[]),
        patch("src.agents.geo_scout.get_db_session") as mock_db,
    ):
        mock_session = AsyncMock()
        mock_db.return_value.__aenter__.return_value = mock_session
        result = await agent._execute(state)  # noqa: SLF001

    assert result["artifacts"]["_geo_scan_results"]["city"] == "Hamburg"


@pytest.mark.asyncio
async def test_geo_scout_node():
    """GeoScout: geo_scout_node wrapper creates agent and runs."""
    state = _make_state(artifacts={"_scan_city": "Berlin"})

    # Mock GeoScoutAgent directly
    mock_agent = AsyncMock()
    mock_agent.invoke = AsyncMock(return_value={
        **state,
        "status": "active",
        "next_agent": "outreach",
    })
    mock_agent._overpass = MagicMock()  # noqa: SLF001
    mock_agent._overpass.close = AsyncMock()  # noqa: SLF001

    with (
        patch("src.agents.geo_scout.GeoScoutAgent", return_value=mock_agent),
        patch("src.core.llm_client.LLMClient"),
        patch("src.core.heartbeat.HeartbeatMonitor"),
        patch("src.core.loop_detector.LoopDetector"),
    ):
        result = await geo_scout_node(state)

    assert result["status"] == "active"
    assert result["next_agent"] == "outreach"


# ===========================================================================
# OutreachAgent tests
# ===========================================================================


@pytest.mark.asyncio
async def test_outreach_execute_success():
    """Outreach: _execute success — mock load_leads, enrich, generate emails."""
    mock_llm = MagicMock()
    mock_heartbeat = MagicMock()
    mock_loop_detector = MagicMock()
    mock_waterfall = AsyncMock(spec=EnrichmentWaterfall)
    mock_waterfall.total_cost = Decimal("0.10")

    # Mock enrichment result
    mock_waterfall.enrich.return_value = EnrichmentResult(
        email="test@cafe.com",
        phone="+49123456",
        source="osint",
        raw_data={"cost": "0.05"},
    )

    agent = OutreachAgent(
        llm_client=mock_llm,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
        waterfall=mock_waterfall,
    )

    # Mock LLM response
    agent._call_llm = AsyncMock(  # noqa: SLF001
        return_value=(
            MagicMock(content='{"subject": "Test", "body": "Test body"}'),
            {},
        )
    )

    state = _make_state(
        artifacts={"_geo_scan_results": {"city": "Berlin"}},
    )

    # Create mock Lead
    mock_lead = MagicMock(spec=Lead)
    mock_lead.id = uuid.uuid4()
    mock_lead.name = "Test Cafe"
    mock_lead.category = "cafe"
    mock_lead.city = "Berlin"
    mock_lead.address = "Test St"
    mock_lead.email = None

    # Mock campaign and HITL creation
    mock_campaign = MagicMock(spec=EmailCampaign)
    mock_campaign.id = uuid.uuid4()
    mock_hitl = MagicMock(spec=HITLQueue)
    mock_hitl.id = uuid.uuid4()

    with (
        patch("src.agents.outreach.get_db_session") as mock_db,
        patch("src.agents.outreach.EmailCampaign", return_value=mock_campaign),
        patch("src.agents.outreach.HITLQueue", return_value=mock_hitl),
    ):
        mock_session = AsyncMock()

        # Mock _load_leads (select leads) - need awaitable
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_lead]
        mock_exec_result = MagicMock()
        mock_exec_result.scalars.return_value = mock_scalars

        # Multiple execute calls: load_leads, enrich (update), generate (add), hitl (add)
        mock_session.execute = AsyncMock(return_value=mock_exec_result)

        mock_db.return_value.__aenter__.return_value = mock_session

        result = await agent._execute(state)  # noqa: SLF001

    assert result["status"] == "paused"
    assert result["requires_hitl"] is True
    assert "_outreach_results" in result["artifacts"]
    assert result["artifacts"]["_outreach_results"]["enriched"] == 1
    assert result["artifacts"]["_outreach_results"]["emails_drafted"] == 1


@pytest.mark.asyncio
async def test_outreach_execute_no_city():
    """Outreach: _execute no city → fails."""
    mock_llm = MagicMock()
    mock_heartbeat = MagicMock()
    mock_loop_detector = MagicMock()

    agent = OutreachAgent(
        llm_client=mock_llm,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    state = _make_state(artifacts={}, project={"requirements": ""})
    result = await agent._execute(state)  # noqa: SLF001

    assert result["status"] == "failed"
    assert "No city context" in result["errors"][-1]
    assert result["next_agent"] is None


@pytest.mark.asyncio
async def test_outreach_execute_no_leads():
    """Outreach: _execute no leads found → completed (no HITL)."""
    mock_llm = MagicMock()
    mock_heartbeat = MagicMock()
    mock_loop_detector = MagicMock()

    agent = OutreachAgent(
        llm_client=mock_llm,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    state = _make_state(
        artifacts={"_geo_scan_results": {"city": "Berlin"}},
    )

    with patch("src.agents.outreach.get_db_session") as mock_db:
        mock_session = AsyncMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_exec_result = MagicMock()
        mock_exec_result.scalars.return_value = mock_scalars
        mock_session.execute = AsyncMock(return_value=mock_exec_result)
        mock_db.return_value.__aenter__.return_value = mock_session

        result = await agent._execute(state)  # noqa: SLF001

    assert result["status"] == "completed"
    assert result["requires_hitl"] is False
    assert result["artifacts"]["_outreach_results"]["enriched"] == 0
    assert result["next_agent"] is None


@pytest.mark.asyncio
async def test_outreach_get_waterfall_env_vars():
    """Outreach: _get_waterfall creates waterfall from settings when no user_id."""
    mock_llm = MagicMock()
    mock_heartbeat = MagicMock()
    mock_loop_detector = MagicMock()

    agent = OutreachAgent(
        llm_client=mock_llm,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
        waterfall=None,
    )

    mock_waterfall_instance = MagicMock(spec=EnrichmentWaterfall)

    with (
        patch("src.core.config.get_settings") as mock_settings,
        patch("src.agents.outreach.EnrichmentWaterfall", return_value=mock_waterfall_instance) as mock_wf_cls,
    ):
        mock_settings_instance = MagicMock()
        mock_settings_instance.HUNTER_API_KEY = "hunter_key"
        mock_settings_instance.APOLLO_API_KEY = "apollo_key"
        mock_settings.return_value = mock_settings_instance

        waterfall = await agent._get_waterfall()  # noqa: SLF001

    mock_wf_cls.assert_called_once_with(
        hunter_api_key="hunter_key",
        apollo_api_key="apollo_key",
    )
    assert waterfall == mock_waterfall_instance


@pytest.mark.asyncio
async def test_outreach_get_waterfall_db_credentials():
    """Outreach: _get_waterfall loads credentials from DB when user_id provided."""
    mock_llm = MagicMock()
    mock_heartbeat = MagicMock()
    mock_loop_detector = MagicMock()

    agent = OutreachAgent(
        llm_client=mock_llm,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
        waterfall=None,
    )

    mock_waterfall_instance = MagicMock(spec=EnrichmentWaterfall)

    async def fake_get_credential(key_name, user_id=None):
        creds = {"hunter_api_key": "db_hunter_key", "apollo_api_key": "db_apollo_key"}
        return creds.get(key_name)

    with (
        patch.object(agent, "_get_credential", side_effect=fake_get_credential),
        patch("src.agents.outreach.EnrichmentWaterfall", return_value=mock_waterfall_instance) as mock_wf_cls,
    ):
        waterfall = await agent._get_waterfall(user_id="user-123")  # noqa: SLF001

    mock_wf_cls.assert_called_once_with(
        hunter_api_key="db_hunter_key",
        apollo_api_key="db_apollo_key",
    )
    assert waterfall == mock_waterfall_instance


@pytest.mark.asyncio
async def test_outreach_get_waterfall_db_partial_fallback():
    """Outreach: _get_waterfall falls back to env when DB has only one key."""
    mock_llm = MagicMock()
    mock_heartbeat = MagicMock()
    mock_loop_detector = MagicMock()

    agent = OutreachAgent(
        llm_client=mock_llm,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
        waterfall=None,
    )

    mock_waterfall_instance = MagicMock(spec=EnrichmentWaterfall)

    async def fake_get_credential(key_name, user_id=None):
        # Only hunter key in DB, no apollo
        if key_name == "hunter_api_key":
            return "db_hunter_key"
        return None

    with (
        patch.object(agent, "_get_credential", side_effect=fake_get_credential),
        patch("src.core.config.get_settings") as mock_settings,
        patch("src.agents.outreach.EnrichmentWaterfall", return_value=mock_waterfall_instance) as mock_wf_cls,
    ):
        mock_settings_instance = MagicMock()
        mock_settings_instance.HUNTER_API_KEY = "env_hunter_key"
        mock_settings_instance.APOLLO_API_KEY = "env_apollo_key"
        mock_settings.return_value = mock_settings_instance

        waterfall = await agent._get_waterfall(user_id="user-123")  # noqa: SLF001

    # Hunter from DB, Apollo from env fallback
    mock_wf_cls.assert_called_once_with(
        hunter_api_key="db_hunter_key",
        apollo_api_key="env_apollo_key",
    )
    assert waterfall == mock_waterfall_instance


@pytest.mark.asyncio
async def test_outreach_get_waterfall_uses_cached():
    """Outreach: _get_waterfall returns cached waterfall on second call."""
    mock_llm = MagicMock()
    mock_heartbeat = MagicMock()
    mock_loop_detector = MagicMock()

    pre_waterfall = MagicMock(spec=EnrichmentWaterfall)
    agent = OutreachAgent(
        llm_client=mock_llm,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
        waterfall=pre_waterfall,
    )

    result = await agent._get_waterfall()  # noqa: SLF001
    assert result is pre_waterfall


def test_parse_email_json_valid():
    """Outreach: _parse_email_json parses valid JSON."""
    text = '{"subject": "Test", "body": "Body"}'
    result = _parse_email_json(text)
    assert result == {"subject": "Test", "body": "Body"}


def test_parse_email_json_markdown_fences():
    """Outreach: _parse_email_json strips markdown code fences."""
    text = '```json\n{"subject": "Test", "body": "Body"}\n```'
    result = _parse_email_json(text)
    assert result == {"subject": "Test", "body": "Body"}


def test_parse_email_json_invalid():
    """Outreach: _parse_email_json returns exception on invalid JSON."""
    text = "not json"
    with pytest.raises((json.JSONDecodeError, ValueError)):
        _parse_email_json(text)


@pytest.mark.asyncio
async def test_outreach_load_leads():
    """Outreach: _load_leads loads unenriched leads for city."""
    mock_llm = MagicMock()
    mock_heartbeat = MagicMock()
    mock_loop_detector = MagicMock()

    agent = OutreachAgent(
        llm_client=mock_llm,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    mock_lead = MagicMock(spec=Lead)
    mock_lead.id = uuid.uuid4()
    mock_lead.name = "Test"

    with patch("src.agents.outreach.get_db_session") as mock_db:
        mock_session = AsyncMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_lead]
        mock_exec_result = MagicMock()
        mock_exec_result.scalars.return_value = mock_scalars
        mock_session.execute = AsyncMock(return_value=mock_exec_result)
        mock_db.return_value.__aenter__.return_value = mock_session

        leads = await agent._load_leads("Berlin")  # noqa: SLF001

    assert len(leads) == 1
    assert leads[0].name == "Test"


@pytest.mark.asyncio
async def test_outreach_enrich_leads():
    """Outreach: _enrich_leads enriches with waterfall."""
    mock_llm = MagicMock()
    mock_heartbeat = MagicMock()
    mock_loop_detector = MagicMock()

    agent = OutreachAgent(
        llm_client=mock_llm,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    mock_lead = MagicMock(spec=Lead)
    mock_lead.id = uuid.uuid4()
    mock_lead.name = "Test"
    mock_lead.city = "Berlin"
    mock_lead.phone = None
    mock_lead.email = None

    mock_waterfall = AsyncMock(spec=EnrichmentWaterfall)
    mock_waterfall.enrich.return_value = EnrichmentResult(
        email="found@test.com",
        phone="+49123",
        source="osint",
        raw_data={"cost": "0.01"},
    )

    with patch("src.agents.outreach.get_db_session") as mock_db:
        mock_session = AsyncMock()
        mock_db.return_value.__aenter__.return_value = mock_session

        enriched = await agent._enrich_leads([mock_lead], mock_waterfall)  # noqa: SLF001

    assert len(enriched) == 1
    assert enriched[0].email == "found@test.com"
    assert enriched[0].status == "enriched"


@pytest.mark.asyncio
async def test_outreach_create_campaign():
    """Outreach: _create_campaign creates EmailCampaign."""
    mock_llm = MagicMock()
    mock_heartbeat = MagicMock()
    mock_loop_detector = MagicMock()

    agent = OutreachAgent(
        llm_client=mock_llm,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    mock_campaign = MagicMock(spec=EmailCampaign)
    mock_campaign.id = uuid.uuid4()

    with patch("src.agents.outreach.get_db_session") as mock_db:
        mock_session = AsyncMock()
        mock_session.refresh = AsyncMock()
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()

        # Mock the campaign creation
        with patch("src.agents.outreach.EmailCampaign", return_value=mock_campaign):
            mock_db.return_value.__aenter__.return_value = mock_session
            campaign = await agent._create_campaign("Berlin")  # noqa: SLF001

    assert campaign.id is not None
    mock_session.add.assert_called_once()


@pytest.mark.asyncio
async def test_outreach_generate_messages():
    """Outreach: _generate_messages generates via LLM."""
    mock_llm = MagicMock()
    mock_heartbeat = MagicMock()
    mock_loop_detector = MagicMock()

    agent = OutreachAgent(
        llm_client=mock_llm,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    agent._call_llm = AsyncMock(  # noqa: SLF001
        return_value=(
            MagicMock(content='{"subject": "Test", "body": "Body"}'),
            {},
        )
    )

    mock_lead = MagicMock(spec=Lead)
    mock_lead.id = uuid.uuid4()
    mock_lead.name = "Test Cafe"
    mock_lead.category = "cafe"
    mock_lead.city = "Berlin"
    mock_lead.address = "Test St"
    mock_lead.email = "test@cafe.com"
    mock_lead.telegram_username = None  # email channel

    mock_campaign = MagicMock(spec=EmailCampaign)
    mock_campaign.id = uuid.uuid4()

    with patch("src.agents.outreach.get_db_session") as mock_db:
        mock_session = AsyncMock()
        mock_db.return_value.__aenter__.return_value = mock_session

        count, channel_counts = await agent._generate_messages([mock_lead], mock_campaign)  # noqa: SLF001

    assert count == 1
    assert channel_counts["email"] == 1
    assert mock_session.add.call_count == 2  # CampaignLead + campaign


@pytest.mark.asyncio
async def test_outreach_create_hitl_request():
    """Outreach: _create_hitl_request creates HITL entry."""
    mock_llm = MagicMock()
    mock_heartbeat = MagicMock()
    mock_loop_detector = MagicMock()

    agent = OutreachAgent(
        llm_client=mock_llm,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    mock_campaign = MagicMock(spec=EmailCampaign)
    mock_campaign.id = uuid.uuid4()

    mock_hitl = MagicMock(spec=HITLQueue)
    mock_hitl.id = uuid.uuid4()

    with patch("src.agents.outreach.get_db_session") as mock_db:
        mock_session = AsyncMock()
        mock_session.refresh = AsyncMock()

        with patch("src.agents.outreach.HITLQueue", return_value=mock_hitl):
            mock_db.return_value.__aenter__.return_value = mock_session
            hitl_id = await agent._create_hitl_request(mock_campaign, "Berlin", 5)  # noqa: SLF001

    assert hitl_id is not None
    mock_session.add.assert_called_once()


@pytest.mark.asyncio
async def test_outreach_node():
    """Outreach: outreach_node wrapper creates agent and runs."""
    state = _make_state(
        artifacts={"_geo_scan_results": {"city": "Berlin"}},
    )

    # Mock OutreachAgent directly
    mock_agent = AsyncMock()
    mock_agent.invoke = AsyncMock(return_value={
        **state,
        "status": "completed",
    })
    mock_agent._waterfall = None  # noqa: SLF001

    with (
        patch("src.agents.outreach.OutreachAgent", return_value=mock_agent),
        patch("src.core.llm_client.LLMClient"),
        patch("src.core.heartbeat.HeartbeatMonitor"),
        patch("src.core.loop_detector.LoopDetector"),
    ):
        result = await outreach_node(state)

    assert result["status"] == "completed"


# ===========================================================================
# Pipeline B routing tests
# ===========================================================================


def test_route_after_geo_scout_to_outreach():
    """Routing: _route_after_geo_scout → outreach_node when next_agent=outreach."""
    state = _make_state(status="active", next_agent="outreach")
    result = _route_after_geo_scout(state)
    assert result == "outreach_node"


def test_route_after_geo_scout_to_end_failed():
    """Routing: _route_after_geo_scout → END when status=failed."""
    state = _make_state(status="failed", next_agent="outreach")
    result = _route_after_geo_scout(state)
    assert result == END


def test_route_after_geo_scout_to_end_no_next():
    """Routing: _route_after_geo_scout → END when no next_agent."""
    state = _make_state(status="active", next_agent=None)
    result = _route_after_geo_scout(state)
    assert result == END


def test_route_after_outreach_to_hitl_outreach():
    """Routing: _route_after_outreach → hitl_outreach_node when requires_hitl=True."""
    state = _make_state(status="paused", requires_hitl=True)
    result = _route_after_outreach(state)
    assert result == "hitl_outreach_node"


def test_route_after_outreach_to_end_failed():
    """Routing: _route_after_outreach → END when status=failed."""
    state = _make_state(status="failed", requires_hitl=False)
    result = _route_after_outreach(state)
    assert result == END


def test_route_after_outreach_to_end_no_hitl():
    """Routing: _route_after_outreach → END when no HITL needed."""
    state = _make_state(status="completed", requires_hitl=False)
    result = _route_after_outreach(state)
    assert result == END


def test_route_after_hitl_outreach_no_approval():
    """Routing: _route_after_hitl_outreach → END when not approved."""
    state = _make_state()
    result = _route_after_hitl_outreach(state)
    assert result == END


def test_route_after_hitl_outreach_approved():
    """Routing: _route_after_hitl_outreach → message_dispatch_node when approved."""
    state = _make_state(artifacts={"emails_approved": True})
    result = _route_after_hitl_outreach(state)
    assert result == "message_dispatch_node"


def test_route_after_hitl_outreach_failed():
    """Routing: _route_after_hitl_outreach → END when status=failed."""
    state = _make_state(status="failed", artifacts={"emails_approved": True})
    result = _route_after_hitl_outreach(state)
    assert result == END


def test_route_after_hitl_email_backward_compat():
    """Routing: _route_after_hitl_email is alias for _route_after_hitl_outreach."""
    assert _route_after_hitl_email is _route_after_hitl_outreach


# ===========================================================================
# hitl_outreach_node tests
# ===========================================================================


@pytest.mark.asyncio
async def test_hitl_outreach_node_pauses():
    """HITL outreach: sets status=paused and requires_hitl=True."""
    state = _make_state(status="active", requires_hitl=False)
    result = await hitl_outreach_node(state)

    assert result["status"] == "paused"
    assert result["requires_hitl"] is True
    assert result["current_agent"] == "hitl_outreach"


@pytest.mark.asyncio
async def test_hitl_outreach_node_already_paused():
    """HITL outreach: returns state unchanged when already paused."""
    state = _make_state(status="paused", requires_hitl=True)
    result = await hitl_outreach_node(state)

    assert result == state


@pytest.mark.asyncio
async def test_hitl_email_node_backward_compat():
    """hitl_email_node is alias for hitl_outreach_node."""
    assert hitl_email_node is hitl_outreach_node


# ===========================================================================
# build_pipeline_b_graph tests
# ===========================================================================


def test_build_pipeline_b_graph_no_checkpointer():
    """Graph: build_pipeline_b_graph returns compiled graph."""
    graph = build_pipeline_b_graph()
    assert graph is not None
    # Check nodes exist
    assert "geo_scout_node" in graph.nodes
    assert "outreach_node" in graph.nodes
    assert "hitl_outreach_node" in graph.nodes
    assert "message_dispatch_node" in graph.nodes


def test_build_pipeline_b_graph_with_checkpointer():
    """Graph: build_pipeline_b_graph accepts optional checkpointer."""
    # Create a minimal checkpointer mock that won't cause compilation errors
    mock_checkpointer = None  # LangGraph allows None for no-op checkpointer
    graph = build_pipeline_b_graph(checkpointer=mock_checkpointer)
    assert graph is not None


# ===========================================================================
# PipelineBController tests
# ===========================================================================


@pytest.mark.asyncio
async def test_pipeline_b_start_scan_valid():
    """API: start_scan with valid city → returns status=started."""
    from src.api.routes.pipeline_b import PipelineBController
    from src.api.schemas import PipelineBScanRequestSchema

    controller = PipelineBController(owner=MagicMock())
    mock_session = AsyncMock()
    mock_request = MagicMock()
    mock_request.user = MagicMock()
    mock_request.user.id = uuid.uuid4()
    data = PipelineBScanRequestSchema(city="Berlin")

    with patch("src.api.routes.pipeline_b.asyncio.create_task") as mock_task:
        result = await PipelineBController.start_scan.fn(
            controller,
            data=data,
            db_session=mock_session,
            request=mock_request,
        )

    assert result["status"] == "started"
    assert result["city"] == "Berlin"
    assert "thread_id" in result
    mock_task.assert_called_once()


@pytest.mark.asyncio
async def test_pipeline_b_start_scan_empty_city():
    """API: start_scan with empty city → Pydantic ValidationError."""
    from pydantic import ValidationError

    from src.api.schemas import PipelineBScanRequestSchema

    with pytest.raises(ValidationError):
        PipelineBScanRequestSchema(city="")


@pytest.mark.asyncio
async def test_pipeline_b_list_leads():
    """API: list_leads returns lead list from DB."""
    from src.api.routes.pipeline_b import PipelineBController

    mock_session = AsyncMock()

    mock_lead = MagicMock(spec=Lead)
    mock_lead.id = uuid.uuid4()
    mock_lead.name = "Test Cafe"
    mock_lead.category = "cafe"
    mock_lead.city = "Berlin"
    mock_lead.address = "Test St"
    mock_lead.phone = "+49123"
    mock_lead.email = "test@cafe.com"
    mock_lead.status = "enriched"
    mock_lead.enrichment_source = "osint"
    mock_lead.discovered_at = datetime.now(UTC)

    # Mock execute results
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = [mock_lead]
    mock_exec_result_1 = MagicMock()
    mock_exec_result_1.scalars.return_value = mock_scalars

    mock_exec_result_2 = MagicMock()
    mock_exec_result_2.scalar.return_value = 1

    mock_session.execute = AsyncMock(side_effect=[mock_exec_result_1, mock_exec_result_2])

    result = await PipelineBController.list_leads.fn(
        self=None,
        db_session=mock_session,
        city=None,
        status=None,
        limit=50,
        offset=0,
    )

    assert result["total"] == 1
    assert len(result["leads"]) == 1
    assert result["leads"][0]["name"] == "Test Cafe"


@pytest.mark.asyncio
async def test_pipeline_b_list_leads_with_filters():
    """API: list_leads with city filter."""
    from src.api.routes.pipeline_b import PipelineBController

    mock_session = AsyncMock()

    mock_scalars = MagicMock()
    mock_scalars.all.return_value = []
    mock_exec_result_1 = MagicMock()
    mock_exec_result_1.scalars.return_value = mock_scalars

    mock_exec_result_2 = MagicMock()
    mock_exec_result_2.scalar.return_value = 0

    mock_session.execute = AsyncMock(side_effect=[mock_exec_result_1, mock_exec_result_2])

    result = await PipelineBController.list_leads.fn(
        self=None,
        db_session=mock_session,
        city="Berlin",
        status="enriched",
        limit=10,
        offset=0,
    )

    assert result["total"] == 0
    assert len(result["leads"]) == 0


@pytest.mark.asyncio
async def test_pipeline_b_scan_stats():
    """API: scan_stats returns status counts."""
    from src.api.routes.pipeline_b import PipelineBController

    mock_session = AsyncMock()

    # Mock status counts: [(status, count), ...]
    mock_session.execute = AsyncMock(side_effect=[
        [("new", 10), ("enriched", 5), ("no_contact", 2)],
        [("Berlin", 12), ("Munich", 5)],
    ])

    result = await PipelineBController.scan_stats.fn(
        self=None,
        db_session=mock_session,
    )

    assert result["total_leads"] == 17
    assert result["by_status"] == {"new": 10, "enriched": 5, "no_contact": 2}
    assert len(result["top_cities"]) == 2
    assert result["top_cities"][0]["city"] == "Berlin"
    assert result["top_cities"][0]["count"] == 12
