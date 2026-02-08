"""Unit tests for src.core.llm_client (LLMClient, CostTracker, AGENT_MODEL_REGISTRY).

All LLM calls are mocked -- no real API requests are made.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from src.core.exceptions import LLMException, LLMRateLimitError
from src.core.llm_client import (
    AGENT_MODEL_REGISTRY,
    MODELS,
    CallMetrics,
    CostTracker,
    LLMClient,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ai_response(content: str = "ok", tokens_in: int = 50, tokens_out: int = 30) -> MagicMock:
    """Return a MagicMock that looks like a LangChain AIMessage with usage metadata."""
    msg = MagicMock(spec=AIMessage)
    msg.content = content
    usage = MagicMock()
    usage.input_tokens = tokens_in
    usage.output_tokens = tokens_out
    msg.usage_metadata = usage
    return msg


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_call_uses_primary_model_for_scout():
    """LLMClient.call('scout', ...) should use claude-haiku-4-5 as primary."""
    client = LLMClient(max_retries=1, base_backoff_seconds=0.0)
    mock_response = _make_ai_response("scout result", 80, 40)

    with patch.object(client, "_get_or_create_model") as mock_model_fn:
        mock_chat = AsyncMock()
        mock_chat.ainvoke = AsyncMock(return_value=mock_response)
        mock_model_fn.return_value = mock_chat

        response, metrics = await client.call("scout", [HumanMessage(content="find jobs")])

    assert metrics.model_id == "anthropic/claude-haiku-4.5"
    assert metrics.provider == "anthropic"
    assert metrics.was_fallback is False
    assert response.content == "scout result"


async def test_call_falls_back_on_rate_limit():
    """If the primary model raises a rate-limit error, the fallback model should be tried."""
    client = LLMClient(max_retries=1, base_backoff_seconds=0.0)
    mock_fallback_response = _make_ai_response("fallback result", 60, 20)

    with patch.object(client, "_invoke_with_retries") as mock_invoke:
        mock_invoke.side_effect = [
            LLMRateLimitError(
                "Rate limit", model="anthropic/claude-haiku-4.5",
                provider="anthropic", agent_name="scout",
            ),
            (mock_fallback_response, CallMetrics(
                agent_name="scout",
                model_id="openai/gpt-4o-mini",
                provider="openai",
                was_fallback=True,
                attempt=1,
            )),
        ]

        response, metrics = await client.call("scout", [HumanMessage(content="find jobs")])

    assert metrics.model_id == "openai/gpt-4o-mini"
    assert metrics.provider == "openai"
    assert metrics.was_fallback is True


async def test_call_all_providers_exhausted_raises():
    """When all models in the chain fail, LLMException should be raised."""
    client = LLMClient(max_retries=1, base_backoff_seconds=0.0)

    with patch.object(client, "_invoke_with_retries") as mock_invoke:
        mock_invoke.side_effect = LLMRateLimitError(
            "Rate limit", model="test", provider="test", agent_name="scout"
        )

        with pytest.raises(LLMException, match="All LLM providers exhausted"):
            await client.call("scout", [HumanMessage(content="find jobs")])


async def test_cost_tracker_records_metrics():
    """CostTracker should accumulate records and compute totals correctly."""
    tracker = CostTracker()

    m1 = CallMetrics(agent_name="scout", model_id="gemini-3-flash", provider="google", cost_usd=0.001)
    m2 = CallMetrics(agent_name="bid", model_id="gemini-3-flash", provider="google", cost_usd=0.002)
    m3 = CallMetrics(agent_name="scout", model_id="claude-haiku-4-5", provider="anthropic", cost_usd=0.005)

    tracker.record(m1)
    tracker.record(m2)
    tracker.record(m3)

    assert len(tracker.records) == 3
    assert abs(tracker.total_cost_usd - 0.008) < 1e-9

    by_agent = tracker.by_agent()
    assert abs(by_agent["scout"] - 0.006) < 1e-9
    assert abs(by_agent["bid"] - 0.002) < 1e-9

    by_model = tracker.by_model()
    assert abs(by_model["gemini-3-flash"] - 0.003) < 1e-9
    assert abs(by_model["claude-haiku-4-5"] - 0.005) < 1e-9


def test_agent_model_registry_completeness():
    """Every agent in AGENT_MODEL_REGISTRY should reference models that exist in MODELS."""
    expected_agents = {
        "scout", "bid", "planner", "dev", "content",
        "design", "critic", "packager", "geoscout", "outreach",
    }

    assert set(AGENT_MODEL_REGISTRY.keys()) == expected_agents, (
        f"Registry agents mismatch. Missing: {expected_agents - set(AGENT_MODEL_REGISTRY.keys())}, "
        f"Extra: {set(AGENT_MODEL_REGISTRY.keys()) - expected_agents}"
    )

    for agent_name, (primary, fallback) in AGENT_MODEL_REGISTRY.items():
        assert primary in MODELS, f"Agent '{agent_name}' primary model '{primary}' not in MODELS"
        if fallback is not None:
            assert fallback in MODELS, f"Agent '{agent_name}' fallback model '{fallback}' not in MODELS"
