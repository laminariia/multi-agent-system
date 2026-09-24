"""Tests for configurable embedding provider (Qwen3 via OpenRouter default, OpenAI fallback).

Validates:
- Config fields load correctly with defaults and overrides.
- LLMClient.generate_embedding routes to OpenRouter primary.
- Transparent fallback to OpenAI when OpenRouter fails.
- Explicit OpenAI provider selection.
- Dimension validation (3072).
- Timeout handling.
- SemanticCache and EmbeddingService honour the configured provider.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.core.llm_client import LLMClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_3072 = [0.01] * 3072
_FAKE_WRONG_DIM = [0.01] * 1536


def _openrouter_embedding_response(embedding: list[float] | None = None) -> dict:
    """Build a mock OpenRouter embedding API response."""
    return {
        "data": [{"embedding": embedding or _FAKE_3072}],
        "model": "qwen/qwen3-embedding-8b",
        "usage": {"prompt_tokens": 5, "total_tokens": 5},
    }


def _openai_embedding_response(embedding: list[float] | None = None) -> dict:
    """Build a mock OpenAI embedding API response."""
    return {
        "data": [{"embedding": embedding or _FAKE_3072}],
        "model": "text-embedding-3-large",
        "usage": {"prompt_tokens": 5, "total_tokens": 5},
    }


def _mock_settings(**overrides):
    """Create a mock Settings object with embedding defaults and optional overrides."""
    defaults = {
        "EMBEDDING_PROVIDER": "openrouter",
        "EMBEDDING_MODEL": "qwen/qwen3-embedding-8b",
        "EMBEDDING_MODEL_OPENAI": "text-embedding-3-large",
        "EMBEDDING_DIMENSIONS": 3072,
        "OPENROUTER_API_KEY": "test-openrouter-key",
        "OPENROUTER_BASE_URL": "https://openrouter.ai/api/v1",
    }
    defaults.update(overrides)
    mock = MagicMock()
    for k, v in defaults.items():
        setattr(mock, k, v)
    return mock


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestEmbeddingConfig:
    """Tests for embedding configuration fields in Settings."""

    def test_default_provider_is_openrouter(self) -> None:
        """EMBEDDING_PROVIDER defaults to 'openrouter'."""
        with patch.dict("os.environ", {"DEBUG": "true"}, clear=False):
            from src.core.config import Settings

            s = Settings(DEBUG=True)
            assert s.EMBEDDING_PROVIDER == "openrouter"

    def test_default_model_is_qwen3(self) -> None:
        """EMBEDDING_MODEL defaults to qwen/qwen3-embedding-8b."""
        with patch.dict("os.environ", {"DEBUG": "true"}, clear=False):
            from src.core.config import Settings

            s = Settings(DEBUG=True)
            assert s.EMBEDDING_MODEL == "qwen/qwen3-embedding-8b"

    def test_default_openai_model(self) -> None:
        """EMBEDDING_MODEL_OPENAI defaults to text-embedding-3-large."""
        with patch.dict("os.environ", {"DEBUG": "true"}, clear=False):
            from src.core.config import Settings

            s = Settings(DEBUG=True)
            assert s.EMBEDDING_MODEL_OPENAI == "text-embedding-3-large"

    def test_default_dimensions(self) -> None:
        """EMBEDDING_DIMENSIONS defaults to 3072."""
        with patch.dict("os.environ", {"DEBUG": "true"}, clear=False):
            from src.core.config import Settings

            s = Settings(DEBUG=True)
            assert s.EMBEDDING_DIMENSIONS == 3072

    def test_provider_override_via_env(self) -> None:
        """EMBEDDING_PROVIDER can be overridden to 'openai'."""
        with patch.dict("os.environ", {"DEBUG": "true", "EMBEDDING_PROVIDER": "openai"}, clear=False):
            from src.core.config import Settings

            s = Settings(DEBUG=True, EMBEDDING_PROVIDER="openai")
            assert s.EMBEDDING_PROVIDER == "openai"


# ---------------------------------------------------------------------------
# LLMClient.generate_embedding tests
# ---------------------------------------------------------------------------


class TestGenerateEmbeddingOpenRouter:
    """Tests for the OpenRouter primary embedding path."""

    @pytest.mark.asyncio
    async def test_openrouter_primary_success(self) -> None:
        """generate_embedding returns 3072 floats via OpenRouter when configured."""
        settings = _mock_settings()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = _openrouter_embedding_response()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        client = LLMClient(api_key="test-key")

        with (
            patch("src.core.config.get_settings", return_value=settings),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            result = await client.generate_embedding("Hello world")

        assert len(result) == 3072
        assert result == _FAKE_3072

    @pytest.mark.asyncio
    async def test_openrouter_sends_correct_payload(self) -> None:
        """OpenRouter request includes model, input, and dimensions."""
        settings = _mock_settings()

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = _openrouter_embedding_response()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        client = LLMClient(api_key="test-key")

        with (
            patch("src.core.config.get_settings", return_value=settings),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            await client.generate_embedding("test input")

        call_kwargs = mock_client.post.call_args
        payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
        assert payload["model"] == "qwen/qwen3-embedding-8b"
        assert payload["input"] == "test input"
        assert payload["dimensions"] == 3072

    @pytest.mark.asyncio
    async def test_openrouter_uses_correct_url(self) -> None:
        """OpenRouter request goes to the configured base_url + /embeddings."""
        settings = _mock_settings()

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = _openrouter_embedding_response()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        client = LLMClient(api_key="test-key", base_url="https://openrouter.ai/api/v1")

        with (
            patch("src.core.config.get_settings", return_value=settings),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            await client.generate_embedding("test")

        call_args = mock_client.post.call_args
        url = call_args[0][0] if call_args[0] else call_args.kwargs.get("url", "")
        assert url == "https://openrouter.ai/api/v1/embeddings"


class TestGenerateEmbeddingFallback:
    """Tests for automatic fallback from OpenRouter to OpenAI."""

    @pytest.mark.asyncio
    async def test_fallback_to_openai_on_openrouter_failure(self) -> None:
        """When OpenRouter fails, generate_embedding falls back to OpenAI."""
        settings = _mock_settings()

        # OpenAI response
        openai_response = MagicMock()
        openai_response.raise_for_status = MagicMock()
        openai_response.json.return_value = _openai_embedding_response()

        mock_client = AsyncMock()
        # First call (OpenRouter) raises, second call (OpenAI) succeeds
        mock_client.post = AsyncMock(
            side_effect=[httpx.HTTPStatusError("503", request=MagicMock(), response=MagicMock()), openai_response]
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        client = LLMClient(api_key="test-key")

        with (
            patch("src.core.config.get_settings", return_value=settings),
            patch("httpx.AsyncClient", return_value=mock_client),
            patch.dict("os.environ", {"OPENAI_API_KEY": "test-openai-key"}),
        ):
            result = await client.generate_embedding("fallback test")

        assert len(result) == 3072
        # Two calls: one failed OpenRouter, one successful OpenAI
        assert mock_client.post.call_count == 2

    @pytest.mark.asyncio
    async def test_fallback_is_transparent(self) -> None:
        """Fallback produces the same 3072-dim output without caller intervention."""
        settings = _mock_settings()

        openai_response = MagicMock()
        openai_response.raise_for_status = MagicMock()
        openai_response.json.return_value = _openai_embedding_response()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=[ConnectionError("OpenRouter down"), openai_response])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        client = LLMClient(api_key="test-key")

        with (
            patch("src.core.config.get_settings", return_value=settings),
            patch("httpx.AsyncClient", return_value=mock_client),
            patch.dict("os.environ", {"OPENAI_API_KEY": "test-openai-key"}),
        ):
            result = await client.generate_embedding("transparent fallback")

        assert isinstance(result, list)
        assert all(isinstance(v, float) for v in result)
        assert len(result) == 3072


class TestGenerateEmbeddingOpenAI:
    """Tests for explicit OpenAI provider selection."""

    @pytest.mark.asyncio
    async def test_openai_direct_when_configured(self) -> None:
        """When EMBEDDING_PROVIDER=openai, OpenAI is called directly (no OpenRouter)."""
        settings = _mock_settings(EMBEDDING_PROVIDER="openai")

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = _openai_embedding_response()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        client = LLMClient(api_key="test-key")

        with (
            patch("src.core.config.get_settings", return_value=settings),
            patch("httpx.AsyncClient", return_value=mock_client),
            patch.dict("os.environ", {"OPENAI_API_KEY": "test-openai-key"}),
        ):
            result = await client.generate_embedding("openai direct")

        assert len(result) == 3072
        # Only one call -- direct to OpenAI, no OpenRouter attempt
        assert mock_client.post.call_count == 1
        call_args = mock_client.post.call_args
        url = call_args[0][0] if call_args[0] else call_args.kwargs.get("url", "")
        assert "api.openai.com" in url

    @pytest.mark.asyncio
    async def test_openai_uses_correct_model(self) -> None:
        """OpenAI path uses EMBEDDING_MODEL_OPENAI from settings."""
        settings = _mock_settings(EMBEDDING_PROVIDER="openai")

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = _openai_embedding_response()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        client = LLMClient(api_key="test-key")

        with (
            patch("src.core.config.get_settings", return_value=settings),
            patch("httpx.AsyncClient", return_value=mock_client),
            patch.dict("os.environ", {"OPENAI_API_KEY": "test-openai-key"}),
        ):
            await client.generate_embedding("model check")

        payload = mock_client.post.call_args.kwargs.get("json") or mock_client.post.call_args[1].get("json")
        assert payload["model"] == "text-embedding-3-large"


class TestDimensionValidation:
    """Tests for embedding dimension validation."""

    @pytest.mark.asyncio
    async def test_openrouter_dimension_mismatch_raises(self) -> None:
        """If OpenRouter returns wrong dimensions, LLMException is raised."""
        settings = _mock_settings()

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = _openrouter_embedding_response(embedding=_FAKE_WRONG_DIM)

        # OpenAI fallback also returns wrong dimensions
        openai_response = MagicMock()
        openai_response.raise_for_status = MagicMock()
        openai_response.json.return_value = _openai_embedding_response(embedding=_FAKE_WRONG_DIM)

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=[mock_response, openai_response])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        client = LLMClient(api_key="test-key")

        from src.core.llm_client import LLMException

        with (
            patch("src.core.config.get_settings", return_value=settings),
            patch("httpx.AsyncClient", return_value=mock_client),
            patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}),
        ):
            with pytest.raises(LLMException, match="dimension mismatch"):
                await client.generate_embedding("dimension test")

    @pytest.mark.asyncio
    async def test_openai_dimension_mismatch_raises(self) -> None:
        """If OpenAI returns wrong dimensions, LLMException is raised."""
        settings = _mock_settings(EMBEDDING_PROVIDER="openai")

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = _openai_embedding_response(embedding=_FAKE_WRONG_DIM)

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        client = LLMClient(api_key="test-key")

        from src.core.llm_client import LLMException

        with (
            patch("src.core.config.get_settings", return_value=settings),
            patch("httpx.AsyncClient", return_value=mock_client),
            patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}),
        ):
            with pytest.raises(LLMException, match="dimension mismatch"):
                await client.generate_embedding("wrong dim")

    @pytest.mark.asyncio
    async def test_correct_dimensions_pass_validation(self) -> None:
        """Embeddings with exactly 3072 dimensions pass validation."""
        settings = _mock_settings()

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = _openrouter_embedding_response()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        client = LLMClient(api_key="test-key")

        with (
            patch("src.core.config.get_settings", return_value=settings),
            patch("httpx.AsyncClient", return_value=mock_client),
        ):
            result = await client.generate_embedding("correct dim")

        assert len(result) == 3072


class TestTimeoutHandling:
    """Tests for timeout behaviour in embedding generation."""

    @pytest.mark.asyncio
    async def test_openrouter_timeout_triggers_fallback(self) -> None:
        """httpx.TimeoutException on OpenRouter triggers OpenAI fallback."""
        settings = _mock_settings()

        openai_response = MagicMock()
        openai_response.raise_for_status = MagicMock()
        openai_response.json.return_value = _openai_embedding_response()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=[httpx.TimeoutException("Request timed out"), openai_response])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        client = LLMClient(api_key="test-key")

        with (
            patch("src.core.config.get_settings", return_value=settings),
            patch("httpx.AsyncClient", return_value=mock_client),
            patch.dict("os.environ", {"OPENAI_API_KEY": "test-openai-key"}),
        ):
            result = await client.generate_embedding("timeout test")

        assert len(result) == 3072

    @pytest.mark.asyncio
    async def test_both_providers_timeout_raises(self) -> None:
        """When both providers time out, the exception propagates."""
        settings = _mock_settings()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("All timed out"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        client = LLMClient(api_key="test-key")

        with (
            patch("src.core.config.get_settings", return_value=settings),
            patch("httpx.AsyncClient", return_value=mock_client),
            patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}),
        ):
            with pytest.raises(httpx.TimeoutException):
                await client.generate_embedding("double timeout")


class TestSemanticCacheEmbeddingConfig:
    """Tests for SemanticCache embedding model configuration."""

    def test_build_embedding_model_openrouter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_build_embedding_model returns OpenAIEmbeddings with OpenRouter config."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        settings = _mock_settings()

        with (
            patch("src.core.config.get_settings", return_value=settings),
            patch("src.core.semantic_cache.OpenAIEmbeddings") as mock_cls,
        ):
            from src.core.semantic_cache import _build_embedding_model

            _build_embedding_model()

        mock_cls.assert_called_once()
        call_kwargs = mock_cls.call_args[1]
        assert call_kwargs["model"] == "qwen/qwen3-embedding-8b"
        assert call_kwargs["dimensions"] == 3072

    def test_build_embedding_model_openai(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_build_embedding_model returns OpenAIEmbeddings for OpenAI when configured."""
        monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
        settings = _mock_settings(EMBEDDING_PROVIDER="openai")

        with (
            patch("src.core.config.get_settings", return_value=settings),
            patch("src.core.semantic_cache.OpenAIEmbeddings") as mock_cls,
        ):
            from src.core.semantic_cache import _build_embedding_model

            _build_embedding_model()

        mock_cls.assert_called_once()
        call_kwargs = mock_cls.call_args[1]
        assert call_kwargs["model"] == "text-embedding-3-large"

    def test_build_embedding_model_missing_openrouter_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_build_embedding_model raises ValueError if OPENROUTER_API_KEY is missing."""
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        settings = _mock_settings()

        with (
            patch("src.core.config.get_settings", return_value=settings),
            patch("src.core.semantic_cache.OpenAIEmbeddings"),
        ):
            from src.core.semantic_cache import _build_embedding_model

            with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
                _build_embedding_model()


class TestEmbeddingServiceConfig:
    """Tests for EmbeddingService provider configuration."""

    def test_service_uses_openrouter_by_default(self) -> None:
        """EmbeddingService uses OpenRouter model when provider is openrouter."""
        with (
            patch(
                "src.knowledge.embedding_service._resolve_embedding_config",
                return_value=("openrouter", "qwen/qwen3-embedding-8b", "text-embedding-3-large", 3072),
            ),
            patch("src.knowledge.embedding_service.OpenAIEmbeddings") as mock_cls,
            patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}),
        ):
            from src.knowledge.embedding_service import EmbeddingService

            svc = EmbeddingService(api_key="test-key")

        mock_cls.assert_called_once()
        call_kwargs = mock_cls.call_args[1]
        assert call_kwargs["model"] == "qwen/qwen3-embedding-8b"
        assert svc.dimension == 3072

    def test_service_uses_openai_when_configured(self) -> None:
        """EmbeddingService uses OpenAI model when provider is openai."""
        with (
            patch(
                "src.knowledge.embedding_service._resolve_embedding_config",
                return_value=("openai", "qwen/qwen3-embedding-8b", "text-embedding-3-large", 3072),
            ),
            patch("src.knowledge.embedding_service.OpenAIEmbeddings") as mock_cls,
            patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}),
        ):
            from src.knowledge.embedding_service import EmbeddingService

            svc = EmbeddingService(api_key="test-key")

        mock_cls.assert_called_once()
        call_kwargs = mock_cls.call_args[1]
        assert call_kwargs["model"] == "text-embedding-3-large"
        assert svc.dimension == 3072
