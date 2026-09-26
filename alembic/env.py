"""Alembic async migration environment.

Configures Alembic to use ``asyncpg`` via ``async_engine_from_config`` and
imports all models from ``src.core.models`` so that ``--autogenerate`` sees
every table.
"""

from __future__ import annotations

import asyncio
import sys
from logging.config import fileConfig
from pathlib import Path

# Ensure the project root is on sys.path so ``src`` is importable.
_project_root = str(Path(__file__).resolve().parents[1])
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from src.core.config import get_settings
from src.core.models import Base  # noqa: F401 - ensures all models registered

# ---------------------------------------------------------------------------
# Alembic Config object
# ---------------------------------------------------------------------------

config = context.config

# Override the sqlalchemy.url with the value from our .env / Settings
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.async_database_url)

# Set up Python logging from the alembic.ini [loggers] section
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Target metadata for autogenerate
target_metadata = Base.metadata

# DB objects that migrations create but models cannot or do not describe:
# the DiskANN index is raw SQL (pgvectorscale), telegram_notifications is a
# legacy table filled by the seed script only.
_UNMAPPED_TABLES = {"telegram_notifications"}
_UNMAPPED_INDEXES = {"idx_knowledge_embedding_diskann"}


def include_object(obj, name, type_, reflected, compare_to):  # noqa: ANN001, ANN201
    """Keep unmapped DB objects out of autogenerate and ``alembic check``."""
    if type_ == "table" and name in _UNMAPPED_TABLES:
        return False
    if type_ == "index" and (name in _UNMAPPED_INDEXES or obj.table.name in _UNMAPPED_TABLES):
        return False
    return True


# ---------------------------------------------------------------------------
# Offline mode (generates SQL scripts without a live DB connection)
# ---------------------------------------------------------------------------

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    Configures the context with just a URL and not an Engine.  Calls to
    ``context.execute()`` emit the given SQL string to the script output.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        include_object=include_object,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Online mode (connects to the database asynchronously)
# ---------------------------------------------------------------------------

def do_run_migrations(connection) -> None:  # noqa: ANN001
    """Synchronous helper called inside ``connection.run_sync``."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async engine and run migrations inside a connection."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
