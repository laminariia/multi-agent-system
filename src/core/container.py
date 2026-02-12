"""Dependency-injection container for agent infrastructure.

Provides a singleton :class:`AgentContainer` that lazily creates and shares
long-lived resources (LLM client, heartbeat monitor, loop detector,
semantic cache, browser pool) across all agent node functions.

Usage in node functions::

    from src.core.container import get_container

    container = get_container()
    agent = SomeAgent(
        llm_client=container.llm_client,
        heartbeat=container.heartbeat,
        loop_detector=container.loop_detector,
    )

On application shutdown call :func:`teardown_container` to close HTTP
clients, browser pools, and other owned resources.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_container: AgentContainer | None = None
_lock = asyncio.Lock()


class AgentContainer:
    """Shared infrastructure for all agent nodes.

    Resources are created lazily on first access and reused across
    invocations.  Call :meth:`teardown` during application shutdown to
    release all held resources.
    """

    def __init__(self) -> None:
        self._llm_client: Any | None = None
        self._heartbeat: Any | None = None
        self._loop_detector: Any | None = None
        self._semantic_cache: Any | None = None
        self._browser_pool: Any | None = None

    # ------------------------------------------------------------------
    # Lazy accessors
    # ------------------------------------------------------------------

    @property
    def llm_client(self) -> Any:
        """Return the shared :class:`LLMClient` instance."""
        if self._llm_client is None:
            from src.core.llm_client import LLMClient  # noqa: PLC0415

            self._llm_client = LLMClient(semantic_cache=self._semantic_cache)
        return self._llm_client

    @property
    def heartbeat(self) -> Any:
        """Return the shared :class:`HeartbeatMonitor` instance."""
        if self._heartbeat is None:
            from src.core.database import get_valkey  # noqa: PLC0415
            from src.core.heartbeat import HeartbeatMonitor  # noqa: PLC0415

            self._heartbeat = HeartbeatMonitor(
                valkey=get_valkey(),
                db_pool=None,
            )
        return self._heartbeat

    @property
    def loop_detector(self) -> Any:
        """Return the shared :class:`LoopDetector` instance."""
        if self._loop_detector is None:
            from src.core.loop_detector import LoopDetector  # noqa: PLC0415

            self._loop_detector = LoopDetector(
                max_iterations=50,
                max_identical_steps=3,
            )
        return self._loop_detector

    @property
    def semantic_cache(self) -> Any | None:
        """Return the semantic cache, if initialized."""
        return self._semantic_cache

    @semantic_cache.setter
    def semantic_cache(self, value: Any) -> None:
        self._semantic_cache = value
        # Propagate to existing LLM client if already created.
        if self._llm_client is not None:
            self._llm_client._semantic_cache = value  # noqa: SLF001

    @property
    def browser_pool(self) -> Any | None:
        """Return the browser pool (lazy-created on first call).

        Returns ``None`` when proxy credentials are not configured.
        """
        if self._browser_pool is None:
            from src.core.config import get_settings  # noqa: PLC0415

            settings = get_settings()
            if settings.BRIGHTDATA_USERNAME:
                from src.browser.pool import BrowserPool, PoolConfig  # noqa: PLC0415

                self._browser_pool = BrowserPool(
                    config=PoolConfig(
                        max_browsers=settings.BROWSER_POOL_MAX,
                        proxy_rotation_minutes=settings.BROWSER_PROXY_ROTATION_MINUTES,
                    ),
                )
        return self._browser_pool

    # ------------------------------------------------------------------
    # Teardown
    # ------------------------------------------------------------------

    async def teardown(self) -> None:
        """Release all resources held by the container.

        Safe to call multiple times.
        """
        errors: list[str] = []

        # Browser pool
        if self._browser_pool is not None:
            try:
                await self._browser_pool.shutdown()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"browser_pool: {exc}")
            finally:
                self._browser_pool = None

        # LLM client (close underlying httpx sessions if applicable)
        if self._llm_client is not None:
            if hasattr(self._llm_client, "close"):
                try:
                    await self._llm_client.close()
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"llm_client: {exc}")
            self._llm_client = None

        # Heartbeat monitor (cancel background task if running)
        if self._heartbeat is not None:
            if hasattr(self._heartbeat, "stop"):
                try:
                    await self._heartbeat.stop()
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"heartbeat: {exc}")
            self._heartbeat = None

        # Loop detector (stateless, just drop reference)
        self._loop_detector = None

        # Semantic cache reference (owned by app.state, not us)
        self._semantic_cache = None

        if errors:
            logger.warning("container.teardown_errors", errors=errors)
        else:
            logger.info("container.teardown_complete")


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


def get_container() -> AgentContainer:
    """Return the module-level singleton container.

    Creates the container on first call.  Thread-safe for sync callers
    because Python's GIL protects the simple None check.
    """
    global _container  # noqa: PLW0603
    if _container is None:
        _container = AgentContainer()
    return _container


async def teardown_container() -> None:
    """Tear down the global container and reset the singleton.

    Should be called once during application shutdown.
    """
    global _container  # noqa: PLW0603
    if _container is not None:
        await _container.teardown()
        _container = None


def reset_container() -> None:
    """Reset the global container (for testing only)."""
    global _container  # noqa: PLW0603
    _container = None
