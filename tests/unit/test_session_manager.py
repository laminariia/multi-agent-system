"""Unit tests for src.browser.session.SessionManager.

Valkey interactions are mocked.
"""
from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock

import pytest

from src.browser.session import SessionManager

# Suppress RuntimeWarnings from AsyncMock coroutines that are never awaited.
pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_valkey(**overrides: object) -> AsyncMock:
    """Create a mock Valkey (redis.asyncio) client."""
    v = AsyncMock()
    v.set = AsyncMock()
    v.get = AsyncMock(return_value=None)
    v.delete = AsyncMock()
    for k, val in overrides.items():
        setattr(v, k, val)
    return v


def _mock_context(cookies: list[dict] | None = None) -> AsyncMock:
    """Create a mock StealthContext for save/restore operations."""
    ctx = AsyncMock()
    ctx.cookies = AsyncMock(return_value=cookies or [])
    ctx.add_cookies = AsyncMock()
    return ctx


# ---------------------------------------------------------------------------
# Tests: save_session
# ---------------------------------------------------------------------------

class TestSaveSession:
    """Test cookie saving to Valkey."""

    @pytest.mark.asyncio
    async def test_saves_cookies_as_json(self) -> None:
        valkey = _mock_valkey()
        mgr = SessionManager(valkey=valkey, ttl_hours=72)
        cookies = [{"name": "session", "value": "abc123", "domain": ".example.com"}]
        ctx = _mock_context(cookies=cookies)

        await mgr.save_session("upwork", ctx)

        # Check that cookies were saved to the right key.
        valkey.set.assert_any_await(
            "browser:session:upwork:cookies",
            json.dumps(cookies),
            ex=72 * 3600,
        )

    @pytest.mark.asyncio
    async def test_saves_metadata(self) -> None:
        valkey = _mock_valkey()
        mgr = SessionManager(valkey=valkey, ttl_hours=48)
        ctx = _mock_context(cookies=[{"name": "a", "value": "b"}])

        await mgr.save_session("kwork", ctx)

        # Metadata should also be saved.
        meta_calls = [c for c in valkey.set.await_args_list if "meta" in str(c)]
        assert len(meta_calls) >= 1


# ---------------------------------------------------------------------------
# Tests: restore_session
# ---------------------------------------------------------------------------

class TestRestoreSession:
    """Test cookie restoration from Valkey."""

    @pytest.mark.asyncio
    async def test_restores_cookies_when_found(self) -> None:
        cookies = [{"name": "sid", "value": "xyz"}]
        valkey = _mock_valkey()
        valkey.get = AsyncMock(return_value=json.dumps(cookies))
        mgr = SessionManager(valkey=valkey)
        ctx = _mock_context()

        result = await mgr.restore_session("upwork", ctx)

        assert result is True
        ctx.add_cookies.assert_awaited_once_with(cookies)

    @pytest.mark.asyncio
    async def test_returns_false_when_not_found(self) -> None:
        valkey = _mock_valkey()
        valkey.get = AsyncMock(return_value=None)
        mgr = SessionManager(valkey=valkey)
        ctx = _mock_context()

        result = await mgr.restore_session("upwork", ctx)

        assert result is False
        ctx.add_cookies.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handles_corrupted_json(self) -> None:
        valkey = _mock_valkey()
        valkey.get = AsyncMock(return_value="not-valid-json{{{")
        mgr = SessionManager(valkey=valkey)
        ctx = _mock_context()

        result = await mgr.restore_session("upwork", ctx)

        assert result is False


# ---------------------------------------------------------------------------
# Tests: clear_session
# ---------------------------------------------------------------------------

class TestClearSession:
    """Test session clearing."""

    @pytest.mark.asyncio
    async def test_deletes_both_keys(self) -> None:
        valkey = _mock_valkey()
        mgr = SessionManager(valkey=valkey)

        await mgr.clear_session("kwork")

        valkey.delete.assert_any_await("browser:session:kwork:cookies")
        valkey.delete.assert_any_await("browser:session:kwork:meta")


# ---------------------------------------------------------------------------
# Tests: TTL
# ---------------------------------------------------------------------------

class TestTTL:
    """Test that TTL is applied correctly."""

    @pytest.mark.asyncio
    async def test_default_ttl_72_hours(self) -> None:
        valkey = _mock_valkey()
        mgr = SessionManager(valkey=valkey)  # Default 72h
        ctx = _mock_context(cookies=[{"name": "a", "value": "b"}])

        await mgr.save_session("test", ctx)

        call_args = valkey.set.call_args_list[0]
        assert call_args.kwargs.get("ex") == 72 * 3600 or call_args[1].get("ex") == 72 * 3600

    @pytest.mark.asyncio
    async def test_custom_ttl(self) -> None:
        valkey = _mock_valkey()
        mgr = SessionManager(valkey=valkey, ttl_hours=24)
        ctx = _mock_context(cookies=[{"name": "a", "value": "b"}])

        await mgr.save_session("test", ctx)

        call_args = valkey.set.call_args_list[0]
        assert call_args.kwargs.get("ex") == 24 * 3600 or call_args[1].get("ex") == 24 * 3600


# ---------------------------------------------------------------------------
# Tests: get_session_meta
# ---------------------------------------------------------------------------

class TestSessionMeta:
    """Test metadata retrieval."""

    @pytest.mark.asyncio
    async def test_returns_meta_when_found(self) -> None:
        meta = {"last_saved": time.time(), "cookie_count": 5, "platform": "upwork"}
        valkey = _mock_valkey()
        valkey.get = AsyncMock(return_value=json.dumps(meta))
        mgr = SessionManager(valkey=valkey)

        result = await mgr.get_session_meta("upwork")
        assert result is not None
        assert result["cookie_count"] == 5

    @pytest.mark.asyncio
    async def test_returns_none_when_missing(self) -> None:
        valkey = _mock_valkey()
        valkey.get = AsyncMock(return_value=None)
        mgr = SessionManager(valkey=valkey)

        result = await mgr.get_session_meta("upwork")
        assert result is None
