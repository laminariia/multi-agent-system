"""Tests for the Telegram post LLM parser."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.telegram_parser import _MIN_POST_LENGTH, _safe_float, parse_telegram_post


@pytest.fixture()
def mock_llm_client() -> AsyncMock:
    return AsyncMock()


# ---------------------------------------------------------------------------
# _safe_float
# ---------------------------------------------------------------------------


def test_safe_float_with_number() -> None:
    assert _safe_float(42.5) == 42.5


def test_safe_float_with_string() -> None:
    assert _safe_float("100") == 100.0


def test_safe_float_with_none() -> None:
    assert _safe_float(None) is None


def test_safe_float_with_invalid() -> None:
    assert _safe_float("not_a_number") is None


# ---------------------------------------------------------------------------
# parse_telegram_post — too short
# ---------------------------------------------------------------------------


async def test_parse_short_post_returns_none(mock_llm_client: AsyncMock) -> None:
    result = await parse_telegram_post("Short", "test_channel", mock_llm_client)
    assert result is None
    mock_llm_client.call.assert_not_called()


async def test_parse_empty_post_returns_none(mock_llm_client: AsyncMock) -> None:
    result = await parse_telegram_post("", "test_channel", mock_llm_client)
    assert result is None


# ---------------------------------------------------------------------------
# parse_telegram_post — not a job
# ---------------------------------------------------------------------------


async def test_parse_non_job_returns_none(mock_llm_client: AsyncMock) -> None:
    llm_response = MagicMock()
    llm_response.content = '{"is_job": false}'
    mock_llm_client.call.return_value = (llm_response, {})

    text = "A" * (_MIN_POST_LENGTH + 10)
    result = await parse_telegram_post(text, "test_channel", mock_llm_client)
    assert result is None


# ---------------------------------------------------------------------------
# parse_telegram_post — valid job
# ---------------------------------------------------------------------------


async def test_parse_valid_job(mock_llm_client: AsyncMock) -> None:
    llm_response = MagicMock()
    llm_response.content = """{
        "is_job": true,
        "title": "Build a landing page",
        "description": "Need a modern responsive landing page with animations",
        "budget_min": 30000,
        "budget_max": 50000,
        "currency": "RUB",
        "skills": ["HTML", "CSS", "JavaScript"],
        "deadline": "2026-03-15"
    }"""
    mock_llm_client.call.return_value = (llm_response, {})

    text = "A" * (_MIN_POST_LENGTH + 10)
    result = await parse_telegram_post(text, "freelance_channel", mock_llm_client)

    assert result is not None
    assert result["title"] == "Build a landing page"
    assert result["budget_min"] == 30000.0
    assert result["budget_max"] == 50000.0
    assert result["currency"] == "RUB"
    assert "HTML" in result["skills"]
    assert result["deadline"] == "2026-03-15"


# ---------------------------------------------------------------------------
# parse_telegram_post — LLM failure
# ---------------------------------------------------------------------------


async def test_parse_llm_failure_returns_none(mock_llm_client: AsyncMock) -> None:
    mock_llm_client.call.side_effect = RuntimeError("LLM unavailable")

    text = "A" * (_MIN_POST_LENGTH + 10)
    result = await parse_telegram_post(text, "test_channel", mock_llm_client)
    assert result is None


# ---------------------------------------------------------------------------
# parse_telegram_post — invalid JSON from LLM
# ---------------------------------------------------------------------------


async def test_parse_invalid_json_returns_none(mock_llm_client: AsyncMock) -> None:
    llm_response = MagicMock()
    llm_response.content = "This is not JSON at all"
    mock_llm_client.call.return_value = (llm_response, {})

    text = "A" * (_MIN_POST_LENGTH + 10)
    result = await parse_telegram_post(text, "test_channel", mock_llm_client)
    assert result is None


# ---------------------------------------------------------------------------
# parse_telegram_post — null budget handling
# ---------------------------------------------------------------------------


async def test_parse_job_with_null_budget(mock_llm_client: AsyncMock) -> None:
    llm_response = MagicMock()
    llm_response.content = """{
        "is_job": true,
        "title": "Write an article",
        "description": "Need a 2000-word article about AI",
        "budget_min": null,
        "budget_max": null,
        "currency": null,
        "skills": ["copywriting"],
        "deadline": null
    }"""
    mock_llm_client.call.return_value = (llm_response, {})

    text = "A" * (_MIN_POST_LENGTH + 10)
    result = await parse_telegram_post(text, "test_channel", mock_llm_client)

    assert result is not None
    assert result["budget_min"] is None
    assert result["budget_max"] is None
    assert result["currency"] == "RUB"  # fallback default
    assert result["skills"] == ["copywriting"]
