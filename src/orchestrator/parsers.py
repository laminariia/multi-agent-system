"""Shared orchestrator file parsers and utilities.

Extracted from ``src.bot.orchestrator_commands`` so that both the Telegram bot
and the REST API can reuse the same logic for reading goals, health reports,
vision milestones, and runner state.

All functions are **synchronous** (file I/O only) and safe to call from
async contexts via the default thread-pool executor.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_HOME = Path.home()

ORCH_DIR = _HOME / ".claude" / "orchestrator"
LOG_DIR = _HOME / ".claude" / "logs"
SCRIPTS_DIR = _HOME / ".claude" / "scripts"

HEALTH_REPORT_FILE = ORCH_DIR / "health-report.yaml"
VISION_FILE = ORCH_DIR / "vision.md"
PID_FILE = ORCH_DIR / "runner.pid"
_RUNNER_EXT = ".ps1" if sys.platform == "win32" else ".sh"
RUNNER_SCRIPT = SCRIPTS_DIR / f"orchestrator-runner{_RUNNER_EXT}"


# ---------------------------------------------------------------------------
# Runner state
# ---------------------------------------------------------------------------


def is_runner_alive() -> tuple[bool, int | None]:
    """Check if the orchestrator runner process is alive.

    Returns:
        Tuple of ``(alive, pid)``.
    """
    if not PID_FILE.exists():
        return False, None

    try:
        pid = int(PID_FILE.read_text().strip())
    except (ValueError, OSError):
        return False, None

    # Check if process exists (Windows-compatible)
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        SYNCHRONIZE = 0x00100000
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True, pid
        return False, pid
    except (AttributeError, OSError):
        # Fallback: try os.kill with signal 0 (Unix)
        try:
            os.kill(pid, 0)
            # os.kill(0) succeeds for zombie (defunct) processes too —
            # check via `ps` to filter them out.
            import subprocess as _sp

            stat = _sp.run(
                ["ps", "-p", str(pid), "-o", "stat="],
                capture_output=True,
                text=True,
            ).stdout.strip()
            if stat.startswith("Z"):  # zombie
                return False, pid
            return True, pid
        except (OSError, ProcessLookupError):
            return False, pid


# ---------------------------------------------------------------------------
# File readers
# ---------------------------------------------------------------------------


def tail_file(path: Path, n: int = 10) -> list[str]:
    """Read the last *n* lines of a file."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return lines[-n:]
    except OSError:
        return []


def get_runner_log_path(date: str | None = None) -> Path | None:
    """Find a runner log file.

    Args:
        date: Optional ``YYYY-MM-DD`` string.  When *None*, the most recent
              log is returned.

    Returns:
        Path to the log file, or *None* if not found.
    """
    if not LOG_DIR.exists():
        return None

    if date:
        target = LOG_DIR / f"runner_{date}.log"
        return target if target.exists() else None

    logs = sorted(LOG_DIR.glob("runner_*.log"), reverse=True)
    return logs[0] if logs else None


# ---------------------------------------------------------------------------
# YAML / Markdown parsers
# ---------------------------------------------------------------------------


def parse_health_report(path: Path | None = None) -> dict[str, Any]:
    """Parse ``health-report.yaml`` into a dict."""
    fpath = path or HEALTH_REPORT_FILE
    if not fpath.exists():
        return {}

    content = fpath.read_text(encoding="utf-8")
    result: dict[str, Any] = {}
    dimensions: dict[str, dict[str, str]] = {}
    problems: list[dict[str, str]] = []
    current_dim: str | None = None
    section: str | None = None

    for line in content.splitlines():
        stripped = line.rstrip()

        if stripped.startswith("#"):
            continue

        if m := re.match(r"^overall_grade:\s+(.+)", stripped):
            result["overall_grade"] = m.group(1).strip()
        elif m := re.match(r"^score:\s+(\d+)", stripped):
            result["score"] = int(m.group(1))
        elif stripped.startswith("dimensions:"):
            section = "dimensions"
        elif stripped.startswith("problems_detected:"):
            section = "problems"
            current_dim = None
        elif stripped.startswith("problems_fixed_this_session:"):
            section = "fixed"
        elif stripped.startswith("improvement_areas:"):
            section = "improvements"

        elif section == "dimensions":
            if m := re.match(r"^\s{2}(\w+):\s*$", stripped):
                current_dim = m.group(1)
                dimensions[current_dim] = {}
            elif current_dim is not None:
                if m := re.match(r"^\s+grade:\s+(.+)", stripped):
                    dimensions[current_dim]["grade"] = m.group(1).strip()
                elif m := re.match(r'^\s+notes:\s+"(.+)"', stripped):
                    dimensions[current_dim]["notes"] = m.group(1)

        elif section == "problems":
            if m := re.match(r"^\s+-\s+severity:\s+(\w+)", stripped):
                problems.append({"severity": m.group(1)})
            elif problems and (m := re.match(r'^\s+description:\s+"(.+)"', stripped)):
                problems[-1]["description"] = m.group(1)

    result["dimensions"] = dimensions
    result["problems"] = problems
    return result


def parse_vision_md(path: Path | None = None) -> list[dict[str, Any]]:
    """Parse ``vision.md`` phases and milestones."""
    fpath = path or VISION_FILE
    if not fpath.exists():
        return []

    content = fpath.read_text(encoding="utf-8")
    phases: list[dict[str, Any]] = []
    current_phase: dict[str, Any] | None = None

    for line in content.splitlines():
        # Phase headers: ## Phase N — Title
        if m := re.match(
            r"^##\s+(?:Current Phase:\s+)?Phase\s+(\d+)\s*[—–-]\s*(.+?)(?:\s*\(FUTURE\))?\s*$",
            line,
        ):
            if current_phase is not None:
                phases.append(current_phase)
            current_phase = {
                "number": int(m.group(1)),
                "title": m.group(2).strip(),
                "milestones": [],
                "is_future": "FUTURE" in line,
            }

        # Milestones: - [x] or - [ ]
        elif current_phase is not None and (m := re.match(r"^-\s+\[([ xX])\]\s+(.+)$", line)):
            done = m.group(1).lower() == "x"
            current_phase["milestones"].append({"text": m.group(2).strip(), "done": done})

    if current_phase is not None:
        phases.append(current_phase)

    return phases
