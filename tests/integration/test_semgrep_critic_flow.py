"""Integration test: Semgrep security gate integrated with Critic Agent.

Verifies that the Critic Agent blocks dangerous code via SemgrepGate
before even consulting the LLM for a review verdict.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.security.semgrep_gate import ScanResult, SemgrepFinding, SemgrepGate


# ---------------------------------------------------------------------------
# Test: SemgrepGate blocks os.system()
# ---------------------------------------------------------------------------

class TestSemgrepCriticIntegration:
    """Integration flow: Critic uses SemgrepGate to pre-screen code artifacts."""

    def _make_dev_artifacts(self, code: str) -> list[str]:
        """Simulate dev artifacts as stored in state['artifacts']['dev']."""
        artifact = {
            "files": [
                {"path": "main.py", "content": code, "language": "python"},
            ],
            "dependencies": [],
        }
        return ["artifact-id-001", json.dumps(artifact)]

    @pytest.mark.asyncio
    async def test_scan_result_blocks_dangerous_code(self) -> None:
        """SemgrepGate.scan_files should flag os.system() as blocked."""
        gate = SemgrepGate()

        files = [{"path": "main.py", "content": "import os\nos.system('rm -rf /')\n"}]

        # If semgrep is not installed, mock the subprocess call.
        mock_output = json.dumps({
            "results": [
                {
                    "check_id": "mas-dangerous-os-system",
                    "path": "main.py",
                    "start": {"line": 2, "col": 1},
                    "end": {"line": 2, "col": 25},
                    "extra": {
                        "message": "os.system() is blocked in MAS sandbox",
                        "severity": "ERROR",
                        "lines": "os.system('rm -rf /')",
                    },
                }
            ],
            "errors": [],
        })

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            proc = AsyncMock()
            proc.communicate = AsyncMock(return_value=(mock_output.encode(), b""))
            proc.returncode = 1  # Semgrep returns 1 when findings exist
            mock_exec.return_value = proc

            result = await gate.scan_files(files)

        assert result.blocked is True
        assert result.critical_count >= 1
        assert any("os.system" in f.message or "os-system" in f.rule_id for f in result.findings)

    @pytest.mark.asyncio
    async def test_scan_result_allows_clean_code(self) -> None:
        """SemgrepGate.scan_files should pass safe code."""
        gate = SemgrepGate()

        files = [{"path": "app.py", "content": "def hello():\n    return 'Hello, World!'\n"}]

        mock_output = json.dumps({"results": [], "errors": []})

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            proc = AsyncMock()
            proc.communicate = AsyncMock(return_value=(mock_output.encode(), b""))
            proc.returncode = 0
            mock_exec.return_value = proc

            result = await gate.scan_files(files)

        assert result.blocked is False
        assert result.critical_count == 0
        assert len(result.findings) == 0

    @pytest.mark.asyncio
    async def test_scan_code_single_string(self) -> None:
        """SemgrepGate.scan_code should handle a raw code string."""
        gate = SemgrepGate()

        mock_output = json.dumps({
            "results": [
                {
                    "check_id": "mas-dangerous-eval",
                    "path": "temp.py",
                    "start": {"line": 1, "col": 1},
                    "end": {"line": 1, "col": 20},
                    "extra": {
                        "message": "eval() is blocked",
                        "severity": "ERROR",
                        "lines": "eval(user_input)",
                    },
                }
            ],
            "errors": [],
        })

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            proc = AsyncMock()
            proc.communicate = AsyncMock(return_value=(mock_output.encode(), b""))
            proc.returncode = 1
            mock_exec.return_value = proc

            result = await gate.scan_code("eval(user_input)", language="python")

        assert result.blocked is True
        assert gate.is_blocked(result)
