"""Unit tests for src.core.container -- AgentContainer DI singleton."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.container import AgentContainer, get_container, reset_container, teardown_container


@pytest.fixture(autouse=True)
def _reset():
    """Ensure each test starts with a fresh container."""
    reset_container()
    yield
    reset_container()


class TestAgentContainer:
    """Tests for AgentContainer resource management."""

    def test_llm_client_created_lazily(self) -> None:
        """LLM client should be None initially and created on first access."""
        c = AgentContainer()
        assert c._llm_client is None

        with patch("src.core.llm_client.LLMClient", autospec=True) as mock_cls:
            mock_cls.return_value = MagicMock()
            client = c.llm_client
            assert client is not None
            mock_cls.assert_called_once()

    def test_llm_client_returns_same_instance(self) -> None:
        """Repeated access should return the same LLM client."""
        c = AgentContainer()
        with patch("src.core.llm_client.LLMClient", autospec=True) as mock_cls:
            mock_cls.return_value = MagicMock()
            first = c.llm_client
            second = c.llm_client
            assert first is second
            assert mock_cls.call_count == 1

    def test_heartbeat_created_lazily(self) -> None:
        """Heartbeat should be created on first access."""
        c = AgentContainer()
        assert c._heartbeat is None

        with (
            patch("src.core.heartbeat.HeartbeatMonitor", autospec=True) as mock_cls,
            patch("src.core.database.get_valkey") as mock_valkey,
        ):
            mock_valkey.return_value = MagicMock()
            mock_cls.return_value = MagicMock()
            hb = c.heartbeat
            assert hb is not None
            mock_cls.assert_called_once()

    def test_loop_detector_created_lazily(self) -> None:
        """Loop detector should be created on first access."""
        c = AgentContainer()
        assert c._loop_detector is None

        with patch("src.core.loop_detector.LoopDetector", autospec=True) as mock_cls:
            mock_cls.return_value = MagicMock()
            ld = c.loop_detector
            assert ld is not None
            mock_cls.assert_called_once_with(max_iterations=50, max_identical_steps=3)

    def test_semantic_cache_defaults_to_none(self) -> None:
        """Semantic cache should default to None."""
        c = AgentContainer()
        assert c.semantic_cache is None

    def test_semantic_cache_setter_propagates_to_llm_client(self) -> None:
        """Setting semantic_cache should update existing LLM client."""
        c = AgentContainer()
        mock_client = MagicMock()
        c._llm_client = mock_client

        mock_cache = MagicMock()
        c.semantic_cache = mock_cache

        assert c._semantic_cache is mock_cache
        assert mock_client._semantic_cache is mock_cache

    def test_browser_pool_returns_none_without_proxy(self) -> None:
        """Browser pool should be None when proxy is not configured."""
        c = AgentContainer()
        with patch("src.core.config.get_settings") as mock_settings:
            mock_settings.return_value = MagicMock(BRIGHTDATA_USERNAME="")
            assert c.browser_pool is None

    def test_browser_pool_created_with_proxy(self) -> None:
        """Browser pool should be created when proxy is configured."""
        c = AgentContainer()
        with (
            patch("src.core.config.get_settings") as mock_settings,
            patch("src.browser.pool.BrowserPool") as mock_pool_cls,
            patch("src.browser.pool.PoolConfig"),
        ):
            mock_settings.return_value = MagicMock(
                BRIGHTDATA_USERNAME="user",
                BROWSER_POOL_MAX=3,
                BROWSER_PROXY_ROTATION_MINUTES=30,
            )
            mock_pool_cls.return_value = MagicMock()
            pool = c.browser_pool
            assert pool is not None
            mock_pool_cls.assert_called_once()

    @pytest.mark.asyncio
    async def test_teardown_cleans_all_resources(self) -> None:
        """Teardown should release browser pool, LLM client, heartbeat."""
        c = AgentContainer()
        mock_pool = AsyncMock()
        mock_client = AsyncMock()
        mock_hb = AsyncMock()

        c._browser_pool = mock_pool
        c._llm_client = mock_client
        c._heartbeat = mock_hb
        c._loop_detector = MagicMock()
        c._semantic_cache = MagicMock()

        await c.teardown()

        mock_pool.shutdown.assert_awaited_once()
        mock_client.close.assert_awaited_once()
        mock_hb.stop.assert_awaited_once()
        assert c._browser_pool is None
        assert c._llm_client is None
        assert c._heartbeat is None
        assert c._loop_detector is None
        assert c._semantic_cache is None

    @pytest.mark.asyncio
    async def test_teardown_handles_errors_gracefully(self) -> None:
        """Teardown should continue even if individual resource cleanup fails."""
        c = AgentContainer()
        mock_pool = AsyncMock()
        mock_pool.shutdown.side_effect = RuntimeError("pool error")
        c._browser_pool = mock_pool

        mock_client = MagicMock(spec=[])  # No close method
        c._llm_client = mock_client

        # Should not raise
        await c.teardown()

        assert c._browser_pool is None
        assert c._llm_client is None

    @pytest.mark.asyncio
    async def test_teardown_safe_to_call_twice(self) -> None:
        """Calling teardown twice should not error."""
        c = AgentContainer()
        c._llm_client = MagicMock(spec=[])
        await c.teardown()
        await c.teardown()  # Second call is a no-op


class TestModuleLevelHelpers:
    """Tests for get_container, teardown_container, reset_container."""

    def test_get_container_returns_singleton(self) -> None:
        """get_container should return the same instance on repeated calls."""
        first = get_container()
        second = get_container()
        assert first is second

    def test_reset_container_clears_singleton(self) -> None:
        """reset_container should create a new instance on next get_container."""
        first = get_container()
        reset_container()
        second = get_container()
        assert first is not second

    @pytest.mark.asyncio
    async def test_teardown_container_resets_singleton(self) -> None:
        """teardown_container should tear down and reset the global container."""
        container = get_container()
        container._llm_client = MagicMock(spec=[])

        await teardown_container()

        new_container = get_container()
        assert new_container is not container
        assert new_container._llm_client is None

    @pytest.mark.asyncio
    async def test_teardown_container_noop_when_none(self) -> None:
        """teardown_container should be safe to call when no container exists."""
        reset_container()
        await teardown_container()  # Should not raise
