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
    """LLMClient.call('scout', ...) should use deepseek-v3-2 as primary."""
    client = LLMClient(max_retries=1, base_backoff_seconds=0.0)
    mock_response = _make_ai_response("scout result", 80, 40)

    with patch.object(client, "_get_or_create_model") as mock_model_fn:
        mock_chat = AsyncMock()
        mock_chat.ainvoke = AsyncMock(return_value=mock_response)
        mock_model_fn.return_value = mock_chat

        response, metrics = await client.call("scout", [HumanMessage(content="find jobs")])

    assert metrics.model_id == "google/gemini-2.5-flash"
    assert metrics.provider == "google"
    assert metrics.was_fallback is False
    assert response.content == "scout result"


async def test_call_falls_back_on_rate_limit():
    """If the primary model raises a rate-limit error, the fallback model should be tried."""
    client = LLMClient(max_retries=1, base_backoff_seconds=0.0)
    mock_fallback_response = _make_ai_response("fallback result", 60, 20)

    with patch.object(client, "_invoke_with_retries") as mock_invoke:
        mock_invoke.side_effect = [
            LLMRateLimitError(
                "Rate limit",
                model="deepseek/deepseek-v3.2",
                provider="deepseek",
                agent_name="scout",
            ),
            (
                mock_fallback_response,
                CallMetrics(
                    agent_name="scout",
                    model_id="anthropic/claude-haiku-4.5",
                    provider="anthropic",
                    was_fallback=True,
                    attempt=1,
                ),
            ),
        ]

        response, metrics = await client.call("scout", [HumanMessage(content="find jobs")])

    assert metrics.model_id == "anthropic/claude-haiku-4.5"
    assert metrics.provider == "anthropic"
    assert metrics.was_fallback is True


async def test_call_all_providers_exhausted_raises():
    """When all models in the chain fail, LLMException should be raised."""
    client = LLMClient(max_retries=1, base_backoff_seconds=0.0)

    with patch.object(client, "_invoke_with_retries") as mock_invoke:
        mock_invoke.side_effect = LLMRateLimitError("Rate limit", model="test", provider="test", agent_name="scout")

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
        "scout",
        "bid",
        "planner",
        "dev",
        "content",
        "design",
        "critic",
        "packager",
        "geoscout",
        "outreach",
    }

    assert set(AGENT_MODEL_REGISTRY.keys()) == expected_agents, (
        f"Registry agents mismatch. Missing: {expected_agents - set(AGENT_MODEL_REGISTRY.keys())}, "
        f"Extra: {set(AGENT_MODEL_REGISTRY.keys()) - expected_agents}"
    )

    for agent_name, model_chain in AGENT_MODEL_REGISTRY.items():
        assert len(model_chain) >= 2, f"Agent '{agent_name}' must have at least 2 models in chain"
        for model_key in model_chain:
            assert model_key in MODELS, f"Agent '{agent_name}' model '{model_key}' not in MODELS"


# ---------------------------------------------------------------------------
# Constructor api_key / base_url
# ---------------------------------------------------------------------------


def test_constructor_stores_api_key():
    """LLMClient should store the api_key passed at construction time."""
    client = LLMClient(api_key="sk-test-123")
    assert client._api_key == "sk-test-123"


def test_constructor_stores_base_url():
    """LLMClient should store the base_url passed at construction time."""
    client = LLMClient(base_url="https://custom.api/v1")
    assert client._base_url == "https://custom.api/v1"


def test_constructor_defaults_api_key_none():
    """When no api_key is provided, _api_key should be None."""
    client = LLMClient()
    assert client._api_key is None


def test_constructor_defaults_base_url_none():
    """When no base_url is provided, _base_url should be None."""
    client = LLMClient()
    assert client._base_url is None


# ---------------------------------------------------------------------------
# update_credentials
# ---------------------------------------------------------------------------


def test_update_credentials_sets_api_key():
    """update_credentials should update _api_key."""
    client = LLMClient()
    client.update_credentials(api_key="sk-new-key")
    assert client._api_key == "sk-new-key"


def test_update_credentials_sets_base_url():
    """update_credentials should update _base_url when provided."""
    client = LLMClient()
    client.update_credentials(api_key="sk-key", base_url="https://new.api/v1")
    assert client._base_url == "https://new.api/v1"


def test_update_credentials_preserves_base_url_when_none():
    """update_credentials should keep existing _base_url when base_url is None."""
    client = LLMClient(base_url="https://original.api/v1")
    client.update_credentials(api_key="sk-key")
    assert client._base_url == "https://original.api/v1"


def test_update_credentials_clears_model_cache():
    """update_credentials should clear the internal model cache."""
    client = LLMClient()
    # Pre-populate the cache
    client._chat_model_cache["some_key"] = MagicMock()
    client._chat_model_cache["another_key"] = MagicMock()
    assert len(client._chat_model_cache) == 2

    client.update_credentials(api_key="sk-rotated")
    assert len(client._chat_model_cache) == 0


def test_update_credentials_overwrites_previous_key():
    """Calling update_credentials twice should use the latest api_key."""
    client = LLMClient(api_key="sk-first")
    client.update_credentials(api_key="sk-second")
    assert client._api_key == "sk-second"


# ---------------------------------------------------------------------------
# _get_or_create_model uses _api_key with env fallback
# ---------------------------------------------------------------------------


def test_get_or_create_model_uses_instance_api_key():
    """_get_or_create_model should pass self._api_key to ChatOpenAI when set."""
    client = LLMClient(api_key="sk-instance-key")
    spec = MODELS["claude-haiku-4-5"]

    with patch("src.core.llm_client.ChatOpenAI") as mock_chat_cls:
        mock_chat_cls.return_value = MagicMock()
        client._get_or_create_model("claude-haiku-4-5", spec, 0.7, None)

        call_kwargs = mock_chat_cls.call_args
        assert call_kwargs.kwargs["api_key"] == "sk-instance-key"


def test_get_or_create_model_falls_back_to_env_var():
    """_get_or_create_model should use OPENROUTER_API_KEY env var when _api_key is None."""
    client = LLMClient()  # no api_key
    spec = MODELS["claude-haiku-4-5"]

    with (
        patch("src.core.llm_client.ChatOpenAI") as mock_chat_cls,
        patch.dict("os.environ", {"OPENROUTER_API_KEY": "sk-env-key"}),
    ):
        mock_chat_cls.return_value = MagicMock()
        client._get_or_create_model("claude-haiku-4-5", spec, 0.7, None)

        call_kwargs = mock_chat_cls.call_args
        assert call_kwargs.kwargs["api_key"] == "sk-env-key"


def test_get_or_create_model_uses_instance_base_url():
    """_get_or_create_model should pass self._base_url to ChatOpenAI when set."""
    client = LLMClient(api_key="sk-key", base_url="https://custom.api/v1")
    spec = MODELS["claude-haiku-4-5"]

    with patch("src.core.llm_client.ChatOpenAI") as mock_chat_cls:
        mock_chat_cls.return_value = MagicMock()
        client._get_or_create_model("claude-haiku-4-5", spec, 0.7, None)

        call_kwargs = mock_chat_cls.call_args
        assert call_kwargs.kwargs["base_url"] == "https://custom.api/v1"


def test_get_or_create_model_caches_result():
    """_get_or_create_model should cache and return the same model for identical params."""
    client = LLMClient(api_key="sk-key")
    spec = MODELS["claude-haiku-4-5"]

    with patch("src.core.llm_client.ChatOpenAI") as mock_chat_cls:
        mock_instance = MagicMock()
        mock_chat_cls.return_value = mock_instance

        model1 = client._get_or_create_model("claude-haiku-4-5", spec, 0.7, None)
        model2 = client._get_or_create_model("claude-haiku-4-5", spec, 0.7, None)

        assert model1 is model2
        assert mock_chat_cls.call_count == 1


def test_get_or_create_model_cache_invalidated_by_update_credentials():
    """After update_credentials, _get_or_create_model should create a new model instance."""
    client = LLMClient(api_key="sk-old-key")
    spec = MODELS["claude-haiku-4-5"]

    with patch("src.core.llm_client.ChatOpenAI") as mock_chat_cls:
        mock_chat_cls.return_value = MagicMock()

        client._get_or_create_model("claude-haiku-4-5", spec, 0.7, None)
        assert mock_chat_cls.call_count == 1

        # Rotate credentials
        client.update_credentials(api_key="sk-new-key")

        client._get_or_create_model("claude-haiku-4-5", spec, 0.7, None)
        assert mock_chat_cls.call_count == 2
        # Verify the new key was used
        assert mock_chat_cls.call_args.kwargs["api_key"] == "sk-new-key"
