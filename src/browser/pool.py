"""Browser instance pool with proxy rotation.

Manages a bounded pool of :class:`StealthBrowser` instances (one per platform),
handles cookie restoration / persistence on acquire / release, and rotates
BrightData residential proxies on a configurable schedule.

Usage::

    async with BrowserPool() as pool:
        page = await pool.acquire("freelancer")
        try:
            await page.goto("https://www.freelancer.com")
            # ... interact ...
        finally:
            await pool.release("freelancer", page)
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import structlog

from src.browser.session import SessionManager, create_session_manager
from src.browser.stealth import StealthBrowser, StealthConfig, StealthContext, StealthPage

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PoolConfig:
    """Configuration for the browser pool.

    Attributes:
        max_browsers: Maximum simultaneous browser instances.
        proxy_rotation_minutes: Rotate the proxy after this many minutes.
        session_ttl_hours: Cookie TTL passed to the :class:`SessionManager`.
    """

    max_browsers: int = 3
    proxy_rotation_minutes: int = 45
    session_ttl_hours: int = 72


# ---------------------------------------------------------------------------
# Internal bookkeeping
# ---------------------------------------------------------------------------


@dataclass
class _BrowserSlot:
    """Tracks state for a single platform browser instance."""

    browser: StealthBrowser
    context: StealthContext
    proxy_url: str | None
    last_rotation_ts: float


# ---------------------------------------------------------------------------
# BrowserPool
# ---------------------------------------------------------------------------


class BrowserPool:
    """Pool of stealth browser instances keyed by platform name.

    Parameters
    ----------
    config:
        Pool configuration.  Defaults to sensible values if omitted.
    session_manager:
        Cookie persistence manager.  If ``None`` one will be created from
        the global Valkey connection on first use.
    """

    def __init__(
        self,
        config: PoolConfig | None = None,
        session_manager: SessionManager | None = None,
    ) -> None:
        self._config = config or PoolConfig()
        self._session_manager = session_manager
        self._slots: dict[str, _BrowserSlot] = {}
        self._lock = asyncio.Lock()
        self._log = logger.bind(component="browser_pool")

    # -- context manager ---------------------------------------------------

    async def __aenter__(self) -> BrowserPool:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.shutdown()

    # -- public API --------------------------------------------------------

    async def acquire(self, platform: str) -> StealthPage:
        """Acquire a :class:`StealthPage` for *platform*.

        If a browser for the platform already exists it is reused (after
        an optional proxy rotation check).  Otherwise a new browser is
        launched, honouring the ``max_browsers`` cap.

        Cookies are restored automatically if a saved session exists.
        """
        async with self._lock:
            sm = self._get_session_manager()

            # Reuse existing slot if available.
            if platform in self._slots:
                slot = self._slots[platform]
                await self._maybe_rotate_proxy(platform)
                page = await slot.context.new_page()
                self._log.debug("page_acquired_existing", platform=platform)
                return page

            # Enforce capacity limit.
            if len(self._slots) >= self._config.max_browsers:
                # Evict the oldest slot (by last rotation timestamp).
                oldest_platform = min(
                    self._slots,
                    key=lambda p: self._slots[p].last_rotation_ts,
                )
                # Save cookies before eviction so session is not lost.
                try:
                    oldest_slot = self._slots[oldest_platform]
                    if self._session_manager and oldest_slot.context:
                        await self._session_manager.save_session(oldest_platform, oldest_slot.context)
                except Exception:
                    self._log.warning("failed_save_session_before_eviction", platform=oldest_platform)
                self._log.info("evicting_browser", platform=oldest_platform)
                await self._close_slot(oldest_platform)

            # Launch a fresh browser.
            proxy_url = self._build_proxy_url()
            stealth_config = StealthConfig(proxy_url=proxy_url)
            browser = StealthBrowser(stealth_config)
            await browser.launch()

            context = await browser.new_context()

            # Restore cookies from Valkey.
            restored = await sm.restore_session(platform, context)
            self._log.info(
                "browser_acquired_new",
                platform=platform,
                cookies_restored=restored,
                has_proxy=proxy_url is not None,
            )

            slot = _BrowserSlot(
                browser=browser,
                context=context,
                proxy_url=proxy_url,
                last_rotation_ts=time.time(),
            )
            self._slots[platform] = slot

            page = await context.new_page()
            return page

    async def release(self, platform: str, page: StealthPage) -> None:
        """Release a page back to the pool, persisting cookies."""
        async with self._lock:
            sm = self._get_session_manager()

            slot = self._slots.get(platform)
            if slot is None:
                self._log.warning("release_unknown_platform", platform=platform)
                return

            # Save cookies before releasing.
            await sm.save_session(platform, slot.context)

            # Close the individual page but keep the browser alive for reuse.
            await page.page.close()
            self._log.debug("page_released", platform=platform)

    async def release_platform(self, platform: str) -> None:
        """Release and close the entire browser slot for *platform*.

        Used by ban recovery to tear down the browser, context, and all pages
        for a platform that has been banned or blocked, freeing the slot for a
        fresh browser instance on next :meth:`acquire`.
        """
        async with self._lock:
            sm = self._get_session_manager()

            slot = self._slots.get(platform)
            if slot is None:
                self._log.warning("release_platform_not_found", platform=platform)
                return

            # Attempt to save cookies before teardown (best-effort).
            try:
                await sm.save_session(platform, slot.context)
            except Exception:  # noqa: BLE001
                self._log.debug("save_session_before_release_failed", platform=platform)

            await self._close_slot(platform)
            self._log.info("platform_released", platform=platform)

    async def shutdown(self) -> None:
        """Close all browser instances and clear the pool."""
        platforms = list(self._slots.keys())
        for platform in platforms:
            await self._close_slot(platform)
        self._log.info("pool_shutdown_complete", closed_count=len(platforms))

    # -- proxy rotation ----------------------------------------------------

    async def _maybe_rotate_proxy(self, platform: str) -> None:
        """Rotate the proxy for *platform* if the rotation interval has elapsed.

        Rotation involves closing the old browser and launching a new one with
        a fresh proxy URL, then restoring cookies into the new context.
        """
        slot = self._slots.get(platform)
        if slot is None:
            return

        elapsed_minutes = (time.time() - slot.last_rotation_ts) / 60
        if elapsed_minutes < self._config.proxy_rotation_minutes:
            return

        self._log.info(
            "rotating_proxy",
            platform=platform,
            elapsed_minutes=round(elapsed_minutes, 1),
        )

        sm = self._get_session_manager()

        # Persist current session before tearing down.
        await sm.save_session(platform, slot.context)

        # Tear down old browser.
        await slot.context.close()
        await slot.browser.close()

        # Launch a new browser with a (potentially different) proxy.
        new_proxy_url = self._build_proxy_url()
        new_config = StealthConfig(proxy_url=new_proxy_url)
        new_browser = StealthBrowser(new_config)
        await new_browser.launch()

        new_context = await new_browser.new_context()
        await sm.restore_session(platform, new_context)

        self._slots[platform] = _BrowserSlot(
            browser=new_browser,
            context=new_context,
            proxy_url=new_proxy_url,
            last_rotation_ts=time.time(),
        )
        self._log.info("proxy_rotated", platform=platform, has_proxy=new_proxy_url is not None)

    # -- proxy URL builder -------------------------------------------------

    def _build_proxy_url(self) -> str | None:
        """Build a BrightData proxy URL from application settings.

        Returns ``None`` if BrightData credentials are not configured.
        """
        from src.core.config import get_settings  # noqa: PLC0415

        settings = get_settings()

        username = settings.BRIGHTDATA_USERNAME
        password = settings.BRIGHTDATA_PASSWORD
        host = settings.BRIGHTDATA_HOST

        if not username or not password:
            return None

        # BrightData residential proxy default port
        return f"http://{username}:{password}@{host}:22225"

    # -- internal helpers --------------------------------------------------

    async def _close_slot(self, platform: str) -> None:
        """Close the browser slot for *platform* and remove it from the pool."""
        slot = self._slots.pop(platform, None)
        if slot is None:
            return
        try:
            await slot.context.close()
        except Exception:
            self._log.debug("context_close_error", platform=platform, exc_info=True)
        try:
            await slot.browser.close()
        except Exception:
            self._log.debug("browser_close_error", platform=platform, exc_info=True)
        self._log.debug("slot_closed", platform=platform)

    def _get_session_manager(self) -> SessionManager:
        """Return (lazily creating) the session manager."""
        if self._session_manager is None:
            self._session_manager = create_session_manager(
                ttl_hours=self._config.session_ttl_hours,
            )
        return self._session_manager
