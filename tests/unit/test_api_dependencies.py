"""Unit tests for src/api/dependencies.py — Litestar dependency injection providers.

Tests provide_db_session (commit/rollback/close behavior), provide_valkey (returns Redis
client), and provide_settings (returns cached singleton).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import provide_db_session, provide_settings, provide_valkey
from src.core.config import Settings

pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")


class TestProvideDbSession:
    """Test provide_db_session dependency provider."""

    @pytest.mark.asyncio
    async def test_yields_async_session(self):
        """provide_db_session yields AsyncSession instance."""
        with patch("src.api.dependencies.async_session_factory") as mock_factory:
            mock_session = AsyncMock(spec=AsyncSession)
            mock_factory.return_value = mock_session

            gen = provide_db_session()
            session = await gen.__anext__()

            assert session is mock_session

            # Cleanup
            try:
                await gen.__anext__()
            except StopAsyncIteration:
                pass

    @pytest.mark.asyncio
    async def test_commits_on_success(self):
        """provide_db_session commits when no exception occurs."""
        with patch("src.api.dependencies.async_session_factory") as mock_factory:
            mock_session = AsyncMock(spec=AsyncSession)
            mock_factory.return_value = mock_session

            gen = provide_db_session()
            await gen.__anext__()

            # Exhaust generator (triggers commit)
            with pytest.raises(StopAsyncIteration):
                await gen.__anext__()

            mock_session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rollbacks_on_exception(self):
        """provide_db_session rolls back on exception."""
        with patch("src.api.dependencies.async_session_factory") as mock_factory:
            mock_session = AsyncMock(spec=AsyncSession)
            mock_factory.return_value = mock_session

            gen = provide_db_session()
            await gen.__anext__()

            # Throw exception into generator
            with pytest.raises(ValueError, match="test error"):
                await gen.athrow(ValueError("test error"))

            mock_session.rollback.assert_awaited_once()
            mock_session.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_closes_session_in_finally(self):
        """provide_db_session closes session in finally block."""
        with patch("src.api.dependencies.async_session_factory") as mock_factory:
            mock_session = AsyncMock(spec=AsyncSession)
            mock_factory.return_value = mock_session

            gen = provide_db_session()
            await gen.__anext__()

            # Exhaust generator
            with pytest.raises(StopAsyncIteration):
                await gen.__anext__()

            mock_session.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_closes_session_on_exception(self):
        """provide_db_session closes session even when exception occurs."""
        with patch("src.api.dependencies.async_session_factory") as mock_factory:
            mock_session = AsyncMock(spec=AsyncSession)
            mock_factory.return_value = mock_session

            gen = provide_db_session()
            await gen.__anext__()

            # Throw exception
            with pytest.raises(RuntimeError):
                await gen.athrow(RuntimeError("test"))

            mock_session.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_session_created_from_factory(self):
        """provide_db_session creates session from async_session_factory."""
        with patch("src.api.dependencies.async_session_factory") as mock_factory:
            mock_session = AsyncMock(spec=AsyncSession)
            mock_factory.return_value = mock_session

            gen = provide_db_session()
            await gen.__anext__()

            mock_factory.assert_called_once()

            # Cleanup
            try:
                await gen.__anext__()
            except StopAsyncIteration:
                pass


class TestProvideValkey:
    """Test provide_valkey dependency provider."""

    @pytest.mark.asyncio
    async def test_returns_redis_client(self):
        """provide_valkey returns redis.asyncio.Redis instance."""
        with patch("src.api.dependencies.get_valkey") as mock_get_valkey:
            mock_client = MagicMock(spec=aioredis.Redis)
            mock_get_valkey.return_value = mock_client

            result = await provide_valkey()

            assert result is mock_client

    @pytest.mark.asyncio
    async def test_calls_get_valkey(self):
        """provide_valkey calls get_valkey from database module."""
        with patch("src.api.dependencies.get_valkey") as mock_get_valkey:
            mock_client = MagicMock(spec=aioredis.Redis)
            mock_get_valkey.return_value = mock_client

            await provide_valkey()

            mock_get_valkey.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_get_valkey_result(self):
        """provide_valkey returns exactly what get_valkey returns."""
        with patch("src.api.dependencies.get_valkey") as mock_get_valkey:
            mock_client = MagicMock(spec=aioredis.Redis)
            mock_get_valkey.return_value = mock_client

            result = await provide_valkey()

            assert result is mock_get_valkey.return_value


class TestProvideSettings:
    """Test provide_settings dependency provider."""

    def test_returns_settings_instance(self):
        """provide_settings returns Settings instance."""
        with patch("src.api.dependencies.get_settings") as mock_get_settings:
            mock_settings = MagicMock(spec=Settings)
            mock_get_settings.return_value = mock_settings

            result = provide_settings()

            assert result is mock_settings

    def test_calls_get_settings(self):
        """provide_settings calls get_settings from config module."""
        with patch("src.api.dependencies.get_settings") as mock_get_settings:
            mock_settings = MagicMock(spec=Settings)
            mock_get_settings.return_value = mock_settings

            provide_settings()

            mock_get_settings.assert_called_once()

    def test_returns_singleton_same_instance(self):
        """provide_settings returns same instance on repeated calls (via get_settings cache)."""
        with patch("src.api.dependencies.get_settings") as mock_get_settings:
            mock_settings = MagicMock(spec=Settings)
            mock_get_settings.return_value = mock_settings

            result1 = provide_settings()
            result2 = provide_settings()

            # Both calls return the same mock instance
            assert result1 is result2
            assert result1 is mock_settings

    def test_returns_get_settings_result(self):
        """provide_settings returns exactly what get_settings returns."""
        with patch("src.api.dependencies.get_settings") as mock_get_settings:
            mock_settings = MagicMock(spec=Settings)
            mock_get_settings.return_value = mock_settings

            result = provide_settings()

            assert result is mock_get_settings.return_value
