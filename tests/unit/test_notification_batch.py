"""Unit tests for src.notifications.batch.BatchCollector.

Tests batch accumulation, flush, and window-elapsed logic using a mock
Valkey client.  No real Valkey connections.
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.notifications.batch import (
    DEFAULT_BATCH_WINDOW_SECONDS,
    BatchCollector,
    _dict_to_notification,
)
from src.notifications.models import Notification, Severity

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_valkey() -> AsyncMock:
    """AsyncMock of redis.asyncio.Redis with batch-relevant methods."""
    v = AsyncMock()
    v.rpush = AsyncMock(return_value=1)
    v.llen = AsyncMock(return_value=0)
    v.get = AsyncMock(return_value=None)
    v.set = AsyncMock(return_value=True)
    v.delete = AsyncMock(return_value=1)
    v.lrange = AsyncMock(return_value=[])

    # pipeline mock
    pipe = AsyncMock()
    pipe.lrange = MagicMock(return_value=pipe)
    pipe.delete = MagicMock(return_value=pipe)
    pipe.execute = AsyncMock(return_value=[[], 1])
    v.pipeline = MagicMock(return_value=pipe)

    return v


@pytest.fixture()
def collector(mock_valkey: AsyncMock) -> BatchCollector:
    return BatchCollector(mock_valkey, window_seconds=1800, max_batch_size=200)


def _make_notification(severity: Severity = Severity.WARNING, **kwargs) -> Notification:
    defaults = {
        "title": "Test warning",
        "message": "Something needs attention",
        "source": "test",
    }
    defaults.update(kwargs)
    return Notification(severity=severity, **defaults)


# ---------------------------------------------------------------------------
# add()
# ---------------------------------------------------------------------------


class TestAdd:
    async def test_add_pushes_to_valkey(self, collector: BatchCollector, mock_valkey: AsyncMock):
        n = _make_notification()
        length = await collector.add(n)

        assert length == 1
        mock_valkey.rpush.assert_awaited_once()
        call_args = mock_valkey.rpush.call_args
        key = call_args[0][0]
        assert "warning" in key

        # Verify payload is valid JSON.
        payload = json.loads(call_args[0][1])
        assert payload["title"] == "Test warning"
        assert payload["severity"] == "warning"

    async def test_add_returns_queue_length(self, collector: BatchCollector, mock_valkey: AsyncMock):
        mock_valkey.rpush.return_value = 5
        n = _make_notification()
        length = await collector.add(n)
        assert length == 5

    async def test_add_different_severities_use_different_keys(self, collector: BatchCollector, mock_valkey: AsyncMock):
        await collector.add(_make_notification(Severity.WARNING))
        await collector.add(_make_notification(Severity.ERROR))

        calls = mock_valkey.rpush.call_args_list
        keys = [c[0][0] for c in calls]
        assert keys[0] != keys[1]
        assert "warning" in keys[0]
        assert "error" in keys[1]


# ---------------------------------------------------------------------------
# size()
# ---------------------------------------------------------------------------


class TestSize:
    async def test_size_returns_llen(self, collector: BatchCollector, mock_valkey: AsyncMock):
        mock_valkey.llen.return_value = 7
        result = await collector.size(Severity.WARNING)
        assert result == 7
        mock_valkey.llen.assert_awaited_once()

    async def test_size_uses_correct_key(self, collector: BatchCollector, mock_valkey: AsyncMock):
        await collector.size(Severity.ERROR)
        key = mock_valkey.llen.call_args[0][0]
        assert "error" in key


# ---------------------------------------------------------------------------
# is_window_elapsed()
# ---------------------------------------------------------------------------


class TestIsWindowElapsed:
    async def test_no_previous_flush_returns_true(self, collector: BatchCollector, mock_valkey: AsyncMock):
        mock_valkey.get.return_value = None
        result = await collector.is_window_elapsed(Severity.WARNING)
        assert result is True

    async def test_recent_flush_returns_false(self, collector: BatchCollector, mock_valkey: AsyncMock):
        # Flush happened just now.
        mock_valkey.get.return_value = str(time.time())
        result = await collector.is_window_elapsed(Severity.WARNING)
        assert result is False

    async def test_old_flush_returns_true(self, collector: BatchCollector, mock_valkey: AsyncMock):
        # Flush happened 2 hours ago.
        mock_valkey.get.return_value = str(time.time() - 7200)
        result = await collector.is_window_elapsed(Severity.WARNING)
        assert result is True

    async def test_invalid_timestamp_returns_true(self, collector: BatchCollector, mock_valkey: AsyncMock):
        mock_valkey.get.return_value = "not-a-number"
        result = await collector.is_window_elapsed(Severity.WARNING)
        assert result is True


# ---------------------------------------------------------------------------
# flush()
# ---------------------------------------------------------------------------


class TestFlush:
    async def test_flush_drains_queue(self, collector: BatchCollector, mock_valkey: AsyncMock):
        n = _make_notification(title="warn1")
        raw = json.dumps(n.to_dict(), default=str)

        pipe = mock_valkey.pipeline()
        pipe.execute.return_value = [[raw], 1]

        result = await collector.flush(Severity.WARNING)

        assert len(result) == 1
        assert result[0].title == "warn1"
        assert result[0].severity == Severity.WARNING

    async def test_flush_updates_timestamp(self, collector: BatchCollector, mock_valkey: AsyncMock):
        pipe = mock_valkey.pipeline()
        pipe.execute.return_value = [[], 1]

        await collector.flush(Severity.WARNING)

        # Should set the flush timestamp.
        mock_valkey.set.assert_awaited()
        call_args = mock_valkey.set.call_args
        key = call_args[0][0]
        assert "flush_ts" in key
        assert "warning" in key

    async def test_flush_empty_queue(self, collector: BatchCollector, mock_valkey: AsyncMock):
        pipe = mock_valkey.pipeline()
        pipe.execute.return_value = [[], 1]

        result = await collector.flush(Severity.WARNING)
        assert result == []

    async def test_flush_handles_invalid_json(self, collector: BatchCollector, mock_valkey: AsyncMock):
        pipe = mock_valkey.pipeline()
        pipe.execute.return_value = [["not-json", '{"severity":"warning","title":"ok","message":"m"}'], 1]

        result = await collector.flush(Severity.WARNING)

        # Should skip the bad item and parse the good one.
        assert len(result) == 1
        assert result[0].title == "ok"

    async def test_flush_multiple_items(self, collector: BatchCollector, mock_valkey: AsyncMock):
        items = []
        for i in range(5):
            n = _make_notification(title=f"item{i}")
            items.append(json.dumps(n.to_dict(), default=str))

        pipe = mock_valkey.pipeline()
        pipe.execute.return_value = [items, 1]

        result = await collector.flush(Severity.WARNING)

        assert len(result) == 5
        titles = {r.title for r in result}
        for i in range(5):
            assert f"item{i}" in titles


# ---------------------------------------------------------------------------
# flush_if_ready()
# ---------------------------------------------------------------------------


class TestFlushIfReady:
    async def test_not_elapsed_returns_empty(self, collector: BatchCollector, mock_valkey: AsyncMock):
        mock_valkey.get.return_value = str(time.time())  # Just flushed.
        result = await collector.flush_if_ready(Severity.WARNING)
        assert result == []

    async def test_elapsed_but_empty_returns_empty(self, collector: BatchCollector, mock_valkey: AsyncMock):
        mock_valkey.get.return_value = None  # Never flushed.
        mock_valkey.llen.return_value = 0  # No items.
        result = await collector.flush_if_ready(Severity.WARNING)
        assert result == []

    async def test_elapsed_with_items_flushes(self, collector: BatchCollector, mock_valkey: AsyncMock):
        mock_valkey.get.return_value = None  # Never flushed → elapsed.
        mock_valkey.llen.return_value = 2

        n = _make_notification(title="ready")
        raw = json.dumps(n.to_dict(), default=str)
        pipe = mock_valkey.pipeline()
        pipe.execute.return_value = [[raw, raw], 1]

        result = await collector.flush_if_ready(Severity.WARNING)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# _dict_to_notification()
# ---------------------------------------------------------------------------


class TestDictToNotification:
    def test_roundtrip(self):
        n = _make_notification(
            severity=Severity.ERROR,
            title="roundtrip test",
            source="test_src",
            agent_name="scout",
            thread_id="t-123",
        )
        data = n.to_dict()
        restored = _dict_to_notification(data)

        assert restored.severity == Severity.ERROR
        assert restored.title == "roundtrip test"
        assert restored.source == "test_src"
        assert restored.agent_name == "scout"
        assert restored.thread_id == "t-123"
        assert restored.id == n.id

    def test_minimal_dict(self):
        data = {"severity": "info", "title": "minimal", "message": ""}
        n = _dict_to_notification(data)
        assert n.severity == Severity.INFO
        assert n.title == "minimal"

    def test_invalid_severity_raises(self):
        data = {"severity": "unknown", "title": "bad"}
        with pytest.raises(ValueError):
            _dict_to_notification(data)

    def test_missing_title_raises(self):
        data = {"severity": "info"}
        with pytest.raises(KeyError):
            _dict_to_notification(data)


# ---------------------------------------------------------------------------
# DEFAULT_BATCH_WINDOW_SECONDS
# ---------------------------------------------------------------------------


def test_default_window_is_30_minutes():
    assert DEFAULT_BATCH_WINDOW_SECONDS == 30 * 60
    assert DEFAULT_BATCH_WINDOW_SECONDS == 1800
