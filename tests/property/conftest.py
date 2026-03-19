"""Shared Hypothesis strategies for property-based tests.

Provides reusable strategies for generating AgentState dicts,
ProjectContext dicts, agent names, platforms, statuses, scores,
bid amounts, and other domain-specific data.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import hypothesis.strategies as st

# ---------------------------------------------------------------------------
# Domain constants (mirrors src/core/state.py & src/core/graph.py)
# ---------------------------------------------------------------------------

VALID_STATUSES = ("active", "paused", "completed", "failed")
VALID_PLATFORMS = ("freelancer", "upwork", "flru", "kwork", "internal", "outreach")
VALID_DELIVERY_TYPES = ("files", "credentials", "deploy", "instructions", "mixed")

# All agent node names used in the pipeline graph
PIPELINE_A_AGENTS = (
    "scout",
    "bid",
    "planner",
    "dev",
    "content",
    "design",
    "critic",
    "packager",
)
PIPELINE_B_AGENTS = ("geo_scout", "outreach")
HITL_AGENTS = ("hitl_bid", "hitl_review", "hitl_dev_launch", "hitl_outreach")
ALL_AGENTS = PIPELINE_A_AGENTS + PIPELINE_B_AGENTS + HITL_AGENTS

# Temperature classifications from LeadScorer
VALID_TEMPERATURES = ("hot", "warm", "cold")
VALID_ANALYSIS_TIERS = ("deep", "medium", "quick")

# Negotiation states from state_machine.py
NEGOTIATION_STATES = (
    "initial",
    "qualifying",
    "proposing",
    "negotiating",
    "closing",
    "accepted",
    "declined",
    "stale",
    "operator_override",
)

# Terminal negotiation states
TERMINAL_NEGOTIATION_STATES = ("accepted", "declined")
NON_TERMINAL_NEGOTIATION_STATES = tuple(s for s in NEGOTIATION_STATES if s not in TERMINAL_NEGOTIATION_STATES)

# Sales stages from sales_conversation.py
SALES_STAGES = (
    "first_contact",
    "awaiting_reply",
    "discovery",
    "analysis",
    "concept",
    "concept_review",
    "proposal",
    "negotiation",
    "won",
    "lost",
    "stale",
)

# Touch sequence states
TOUCH_STATES = ("pending", "active", "replied", "completed", "paused", "stopped")
TERMINAL_TOUCH_STATES = ("replied", "completed", "stopped")

# Revision severities used in Critic flow
REVISION_SEVERITIES = ("minor", "major")

# Critic verdicts
CRITIC_VERDICTS = ("APPROVE", "REVISE", "REJECT")

# ---------------------------------------------------------------------------
# Primitive strategies
# ---------------------------------------------------------------------------

status_strategy = st.sampled_from(VALID_STATUSES)
platform_strategy = st.sampled_from(VALID_PLATFORMS)
delivery_type_strategy = st.sampled_from(VALID_DELIVERY_TYPES)
agent_name_strategy = st.sampled_from(ALL_AGENTS)
pipeline_a_agent_strategy = st.sampled_from(PIPELINE_A_AGENTS)
pipeline_b_agent_strategy = st.sampled_from(PIPELINE_B_AGENTS)
temperature_strategy = st.sampled_from(VALID_TEMPERATURES)
analysis_tier_strategy = st.sampled_from(VALID_ANALYSIS_TIERS)
negotiation_state_strategy = st.sampled_from(NEGOTIATION_STATES)
non_terminal_negotiation_strategy = st.sampled_from(NON_TERMINAL_NEGOTIATION_STATES)
sales_stage_strategy = st.sampled_from(SALES_STAGES)
touch_state_strategy = st.sampled_from(TOUCH_STATES)
verdict_strategy = st.sampled_from(CRITIC_VERDICTS)
severity_strategy = st.sampled_from(REVISION_SEVERITIES)

# Score: 0-100 integer (used by agents like Critic)
score_strategy = st.integers(min_value=0, max_value=100)

# Budget: positive float, reasonable range for freelance projects
budget_strategy = st.floats(min_value=10.0, max_value=100_000.0, allow_nan=False, allow_infinity=False)

# Bid amount: positive float
bid_amount_strategy = st.floats(min_value=1.0, max_value=100_000.0, allow_nan=False, allow_infinity=False)

# Timestamps in a reasonable range
datetime_strategy = st.datetimes(
    min_value=datetime(2024, 1, 1),
    max_value=datetime(2030, 12, 31),
    timezones=st.just(UTC),
)

# Thread IDs: hex strings (like uuid4().hex)
thread_id_strategy = st.text(alphabet="0123456789abcdef", min_size=32, max_size=32)

# Non-empty text for requirements, error messages, etc.
nonempty_text_strategy = st.text(min_size=1, max_size=500)

# Arbitrary unicode text for fuzzing parsers
unicode_text_strategy = st.text(min_size=0, max_size=2000)

# JSON-safe strings (no surrogates)
json_safe_text_strategy = st.text(
    alphabet=st.characters(
        blacklist_categories=("Cs",),  # exclude surrogates
    ),
    min_size=0,
    max_size=1000,
)

# Retry counts
retry_count_strategy = st.integers(min_value=0, max_value=10)

# Error list
error_list_strategy = st.lists(nonempty_text_strategy, max_size=5)

# Client rating
client_rating_strategy = st.floats(min_value=0.0, max_value=5.0, allow_nan=False, allow_infinity=False)

# Google/Yandex rating
google_rating_strategy = st.floats(min_value=0.0, max_value=5.0, allow_nan=False, allow_infinity=False)

# Review count
review_count_strategy = st.integers(min_value=0, max_value=100_000)


# ---------------------------------------------------------------------------
# Composite strategies
# ---------------------------------------------------------------------------


@st.composite
def project_context_strategy(draw: st.DrawFn) -> dict:
    """Generate a valid ProjectContext dict."""
    return {
        "project_id": draw(st.text(min_size=1, max_size=50)),
        "job_id": draw(st.text(min_size=1, max_size=50)),
        "platform": draw(platform_strategy),
        "client": {
            "name": draw(st.text(min_size=1, max_size=100)),
            "rating": draw(client_rating_strategy),
            "reviews": draw(st.integers(min_value=0, max_value=10_000)),
        },
        "requirements": draw(st.text(min_size=1, max_size=500)),
        "budget": draw(budget_strategy),
        "deadline": draw(datetime_strategy),
    }


@st.composite
def agent_state_strategy(draw: st.DrawFn) -> dict:
    """Generate a valid AgentState-like dict with all required fields."""
    now = draw(datetime_strategy)
    updated = now + timedelta(seconds=draw(st.integers(min_value=0, max_value=3600)))
    status = draw(status_strategy)
    current_agent = draw(agent_name_strategy)

    return {
        "thread_id": draw(thread_id_strategy),
        "mas_checkpoint_id": draw(thread_id_strategy),
        "project": draw(project_context_strategy()),
        "current_agent": current_agent,
        "current_task": None,
        "artifacts": {},
        "messages": [],
        "next_agent": draw(st.one_of(st.none(), agent_name_strategy)),
        "requires_hitl": draw(st.booleans()),
        "hitl_request_id": draw(st.one_of(st.none(), thread_id_strategy)),
        "agent_sequence": draw(st.lists(pipeline_a_agent_strategy, max_size=8)),
        "current_sequence_index": draw(st.integers(min_value=0, max_value=7)),
        "delivery_type": draw(delivery_type_strategy),
        "revision_target": draw(st.one_of(st.none(), pipeline_a_agent_strategy)),
        "revision_severity": draw(st.one_of(st.none(), st.sampled_from(REVISION_SEVERITIES))),
        "real_hours": draw(
            st.one_of(st.none(), st.floats(min_value=0.0, max_value=1000.0, allow_nan=False, allow_infinity=False))
        ),
        "proposed_days": draw(st.one_of(st.none(), st.integers(min_value=1, max_value=365))),
        "min_delivery_at": draw(st.one_of(st.none(), datetime_strategy)),
        "scheduled_messages": [],
        "failed_agent": draw(st.one_of(st.none(), agent_name_strategy)),
        "failure_reason": draw(st.one_of(st.none(), nonempty_text_strategy)),
        "recovery_attempted": draw(st.integers(min_value=0, max_value=10)),
        "skipped_agents": draw(st.lists(agent_name_strategy, max_size=5)),
        "retry_count": draw(retry_count_strategy),
        "errors": draw(error_list_strategy),
        "created_at": now,
        "updated_at": updated,
        "status": status,
    }


@st.composite
def bid_artifact_strategy(draw: st.DrawFn) -> dict:
    """Generate a bid artifact dict with amount and proposal."""
    budget = draw(budget_strategy)
    # bid_amount should be <= budget for valid bids
    bid_amount = draw(
        st.floats(
            min_value=1.0,
            max_value=max(budget, 1.01),
            allow_nan=False,
            allow_infinity=False,
        )
    )
    return {
        "budget": budget,
        "bid_amount": bid_amount,
        "proposal": draw(st.text(min_size=10, max_size=500)),
        "delivery_days": draw(st.integers(min_value=1, max_value=365)),
    }


@st.composite
def invalid_bid_artifact_strategy(draw: st.DrawFn) -> dict:
    """Generate a bid artifact where bid_amount exceeds budget (invalid)."""
    budget = draw(st.floats(min_value=10.0, max_value=50_000.0, allow_nan=False, allow_infinity=False))
    # Force bid_amount > budget
    bid_amount = draw(
        st.floats(
            min_value=budget + 0.01,
            max_value=budget + 50_000.0,
            allow_nan=False,
            allow_infinity=False,
        )
    )
    return {
        "budget": budget,
        "bid_amount": bid_amount,
        "proposal": draw(st.text(min_size=10, max_size=500)),
        "delivery_days": draw(st.integers(min_value=1, max_value=365)),
    }


@st.composite
def llm_json_response_strategy(draw: st.DrawFn) -> str:
    """Generate strings that look like LLM JSON responses (with fences, noise).

    The ``result`` field is restricted to characters that survive JSON
    round-tripping *and* regex extraction when the JSON blob is embedded
    inside surrounding prose text.  Characters like ``{``, ``}``, ``"``,
    and ``\\`` would break the regex heuristic in ``extract_json`` when the
    response is wrapped with a preamble (e.g. ``"Here is the result:\\n"``),
    so they are excluded from the generated alphabet.
    """
    # Safe alphabet: printable ASCII without JSON-structural chars and
    # backslash (which can create invalid escape sequences).
    _safe_alphabet = st.characters(
        whitelist_categories=("L", "N", "P", "Z"),
        blacklist_characters='{}[]"\\',
    )
    payload = draw(
        st.one_of(
            # Clean JSON with safe text values
            st.fixed_dictionaries({"result": st.text(alphabet=_safe_alphabet, max_size=50)}),
            # Nested JSON
            st.fixed_dictionaries(
                {
                    "score": st.integers(min_value=0, max_value=100),
                    "verdict": st.sampled_from(CRITIC_VERDICTS),
                }
            ),
        )
    )
    json_str = json.dumps(payload, ensure_ascii=False)

    wrapper = draw(
        st.sampled_from(
            [
                # No wrapper
                lambda s: s,
                # Markdown fence
                lambda s: f"```json\n{s}\n```",
                # With preamble text
                lambda s: f"Here is the result:\n{s}",
                # Markdown fence with preamble
                lambda s: f"Sure, here you go:\n```json\n{s}\n```\nLet me know if you need more.",
                # Extra whitespace
                lambda s: f"  \n\n{s}\n  ",
            ]
        )
    )
    return wrapper(json_str)


@st.composite
def malformed_json_strategy(draw: st.DrawFn) -> str:
    """Generate strings that are intentionally broken JSON or not JSON at all."""
    return draw(
        st.one_of(
            # Truncated JSON
            st.just('{"key": "val'),
            # Single quotes
            st.just("{'key': 'value'}"),
            # Trailing commas
            st.just('{"a": 1, "b": 2,}'),
            # Python booleans/None
            st.just('{"flag": True, "val": None}'),
            # Empty
            st.just(""),
            # Just whitespace
            st.just("   \n\t  "),
            # HTML instead of JSON
            st.just("<html><body>error</body></html>"),
            # Random unicode
            unicode_text_strategy,
            # Binary-like noise
            st.binary(min_size=1, max_size=200).map(lambda b: b.decode("utf-8", errors="replace")),
            # Nested code fences with garbage
            st.just("```json\nnot actually json\n```"),
            # Multiple JSON objects (ambiguous)
            st.just('{"a": 1} {"b": 2}'),
            # Array when dict expected
            st.just("[1, 2, 3]"),
        )
    )


@st.composite
def scoring_result_strategy(draw: st.DrawFn) -> dict:
    """Generate a scoring result dict."""
    score = draw(score_strategy)
    return {
        "lead_id": draw(st.text(min_size=1, max_size=50)),
        "total_score": score,
        "temperature": draw(temperature_strategy),
        "analysis_tier": draw(analysis_tier_strategy),
        "matched_rules": [],
    }


@st.composite
def negotiation_transition_strategy(draw: st.DrawFn) -> tuple[str, str]:
    """Generate a (from_state, to_state) pair for negotiation transitions.

    Draws from ALL possible pairs, including invalid ones.
    """
    from_state = draw(negotiation_state_strategy)
    to_state = draw(negotiation_state_strategy)
    return (from_state, to_state)
