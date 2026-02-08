"""Unit tests for src.knowledge.retrieval and src.knowledge.embedding_service.

All embedding API calls and database operations are mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.knowledge.retrieval import KnowledgeResult, KnowledgeRetriever

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_VECTOR = [0.1] * 768


def _make_row(
    *,
    id: str = "row-1",
    title: str = "Sample",
    content: str = "Sample content",
    category: str | None = "web_development",
    similarity: float = 0.9,
    success_rate: float | None = 0.8,
) -> MagicMock:
    """Return a mock asyncpg.Record with the expected column names."""
    row = MagicMock()
    row.__getitem__ = lambda self, key: {
        "id": id,
        "title": title,
        "content": content,
        "category": category,
        "similarity": similarity,
        "success_rate": success_rate,
    }[key]
    return row


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def embedding_service() -> AsyncMock:
    """Mock EmbeddingService with a canned embed_text / embed_batch."""
    svc = AsyncMock()
    svc.embed_text = AsyncMock(return_value=FAKE_VECTOR)
    svc.embed_batch = AsyncMock(return_value=[FAKE_VECTOR, FAKE_VECTOR])
    svc.dimension = 768
    return svc


@pytest.fixture()
def retriever(embedding_service: AsyncMock, mock_db_pool: AsyncMock) -> KnowledgeRetriever:
    """KnowledgeRetriever wired to mock embedding service and DB pool."""
    return KnowledgeRetriever(embedding_service=embedding_service, db_pool=mock_db_pool)


# ---------------------------------------------------------------------------
# TestEmbeddingService
# ---------------------------------------------------------------------------


class TestEmbeddingService:
    """Tests for the EmbeddingService wrapper (mocked GoogleGenerativeAIEmbeddings)."""

    async def test_embed_text_returns_vector(self) -> None:
        """embed_text should return a list of floats from the underlying model."""
        with patch("src.knowledge.embedding_service.GoogleGenerativeAIEmbeddings") as mock_cls:
            mock_model = AsyncMock()
            mock_model.aembed_query = AsyncMock(return_value=FAKE_VECTOR)
            mock_cls.return_value = mock_model

            from src.knowledge.embedding_service import EmbeddingService

            svc = EmbeddingService(api_key="test-key")
            result = await svc.embed_text("hello world")

            assert isinstance(result, list)
            assert len(result) == 768
            assert all(isinstance(v, float) for v in result)
            mock_model.aembed_query.assert_awaited_once_with("hello world")

    async def test_embed_batch_returns_list_of_vectors(self) -> None:
        """embed_batch should return one vector per input text."""
        with patch("src.knowledge.embedding_service.GoogleGenerativeAIEmbeddings") as mock_cls:
            mock_model = AsyncMock()
            mock_model.aembed_documents = AsyncMock(return_value=[FAKE_VECTOR, FAKE_VECTOR])
            mock_cls.return_value = mock_model

            from src.knowledge.embedding_service import EmbeddingService

            svc = EmbeddingService(api_key="test-key")
            result = await svc.embed_batch(["text1", "text2"])

            assert isinstance(result, list)
            assert len(result) == 2
            assert all(len(vec) == 768 for vec in result)

    async def test_embed_text_rate_limiting(self) -> None:
        """Under normal rate the rate limiter should not block."""
        with patch("src.knowledge.embedding_service.GoogleGenerativeAIEmbeddings") as mock_cls:
            mock_model = AsyncMock()
            mock_model.aembed_query = AsyncMock(return_value=FAKE_VECTOR)
            mock_cls.return_value = mock_model

            from src.knowledge.embedding_service import EmbeddingService

            svc = EmbeddingService(api_key="test-key", max_rpm=1500)

            # Call 3 times quickly -- should not block with capacity=1500
            for _ in range(3):
                vec = await svc.embed_text("test")
                assert len(vec) == 768

            assert mock_model.aembed_query.await_count == 3


# ---------------------------------------------------------------------------
# TestKnowledgeRetriever
# ---------------------------------------------------------------------------


class TestKnowledgeRetriever:
    """Tests for the KnowledgeRetriever vector-search wrapper."""

    async def test_search_returns_results(
        self,
        retriever: KnowledgeRetriever,
        mock_db_pool: AsyncMock,
    ) -> None:
        """search() should return KnowledgeResult objects from DB rows."""
        rows = [
            _make_row(id="r1", title="Proposal A", similarity=0.95, success_rate=0.8),
            _make_row(id="r2", title="Proposal B", similarity=0.85, success_rate=0.6),
        ]
        mock_db_pool._test_conn.fetch = AsyncMock(return_value=rows)

        results = await retriever.search("build a landing page")

        assert len(results) == 2
        assert all(isinstance(r, KnowledgeResult) for r in results)
        assert results[0].id == "r1"
        assert results[0].title == "Proposal A"

    async def test_search_with_category_filter(
        self,
        retriever: KnowledgeRetriever,
        embedding_service: AsyncMock,
        mock_db_pool: AsyncMock,
    ) -> None:
        """When category is provided, the SQL params should include it."""
        mock_db_pool._test_conn.fetch = AsyncMock(return_value=[])

        await retriever.search("test query", category="web_development")

        # The fetch call args include the parameterized values.
        call_args = mock_db_pool._test_conn.fetch.call_args
        # params list (positional args after SQL string) should contain the category
        params = call_args[0][1:]  # skip the SQL string
        assert "web_development" in params

    async def test_search_with_type_filter(
        self,
        retriever: KnowledgeRetriever,
        mock_db_pool: AsyncMock,
    ) -> None:
        """When kb_type is provided, the SQL params should include it."""
        mock_db_pool._test_conn.fetch = AsyncMock(return_value=[])

        await retriever.search("test query", kb_type="proposal_template")

        call_args = mock_db_pool._test_conn.fetch.call_args
        params = call_args[0][1:]
        assert "proposal_template" in params

    async def test_search_empty_results(
        self,
        retriever: KnowledgeRetriever,
        mock_db_pool: AsyncMock,
    ) -> None:
        """When the DB returns no rows, search() should return an empty list."""
        mock_db_pool._test_conn.fetch = AsyncMock(return_value=[])

        results = await retriever.search("nonexistent topic")

        assert results == []

    async def test_search_reranks_by_success_rate(
        self,
        retriever: KnowledgeRetriever,
        mock_db_pool: AsyncMock,
    ) -> None:
        """Results should be ordered by final_score = 0.7*similarity + 0.3*success_rate."""
        # Row A: similarity=0.80, success_rate=1.0  -> score = 0.56 + 0.30 = 0.86
        # Row B: similarity=0.95, success_rate=0.0  -> score = 0.665 + 0.0 = 0.665
        # Row C: similarity=0.85, success_rate=0.9  -> score = 0.595 + 0.27 = 0.865
        rows = [
            _make_row(id="B", title="Low SR", similarity=0.95, success_rate=0.0),
            _make_row(id="A", title="High SR", similarity=0.80, success_rate=1.0),
            _make_row(id="C", title="Mid SR", similarity=0.85, success_rate=0.9),
        ]
        mock_db_pool._test_conn.fetch = AsyncMock(return_value=rows)

        results = await retriever.search("query", top_k=3)

        # Expected order by final_score: C (0.865), A (0.86), ... wait let's recalculate:
        # A: 0.7*0.80 + 0.3*1.0 = 0.56 + 0.30 = 0.86
        # C: 0.7*0.85 + 0.3*0.9 = 0.595 + 0.27 = 0.865
        # B: 0.7*0.95 + 0.3*0.0 = 0.665 + 0.0 = 0.665
        # Order: C(0.865), A(0.86), B(0.665)
        assert results[0].id == "C"
        assert results[1].id == "A"
        assert results[2].id == "B"

        # Verify score values
        assert abs(results[0].final_score - 0.865) < 1e-6
        assert abs(results[1].final_score - 0.86) < 1e-6
        assert abs(results[2].final_score - 0.665) < 1e-6
