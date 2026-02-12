"""Golden set evaluator -- property-based validation for LLM agent outputs.

Uses ``GoldenCase`` / ``ValidationRule`` dataclasses to declare expectations
and ``evaluate_case()`` to run all validators against a parsed output dict.

Supported validation rule types
-------------------------------
must_mention      keywords that must appear in text (case-insensitive)
must_not_mention  keywords that must NOT appear
length_range      word-count min/max of a text field
score_range       numeric field must be within [min, max]
has_keys          required top-level keys in the output dict
regex_match       a regex pattern must match somewhere in a field
json_valid        raw text must be parseable as JSON
value_equals      a field must equal an expected value
value_in          a field must be one of a set of values
list_non_empty    a list field must contain at least one element
pydantic_valid    marker -- actual Pydantic check happens at test level
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ValidationRule:
    """A single validation rule to check against an agent output."""

    rule_type: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class GoldenCase:
    """A golden set test case for one agent.

    ``expected_output`` is a *raw string* as an LLM might return it, possibly
    wrapped in markdown code fences.  Tests parse it via
    ``json_repair.extract_json()`` before validation.
    """

    name: str
    agent: str  # scout, bid, content, planner, critic, outreach
    input_data: dict[str, Any]
    expected_output: str  # raw LLM response string (may include markdown fences)
    validators: list[ValidationRule] = field(default_factory=list)


@dataclass
class EvalResult:
    """Result of evaluating a single golden case."""

    case_name: str
    passed: bool
    details: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def evaluate_case(case: GoldenCase, parsed_output: dict[str, Any]) -> EvalResult:
    """Run all validators against *parsed_output* and return an ``EvalResult``."""
    details: list[str] = []
    passed = True

    text_content = _extract_text(parsed_output)

    for rule in case.validators:
        rule_ok, msgs = _run_rule(rule, parsed_output, text_content)
        if not rule_ok:
            passed = False
        details.extend(msgs)

    return EvalResult(case_name=case.name, passed=passed, details=details)


# ---------------------------------------------------------------------------
# Rule dispatcher
# ---------------------------------------------------------------------------

def _run_rule(
    rule: ValidationRule,
    output: dict[str, Any],
    text: str,
) -> tuple[bool, list[str]]:
    """Dispatch one ``ValidationRule``.  Returns (ok, messages)."""
    t = rule.rule_type
    p = rule.params
    msgs: list[str] = []
    ok = True

    if t == "must_mention":
        for kw in p.get("keywords", []):
            if kw.lower() not in text.lower():
                ok = False
                msgs.append(f"Missing keyword: '{kw}'")

    elif t == "must_not_mention":
        for kw in p.get("keywords", []):
            if kw.lower() in text.lower():
                ok = False
                msgs.append(f"Forbidden keyword found: '{kw}'")

    elif t == "length_range":
        fld = p.get("field")
        src = str(output.get(fld, "")) if fld else text
        wc = len(src.split())
        lo, hi = p.get("min", 0), p.get("max", 999999)
        if not (lo <= wc <= hi):
            ok = False
            msgs.append(f"Word count {wc} outside [{lo}, {hi}]")

    elif t == "score_range":
        fld = p.get("field", "score")
        val = output.get(fld, 0)
        lo, hi = p.get("min", 0), p.get("max", 1.0)
        if not (lo <= val <= hi):
            ok = False
            msgs.append(f"{fld}={val} outside [{lo}, {hi}]")

    elif t == "has_keys":
        for key in p.get("keys", []):
            if key not in output or output[key] is None:
                ok = False
                msgs.append(f"Missing key: '{key}'")

    elif t == "regex_match":
        fld = p.get("field")
        pattern = p.get("pattern", "")
        src = str(output.get(fld, "")) if fld else text
        if not re.search(pattern, src, re.IGNORECASE):
            ok = False
            msgs.append(f"Regex '{pattern}' no match in '{fld or 'text'}'")

    elif t == "json_valid":
        raw = p.get("raw", json.dumps(output))
        try:
            json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            ok = False
            msgs.append("Raw text is not valid JSON")

    elif t == "pydantic_valid":
        pass  # marker -- checked at test level

    elif t == "value_equals":
        fld = p.get("field", "")
        expected = p.get("value")
        actual = output.get(fld)
        if actual != expected:
            ok = False
            msgs.append(f"Expected {fld}={expected!r}, got {actual!r}")

    elif t == "value_in":
        fld = p.get("field", "")
        allowed = p.get("values", [])
        actual = output.get(fld)
        if actual not in allowed:
            ok = False
            msgs.append(f"{fld}={actual!r} not in {allowed}")

    elif t == "list_non_empty":
        fld = p.get("field", "")
        val = output.get(fld, [])
        if not (isinstance(val, list) and len(val) > 0):
            ok = False
            msgs.append(f"Expected non-empty list for '{fld}'")

    else:
        ok = False
        msgs.append(f"Unknown rule type: {t}")

    return ok, msgs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_text(output: dict[str, Any]) -> str:
    """Concatenate the main text content for keyword / length checks."""
    for key in ("proposal_text", "body", "reasoning", "revision_instructions"):
        if key in output and output[key]:
            return str(output[key])

    if "deliverables" in output:
        parts = [
            str(d["content"])
            for d in output["deliverables"]
            if isinstance(d, dict) and "content" in d
        ]
        if parts:
            return " ".join(parts)

    return " ".join(
        str(v) for v in output.values() if isinstance(v, str) and len(v) > 15
    )
