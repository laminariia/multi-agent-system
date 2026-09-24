"""Async database engine, session factory, and Valkey client.

Uses ``asyncpg`` as the PostgreSQL driver and ``redis.asyncio`` for Valkey
(which is wire-compatible with Redis).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
import structlog
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.core.config import get_settings

_db_logger = structlog.get_logger(__name__)

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


# ---------------------------------------------------------------------------
# Raw asyncpg pool (for pgvector / knowledge base queries)
# ---------------------------------------------------------------------------

_asyncpg_pool: object | None = None  # asyncpg.Pool | None
_asyncpg_lock: object | None = None  # asyncio.Lock (created lazily)


async def get_asyncpg_pool() -> object | None:
    """Return a shared ``asyncpg.Pool`` for direct SQL queries.

    Creates the pool lazily on first call.  Returns ``None`` when the
    DATABASE_URL is not configured or the pool cannot be created.

    This pool is used by the knowledge base, experience store, and
    semantic cache -- modules that need raw asyncpg access for pgvector
    operations rather than SQLAlchemy ORM sessions.
    """
    import asyncio  # noqa: PLC0415

    import asyncpg as _asyncpg  # noqa: PLC0415

    global _asyncpg_pool, _asyncpg_lock  # noqa: PLW0603

    if _asyncpg_lock is None:
        _asyncpg_lock = asyncio.Lock()

    if _asyncpg_pool is not None:
        return _asyncpg_pool

    async with _asyncpg_lock:
        if _asyncpg_pool is not None:
            return _asyncpg_pool
        try:
            settings = get_settings()
            _asyncpg_pool = await _asyncpg.create_pool(
                dsn=settings.DATABASE_URL,
                min_size=1,
                max_size=5,
            )
            _db_logger.info("asyncpg_pool_created")
            return _asyncpg_pool
        except Exception:  # noqa: BLE001
            _db_logger.warning("asyncpg_pool_creation_failed", exc_info=True)
            return None
