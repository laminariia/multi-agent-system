"""Anti-detection Playwright wrapper with human-like interaction patterns.

Provides ``StealthBrowser``, ``StealthContext``, and ``StealthPage`` which wrap
the standard Playwright objects with:

* Anti-detection launch arguments (no CDP, no ``webdriver`` flag).
* ``playwright-stealth`` patches applied to every new page.
* Human-like mouse movement (bezier curves), keystroke timing, and random delays.
* Proxy support via BrightData residential proxies.

CRITICAL:  ``connect_over_cdp()`` is NEVER used -- it exposes automation signals
that are trivially detected by anti-bot systems.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Any

import structlog
from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)
from playwright_stealth import Stealth

# Module-level stealth instance (reused across all pages).
_stealth = Stealth()

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Default user-agent (realistic Windows Chrome)
# ---------------------------------------------------------------------------
_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StealthConfig:
    """Immutable configuration for a stealth browser instance.

    Attributes:
        headless: Run in headless mode.  ``False`` recommended for lower
            detection rates on platforms with advanced bot protection.
        viewport_width: Browser viewport width in pixels.
        viewport_height: Browser viewport height in pixels.
        locale: Browser locale string (BCP-47).
        timezone_id: IANA timezone identifier.
        proxy_url: Optional proxy in ``http://user:pass@host:port`` format.
        min_delay: Minimum random delay (seconds) between page interactions.
        max_delay: Maximum random delay (seconds) between page interactions.
    """

    headless: bool = False
    viewport_width: int = 1920
    viewport_height: int = 1080
    locale: str = "en-US"
    timezone_id: str = "America/New_York"
    proxy_url: str | None = None
    min_delay: float = 1.0
    max_delay: float = 5.0


# ---------------------------------------------------------------------------
# StealthPage -- human-like interaction proxy over Playwright Page
# ---------------------------------------------------------------------------


class StealthPage:
    """Thin wrapper around :class:`playwright.async_api.Page` that adds
    human-like delays, mouse movement, and keystroke timing.

    Access the raw ``Page`` via the :attr:`page` property when you need
    Playwright primitives directly (e.g. ``page.evaluate``).
    """

    def __init__(self, page: Page, config: StealthConfig) -> None:
        self._page = page
        self._config = config
        self._log = logger.bind(component="stealth_page")

    # -- raw access --------------------------------------------------------

    @property
    def page(self) -> Page:
        """Return the underlying Playwright ``Page`` object."""
        return self._page

    # -- navigation --------------------------------------------------------

    async def goto(self, url: str, **kwargs: Any) -> None:
        """Navigate to *url*, then pause for a random human-like delay."""
        self._log.debug("goto", url=url)
        kwargs.setdefault("wait_until", "domcontentloaded")
        await self._page.goto(url, **kwargs)
        await self.wait_random()

    # -- clicking ----------------------------------------------------------

    async def click(self, selector: str) -> None:
        """Move the mouse along a bezier curve to *selector*, then click."""
        element = await self._page.wait_for_selector(selector, state="visible")
        if element is None:
            self._log.warning("click_element_not_found", selector=selector)
            return

        bounding_box = await element.bounding_box()
        if bounding_box is None:
            # Fallback: plain click if bounding box unavailable.
            await element.click()
            await self.wait_random()
            return

        target_x = bounding_box["x"] + bounding_box["width"] / 2
        target_y = bounding_box["y"] + bounding_box["height"] / 2

        # Move via bezier curve
        await self._bezier_move(target_x, target_y)
        await self._page.mouse.click(target_x, target_y)
        await self.wait_random()

    # -- typing ------------------------------------------------------------

    async def type_text(self, selector: str, text: str) -> None:
        """Type *text* into *selector* with random per-keystroke delay (50-150 ms)."""
        await self._page.wait_for_selector(selector, state="visible")
        await self._page.focus(selector)
        for char in text:
            await self._page.keyboard.type(char)
            delay_ms = random.uniform(50, 150) / 1000  # noqa: S311
            await asyncio.sleep(delay_ms)
        await self.wait_random(min_s=0.3, max_s=0.8)

    # -- scrolling ---------------------------------------------------------

    async def scroll_to(self, selector: str) -> None:
        """Smooth-scroll the viewport until *selector* is visible."""
        self._log.debug("scroll_to", selector=selector)
        await self._page.evaluate(
            """(sel) => {
                const el = document.querySelector(sel);
                if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }""",
            selector,
        )
        await self.wait_random(min_s=0.5, max_s=1.5)

    # -- random delay ------------------------------------------------------

    async def wait_random(
        self,
        min_s: float | None = None,
        max_s: float | None = None,
    ) -> None:
        """Sleep for a random duration between *min_s* and *max_s* seconds."""
        lo = min_s if min_s is not None else self._config.min_delay
        hi = max_s if max_s is not None else self._config.max_delay
        duration = random.uniform(lo, hi)  # noqa: S311
        await asyncio.sleep(duration)

    # -- proxy methods (pass-through) --------------------------------------

    async def query_selector(self, selector: str) -> Any:
        """Proxy for ``page.query_selector``."""
        return await self._page.query_selector(selector)

    async def query_selector_all(self, selector: str) -> list[Any]:
        """Proxy for ``page.query_selector_all``."""
        return await self._page.query_selector_all(selector)

    async def content(self) -> str:
        """Return the full HTML content of the page."""
        return await self._page.content()

    async def screenshot(self, **kwargs: Any) -> bytes:
        """Take a screenshot and return it as PNG bytes."""
        return await self._page.screenshot(**kwargs)

    # -- internal helpers --------------------------------------------------

    async def _bezier_move(self, target_x: float, target_y: float) -> None:
        """Move the mouse from its current position to *(target_x, target_y)*
        along a randomised cubic bezier curve with 15-25 intermediate steps.
        """
        # Current mouse position -- default to a random viewport point.
        viewport = self._page.viewport_size or {
            "width": self._config.viewport_width,
            "height": self._config.viewport_height,
        }
        start_x = random.uniform(0, viewport["width"] * 0.8)  # noqa: S311
        start_y = random.uniform(0, viewport["height"] * 0.8)  # noqa: S311

        # Two random control points for the cubic bezier
        cp1_x = start_x + (target_x - start_x) * random.uniform(0.2, 0.5)  # noqa: S311
        cp1_y = start_y + random.uniform(-100, 100)  # noqa: S311
        cp2_x = start_x + (target_x - start_x) * random.uniform(0.5, 0.8)  # noqa: S311
        cp2_y = target_y + random.uniform(-100, 100)  # noqa: S311

        steps = random.randint(15, 25)  # noqa: S311
        for i in range(steps + 1):
            t = i / steps
            inv_t = 1 - t
            # Cubic bezier formula: B(t) = (1-t)^3*P0 + 3(1-t)^2*t*P1 + 3(1-t)*t^2*P2 + t^3*P3
            x = (
                inv_t ** 3 * start_x
                + 3 * inv_t ** 2 * t * cp1_x
                + 3 * inv_t * t ** 2 * cp2_x
                + t ** 3 * target_x
            )
            y = (
                inv_t ** 3 * start_y
                + 3 * inv_t ** 2 * t * cp1_y
                + 3 * inv_t * t ** 2 * cp2_y
                + t ** 3 * target_y
            )
            await self._page.mouse.move(x, y)
            await asyncio.sleep(random.uniform(0.005, 0.02))  # noqa: S311


# ---------------------------------------------------------------------------
# StealthContext -- wraps BrowserContext with stealth page creation
# ---------------------------------------------------------------------------


class StealthContext:
    """Wraps a Playwright ``BrowserContext``, applying stealth patches to every
    new page created through :meth:`new_page`.
    """

    def __init__(self, context: BrowserContext, config: StealthConfig) -> None:
        self._context = context
        self._config = config
        self._log = logger.bind(component="stealth_context")

    async def new_page(self) -> StealthPage:
        """Create a new page with ``playwright-stealth`` patches applied."""
        page = await self._context.new_page()
        await _stealth.apply_stealth_async(page)
        self._log.debug("stealth_page_created")
        return StealthPage(page, self._config)

    async def cookies(self) -> list[dict[str, Any]]:
        """Return all cookies for the current context."""
        return await self._context.cookies()

    async def add_cookies(self, cookies: list[dict[str, Any]]) -> None:
        """Add *cookies* to the current context."""
        await self._context.add_cookies(cookies)
        self._log.debug("cookies_added", count=len(cookies))

    async def close(self) -> None:
        """Close the browser context."""
        await self._context.close()
        self._log.debug("context_closed")


# ---------------------------------------------------------------------------
# StealthBrowser -- anti-detection Chromium launcher
# ---------------------------------------------------------------------------


class StealthBrowser:
    """Anti-detection Playwright Chromium launcher.

    CRITICAL: this class deliberately avoids ``connect_over_cdp()`` because
    CDP connections are trivially fingerprinted by anti-bot systems.

    Usage::

        browser = StealthBrowser(StealthConfig(headless=False))
        await browser.launch()
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto("https://example.com")
        await browser.close()
    """

    def __init__(self, config: StealthConfig | None = None) -> None:
        self._config = config or StealthConfig()
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._log = logger.bind(component="stealth_browser")

    # -- lifecycle ---------------------------------------------------------

    async def launch(self) -> None:
        """Start the Playwright runtime and launch Chromium with anti-detection args.

        MUST NOT use ``connect_over_cdp`` -- this is critical for anti-detection.
        """
        if self._browser is not None:
            self._log.warning("browser_already_running")
            return

        self._playwright = await async_playwright().start()

        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
        ]

        proxy_settings: dict[str, str] | None = None
        if self._config.proxy_url:
            proxy_settings = {"server": self._config.proxy_url}
            self._log.info("proxy_configured", proxy_host=self._config.proxy_url.split("@")[-1])

        self._browser = await self._playwright.chromium.launch(
            headless=self._config.headless,
            args=launch_args,
            proxy=proxy_settings,
        )
        self._log.info(
            "browser_launched",
            headless=self._config.headless,
            has_proxy=proxy_settings is not None,
        )

    async def close(self) -> None:
        """Shut down the browser and Playwright runtime."""
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
        self._log.info("browser_closed")

    # -- properties --------------------------------------------------------

    @property
    def is_running(self) -> bool:
        """Return ``True`` if the browser process is alive."""
        return self._browser is not None and self._browser.is_connected()

    # -- context creation --------------------------------------------------

    async def new_context(self, **kwargs: Any) -> StealthContext:
        """Create a new ``StealthContext`` with viewport, locale, timezone, and
        user-agent pre-configured.

        Extra *kwargs* are forwarded to ``browser.new_context()``.
        """
        if self._browser is None:
            raise RuntimeError("Browser not launched.  Call ``await browser.launch()`` first.")

        context_kwargs: dict[str, Any] = {
            "viewport": {
                "width": self._config.viewport_width,
                "height": self._config.viewport_height,
            },
            "locale": self._config.locale,
            "timezone_id": self._config.timezone_id,
            "user_agent": _DEFAULT_USER_AGENT,
        }
        context_kwargs.update(kwargs)

        raw_context = await self._browser.new_context(**context_kwargs)
        self._log.debug(
            "context_created",
            viewport=f"{self._config.viewport_width}x{self._config.viewport_height}",
            locale=self._config.locale,
        )
        return StealthContext(raw_context, self._config)
