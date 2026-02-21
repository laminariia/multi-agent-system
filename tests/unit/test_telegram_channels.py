"""Tests for the Telegram channel adapter (queue consumer)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from src.adapters.telegram_channels import VALKEY_QUEUE_KEY, TelegramChannelAdapter


@pytest.fixture()
def mock_valkey() -> AsyncMock:
    return AsyncMock()


@pytest.fixture()
def adapter(mock_valkey: AsyncMock) -> TelegramChannelAdapter:
    return TelegramChannelAdapter(valkey=mock_valkey)


# ---------------------------------------------------------------------------
# fetch_jobs — empty queue
# ---------------------------------------------------------------------------


async def test_fetch_jobs_empty_queue(adapter: TelegramChannelAdapter, mock_valkey: AsyncMock) -> None:
    mock_valkey.rpop.return_value = None

    result = await adapter.fetch_jobs()

    assert result == []
    mock_valkey.rpop.assert_called_once_with(VALKEY_QUEUE_KEY)


# ---------------------------------------------------------------------------
# fetch_jobs — multiple items
# ---------------------------------------------------------------------------


async def test_fetch_jobs_returns_parsed_items(adapter: TelegramChannelAdapter, mock_valkey: AsyncMock) -> None:
    job1 = {
        "platform": "telegram",
        "external_id": "tg_123_456",
        "title": "Build a landing page",
        "description": "Need a modern landing page",
        "budget_min": 500.0,
        "budget_max": 1000.0,
        "currency": "USD",
    }
    job2 = {
        "platform": "telegram",
        "external_id": "tg_123_789",
        "title": "WordPress plugin",
        "description": "Custom WP plugin",
        "budget_min": None,
        "budget_max": None,
        "currency": "RUB",
    }

    # rpop returns items then None
    mock_valkey.rpop.side_effect = [
        json.dumps(job1),
        json.dumps(job2),
        None,
    ]

    result = await adapter.fetch_jobs(max_results=10)

    assert len(result) == 2
    assert result[0]["external_id"] == "tg_123_456"
    assert result[1]["title"] == "WordPress plugin"


# ---------------------------------------------------------------------------
# fetch_jobs — respects max_results
# ---------------------------------------------------------------------------


async def test_fetch_jobs_respects_max_results(adapter: TelegramChannelAdapter, mock_valkey: AsyncMock) -> None:
    job = {"platform": "telegram", "external_id": "tg_1_1", "title": "Test"}
    mock_valkey.rpop.return_value = json.dumps(job)

    result = await adapter.fetch_jobs(max_results=3)

    assert len(result) == 3
    assert mock_valkey.rpop.call_count == 3


# ---------------------------------------------------------------------------
# fetch_jobs — skips invalid JSON
# ---------------------------------------------------------------------------


async def test_fetch_jobs_skips_invalid_json(adapter: TelegramChannelAdapter, mock_valkey: AsyncMock) -> None:
    valid_job = {"platform": "telegram", "external_id": "tg_1_2", "title": "Valid"}
    mock_valkey.rpop.side_effect = [
        "not valid json {{{",
        json.dumps(valid_job),
        None,
    ]

    result = await adapter.fetch_jobs()

    assert len(result) == 1
    assert result[0]["title"] == "Valid"


# ---------------------------------------------------------------------------
# fetch_jobs — with rate limiter
# ---------------------------------------------------------------------------


async def test_fetch_jobs_calls_rate_limiter(mock_valkey: AsyncMock) -> None:
    mock_limiter = AsyncMock()
    adapter = TelegramChannelAdapter(valkey=mock_valkey, rate_limiter=mock_limiter)

    mock_valkey.rpop.return_value = None

    await adapter.fetch_jobs()

    mock_limiter.acquire.assert_called_once_with("telegram")


# ---------------------------------------------------------------------------
# close — no-op
# ---------------------------------------------------------------------------


async def test_close_is_noop(adapter: TelegramChannelAdapter) -> None:
    await adapter.close()  # Should not raise
