"""Litestar dependency injection providers.

These callables are registered in the Litestar ``dependencies`` mapping
(see ``src.api.main``) so that route handlers can declare them as typed
parameters and receive the corresponding resource automatically.

Example usage in a route handler::

    @get("/example")
    async def example(db_session: AsyncSession, valkey: Redis, settings: Settings) -> dict:
        ...
"""
from __future__ import annotations

from collections.abc import AsyncGenerator

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import Settings, get_settings
from src.core.database import async_session_factory, get_valkey


async def provide_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async SQLAlchemy session scoped to a single request.

    The session is committed on success and rolled back on exception,
    then unconditionally closed.
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


async def provide_valkey() -> aioredis.Redis:
    """Return the shared ``redis.asyncio.Redis`` client connected to Valkey.

    The underlying connection pool is created once and reused across
    the application lifetime.
    """
    return get_valkey()


def provide_settings() -> Settings:
    """Return the cached application ``Settings`` singleton.

    Because ``get_settings`` is ``@lru_cache``-decorated this will always
    return the same instance after the first call.
    """
    return get_settings()
