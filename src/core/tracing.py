"""LangSmith tracing integration for pipeline execution.

Provides helpers to build LangSmith callback configuration that is
injected into ``graph.ainvoke()`` calls.  When the ``LANGSMITH_API_KEY``
environment variable is not set, all functions return no-op values so
the system works identically without tracing.

Usage::

    config = build_langsmith_config(
        pipeline_name="full_pipeline",
        thread_id="abc-123",
    )
    result = await graph.ainvoke(initial_state, config=config)
"""

from __future__ import annotations

import os
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def langsmith_enabled() -> bool:
    """Return ``True`` when a LangSmith API key is configured."""
    return bool(os.environ.get("LANGSMITH_API_KEY", "").strip())


def _create_langsmith_handler(
    *,
    pipeline_name: str,
    thread_id: str,
    extra_metadata: dict[str, Any] | None = None,
) -> Any:
    """Create a LangSmith callback handler with custom metadata.

    Returns ``None`` if LangSmith is not available (import fails or key
    is missing).
    """
    if not langsmith_enabled():
        return None

    try:
        from langsmith import Client  # noqa: PLC0415
        from langsmith.run_helpers import get_current_run_tree  # noqa: PLC0415, F401

        metadata: dict[str, Any] = {
            "pipeline_name": pipeline_name,
            "thread_id": thread_id,
        }
        if extra_metadata:
            metadata.update(extra_metadata)

        client = Client()

        # LangChain's LangSmithCallbackHandler integrates with LangGraph
        # via the standard callbacks mechanism.
        from langchain_core.tracers import LangChainTracer  # noqa: PLC0415

        handler = LangChainTracer(
            client=client,
            project_name=os.environ.get("LANGSMITH_PROJECT", "multi-agent-service"),
        )
        # Attach metadata so it appears on every trace span.
        handler.metadata = metadata  # type: ignore[attr-defined]

        logger.debug(
            "langsmith_handler_created",
            pipeline_name=pipeline_name,
            thread_id=thread_id,
        )
        return handler
    except Exception:  # noqa: BLE001
        logger.warning(
            "langsmith_handler_creation_failed",
            pipeline_name=pipeline_name,
            exc_info=True,
        )
        return None


def build_langsmith_config(
    *,
    pipeline_name: str,
    thread_id: str,
    extra_metadata: dict[str, Any] | None = None,
    existing_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a LangGraph config dict with LangSmith callbacks.

    If LangSmith is not enabled or handler creation fails, returns a
    config dict without callbacks (so ``graph.ainvoke()`` works
    identically).

    Args:
        pipeline_name: Name to tag in traces (e.g. ``"full_pipeline"``).
        thread_id: The workflow thread ID for correlation.
        extra_metadata: Additional key-value pairs to attach to traces.
        existing_config: An optional base config to merge into.

    Returns:
        A config dict suitable for ``graph.ainvoke(..., config=config)``.
    """
    config: dict[str, Any] = dict(existing_config) if existing_config else {}

    handler = _create_langsmith_handler(
        pipeline_name=pipeline_name,
        thread_id=thread_id,
        extra_metadata=extra_metadata,
    )

    if handler is not None:
        callbacks = config.get("callbacks", [])
        if not isinstance(callbacks, list):
            callbacks = list(callbacks)
        callbacks.append(handler)
        config["callbacks"] = callbacks

        # Also inject metadata at the config level for LangGraph tracing
        metadata = config.get("metadata", {})
        metadata["pipeline_name"] = pipeline_name
        metadata["thread_id"] = thread_id
        if extra_metadata:
            metadata.update(extra_metadata)
        config["metadata"] = metadata

    return config
