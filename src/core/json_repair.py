"""JSON extraction and repair for LLM responses.

LLMs frequently return JSON wrapped in markdown fences, with extra commentary
text, trailing commas, single quotes, or other minor deviations from strict
JSON.  This module provides progressive repair strategies so that every agent
can survive real-world LLM output without manual cleanup.

The primary entry points are:

* :func:`extract_json` -- best-effort JSON extraction from raw text.
* :func:`extract_and_validate` -- extract + Pydantic v2 validation.
"""

from __future__ import annotations

import json
import re
from typing import Any, TypeVar

import structlog
from pydantic import BaseModel

logger = structlog.get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

# Pre-compiled patterns
_FENCE_RE = re.compile(
    r"```(?:json|python|javascript|js|text|plaintext)?\s*\n?(.*?)```",
    re.DOTALL,
)
_JSON_OBJ_RE = re.compile(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", re.DOTALL)
_JSON_DEEP_OBJ_RE = re.compile(r"\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\}", re.DOTALL)
_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r",\s*([\]}])")
_SINGLE_QUOTE_RE = re.compile(r"(?<=[\[{,:\s])'((?:[^'\\]|\\.)*)'(?=\s*[,\]}:])")


def extract_json(raw: str, expected_type: type = dict) -> Any:
    """Extract JSON from an LLM response with progressive repair strategies.

    Strategy chain:
    1. Direct ``json.loads`` (fast path for clean responses).
    2. Strip markdown code fences (````json ... ``` `` or ```` ... ``` ``).
    3. Regex-extract the first JSON object or array.
    4. Repair heuristics (trailing commas, single quotes).

    Parameters
    ----------
    raw:
        The raw LLM response text.
    expected_type:
        The expected top-level Python type (``dict`` or ``list``).
        Used to guide extraction when multiple candidates exist.

    Returns
    -------
    Any
        The parsed JSON value (dict, list, or primitive).

    Raises
    ------
    ValueError
        When all strategies fail to produce valid JSON.
    """
    if not raw or not raw.strip():
        raise ValueError("Empty response: cannot extract JSON")

    text = raw.strip()

    # Strategy 1: direct parse
    try:
        result = json.loads(text)
        if _type_ok(result, expected_type):
            return result
    except json.JSONDecodeError:
        pass

    # Strategy 2: strip markdown code fences
    fenced = _strip_fences(text)
    if fenced != text:
        try:
            result = json.loads(fenced)
            if _type_ok(result, expected_type):
                return result
        except json.JSONDecodeError:
            pass

    # Strategy 3: regex extract first JSON object/array
    candidate = _regex_extract(text, expected_type)
    if candidate is not None:
        try:
            result = json.loads(candidate)
            if _type_ok(result, expected_type):
                return result
        except json.JSONDecodeError:
            # Try repair on the candidate
            repaired = _repair_json_text(candidate)
            try:
                result = json.loads(repaired)
                if _type_ok(result, expected_type):
                    return result
            except json.JSONDecodeError:
                pass

    # Strategy 4: repair heuristics on the best available text
    for attempt_text in (fenced, text):
        repaired = _repair_json_text(attempt_text)
        try:
            result = json.loads(repaired)
            if _type_ok(result, expected_type):
                return result
        except json.JSONDecodeError:
            pass

    raise ValueError(
        f"Failed to extract JSON from LLM response (length={len(raw)}): "
        f"{raw[:200]!r}..."
    )


def extract_and_validate(raw: str, model: type[T]) -> T:  # noqa: UP047
    """Extract JSON from an LLM response and validate against a Pydantic model.

    Parameters
    ----------
    raw:
        The raw LLM response text.
    model:
        A Pydantic v2 ``BaseModel`` subclass to validate against.

    Returns
    -------
    T
        A validated instance of *model*.

    Raises
    ------
    ValueError
        When JSON extraction fails.
    ValidationError
        When the extracted data does not match the model schema.
    """
    data = extract_json(raw, expected_type=dict)
    return model.model_validate(data)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _type_ok(value: Any, expected_type: type) -> bool:
    """Check if the parsed value matches the expected top-level type."""
    if expected_type is dict:
        return isinstance(value, dict)
    if expected_type is list:
        return isinstance(value, list)
    return True


def _strip_fences(text: str) -> str:
    """Remove markdown code fences, returning the inner content."""
    match = _FENCE_RE.search(text)
    if match:
        return match.group(1).strip()

    # Fallback: simple ``` ... ``` without language tag
    if text.startswith("```"):
        lines = text.split("\n")
        # Skip first line (```json or ```)
        inner_lines = lines[1:]
        # Remove trailing ```
        if inner_lines and inner_lines[-1].strip() == "```":
            inner_lines = inner_lines[:-1]
        return "\n".join(inner_lines).strip()

    return text


def _regex_extract(text: str, expected_type: type) -> str | None:
    """Regex-extract the first JSON object or array from text."""
    if expected_type is list:
        match = _JSON_ARRAY_RE.search(text)
        if match:
            return match.group(0)

    # Try deep object match first for nested structures
    match = _JSON_DEEP_OBJ_RE.search(text)
    if match:
        return match.group(0)

    if expected_type is not list:
        # Also try array if dict failed
        match = _JSON_ARRAY_RE.search(text)
        if match:
            return match.group(0)

    return None


def _repair_json_text(text: str) -> str:
    """Apply repair heuristics to almost-valid JSON text."""
    repaired = text

    # Remove trailing commas before } or ]
    repaired = _TRAILING_COMMA_RE.sub(r"\1", repaired)

    # Replace single quotes with double quotes (best effort)
    repaired = _SINGLE_QUOTE_RE.sub(r'"\1"', repaired)

    # Fix unquoted keys: word: -> "word":
    # Only apply if the text still fails to parse
    try:
        json.loads(repaired)
        return repaired
    except json.JSONDecodeError:
        pass

    # Try replacing Python-style None/True/False
    repaired = repaired.replace(": None", ": null")
    repaired = repaired.replace(": True", ": true")
    repaired = repaired.replace(": False", ": false")
    repaired = repaired.replace(":None", ":null")
    repaired = repaired.replace(":True", ":true")
    repaired = repaired.replace(":False", ":false")

    return repaired
