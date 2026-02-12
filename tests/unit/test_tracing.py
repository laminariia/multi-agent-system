"""Unit tests for LangSmith tracing integration (src.core.tracing)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.core.tracing import (
    _create_langsmith_handler,
    build_langsmith_config,
    langsmith_enabled,
)

# ---------------------------------------------------------------------------
# langsmith_enabled()
# ---------------------------------------------------------------------------


class TestLangsmithEnabled:
    """Tests for the langsmith_enabled() helper."""

    def test_enabled_when_key_set(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LANGSMITH_API_KEY", "ls-test-key-12345")
        assert langsmith_enabled() is True

    def test_disabled_when_key_missing(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
        assert langsmith_enabled() is False

    def test_disabled_when_key_empty(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LANGSMITH_API_KEY", "")
        assert langsmith_enabled() is False

    def test_disabled_when_key_whitespace(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LANGSMITH_API_KEY", "   ")
        assert langsmith_enabled() is False


# ---------------------------------------------------------------------------
# _create_langsmith_handler()
# ---------------------------------------------------------------------------


class TestCreateLangsmithHandler:
    """Tests for handler creation."""

    def test_returns_none_when_key_missing(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
        handler = _create_langsmith_handler(
            pipeline_name="test_pipeline",
            thread_id="t-001",
        )
        assert handler is None

    @patch("src.core.tracing.langsmith_enabled", return_value=True)
    def test_returns_handler_when_key_set(
        self, mock_enabled: MagicMock, monkeypatch: pytest.MonkeyPatch
    ):
        """When LangSmith is enabled and imports succeed, a handler is returned."""
        mock_tracer = MagicMock()
        mock_tracer_cls = MagicMock(return_value=mock_tracer)
        mock_client_cls = MagicMock()

        with (
            patch("langsmith.Client", mock_client_cls),
            patch("langchain_core.tracers.LangChainTracer", mock_tracer_cls),
        ):
            handler = _create_langsmith_handler(
                pipeline_name="full_pipeline",
                thread_id="t-123",
            )

        assert handler is mock_tracer
        assert handler.metadata == {
            "pipeline_name": "full_pipeline",
            "thread_id": "t-123",
        }

    @patch("src.core.tracing.langsmith_enabled", return_value=True)
    def test_handler_includes_extra_metadata(
        self, mock_enabled: MagicMock, monkeypatch: pytest.MonkeyPatch
    ):
        mock_tracer = MagicMock()
        mock_tracer_cls = MagicMock(return_value=mock_tracer)
        mock_client_cls = MagicMock()

        with (
            patch("langsmith.Client", mock_client_cls),
            patch("langchain_core.tracers.LangChainTracer", mock_tracer_cls),
        ):
            handler = _create_langsmith_handler(
                pipeline_name="pipeline_b",
                thread_id="t-456",
                extra_metadata={"city": "Moscow"},
            )

        assert handler is mock_tracer
        assert handler.metadata == {
            "pipeline_name": "pipeline_b",
            "thread_id": "t-456",
            "city": "Moscow",
        }

    @patch("src.core.tracing.langsmith_enabled", return_value=True)
    def test_returns_none_on_import_error(
        self, mock_enabled: MagicMock, monkeypatch: pytest.MonkeyPatch
    ):
        """If langsmith or langchain_core cannot be imported, return None gracefully."""
        with patch.dict("sys.modules", {"langsmith": None}):
            handler = _create_langsmith_handler(
                pipeline_name="test",
                thread_id="t-err",
            )
        # Should be None due to import failure
        assert handler is None


# ---------------------------------------------------------------------------
# build_langsmith_config()
# ---------------------------------------------------------------------------


class TestBuildLangsmithConfig:
    """Tests for the main config builder."""

    def test_empty_config_when_disabled(self, monkeypatch: pytest.MonkeyPatch):
        """When LangSmith is disabled, returns a plain config without callbacks."""
        monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
        config = build_langsmith_config(
            pipeline_name="test",
            thread_id="t-001",
        )
        assert "callbacks" not in config
        assert "metadata" not in config

    @patch("src.core.tracing._create_langsmith_handler")
    def test_config_includes_callbacks_when_enabled(
        self, mock_create: MagicMock
    ):
        """When handler is created, config contains callbacks and metadata."""
        mock_handler = MagicMock()
        mock_create.return_value = mock_handler

        config = build_langsmith_config(
            pipeline_name="full_pipeline",
            thread_id="t-123",
        )

        assert "callbacks" in config
        assert mock_handler in config["callbacks"]
        assert config["metadata"]["pipeline_name"] == "full_pipeline"
        assert config["metadata"]["thread_id"] == "t-123"

    @patch("src.core.tracing._create_langsmith_handler")
    def test_config_merges_with_existing(self, mock_create: MagicMock):
        """Existing config keys are preserved and merged."""
        mock_handler = MagicMock()
        mock_create.return_value = mock_handler

        existing = {
            "configurable": {"thread_id": "t-abc"},
            "callbacks": [MagicMock()],
        }

        config = build_langsmith_config(
            pipeline_name="test",
            thread_id="t-abc",
            existing_config=existing,
        )

        # Original callback preserved + new one appended
        assert len(config["callbacks"]) == 2
        assert config["callbacks"][-1] is mock_handler
        # configurable key preserved
        assert config["configurable"]["thread_id"] == "t-abc"

    @patch("src.core.tracing._create_langsmith_handler")
    def test_extra_metadata_in_config(self, mock_create: MagicMock):
        """Extra metadata is included in config-level metadata."""
        mock_handler = MagicMock()
        mock_create.return_value = mock_handler

        config = build_langsmith_config(
            pipeline_name="pipeline_b",
            thread_id="t-456",
            extra_metadata={"city": "London"},
        )

        assert config["metadata"]["city"] == "London"
        assert config["metadata"]["pipeline_name"] == "pipeline_b"

    @patch("src.core.tracing._create_langsmith_handler", return_value=None)
    def test_no_callbacks_when_handler_fails(self, mock_create: MagicMock):
        """When handler creation returns None, no callbacks are added."""
        config = build_langsmith_config(
            pipeline_name="test",
            thread_id="t-err",
        )
        assert "callbacks" not in config

    def test_does_not_mutate_existing_config(self):
        """The original existing_config dict is not modified."""
        original = {"configurable": {"thread_id": "t-1"}}
        original_copy = dict(original)

        with patch("src.core.tracing._create_langsmith_handler", return_value=None):
            build_langsmith_config(
                pipeline_name="test",
                thread_id="t-1",
                existing_config=original,
            )

        assert original == original_copy
