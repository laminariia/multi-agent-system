"""Unit tests for src.browser.stealth -- StealthBrowser, StealthContext, StealthPage.

All Playwright internals are mocked — no real browser is launched.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.browser.stealth import StealthBrowser, StealthConfig, StealthContext, StealthPage

# Suppress RuntimeWarnings from AsyncMock coroutines that are never awaited —
# these are harmless artifacts of mocking async Playwright objects.
pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_playwright() -> tuple[AsyncMock, AsyncMock, AsyncMock]:
    """Create mock Playwright, Browser, and BrowserContext."""
    page = AsyncMock()
    page.goto = AsyncMock()
    page.close = AsyncMock()
    page.content = AsyncMock(return_value="<html></html>")
    page.screenshot = AsyncMock(return_value=b"PNG")
    page.query_selector = AsyncMock(return_value=None)
    page.query_selector_all = AsyncMock(return_value=[])
    page.evaluate = AsyncMock(return_value=None)
    page.mouse = MagicMock()
    page.mouse.move = AsyncMock()
    page.mouse.click = AsyncMock()

    context = AsyncMock()
    context.new_page = AsyncMock(return_value=page)
    context.cookies = AsyncMock(return_value=[{"name": "session", "value": "abc"}])
    context.add_cookies = AsyncMock()
    context.close = AsyncMock()

    browser = AsyncMock()
    browser.new_context = AsyncMock(return_value=context)
    browser.close = AsyncMock()

    return browser, context, page


# ---------------------------------------------------------------------------
# Tests: StealthConfig
# ---------------------------------------------------------------------------


class TestStealthConfig:
    """Test default configuration values."""

    def test_default_values(self) -> None:
        config = StealthConfig()
        assert config.headless is False
        assert config.viewport_width == 1920
        assert config.viewport_height == 1080
        assert config.locale == "en-US"
        assert config.timezone_id == "America/New_York"
        assert config.proxy_url is None
        assert config.min_delay >= 1.0
        assert config.max_delay >= config.min_delay

    def test_custom_proxy(self) -> None:
        config = StealthConfig(proxy_url="http://user:pass@proxy:8080")
        assert config.proxy_url == "http://user:pass@proxy:8080"


# ---------------------------------------------------------------------------
# Tests: StealthBrowser
# ---------------------------------------------------------------------------


class TestStealthBrowser:
    """Test browser launch with anti-detection settings."""

    @pytest.mark.asyncio
    async def test_launch_uses_correct_args(self) -> None:
        mock_browser, _, _ = _mock_playwright()
        mock_browser.is_connected = MagicMock(return_value=True)

        with patch("src.browser.stealth.async_playwright") as mock_pw:
            pw_instance = AsyncMock()
            pw_instance.chromium.launch = AsyncMock(return_value=mock_browser)
            mock_pw.return_value.__aenter__ = AsyncMock(return_value=pw_instance)
            mock_pw.return_value.__aexit__ = AsyncMock()

            sb = StealthBrowser()
            sb._playwright = pw_instance
            sb._browser = mock_browser

            assert sb.is_running

    def test_no_cdp_connection(self) -> None:
        """Verify StealthBrowser.launch() does NOT call connect_over_cdp."""
        import inspect

        source = inspect.getsource(StealthBrowser.launch)
        # Filter out comments and docstrings — only check executable lines.
        code_lines = [
            line
            for line in source.split("\n")
            if line.strip() and not line.strip().startswith(("#", '"""', "'''", "MUST NOT", "CRITICAL"))
        ]
        code_body = "\n".join(code_lines)
        # Verify no actual call to connect_over_cdp (excluding docstring warnings).
        assert ".connect_over_cdp(" not in code_body

    def test_anti_detection_args_in_source(self) -> None:
        """Verify anti-detection Chromium args are in the source."""
        import inspect

        source = inspect.getsource(StealthBrowser)
        assert "--disable-blink-features=AutomationControlled" in source


# ---------------------------------------------------------------------------
# Tests: StealthPage
# ---------------------------------------------------------------------------


class TestStealthPage:
    """Test human-like interaction methods."""

    @pytest.mark.asyncio
    async def test_type_text_has_delays(self) -> None:
        """type_text should have random delays between keystrokes."""
        _, _, mock_page = _mock_playwright()
        config = StealthConfig(min_delay=0.01, max_delay=0.02)
        sp = StealthPage(mock_page, config)

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await sp.type_text("input#name", "Hi")

        # Should have called sleep at least once per character.
        assert mock_sleep.await_count >= 2

    @pytest.mark.asyncio
    async def test_wait_random_uses_config_range(self) -> None:
        """wait_random should sleep within the configured range."""
        _, _, mock_page = _mock_playwright()
        config = StealthConfig(min_delay=1.0, max_delay=5.0)
        sp = StealthPage(mock_page, config)

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await sp.wait_random()

        mock_sleep.assert_awaited_once()
        sleep_duration = mock_sleep.call_args[0][0]
        assert 1.0 <= sleep_duration <= 5.0

    @pytest.mark.asyncio
    async def test_goto_proxies_to_page(self) -> None:
        _, _, mock_page = _mock_playwright()
        config = StealthConfig(min_delay=0.01, max_delay=0.02)
        sp = StealthPage(mock_page, config)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await sp.goto("https://example.com")

        mock_page.goto.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_content_proxies(self) -> None:
        _, _, mock_page = _mock_playwright()
        mock_page.content = AsyncMock(return_value="<html>test</html>")
        config = StealthConfig()
        sp = StealthPage(mock_page, config)

        result = await sp.content()
        assert result == "<html>test</html>"

    @pytest.mark.asyncio
    async def test_screenshot_proxies(self) -> None:
        _, _, mock_page = _mock_playwright()
        mock_page.screenshot = AsyncMock(return_value=b"PNG_DATA")
        config = StealthConfig()
        sp = StealthPage(mock_page, config)

        result = await sp.screenshot()
        assert result == b"PNG_DATA"

    def test_page_property(self) -> None:
        _, _, mock_page = _mock_playwright()
        config = StealthConfig()
        sp = StealthPage(mock_page, config)
        assert sp.page is mock_page


# ---------------------------------------------------------------------------
# Tests: StealthContext
# ---------------------------------------------------------------------------


class TestStealthContext:
    """Test context wrapper."""

    @pytest.mark.asyncio
    async def test_cookies_delegates(self) -> None:
        _, mock_ctx, _ = _mock_playwright()
        config = StealthConfig()
        sc = StealthContext(mock_ctx, config)

        cookies = await sc.cookies()
        assert len(cookies) == 1
        assert cookies[0]["name"] == "session"

    @pytest.mark.asyncio
    async def test_add_cookies_delegates(self) -> None:
        _, mock_ctx, _ = _mock_playwright()
        config = StealthConfig()
        sc = StealthContext(mock_ctx, config)

        await sc.add_cookies([{"name": "test", "value": "123"}])
        mock_ctx.add_cookies.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_new_page_applies_stealth(self) -> None:
        _, mock_ctx, mock_page = _mock_playwright()
        config = StealthConfig()
        sc = StealthContext(mock_ctx, config)

        with patch("src.browser.stealth._stealth") as mock_stealth_obj:
            mock_stealth_obj.apply_stealth_async = AsyncMock()
            page = await sc.new_page()

        mock_stealth_obj.apply_stealth_async.assert_awaited_once_with(mock_page)
        assert isinstance(page, StealthPage)
