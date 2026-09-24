"""Embedding service wrapping Qwen3-Embedding-8B via OpenRouter.

Provides both single-text and batch embedding methods.  A simple token-bucket
rate limiter enforces the RPM limit to avoid 429 errors.

Embeddings are 3072-dimensional float vectors (``list[float]``).  No numpy
dependency is used in this module.

Model: qwen/qwen3-embedding-8b (Matryoshka 32-4096 dims, 32K context).
API: OpenRouter (OpenAI-compatible endpoint).
"""

from __future__ import annotations

import asyncio
import os
import time

import structlog
from langchain_openai import OpenAIEmbeddings

logger = structlog.get_logger(__name__)

# OpenRouter rate limit (conservative).
_DEFAULT_MAX_REQUESTS_PER_MINUTE: int = 3000
_EMBEDDING_MODEL_DEFAULT: str = "qwen/qwen3-embedding-8b"
_EMBEDDING_MODEL_OPENAI_DEFAULT: str = "text-embedding-3-large"
_EMBEDDING_DIM: int = 3072

# Backward-compatible alias (used by existing tests)
_EMBEDDING_MODEL: str = _EMBEDDING_MODEL_DEFAULT


def _resolve_embedding_config() -> tuple[str, str, str, int]:
    """Load embedding config from Settings, falling back to defaults.

    Returns:
        Tuple of (provider, model, model_openai, dimensions).
    """
    try:
        from src.core.config import get_settings  # noqa: PLC0415

        s = get_settings()
        return (
            s.EMBEDDING_PROVIDER,
            s.EMBEDDING_MODEL,
            s.EMBEDDING_MODEL_OPENAI,
            s.EMBEDDING_DIMENSIONS,
        )
    except Exception:  # noqa: BLE001
        return ("openrouter", _EMBEDDING_MODEL_DEFAULT, _EMBEDDING_MODEL_OPENAI_DEFAULT, _EMBEDDING_DIM)


class _TokenBucket:
    """Async token-bucket rate limiter.

    Allows up to *capacity* tokens per *refill_period* seconds.  Each call to
    :meth:`acquire` consumes one token, blocking until a token is available.
    """

    def __init__(self, capacity: int, refill_period: float = 60.0) -> None:
        self._capacity = capacity
        self._refill_period = refill_period
        self._tokens = float(capacity)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until a token is available, then consume it."""
        async with self._lock:
            self._refill()
            while self._tokens < 1.0:
                # Calculate how long to wait for the next token.
                deficit = 1.0 - self._tokens
                wait = deficit * (self._refill_period / self._capacity)
                await asyncio.sleep(wait)
                self._refill()
            self._tokens -= 1.0

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(
            self._capacity,
            self._tokens + elapsed * (self._capacity / self._refill_period),
        )
        self._last_refill = now


class EmbeddingService:
    """Thin wrapper around OpenAIEmbeddings routed through OpenRouter.

    Parameters
    ----------
    api_key:
        OpenRouter API key.  Falls back to the ``OPENROUTER_API_KEY`` env var.
    base_url:
        OpenRouter base URL.  Falls back to the ``OPENROUTER_BASE_URL`` env var.
    max_rpm:
        Maximum embedding requests per minute.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        max_rpm: int = _DEFAULT_MAX_REQUESTS_PER_MINUTE,
    ) -> None:
        provider, model, model_openai, dimensions = _resolve_embedding_config()

        if provider == "openrouter":
            resolved_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
            resolved_base = base_url or os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
            active_model = model
            self._embeddings = OpenAIEmbeddings(
                model=model,
                openai_api_key=resolved_key,
                openai_api_base=resolved_base,
                dimensions=dimensions,
                check_embedding_ctx_length=False,
            )
        else:
            # OpenAI direct
            resolved_key = api_key or os.environ.get("OPENAI_API_KEY", "")
            active_model = model_openai
            self._embeddings = OpenAIEmbeddings(
                model=model_openai,
                openai_api_key=resolved_key,
                dimensions=dimensions,
                check_embedding_ctx_length=False,
            )

        self._bucket = _TokenBucket(capacity=max_rpm, refill_period=60.0)
        self._dim = dimensions
        logger.info(
            "embedding_service_initialized",
            provider=provider,
            model=active_model,
            dim=dimensions,
            max_rpm=max_rpm,
        )

    @property
    def dimension(self) -> int:
        """Return the embedding vector dimensionality (3072)."""
        return self._dim

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def embed_text(self, text: str) -> list[float]:
        """Embed a single text string.

        Returns a 3072-dimensional float vector.
        """
        await self._bucket.acquire()
        try:
            result: list[float] = await self._embeddings.aembed_query(text)
            return result
        except Exception:
            logger.error("embed_text_failed", text_len=len(text), exc_info=True)
            raise

    async def embed_batch(
        self,
        texts: list[str],
        *,
        batch_size: int = 20,
    ) -> list[list[float]]:
        """Embed a list of texts in batches with rate limiting.

        Parameters
        ----------
        texts:
            Texts to embed.
        batch_size:
            Number of texts per API call.  A 100 ms sleep is inserted
            between consecutive batches.

        Returns
        -------
        list[list[float]]
            One 3072-dim vector per input text, in the same order.
        """
        if not texts:
            return []

        all_embeddings: list[list[float]] = []

        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]

            # Acquire one token per text in the batch.
            for _ in batch:
                await self._bucket.acquire()

            try:
                batch_result: list[list[float]] = await self._embeddings.aembed_documents(batch)
                all_embeddings.extend(batch_result)
            except Exception:
                logger.error(
                    "embed_batch_failed",
                    batch_start=start,
                    batch_len=len(batch),
                    exc_info=True,
                )
                raise

            # Brief pause between batches to smooth out API load.
            if start + batch_size < len(texts):
                await asyncio.sleep(0.1)

        logger.debug("embed_batch_complete", total=len(texts), batches=(len(texts) + batch_size - 1) // batch_size)
        return all_embeddings
