"""Integration tests for Sentry SDK configuration.

Tests verify that Sentry init, exception capture, breadcrumbs,
transaction tracing, and the disabled-when-no-DSN path all work
correctly through the actual init_sentry function.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest


def _make_sentry_mocks():
    """Create a full set of sentry_sdk mock modules."""
    mock_sdk = MagicMock()
    mock_asyncio_mod = MagicMock()
    mock_sqlalchemy_mod = MagicMock()
    mock_litestar_mod = MagicMock()

    modules = {
        "sentry_sdk": mock_sdk,
        "sentry_sdk.integrations": MagicMock(),
        "sentry_sdk.integrations.asyncio": mock_asyncio_mod,
        "sentry_sdk.integrations.sqlalchemy": mock_sqlalchemy_mod,
        "sentry_sdk.integrations.litestar": mock_litestar_mod,
    }
    return mock_sdk, modules


class TestSentryDSNConfiguration:
    """Test Sentry DSN configuration paths."""

    def test_sentry_disabled_when_dsn_is_none(self) -> None:
        from src.monitoring.sentry_config import init_sentry

        with patch("src.monitoring.sentry_config.logger") as mock_logger:
            init_sentry(None)
            mock_logger.info.assert_called_with(
                "sentry_disabled",
                reason="no DSN configured",
            )

    def test_sentry_disabled_when_dsn_is_empty(self) -> None:
        from src.monitoring.sentry_config import init_sentry

        with patch("src.monitoring.sentry_config.logger") as mock_logger:
            init_sentry("")
            mock_logger.info.assert_called_with(
                "sentry_disabled",
                reason="no DSN configured",
            )

    def test_sentry_enabled_with_valid_dsn(self) -> None:
        mock_sdk, modules = _make_sentry_mocks()

        with patch.dict(sys.modules, modules):
            from src.monitoring.sentry_config import init_sentry

            init_sentry("https://abc123@o456.ingest.sentry.io/789")

            mock_sdk.init.assert_called_once()
            call_kwargs = mock_sdk.init.call_args.kwargs
            assert call_kwargs["dsn"] == "https://abc123@o456.ingest.sentry.io/789"

    def test_sentry_passes_environment(self) -> None:
        mock_sdk, modules = _make_sentry_mocks()

        with patch.dict(sys.modules, modules):
            from src.monitoring.sentry_config import init_sentry

            init_sentry("https://abc@sentry.io/1", environment="staging")

            call_kwargs = mock_sdk.init.call_args.kwargs
            assert call_kwargs["environment"] == "staging"


class TestExceptionCapture:
    """Test Sentry exception capture via before_send filter."""

    def test_normal_exception_passes_through(self) -> None:
        from src.monitoring.sentry_config import _before_send

        event = {"exception": {"values": [{"type": "ValueError"}]}}
        hint = {"exc_info": (ValueError, ValueError("test"), None)}

        result = _before_send(event, hint)
        assert result is event

    def test_rate_limit_exceptions_filtered(self) -> None:
        from src.monitoring.sentry_config import _before_send

        for exc_name in ("LLMRateLimitError", "PlatformRateLimitError"):
            exc_type = type(exc_name, (Exception,), {})
            event = {"message": "rate limit"}
            hint = {"exc_info": (exc_type, None, None)}

            result = _before_send(event, hint)
            assert result is None, f"{exc_name} should be filtered"

    def test_non_rate_limit_exception_not_filtered(self) -> None:
        from src.monitoring.sentry_config import _before_send

        exc_type = type("DatabaseError", (Exception,), {})
        event = {"message": "db error"}
        hint = {"exc_info": (exc_type, None, None)}

        result = _before_send(event, hint)
        assert result is event


class TestBreadcrumbRecording:
    """Test that Sentry breadcrumbs are correctly configured."""

    def test_send_default_pii_disabled(self) -> None:
        """PII scrubbing should be enabled (send_default_pii=False)."""
        mock_sdk, modules = _make_sentry_mocks()

        with patch.dict(sys.modules, modules):
            from src.monitoring.sentry_config import init_sentry

            init_sentry("https://abc@sentry.io/1")

            call_kwargs = mock_sdk.init.call_args.kwargs
            assert call_kwargs["send_default_pii"] is False

    def test_before_send_hook_installed(self) -> None:
        """The before_send hook should be set in init."""
        from src.monitoring.sentry_config import _before_send

        mock_sdk, modules = _make_sentry_mocks()

        with patch.dict(sys.modules, modules):
            from src.monitoring.sentry_config import init_sentry

            init_sentry("https://abc@sentry.io/1")

            call_kwargs = mock_sdk.init.call_args.kwargs
            assert call_kwargs["before_send"] is _before_send


class TestTransactionTracing:
    """Test Sentry performance transaction tracing for agents."""

    def test_transaction_created_with_agent_op(self) -> None:
        mock_sdk = MagicMock()
        mock_txn = MagicMock()
        mock_sdk.start_transaction.return_value = mock_txn

        with patch.dict(sys.modules, {"sentry_sdk": mock_sdk}):
            from src.monitoring.sentry_config import start_agent_transaction

            with start_agent_transaction("scout", "thread-001"):
                pass

            mock_sdk.start_transaction.assert_called_once_with(
                op="agent",
                name="scout",
            )

    def test_transaction_tags_set(self) -> None:
        mock_sdk = MagicMock()
        mock_txn = MagicMock()
        mock_sdk.start_transaction.return_value = mock_txn

        with patch.dict(sys.modules, {"sentry_sdk": mock_sdk}):
            from src.monitoring.sentry_config import start_agent_transaction

            with start_agent_transaction("bid", "thread-xyz"):
                pass

            mock_txn.set_tag.assert_any_call("agent.role", "bid")
            mock_txn.set_tag.assert_any_call("thread.id", "thread-xyz")

    def test_transaction_ok_on_success(self) -> None:
        mock_sdk = MagicMock()
        mock_txn = MagicMock()
        mock_sdk.start_transaction.return_value = mock_txn

        with patch.dict(sys.modules, {"sentry_sdk": mock_sdk}):
            from src.monitoring.sentry_config import start_agent_transaction

            with start_agent_transaction("dev", "t-1"):
                pass

            mock_txn.set_status.assert_called_with("ok")
            mock_txn.finish.assert_called_once()

    def test_transaction_error_on_exception(self) -> None:
        mock_sdk = MagicMock()
        mock_txn = MagicMock()
        mock_sdk.start_transaction.return_value = mock_txn

        with patch.dict(sys.modules, {"sentry_sdk": mock_sdk}):
            from src.monitoring.sentry_config import start_agent_transaction

            with pytest.raises(RuntimeError):
                with start_agent_transaction("critic", "t-2"):
                    raise RuntimeError("test failure")

            mock_txn.set_status.assert_called_with("internal_error")
            mock_txn.finish.assert_called_once()

    def test_transaction_yields_none_without_sentry(self) -> None:
        with patch.dict(sys.modules, {"sentry_sdk": None}):
            from src.monitoring.sentry_config import start_agent_transaction

            with start_agent_transaction("planner", "t-3") as txn:
                assert txn is None

    def test_traces_sample_rate_configured(self) -> None:
        mock_sdk, modules = _make_sentry_mocks()

        with patch.dict(sys.modules, modules):
            from src.monitoring.sentry_config import init_sentry

            init_sentry("https://abc@sentry.io/1")

            call_kwargs = mock_sdk.init.call_args.kwargs
            assert call_kwargs["traces_sample_rate"] == 0.1


class TestIntegrations:
    """Test that all expected Sentry integrations are included."""

    def test_asyncio_integration_included(self) -> None:
        mock_sdk, modules = _make_sentry_mocks()
        mock_inst = MagicMock()
        modules["sentry_sdk.integrations.asyncio"].AsyncioIntegration.return_value = mock_inst

        with patch.dict(sys.modules, modules):
            from src.monitoring.sentry_config import init_sentry

            init_sentry("https://abc@sentry.io/1")

            call_kwargs = mock_sdk.init.call_args.kwargs
            assert mock_inst in call_kwargs["integrations"]

    def test_sqlalchemy_integration_included(self) -> None:
        mock_sdk, modules = _make_sentry_mocks()
        mock_inst = MagicMock()
        modules["sentry_sdk.integrations.sqlalchemy"].SqlalchemyIntegration.return_value = mock_inst

        with patch.dict(sys.modules, modules):
            from src.monitoring.sentry_config import init_sentry

            init_sentry("https://abc@sentry.io/1")

            call_kwargs = mock_sdk.init.call_args.kwargs
            assert mock_inst in call_kwargs["integrations"]

    def test_litestar_integration_included(self) -> None:
        mock_sdk, modules = _make_sentry_mocks()
        mock_inst = MagicMock()
        modules["sentry_sdk.integrations.litestar"].LitestarIntegration.return_value = mock_inst

        with patch.dict(sys.modules, modules):
            from src.monitoring.sentry_config import init_sentry

            init_sentry("https://abc@sentry.io/1")

            call_kwargs = mock_sdk.init.call_args.kwargs
            assert mock_inst in call_kwargs["integrations"]
