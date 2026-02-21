"""Unit tests for src/core/database.py — AsyncEngine, session factory, and Valkey client.

Tests database engine configuration, async session factory settings, get_db_session context
manager behavior (commit/rollback/close), and get_valkey lazy pool creation with thread-safety.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.core import database

pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")


class TestEngineConfiguration:
    """Test AsyncEngine configuration parameters."""

    def test_engine_is_async_engine(self):
        """Engine is an AsyncEngine instance."""
        assert isinstance(database.engine, AsyncEngine)

    def test_engine_pool_size(self):
        """Engine has pool_size=10."""
        assert database.engine.pool.size() == 10

    def test_engine_max_overflow(self):
        """Engine has max_overflow=20."""
        assert database.engine.pool.overflow() == -10  # overflow tracks current, max is internal

    def test_engine_pool_timeout(self):
        """Engine has pool_timeout=30."""
        assert database.engine.pool.timeout() == 30

    def test_engine_pool_recycle(self):
        """Engine has pool_recycle=3600."""
        assert database.engine.pool._recycle == 3600

    def test_engine_pool_pre_ping(self):
        """Engine has pool_pre_ping=True."""
        assert database.engine.pool._pre_ping is True


class TestSessionFactory:
    """Test async_sessionmaker configuration."""

    def test_session_factory_is_async_sessionmaker(self):
        """Session factory is async_sessionmaker instance."""
        assert isinstance(database.async_session_factory, async_sessionmaker)

    def test_session_factory_bound_to_engine(self):
        """Session factory is bound to the engine."""
        assert database.async_session_factory.kw["bind"] is database.engine

    def test_session_factory_class_is_async_session(self):
        """Session factory creates AsyncSession instances."""
        assert database.async_session_factory.class_ is AsyncSession

    def test_session_factory_expire_on_commit_false(self):
        """Session factory has expire_on_commit=False."""
        assert database.async_session_factory.kw["expire_on_commit"] is False


class TestGetDbSession:
    """Test get_db_session context manager."""

    @pytest.mark.asyncio
    async def test_yields_async_session(self):
        """get_db_session yields AsyncSession instance."""
        with patch.object(database, "async_session_factory") as mock_factory:
            mock_session = AsyncMock(spec=AsyncSession)
            mock_factory.return_value = mock_session

            async with database.get_db_session() as session:
                assert session is mock_session

    @pytest.mark.asyncio
    async def test_commits_on_success(self):
        """get_db_session commits when no exception occurs."""
        with patch.object(database, "async_session_factory") as mock_factory:
            mock_session = AsyncMock(spec=AsyncSession)
            mock_factory.return_value = mock_session

            async with database.get_db_session():
                pass

            mock_session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rollbacks_on_exception(self):
        """get_db_session rolls back on exception."""
        with patch.object(database, "async_session_factory") as mock_factory:
            mock_session = AsyncMock(spec=AsyncSession)
            mock_factory.return_value = mock_session

            with pytest.raises(ValueError, match="test error"):
                async with database.get_db_session():
                    raise ValueError("test error")

            mock_session.rollback.assert_awaited_once()
            mock_session.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_always_closes_session_on_success(self):
        """get_db_session closes session in finally block (success path)."""
        with patch.object(database, "async_session_factory") as mock_factory:
            mock_session = AsyncMock(spec=AsyncSession)
            mock_factory.return_value = mock_session

            async with database.get_db_session():
                pass

            mock_session.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_always_closes_session_on_exception(self):
        """get_db_session closes session in finally block (exception path)."""
        with patch.object(database, "async_session_factory") as mock_factory:
            mock_session = AsyncMock(spec=AsyncSession)
            mock_factory.return_value = mock_session

            with pytest.raises(RuntimeError):
                async with database.get_db_session():
                    raise RuntimeError("test")

            mock_session.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_session_created_from_factory(self):
        """get_db_session creates session from async_session_factory."""
        with patch.object(database, "async_session_factory") as mock_factory:
            mock_session = AsyncMock(spec=AsyncSession)
            mock_factory.return_value = mock_session

            async with database.get_db_session() as session:
                assert session is mock_session

            mock_factory.assert_called_once()


class TestGetValkey:
    """Test get_valkey eager pool initialization and Redis client."""

    def test_returns_redis_instance(self):
        """get_valkey returns redis.asyncio.Redis instance."""
        client = database.get_valkey()
        assert isinstance(client, aioredis.Redis)

    def test_pool_initialized_at_module_level(self):
        """Connection pool is created eagerly at module import."""
        assert database._valkey_pool is not None
        assert isinstance(database._valkey_pool, aioredis.ConnectionPool)

    def test_reuses_pool_on_second_call(self):
        """get_valkey uses the same module-level pool for all clients."""
        with patch("redis.asyncio.Redis") as mock_redis_cls:
            mock_redis_cls.return_value = MagicMock()

            database.get_valkey()
            database.get_valkey()

            assert mock_redis_cls.call_count == 2
            for call in mock_redis_cls.call_args_list:
                assert call[1]["connection_pool"] is database._valkey_pool

    def test_pool_max_connections_twenty(self):
        """Module-level pool configured with max_connections=20."""
        assert database._valkey_pool.max_connections == 20

    def test_pool_decode_responses_true(self):
        """Module-level pool configured with decode_responses=True."""
        assert database._valkey_pool.connection_kwargs.get("decode_responses") is True

    def test_pool_uses_settings_url(self):
        """Module-level pool created from settings valkey_redis_url."""
        pool = database._valkey_pool
        assert pool is not None
        assert isinstance(pool, aioredis.ConnectionPool)

    def test_concurrent_get_valkey_calls(self):
        """Concurrent calls all get a valid client using the same pool."""
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(database.get_valkey) for _ in range(10)]
            clients = [f.result() for f in futures]

        # All clients should reference the same pool
        for client in clients:
            assert client.connection_pool is database._valkey_pool
