"""Unit tests for bot and CLI entry points.

Tests ``src.bot.__main__`` and ``src.cli.__main__`` — module execution,
function calls, and error handling.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# TestBotMain — src.bot.__main__
# ---------------------------------------------------------------------------


class TestBotMain:
    """Tests for the bot __main__ module."""

    def test_main_creates_application(self) -> None:
        """Should call create_bot_application."""
        from src.bot.__main__ import main

        with patch("src.bot.__main__.create_bot_application") as mock_create:
            mock_app = MagicMock()
            mock_create.return_value = mock_app

            main()

            mock_create.assert_called_once()

    def test_main_calls_run_polling(self) -> None:
        """Should call run_polling on the created application."""
        from src.bot.__main__ import main

        with patch("src.bot.__main__.create_bot_application") as mock_create:
            mock_app = MagicMock()
            mock_create.return_value = mock_app

            main()

            mock_app.run_polling.assert_called_once()

    def test_main_passes_correct_allowed_updates(self) -> None:
        """Should pass allowed_updates=['message', 'callback_query']."""
        from src.bot.__main__ import main

        with patch("src.bot.__main__.create_bot_application") as mock_create:
            mock_app = MagicMock()
            mock_create.return_value = mock_app

            main()

            mock_app.run_polling.assert_called_once_with(
                allowed_updates=["message", "callback_query"]
            )

    def test_main_with_create_failure(self) -> None:
        """Should propagate exception when create_bot_application fails."""
        from src.bot.__main__ import main

        with patch("src.bot.__main__.create_bot_application") as mock_create:
            mock_create.side_effect = RuntimeError("Bot token missing")

            with pytest.raises(RuntimeError, match="Bot token missing"):
                main()

    def test_main_with_run_polling_failure(self) -> None:
        """Should propagate exception when run_polling fails."""
        from src.bot.__main__ import main

        with patch("src.bot.__main__.create_bot_application") as mock_create:
            mock_app = MagicMock()
            mock_app.run_polling.side_effect = ConnectionError("Network error")
            mock_create.return_value = mock_app

            with pytest.raises(ConnectionError, match="Network error"):
                main()

    def test_module_has_main_guard(self) -> None:
        """Should verify __name__ == '__main__' pattern exists."""
        import inspect
        from pathlib import Path

        import src.bot.__main__

        # Read the source file
        module_path = Path(inspect.getfile(src.bot.__main__))
        source = module_path.read_text()

        # Verify the guard pattern exists
        assert 'if __name__ == "__main__":' in source
        assert "main()" in source

    def test_main_function_signature(self) -> None:
        """Should verify main() has correct signature."""
        import inspect

        from src.bot.__main__ import main

        sig = inspect.signature(main)

        # Should have no parameters
        assert len(sig.parameters) == 0

        # Should return None
        assert sig.return_annotation is None or sig.return_annotation is type(None)


# ---------------------------------------------------------------------------
# TestCliMain — src.cli.__main__
# ---------------------------------------------------------------------------


class TestCliMain:
    """Tests for the CLI __main__ module."""

    def test_cli_module_imports_main(self) -> None:
        """Should import main from src.cli.create_user."""
        from pathlib import Path

        # Read the source file directly without importing
        module_path = Path("src/cli/__main__.py")
        source = module_path.read_text()

        # Verify the import exists
        assert "from src.cli.create_user import main" in source

    def test_cli_module_calls_main(self) -> None:
        """Should call main() at module level."""
        from pathlib import Path

        # Read the source file directly without importing
        module_path = Path("src/cli/__main__.py")
        source = module_path.read_text()

        # Verify main() is called
        assert "main()" in source

    def test_cli_main_execution_order(self) -> None:
        """Should verify main() is called after import (not before)."""
        from pathlib import Path

        # Read the source file directly without importing
        module_path = Path("src/cli/__main__.py")
        source = module_path.read_text()
        lines = source.split("\n")

        # Find import and call lines
        import_line_idx = None
        call_line_idx = None

        for idx, line in enumerate(lines):
            if "from src.cli.create_user import main" in line:
                import_line_idx = idx
            if line.strip() == "main()":
                call_line_idx = idx

        # Verify both exist
        assert import_line_idx is not None, "Import not found"
        assert call_line_idx is not None, "main() call not found"

        # Verify call comes after import
        assert call_line_idx > import_line_idx, "main() called before import"

    def test_cli_main_called_on_import(self) -> None:
        """Should call main when the module is imported."""
        import sys

        # We cannot re-import the module (it executes main() immediately),
        # so we test by patching and importing in isolation
        with patch("src.cli.create_user.main") as mock_main:
            # Remove the module if already imported
            if "src.cli.__main__" in sys.modules:
                del sys.modules["src.cli.__main__"]

            # Import the module — this should trigger main()
            import src.cli.__main__  # noqa: F401

            # Verify main was called
            mock_main.assert_called_once()
