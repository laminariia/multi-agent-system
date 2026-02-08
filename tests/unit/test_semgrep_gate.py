"""Unit tests for src.security.semgrep_gate.SemgrepGate.

All subprocess calls are mocked — no real Semgrep binary required.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from src.security.semgrep_gate import ScanResult, SemgrepGate

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _semgrep_output(results: list[dict] | None = None, errors: list[dict] | None = None) -> bytes:
    """Build mock Semgrep JSON output."""
    payload = {
        "results": results or [],
        "errors": errors or [],
    }
    return json.dumps(payload).encode("utf-8")


def _finding(
    rule_id: str = "mas-dangerous-eval",
    severity: str = "ERROR",
    message: str = "eval() is blocked",
    path: str = "scan_target.py",
    line: int = 1,
    code_snippet: str = "eval(x)",
) -> dict:
    """Build a single Semgrep finding dict."""
    return {
        "check_id": rule_id,
        "path": path,
        "start": {"line": line, "col": 1},
        "end": {"line": line, "col": 20},
        "extra": {
            "message": message,
            "severity": severity,
            "lines": code_snippet,
        },
    }


def _mock_subprocess(stdout: bytes, returncode: int = 0) -> AsyncMock:
    """Create a mock for asyncio.create_subprocess_exec."""
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    proc.returncode = returncode
    return proc


# ---------------------------------------------------------------------------
# Tests: scan_code
# ---------------------------------------------------------------------------

class TestScanCode:
    """Tests for SemgrepGate.scan_code()."""

    @pytest.mark.asyncio
    async def test_clean_code_not_blocked(self) -> None:
        gate = SemgrepGate()
        stdout = _semgrep_output(results=[])

        with patch("asyncio.create_subprocess_exec", return_value=_mock_subprocess(stdout)):
            result = await gate.scan_code("print('hello')")

        assert result.blocked is False
        assert result.critical_count == 0
        assert len(result.findings) == 0

    @pytest.mark.asyncio
    async def test_dangerous_code_blocked(self) -> None:
        gate = SemgrepGate()
        stdout = _semgrep_output(results=[_finding()])

        with patch("asyncio.create_subprocess_exec", return_value=_mock_subprocess(stdout, returncode=1)):
            result = await gate.scan_code("eval(user_input)")

        assert result.blocked is True
        assert result.critical_count == 1
        assert len(result.findings) == 1
        assert result.findings[0].rule_id == "mas-dangerous-eval"

    @pytest.mark.asyncio
    async def test_warning_does_not_block(self) -> None:
        gate = SemgrepGate()
        stdout = _semgrep_output(results=[
            _finding(rule_id="mas-network-requests-get", severity="WARNING", message="requests.get detected"),
        ])

        with patch("asyncio.create_subprocess_exec", return_value=_mock_subprocess(stdout)):
            result = await gate.scan_code("requests.get('http://example.com')")

        assert result.blocked is False
        assert result.warning_count == 1
        assert result.critical_count == 0


# ---------------------------------------------------------------------------
# Tests: scan_files
# ---------------------------------------------------------------------------

class TestScanFiles:
    """Tests for SemgrepGate.scan_files()."""

    @pytest.mark.asyncio
    async def test_scan_files_with_multiple_files(self) -> None:
        gate = SemgrepGate()
        files = [
            {"path": "app.py", "content": "import os\nos.system('ls')"},
            {"path": "utils.py", "content": "def helper(): pass"},
        ]
        stdout = _semgrep_output(results=[
            _finding(rule_id="mas-dangerous-os-system", path="app.py", line=2),
        ])

        with patch("asyncio.create_subprocess_exec", return_value=_mock_subprocess(stdout, returncode=1)):
            result = await gate.scan_files(files)

        assert result.blocked is True
        assert result.critical_count == 1

    @pytest.mark.asyncio
    async def test_scan_files_empty_list(self) -> None:
        gate = SemgrepGate()
        result = await gate.scan_files([])

        assert result.blocked is False
        assert len(result.findings) == 0

    @pytest.mark.asyncio
    async def test_scan_files_matches_dev_agent_format(self) -> None:
        """Verify scan_files accepts the same format as DevAgent._parse_code_response."""
        gate = SemgrepGate()
        # This is the format DevAgent produces: {"path": "...", "content": "...", "language": "..."}
        files = [
            {
                "path": "src/components/Hero.tsx",
                "content": "export default function Hero() {}",
                "language": "typescript",
            },
        ]
        stdout = _semgrep_output(results=[])

        with patch("asyncio.create_subprocess_exec", return_value=_mock_subprocess(stdout)):
            result = await gate.scan_files(files)

        assert result.blocked is False


# ---------------------------------------------------------------------------
# Tests: is_blocked
# ---------------------------------------------------------------------------

class TestIsBlocked:
    """Tests for SemgrepGate.is_blocked()."""

    def test_blocked_result(self) -> None:
        result = ScanResult(findings=[], critical_count=1, warning_count=0, blocked=True)
        assert SemgrepGate.is_blocked(result) is True

    def test_clean_result(self) -> None:
        result = ScanResult(findings=[], critical_count=0, warning_count=0, blocked=False)
        assert SemgrepGate.is_blocked(result) is False


# ---------------------------------------------------------------------------
# Tests: error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    """Tests for timeout, missing binary, and parse errors."""

    @pytest.mark.asyncio
    async def test_semgrep_not_installed_fails_closed(self) -> None:
        gate = SemgrepGate()

        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("semgrep not found")):
            result = await gate.scan_code("print('hello')")

        # Fail closed: blocked when semgrep unavailable.
        assert result.blocked is True
        assert result.critical_count >= 1

    @pytest.mark.asyncio
    async def test_semgrep_timeout_fails_closed(self) -> None:
        gate = SemgrepGate()
        proc = _mock_subprocess(b"")
        proc.communicate = AsyncMock(side_effect=TimeoutError("timed out"))

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            # asyncio.wait_for wraps TimeoutError as asyncio.TimeoutError
            with patch("asyncio.wait_for", side_effect=TimeoutError("timed out")):
                result = await gate.scan_code("print('hello')")

        assert result.blocked is True

    @pytest.mark.asyncio
    async def test_unparseable_output_fails_closed(self) -> None:
        gate = SemgrepGate()

        with patch("asyncio.create_subprocess_exec", return_value=_mock_subprocess(b"not json at all")):
            result = await gate.scan_code("print('hello')")

        assert result.blocked is True


# ---------------------------------------------------------------------------
# Tests: no shell=True
# ---------------------------------------------------------------------------

class TestNoShell:
    """Verify SemgrepGate does NOT use shell=True."""

    @pytest.mark.asyncio
    async def test_uses_create_subprocess_exec(self) -> None:
        gate = SemgrepGate()
        stdout = _semgrep_output(results=[])

        with patch("asyncio.create_subprocess_exec", return_value=_mock_subprocess(stdout)) as mock_exec:
            await gate.scan_code("print('safe')")

        mock_exec.assert_awaited_once()
        # Verify the first argument is "semgrep" (not a shell command string).
        call_args = mock_exec.call_args
        assert call_args[0][0] == "semgrep"
