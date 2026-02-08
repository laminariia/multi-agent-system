"""MAS exception hierarchy.

Provides structured exceptions for all failure modes across the Multi-Agent System.
Each exception carries contextual attributes (agent name, model, platform, etc.)
to support structured logging and targeted error recovery.
"""

from __future__ import annotations

from typing import Any


class MASException(Exception):
    """Root exception for the Multi-Agent System."""

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        self.details = details or {}
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_type": type(self).__name__,
            "message": str(self),
            "details": self.details,
        }


# ---------------------------------------------------------------------------
# Agent exceptions
# ---------------------------------------------------------------------------

class AgentException(MASException):
    """Base exception for agent-level failures."""

    def __init__(
        self,
        message: str,
        *,
        agent_name: str = "",
        thread_id: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        self.agent_name = agent_name
        self.thread_id = thread_id
        super().__init__(message, details={**(details or {}), "agent_name": agent_name, "thread_id": thread_id})


class HeartbeatTimeoutError(AgentException):
    """Raised when an agent fails to send heartbeat within the expected window."""

    def __init__(
        self,
        message: str = "Agent heartbeat timed out",
        *,
        agent_name: str = "",
        thread_id: str = "",
        last_heartbeat_ts: float = 0.0,
        timeout_seconds: float = 180.0,
        restart_count: int = 0,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.last_heartbeat_ts = last_heartbeat_ts
        self.timeout_seconds = timeout_seconds
        self.restart_count = restart_count
        super().__init__(
            message,
            agent_name=agent_name,
            thread_id=thread_id,
            details={
                **(details or {}),
                "last_heartbeat_ts": last_heartbeat_ts,
                "timeout_seconds": timeout_seconds,
                "restart_count": restart_count,
            },
        )


class LoopDetectedError(AgentException):
    """Raised when the loop detector identifies step repetition or iteration overflow."""

    def __init__(
        self,
        message: str = "Agent loop detected",
        *,
        agent_name: str = "",
        thread_id: str = "",
        iteration_count: int = 0,
        step_hash: str = "",
        repeat_count: int = 0,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.iteration_count = iteration_count
        self.step_hash = step_hash
        self.repeat_count = repeat_count
        super().__init__(
            message,
            agent_name=agent_name,
            thread_id=thread_id,
            details={
                **(details or {}),
                "iteration_count": iteration_count,
                "step_hash": step_hash,
                "repeat_count": repeat_count,
            },
        )


class HITLRequiredError(AgentException):
    """Raised when human-in-the-loop intervention is required to proceed."""

    def __init__(
        self,
        message: str = "Human-in-the-loop approval required",
        *,
        agent_name: str = "",
        thread_id: str = "",
        hitl_request_id: str = "",
        reason: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        self.hitl_request_id = hitl_request_id
        self.reason = reason
        super().__init__(
            message,
            agent_name=agent_name,
            thread_id=thread_id,
            details={**(details or {}), "hitl_request_id": hitl_request_id, "reason": reason},
        )


# ---------------------------------------------------------------------------
# LLM exceptions
# ---------------------------------------------------------------------------

class LLMException(MASException):
    """Base exception for LLM provider failures."""

    def __init__(
        self,
        message: str,
        *,
        model: str = "",
        provider: str = "",
        agent_name: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        self.model = model
        self.provider = provider
        self.agent_name = agent_name
        super().__init__(
            message,
            details={**(details or {}), "model": model, "provider": provider, "agent_name": agent_name},
        )


class LLMRateLimitError(LLMException):
    """Raised on HTTP 429 / rate-limit responses from LLM providers."""

    def __init__(
        self,
        message: str = "LLM rate limit exceeded",
        *,
        model: str = "",
        provider: str = "",
        agent_name: str = "",
        retry_after_seconds: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            message,
            model=model,
            provider=provider,
            agent_name=agent_name,
            details={**(details or {}), "retry_after_seconds": retry_after_seconds},
        )


class LLMTimeoutError(LLMException):
    """Raised when an LLM call exceeds the allowed response time."""

    def __init__(
        self,
        message: str = "LLM call timed out",
        *,
        model: str = "",
        provider: str = "",
        agent_name: str = "",
        timeout_seconds: float = 30.0,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        super().__init__(
            message,
            model=model,
            provider=provider,
            agent_name=agent_name,
            details={**(details or {}), "timeout_seconds": timeout_seconds},
        )


class LLMContextOverflowError(LLMException):
    """Raised when the prompt/messages exceed the model's context window."""

    def __init__(
        self,
        message: str = "LLM context window exceeded",
        *,
        model: str = "",
        provider: str = "",
        agent_name: str = "",
        token_count: int = 0,
        max_tokens: int = 0,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.token_count = token_count
        self.max_tokens = max_tokens
        super().__init__(
            message,
            model=model,
            provider=provider,
            agent_name=agent_name,
            details={**(details or {}), "token_count": token_count, "max_tokens": max_tokens},
        )


class LLMInvalidResponseError(LLMException):
    """Raised when the LLM returns an unparseable or structurally invalid response."""

    def __init__(
        self,
        message: str = "LLM returned invalid response",
        *,
        model: str = "",
        provider: str = "",
        agent_name: str = "",
        raw_response: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        self.raw_response = raw_response
        super().__init__(
            message,
            model=model,
            provider=provider,
            agent_name=agent_name,
            details={**(details or {}), "raw_response": raw_response[:500]},
        )


# ---------------------------------------------------------------------------
# Cache exceptions
# ---------------------------------------------------------------------------

class CacheException(MASException):
    """Base exception for Valkey/semantic cache failures."""

    def __init__(
        self,
        message: str,
        *,
        cache_layer: str = "",
        operation: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        self.cache_layer = cache_layer
        self.operation = operation
        super().__init__(message, details={**(details or {}), "cache_layer": cache_layer, "operation": operation})


# ---------------------------------------------------------------------------
# Checkpoint exceptions
# ---------------------------------------------------------------------------

class CheckpointException(MASException):
    """Base exception for LangGraph checkpoint persistence failures."""

    def __init__(
        self,
        message: str,
        *,
        thread_id: str = "",
        checkpoint_id: str = "",
        storage_layer: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        self.thread_id = thread_id
        self.checkpoint_id = checkpoint_id
        self.storage_layer = storage_layer
        super().__init__(
            message,
            details={
                **(details or {}),
                "thread_id": thread_id,
                "checkpoint_id": checkpoint_id,
                "storage_layer": storage_layer,
            },
        )


# ---------------------------------------------------------------------------
# Platform exceptions
# ---------------------------------------------------------------------------

class PlatformException(MASException):
    """Base exception for freelance platform interaction failures."""

    def __init__(
        self,
        message: str,
        *,
        platform: str = "",
        operation: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        self.platform = platform
        self.operation = operation
        super().__init__(message, details={**(details or {}), "platform": platform, "operation": operation})


class PlatformAPIError(PlatformException):
    """Raised on non-rate-limit, non-ban API errors from freelance platforms."""

    pass


class PlatformRateLimitError(PlatformException):
    """Raised when a freelance platform returns a rate-limit response."""

    def __init__(
        self,
        message: str = "Platform rate limit exceeded",
        *,
        platform: str = "",
        operation: str = "",
        retry_after_seconds: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            message,
            platform=platform,
            operation=operation,
            details={**(details or {}), "retry_after_seconds": retry_after_seconds},
        )


class PlatformBannedError(PlatformException):
    """Raised when the account is suspended or banned on a platform."""

    def __init__(
        self,
        message: str = "Account banned on platform",
        *,
        platform: str = "",
        operation: str = "",
        ban_reason: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        self.ban_reason = ban_reason
        super().__init__(
            message,
            platform=platform,
            operation=operation,
            details={**(details or {}), "ban_reason": ban_reason},
        )


# ---------------------------------------------------------------------------
# Security exceptions
# ---------------------------------------------------------------------------

class SecurityException(MASException):
    """Base exception for security-related failures."""

    def __init__(
        self,
        message: str,
        *,
        source: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        self.source = source
        super().__init__(message, details={**(details or {}), "source": source})


class CaptchaDetectedError(PlatformException):
    """Raised when a CAPTCHA challenge is detected during browser automation."""

    def __init__(
        self,
        message: str = "CAPTCHA detected — HITL intervention required",
        *,
        platform: str = "",
        operation: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, platform=platform, operation=operation, details=details)


class CloudflareBlockError(PlatformException):
    """Raised when Cloudflare anti-bot challenge blocks the request."""

    def __init__(
        self,
        message: str = "Cloudflare anti-bot block detected",
        *,
        platform: str = "",
        operation: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, platform=platform, operation=operation, details=details)


class SemgrepBlockedError(SecurityException):
    """Raised when Semgrep static analysis flags dangerous patterns in generated code."""

    def __init__(
        self,
        message: str = "Code blocked by Semgrep security scan",
        *,
        source: str = "semgrep",
        rule_ids: list[str] | None = None,
        critical_count: int = 0,
        warning_count: int = 0,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.rule_ids = rule_ids or []
        self.critical_count = critical_count
        self.warning_count = warning_count
        super().__init__(
            message,
            source=source,
            details={
                **(details or {}),
                "rule_ids": self.rule_ids,
                "critical_count": critical_count,
                "warning_count": warning_count,
            },
        )
