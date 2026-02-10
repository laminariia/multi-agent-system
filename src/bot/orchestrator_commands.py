"""Orchestrator control commands for the Telegram bot.

Extends the existing HITL bot with 8 new commands for managing the
autonomous orchestrator runner from Telegram:
    /run, /stop, /orch, /goals, /health, /milestones, /logs, /add_goal

All command responses include inline keyboard buttons for quick navigation.
Callback data format: ``orch:<action>`` or ``orch:<action>:<arg>``.

File paths follow the orchestrator layout:
    - goals.yaml:       ~/.claude/orchestrator/goals.yaml
    - health-report:    ~/.claude/orchestrator/health-report.yaml
    - vision.md:        ~/.claude/orchestrator/vision.md
    - runner PID:       ~/.claude/orchestrator/runner.pid
    - runner logs:      ~/.claude/logs/runner_YYYY-MM-DD.log
    - runner script:    ~/.claude/scripts/orchestrator-runner.ps1
"""

from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_ORCH_DIR = Path(os.environ.get("USERPROFILE", os.path.expanduser("~"))) / ".claude" / "orchestrator"
_LOG_DIR = Path(os.environ.get("USERPROFILE", os.path.expanduser("~"))) / ".claude" / "logs"
_SCRIPTS_DIR = Path(os.environ.get("USERPROFILE", os.path.expanduser("~"))) / ".claude" / "scripts"

GOALS_FILE = _ORCH_DIR / "goals.yaml"
HEALTH_REPORT_FILE = _ORCH_DIR / "health-report.yaml"
VISION_FILE = _ORCH_DIR / "vision.md"
PID_FILE = _ORCH_DIR / "runner.pid"
RUNNER_SCRIPT = _SCRIPTS_DIR / "orchestrator-runner.ps1"


# ---------------------------------------------------------------------------
# Inline keyboards
# ---------------------------------------------------------------------------


def _kb_main_menu() -> InlineKeyboardMarkup:
    """Main orchestrator dashboard keyboard."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("\U0001f3af Цели", callback_data="orch:goals"),
            InlineKeyboardButton("\U0001f4ca Health", callback_data="orch:health"),
            InlineKeyboardButton("\U0001f3c1 Milestones", callback_data="orch:milestones"),
        ],
        [
            InlineKeyboardButton("\U0001f4dc Логи", callback_data="orch:logs"),
            InlineKeyboardButton("\U0001f504 Обновить", callback_data="orch:status"),
        ],
    ])


def _kb_main_menu_with_run() -> InlineKeyboardMarkup:
    """Main menu when runner is stopped — adds Run button."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("\U0001f680 Запустить", callback_data="orch:run"),
            InlineKeyboardButton("\U0001f3af Цели", callback_data="orch:goals"),
            InlineKeyboardButton("\U0001f4ca Health", callback_data="orch:health"),
        ],
        [
            InlineKeyboardButton("\U0001f3c1 Milestones", callback_data="orch:milestones"),
            InlineKeyboardButton("\U0001f4dc Логи", callback_data="orch:logs"),
        ],
    ])


def _kb_main_menu_with_stop() -> InlineKeyboardMarkup:
    """Main menu when runner is active — adds Stop button."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("\U0001f6d1 Остановить", callback_data="orch:stop"),
            InlineKeyboardButton("\U0001f3af Цели", callback_data="orch:goals"),
            InlineKeyboardButton("\U0001f4ca Health", callback_data="orch:health"),
        ],
        [
            InlineKeyboardButton("\U0001f3c1 Milestones", callback_data="orch:milestones"),
            InlineKeyboardButton("\U0001f4dc Логи", callback_data="orch:logs"),
        ],
    ])


def _kb_goals_nav() -> InlineKeyboardMarkup:
    """Navigation after /goals."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("\u23f3 Pending", callback_data="orch:goals:pending"),
            InlineKeyboardButton("\u2705 Done", callback_data="orch:goals:done"),
            InlineKeyboardButton("\U0001f4cb Все", callback_data="orch:goals"),
        ],
        [
            InlineKeyboardButton("\u2b05 Статус", callback_data="orch:status"),
        ],
    ])


def _kb_back_to_status() -> InlineKeyboardMarkup:
    """Simple 'back' keyboard."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("\u2b05 Статус", callback_data="orch:status"),
            InlineKeyboardButton("\U0001f3af Цели", callback_data="orch:goals"),
        ],
    ])


def _kb_logs_nav() -> InlineKeyboardMarkup:
    """Navigation for logs — show more or go back."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("\U0001f4dc +20 строк", callback_data="orch:logs:20"),
            InlineKeyboardButton("\U0001f4dc +50 строк", callback_data="orch:logs:50"),
        ],
        [
            InlineKeyboardButton("\u2b05 Статус", callback_data="orch:status"),
        ],
    ])


def _kb_after_run() -> InlineKeyboardMarkup:
    """Buttons after /run — check status or stop."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("\U0001f4ca Статус", callback_data="orch:status"),
            InlineKeyboardButton("\U0001f6d1 Остановить", callback_data="orch:stop"),
        ],
    ])


def _kb_after_stop() -> InlineKeyboardMarkup:
    """Buttons after /stop — check status or restart."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("\U0001f4ca Статус", callback_data="orch:status"),
            InlineKeyboardButton("\U0001f680 Запустить", callback_data="orch:run"),
        ],
    ])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _esc(text: str) -> str:
    """Escape special HTML characters for Telegram HTML parse mode."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _is_runner_alive() -> tuple[bool, int | None]:
    """Check if the orchestrator runner process is alive.

    Returns (alive, pid).
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
            return True, pid
        except (OSError, ProcessLookupError):
            return False, pid


def _parse_goals_yaml(path: Path | None = None) -> list[dict[str, Any]]:
    """Parse goals.yaml into a list of goal dicts."""
    fpath = path or GOALS_FILE
    if not fpath.exists():
        return []

    content = fpath.read_text(encoding="utf-8")
    goals: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for line in content.splitlines():
        stripped = line.rstrip()

        if re.match(r'^\s+-\s+id:\s+"?(.+?)"?\s*$', stripped):
            if current is not None:
                goals.append(current)
            match = re.match(r'^\s+-\s+id:\s+"?(.+?)"?\s*$', stripped)
            current = {"id": match.group(1) if match else "", "success_criteria": []}  # type: ignore[union-attr]

        elif current is not None:
            if m := re.match(r'^\s+title:\s+"(.+)"', stripped):
                current["title"] = m.group(1)
            elif m := re.match(r"^\s+priority:\s+(\w+)", stripped):
                current["priority"] = m.group(1)
            elif m := re.match(r"^\s+category:\s+(\w+)", stripped):
                current["category"] = m.group(1)
            elif m := re.match(r"^\s+status:\s+(\w+)", stripped):
                current["status"] = m.group(1)
            elif m := re.match(r'^\s+completed_at:\s+"?(.+?)"?\s*$', stripped):
                current["completed_at"] = m.group(1)
            elif m := re.match(r'^\s+result:\s+"(.+)"', stripped):
                current["result"] = m.group(1)

    if current is not None:
        goals.append(current)

    return goals


def _parse_health_report(path: Path | None = None) -> dict[str, Any]:
    """Parse health-report.yaml into a dict."""
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
                if m := re.match(r'^\s+grade:\s+(.+)', stripped):
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


def _parse_vision_md(path: Path | None = None) -> list[dict[str, Any]]:
    """Parse vision.md phases and milestones."""
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
        elif current_phase is not None and (
            m := re.match(r"^-\s+\[([ xX])\]\s+(.+)$", line)
        ):
            done = m.group(1).lower() == "x"
            current_phase["milestones"].append({"text": m.group(2).strip(), "done": done})

    if current_phase is not None:
        phases.append(current_phase)

    return phases


def _get_runner_log_path() -> Path | None:
    """Find the most recent runner log file."""
    if not _LOG_DIR.exists():
        return None

    logs = sorted(_LOG_DIR.glob("runner_*.log"), reverse=True)
    return logs[0] if logs else None


def _tail_file(path: Path, n: int = 10) -> list[str]:
    """Read the last N lines of a file."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return lines[-n:]
    except OSError:
        return []


def _priority_emoji(priority: str) -> str:
    """Map priority to an emoji indicator."""
    if priority in ("critical", "high"):
        return "\U0001f534"  # red circle
    if priority == "medium":
        return "\U0001f7e1"  # yellow circle
    return "\U0001f7e2"  # green circle


def _format_runner_log_line(line: str) -> str:
    """Shorten a runner log line: strip date, keep time+level+msg."""
    # Format: [2026-02-09 06:37:12] [WARN] message
    if m := re.match(
        r"\[\d{4}-\d{2}-\d{2}\s+(\d{2}:\d{2}):\d{2}\]\s+\[(\w+)\]\s+(.+)", line
    ):
        return f"{m.group(1)} [{m.group(2)}] {m.group(3)}"
    return line


# ---------------------------------------------------------------------------
# Text builders (shared by commands and callbacks)
# ---------------------------------------------------------------------------


def _build_orch_text() -> tuple[str, InlineKeyboardMarkup]:
    """Build the /orch status text and appropriate keyboard."""
    alive, pid = _is_runner_alive()

    goals = _parse_goals_yaml()
    pending = sum(1 for g in goals if g.get("status") == "pending")
    completed = sum(1 for g in goals if g.get("status") == "completed")
    failed = sum(1 for g in goals if g.get("status") == "failed")

    health = _parse_health_report()
    grade = health.get("overall_grade", "?")
    score = health.get("score", "?")

    if alive:
        log_path = _get_runner_log_path()
        session_info = ""
        errors_info = "0"
        if log_path:
            lines = _tail_file(log_path, 100)
            session_count = 0
            for line in lines:
                if m := re.search(r"Session (\d+)", line):
                    session_count = max(session_count, int(m.group(1)))
                if "Consecutive errors:" in line:
                    if m := re.search(r"Consecutive errors:\s*(\d+)", line):
                        errors_info = m.group(1)
            session_info = (
                f"\U0001f4ca Сессия: {session_count}"
                f" | Ошибки подряд: {errors_info}\n"
            )

        text = (
            f"\U0001f7e2 Оркестратор <b>РАБОТАЕТ</b> (PID: {pid})\n"
            f"{session_info}"
            f"\U0001f3af Pending: {pending}"
            f" | Completed: {completed}"
            f" | Failed: {failed}\n"
            f"\U0001f4c8 Health: {_esc(str(grade))} ({score})"
        )
        keyboard = _kb_main_menu_with_stop()
    else:
        log_path = _get_runner_log_path()
        last_run_info = ""
        if log_path:
            lines = _tail_file(log_path, 20)
            for line in reversed(lines):
                if "Runner finished" in line or "Runner started" in line:
                    if m := re.match(
                        r"\[(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})", line
                    ):
                        last_run_info = (
                            f"\U0001f4c5 Последний запуск: {m.group(1)}\n"
                        )
                    break

        text = (
            f"\U0001f534 Оркестратор <b>ОСТАНОВЛЕН</b>\n"
            f"{last_run_info}"
            f"\U0001f3af Pending: {pending} целей\n"
            f"\U0001f4c8 Health: {_esc(str(grade))} ({score})"
        )
        keyboard = _kb_main_menu_with_run()

    return text, keyboard


def _build_goals_text(filter_status: str | None = None) -> str:
    """Build the /goals text."""
    goals = _parse_goals_yaml()

    if not goals:
        return "Файл goals.yaml не найден или пуст."

    pending = [g for g in goals if g.get("status") == "pending"]
    done = [g for g in goals if g.get("status") in ("completed", "skipped")]
    failed_goals = [g for g in goals if g.get("status") == "failed"]

    lines: list[str] = [
        f"\U0001f4cb Цели ({len(pending)} pending,"
        f" {len(done)} done, {len(failed_goals)} failed)\n"
    ]

    if filter_status != "done":
        if pending:
            lines.append("\u23f3 <b>PENDING:</b>")
            for g in pending:
                emoji = _priority_emoji(g.get("priority", "low"))
                lines.append(
                    f"  {emoji} {_esc(g['id'])}:"
                    f" {_esc(g.get('title', '?'))}"
                    f" [{g.get('priority', '?')}]"
                )
            lines.append("")

    if filter_status != "pending":
        if done:
            show = done if filter_status == "done" else done[-5:]
            lines.append(f"\u2705 <b>COMPLETED</b> (последние {len(show)}):")
            for g in show:
                ca = g.get("completed_at", "")
                date_str = ca[:10] if ca else ""
                date_display = f" [{date_str}]" if date_str else ""
                lines.append(
                    f"  \u2705 {_esc(g['id'])}:"
                    f" {_esc(g.get('title', '?'))}{date_display}"
                )
            lines.append("")

    if failed_goals and filter_status not in ("pending", "done"):
        lines.append("\u274c <b>FAILED:</b>")
        for g in failed_goals:
            lines.append(
                f"  \u274c {_esc(g['id'])}:"
                f" {_esc(g.get('title', '?'))}"
            )

    return "\n".join(lines)


def _build_health_text() -> str:
    """Build the /health text."""
    report = _parse_health_report()

    if not report:
        return "health-report.yaml не найден."

    grade = report.get("overall_grade", "?")
    score = report.get("score", "?")
    dims = report.get("dimensions", {})

    dim_labels = {
        "code_completeness": "Code",
        "test_coverage": "Tests",
        "infrastructure": "Infra",
        "ci_cd": "CI/CD",
        "documentation": "Docs",
        "api": "API",
        "security": "Security",
        "code_quality": "Quality",
    }

    dim_items = list(dims.items())
    table_lines: list[str] = []
    for i in range(0, len(dim_items), 2):
        left_key, left_val = dim_items[i]
        left_label = dim_labels.get(left_key, left_key)
        left_grade = left_val.get("grade", "?")
        col = f"{left_label:>8}: {left_grade:<3}"

        if i + 1 < len(dim_items):
            right_key, right_val = dim_items[i + 1]
            right_label = dim_labels.get(right_key, right_key)
            right_grade = right_val.get("grade", "?")
            col += f" \u2502 {right_label:>8}: {right_grade:<3}"

        table_lines.append(col)

    table = "\n".join(table_lines)

    problems = report.get("problems", [])
    problem_lines = ""
    if problems:
        severity_emoji = {
            "info": "\u2139\ufe0f",
            "low": "\u26a0\ufe0f",
            "medium": "\u274c",
            "high": "\U0001f6a8",
        }
        p_items = []
        for p in problems:
            emoji = severity_emoji.get(p.get("severity", "info"), "\u2753")
            desc = p.get("description", "")[:100]
            p_items.append(f"  {emoji} {_esc(desc)}")
        problem_lines = (
            f"\n\u26a0\ufe0f Проблемы ({len(problems)}):\n"
            + "\n".join(p_items)
        )

    return (
        f"\U0001f4ca <b>Health Report \u2014"
        f" {_esc(str(grade))} ({score})</b>\n\n"
        f"<pre>{_esc(table)}</pre>"
        f"{problem_lines}"
    )


def _build_milestones_text(show_all: bool = False) -> str:
    """Build the /milestones text."""
    phases = _parse_vision_md()

    if not phases:
        return "vision.md не найден или пуст."

    lines: list[str] = []

    for phase in phases:
        milestones = phase["milestones"]
        if not milestones and not show_all:
            continue

        done_count = sum(1 for m in milestones if m["done"])
        total = len(milestones)
        status = (
            "\u2705 COMPLETE"
            if done_count == total and total > 0
            else f"{done_count}/{total}"
        )

        if not show_all and phase.get("is_future") and done_count == 0:
            lines.append(
                f"\n\U0001f4cb Phase {phase['number']}"
                f" \u2014 {_esc(phase['title'])} (NEXT)"
            )
            for m in milestones:
                lines.append(f"  \u2610 {_esc(m['text'])}")
            continue

        lines.append(
            f"\n\U0001f3af <b>Phase {phase['number']}</b>"
            f" \u2014 {_esc(phase['title'])}\n"
            f"Progress: {status}"
        )

        for m in milestones:
            marker = "\u2705" if m["done"] else "\u2610"
            lines.append(f"  {marker} {_esc(m['text'])}")

    return "\n".join(lines) if lines else "Нет фаз/milestones."


def _build_logs_text(n: int = 10) -> str:
    """Build the /logs text."""
    log_path = _get_runner_log_path()
    if not log_path or not log_path.exists():
        return "\U0001f4dc Лог runner'а не найден."

    raw_lines = _tail_file(log_path, n)
    formatted = [_format_runner_log_line(line) for line in raw_lines]

    return (
        f"\U0001f4dc <b>Runner Log</b>"
        f" (последние {len(formatted)}):\n\n"
        f"<pre>{_esc(chr(10).join(formatted))}</pre>"
    )


# ---------------------------------------------------------------------------
# Callback handler for orchestrator buttons
# ---------------------------------------------------------------------------


async def orch_button_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """Handle ``orch:*`` callback queries.

    Returns True if handled, False if the callback wasn't for us.
    """
    query = update.callback_query
    if query is None:
        return False

    data = query.data or ""
    if not data.startswith("orch:"):
        return False

    await query.answer()

    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    arg = parts[2] if len(parts) > 2 else None

    if action == "status":
        text, keyboard = _build_orch_text()
        await query.edit_message_text(
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )

    elif action == "goals":
        text = _build_goals_text(filter_status=arg)
        await query.edit_message_text(
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=_kb_goals_nav(),
        )

    elif action == "health":
        text = _build_health_text()
        await query.edit_message_text(
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=_kb_back_to_status(),
        )

    elif action == "milestones":
        text = _build_milestones_text(show_all=arg == "all")
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "\U0001f4cb Все фазы",
                    callback_data="orch:milestones:all",
                ),
                InlineKeyboardButton(
                    "\u2b05 Статус", callback_data="orch:status"
                ),
            ],
        ])
        await query.edit_message_text(
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )

    elif action == "logs":
        n = int(arg) if arg and arg.isdigit() else 10
        text = _build_logs_text(n)
        await query.edit_message_text(
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=_kb_logs_nav(),
        )

    elif action == "run":
        # Quick-run with defaults (12h, 60min)
        alive, _ = _is_runner_alive()
        if alive:
            text = "\U0001f7e2 Оркестратор уже запущен."
            await query.edit_message_text(
                text=text, reply_markup=_kb_main_menu_with_stop()
            )
        elif not RUNNER_SCRIPT.exists():
            await query.edit_message_text(
                text=f"\u274c Runner script не найден: {_esc(str(RUNNER_SCRIPT))}",
                parse_mode=ParseMode.HTML,
                reply_markup=_kb_back_to_status(),
            )
        else:
            goals = _parse_goals_yaml()
            pending_count = sum(
                1 for g in goals if g.get("status") == "pending"
            )
            cmd = [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy", "Bypass",
                "-File", str(RUNNER_SCRIPT),
                "-Mode", "self-direct",
            ]
            try:
                proc = subprocess.Popen(  # noqa: S603
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=(
                        subprocess.CREATE_NO_WINDOW
                        | subprocess.DETACHED_PROCESS
                    ),
                )
                _ORCH_DIR.mkdir(parents=True, exist_ok=True)
                PID_FILE.write_text(str(proc.pid))
                text = (
                    f"\U0001f680 Оркестратор запущен\n"
                    f"\u23f1 Режим: self-direct (12ч, 60мин/сессия)\n"
                    f"\U0001f916 Модель: opus\n"
                    f"\U0001f4ca Pending: {pending_count} целей"
                )
                await query.edit_message_text(
                    text=text, reply_markup=_kb_after_run()
                )
            except OSError as exc:
                await query.edit_message_text(
                    text=f"\u274c Ошибка запуска: {_esc(str(exc))}",
                    parse_mode=ParseMode.HTML,
                    reply_markup=_kb_back_to_status(),
                )

    elif action == "stop":
        alive, pid = _is_runner_alive()
        if not alive:
            text = "\U0001f534 Оркестратор не запущен."
            await query.edit_message_text(
                text=text, reply_markup=_kb_main_menu_with_run()
            )
        else:
            try:
                subprocess.run(  # noqa: S603
                    ["taskkill", "/PID", str(pid), "/T", "/F"],  # noqa: S607
                    capture_output=True,
                    timeout=10,
                )
            except (subprocess.SubprocessError, OSError):
                pass
            PID_FILE.unlink(missing_ok=True)
            goals = _parse_goals_yaml()
            remaining = sum(
                1 for g in goals if g.get("status") == "pending"
            )
            text = (
                f"\U0001f6d1 Оркестратор остановлен\n"
                f"\U0001f4ca Remaining: {remaining} целей"
            )
            await query.edit_message_text(
                text=text, reply_markup=_kb_after_stop()
            )

    else:
        await query.edit_message_text(text=f"Unknown action: {_esc(action)}")

    return True


# ---------------------------------------------------------------------------
# /run — Start orchestrator
# ---------------------------------------------------------------------------


async def run_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Start the orchestrator runner as a background process."""
    alive, _ = _is_runner_alive()
    if alive:
        await update.effective_message.reply_text(  # type: ignore[union-attr]
            "\U0001f7e2 Оркестратор уже запущен."
            " Используй /stop чтобы остановить.",
            reply_markup=_kb_main_menu_with_stop(),
        )
        return

    if not RUNNER_SCRIPT.exists():
        await update.effective_message.reply_text(  # type: ignore[union-attr]
            f"\u274c Runner script не найден: {_esc(str(RUNNER_SCRIPT))}",
            parse_mode=ParseMode.HTML,
        )
        return

    # Parse arguments: /run [hours] [session_minutes]
    args = context.args or []
    total_hours = 12
    session_minutes = 60
    try:
        if len(args) >= 1:
            total_hours = int(args[0])
        if len(args) >= 2:
            session_minutes = int(args[1])
    except ValueError:
        await update.effective_message.reply_text(  # type: ignore[union-attr]
            "Usage: /run [hours] [session_minutes]\nExample: /run 4 90",
        )
        return

    goals = _parse_goals_yaml()
    pending_count = sum(1 for g in goals if g.get("status") == "pending")

    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", str(RUNNER_SCRIPT),
        "-Mode", "self-direct",
        "-TotalRunTimeHours", str(total_hours),
        "-SessionTimeoutMinutes", str(session_minutes),
    ]

    try:
        proc = subprocess.Popen(  # noqa: S603
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=(
                subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
            ),
        )
        _ORCH_DIR.mkdir(parents=True, exist_ok=True)
        PID_FILE.write_text(str(proc.pid))

        logger.info(
            "orchestrator.started",
            pid=proc.pid,
            hours=total_hours,
            session_min=session_minutes,
        )

        await update.effective_message.reply_text(  # type: ignore[union-attr]
            f"\U0001f680 Оркестратор запущен\n"
            f"\u23f1 Режим: self-direct"
            f" ({total_hours}ч, {session_minutes}мин/сессия)\n"
            f"\U0001f916 Модель: opus\n"
            f"\U0001f4ca Pending: {pending_count} целей",
            reply_markup=_kb_after_run(),
        )
    except OSError as exc:
        logger.error("orchestrator.start_failed", error=str(exc))
        await update.effective_message.reply_text(  # type: ignore[union-attr]
            f"\u274c Ошибка запуска: {_esc(str(exc))}",
            parse_mode=ParseMode.HTML,
        )


# ---------------------------------------------------------------------------
# /stop — Stop orchestrator
# ---------------------------------------------------------------------------


async def stop_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Stop the running orchestrator runner."""
    alive, pid = _is_runner_alive()
    if not alive:
        await update.effective_message.reply_text(  # type: ignore[union-attr]
            "\U0001f534 Оркестратор не запущен.",
            reply_markup=_kb_main_menu_with_run(),
        )
        if PID_FILE.exists():
            PID_FILE.unlink(missing_ok=True)
        return

    try:
        subprocess.run(  # noqa: S603
            ["taskkill", "/PID", str(pid), "/T", "/F"],  # noqa: S607
            capture_output=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        logger.error("orchestrator.stop_failed", error=str(exc), pid=pid)
        await update.effective_message.reply_text(  # type: ignore[union-attr]
            f"\u274c Ошибка остановки (PID {pid}): {_esc(str(exc))}",
            parse_mode=ParseMode.HTML,
        )
        return

    PID_FILE.unlink(missing_ok=True)

    log_path = _get_runner_log_path()
    session_count = 0
    if log_path:
        for line in _tail_file(log_path, 50):
            if m := re.search(r"Session (\d+)", line):
                session_count = max(session_count, int(m.group(1)))

    goals = _parse_goals_yaml()
    remaining = sum(1 for g in goals if g.get("status") == "pending")

    logger.info("orchestrator.stopped", pid=pid)
    await update.effective_message.reply_text(  # type: ignore[union-attr]
        f"\U0001f6d1 Оркестратор остановлен\n"
        f"\u2705 Сессий выполнено: {session_count}\n"
        f"\U0001f4ca Remaining: {remaining} целей",
        reply_markup=_kb_after_stop(),
    )


# ---------------------------------------------------------------------------
# /orch — Orchestrator status (main dashboard)
# ---------------------------------------------------------------------------


async def orch_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show orchestrator runner status with navigation buttons."""
    text, keyboard = _build_orch_text()
    await update.effective_message.reply_text(  # type: ignore[union-attr]
        text, parse_mode=ParseMode.HTML, reply_markup=keyboard
    )


# ---------------------------------------------------------------------------
# /goals — List goals
# ---------------------------------------------------------------------------


async def goals_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show goals from goals.yaml, grouped by status."""
    args = context.args or []
    filter_status = args[0].lower() if args else None
    text = _build_goals_text(filter_status=filter_status)
    await update.effective_message.reply_text(  # type: ignore[union-attr]
        text, parse_mode=ParseMode.HTML, reply_markup=_kb_goals_nav()
    )


# ---------------------------------------------------------------------------
# /health — Health report
# ---------------------------------------------------------------------------


async def health_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show the health report with dimension grades."""
    text = _build_health_text()
    await update.effective_message.reply_text(  # type: ignore[union-attr]
        text, parse_mode=ParseMode.HTML, reply_markup=_kb_back_to_status()
    )


# ---------------------------------------------------------------------------
# /milestones — Vision milestones
# ---------------------------------------------------------------------------


async def milestones_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show milestone progress from vision.md."""
    args = context.args or []
    show_all = bool(args and args[0].lower() == "all")
    text = _build_milestones_text(show_all=show_all)
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "\U0001f4cb Все фазы",
                callback_data="orch:milestones:all",
            ),
            InlineKeyboardButton(
                "\u2b05 Статус", callback_data="orch:status"
            ),
        ],
    ])
    await update.effective_message.reply_text(  # type: ignore[union-attr]
        text, parse_mode=ParseMode.HTML, reply_markup=kb
    )


# ---------------------------------------------------------------------------
# /logs — Runner log tail
# ---------------------------------------------------------------------------


async def logs_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show the last N lines of the runner log."""
    args = context.args or []
    n = 10
    if args:
        try:
            n = min(int(args[0]), 50)
        except ValueError:
            pass

    text = _build_logs_text(n)
    await update.effective_message.reply_text(  # type: ignore[union-attr]
        text, parse_mode=ParseMode.HTML, reply_markup=_kb_logs_nav()
    )


# ---------------------------------------------------------------------------
# /add_goal — Add a goal manually
# ---------------------------------------------------------------------------


async def add_goal_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Append a new goal to goals.yaml."""
    args = context.args or []
    if not args:
        await update.effective_message.reply_text(  # type: ignore[union-attr]
            "Usage: /add_goal <title>\n"
            "Example: /add_goal Fix login page CSS bug",
        )
        return

    title = " ".join(args)

    goals = _parse_goals_yaml()
    max_num = 0
    for g in goals:
        if m := re.match(r"g_(\d+)", g.get("id", "")):
            max_num = max(max_num, int(m.group(1)))
    new_id = f"g_{max_num + 1:03d}"

    today = datetime.now().strftime("%Y-%m-%d")

    yaml_block = f"""
  - id: {new_id}
    title: "{title}"
    priority: medium
    category: feature
    parallelizable: true
    team_size: 1
    context: "Manually added via Telegram bot"
    success_criteria:
      - "Task completed successfully"
    status: pending
    depends_on: []
    created: "{today}"
"""

    try:
        if not GOALS_FILE.exists():
            GOALS_FILE.parent.mkdir(parents=True, exist_ok=True)
            GOALS_FILE.write_text(f"goals:{yaml_block}", encoding="utf-8")
        else:
            with GOALS_FILE.open("a", encoding="utf-8") as f:
                f.write(yaml_block)

        logger.info("orchestrator.goal_added", goal_id=new_id, title=title)

        await update.effective_message.reply_text(  # type: ignore[union-attr]
            f"\u2705 Цель добавлена: <b>{_esc(new_id)}</b>\n"
            f"\U0001f4cb \"{_esc(title)}\"\n"
            f"\U0001f7e1 Priority: medium | Status: pending",
            parse_mode=ParseMode.HTML,
            reply_markup=_kb_goals_nav(),
        )
    except OSError as exc:
        await update.effective_message.reply_text(  # type: ignore[union-attr]
            f"\u274c Ошибка записи: {_esc(str(exc))}",
            parse_mode=ParseMode.HTML,
        )
