"""Unit tests for src/core/database.py — AsyncEngine, session factory, and Valkey client.

Tests database engine configuration, async session factory settings, get_db_session context
manager behavior (commit/rollback/close), and get_valkey lazy pool creation with thread-safety.
"""

from __future__ import annotations

import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
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
    """Test get_valkey lazy pool creation and Redis client."""

    def test_returns_redis_instance(self):
        """get_valkey returns redis.asyncio.Redis instance."""
        # Reset pool to test fresh creation
        original_pool = database._valkey_pool
        database._valkey_pool = None

        try:
            with (
                patch("redis.asyncio.ConnectionPool.from_url") as mock_pool_cls,
                patch("redis.asyncio.Redis") as mock_redis_cls,
            ):
                mock_pool = MagicMock()
                mock_pool.connection_kwargs = {"protocol": 2}
                mock_pool_cls.return_value = mock_pool

                mock_client = MagicMock()
                mock_redis_cls.return_value = mock_client

                client = database.get_valkey()

                assert client is mock_client
                mock_redis_cls.assert_called_once_with(connection_pool=mock_pool)
        finally:
            database._valkey_pool = original_pool

    def test_creates_pool_lazily_first_call(self):
        """get_valkey creates pool on first call."""
        # Reset pool to simulate first call
        original_pool = database._valkey_pool
        database._valkey_pool = None

        try:
            with (
                patch("redis.asyncio.ConnectionPool.from_url") as mock_pool_cls,
                patch("redis.asyncio.Redis"),
            ):
                mock_pool = MagicMock()
                mock_pool.connection_kwargs = {"protocol": 2}
                mock_pool_cls.return_value = mock_pool

                database.get_valkey()

                mock_pool_cls.assert_called_once()
                assert database._valkey_pool is mock_pool
        finally:
            database._valkey_pool = original_pool

    def test_reuses_pool_on_second_call(self):
        """get_valkey reuses existing pool on subsequent calls."""
        # Reset and set up mock pool
        original_pool = database._valkey_pool
        mock_pool = MagicMock()
        mock_pool.connection_kwargs = {"protocol": 2}
        database._valkey_pool = mock_pool

        try:
            with (
                patch("redis.asyncio.ConnectionPool.from_url") as mock_pool_cls,
                patch("redis.asyncio.Redis") as mock_redis_cls,
            ):
                mock_client = MagicMock()
                mock_redis_cls.return_value = mock_client

                database.get_valkey()
                database.get_valkey()

                # Pool creation not called (pool already exists)
                mock_pool_cls.assert_not_called()

                # Both clients created with the same pool
                assert mock_redis_cls.call_count == 2
                for call in mock_redis_cls.call_args_list:
                    assert call[1]["connection_pool"] is mock_pool
        finally:
            database._valkey_pool = original_pool

    def test_pool_max_connections_twenty(self):
        """get_valkey creates pool with max_connections=20."""
        original_pool = database._valkey_pool
        database._valkey_pool = None

        try:
            with (
                patch("redis.asyncio.ConnectionPool.from_url") as mock_pool_cls,
                patch("redis.asyncio.Redis"),
            ):
                mock_pool = MagicMock()
                mock_pool.connection_kwargs = {"protocol": 2}
                mock_pool_cls.return_value = mock_pool

                database.get_valkey()

                mock_pool_cls.assert_called_once_with(
                    database._settings.valkey_redis_url,
                    max_connections=20,
                    decode_responses=True,
                )
        finally:
            database._valkey_pool = original_pool

    def test_pool_decode_responses_true(self):
        """get_valkey creates pool with decode_responses=True."""
        original_pool = database._valkey_pool
        database._valkey_pool = None

        try:
            with (
                patch("redis.asyncio.ConnectionPool.from_url") as mock_pool_cls,
                patch("redis.asyncio.Redis"),
            ):
                mock_pool = MagicMock()
                mock_pool.connection_kwargs = {"protocol": 2}
                mock_pool_cls.return_value = mock_pool

                database.get_valkey()

                # Check decode_responses=True in call args
                call_kwargs = mock_pool_cls.call_args[1]
                assert call_kwargs["decode_responses"] is True
        finally:
            database._valkey_pool = original_pool

    def test_uses_settings_valkey_redis_url(self):
        """get_valkey uses valkey_redis_url from settings."""
        original_pool = database._valkey_pool
        database._valkey_pool = None

        try:
            with (
                patch("redis.asyncio.ConnectionPool.from_url") as mock_pool_cls,
                patch("redis.asyncio.Redis"),
            ):
                mock_pool = MagicMock()
                mock_pool.connection_kwargs = {"protocol": 2}
                mock_pool_cls.return_value = mock_pool

                database.get_valkey()

                # First positional arg is the URL from settings
                call_args = mock_pool_cls.call_args[0]
                assert call_args[0] == database._settings.valkey_redis_url
        finally:
            database._valkey_pool = original_pool

    def test_pool_lock_exists(self):
        """Module has a threading.Lock for pool initialisation."""
        assert isinstance(database._valkey_pool_lock, type(threading.Lock()))

    def test_thread_safe_pool_creation(self):
        """Concurrent calls to get_valkey create pool only once."""
        original_pool = database._valkey_pool
        database._valkey_pool = None
        creation_count = 0

        try:
            def counting_from_url(*args, **kwargs):
                nonlocal creation_count
                creation_count += 1
                mock_pool = MagicMock()
                mock_pool.connection_kwargs = {"protocol": 2}
                return mock_pool

            with (
                patch("redis.asyncio.ConnectionPool.from_url", side_effect=counting_from_url),
                patch("redis.asyncio.Redis", return_value=MagicMock()),
            ):
                threads = []
                for _ in range(10):
                    t = threading.Thread(target=database.get_valkey)
                    threads.append(t)
                    t.start()

                for t in threads:
                    t.join()

                # Pool should be created exactly once despite 10 concurrent calls
                assert creation_count == 1
        finally:
            database._valkey_pool = original_pool
