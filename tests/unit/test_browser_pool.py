"""Unit tests for src.browser.pool.BrowserPool.

All browser launch, context, and session operations are mocked.
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.browser.pool import BrowserPool, PoolConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_stealth_page() -> AsyncMock:
    """Create a mock StealthPage."""
    page = AsyncMock()
    page.page = AsyncMock()
    page.page.close = AsyncMock()
    return page


def _mock_session_manager(restored: bool = True) -> AsyncMock:
    """Create a mock SessionManager."""
    sm = AsyncMock()
    sm.save_session = AsyncMock()
    sm.restore_session = AsyncMock(return_value=restored)
    return sm


def _mock_stealth_browser(page: AsyncMock | None = None) -> AsyncMock:
    """Create a mock StealthBrowser."""
    if page is None:
        page = _mock_stealth_page()

    context = AsyncMock()
    context.new_page = AsyncMock(return_value=page)
    context.close = AsyncMock()

    browser = AsyncMock()
    browser.launch = AsyncMock()
    browser.close = AsyncMock()
    browser.new_context = AsyncMock(return_value=context)
    browser.is_running = True

    return browser


# ---------------------------------------------------------------------------
# Tests: acquire / release
# ---------------------------------------------------------------------------

class TestAcquireRelease:
    """Test page acquisition and release lifecycle."""

    @pytest.mark.asyncio
    async def test_acquire_creates_browser(self) -> None:
        sm = _mock_session_manager()
        pool = BrowserPool(config=PoolConfig(max_browsers=3), session_manager=sm)

        with patch("src.browser.pool.StealthBrowser") as MockBrowser, \
             patch("src.browser.pool.StealthConfig"):
            mock_browser = _mock_stealth_browser()
            MockBrowser.return_value = mock_browser

            page = await pool.acquire("upwork")

        assert page is not None
        mock_browser.launch.assert_awaited_once()
        sm.restore_session.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_release_saves_cookies(self) -> None:
        sm = _mock_session_manager()
        pool = BrowserPool(config=PoolConfig(max_browsers=3), session_manager=sm)

        with patch("src.browser.pool.StealthBrowser") as MockBrowser, \
             patch("src.browser.pool.StealthConfig"):
            MockBrowser.return_value = _mock_stealth_browser()
            page = await pool.acquire("upwork")
            await pool.release("upwork", page)

        sm.save_session.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reuse_existing_browser(self) -> None:
        sm = _mock_session_manager()
        pool = BrowserPool(config=PoolConfig(max_browsers=3), session_manager=sm)

        with patch("src.browser.pool.StealthBrowser") as MockBrowser, \
             patch("src.browser.pool.StealthConfig"):
            MockBrowser.return_value = _mock_stealth_browser()

            page1 = await pool.acquire("upwork")
            await pool.release("upwork", page1)
            page2 = await pool.acquire("upwork")

        # StealthBrowser should be created only once.
        assert MockBrowser.call_count == 1


# ---------------------------------------------------------------------------
# Tests: max_browsers limit
# ---------------------------------------------------------------------------

class TestMaxBrowsers:
    """Test that the pool respects the max_browsers cap."""

    @pytest.mark.asyncio
    async def test_evicts_oldest_when_full(self) -> None:
        sm = _mock_session_manager()
        pool = BrowserPool(config=PoolConfig(max_browsers=2), session_manager=sm)

        with patch("src.browser.pool.StealthBrowser") as MockBrowser, \
             patch("src.browser.pool.StealthConfig"):
            browsers = []
            for _ in range(3):
                b = _mock_stealth_browser()
                browsers.append(b)
            MockBrowser.side_effect = browsers

            # Acquire 2 platforms (fills pool).
            await pool.acquire("upwork")
            await pool.acquire("kwork")

            # Third acquire should evict the oldest.
            await pool.acquire("freelancer")

        # One of the first two browsers should have been closed.
        close_count = sum(1 for b in browsers[:2] if b.close.await_count > 0)
        assert close_count >= 1


# ---------------------------------------------------------------------------
# Tests: proxy rotation
# ---------------------------------------------------------------------------

class TestProxyRotation:
    """Test automatic proxy rotation after interval."""

    @pytest.mark.asyncio
    async def test_rotation_after_interval(self) -> None:
        sm = _mock_session_manager()
        pool = BrowserPool(config=PoolConfig(proxy_rotation_minutes=1), session_manager=sm)

        with patch("src.browser.pool.StealthBrowser") as MockBrowser, \
             patch("src.browser.pool.StealthConfig"), \
             patch("time.time") as mock_time:

            # First acquire at t=0.
            mock_time.return_value = 0.0
            MockBrowser.return_value = _mock_stealth_browser()
            await pool.acquire("upwork")
            await pool.release("upwork", _mock_stealth_page())

            # Second acquire at t=120s (> 60s rotation interval).
            mock_time.return_value = 120.0
            MockBrowser.return_value = _mock_stealth_browser()
            await pool.acquire("upwork")

        # Should have created a new browser (rotation).
        assert MockBrowser.call_count >= 2


# ---------------------------------------------------------------------------
# Tests: shutdown
# ---------------------------------------------------------------------------

class TestShutdown:
    """Test pool shutdown."""

    @pytest.mark.asyncio
    async def test_shutdown_closes_all(self) -> None:
        sm = _mock_session_manager()
        pool = BrowserPool(config=PoolConfig(max_browsers=3), session_manager=sm)

        with patch("src.browser.pool.StealthBrowser") as MockBrowser, \
             patch("src.browser.pool.StealthConfig"):
            browser1 = _mock_stealth_browser()
            browser2 = _mock_stealth_browser()
            MockBrowser.side_effect = [browser1, browser2]

            await pool.acquire("upwork")
            await pool.acquire("kwork")
            await pool.shutdown()

        browser1.close.assert_awaited()
        browser2.close.assert_awaited()

    @pytest.mark.asyncio
    async def test_context_manager(self) -> None:
        sm = _mock_session_manager()

        with patch("src.browser.pool.StealthBrowser") as MockBrowser, \
             patch("src.browser.pool.StealthConfig"):
            MockBrowser.return_value = _mock_stealth_browser()

            async with BrowserPool(session_manager=sm) as pool:
                await pool.acquire("upwork")

        # After exiting context, browser should be closed.
        MockBrowser.return_value.close.assert_awaited()


# ---------------------------------------------------------------------------
# Tests: proxy URL building
# ---------------------------------------------------------------------------

class TestProxyUrl:
    """Test proxy URL construction from settings."""

    def test_builds_url_with_credentials(self) -> None:
        sm = _mock_session_manager()
        pool = BrowserPool(session_manager=sm)
        with patch("src.core.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                BRIGHTDATA_USERNAME="user123",
                BRIGHTDATA_PASSWORD="pass456",
                BRIGHTDATA_HOST="brd.superproxy.io",
            )
            url = pool._build_proxy_url()

        assert url == "http://user123:pass456@brd.superproxy.io:22225"

    def test_returns_none_without_credentials(self) -> None:
        sm = _mock_session_manager()
        pool = BrowserPool(session_manager=sm)
        with patch("src.core.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(
                BRIGHTDATA_USERNAME=None,
                BRIGHTDATA_PASSWORD=None,
                BRIGHTDATA_HOST="brd.superproxy.io",
            )
            url = pool._build_proxy_url()

        assert url is None
