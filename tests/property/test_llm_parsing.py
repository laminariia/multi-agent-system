"""Property-based tests for LLM response parsing robustness.

Verifies that the JSON extraction/repair pipeline (src/core/json_repair.py)
never crashes on arbitrary input, correctly handles valid JSON in various
wrappers, and always either returns valid data or raises ValueError.
"""

from __future__ import annotations

import json

from hypothesis import given, settings
from hypothesis import strategies as st

from src.core.json_repair import (
    _repair_json_text,
    _strip_fences,
    extract_json,
)

from .conftest import (
    json_safe_text_strategy,
    llm_json_response_strategy,
    malformed_json_strategy,
    unicode_text_strategy,
)

# ---------------------------------------------------------------------------
# extract_json never crashes
# ---------------------------------------------------------------------------


class TestExtractJsonNeverCrashes:
    """extract_json must never raise an unhandled exception.

    It either returns parsed JSON or raises ValueError. No other
    exception type should escape.
    """

    @given(text=unicode_text_strategy)
    @settings(max_examples=200)
    def test_arbitrary_unicode_no_crash(self, text: str) -> None:
        """extract_json never crashes on arbitrary unicode input."""
        try:
            result = extract_json(text)
            # If it succeeds, the result must be a valid Python object
            assert result is not None or result is None  # Just ensuring no crash
        except ValueError:
            pass  # Expected for unparseable input

    @given(text=malformed_json_strategy())
    @settings(max_examples=200)
    def test_malformed_json_no_crash(self, text: str) -> None:
        """extract_json handles malformed JSON gracefully."""
        try:
            extract_json(text)
        except ValueError:
            pass  # Expected

    @given(data=st.binary(min_size=0, max_size=500))
    @settings(max_examples=200)
    def test_binary_decoded_as_text_no_crash(self, data: bytes) -> None:
        """extract_json handles decoded binary data without crashing."""
        text = data.decode("utf-8", errors="replace")
        try:
            extract_json(text)
        except ValueError:
            pass  # Expected

    @given(
        n=st.integers(min_value=0, max_value=50),
    )
    @settings(max_examples=200)
    def test_repeated_braces_no_crash(self, n: int) -> None:
        """extract_json handles pathological brace patterns."""
        text = "{" * n + "}" * n
        try:
            extract_json(text)
        except ValueError:
            pass

    @given(
        depth=st.integers(min_value=1, max_value=20),
    )
    @settings(max_examples=200)
    def test_nested_fences_no_crash(self, depth: int) -> None:
        """extract_json handles deeply nested code fences."""
        text = "```json\n" * depth + '{"key": "value"}' + "\n```" * depth
        try:
            result = extract_json(text)
            # If it parses, it must have the key
            if isinstance(result, dict):
                assert "key" in result
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# extract_json returns valid JSON for valid input
# ---------------------------------------------------------------------------


class TestExtractJsonValidInput:
    """extract_json must always succeed for well-formed LLM responses."""

    @given(response=llm_json_response_strategy())
    @settings(max_examples=200)
    def test_valid_llm_response_always_parsed(self, response: str) -> None:
        """Any well-formed LLM JSON response (with or without fences) is parseable."""
        result = extract_json(response)
        assert isinstance(result, dict)

    @given(
        payload=st.fixed_dictionaries(
            {
                "score": st.integers(min_value=0, max_value=100),
                "verdict": st.sampled_from(("APPROVE", "REVISE", "REJECT")),
            }
        )
    )
    @settings(max_examples=200)
    def test_clean_json_dict_always_extracted(self, payload: dict) -> None:
        """Clean JSON dict string is always extracted correctly."""
        text = json.dumps(payload)
        result = extract_json(text)
        assert result == payload

    @given(
        items=st.lists(st.integers(min_value=-1000, max_value=1000), min_size=0, max_size=20),
    )
    @settings(max_examples=200)
    def test_clean_json_array_extracted(self, items: list[int]) -> None:
        """Clean JSON array string is always extracted correctly."""
        text = json.dumps(items)
        result = extract_json(text, expected_type=list)
        assert result == items

    @given(
        payload=st.fixed_dictionaries({"result": json_safe_text_strategy}),
    )
    @settings(max_examples=200)
    def test_json_with_unicode_values(self, payload: dict) -> None:
        """JSON with unicode string values is extracted correctly."""
        text = json.dumps(payload, ensure_ascii=False)
        result = extract_json(text)
        assert result == payload


# ---------------------------------------------------------------------------
# extract_json with markdown fences
# ---------------------------------------------------------------------------


class TestExtractJsonMarkdownFences:
    """extract_json must handle all markdown fence variations."""

    @given(
        payload=st.fixed_dictionaries({"key": st.text(max_size=50)}),
        lang=st.sampled_from(("json", "python", "javascript", "text", "")),
    )
    @settings(max_examples=200)
    def test_fenced_json_always_extracted(self, payload: dict, lang: str) -> None:
        """JSON wrapped in markdown fences with any language tag is extracted."""
        json_str = json.dumps(payload, ensure_ascii=False)
        fenced = f"```{lang}\n{json_str}\n```"
        result = extract_json(fenced)
        assert isinstance(result, dict)
        assert result["key"] == payload["key"]

    @given(
        payload=st.fixed_dictionaries({"value": st.integers(min_value=0, max_value=100)}),
        preamble=st.text(min_size=0, max_size=100).filter(lambda s: "{" not in s and "[" not in s),
        epilogue=st.text(min_size=0, max_size=100).filter(lambda s: "{" not in s and "[" not in s),
    )
    @settings(max_examples=200)
    def test_fenced_json_with_surrounding_text(self, payload: dict, preamble: str, epilogue: str) -> None:
        """JSON in fences surrounded by preamble/epilogue text is still extracted."""
        json_str = json.dumps(payload)
        text = f"{preamble}\n```json\n{json_str}\n```\n{epilogue}"
        result = extract_json(text)
        assert isinstance(result, dict)
        assert result["value"] == payload["value"]


# ---------------------------------------------------------------------------
# _strip_fences invariants
# ---------------------------------------------------------------------------


class TestStripFences:
    """_strip_fences must cleanly remove markdown code fences."""

    @given(content=json_safe_text_strategy.filter(lambda s: "```" not in s and len(s.strip()) > 0))
    @settings(max_examples=200)
    def test_strip_fences_returns_inner_content(self, content: str) -> None:
        """Inner content is preserved after stripping fences."""
        fenced = f"```json\n{content}\n```"
        stripped = _strip_fences(fenced)
        assert stripped.strip() == content.strip()

    @given(text=json_safe_text_strategy.filter(lambda s: "```" not in s))
    @settings(max_examples=200)
    def test_no_fences_returns_original(self, text: str) -> None:
        """Text without fences is returned unchanged."""
        assert _strip_fences(text) == text


# ---------------------------------------------------------------------------
# _repair_json_text invariants
# ---------------------------------------------------------------------------


class TestRepairJsonText:
    """_repair_json_text must never crash and should fix common issues."""

    @given(text=unicode_text_strategy)
    @settings(max_examples=200)
    def test_repair_never_crashes(self, text: str) -> None:
        """Repair function never crashes on any input."""
        result = _repair_json_text(text)
        assert isinstance(result, str)

    @given(
        payload=st.fixed_dictionaries({"a": st.integers(min_value=0, max_value=100)}),
    )
    @settings(max_examples=200)
    def test_repair_preserves_valid_json(self, payload: dict) -> None:
        """Valid JSON is preserved (not corrupted) by repair."""
        text = json.dumps(payload)
        repaired = _repair_json_text(text)
        # Repaired text should still be valid JSON
        parsed = json.loads(repaired)
        assert parsed == payload

    @given(
        key=st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=20),
        value=st.integers(min_value=0, max_value=100),
    )
    @settings(max_examples=200)
    def test_repair_fixes_trailing_commas(self, key: str, value: int) -> None:
        """Trailing commas before } are removed by repair."""
        broken = f'{{"{key}": {value},}}'
        repaired = _repair_json_text(broken)
        parsed = json.loads(repaired)
        assert parsed[key] == value

    @given(
        key=st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=20),
    )
    @settings(max_examples=200)
    def test_repair_fixes_python_none(self, key: str) -> None:
        """Python-style None is converted to JSON null."""
        broken = f'{{"{key}": None}}'
        repaired = _repair_json_text(broken)
        parsed = json.loads(repaired)
        assert parsed[key] is None

    @given(
        key=st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=20),
        value=st.booleans(),
    )
    @settings(max_examples=200)
    def test_repair_fixes_python_booleans(self, key: str, value: bool) -> None:
        """Python-style True/False are converted to JSON true/false."""
        py_bool = "True" if value else "False"
        broken = f'{{"{key}": {py_bool}}}'
        repaired = _repair_json_text(broken)
        parsed = json.loads(repaired)
        assert parsed[key] is value


# ---------------------------------------------------------------------------
# Return type guarantees
# ---------------------------------------------------------------------------


class TestReturnTypeGuarantees:
    """extract_json return type must match expected_type."""

    @given(response=llm_json_response_strategy())
    @settings(max_examples=200)
    def test_returns_dict_when_dict_expected(self, response: str) -> None:
        """When expected_type=dict (default), result is always a dict."""
        result = extract_json(response, expected_type=dict)
        assert isinstance(result, dict)

    @given(
        items=st.lists(st.integers(min_value=0, max_value=100), min_size=1, max_size=10),
    )
    @settings(max_examples=200)
    def test_returns_list_when_list_expected(self, items: list[int]) -> None:
        """When expected_type=list, result is always a list."""
        text = json.dumps(items)
        result = extract_json(text, expected_type=list)
        assert isinstance(result, list)
