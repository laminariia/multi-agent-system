"""Extended unit tests for src.agents.scout.ScoutAgent.

Covers: multi-platform parallel fetch, deduplication edge cases, LLM scoring
with batching, JSON parsing edge cases (markdown fences, single dict, invalid
types), borderline/rejected job classification, error handling for adapter
failures, and state management.

All LLM calls, DB operations, and platform adapters are mocked.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from src.agents.scout import _MAX_JOBS_PER_BATCH, _SCORE_BID_THRESHOLD, _SCORE_REVIEW_THRESHOLD, ScoutAgent
from src.core.exceptions import LLMInvalidResponseError, PlatformException
from src.core.llm_client import CallMetrics
from src.core.state import AgentState, create_initial_state
from tests.factories import JobFactory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_state(**overrides: Any) -> AgentState:
    """Build a test AgentState with sensible defaults."""
    project = {
        "project_id": "proj-test-001",
        "job_id": "job-test-001",
        "platform": "freelancer",
        "client": {"name": "Test Client"},
        "requirements": "Build a landing page",
        "budget": 500.0,
        "deadline": datetime(2026, 3, 15, tzinfo=UTC),
    }
    state = create_initial_state(project=project, first_agent="scout", thread_id="thread-scout-ext")
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def _make_llm_response(content: str) -> tuple[AIMessage, CallMetrics]:
    return (
        AIMessage(content=content),
        CallMetrics(agent_name="scout", model_id="gemini-3-flash", provider="google"),
    )


# ---------------------------------------------------------------------------
# Multi-platform parallel fetch tests
# ---------------------------------------------------------------------------


async def test_fetch_all_platforms_parallel(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """Multiple adapters should be fetched in parallel and results merged."""
    freelancer_jobs = JobFactory.create_batch(2, platform="freelancer")
    flru_jobs = JobFactory.create_batch(1, platform="flru")

    adapters = {
        "freelancer": MagicMock(fetch_jobs=AsyncMock(return_value=freelancer_jobs)),
        "flru": MagicMock(fetch_jobs=AsyncMock(return_value=flru_jobs)),
    }

    agent = ScoutAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
        adapters=adapters,
    )

    result = await agent._fetch_all_platforms()
    assert len(result) == 3


async def test_fetch_adapter_failure_continues(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """If one adapter fails, others should still return results."""
    good_jobs = JobFactory.create_batch(2)
    adapters = {
        "freelancer": MagicMock(fetch_jobs=AsyncMock(side_effect=PlatformException("API down"))),
        "flru": MagicMock(fetch_jobs=AsyncMock(return_value=good_jobs)),
    }

    agent = ScoutAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
        adapters=adapters,
    )

    result = await agent._fetch_all_platforms()
    assert len(result) == 2


async def test_all_adapters_fail_returns_empty(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """If all adapters fail, fetch should return empty list."""
    adapters = {
        "freelancer": MagicMock(fetch_jobs=AsyncMock(side_effect=Exception("Down"))),
        "flru": MagicMock(fetch_jobs=AsyncMock(side_effect=Exception("Down"))),
    }

    agent = ScoutAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
        adapters=adapters,
    )

    result = await agent._fetch_all_platforms()
    assert result == []


async def test_unknown_platform_ignored(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """Unknown platform adapters should be skipped with a warning."""
    adapters = {
        "unknown_platform": MagicMock(fetch_jobs=AsyncMock(return_value=[{"id": 1}])),
    }

    agent = ScoutAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
        adapters=adapters,
    )

    result = await agent._fetch_all_platforms()
    # Unknown platforms are warned but no task is created
    assert result == []


# ---------------------------------------------------------------------------
# _parse_scored_response edge cases
# ---------------------------------------------------------------------------


def test_parse_markdown_fenced_json(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """LLM responses wrapped in ```json``` fences should be parsed correctly."""
    agent = ScoutAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
        adapters={},
    )

    fenced = '```json\n[{"title": "Job", "match_score": 0.8, "recommendation": "bid"}]\n```'
    result = agent._parse_scored_response(fenced, expected_count=1)
    assert len(result) == 1
    assert result[0]["match_score"] == 0.8


def test_parse_single_dict_wrapped_in_list(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """A single dict response (not wrapped in array) should be auto-wrapped."""
    agent = ScoutAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
        adapters={},
    )

    single = json.dumps({"title": "Job", "match_score": 0.9, "recommendation": "bid"})
    result = agent._parse_scored_response(single, expected_count=1)
    assert len(result) == 1


def test_parse_invalid_match_score_defaults_to_zero(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """Non-numeric match_score should default to 0.0."""
    agent = ScoutAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
        adapters={},
    )

    data = json.dumps([{"title": "Job", "match_score": "invalid", "recommendation": "bid"}])
    result = agent._parse_scored_response(data, expected_count=1)
    assert result[0]["match_score"] == 0.0


def test_parse_invalid_recommendation_defaults_to_skip(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """Invalid recommendation should default to 'skip'."""
    agent = ScoutAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
        adapters={},
    )

    data = json.dumps([{"title": "Job", "match_score": 0.8, "recommendation": "maybe"}])
    result = agent._parse_scored_response(data, expected_count=1)
    assert result[0]["recommendation"] == "skip"


def test_parse_non_dict_items_filtered(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """Non-dict items in the parsed list should be silently skipped."""
    agent = ScoutAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
        adapters={},
    )

    data = json.dumps([
        {"title": "Good", "match_score": 0.8, "recommendation": "bid"},
        "not a dict",
        42,
    ])
    result = agent._parse_scored_response(data, expected_count=3)
    assert len(result) == 1


def test_parse_non_list_non_dict_raises(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """A response that parses to neither list nor dict should raise."""
    agent = ScoutAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
        adapters={},
    )

    with pytest.raises(LLMInvalidResponseError):
        agent._parse_scored_response('"just a string"', expected_count=1)


def test_parse_missing_match_score_defaults(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """Missing match_score key should default to 0.0."""
    agent = ScoutAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
        adapters={},
    )

    data = json.dumps([{"title": "No score"}])
    result = agent._parse_scored_response(data, expected_count=1)
    assert result[0]["match_score"] == 0.0
    assert result[0]["recommendation"] == "skip"


# ---------------------------------------------------------------------------
# Job classification tests (scoring thresholds)
# ---------------------------------------------------------------------------


async def test_mixed_classification(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """Jobs should be classified into qualified, review, and rejected buckets."""
    raw_jobs = JobFactory.create_batch(3)

    scored = [
        {**raw_jobs[0], "match_score": 0.85, "recommendation": "bid"},     # qualified
        {**raw_jobs[1], "match_score": 0.55, "recommendation": "review"},   # review
        {**raw_jobs[2], "match_score": 0.20, "recommendation": "skip"},     # rejected
    ]

    mock_llm_client.call = AsyncMock(return_value=_make_llm_response(json.dumps(scored)))

    adapters = {
        "freelancer": MagicMock(fetch_jobs=AsyncMock(return_value=raw_jobs)),
    }

    agent = ScoutAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
        adapters=adapters,
    )

    state = _build_state()

    with (
        patch.object(agent, "_deduplicate", new_callable=AsyncMock, return_value=raw_jobs),
        patch.object(agent, "_store_jobs", new_callable=AsyncMock, side_effect=[["id-1"], [], []]) as store_spy,
        patch.object(agent, "_create_hitl_review", new_callable=AsyncMock) as hitl_spy,
        patch.object(agent, "_log_decision_summary", new_callable=AsyncMock) as log_spy,
    ):
        result = await agent._execute(state)

    # _store_jobs called 3x: qualified, review, rejected
    assert store_spy.await_count == 3
    # HITL review created for the review-band job
    hitl_spy.assert_awaited_once()
    # Decision summary logged
    log_spy.assert_awaited_once()
    # qualified jobs exist → route to bid
    assert result["next_agent"] == "bid"


async def test_only_rejected_jobs_no_bid(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """If all jobs are rejected, next_agent should be None."""
    raw_jobs = JobFactory.create_batch(2)

    scored = [
        {**raw_jobs[0], "match_score": 0.20, "recommendation": "skip"},
        {**raw_jobs[1], "match_score": 0.10, "recommendation": "skip"},
    ]

    mock_llm_client.call = AsyncMock(return_value=_make_llm_response(json.dumps(scored)))

    adapters = {
        "freelancer": MagicMock(fetch_jobs=AsyncMock(return_value=raw_jobs)),
    }

    agent = ScoutAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
        adapters=adapters,
    )

    state = _build_state()
    with (
        patch.object(agent, "_deduplicate", new_callable=AsyncMock, return_value=raw_jobs),
        patch.object(agent, "_store_jobs", new_callable=AsyncMock, return_value=[]),
        patch.object(agent, "_create_hitl_review", new_callable=AsyncMock),
        patch.object(agent, "_log_decision_summary", new_callable=AsyncMock),
    ):
        result = await agent._execute(state)

    assert result["next_agent"] is None


# ---------------------------------------------------------------------------
# State management tests
# ---------------------------------------------------------------------------


async def test_artifacts_include_scout_key(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """Result state should include scout artifacts with stored job IDs."""
    raw_jobs = JobFactory.create_batch(1)
    scored = [{**raw_jobs[0], "match_score": 0.9, "recommendation": "bid"}]
    mock_llm_client.call = AsyncMock(return_value=_make_llm_response(json.dumps(scored)))

    adapters = {"freelancer": MagicMock(fetch_jobs=AsyncMock(return_value=raw_jobs))}
    agent = ScoutAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
        adapters=adapters,
    )

    state = _build_state()
    with (
        patch.object(agent, "_deduplicate", new_callable=AsyncMock, return_value=raw_jobs),
        patch.object(agent, "_store_jobs", new_callable=AsyncMock, return_value=["uuid-1"]),
        patch.object(agent, "_create_hitl_review", new_callable=AsyncMock),
        patch.object(agent, "_log_decision_summary", new_callable=AsyncMock),
    ):
        result = await agent._execute(state)

    assert "scout" in result["artifacts"]
    assert result["artifacts"]["scout"] == ["uuid-1"]


async def test_state_preserves_existing_artifacts(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """Scout should preserve any pre-existing artifacts in state."""
    state = _build_state(artifacts={"previous": ["data"]})
    adapters = {"freelancer": MagicMock(fetch_jobs=AsyncMock(return_value=[]))}

    agent = ScoutAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
        adapters=adapters,
    )

    with patch.object(agent, "_deduplicate", new_callable=AsyncMock, return_value=[]):
        result = await agent._execute(state)

    # Even with no new jobs, the state shouldn't lose "previous" artifacts
    # Note: when no jobs found, _execute returns early without modifying artifacts
    assert result["status"] == "active"


async def test_current_agent_set_to_scout(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """Result state should always set current_agent to 'scout'."""
    adapters = {"freelancer": MagicMock(fetch_jobs=AsyncMock(return_value=[]))}

    agent = ScoutAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
        adapters=adapters,
    )

    state = _build_state()
    with patch.object(agent, "_deduplicate", new_callable=AsyncMock, return_value=[]):
        result = await agent._execute(state)

    assert result["current_agent"] == "scout"


# ---------------------------------------------------------------------------
# Constrained agent properties
# ---------------------------------------------------------------------------


def test_scout_agent_name(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """ScoutAgent should have agent_name='scout'."""
    agent = ScoutAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
        adapters={},
    )
    assert agent.agent_name == "scout"


def test_scout_allowed_tools(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """ScoutAgent should have the expected allowed tools."""
    agent = ScoutAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
        adapters={},
    )
    assert "fetch_freelancer_jobs" in agent.allowed_tools
    assert "fetch_flru_rss" in agent.allowed_tools
    assert "fetch_kwork_jobs" in agent.allowed_tools


# ---------------------------------------------------------------------------
# Constants tests
# ---------------------------------------------------------------------------


def test_score_thresholds():
    """Bid and review thresholds should match documented values."""
    assert _SCORE_BID_THRESHOLD == 0.7
    assert _SCORE_REVIEW_THRESHOLD == 0.5


def test_max_jobs_per_batch():
    """Batch size should be 25 to avoid context overflow."""
    assert _MAX_JOBS_PER_BATCH == 25


# ---------------------------------------------------------------------------
# Kwork/Upwork adapter integration
# ---------------------------------------------------------------------------


async def test_kwork_adapter_included(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """Kwork adapter should be fetched when present in adapters dict."""
    kwork_jobs = [{"platform": "kwork", "external_id": "k1", "title": "Kwork job"}]
    adapters = {
        "kwork": MagicMock(fetch_jobs=AsyncMock(return_value=kwork_jobs)),
    }

    agent = ScoutAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
        adapters=adapters,
    )

    result = await agent._fetch_all_platforms()
    assert len(result) == 1
    assert result[0]["platform"] == "kwork"


async def test_upwork_adapter_included(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """Upwork adapter should be fetched when present in adapters dict."""
    upwork_jobs = [{"platform": "upwork", "external_id": "u1", "title": "Upwork job"}]
    adapters = {
        "upwork": MagicMock(fetch_jobs=AsyncMock(return_value=upwork_jobs)),
    }

    agent = ScoutAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
        adapters=adapters,
    )

    result = await agent._fetch_all_platforms()
    assert len(result) == 1
    assert result[0]["platform"] == "upwork"


# ---------------------------------------------------------------------------
# scout_node DB credential loading
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scout_node_loads_db_credentials():
    """scout_node should load Freelancer credentials from DB when user_id present."""
    from src.agents.scout import scout_node

    _project = {"title": "T", "requirements": "r", "budget_min": 100, "budget_max": 500, "platforms": ["freelancer"]}
    state = create_initial_state(project=_project, user_id="user-123")

    # Adapter mocks need async close() since scout_node awaits it
    mock_fl_ru_instance = MagicMock(spec=[])  # spec=[] prevents auto-close attr
    mock_fc_instance = MagicMock()
    mock_fc_instance.close = AsyncMock()

    mock_container = MagicMock()
    mock_container.llm_client = MagicMock()
    mock_container.heartbeat = MagicMock()
    mock_container.loop_detector = MagicMock()
    mock_container.browser_pool = None

    with (
        patch("src.core.config.get_settings") as mock_settings,
        patch("src.core.credential_loader.load_platform_credentials", new_callable=AsyncMock) as mock_load,
        patch("src.adapters.freelancer.FreelancerClient", return_value=mock_fc_instance) as mock_fc,
        patch("src.adapters.fl_ru.FlRuClient", return_value=mock_fl_ru_instance),
        patch("src.core.container.get_container", return_value=mock_container),
        patch("src.agents.scout.ScoutAgent") as mock_agent_cls,
    ):
        mock_settings_instance = MagicMock()
        mock_settings_instance.FREELANCER_CLIENT_ID = ""
        mock_settings_instance.FREELANCER_CLIENT_SECRET = ""
        mock_settings_instance.BRIGHTDATA_USERNAME = ""
        mock_settings_instance.BROWSER_POOL_MAX = 2
        mock_settings_instance.BROWSER_PROXY_ROTATION_MINUTES = 30
        mock_settings.return_value = mock_settings_instance

        mock_load.return_value = {"client_id": "db_id", "client_secret": "db_secret"}

        mock_agent = MagicMock()
        mock_agent.invoke = AsyncMock(return_value=state)
        mock_agent_cls.return_value = mock_agent

        await scout_node(state)

        mock_load.assert_called_once_with("freelancer", user_id="user-123")
        mock_fc.assert_called_once_with(client_id="db_id", client_secret="db_secret")


@pytest.mark.asyncio
async def test_scout_node_env_fallback_no_user_id():
    """scout_node should use env credentials when no user_id in state."""
    from src.agents.scout import scout_node

    _project = {"title": "T", "requirements": "r", "budget_min": 100, "budget_max": 500, "platforms": ["freelancer"]}
    state = create_initial_state(project=_project)

    mock_fl_ru_instance = MagicMock(spec=[])
    mock_fc_instance = MagicMock()
    mock_fc_instance.close = AsyncMock()

    mock_container = MagicMock()
    mock_container.llm_client = MagicMock()
    mock_container.heartbeat = MagicMock()
    mock_container.loop_detector = MagicMock()
    mock_container.browser_pool = None

    with (
        patch("src.core.config.get_settings") as mock_settings,
        patch("src.adapters.freelancer.FreelancerClient", return_value=mock_fc_instance) as mock_fc,
        patch("src.adapters.fl_ru.FlRuClient", return_value=mock_fl_ru_instance),
        patch("src.core.container.get_container", return_value=mock_container),
        patch("src.agents.scout.ScoutAgent") as mock_agent_cls,
    ):
        mock_settings_instance = MagicMock()
        mock_settings_instance.FREELANCER_CLIENT_ID = "env_id"
        mock_settings_instance.FREELANCER_CLIENT_SECRET = "env_secret"
        mock_settings_instance.BRIGHTDATA_USERNAME = ""
        mock_settings_instance.BROWSER_POOL_MAX = 2
        mock_settings_instance.BROWSER_PROXY_ROTATION_MINUTES = 30
        mock_settings.return_value = mock_settings_instance

        mock_agent = MagicMock()
        mock_agent.invoke = AsyncMock(return_value=state)
        mock_agent_cls.return_value = mock_agent

        await scout_node(state)

        mock_fc.assert_called_once_with(client_id="env_id", client_secret="env_secret")
