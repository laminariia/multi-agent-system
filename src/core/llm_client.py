"""LLM client with fallback chains and cost tracking.

Centralises all LLM calls for the Multi-Agent System.  Every agent delegates
to ``LLMClient.call(agent_name, messages, **kwargs)`` which transparently
handles:

* Model selection from a canonical registry (TECH_STACK.md).
* Automatic fallback to a secondary provider on transient errors.
* Exponential back-off on rate-limit responses (1 s -> 2 s -> 4 s -> 8 s,
  max 5 retries per provider attempt).
* Per-call cost and latency tracking.
* Structured logging via *structlog*.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Any

import structlog
from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI

from src.core.exceptions import (
    LLMContextOverflowError,
    LLMException,
    LLMInvalidResponseError,
    LLMRateLimitError,
    LLMTimeoutError,
)

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Model registry -- canonical agent -> (primary, fallback) mapping
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ModelSpec:
    """Specification for a single LLM model."""

    provider: str          # "google", "anthropic", "openai" (original provider)
    model_id: str          # OpenRouter model identifier (e.g. "google/gemini-2.5-flash")
    cost_input_per_1k: float   # USD per 1 000 input tokens
    cost_output_per_1k: float  # USD per 1 000 output tokens
    max_context_tokens: int = 128_000


# OpenRouter model IDs — all routed through a single API key.
# Prices are approximate (OpenRouter adds a small markup).
MODELS: dict[str, ModelSpec] = {
    "gemini-3-flash": ModelSpec(
        provider="google",
        model_id="google/gemini-2.5-flash",
        cost_input_per_1k=0.0001,
        cost_output_per_1k=0.0004,
        max_context_tokens=1_048_576,
    ),
    "gemini-3-pro": ModelSpec(
        provider="google",
        model_id="google/gemini-2.5-pro",
        cost_input_per_1k=0.00125,
        cost_output_per_1k=0.005,
        max_context_tokens=1_048_576,
    ),
    "claude-opus-4-6": ModelSpec(
        provider="anthropic",
        model_id="anthropic/claude-opus-4.6",
        cost_input_per_1k=0.015,
        cost_output_per_1k=0.075,
        max_context_tokens=1_000_000,
    ),
    "claude-haiku-4-5": ModelSpec(
        provider="anthropic",
        model_id="anthropic/claude-haiku-4.5",
        cost_input_per_1k=0.0008,
        cost_output_per_1k=0.004,
        max_context_tokens=200_000,
    ),
    "gpt-4o": ModelSpec(
        provider="openai",
        model_id="openai/gpt-4o",
        cost_input_per_1k=0.002,
        cost_output_per_1k=0.008,
        max_context_tokens=128_000,
    ),
    "gpt-4o-mini": ModelSpec(
        provider="openai",
        model_id="openai/gpt-4o-mini",
        cost_input_per_1k=0.00015,
        cost_output_per_1k=0.0006,
        max_context_tokens=128_000,
    ),
    "claude-sonnet-4-5": ModelSpec(
        provider="anthropic",
        model_id="anthropic/claude-sonnet-4.5",
        cost_input_per_1k=0.003,
        cost_output_per_1k=0.015,
        max_context_tokens=1_000_000,
    ),
}

# Agent -> (primary_model_key, fallback_model_key | None)
# NOTE: Gemini models are blocked on some OpenRouter accounts.
# Using Claude Haiku as default cheap model, GPT-4o-mini as fallback.
AGENT_MODEL_REGISTRY: dict[str, tuple[str, str | None]] = {
    "scout":     ("claude-haiku-4-5", "gpt-4o-mini"),
    "bid":       ("claude-haiku-4-5", "gpt-4o-mini"),
    "planner":   ("claude-opus-4-6",  "claude-sonnet-4-5"),
    "dev":       ("claude-opus-4-6",  "claude-sonnet-4-5"),
    "content":   ("claude-haiku-4-5", "gpt-4o-mini"),
    "design":    ("claude-sonnet-4-5", "claude-haiku-4-5"),
    "critic":    ("gpt-4o",           "claude-sonnet-4-5"),
    "packager":  ("claude-haiku-4-5", None),
    "geoscout":  ("claude-haiku-4-5", None),
    "outreach":  ("claude-haiku-4-5", None),
}

# ---------------------------------------------------------------------------
# Cost tracking
# ---------------------------------------------------------------------------

@dataclass
class CallMetrics:
    """Metrics captured for a single LLM invocation."""

    agent_name: str
    model_id: str
    provider: str
    tokens_input: int = 0
    tokens_output: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    was_fallback: bool = False
    attempt: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "model_id": self.model_id,
            "provider": self.provider,
            "tokens_input": self.tokens_input,
            "tokens_output": self.tokens_output,
            "cost_usd": round(self.cost_usd, 6),
            "latency_ms": round(self.latency_ms, 2),
            "was_fallback": self.was_fallback,
            "attempt": self.attempt,
        }


@dataclass
class CostTracker:
    """Accumulates per-agent and per-model cost totals across the process lifetime."""

    records: list[CallMetrics] = field(default_factory=list)

    @property
    def total_cost_usd(self) -> float:
        return sum(r.cost_usd for r in self.records)

    def by_agent(self) -> dict[str, float]:
        costs: dict[str, float] = {}
        for r in self.records:
            costs[r.agent_name] = costs.get(r.agent_name, 0.0) + r.cost_usd
        return costs

    def by_model(self) -> dict[str, float]:
        costs: dict[str, float] = {}
        for r in self.records:
            costs[r.model_id] = costs.get(r.model_id, 0.0) + r.cost_usd
        return costs

    def record(self, metrics: CallMetrics) -> None:
        self.records.append(metrics)


# ---------------------------------------------------------------------------
# LLM Client
# ---------------------------------------------------------------------------

_RATE_LIMIT_EXCEPTIONS: tuple[type[BaseException], ...] = ()
_TIMEOUT_EXCEPTIONS: tuple[type[BaseException], ...] = ()

# These will be populated lazily so we don't fail at import time if an
# optional provider SDK is missing.
_PROVIDER_ERRORS_LOADED = False


def _load_provider_errors() -> None:
    """Populate exception tuples from provider SDKs (best-effort)."""
    global _RATE_LIMIT_EXCEPTIONS, _TIMEOUT_EXCEPTIONS, _PROVIDER_ERRORS_LOADED  # noqa: PLW0603
    if _PROVIDER_ERRORS_LOADED:
        return

    rate_limit: list[type[BaseException]] = []
    timeout: list[type[BaseException]] = []

    try:
        from openai import RateLimitError as OpenAIRateLimit
        rate_limit.append(OpenAIRateLimit)
    except ImportError:
        pass
    try:
        from openai import APITimeoutError as OpenAITimeout
        timeout.append(OpenAITimeout)
    except ImportError:
        pass
    try:
        from anthropic import RateLimitError as AnthropicRateLimit
        rate_limit.append(AnthropicRateLimit)
    except ImportError:
        pass
    try:
        from google.api_core.exceptions import ResourceExhausted
        rate_limit.append(ResourceExhausted)
    except ImportError:
        pass

    _RATE_LIMIT_EXCEPTIONS = tuple(rate_limit)
    _TIMEOUT_EXCEPTIONS = tuple(timeout)
    _PROVIDER_ERRORS_LOADED = True


class LLMClient:
    """Unified interface for calling LLMs with automatic fallback.

    Args:
        cost_tracker: Optional external tracker; a private one is created if omitted.
        max_retries: Number of retries *per provider* on rate-limit errors.
        base_backoff_seconds: Initial back-off delay that doubles on each retry.
        request_timeout: Per-request timeout in seconds passed to chat model constructors.
    """

    def __init__(
        self,
        *,
        cost_tracker: CostTracker | None = None,
        max_retries: int = 5,
        base_backoff_seconds: float = 1.0,
        request_timeout: float = 60.0,
    ) -> None:
        _load_provider_errors()
        self.cost_tracker = cost_tracker or CostTracker()
        self.max_retries = max_retries
        self.base_backoff_seconds = base_backoff_seconds
        self.request_timeout = request_timeout
        self._chat_model_cache: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    async def call(
        self,
        agent_name: str,
        messages: list[BaseMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        force_model: str | None = None,
    ) -> tuple[BaseMessage, CallMetrics]:
        """Invoke the LLM assigned to *agent_name* with automatic fallback.

        Args:
            agent_name: Canonical agent name (e.g. ``"scout"``, ``"dev"``).
            messages: List of LangChain ``BaseMessage`` objects.
            temperature: Sampling temperature.
            max_tokens: Optional response length cap.
            force_model: Override the registry and use this model key directly.

        Returns:
            A tuple of (response_message, call_metrics).

        Raises:
            LLMException: When all providers (including fallbacks) have been
                exhausted.
        """
        if force_model:
            model_chain = [force_model]
        else:
            primary, fallback = AGENT_MODEL_REGISTRY.get(agent_name, ("gemini-3-flash", None))
            model_chain = [primary] + ([fallback] if fallback else [])

        last_error: BaseException | None = None

        for chain_idx, model_key in enumerate(model_chain):
            is_fallback = chain_idx > 0
            spec = MODELS.get(model_key)
            if spec is None:
                logger.warning("unknown_model_key", model_key=model_key, agent=agent_name)
                continue

            try:
                result, metrics = await self._invoke_with_retries(
                    agent_name=agent_name,
                    model_key=model_key,
                    spec=spec,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    is_fallback=is_fallback,
                )
                return result, metrics
            except (LLMRateLimitError, LLMTimeoutError) as exc:
                last_error = exc
                logger.warning(
                    "llm_provider_failed_switching_to_fallback",
                    agent=agent_name,
                    model=model_key,
                    error=str(exc),
                    fallback_available=chain_idx < len(model_chain) - 1,
                )
            except LLMException as exc:
                last_error = exc
                logger.error(
                    "llm_non_retryable_error",
                    agent=agent_name,
                    model=model_key,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )

        raise LLMException(
            f"All LLM providers exhausted for agent '{agent_name}'",
            agent_name=agent_name,
            details={"last_error": str(last_error)},
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _invoke_with_retries(
        self,
        *,
        agent_name: str,
        model_key: str,
        spec: ModelSpec,
        messages: list[BaseMessage],
        temperature: float,
        max_tokens: int | None,
        is_fallback: bool,
    ) -> tuple[BaseMessage, CallMetrics]:
        """Try to call a single model up to ``max_retries`` times with exponential back-off."""
        chat_model = self._get_or_create_model(model_key, spec, temperature, max_tokens)

        for attempt in range(1, self.max_retries + 1):
            t0 = time.perf_counter()
            try:
                response = await chat_model.ainvoke(messages)
                latency_ms = (time.perf_counter() - t0) * 1000

                usage = getattr(response, "usage_metadata", None) or {}
                tokens_in = (
                    usage.get("input_tokens", 0) if isinstance(usage, dict)
                    else getattr(usage, "input_tokens", 0)
                )
                tokens_out = (
                    usage.get("output_tokens", 0) if isinstance(usage, dict)
                    else getattr(usage, "output_tokens", 0)
                )

                cost = (
                    (tokens_in / 1000) * spec.cost_input_per_1k
                    + (tokens_out / 1000) * spec.cost_output_per_1k
                )

                metrics = CallMetrics(
                    agent_name=agent_name,
                    model_id=spec.model_id,
                    provider=spec.provider,
                    tokens_input=tokens_in,
                    tokens_output=tokens_out,
                    cost_usd=cost,
                    latency_ms=latency_ms,
                    was_fallback=is_fallback,
                    attempt=attempt,
                )
                self.cost_tracker.record(metrics)

                logger.info(
                    "llm_call_success",
                    agent=agent_name,
                    model=spec.model_id,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    cost_usd=round(cost, 6),
                    latency_ms=round(latency_ms, 2),
                    attempt=attempt,
                    fallback=is_fallback,
                )
                return response, metrics

            except _RATE_LIMIT_EXCEPTIONS as exc:
                backoff = self.base_backoff_seconds * (2 ** (attempt - 1))
                logger.warning(
                    "llm_rate_limit",
                    agent=agent_name,
                    model=spec.model_id,
                    attempt=attempt,
                    backoff_s=backoff,
                    error=str(exc),
                )
                if attempt == self.max_retries:
                    raise LLMRateLimitError(
                        f"Rate limit on {spec.model_id} after {attempt} retries",
                        model=spec.model_id,
                        provider=spec.provider,
                        agent_name=agent_name,
                    ) from exc
                await asyncio.sleep(backoff)

            except _TIMEOUT_EXCEPTIONS as exc:
                logger.warning(
                    "llm_timeout",
                    agent=agent_name,
                    model=spec.model_id,
                    attempt=attempt,
                    error=str(exc),
                )
                if attempt == self.max_retries:
                    raise LLMTimeoutError(
                        f"Timeout on {spec.model_id} after {attempt} retries",
                        model=spec.model_id,
                        provider=spec.provider,
                        agent_name=agent_name,
                        timeout_seconds=self.request_timeout,
                    ) from exc
                await asyncio.sleep(self.base_backoff_seconds)

            except Exception as exc:
                latency_ms = (time.perf_counter() - t0) * 1000
                error_msg = str(exc).lower()

                is_overflow = "overflow" in error_msg or "too long" in error_msg or "token" in error_msg
                if "context" in error_msg and is_overflow:
                    raise LLMContextOverflowError(
                        f"Context overflow on {spec.model_id}: {exc}",
                        model=spec.model_id,
                        provider=spec.provider,
                        agent_name=agent_name,
                        max_tokens=spec.max_context_tokens,
                    ) from exc

                if "invalid" in error_msg or "parse" in error_msg or "json" in error_msg:
                    raise LLMInvalidResponseError(
                        f"Invalid response from {spec.model_id}: {exc}",
                        model=spec.model_id,
                        provider=spec.provider,
                        agent_name=agent_name,
                        raw_response=str(exc)[:500],
                    ) from exc

                raise LLMException(
                    f"Unexpected error from {spec.model_id}: {exc}",
                    model=spec.model_id,
                    provider=spec.provider,
                    agent_name=agent_name,
                ) from exc

        # Should not reach here, but satisfy the type checker
        raise LLMException("Retry loop exited unexpectedly", model=model_key, agent_name=agent_name)

    def _get_or_create_model(
        self,
        model_key: str,
        spec: ModelSpec,
        temperature: float,
        max_tokens: int | None,
    ) -> Any:
        """Lazily create and cache a LangChain chat model instance.

        All models are routed through OpenRouter via the OpenAI-compatible API.
        """
        cache_key = f"{model_key}:{temperature}:{max_tokens}"
        if cache_key in self._chat_model_cache:
            return self._chat_model_cache[cache_key]

        common_kwargs: dict[str, Any] = {"temperature": temperature}
        if max_tokens is not None:
            common_kwargs["max_tokens"] = max_tokens

        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        base_url = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

        model = ChatOpenAI(
            model=spec.model_id,
            api_key=api_key,
            base_url=base_url,
            request_timeout=self.request_timeout,
            default_headers={
                "HTTP-Referer": "https://multi-agent-service.local",
                "X-Title": "Multi-Agent Service",
            },
            **common_kwargs,
        )

        self._chat_model_cache[cache_key] = model
        return model
