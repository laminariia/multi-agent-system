"""Unit tests for Experience Store (src/knowledge/experience_store.py).

Tests cover: save experience, retrieve similar, record outcome,
get by category, delete, edge cases, and constants.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from src.knowledge.experience_store import (
    EXPERIENCE_CATEGORIES,
    EXPERIENCE_TYPE,
    ExperienceResult,
    ExperienceStore,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_pool_and_conn() -> tuple[AsyncMock, AsyncMock]:
    """Create a mock asyncpg pool + connection.

    Returns (pool, conn) where pool.acquire() is an async context manager
    yielding conn.
    """
    conn = AsyncMock()
    pool = AsyncMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    return pool, conn


def _mock_embedding_service() -> AsyncMock:
    """Create a mock embedding service returning a fixed vector."""
    svc = AsyncMock()
    svc.embed.return_value = [0.1] * 3072
    return svc


def _store(
    pool: AsyncMock | None = None,
    embed: AsyncMock | None = None,
) -> tuple[ExperienceStore, AsyncMock]:
    """Create an ExperienceStore with mocked dependencies. Returns (store, conn)."""
    if pool is None:
        pool, conn = _mock_pool_and_conn()
    else:
        conn = AsyncMock()
    return (
        ExperienceStore(
            embedding_service=embed or _mock_embedding_service(),
            db_pool=pool,
        ),
        conn,
    )


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestExperienceStoreConstants:
    """Verify experience store constants match spec."""

    def test_experience_type(self):
        assert EXPERIENCE_TYPE == "experience"

    def test_categories_defined(self):
        assert "bid" in EXPERIENCE_CATEGORIES
        assert "code" in EXPERIENCE_CATEGORIES
        assert "negotiation" in EXPERIENCE_CATEGORIES
        assert "estimation" in EXPERIENCE_CATEGORIES
        assert "client_feedback" in EXPERIENCE_CATEGORIES


# ---------------------------------------------------------------------------
# save_experience
# ---------------------------------------------------------------------------


class TestSaveExperience:
    """Save project experiences to knowledge_base with type='experience'."""

    async def test_saves_with_embedding(self):
        pool, conn = _mock_pool_and_conn()
        embed = _mock_embedding_service()
        conn.fetchval.return_value = uuid.uuid4()

        store = ExperienceStore(embedding_service=embed, db_pool=pool)
        result_id = await store.save_experience(
            category="bid",
            title="Successful React bid",
            content="Built a React SPA for e-commerce...",
        )

        assert result_id is not None
        embed.embed.assert_called_once()
        conn.fetchval.assert_called_once()

    async def test_saves_with_success_score(self):
        pool, conn = _mock_pool_and_conn()
        embed = _mock_embedding_service()
        conn.fetchval.return_value = uuid.uuid4()

        store = ExperienceStore(embedding_service=embed, db_pool=pool)
        await store.save_experience(
            category="estimation",
            title="Timeline accuracy",
            content="Estimated 5 days, actual 4 days",
            success_score=0.85,
        )

        call_args = conn.fetchval.call_args
        assert any(arg == 0.85 or (isinstance(arg, Decimal) and float(arg) == 0.85) for arg in call_args[0][1:])

    async def test_clamps_success_score(self):
        pool, conn = _mock_pool_and_conn()
        embed = _mock_embedding_service()
        conn.fetchval.return_value = uuid.uuid4()

        store = ExperienceStore(embedding_service=embed, db_pool=pool)
        await store.save_experience(
            category="bid",
            title="Test",
            content="Content here",
            success_score=1.5,
        )

        call_args = conn.fetchval.call_args
        assert any(arg == 1.0 or (isinstance(arg, Decimal) and float(arg) == 1.0) for arg in call_args[0][1:])

    async def test_rejects_empty_content(self):
        store, _ = _store()
        with pytest.raises(ValueError, match="content"):
            await store.save_experience(
                category="bid",
                title="Empty",
                content="",
            )

    async def test_rejects_empty_title(self):
        store, _ = _store()
        with pytest.raises(ValueError, match="title"):
            await store.save_experience(
                category="bid",
                title="",
                content="Some content",
            )


# ---------------------------------------------------------------------------
# retrieve_similar
# ---------------------------------------------------------------------------


class TestRetrieveSimilar:
    """Vector similarity search for past experiences."""

    async def test_returns_results(self):
        pool, conn = _mock_pool_and_conn()
        embed = _mock_embedding_service()
        conn.fetch.return_value = [
            {
                "id": str(uuid.uuid4()),
                "category": "bid",
                "title": "React SPA bid",
                "content": "Built a React SPA...",
                "similarity": 0.92,
                "success_rate": Decimal("0.85"),
                "usage_count": 5,
            },
        ]

        store = ExperienceStore(embedding_service=embed, db_pool=pool)
        results = await store.retrieve_similar("React web application")

        assert len(results) == 1
        assert isinstance(results[0], ExperienceResult)
        assert results[0].title == "React SPA bid"
        embed.embed.assert_called_once_with("React web application")

    async def test_filters_by_category(self):
        pool, conn = _mock_pool_and_conn()
        embed = _mock_embedding_service()
        conn.fetch.return_value = []

        store = ExperienceStore(embedding_service=embed, db_pool=pool)
        await store.retrieve_similar("test query", category="code")

        call_sql = conn.fetch.call_args[0][0]
        assert "category" in call_sql.lower()

    async def test_returns_empty_list_when_no_matches(self):
        pool, conn = _mock_pool_and_conn()
        embed = _mock_embedding_service()
        conn.fetch.return_value = []

        store = ExperienceStore(embedding_service=embed, db_pool=pool)
        results = await store.retrieve_similar("completely irrelevant query")

        assert results == []

    async def test_respects_top_k(self):
        pool, conn = _mock_pool_and_conn()
        embed = _mock_embedding_service()
        conn.fetch.return_value = []

        store = ExperienceStore(embedding_service=embed, db_pool=pool)
        await store.retrieve_similar("query", top_k=10)

        call_args = conn.fetch.call_args[0]
        assert 10 in call_args[1:]


# ---------------------------------------------------------------------------
# record_outcome
# ---------------------------------------------------------------------------


class TestRecordOutcome:
    """Update success_rate based on outcome feedback."""

    async def test_updates_success_rate(self):
        pool, conn = _mock_pool_and_conn()
        conn.execute.return_value = "UPDATE 1"

        store = ExperienceStore(embedding_service=_mock_embedding_service(), db_pool=pool)
        await store.record_outcome(str(uuid.uuid4()), success=True)

        conn.execute.assert_called_once()

    async def test_failure_outcome(self):
        pool, conn = _mock_pool_and_conn()
        conn.execute.return_value = "UPDATE 1"

        store = ExperienceStore(embedding_service=_mock_embedding_service(), db_pool=pool)
        await store.record_outcome(str(uuid.uuid4()), success=False)

        conn.execute.assert_called_once()


# ---------------------------------------------------------------------------
# get_by_category
# ---------------------------------------------------------------------------


class TestGetByCategory:
    """Fetch experiences by category."""

    async def test_returns_results_for_category(self):
        pool, conn = _mock_pool_and_conn()
        conn.fetch.return_value = [
            {
                "id": str(uuid.uuid4()),
                "category": "bid",
                "title": "Bid pattern",
                "content": "A successful bidding approach...",
                "similarity": None,
                "success_rate": Decimal("0.90"),
                "usage_count": 3,
            },
        ]

        store = ExperienceStore(embedding_service=_mock_embedding_service(), db_pool=pool)
        results = await store.get_by_category("bid")

        assert len(results) == 1
        assert results[0].category == "bid"

    async def test_returns_empty_for_unknown_category(self):
        pool, conn = _mock_pool_and_conn()
        conn.fetch.return_value = []

        store = ExperienceStore(embedding_service=_mock_embedding_service(), db_pool=pool)
        results = await store.get_by_category("nonexistent")

        assert results == []

    async def test_respects_limit(self):
        pool, conn = _mock_pool_and_conn()
        conn.fetch.return_value = []

        store = ExperienceStore(embedding_service=_mock_embedding_service(), db_pool=pool)
        await store.get_by_category("bid", limit=20)

        call_args = conn.fetch.call_args[0]
        assert 20 in call_args[1:]


# ---------------------------------------------------------------------------
# delete_experience
# ---------------------------------------------------------------------------


class TestDeleteExperience:
    """Delete an experience entry."""

    async def test_deletes_existing(self):
        pool, conn = _mock_pool_and_conn()
        conn.execute.return_value = "DELETE 1"

        store = ExperienceStore(embedding_service=_mock_embedding_service(), db_pool=pool)
        result = await store.delete_experience(str(uuid.uuid4()))

        assert result is True
        conn.execute.assert_called_once()

    async def test_returns_false_for_missing(self):
        pool, conn = _mock_pool_and_conn()
        conn.execute.return_value = "DELETE 0"

        store = ExperienceStore(embedding_service=_mock_embedding_service(), db_pool=pool)
        result = await store.delete_experience(str(uuid.uuid4()))

        assert result is False
