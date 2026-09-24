"""Integration tests for OutreachAgent.

Mocks enrichment waterfall, LLM client, and database to test:
- Enrichment flow and cost tracking
- LLM email generation with mock responses
- Fallback template when LLM fails
- CampaignLead creation + HITL queue entry
- campaign_id stored in artifacts (bug fix verification)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage

from src.core.exceptions import LLMException, MASException
from src.core.models import EmailCampaign, Lead
from src.core.state import AgentState, ProjectContext, create_initial_state
from src.enrichment.osint import EnrichmentResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_outreach_state(city: str = "Berlin") -> AgentState:
    """Build a state dict ready for Outreach agent (after GeoScout)."""
    project = ProjectContext(
        project_id=f"outreach_test_{uuid.uuid4().hex[:8]}",
        job_id="",
        platform="outreach",
        client={},
        requirements=city,
        budget=0.0,
        deadline=datetime.now(tz=UTC),
    )
    state = create_initial_state(
        project=project,
        first_agent="outreach",
        thread_id=f"thread-out-{uuid.uuid4().hex[:8]}",
    )
    state["artifacts"] = {
        "_scan_city": city,
        "_geo_scan_results": {
            "city": city,
            "hexagons_total": 10,
            "hexagons_scanned": 10,
            "leads_found": 5,
            "leads_stored": 5,
        },
    }
    state["current_agent"] = "outreach"
    return state


def _make_mock_leads(count: int, with_email: bool = False) -> list[MagicMock]:
    """Create mock Lead objects."""
    leads = []
    for i in range(count):
        lead = MagicMock(spec=Lead)
        lead.id = uuid.uuid4()
        lead.name = f"Business {i}"
        lead.category = "restaurant" if i % 2 == 0 else "cafe"
        lead.city = "Berlin"
        lead.address = f"Street {i}"
        lead.email = f"biz{i}@example.com" if with_email else None
        lead.phone = f"+4930{i:07d}" if i % 3 == 0 else None
        lead.osm_id = 100000 + i
        lead.status = "new"
        lead.telegram_username = None
        leads.append(lead)
    return leads


def _mock_db_session():
    """Create a mock async DB session context manager."""
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.execute = AsyncMock()

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, session


def _mock_campaign(campaign_id: uuid.UUID | None = None) -> MagicMock:
    """Create a mock EmailCampaign."""
    campaign = MagicMock(spec=EmailCampaign)
    campaign.id = campaign_id or uuid.uuid4()
    campaign.name = "Pipeline B -- Berlin"
    campaign.status = "draft"
    campaign.total_leads = 0
    return campaign


# ---------------------------------------------------------------------------
# Tests: Enrichment flow
# ---------------------------------------------------------------------------


async def test_outreach_enriches_leads():
    """Outreach: enrichment waterfall is called for each lead."""
    state = _make_outreach_state()
    leads = _make_mock_leads(3)
    db_ctx, db_session = _mock_db_session()
    campaign = _mock_campaign()

    waterfall = AsyncMock()
    waterfall.total_cost = Decimal("0.03")
    waterfall.enrich = AsyncMock(
        side_effect=[
            EnrichmentResult(email="a@test.com", source="osint", confidence=0.8),
            EnrichmentResult(email=None, source="none", confidence=0.0),
            EnrichmentResult(email="c@test.com", source="hunter", confidence=0.9, raw_data={"cost": 0.01}),
        ]
    )

    # Mock _load_leads to return our test leads
    # Mock _create_campaign to return our campaign
    # Mock _create_hitl_request
    with patch("src.agents.outreach.get_db_session", return_value=db_ctx):
        from src.agents.outreach import OutreachAgent

        agent = OutreachAgent(
            llm_client=AsyncMock(),
            heartbeat=AsyncMock(),
            loop_detector=MagicMock(check=MagicMock(return_value=False)),
            waterfall=waterfall,
        )
        agent._load_leads = AsyncMock(return_value=leads)
        agent._create_campaign = AsyncMock(return_value=campaign)
        agent._create_hitl_request = AsyncMock(return_value=uuid.uuid4())

        # Mock LLM for email generation
        mock_response = AIMessage(content='{"subject": "Website for Business", "body": "Hi there"}')
        mock_metrics = MagicMock()
        agent._call_llm = AsyncMock(return_value=(mock_response, mock_metrics))

        result = await agent._execute(state)

    # Enrichment was called for all 3 leads
    assert waterfall.enrich.await_count == 3
    # 2 enriched (a@test.com and c@test.com)
    assert result["artifacts"]["_outreach_results"]["enriched"] == 2


async def test_outreach_tracks_enrichment_cost():
    """Outreach: enrichment cost is stored in artifacts."""
    state = _make_outreach_state()
    leads = _make_mock_leads(2)
    db_ctx, db_session = _mock_db_session()
    campaign = _mock_campaign()

    waterfall = AsyncMock()
    waterfall.total_cost = Decimal("0.06")
    waterfall.enrich = AsyncMock(
        return_value=EnrichmentResult(
            email="lead@test.com",
            source="apollo",
            confidence=0.9,
            raw_data={"cost": 0.05},
        )
    )

    with patch("src.agents.outreach.get_db_session", return_value=db_ctx):
        from src.agents.outreach import OutreachAgent

        agent = OutreachAgent(
            llm_client=AsyncMock(),
            heartbeat=AsyncMock(),
            loop_detector=MagicMock(check=MagicMock(return_value=False)),
            waterfall=waterfall,
        )
        agent._load_leads = AsyncMock(return_value=leads)
        agent._create_campaign = AsyncMock(return_value=campaign)
        agent._create_hitl_request = AsyncMock(return_value=uuid.uuid4())

        mock_response = AIMessage(content='{"subject": "Hi", "body": "Hello"}')
        agent._call_llm = AsyncMock(return_value=(mock_response, MagicMock()))

        result = await agent._execute(state)

    assert result["artifacts"]["_outreach_results"]["enrichment_cost"] == 0.06


# ---------------------------------------------------------------------------
# Tests: LLM email generation
# ---------------------------------------------------------------------------


async def test_outreach_generates_email_via_llm():
    """Outreach: LLM generates personalized email subject and body."""
    lead = _make_mock_leads(1, with_email=True)[0]
    lead.email = "biz@test.com"

    from src.agents.outreach import OutreachAgent

    agent = OutreachAgent(
        llm_client=AsyncMock(),
        heartbeat=AsyncMock(),
        loop_detector=MagicMock(check=MagicMock(return_value=False)),
    )

    mock_response = AIMessage(content='{"subject": "Custom Subject", "body": "Custom Body"}')
    agent._call_llm = AsyncMock(return_value=(mock_response, MagicMock()))

    subject, body = await agent._draft_message_for_lead(lead, "email")

    assert subject == "Custom Subject"
    assert body == "Custom Body"


async def test_outreach_fallback_template_on_llm_failure():
    """Outreach: fallback template used when LLM fails."""
    lead = _make_mock_leads(1, with_email=True)[0]
    lead.name = "Test Cafe"
    lead.email = "cafe@test.com"

    from src.agents.outreach import OutreachAgent

    agent = OutreachAgent(
        llm_client=AsyncMock(),
        heartbeat=AsyncMock(),
        loop_detector=MagicMock(check=MagicMock(return_value=False)),
    )

    # LLM raises an error
    agent._call_llm = AsyncMock(side_effect=LLMException("LLM timeout"))

    subject, body = await agent._draft_message_for_lead(lead, "email")

    assert "Test Cafe" in subject
    assert "Test Cafe" in body
    assert "website" in body.lower()


async def test_outreach_fallback_template_on_empty_body():
    """Outreach: fallback template when LLM returns JSON with empty body."""
    lead = _make_mock_leads(1, with_email=True)[0]
    lead.name = "Empty Body Biz"

    from src.agents.outreach import OutreachAgent

    agent = OutreachAgent(
        llm_client=AsyncMock(),
        heartbeat=AsyncMock(),
        loop_detector=MagicMock(check=MagicMock(return_value=False)),
    )

    # LLM returns JSON with empty body
    mock_response = AIMessage(content='{"subject": "Hi", "body": ""}')
    agent._call_llm = AsyncMock(return_value=(mock_response, MagicMock()))

    subject, body = await agent._draft_message_for_lead(lead, "email")

    assert "Empty Body Biz" in subject
    assert len(body) > 0  # Fallback provides non-empty body


# ---------------------------------------------------------------------------
# Tests: Campaign and HITL
# ---------------------------------------------------------------------------


async def test_outreach_creates_campaign():
    """Outreach: creates EmailCampaign in the database."""
    db_ctx, db_session = _mock_db_session()
    db_session.refresh = AsyncMock()

    with patch("src.agents.outreach.get_db_session", return_value=db_ctx):
        from src.agents.outreach import OutreachAgent

        agent = OutreachAgent(
            llm_client=AsyncMock(),
            heartbeat=AsyncMock(),
            loop_detector=MagicMock(check=MagicMock(return_value=False)),
        )

        db_session.add = MagicMock()
        await agent._create_campaign("Berlin")

    # session.add was called (the campaign was added)
    db_session.add.assert_called_once()


async def test_outreach_campaign_id_in_artifacts():
    """Outreach: campaign_id is stored in artifacts (regression test for bug fix)."""
    state = _make_outreach_state()
    leads = _make_mock_leads(2, with_email=True)
    db_ctx, db_session = _mock_db_session()
    campaign_id = uuid.uuid4()
    campaign = _mock_campaign(campaign_id)

    waterfall = AsyncMock()
    waterfall.total_cost = Decimal("0.02")
    waterfall.enrich = AsyncMock(
        return_value=EnrichmentResult(
            email="lead@test.com",
            source="osint",
            confidence=0.8,
        )
    )

    with patch("src.agents.outreach.get_db_session", return_value=db_ctx):
        from src.agents.outreach import OutreachAgent

        agent = OutreachAgent(
            llm_client=AsyncMock(),
            heartbeat=AsyncMock(),
            loop_detector=MagicMock(check=MagicMock(return_value=False)),
            waterfall=waterfall,
        )
        agent._load_leads = AsyncMock(return_value=leads)
        agent._create_campaign = AsyncMock(return_value=campaign)
        agent._create_hitl_request = AsyncMock(return_value=uuid.uuid4())

        mock_response = AIMessage(content='{"subject": "Hi", "body": "Hello"}')
        agent._call_llm = AsyncMock(return_value=(mock_response, MagicMock()))

        result = await agent._execute(state)

    # This was the bug: campaign_id was not stored
    assert "campaign_id" in result["artifacts"]
    assert result["artifacts"]["campaign_id"] == str(campaign_id)


async def test_outreach_creates_hitl_request():
    """Outreach: creates HITL queue entry when emails are drafted."""
    state = _make_outreach_state()
    leads = _make_mock_leads(2, with_email=True)
    db_ctx, db_session = _mock_db_session()
    campaign = _mock_campaign()
    hitl_id = uuid.uuid4()

    waterfall = AsyncMock()
    waterfall.total_cost = Decimal("0.02")
    waterfall.enrich = AsyncMock(
        return_value=EnrichmentResult(
            email="lead@test.com",
            source="osint",
            confidence=0.8,
        )
    )

    with patch("src.agents.outreach.get_db_session", return_value=db_ctx):
        from src.agents.outreach import OutreachAgent

        agent = OutreachAgent(
            llm_client=AsyncMock(),
            heartbeat=AsyncMock(),
            loop_detector=MagicMock(check=MagicMock(return_value=False)),
            waterfall=waterfall,
        )
        agent._load_leads = AsyncMock(return_value=leads)
        agent._create_campaign = AsyncMock(return_value=campaign)
        agent._create_hitl_request = AsyncMock(return_value=hitl_id)

        mock_response = AIMessage(content='{"subject": "Hi", "body": "Hello"}')
        agent._call_llm = AsyncMock(return_value=(mock_response, MagicMock()))

        result = await agent._execute(state)

    agent._create_hitl_request.assert_awaited_once()
    assert result["requires_hitl"] is True
    assert result["status"] == "paused"
    assert "_outreach_hitl_id" in result["artifacts"]


# ---------------------------------------------------------------------------
# Tests: No leads / empty results
# ---------------------------------------------------------------------------


async def test_outreach_no_leads_completes():
    """Outreach: no leads in DB -> completes without HITL."""
    state = _make_outreach_state()
    db_ctx, db_session = _mock_db_session()

    with patch("src.agents.outreach.get_db_session", return_value=db_ctx):
        from src.agents.outreach import OutreachAgent

        agent = OutreachAgent(
            llm_client=AsyncMock(),
            heartbeat=AsyncMock(),
            loop_detector=MagicMock(check=MagicMock(return_value=False)),
        )
        agent._load_leads = AsyncMock(return_value=[])
        result = await agent._execute(state)

    assert result["status"] == "completed"
    assert result["requires_hitl"] is False
    assert result["artifacts"]["_outreach_results"]["enriched"] == 0
    assert result["artifacts"]["_outreach_results"]["emails_drafted"] == 0


async def test_outreach_no_city_fails():
    """Outreach: no city in state -> fails with error."""
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
        first_agent="outreach",
        thread_id="thread-no-city",
    )
    state["artifacts"] = {}

    from src.agents.outreach import OutreachAgent

    agent = OutreachAgent(
        llm_client=AsyncMock(),
        heartbeat=AsyncMock(),
        loop_detector=MagicMock(check=MagicMock(return_value=False)),
    )
    result = await agent._execute(state)

    assert result["status"] == "failed"
    assert any("No city" in e for e in result["errors"])


# ---------------------------------------------------------------------------
# Tests: Error handling
# ---------------------------------------------------------------------------


async def test_outreach_enrichment_error_handled():
    """Outreach: exception during enrichment -> status=failed, not raised."""
    state = _make_outreach_state()
    leads = _make_mock_leads(2)

    waterfall = AsyncMock()
    waterfall.total_cost = Decimal("0")
    waterfall.enrich = AsyncMock(side_effect=MASException("Hunter API down"))

    with patch("src.agents.outreach.get_db_session", return_value=_mock_db_session()[0]):
        from src.agents.outreach import OutreachAgent

        agent = OutreachAgent(
            llm_client=AsyncMock(),
            heartbeat=AsyncMock(),
            loop_detector=MagicMock(check=MagicMock(return_value=False)),
            waterfall=waterfall,
        )
        agent._load_leads = AsyncMock(return_value=leads)

        result = await agent._execute(state)

    assert result["status"] == "failed"
    assert any("Outreach error" in e for e in result["errors"])
