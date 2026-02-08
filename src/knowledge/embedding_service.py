"""Embedding service wrapping Google text-embedding-004 with rate limiting.

Provides both single-text and batch embedding methods.  A simple token-bucket
rate limiter enforces Google's 1 500 requests/min limit to avoid 429 errors.

Embeddings are 768-dimensional float vectors (``list[float]``).  No numpy
dependency is used in this module.
"""

from __future__ import annotations

import asyncio
import os
import time

import structlog
from langchain_google_genai import GoogleGenerativeAIEmbeddings

logger = structlog.get_logger(__name__)

# Google API default: 1 500 requests per minute.
_DEFAULT_MAX_REQUESTS_PER_MINUTE: int = 1500
_EMBEDDING_MODEL: str = "models/text-embedding-004"
_EMBEDDING_DIM: int = 768


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
    """Thin wrapper around GoogleGenerativeAIEmbeddings with rate limiting.

    Parameters
    ----------
    api_key:
        Google API key.  Falls back to the ``GEMINI_API_KEY`` env var.
    max_rpm:
        Maximum embedding requests per minute (Google default: 1 500).
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        max_rpm: int = _DEFAULT_MAX_REQUESTS_PER_MINUTE,
    ) -> None:
        resolved_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self._embeddings = GoogleGenerativeAIEmbeddings(
            model=_EMBEDDING_MODEL,
            google_api_key=resolved_key,
        )
        self._bucket = _TokenBucket(capacity=max_rpm, refill_period=60.0)
        self._dim = _EMBEDDING_DIM
        logger.info(
            "embedding_service_initialized",
            model=_EMBEDDING_MODEL,
            dim=_EMBEDDING_DIM,
            max_rpm=max_rpm,
        )

    @property
    def dimension(self) -> int:
        """Return the embedding vector dimensionality (768)."""
        return self._dim

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def embed_text(self, text: str) -> list[float]:
        """Embed a single text string.

        Returns a 768-dimensional float vector.
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
            One 768-dim vector per input text, in the same order.
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
