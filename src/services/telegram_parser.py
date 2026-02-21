"""LLM-based parser for unstructured Telegram channel posts.

Classifies whether a post is a job listing and extracts structured fields
(title, description, budget, skills) using DeepSeek V3.2 via the shared
LLM client.
"""

from __future__ import annotations

from typing import Any

import structlog

from src.core.json_repair import extract_json
from src.core.llm_client import LLMClient

logger = structlog.get_logger(__name__)

# Minimum post length to consider (shorter posts are unlikely to be job listings).
_MIN_POST_LENGTH = 50

_PARSE_SYSTEM_PROMPT = """\
You are a freelance job post classifier and parser.
You receive a Telegram channel post and must determine if it is a freelance job/order.

Rules:
- News, discussions, advertisements for courses/tools, memes, and channel promotions are NOT jobs.
- A job post typically contains: a task description, required skills or technologies, and optionally a budget/deadline.
- Posts in any language (Russian, English, etc.) should be processed.

If the post IS a job listing, extract the following fields.
If the post is NOT a job listing, set is_job to false.

Respond with a single JSON object (no markdown fences):
{
  "is_job": true/false,
  "title": "short title summarizing the job (max 200 chars)",
  "description": "full job description as provided",
  "budget_min": null or number (in original currency),
  "budget_max": null or number (in original currency),
  "currency": "RUB" or "USD" or "EUR" or null,
  "skills": ["skill1", "skill2"] or [],
  "deadline": null or "YYYY-MM-DD"
}
"""


async def parse_telegram_post(
    text: str,
    channel_name: str,
    llm_client: LLMClient,
) -> dict[str, Any] | None:
    """Parse a Telegram post into a structured job dict using LLM.

    Parameters
    ----------
    text:
        Raw text of the Telegram message.
    channel_name:
        Channel username for logging context.
    llm_client:
        Shared LLM client instance.

    Returns
    -------
    A normalised job dict if the post is a job listing, or ``None`` if not.
    """
    if len(text.strip()) < _MIN_POST_LENGTH:
        logger.debug("telegram_parser.too_short", channel=channel_name, length=len(text))
        return None

    from langchain_core.messages import HumanMessage, SystemMessage  # noqa: PLC0415

    messages = [
        SystemMessage(content=_PARSE_SYSTEM_PROMPT),
        HumanMessage(content=f"Channel: {channel_name}\n\nPost:\n{text}"),
    ]

    try:
        response_msg, _metrics = await llm_client.call(
            agent_name="scout",
            messages=messages,
            temperature=0.1,
        )
    except Exception:
        logger.exception("telegram_parser.llm_call_failed", channel=channel_name)
        return None

    raw_text = str(response_msg.content)

    try:
        parsed = extract_json(raw_text, expected_type=dict)
    except ValueError:
        logger.warning("telegram_parser.json_parse_failed", raw_preview=raw_text[:300])
        return None

    if not isinstance(parsed, dict):
        return None

    if not parsed.get("is_job"):
        logger.debug("telegram_parser.not_a_job", channel=channel_name)
        return None

    return {
        "title": str(parsed.get("title", ""))[:500],
        "description": str(parsed.get("description", "")),
        "budget_min": _safe_float(parsed.get("budget_min")),
        "budget_max": _safe_float(parsed.get("budget_max")),
        "currency": str(parsed.get("currency", "RUB")) if parsed.get("currency") else "RUB",
        "skills": parsed.get("skills") or [],
        "deadline": parsed.get("deadline"),
    }


def _safe_float(value: Any) -> float | None:
    """Convert a value to float, returning None on failure."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
