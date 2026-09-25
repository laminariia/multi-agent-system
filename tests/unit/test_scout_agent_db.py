"""Tests for ScoutAgent DB methods: _deduplicate, _store_jobs, _create_hitl_review, _log_decision_summary."""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.scout import ScoutAgent

pytestmark = pytest.mark.asyncio


def _agent(llm, hb, ld, adapters=None):
    return ScoutAgent(
        llm_client=llm,
        heartbeat=hb,
        loop_detector=ld,
        adapters=adapters or {},
    )


def _job(**kw: Any) -> dict[str, Any]:
    defaults = {
        "platform": "freelancer",
        "external_id": "ext-1",
        "title": "Build page",
        "description": "React page",
        "budget_min": 200,
        "budget_max": 500,
        "currency": "USD",
        "skills_required": ["react"],
        "match_score": 0.85,
        "recommendation": "bid",
        "reasoning": "Good match",
        "url": "https://x.com",
    }
    defaults.update(kw)
    return defaults


# ===== _deduplicate ===========================================================


@patch("src.agents.scout.get_db_session")
async def test_deduplicate_empty(mock_db, mock_llm_client, mock_heartbeat, mock_loop_detector):
    agent = _agent(mock_llm_client, mock_heartbeat, mock_loop_detector)
    assert await agent._deduplicate([]) == []


@patch("src.agents.scout.get_db_session")
async def test_deduplicate_all_new(mock_db, mock_llm_client, mock_heartbeat, mock_loop_detector):
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.__iter__ = MagicMock(return_value=iter([]))
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

    agent = _agent(mock_llm_client, mock_heartbeat, mock_loop_detector)
    jobs = [_job(external_id="e1"), _job(external_id="e2")]
    result = await agent._deduplicate(jobs)
    assert len(result) == 2


@patch("src.agents.scout.get_db_session")
async def test_deduplicate_filters_existing(mock_db, mock_llm_client, mock_heartbeat, mock_loop_detector):
    existing = MagicMock()
    existing.platform = "freelancer"
    existing.external_id = "e1"

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.__iter__ = MagicMock(return_value=iter([existing]))
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

    agent = _agent(mock_llm_client, mock_heartbeat, mock_loop_detector)
    jobs = [_job(external_id="e1"), _job(external_id="e2")]
    result = await agent._deduplicate(jobs)
    assert len(result) == 1
    assert result[0]["external_id"] == "e2"


# ===== _store_jobs ============================================================


@patch("src.agents.scout.get_db_session")
async def test_store_jobs_empty(mock_db, mock_llm_client, mock_heartbeat, mock_loop_detector):
    agent = _agent(mock_llm_client, mock_heartbeat, mock_loop_detector)
    result = await agent._store_jobs([], status="qualified")
    assert result == []


@patch("src.agents.scout.get_db_session")
async def test_store_jobs_qualified(mock_db, mock_llm_client, mock_heartbeat, mock_loop_detector):
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.rowcount = 1
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

    agent = _agent(mock_llm_client, mock_heartbeat, mock_loop_detector)
    jobs = [_job(), _job(external_id="e2")]
    result = await agent._store_jobs(jobs, status="qualified")
    assert len(result) == 2
    for r in result:
        uuid.UUID(r)
    assert mock_session.execute.call_count == 2


@patch("src.agents.scout.get_db_session")
async def test_store_jobs_disqualified(mock_db, mock_llm_client, mock_heartbeat, mock_loop_detector):
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.rowcount = 1
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

    agent = _agent(mock_llm_client, mock_heartbeat, mock_loop_detector)
    jobs = [_job(reasoning="Too expensive")]
    result = await agent._store_jobs(jobs, status="disqualified")
    assert len(result) == 1
    # Verify the INSERT statement was executed
    assert mock_session.execute.call_count == 1


@patch("src.agents.scout.get_db_session")
async def test_store_jobs_optional_fields(mock_db, mock_llm_client, mock_heartbeat, mock_loop_detector):
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.rowcount = 1
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

    agent = _agent(mock_llm_client, mock_heartbeat, mock_loop_detector)
    jobs = [_job(budget_min=None, budget_max=None)]
    result = await agent._store_jobs(jobs, status="qualified")
    assert len(result) == 1
    assert mock_session.execute.call_count == 1


# ===== _create_hitl_review ====================================================


@patch("src.agents.scout.get_db_session")
async def test_create_hitl_review(mock_db, mock_llm_client, mock_heartbeat, mock_loop_detector):
    mock_session = AsyncMock()
    mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

    agent = _agent(mock_llm_client, mock_heartbeat, mock_loop_detector)
    await agent._create_hitl_review(_job(match_score=0.6, title="Borderline"))
    mock_session.add.assert_called_once()
    added = mock_session.add.call_args[0][0]
    assert added.type == "job_review"
    assert added.priority == "low"


# ===== _log_decision_summary ==================================================


@patch("src.agents.scout.get_db_session")
async def test_log_decision_summary(mock_db, mock_llm_client, mock_heartbeat, mock_loop_detector):
    mock_session = AsyncMock()
    mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

    agent = _agent(mock_llm_client, mock_heartbeat, mock_loop_detector)
    await agent._log_decision_summary(qualified=5, review=2, rejected=3, thread_id="t1")
    mock_session.add.assert_called_once()
    added = mock_session.add.call_args[0][0]
    assert added.event_type == "scan_complete"
    assert "5 qualified" in added.message
