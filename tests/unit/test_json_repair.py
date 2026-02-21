"""Tests for src.core.json_repair -- JSON extraction and repair for LLM responses."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from src.core.json_repair import extract_and_validate, extract_json

# ---------------------------------------------------------------------------
# Pydantic test model
# ---------------------------------------------------------------------------

class SampleModel(BaseModel):
    name: str
    score: float = 0.0
    tags: list[str] = Field(default_factory=list)


# ===================================================================
# Strategy 1: Clean JSON passes through
# ===================================================================

class TestCleanJSON:
    def test_clean_dict(self):
        raw = '{"key": "value", "num": 42}'
        result = extract_json(raw)
        assert result == {"key": "value", "num": 42}

    def test_clean_array(self):
        raw = '[{"a": 1}, {"a": 2}]'
        result = extract_json(raw, expected_type=list)
        assert result == [{"a": 1}, {"a": 2}]

    def test_clean_nested(self):
        raw = '{"outer": {"inner": [1, 2, 3]}, "flag": true}'
        result = extract_json(raw)
        assert result["outer"]["inner"] == [1, 2, 3]
        assert result["flag"] is True


# ===================================================================
# Strategy 2: Markdown fences
# ===================================================================

class TestMarkdownFences:
    def test_json_fence(self):
        raw = '```json\n{"key": "value"}\n```'
        result = extract_json(raw)
        assert result == {"key": "value"}

    def test_plain_fence(self):
        raw = '```\n{"key": "value"}\n```'
        result = extract_json(raw)
        assert result == {"key": "value"}

    def test_python_fence(self):
        raw = '```python\n{"key": "value"}\n```'
        result = extract_json(raw)
        assert result == {"key": "value"}

    def test_javascript_fence(self):
        raw = '```javascript\n{"key": "value"}\n```'
        result = extract_json(raw)
        assert result == {"key": "value"}

    def test_fence_with_extra_whitespace(self):
        raw = '```json\n\n  {"key": "value"}  \n\n```'
        result = extract_json(raw)
        assert result == {"key": "value"}

    def test_fence_array(self):
        raw = '```json\n[{"a": 1}]\n```'
        result = extract_json(raw, expected_type=list)
        assert result == [{"a": 1}]


# ===================================================================
# Strategy 3: Extra text before/after JSON
# ===================================================================

class TestExtraText:
    def test_text_before_json(self):
        raw = 'Here is the result:\n\n{"key": "value"}'
        result = extract_json(raw)
        assert result == {"key": "value"}

    def test_text_after_json(self):
        raw = '{"key": "value"}\n\nLet me know if you need anything else!'
        result = extract_json(raw)
        assert result == {"key": "value"}

    def test_text_before_and_after(self):
        raw = (
            "Sure! Here is the JSON output:\n\n"
            '{"name": "test", "score": 0.9}\n\n'
            "I hope this helps!"
        )
        result = extract_json(raw)
        assert result["name"] == "test"
        assert result["score"] == 0.9

    def test_fence_with_surrounding_text(self):
        raw = (
            "Here is the analysis:\n\n"
            "```json\n"
            '{"verdict": "approve", "score": 0.95}\n'
            "```\n\n"
            "As you can see, the quality is high."
        )
        result = extract_json(raw)
        assert result["verdict"] == "approve"
        assert result["score"] == 0.95


# ===================================================================
# Strategy 4: Repair heuristics
# ===================================================================

class TestRepairHeuristics:
    def test_trailing_comma_in_object(self):
        raw = '{"a": 1, "b": 2,}'
        result = extract_json(raw)
        assert result == {"a": 1, "b": 2}

    def test_trailing_comma_in_array(self):
        raw = '[1, 2, 3,]'
        result = extract_json(raw, expected_type=list)
        assert result == [1, 2, 3]

    def test_single_quotes(self):
        raw = "{'key': 'value'}"
        result = extract_json(raw)
        assert result == {"key": "value"}

    def test_python_booleans_and_none(self):
        raw = '{"flag": True, "other": False, "empty": None}'
        result = extract_json(raw)
        assert result["flag"] is True
        assert result["other"] is False
        assert result["empty"] is None


# ===================================================================
# Edge cases
# ===================================================================

class TestEdgeCases:
    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="Empty response"):
            extract_json("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="Empty response"):
            extract_json("   \n\t  ")

    def test_no_json_at_all_raises(self):
        with pytest.raises(ValueError, match="Failed to extract JSON"):
            extract_json("This is just plain text with no JSON at all.")

    def test_multiple_json_objects_takes_first(self):
        raw = '{"first": 1}\n\n{"second": 2}'
        result = extract_json(raw)
        assert result == {"first": 1}

    def test_nested_objects(self):
        raw = '{"outer": {"mid": {"inner": "deep"}}, "list": [1, 2]}'
        result = extract_json(raw)
        assert result["outer"]["mid"]["inner"] == "deep"

    def test_mixed_markdown_and_broken_json(self):
        raw = (
            "Here is the output:\n\n"
            "```json\n"
            '{"items": [{"name": "a",}, {"name": "b",}],}\n'
            "```"
        )
        result = extract_json(raw)
        assert len(result["items"]) == 2
        assert result["items"][0]["name"] == "a"


# ===================================================================
# Pydantic validation
# ===================================================================

class TestPydanticValidation:
    def test_valid_model(self):
        raw = '{"name": "test", "score": 0.9, "tags": ["a", "b"]}'
        result = extract_and_validate(raw, SampleModel)
        assert isinstance(result, SampleModel)
        assert result.name == "test"
        assert result.score == 0.9
        assert result.tags == ["a", "b"]

    def test_model_with_defaults(self):
        raw = '{"name": "minimal"}'
        result = extract_and_validate(raw, SampleModel)
        assert result.name == "minimal"
        assert result.score == 0.0
        assert result.tags == []

    def test_model_from_fenced_json(self):
        raw = '```json\n{"name": "fenced", "score": 0.5}\n```'
        result = extract_and_validate(raw, SampleModel)
        assert result.name == "fenced"

    def test_invalid_model_raises_validation_error(self):
        from pydantic import ValidationError

        raw = '{"score": 0.9}'  # Missing required 'name'
        with pytest.raises(ValidationError):
            extract_and_validate(raw, SampleModel)

    def test_model_from_text_with_commentary(self):
        raw = (
            "I analyzed the data and here is the result:\n\n"
            '{"name": "commented", "score": 0.7}\n\n'
            "Let me know if you need changes."
        )
        result = extract_and_validate(raw, SampleModel)
        assert result.name == "commented"


# ===================================================================
# Real-world LLM response patterns
# ===================================================================

class TestRealWorldPatterns:
    def test_scout_scored_response(self):
        """Simulate a Scout agent LLM response with markdown fences and commentary."""
        raw = (
            "I've analyzed the jobs. Here are the results:\n\n"
            "```json\n"
            "[\n"
            '  {"job_id": "123", "title": "React Dev", "match_score": 0.85, "recommendation": "bid"},\n'
            '  {"job_id": "456", "title": "WordPress", "match_score": 0.45, "recommendation": "skip"}\n'
            "]\n"
            "```\n\n"
            "The first job is a strong match."
        )
        result = extract_json(raw, expected_type=list)
        assert len(result) == 2
        assert result[0]["match_score"] == 0.85

    def test_bid_proposal_response(self):
        """Simulate a Bid agent LLM response."""
        raw = (
            "```json\n"
            "{\n"
            '  "proposal_text": "I am a skilled developer...",\n'
            '  "bid_amount": 500.00,\n'
            '  "delivery_days": 7,\n'
            '  "confidence_score": 0.8,\n'
            '  "key_points": ["React expert", "Fast delivery"]\n'
            "}\n"
            "```"
        )
        result = extract_json(raw)
        assert result["bid_amount"] == 500.0

    def test_critic_verdict_response(self):
        """Simulate a Critic agent LLM response."""
        raw = (
            "After reviewing all artifacts, here is my assessment:\n\n"
            "```json\n"
            "{\n"
            '  "verdict": "approve",\n'
            '  "score": 0.92,\n'
            '  "issues": [],\n'
            '  "passed_checks": ["code_quality", "requirements_coverage"],\n'
            '  "revision_instructions": "",\n'
            '  "revision_type": "none"\n'
            "}\n"
            "```"
        )
        result = extract_json(raw)
        assert result["verdict"] == "approve"
        assert result["score"] == 0.92
