"""Async database engine, session factory, and Valkey client.

Uses ``asyncpg`` as the PostgreSQL driver and ``redis.asyncio`` for Valkey
(which is wire-compatible with Redis).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.core.config import get_settings

# ---------------------------------------------------------------------------
# Async SQLAlchemy engine
# ---------------------------------------------------------------------------

_settings = get_settings()

engine: AsyncEngine = create_async_engine(
    _settings.async_database_url,
    pool_size=10,
    max_overflow=20,
    pool_timeout=30,
    pool_recycle=3600,
    pool_pre_ping=True,
    echo=False,
)

async_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@asynccontextmanager
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional database session.

    Usage::

        async with get_db_session() as session:
            result = await session.execute(select(Job))
    """
    session = async_session_factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


# ---------------------------------------------------------------------------
# Valkey (Redis-compatible) client
# ---------------------------------------------------------------------------

_valkey_pool: aioredis.ConnectionPool = aioredis.ConnectionPool.from_url(
    _settings.valkey_redis_url,
    max_connections=20,
    decode_responses=True,
)


def get_valkey() -> aioredis.Redis:
    """Return a shared ``redis.asyncio`` client connected to Valkey.

    The ``redis://`` URL scheme is used because ``redis-py`` is the canonical
    Python client for Valkey (which speaks the Redis protocol).

    The connection pool is created eagerly at module level since
    ``ConnectionPool.from_url()`` is synchronous and performs no I/O.
    """
    return aioredis.Redis(connection_pool=_valkey_pool)
