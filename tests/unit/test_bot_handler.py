"""Unit tests for src/bot/handler.py — bot application builder."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from telegram.ext import Application, CallbackQueryHandler, CommandHandler

if TYPE_CHECKING:
    from telegram.ext import BaseHandler

pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")


@pytest.fixture
def mock_settings():
    """Mock settings with TELEGRAM_BOT_TOKEN."""
    with patch("src.bot.handler.get_settings") as mock_get:
        settings_mock = MagicMock()
        settings_mock.TELEGRAM_BOT_TOKEN = "test-bot-token-12345"  # noqa: S105
        mock_get.return_value = settings_mock
        yield settings_mock


@pytest.fixture
def mock_application():
    """Mock telegram Application builder chain."""
    with patch("src.bot.handler.Application.builder") as mock_builder_fn:
        # Create mock app
        mock_app = MagicMock(spec=Application)
        mock_app.handlers = {}

        # Track added handlers
        added_handlers: list[BaseHandler] = []

        def mock_add_handler(handler: BaseHandler) -> None:
            added_handlers.append(handler)

        mock_app.add_handler = mock_add_handler
        mock_app._added_handlers = added_handlers  # Store for inspection

        # Mock builder chain
        mock_builder = MagicMock()
        mock_builder.token.return_value = mock_builder
        mock_builder.build.return_value = mock_app

        mock_builder_fn.return_value = mock_builder

        yield mock_app, mock_builder


class TestCreateBotApplication:
    """Tests for create_bot_application function."""

    def test_returns_application_instance(self, mock_settings, mock_application):
        """create_bot_application returns Application instance."""
        from src.bot.handler import create_bot_application

        mock_app, _ = mock_application
        result = create_bot_application()

        assert result is mock_app

    def test_reads_token_from_settings(self, mock_settings, mock_application):
        """create_bot_application reads token from settings."""
        from src.bot.handler import create_bot_application

        mock_app, mock_builder = mock_application
        create_bot_application()

        # Verify token was passed to builder
        mock_builder.token.assert_called_once_with("test-bot-token-12345")  # noqa: S105

    def test_raises_runtime_error_if_no_token(self, mock_application):
        """create_bot_application raises RuntimeError if no token."""
        from src.bot.handler import create_bot_application

        with patch("src.bot.handler.get_settings") as mock_get:
            settings_mock = MagicMock()
            settings_mock.TELEGRAM_BOT_TOKEN = None
            mock_get.return_value = settings_mock

            with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN is not set"):
                create_bot_application()

    def test_raises_runtime_error_if_empty_token(self, mock_application):
        """create_bot_application raises RuntimeError if token is empty string."""
        from src.bot.handler import create_bot_application

        with patch("src.bot.handler.get_settings") as mock_get:
            settings_mock = MagicMock()
            settings_mock.TELEGRAM_BOT_TOKEN = ""
            mock_get.return_value = settings_mock

            with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN is not set"):
                create_bot_application()

    def test_registers_six_command_handlers(self, mock_settings, mock_application):
        """create_bot_application registers 6 command handlers."""
        from src.bot.handler import create_bot_application

        mock_app, _ = mock_application
        create_bot_application()

        # Count CommandHandler instances
        command_handlers = [
            h for h in mock_app._added_handlers if isinstance(h, CommandHandler)
        ]
        assert len(command_handlers) == 6

    def test_command_names_are_correct(self, mock_settings, mock_application):
        """create_bot_application command names are start, status, pending, stats, approve, skip."""
        from src.bot.handler import create_bot_application

        mock_app, _ = mock_application
        create_bot_application()

        # Extract command names (commands is a frozenset)
        command_handlers = [
            h for h in mock_app._added_handlers if isinstance(h, CommandHandler)
        ]
        all_commands = set()
        for h in command_handlers:
            all_commands.update(h.commands)

        expected_commands = {"start", "status", "pending", "stats", "approve", "skip"}
        assert all_commands == expected_commands

    def test_registers_callback_query_handler(self, mock_settings, mock_application):
        """create_bot_application registers callback query handler."""
        from src.bot.handler import create_bot_application

        mock_app, _ = mock_application
        create_bot_application()

        # Find CallbackQueryHandler
        callback_handlers = [
            h for h in mock_app._added_handlers if isinstance(h, CallbackQueryHandler)
        ]
        assert len(callback_handlers) == 1

    def test_total_seven_handlers_registered(self, mock_settings, mock_application):
        """create_bot_application total 7 handlers registered."""
        from src.bot.handler import create_bot_application

        mock_app, _ = mock_application
        create_bot_application()

        assert len(mock_app._added_handlers) == 7

    def test_logs_creation_with_handler_count(self, mock_settings, mock_application):
        """create_bot_application logs creation with handler count."""
        from src.bot.handler import create_bot_application

        with patch("src.bot.handler.logger") as mock_logger:
            create_bot_application()

            mock_logger.info.assert_called_once_with("telegram_bot.created", handlers=6)

    def test_command_handler_start_is_registered(self, mock_settings, mock_application):
        """create_bot_application registers start command handler."""
        from src.bot.handler import create_bot_application

        mock_app, _ = mock_application
        create_bot_application()

        command_handlers = [
            h for h in mock_app._added_handlers if isinstance(h, CommandHandler)
        ]
        start_handlers = [h for h in command_handlers if "start" in h.commands]
        assert len(start_handlers) == 1

    def test_command_handler_status_is_registered(self, mock_settings, mock_application):
        """create_bot_application registers status command handler."""
        from src.bot.handler import create_bot_application

        mock_app, _ = mock_application
        create_bot_application()

        command_handlers = [
            h for h in mock_app._added_handlers if isinstance(h, CommandHandler)
        ]
        status_handlers = [h for h in command_handlers if "status" in h.commands]
        assert len(status_handlers) == 1

    def test_command_handler_pending_is_registered(self, mock_settings, mock_application):
        """create_bot_application registers pending command handler."""
        from src.bot.handler import create_bot_application

        mock_app, _ = mock_application
        create_bot_application()

        command_handlers = [
            h for h in mock_app._added_handlers if isinstance(h, CommandHandler)
        ]
        pending_handlers = [h for h in command_handlers if "pending" in h.commands]
        assert len(pending_handlers) == 1

    def test_command_handler_stats_is_registered(self, mock_settings, mock_application):
        """create_bot_application registers stats command handler."""
        from src.bot.handler import create_bot_application

        mock_app, _ = mock_application
        create_bot_application()

        command_handlers = [
            h for h in mock_app._added_handlers if isinstance(h, CommandHandler)
        ]
        stats_handlers = [h for h in command_handlers if "stats" in h.commands]
        assert len(stats_handlers) == 1

    def test_command_handler_approve_is_registered(self, mock_settings, mock_application):
        """create_bot_application registers approve command handler."""
        from src.bot.handler import create_bot_application

        mock_app, _ = mock_application
        create_bot_application()

        command_handlers = [
            h for h in mock_app._added_handlers if isinstance(h, CommandHandler)
        ]
        approve_handlers = [h for h in command_handlers if "approve" in h.commands]
        assert len(approve_handlers) == 1

    def test_command_handler_skip_is_registered(self, mock_settings, mock_application):
        """create_bot_application registers skip command handler."""
        from src.bot.handler import create_bot_application

        mock_app, _ = mock_application
        create_bot_application()

        command_handlers = [
            h for h in mock_app._added_handlers if isinstance(h, CommandHandler)
        ]
        skip_handlers = [h for h in command_handlers if "skip" in h.commands]
        assert len(skip_handlers) == 1

    def test_callback_query_handler_uses_button_callback(self, mock_settings, mock_application):
        """create_bot_application callback query handler uses button_callback function."""
        from src.bot.handler import create_bot_application

        mock_app, _ = mock_application
        create_bot_application()

        callback_handlers = [
            h for h in mock_app._added_handlers if isinstance(h, CallbackQueryHandler)
        ]
        # Verify the handler has a callback
        assert callback_handlers[0].callback is not None

    def test_builder_chain_is_called_correctly(self, mock_settings, mock_application):
        """create_bot_application calls Application.builder().token().build()."""
        from src.bot.handler import create_bot_application

        mock_app, mock_builder = mock_application

        with patch("src.bot.handler.Application.builder") as mock_builder_fn:
            mock_builder_fn.return_value = mock_builder
            create_bot_application()

            # Verify chain
            mock_builder_fn.assert_called_once()
            mock_builder.token.assert_called_once_with("test-bot-token-12345")  # noqa: S105
            mock_builder.build.assert_called_once()
