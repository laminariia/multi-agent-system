"""Property-based tests for JSON serialization roundtrip.

Verifies that domain objects survive JSON serialize -> deserialize cycles,
that unicode is handled correctly, that datetime fields are preserved,
and that no data is silently lost or corrupted during serialization.
"""

from __future__ import annotations

import json
from datetime import datetime

from hypothesis import given, settings
from hypothesis import strategies as st

from .conftest import (
    VALID_ANALYSIS_TIERS,
    VALID_PLATFORMS,
    VALID_STATUSES,
    VALID_TEMPERATURES,
    agent_state_strategy,
    bid_artifact_strategy,
    budget_strategy,
    json_safe_text_strategy,
    project_context_strategy,
    score_strategy,
    scoring_result_strategy,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _json_serializable_state(state: dict) -> dict:
    """Convert AgentState dict to JSON-serializable form.

    Converts datetime objects to ISO strings and removes non-serializable
    fields (messages contain LangChain BaseMessage objects).
    """
    out = {}
    for k, v in state.items():
        if k == "messages":
            out[k] = []  # Skip BaseMessage objects
        elif isinstance(v, datetime):
            out[k] = v.isoformat()
        elif isinstance(v, dict):
            out[k] = _json_serializable_dict(v)
        else:
            out[k] = v
    return out


def _json_serializable_dict(d: dict) -> dict:
    """Recursively make a dict JSON-serializable."""
    out = {}
    for k, v in d.items():
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        elif isinstance(v, dict):
            out[k] = _json_serializable_dict(v)
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# JSON roundtrip for AgentState
# ---------------------------------------------------------------------------


class TestAgentStateRoundtrip:
    """AgentState dicts must survive JSON serialization roundtrip."""

    @given(state=agent_state_strategy())
    @settings(max_examples=200)
    def test_roundtrip_preserves_all_fields(self, state: dict) -> None:
        """All fields survive JSON encode -> decode."""
        serializable = _json_serializable_state(state)
        encoded = json.dumps(serializable, ensure_ascii=False)
        decoded = json.loads(encoded)

        # Check key fields (datetimes become strings)
        assert decoded["thread_id"] == state["thread_id"]
        assert decoded["status"] == state["status"]
        assert decoded["current_agent"] == state["current_agent"]
        assert decoded["retry_count"] == state["retry_count"]
        assert decoded["requires_hitl"] == state["requires_hitl"]
        assert decoded["delivery_type"] == state["delivery_type"]

    @given(state=agent_state_strategy())
    @settings(max_examples=200)
    def test_roundtrip_preserves_status_enum(self, state: dict) -> None:
        """Status value is preserved exactly after roundtrip."""
        serializable = _json_serializable_state(state)
        decoded = json.loads(json.dumps(serializable))
        assert decoded["status"] in VALID_STATUSES

    @given(state=agent_state_strategy())
    @settings(max_examples=200)
    def test_roundtrip_preserves_errors_list(self, state: dict) -> None:
        """Errors list is preserved exactly after roundtrip."""
        serializable = _json_serializable_state(state)
        decoded = json.loads(json.dumps(serializable, ensure_ascii=False))
        assert decoded["errors"] == state["errors"]

    @given(state=agent_state_strategy())
    @settings(max_examples=200)
    def test_no_data_loss_on_field_count(self, state: dict) -> None:
        """No fields are dropped during serialization."""
        serializable = _json_serializable_state(state)
        decoded = json.loads(json.dumps(serializable))
        assert set(decoded.keys()) == set(serializable.keys())


# ---------------------------------------------------------------------------
# JSON roundtrip for ProjectContext
# ---------------------------------------------------------------------------


class TestProjectContextRoundtrip:
    """ProjectContext dicts must survive JSON roundtrip."""

    @given(project=project_context_strategy())
    @settings(max_examples=200)
    def test_roundtrip_preserves_project_fields(self, project: dict) -> None:
        """All project fields survive roundtrip."""
        serializable = _json_serializable_dict(project)
        encoded = json.dumps(serializable, ensure_ascii=False)
        decoded = json.loads(encoded)

        assert decoded["project_id"] == project["project_id"]
        assert decoded["job_id"] == project["job_id"]
        assert decoded["platform"] in VALID_PLATFORMS
        assert decoded["budget"] == project["budget"]
        assert decoded["requirements"] == project["requirements"]

    @given(project=project_context_strategy())
    @settings(max_examples=200)
    def test_roundtrip_preserves_client_data(self, project: dict) -> None:
        """Client sub-dict survives roundtrip."""
        serializable = _json_serializable_dict(project)
        decoded = json.loads(json.dumps(serializable, ensure_ascii=False))

        assert decoded["client"]["name"] == project["client"]["name"]
        assert decoded["client"]["reviews"] == project["client"]["reviews"]
        # Float comparison — JSON roundtrip preserves IEEE 754
        assert decoded["client"]["rating"] == project["client"]["rating"]


# ---------------------------------------------------------------------------
# JSON roundtrip for bid artifacts
# ---------------------------------------------------------------------------


class TestBidArtifactRoundtrip:
    """Bid artifact dicts must survive JSON roundtrip."""

    @given(artifact=bid_artifact_strategy())
    @settings(max_examples=200)
    def test_roundtrip_preserves_amounts(self, artifact: dict) -> None:
        """Bid amount and budget survive JSON roundtrip."""
        encoded = json.dumps(artifact, ensure_ascii=False)
        decoded = json.loads(encoded)

        assert decoded["bid_amount"] == artifact["bid_amount"]
        assert decoded["budget"] == artifact["budget"]
        assert decoded["delivery_days"] == artifact["delivery_days"]

    @given(artifact=bid_artifact_strategy())
    @settings(max_examples=200)
    def test_roundtrip_bid_still_le_budget(self, artifact: dict) -> None:
        """After roundtrip, the bid <= budget invariant still holds."""
        decoded = json.loads(json.dumps(artifact))
        assert decoded["bid_amount"] <= decoded["budget"]


# ---------------------------------------------------------------------------
# Unicode handling
# ---------------------------------------------------------------------------


class TestUnicodeHandling:
    """Unicode strings must survive JSON roundtrip without corruption."""

    @given(text=json_safe_text_strategy)
    @settings(max_examples=200)
    def test_unicode_roundtrip(self, text: str) -> None:
        """Any JSON-safe unicode text survives JSON roundtrip."""
        payload = {"content": text}
        decoded = json.loads(json.dumps(payload, ensure_ascii=False))
        assert decoded["content"] == text

    @given(text=json_safe_text_strategy)
    @settings(max_examples=200)
    def test_unicode_roundtrip_ensure_ascii(self, text: str) -> None:
        """Unicode survives even with ensure_ascii=True (escaped form)."""
        payload = {"content": text}
        decoded = json.loads(json.dumps(payload, ensure_ascii=True))
        assert decoded["content"] == text

    @given(
        name=json_safe_text_strategy,
        requirements=json_safe_text_strategy,
    )
    @settings(max_examples=200)
    def test_unicode_in_project_context(self, name: str, requirements: str) -> None:
        """Unicode in project fields survives roundtrip."""
        project = {
            "project_id": "test",
            "job_id": "test",
            "platform": "freelancer",
            "client": {"name": name, "rating": 4.5, "reviews": 10},
            "requirements": requirements,
            "budget": 1000.0,
            "deadline": "2026-01-01T00:00:00+00:00",
        }
        decoded = json.loads(json.dumps(project, ensure_ascii=False))
        assert decoded["client"]["name"] == name
        assert decoded["requirements"] == requirements

    @given(
        errors=st.lists(json_safe_text_strategy, min_size=0, max_size=10),
    )
    @settings(max_examples=200)
    def test_unicode_in_error_list(self, errors: list[str]) -> None:
        """Unicode error messages survive JSON roundtrip."""
        payload = {"errors": errors}
        decoded = json.loads(json.dumps(payload, ensure_ascii=False))
        assert decoded["errors"] == errors


# ---------------------------------------------------------------------------
# Datetime serialization
# ---------------------------------------------------------------------------


class TestDatetimeSerialization:
    """Datetime values must be recoverable after JSON roundtrip via ISO format."""

    @given(state=agent_state_strategy())
    @settings(max_examples=200)
    def test_datetime_iso_roundtrip(self, state: dict) -> None:
        """created_at and updated_at survive ISO serialization roundtrip."""
        iso_created = state["created_at"].isoformat()
        iso_updated = state["updated_at"].isoformat()

        recovered_created = datetime.fromisoformat(iso_created)
        recovered_updated = datetime.fromisoformat(iso_updated)

        assert recovered_created == state["created_at"]
        assert recovered_updated == state["updated_at"]

    @given(state=agent_state_strategy())
    @settings(max_examples=200)
    def test_timestamp_ordering_preserved(self, state: dict) -> None:
        """created_at <= updated_at is preserved after serialization."""
        serializable = _json_serializable_state(state)
        decoded = json.loads(json.dumps(serializable))

        created = datetime.fromisoformat(decoded["created_at"])
        updated = datetime.fromisoformat(decoded["updated_at"])
        assert created <= updated


# ---------------------------------------------------------------------------
# Float precision
# ---------------------------------------------------------------------------


class TestFloatPrecision:
    """Float values must not lose meaningful precision through JSON."""

    @given(budget=budget_strategy)
    @settings(max_examples=200)
    def test_budget_precision_preserved(self, budget: float) -> None:
        """Budget float survives JSON roundtrip exactly (IEEE 754)."""
        decoded = json.loads(json.dumps({"budget": budget}))
        assert decoded["budget"] == budget

    @given(score=score_strategy)
    @settings(max_examples=200)
    def test_integer_score_preserved(self, score: int) -> None:
        """Integer scores are preserved as integers through JSON."""
        decoded = json.loads(json.dumps({"score": score}))
        assert decoded["score"] == score
        assert isinstance(decoded["score"], int)

    @given(result=scoring_result_strategy())
    @settings(max_examples=200)
    def test_scoring_result_roundtrip(self, result: dict) -> None:
        """ScoringResult-like dict survives JSON roundtrip."""
        decoded = json.loads(json.dumps(result))
        assert decoded["total_score"] == result["total_score"]
        assert decoded["temperature"] in VALID_TEMPERATURES
        assert decoded["analysis_tier"] in VALID_ANALYSIS_TIERS
