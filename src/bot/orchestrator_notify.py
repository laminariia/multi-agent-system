"""Push notifications from the orchestrator runner to Telegram.

This module is designed to be invoked as a standalone script by the
PowerShell orchestrator-runner.ps1 after each session.  It uses
``httpx`` directly (like ``notifications.py``) and does NOT depend on
python-telegram-bot or the rest of the application.

Usage from PowerShell::

    python -m src.bot.orchestrator_notify --event session_complete --data '{"session":3,"duration_min":42}'
    python -m src.bot.orchestrator_notify --event critical_error --data '{"consecutive":3,"exit_code":1}'
    python -m src.bot.orchestrator_notify --event all_goals_done --data '{"phase":2,"milestones":5}'

Environment variables required:
    TELEGRAM_BOT_TOKEN  — bot token
    TELEGRAM_CHAT_ID    — chat to notify
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

import httpx

TELEGRAM_API = "https://api.telegram.org"


def _get_env(name: str) -> str:
    """Get a required environment variable."""
    val = os.environ.get(name, "")
    if not val:
        # Try loading from .env in project dir
        env_path = os.path.join(
            os.path.dirname(__file__), "..", "..", ".env"
        )
        if os.path.exists(env_path):
            with open(env_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith(f"{name}="):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
    return val


async def send_telegram_message(token: str, chat_id: str, text: str) -> bool:
    """Send a message via the Telegram Bot API."""
    url = f"{TELEGRAM_API}/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return bool(data.get("ok"))
    except httpx.HTTPError as exc:
        print(f"[orchestrator_notify] Telegram error: {exc}", file=sys.stderr)
        return False


def format_session_complete(data: dict[str, Any]) -> str:
    """Format a 'session complete' notification."""
    session = data.get("session", "?")
    duration = data.get("duration_min", "?")
    goals_completed = data.get("goals_completed", 0)
    health_before = data.get("health_before", "")
    health_after = data.get("health_after", "")
    commits = data.get("commits", 0)

    lines = [
        f"\u2705 Сессия #{session} завершена ({duration}мин)",
    ]
    if goals_completed:
        lines.append(f"\U0001f4ca Goals: +{goals_completed} completed")
    if health_before and health_after:
        lines.append(f"\U0001f4c8 Health: {health_before} \u2192 {health_after}")
    if commits:
        lines.append(f"\U0001f500 Pushed to dev: {commits} commits")

    return "\n".join(lines)


def format_critical_error(data: dict[str, Any]) -> str:
    """Format a 'critical error' notification."""
    consecutive = data.get("consecutive", 3)
    exit_code = data.get("exit_code", 1)
    last_error = data.get("last_error", "")

    lines = [
        f"\U0001f6a8 Оркестратор: {consecutive} ошибки подряд!",
        f"Последняя: exit code {exit_code}",
    ]
    if last_error:
        lines.append(f"Ошибка: {last_error[:200]}")
    lines.append("\u23f8\ufe0f Остановлен автоматически")

    return "\n".join(lines)


def format_all_goals_done(data: dict[str, Any]) -> str:
    """Format an 'all goals done' notification."""
    phase = data.get("phase", "?")
    milestones = data.get("milestones", "?")

    return (
        f"\U0001f389 Все цели фазы выполнены!\n"
        f"\U0001f4ca Phase {phase}: {milestones} milestones \u2705\n"
        f"\U0001f4cb Pending: 0 целей\n"
        f"\U0001f4a1 Запусти /run для re-analysis"
    )


_FORMATTERS = {
    "session_complete": format_session_complete,
    "critical_error": format_critical_error,
    "all_goals_done": format_all_goals_done,
}


async def notify(event: str, data: dict[str, Any]) -> bool:
    """Send an orchestrator notification to Telegram."""
    token = _get_env("TELEGRAM_BOT_TOKEN")
    chat_id = _get_env("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print(
            "[orchestrator_notify] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set",
            file=sys.stderr,
        )
        return False

    formatter = _FORMATTERS.get(event)
    if not formatter:
        print(f"[orchestrator_notify] Unknown event: {event}", file=sys.stderr)
        return False

    text = formatter(data)
    return await send_telegram_message(token, chat_id, text)


def main() -> None:
    """CLI entry point for orchestrator notifications."""
    parser = argparse.ArgumentParser(description="Send orchestrator notification to Telegram")
    parser.add_argument("--event", required=True, choices=list(_FORMATTERS.keys()))
    parser.add_argument("--data", default="{}", help="JSON string with event data")
    args = parser.parse_args()

    try:
        data = json.loads(args.data)
    except json.JSONDecodeError as exc:
        print(f"Invalid JSON data: {exc}", file=sys.stderr)
        sys.exit(1)

    success = asyncio.run(notify(args.event, data))
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
