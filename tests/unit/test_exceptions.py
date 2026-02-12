"""Unit tests for src.core.exceptions — MAS exception hierarchy.

Covers: to_dict serialization, attribute preservation, inheritance,
and exception-specific fields for all exception classes.
"""

from __future__ import annotations

from src.core.exceptions import (
    AgentException,
    CacheException,
    CaptchaDetectedError,
    CheckpointException,
    CloudflareBlockError,
    HeartbeatTimeoutError,
    HITLRequiredError,
    LLMContextOverflowError,
    LLMException,
    LLMInvalidResponseError,
    LLMRateLimitError,
    LLMTimeoutError,
    LoopDetectedError,
    MASException,
    PlatformAPIError,
    PlatformBannedError,
    PlatformException,
    PlatformRateLimitError,
    SecurityException,
    SemgrepBlockedError,
)

# ---------------------------------------------------------------------------
# MASException (root)
# ---------------------------------------------------------------------------


def test_mas_exception_message():
    """MASException should preserve the error message."""
    exc = MASException("Something failed")
    assert str(exc) == "Something failed"


def test_mas_exception_details_default():
    """MASException.details should default to empty dict."""
    exc = MASException("error")
    assert exc.details == {}


def test_mas_exception_details_provided():
    """MASException.details should preserve provided details."""
    exc = MASException("error", details={"key": "value"})
    assert exc.details == {"key": "value"}


def test_mas_exception_to_dict():
    """to_dict should return structured error info."""
    exc = MASException("test error", details={"foo": "bar"})
    d = exc.to_dict()
    assert d["error_type"] == "MASException"
    assert d["message"] == "test error"
    assert d["details"] == {"foo": "bar"}


# ---------------------------------------------------------------------------
# AgentException
# ---------------------------------------------------------------------------


def test_agent_exception_attributes():
    """AgentException should preserve agent_name and thread_id."""
    exc = AgentException("failed", agent_name="scout", thread_id="t-001")
    assert exc.agent_name == "scout"
    assert exc.thread_id == "t-001"
    assert exc.details["agent_name"] == "scout"
    assert exc.details["thread_id"] == "t-001"


def test_agent_exception_inheritance():
    """AgentException should be a subclass of MASException."""
    assert issubclass(AgentException, MASException)


# ---------------------------------------------------------------------------
# HeartbeatTimeoutError
# ---------------------------------------------------------------------------


def test_heartbeat_timeout_attributes():
    """HeartbeatTimeoutError should preserve all custom attributes."""
    exc = HeartbeatTimeoutError(
        agent_name="dev",
        last_heartbeat_ts=1000.0,
        timeout_seconds=180.0,
        restart_count=2,
    )
    assert exc.last_heartbeat_ts == 1000.0
    assert exc.timeout_seconds == 180.0
    assert exc.restart_count == 2
    assert exc.details["last_heartbeat_ts"] == 1000.0


def test_heartbeat_timeout_default_message():
    """HeartbeatTimeoutError should have a default message."""
    exc = HeartbeatTimeoutError()
    assert "heartbeat" in str(exc).lower()


# ---------------------------------------------------------------------------
# LoopDetectedError
# ---------------------------------------------------------------------------


def test_loop_detected_attributes():
    """LoopDetectedError should preserve iteration and repeat info."""
    exc = LoopDetectedError(
        agent_name="planner",
        iteration_count=50,
        step_hash="abc123",
        repeat_count=3,
    )
    assert exc.iteration_count == 50
    assert exc.step_hash == "abc123"
    assert exc.repeat_count == 3


# ---------------------------------------------------------------------------
# HITLRequiredError
# ---------------------------------------------------------------------------


def test_hitl_required_attributes():
    """HITLRequiredError should preserve request ID and reason."""
    exc = HITLRequiredError(
        agent_name="bid",
        hitl_request_id="hitl-001",
        reason="Bid needs approval",
    )
    assert exc.hitl_request_id == "hitl-001"
    assert exc.reason == "Bid needs approval"
    assert exc.details["hitl_request_id"] == "hitl-001"


# ---------------------------------------------------------------------------
# LLM exceptions
# ---------------------------------------------------------------------------


def test_llm_exception_attributes():
    """LLMException should preserve model, provider, agent_name."""
    exc = LLMException("API error", model="gemini-3-flash", provider="google", agent_name="scout")
    assert exc.model == "gemini-3-flash"
    assert exc.provider == "google"
    assert exc.agent_name == "scout"


def test_llm_rate_limit_retry_after():
    """LLMRateLimitError should preserve retry_after_seconds."""
    exc = LLMRateLimitError(provider="google", retry_after_seconds=30.0)
    assert exc.retry_after_seconds == 30.0
    assert exc.details["retry_after_seconds"] == 30.0


def test_llm_timeout_seconds():
    """LLMTimeoutError should preserve timeout_seconds."""
    exc = LLMTimeoutError(timeout_seconds=60.0)
    assert exc.timeout_seconds == 60.0


def test_llm_context_overflow():
    """LLMContextOverflowError should preserve token counts."""
    exc = LLMContextOverflowError(token_count=150000, max_tokens=128000)
    assert exc.token_count == 150000
    assert exc.max_tokens == 128000


def test_llm_invalid_response_raw_truncated():
    """LLMInvalidResponseError should truncate raw_response in details to 500 chars."""
    long_response = "x" * 1000
    exc = LLMInvalidResponseError(raw_response=long_response)
    assert len(exc.details["raw_response"]) == 500


def test_llm_invalid_response_short_preserved():
    """LLMInvalidResponseError should preserve short raw_response as-is."""
    exc = LLMInvalidResponseError(raw_response="short")
    assert exc.details["raw_response"] == "short"


# ---------------------------------------------------------------------------
# Cache exceptions
# ---------------------------------------------------------------------------


def test_cache_exception_attributes():
    """CacheException should preserve cache_layer and operation."""
    exc = CacheException("Cache miss", cache_layer="valkey", operation="get")
    assert exc.cache_layer == "valkey"
    assert exc.operation == "get"


# ---------------------------------------------------------------------------
# Checkpoint exceptions
# ---------------------------------------------------------------------------


def test_checkpoint_exception_attributes():
    """CheckpointException should preserve thread_id, checkpoint_id, storage_layer."""
    exc = CheckpointException(
        "Checkpoint save failed",
        thread_id="t-001",
        checkpoint_id="cp-001",
        storage_layer="postgres",
    )
    assert exc.thread_id == "t-001"
    assert exc.checkpoint_id == "cp-001"
    assert exc.storage_layer == "postgres"


# ---------------------------------------------------------------------------
# Platform exceptions
# ---------------------------------------------------------------------------


def test_platform_exception_attributes():
    """PlatformException should preserve platform and operation."""
    exc = PlatformException("API error", platform="freelancer", operation="fetch_jobs")
    assert exc.platform == "freelancer"
    assert exc.operation == "fetch_jobs"


def test_platform_api_error_inheritance():
    """PlatformAPIError should be a subclass of PlatformException."""
    assert issubclass(PlatformAPIError, PlatformException)


def test_platform_rate_limit_retry_after():
    """PlatformRateLimitError should preserve retry_after_seconds."""
    exc = PlatformRateLimitError(platform="kwork", retry_after_seconds=60.0)
    assert exc.retry_after_seconds == 60.0


def test_platform_banned_reason():
    """PlatformBannedError should preserve ban_reason."""
    exc = PlatformBannedError(platform="upwork", ban_reason="Automated submissions detected")
    assert exc.ban_reason == "Automated submissions detected"


# ---------------------------------------------------------------------------
# Security exceptions
# ---------------------------------------------------------------------------


def test_security_exception_source():
    """SecurityException should preserve source."""
    exc = SecurityException("Blocked", source="semgrep")
    assert exc.source == "semgrep"


def test_captcha_detected_inheritance():
    """CaptchaDetectedError should be a PlatformException."""
    assert issubclass(CaptchaDetectedError, PlatformException)
    exc = CaptchaDetectedError(platform="upwork")
    assert exc.platform == "upwork"


def test_cloudflare_block_inheritance():
    """CloudflareBlockError should be a PlatformException."""
    assert issubclass(CloudflareBlockError, PlatformException)


def test_semgrep_blocked_attributes():
    """SemgrepBlockedError should preserve rule_ids and counts."""
    exc = SemgrepBlockedError(
        rule_ids=["S101", "S102"],
        critical_count=2,
        warning_count=1,
    )
    assert exc.rule_ids == ["S101", "S102"]
    assert exc.critical_count == 2
    assert exc.warning_count == 1


def test_semgrep_blocked_default_rule_ids():
    """SemgrepBlockedError should default rule_ids to empty list."""
    exc = SemgrepBlockedError()
    assert exc.rule_ids == []


# ---------------------------------------------------------------------------
# Inheritance chain validation
# ---------------------------------------------------------------------------


def test_all_exceptions_are_mas_exception():
    """All custom exceptions should be subclasses of MASException."""
    exception_classes = [
        AgentException, HeartbeatTimeoutError, LoopDetectedError,
        HITLRequiredError, LLMException, LLMRateLimitError,
        LLMTimeoutError, LLMContextOverflowError, LLMInvalidResponseError,
        CacheException, CheckpointException, PlatformException,
        PlatformAPIError, PlatformRateLimitError, PlatformBannedError,
        SecurityException, CaptchaDetectedError, CloudflareBlockError,
        SemgrepBlockedError,
    ]
    for cls in exception_classes:
        assert issubclass(cls, MASException), f"{cls.__name__} should be a MASException subclass"


def test_all_exceptions_have_to_dict():
    """All custom exceptions should have a to_dict method."""
    exc = SemgrepBlockedError(rule_ids=["S101"])
    d = exc.to_dict()
    assert "error_type" in d
    assert d["error_type"] == "SemgrepBlockedError"
