"""Unit tests for src/knowledge/embedding_service.py.

Tests _TokenBucket rate limiter and EmbeddingService wrapper around OpenAI embeddings.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")

if TYPE_CHECKING:
    from unittest.mock import _patch


@pytest.fixture
def mock_time() -> _patch:
    """Mock time.monotonic for precise token bucket testing."""
    with patch("src.knowledge.embedding_service.time.monotonic", return_value=0.0) as mock:
        yield mock


@pytest.fixture
def mock_openai_embeddings() -> _patch:
    """Mock OpenAIEmbeddings to avoid real API calls."""
    with patch("src.knowledge.embedding_service.OpenAIEmbeddings") as mock_cls:
        mock_instance = MagicMock()
        mock_instance.aembed_query = AsyncMock(return_value=[0.1] * 3072)
        mock_instance.aembed_documents = AsyncMock(return_value=[[0.1] * 3072, [0.2] * 3072])
        mock_cls.return_value = mock_instance
        yield mock_cls


class TestTokenBucket:
    """Tests for _TokenBucket rate limiter."""

    @pytest.mark.asyncio
    async def test_token_bucket_initial_state_has_full_capacity(self, mock_time):
        """Initial state has full tokens."""
        from src.knowledge.embedding_service import _TokenBucket

        bucket = _TokenBucket(capacity=10)
        assert bucket._tokens == 10.0
        assert bucket._capacity == 10
        assert bucket._refill_period == 60.0

    @pytest.mark.asyncio
    async def test_token_bucket_acquire_decrements_token(self, mock_time):
        """Acquire decrements tokens."""
        from src.knowledge.embedding_service import _TokenBucket

        bucket = _TokenBucket(capacity=10)
        await bucket.acquire()
        assert bucket._tokens == 9.0

    @pytest.mark.asyncio
    async def test_token_bucket_acquire_multiple_times(self, mock_time):
        """Multiple acquire calls decrement tokens sequentially."""
        from src.knowledge.embedding_service import _TokenBucket

        bucket = _TokenBucket(capacity=5)
        await bucket.acquire()
        await bucket.acquire()
        await bucket.acquire()
        assert bucket._tokens == 2.0

    @pytest.mark.asyncio
    async def test_token_bucket_refill_restores_tokens_over_time(self):
        """Refill restores tokens over time."""
        from src.knowledge.embedding_service import _TokenBucket

        with patch("src.knowledge.embedding_service.time.monotonic") as mock_time:
            mock_time.return_value = 0.0
            bucket = _TokenBucket(capacity=100, refill_period=60.0)
            await bucket.acquire()
            assert bucket._tokens == 99.0

            mock_time.return_value = 30.0
            bucket._refill()
            expected = min(100.0, 99.0 + 30.0 * (100.0 / 60.0))
            assert bucket._tokens == expected

    @pytest.mark.asyncio
    async def test_token_bucket_refill_caps_at_capacity(self):
        """Refill cannot exceed capacity."""
        from src.knowledge.embedding_service import _TokenBucket

        with patch("src.knowledge.embedding_service.time.monotonic") as mock_time:
            mock_time.return_value = 0.0
            bucket = _TokenBucket(capacity=10, refill_period=60.0)
            await bucket.acquire()
            assert bucket._tokens == 9.0

            mock_time.return_value = 300.0
            bucket._refill()
            assert bucket._tokens == 10.0

    @pytest.mark.asyncio
    async def test_token_bucket_acquire_blocks_when_empty(self):
        """Acquire blocks when tokens are empty and waits for refill."""
        from src.knowledge.embedding_service import _TokenBucket

        bucket = _TokenBucket(capacity=1, refill_period=60.0)
        await bucket.acquire()
        assert bucket._tokens == 0.0

        start = asyncio.get_event_loop().time()
        await bucket.acquire()
        elapsed = asyncio.get_event_loop().time() - start
        assert elapsed > 0

    @pytest.mark.asyncio
    async def test_token_bucket_custom_refill_period(self, mock_time):
        """Custom refill period works correctly."""
        from src.knowledge.embedding_service import _TokenBucket

        bucket = _TokenBucket(capacity=10, refill_period=120.0)
        assert bucket._refill_period == 120.0
        await bucket.acquire()
        assert bucket._tokens == 9.0


class TestEmbeddingServiceInit:
    """Tests for EmbeddingService initialization."""

    def test_embedding_service_init_with_default_params(self, mock_openai_embeddings):
        """Creates EmbeddingService with default params."""
        from src.knowledge.embedding_service import EmbeddingService

        service = EmbeddingService(api_key="test-key-123")  # noqa: S106
        assert service._dim == 3072
        assert service._bucket._capacity == 3000
        mock_openai_embeddings.assert_called_once()

    def test_embedding_service_init_with_custom_api_key(self, mock_openai_embeddings):
        """Creates with custom api_key."""
        from src.knowledge.embedding_service import EmbeddingService

        service = EmbeddingService(api_key="custom-key-456")  # noqa: S106
        assert service._dim == 3072
        mock_openai_embeddings.assert_called_once()
        call_kwargs = mock_openai_embeddings.call_args.kwargs
        assert call_kwargs["openai_api_key"] == "custom-key-456"  # noqa: S105

    def test_embedding_service_init_with_custom_max_rpm(self, mock_openai_embeddings):
        """Creates with custom max_rpm."""
        from src.knowledge.embedding_service import EmbeddingService

        service = EmbeddingService(api_key="test-key", max_rpm=500)
        assert service._bucket._capacity == 500

    def test_embedding_service_init_fallback_to_env_var(self, mock_openai_embeddings, monkeypatch):
        """Falls back to OPENAI_API_KEY env var."""
        from src.knowledge.embedding_service import EmbeddingService

        monkeypatch.setenv("OPENAI_API_KEY", "env-key-789")
        service = EmbeddingService()
        assert service._dim == 3072
        call_kwargs = mock_openai_embeddings.call_args.kwargs
        assert call_kwargs["openai_api_key"] == "env-key-789"

    def test_embedding_service_dimension_property_returns_3072(self, mock_openai_embeddings):
        """Dimension property returns 3072."""
        from src.knowledge.embedding_service import EmbeddingService

        service = EmbeddingService(api_key="test-key")
        assert service.dimension == 3072


class TestEmbeddingServiceEmbedText:
    """Tests for EmbeddingService.embed_text."""

    @pytest.mark.asyncio
    async def test_embed_text_success_returns_vector(self, mock_openai_embeddings):
        """embed_text success returns vector."""
        from src.knowledge.embedding_service import EmbeddingService

        service = EmbeddingService(api_key="test-key")
        result = await service.embed_text("test text")
        assert isinstance(result, list)
        assert len(result) == 3072
        assert all(isinstance(x, float) for x in result)

    @pytest.mark.asyncio
    async def test_embed_text_calls_acquire(self, mock_openai_embeddings):
        """embed_text calls token bucket acquire."""
        from src.knowledge.embedding_service import EmbeddingService

        service = EmbeddingService(api_key="test-key")
        with patch.object(service._bucket, "acquire", new_callable=AsyncMock) as mock_acquire:
            await service.embed_text("test")
            mock_acquire.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_embed_text_propagates_errors(self, mock_openai_embeddings):
        """embed_text propagates errors from embedding client."""
        from src.knowledge.embedding_service import EmbeddingService

        service = EmbeddingService(api_key="test-key")
        service._embeddings.aembed_query = AsyncMock(side_effect=ValueError("API error"))
        with pytest.raises(ValueError, match="API error"):
            await service.embed_text("test")

    @pytest.mark.asyncio
    async def test_embed_text_logs_on_error(self, mock_openai_embeddings, caplog):
        """embed_text logs error details."""
        from src.knowledge.embedding_service import EmbeddingService

        service = EmbeddingService(api_key="test-key")
        service._embeddings.aembed_query = AsyncMock(side_effect=RuntimeError("Network timeout"))
        with pytest.raises(RuntimeError):
            await service.embed_text("test content")


class TestEmbeddingServiceEmbedBatch:
    """Tests for EmbeddingService.embed_batch."""

    @pytest.mark.asyncio
    async def test_embed_batch_empty_list_returns_empty(self, mock_openai_embeddings):
        """embed_batch with empty list returns []."""
        from src.knowledge.embedding_service import EmbeddingService

        service = EmbeddingService(api_key="test-key")
        result = await service.embed_batch([])
        assert result == []

    @pytest.mark.asyncio
    async def test_embed_batch_single_batch(self, mock_openai_embeddings):
        """embed_batch handles single batch."""
        from src.knowledge.embedding_service import EmbeddingService

        service = EmbeddingService(api_key="test-key")
        service._embeddings.aembed_documents = AsyncMock(return_value=[[0.1] * 3072, [0.2] * 3072])
        result = await service.embed_batch(["text1", "text2"])
        assert len(result) == 2
        assert len(result[0]) == 3072

    @pytest.mark.asyncio
    async def test_embed_batch_multi_batch_with_sleep(self, mock_openai_embeddings):
        """embed_batch handles multiple batches with sleep."""
        from src.knowledge.embedding_service import EmbeddingService

        service = EmbeddingService(api_key="test-key")
        service._embeddings.aembed_documents = AsyncMock(
            side_effect=[
                [[0.1] * 3072, [0.2] * 3072],
                [[0.3] * 3072],
            ]
        )
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await service.embed_batch(["t1", "t2", "t3"], batch_size=2)
            assert len(result) == 3
            mock_sleep.assert_awaited_once_with(0.1)

    @pytest.mark.asyncio
    async def test_embed_batch_no_sleep_on_last_batch(self, mock_openai_embeddings):
        """embed_batch does not sleep after last batch."""
        from src.knowledge.embedding_service import EmbeddingService

        service = EmbeddingService(api_key="test-key")
        service._embeddings.aembed_documents = AsyncMock(return_value=[[0.1] * 3072, [0.2] * 3072])
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await service.embed_batch(["t1", "t2"], batch_size=5)
            mock_sleep.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_embed_batch_acquires_token_per_text(self, mock_openai_embeddings):
        """embed_batch acquires one token per text."""
        from src.knowledge.embedding_service import EmbeddingService

        service = EmbeddingService(api_key="test-key")
        service._embeddings.aembed_documents = AsyncMock(return_value=[[0.1] * 3072, [0.2] * 3072, [0.3] * 3072])
        with patch.object(service._bucket, "acquire", new_callable=AsyncMock) as mock_acquire:
            await service.embed_batch(["t1", "t2", "t3"], batch_size=10)
            assert mock_acquire.await_count == 3

    @pytest.mark.asyncio
    async def test_embed_batch_propagates_errors(self, mock_openai_embeddings):
        """embed_batch propagates errors from embedding client."""
        from src.knowledge.embedding_service import EmbeddingService

        service = EmbeddingService(api_key="test-key")
        service._embeddings.aembed_documents = AsyncMock(side_effect=ConnectionError("Network down"))
        with pytest.raises(ConnectionError, match="Network down"):
            await service.embed_batch(["text1", "text2"])

    @pytest.mark.asyncio
    async def test_embed_batch_custom_batch_size(self, mock_openai_embeddings):
        """embed_batch respects custom batch_size."""
        from src.knowledge.embedding_service import EmbeddingService

        service = EmbeddingService(api_key="test-key")
        service._embeddings.aembed_documents = AsyncMock(
            side_effect=[
                [[0.1] * 3072],
                [[0.2] * 3072],
                [[0.3] * 3072],
            ]
        )
        result = await service.embed_batch(["t1", "t2", "t3"], batch_size=1)
        assert len(result) == 3
        assert service._embeddings.aembed_documents.await_count == 3


class TestModuleConstants:
    """Tests for module-level constants."""

    def test_default_max_requests_per_minute(self):
        """_DEFAULT_MAX_REQUESTS_PER_MINUTE is 3000."""
        from src.knowledge.embedding_service import _DEFAULT_MAX_REQUESTS_PER_MINUTE

        assert _DEFAULT_MAX_REQUESTS_PER_MINUTE == 3000

    def test_embedding_model(self):
        """_EMBEDDING_MODEL is correct."""
        from src.knowledge.embedding_service import _EMBEDDING_MODEL

        assert _EMBEDDING_MODEL == "text-embedding-3-large"

    def test_embedding_dimension(self):
        """_EMBEDDING_DIM is 3072."""
        from src.knowledge.embedding_service import _EMBEDDING_DIM

        assert _EMBEDDING_DIM == 3072
