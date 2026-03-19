"""Property-based tests for bid amount invariants.

Verifies that bid artifacts always satisfy business constraints:
bid_amount <= budget, amounts are positive, values are numeric and finite,
delivery_days are positive integers, and proposals are non-empty.
"""

from __future__ import annotations

import math

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from .conftest import (
    bid_amount_strategy,
    bid_artifact_strategy,
    budget_strategy,
    invalid_bid_artifact_strategy,
)


class TestBidAmountConstraints:
    """Bid amount must never exceed the project budget."""

    @given(artifact=bid_artifact_strategy())
    @settings(max_examples=200)
    def test_bid_amount_le_budget(self, artifact: dict) -> None:
        """bid_amount is always <= budget in a valid bid artifact."""
        assert artifact["bid_amount"] <= artifact["budget"]

    @given(artifact=bid_artifact_strategy())
    @settings(max_examples=200)
    def test_bid_amount_positive(self, artifact: dict) -> None:
        """bid_amount is always positive."""
        assert artifact["bid_amount"] > 0

    @given(artifact=bid_artifact_strategy())
    @settings(max_examples=200)
    def test_budget_positive(self, artifact: dict) -> None:
        """Budget is always positive."""
        assert artifact["budget"] > 0

    @given(artifact=bid_artifact_strategy())
    @settings(max_examples=200)
    def test_bid_amount_is_finite(self, artifact: dict) -> None:
        """bid_amount is never NaN or Infinity."""
        assert math.isfinite(artifact["bid_amount"])

    @given(artifact=bid_artifact_strategy())
    @settings(max_examples=200)
    def test_budget_is_finite(self, artifact: dict) -> None:
        """Budget is never NaN or Infinity."""
        assert math.isfinite(artifact["budget"])


class TestBidDeliveryConstraints:
    """Delivery-related bid fields must be valid."""

    @given(artifact=bid_artifact_strategy())
    @settings(max_examples=200)
    def test_delivery_days_positive_integer(self, artifact: dict) -> None:
        """delivery_days is a positive integer."""
        assert isinstance(artifact["delivery_days"], int)
        assert artifact["delivery_days"] >= 1

    @given(artifact=bid_artifact_strategy())
    @settings(max_examples=200)
    def test_proposal_non_empty(self, artifact: dict) -> None:
        """Proposal text is non-empty (min 10 chars from strategy)."""
        assert len(artifact["proposal"]) >= 10


class TestBidAmountVsBudgetRelationship:
    """Mathematical relationship between bid and budget."""

    @given(budget=budget_strategy)
    @settings(max_examples=200)
    def test_bid_clamping_to_budget(self, budget: float) -> None:
        """Demonstrates the invariant: any valid bid <= budget.

        Simulates what an agent should do: clamp bid to budget.
        """
        raw_bid = budget * 1.5  # Intentionally over-budget
        clamped_bid = min(raw_bid, budget)
        assert clamped_bid <= budget

    @given(
        budget=budget_strategy,
        fraction=st.floats(min_value=0.01, max_value=1.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=200)
    def test_fractional_bid_always_within_budget(self, budget: float, fraction: float) -> None:
        """A bid that is a fraction of the budget never exceeds it."""
        bid = budget * fraction
        assert bid <= budget
        assert bid > 0

    @given(artifact=invalid_bid_artifact_strategy())
    @settings(max_examples=200)
    def test_invalid_bid_exceeds_budget(self, artifact: dict) -> None:
        """Confirms that invalid_bid_artifact_strategy produces over-budget bids.

        This is a meta-test: ensures the 'invalid' strategy is usable for
        testing rejection logic.
        """
        assert artifact["bid_amount"] > artifact["budget"]


class TestBidAmountEdgeCases:
    """Edge cases around bid amount boundaries."""

    @given(
        budget=st.just(10.0),
        bid=st.floats(min_value=0.01, max_value=10.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=200)
    def test_minimum_budget_bids(self, budget: float, bid: float) -> None:
        """Bids against the minimum budget are still valid if <= budget."""
        assert bid <= budget
        assert bid > 0

    @given(
        budget=st.just(100_000.0),
        bid=st.floats(min_value=1.0, max_value=100_000.0, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=200)
    def test_maximum_budget_bids(self, budget: float, bid: float) -> None:
        """Bids against the maximum budget are still valid if <= budget."""
        assert bid <= budget
        assert bid > 0

    @given(bid_amount=bid_amount_strategy)
    @settings(max_examples=200)
    def test_bid_amount_type_is_float(self, bid_amount: float) -> None:
        """bid_amount is always a float."""
        assert isinstance(bid_amount, float)

    @given(budget=budget_strategy, bid_amount=bid_amount_strategy)
    @settings(max_examples=200)
    def test_bid_margin_non_negative_when_valid(self, budget: float, bid_amount: float) -> None:
        """When bid <= budget, the margin (budget - bid) is non-negative."""
        assume(bid_amount <= budget)
        margin = budget - bid_amount
        assert margin >= 0
