"""Extended unit tests for src.agents.bid — BidAgent DB/LLM methods.

Covers: _load_jobs, _fetch_similar_bids, _generate_proposal, _store_bid,
_create_hitl_entry, _log_bid_generated, _infer_category, _execute full flow.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from src.agents.bid import BidAgent
from src.core.llm_client import CallMetrics
from src.core.state import AgentState, create_initial_state

pytestmark = pytest.mark.asyncio


def _state(**overrides: Any) -> AgentState:
    project = {
        "project_id": "p1", "job_id": "j1", "platform": "freelancer",
        "client": {"name": "T"}, "requirements": "page", "budget": 500.0,
        "deadline": datetime(2026, 3, 15, tzinfo=UTC),
    }
    s = create_initial_state(project=project, first_agent="bid", thread_id="t-bid-ext")
    s.update(overrides)  # type: ignore[typeddict-item]
    return s


def _agent(llm, hb, ld):
    return BidAgent(llm_client=llm, heartbeat=hb, loop_detector=ld)


def _job(**kw: Any) -> dict[str, Any]:
    defaults = {
        "id": str(uuid.uuid4()), "platform": "freelancer", "external_id": "ext-1",
        "title": "Build landing page", "description": "React page",
        "budget_min": 200.0, "budget_max": 500.0, "currency": "USD",
        "skills_required": ["react"], "client_info": {}, "url": "https://x.com/1",
        "score": 0.85,
    }
    defaults.update(kw)
    return defaults


def _proposal(**kw: Any) -> dict[str, Any]:
    defaults = {
        "proposal_text": "I can build this.", "bid_amount": 350.0,
        "delivery_days": 7, "confidence_score": 0.8, "milestones": [],
        "requires_hitl": True,
    }
    defaults.update(kw)
    return defaults


def _llm_response(content: str):
    return (AIMessage(content=content),
            CallMetrics(agent_name="bid", model_id="gemini-3-flash", provider="google"))


# ===== _infer_category =======================================================

def test_infer_category_web():
    assert BidAgent._infer_category({"skills_required": ["react"], "title": ""}) == "web_development"

def test_infer_category_wordpress():
    assert BidAgent._infer_category({"skills_required": ["wordpress"], "title": ""}) == "wordpress"

def test_infer_category_design():
    assert BidAgent._infer_category({"skills_required": ["figma"], "title": ""}) == "design"

def test_infer_category_copywriting():
    assert BidAgent._infer_category({"skills_required": ["copywriting"], "title": ""}) == "copywriting"

def test_infer_category_landing():
    assert BidAgent._infer_category({"skills_required": [], "title": "landing page creation"}) == "landing_pages"

def test_infer_category_default():
    assert BidAgent._infer_category({"skills_required": [], "title": "misc task"}) == "web_development"


# ===== _load_jobs =============================================================

@patch("src.agents.bid.get_db_session")
async def test_load_jobs_valid(mock_db, mock_llm_client, mock_heartbeat, mock_loop_detector):
    jid = uuid.uuid4()
    job_mock = MagicMock()
    job_mock.id = jid
    job_mock.platform = "freelancer"
    job_mock.external_id = "ext-1"
    job_mock.title = "Test"
    job_mock.description = "Desc"
    job_mock.budget_min = 200
    job_mock.budget_max = 500
    job_mock.currency = "USD"
    job_mock.skills_required = ["react"]
    job_mock.client_info = {}
    job_mock.url = "https://x.com"
    job_mock.score = 0.9

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = job_mock
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

    agent = _agent(mock_llm_client, mock_heartbeat, mock_loop_detector)
    result = await agent._load_jobs([str(jid)])
    assert len(result) == 1
    assert result[0]["id"] == str(jid)


@patch("src.agents.bid.get_db_session")
async def test_load_jobs_invalid_uuid(mock_db, mock_llm_client, mock_heartbeat, mock_loop_detector):
    mock_session = AsyncMock()
    mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

    agent = _agent(mock_llm_client, mock_heartbeat, mock_loop_detector)
    result = await agent._load_jobs(["not-a-uuid"])
    assert result == []


@patch("src.agents.bid.get_db_session")
async def test_load_jobs_not_found(mock_db, mock_llm_client, mock_heartbeat, mock_loop_detector):
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

    agent = _agent(mock_llm_client, mock_heartbeat, mock_loop_detector)
    result = await agent._load_jobs([str(uuid.uuid4())])
    assert result == []


# ===== _fetch_similar_bids ====================================================

@patch("src.agents.bid.get_db_session")
async def test_fetch_similar_bids_with_retriever(mock_db, mock_llm_client, mock_heartbeat, mock_loop_detector):
    mock_retriever = AsyncMock()
    r = MagicMock()
    r.title = "Past bid"
    r.content = "Some content"
    r.success_rate = 0.9
    mock_retriever.search = AsyncMock(return_value=[r])

    BidAgent.configure_retriever(mock_retriever)
    try:
        agent = _agent(mock_llm_client, mock_heartbeat, mock_loop_detector)
        result = await agent._fetch_similar_bids(_job())
        assert len(result) == 1
        assert result[0]["title"] == "Past bid"
    finally:
        BidAgent._retriever = None


@patch("src.agents.bid.get_db_session")
async def test_fetch_similar_bids_retriever_fails(mock_db, mock_llm_client, mock_heartbeat, mock_loop_detector):
    mock_retriever = AsyncMock()
    mock_retriever.search = AsyncMock(side_effect=Exception("vector fail"))

    BidAgent.configure_retriever(mock_retriever)
    try:
        kb_mock = MagicMock()
        kb_mock.title = "Fallback"
        kb_mock.content = "fb content"
        kb_mock.success_rate = 0.7

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [kb_mock]
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

        agent = _agent(mock_llm_client, mock_heartbeat, mock_loop_detector)
        result = await agent._fetch_similar_bids(_job())
        assert len(result) == 1
        assert result[0]["title"] == "Fallback"
    finally:
        BidAgent._retriever = None


@patch("src.agents.bid.get_db_session")
async def test_fetch_similar_bids_sql_fallback(mock_db, mock_llm_client, mock_heartbeat, mock_loop_detector):
    kb_mock = MagicMock()
    kb_mock.title = "SQL bid"
    kb_mock.content = "sql content"
    kb_mock.success_rate = None

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [kb_mock]
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

    agent = _agent(mock_llm_client, mock_heartbeat, mock_loop_detector)
    result = await agent._fetch_similar_bids(_job())
    assert len(result) == 1
    assert result[0]["success_rate"] is None


# ===== _generate_proposal =====================================================

@patch("src.agents.bid.get_db_session")
async def test_generate_proposal_success(mock_db, mock_llm_client, mock_heartbeat, mock_loop_detector):
    proposal_json = json.dumps(_proposal())
    mock_llm_client.call = AsyncMock(return_value=_llm_response(proposal_json))

    agent = _agent(mock_llm_client, mock_heartbeat, mock_loop_detector)
    result = await agent._generate_proposal(_job(), [])
    assert result is not None
    assert result["proposal_text"] == "I can build this."
    assert result["requires_hitl"] is True


@patch("src.agents.bid.get_db_session")
async def test_generate_proposal_garbage(mock_db, mock_llm_client, mock_heartbeat, mock_loop_detector):
    mock_llm_client.call = AsyncMock(return_value=_llm_response("not json at all"))

    agent = _agent(mock_llm_client, mock_heartbeat, mock_loop_detector)
    result = await agent._generate_proposal(_job(), [])
    assert result is None


# ===== _store_bid =============================================================

@patch("src.agents.bid.get_db_session")
async def test_store_bid(mock_db, mock_llm_client, mock_heartbeat, mock_loop_detector):
    mock_session = AsyncMock()
    mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

    agent = _agent(mock_llm_client, mock_heartbeat, mock_loop_detector)
    bid_id = await agent._store_bid(_job(), _proposal())
    assert bid_id is not None
    uuid.UUID(bid_id)  # validates it's a UUID string
    mock_session.add.assert_called_once()


# ===== _create_hitl_entry =====================================================

@patch("src.agents.bid.get_db_session")
async def test_create_hitl_entry(mock_db, mock_llm_client, mock_heartbeat, mock_loop_detector):
    mock_session = AsyncMock()
    mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

    agent = _agent(mock_llm_client, mock_heartbeat, mock_loop_detector)
    hitl_id = await agent._create_hitl_entry(_job(), _proposal(), str(uuid.uuid4()))
    assert hitl_id is not None
    uuid.UUID(hitl_id)
    mock_session.add.assert_called_once()


# ===== _log_bid_generated =====================================================

@patch("src.agents.bid.get_db_session")
async def test_log_bid_generated(mock_db, mock_llm_client, mock_heartbeat, mock_loop_detector):
    mock_session = AsyncMock()
    mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

    agent = _agent(mock_llm_client, mock_heartbeat, mock_loop_detector)
    await agent._log_bid_generated(_job(), str(uuid.uuid4()), _proposal())
    mock_session.add.assert_called_once()


# ===== _execute full flow =====================================================

@patch("src.agents.bid.get_db_session")
async def test_execute_no_scout_artifacts(mock_db, mock_llm_client, mock_heartbeat, mock_loop_detector):
    agent = _agent(mock_llm_client, mock_heartbeat, mock_loop_detector)
    s = _state(artifacts={})
    result = await agent._execute(s)
    assert result["next_agent"] is None
    assert result["status"] == "active"


@patch("src.agents.bid.get_db_session")
async def test_execute_no_jobs_in_db(mock_db, mock_llm_client, mock_heartbeat, mock_loop_detector):
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

    agent = _agent(mock_llm_client, mock_heartbeat, mock_loop_detector)
    s = _state(artifacts={"scout": [str(uuid.uuid4())]})
    result = await agent._execute(s)
    assert result["next_agent"] is None


@patch("src.agents.bid.get_db_session")
async def test_execute_full_success(mock_db, mock_llm_client, mock_heartbeat, mock_loop_detector):
    jid = uuid.uuid4()
    job_mock = MagicMock()
    job_mock.id = jid
    job_mock.platform = "freelancer"
    job_mock.external_id = "ext-1"
    job_mock.title = "Test Job"
    job_mock.description = "Build page"
    job_mock.budget_min = 200
    job_mock.budget_max = 500
    job_mock.currency = "USD"
    job_mock.skills_required = ["react"]
    job_mock.client_info = {}
    job_mock.url = "https://x.com"
    job_mock.score = 0.9

    call_count = 0

    mock_session = AsyncMock()

    def execute_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        result = MagicMock()
        if call_count == 1:
            result.scalar_one_or_none.return_value = job_mock
        else:
            result.scalars.return_value.all.return_value = []
            result.scalar_one_or_none.return_value = None
        return result

    mock_session.execute = AsyncMock(side_effect=execute_side_effect)
    mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

    proposal_json = json.dumps(_proposal())
    mock_llm_client.call = AsyncMock(return_value=_llm_response(proposal_json))

    agent = _agent(mock_llm_client, mock_heartbeat, mock_loop_detector)
    s = _state(artifacts={"scout": [str(jid)]})
    result = await agent._execute(s)

    assert result["status"] == "paused"
    assert result["requires_hitl"] is True
    assert "bid" in result["artifacts"]


# ===== configure_retriever ====================================================

def test_configure_retriever():
    r = MagicMock()
    BidAgent.configure_retriever(r)
    assert BidAgent._retriever is r
    BidAgent._retriever = None
