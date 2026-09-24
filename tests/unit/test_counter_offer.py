"""Unit tests for src.negotiations.counter_offer -- CounterOfferEngine.

Tests cover: 3 pricing bands, exact boundaries (10%, 25%), suggested_responses
for each band, midpoint calculation, zero/negative amounts, and
CounterOfferPayload fields.
"""

from __future__ import annotations

import pytest

from src.negotiations.counter_offer import CounterOfferEngine, CounterOfferPayload

# ---------------------------------------------------------------------------
# Tests: Band 1 -- accept_possible (<=10%)
# ---------------------------------------------------------------------------


class TestBandAccept:
    """Tests for the accept_possible band (<=10% difference)."""

    def test_exact_amount_is_accept(self):
        engine = CounterOfferEngine()
        result = engine.analyze(1000.0, 1000.0)
        assert result.recommendation == "accept_possible"
        assert result.difference_percent == 0.0

    def test_5_percent_below_is_accept(self):
        engine = CounterOfferEngine()
        result = engine.analyze(1000.0, 950.0)
        assert result.recommendation == "accept_possible"
        assert result.difference_percent == 5.0

    def test_10_percent_below_is_accept(self):
        engine = CounterOfferEngine()
        result = engine.analyze(1000.0, 900.0)
        assert result.recommendation == "accept_possible"
        assert result.difference_percent == 10.0

    def test_10_percent_above_is_accept(self):
        engine = CounterOfferEngine()
        result = engine.analyze(1000.0, 1100.0)
        assert result.recommendation == "accept_possible"
        assert result.difference_percent == 10.0

    def test_accept_has_two_suggestions(self):
        engine = CounterOfferEngine()
        result = engine.analyze(1000.0, 950.0)
        assert len(result.suggested_responses) == 2

    def test_accept_suggestions_mention_counter_amount(self):
        engine = CounterOfferEngine()
        result = engine.analyze(1000.0, 950.0)
        assert any("950" in s for s in result.suggested_responses)


# ---------------------------------------------------------------------------
# Tests: Band 2 -- negotiate (11-25%)
# ---------------------------------------------------------------------------


class TestBandNegotiate:
    """Tests for the negotiate band (11-25% difference)."""

    def test_11_percent_is_negotiate(self):
        engine = CounterOfferEngine()
        result = engine.analyze(1000.0, 890.0)
        assert result.recommendation == "negotiate"
        assert result.difference_percent == 11.0

    def test_15_percent_is_negotiate(self):
        engine = CounterOfferEngine()
        result = engine.analyze(1000.0, 850.0)
        assert result.recommendation == "negotiate"
        assert result.difference_percent == 15.0

    def test_25_percent_is_negotiate(self):
        engine = CounterOfferEngine()
        result = engine.analyze(1000.0, 750.0)
        assert result.recommendation == "negotiate"
        assert result.difference_percent == 25.0

    def test_negotiate_has_three_suggestions(self):
        engine = CounterOfferEngine()
        result = engine.analyze(1000.0, 850.0)
        assert len(result.suggested_responses) == 3

    def test_midpoint_calculation(self):
        engine = CounterOfferEngine()
        result = engine.analyze(1000.0, 800.0)
        # Midpoint of 1000 and 800 is 900
        assert any("900" in s for s in result.suggested_responses)

    def test_midpoint_with_odd_numbers(self):
        engine = CounterOfferEngine()
        result = engine.analyze(1000.0, 770.0)
        # Midpoint: (1000+770)/2 = 885
        assert any("885" in s for s in result.suggested_responses)


# ---------------------------------------------------------------------------
# Tests: Band 3 -- decline_or_reduce_scope (>25%)
# ---------------------------------------------------------------------------


class TestBandDecline:
    """Tests for the decline_or_reduce_scope band (>25% difference)."""

    def test_26_percent_is_decline(self):
        engine = CounterOfferEngine()
        result = engine.analyze(1000.0, 740.0)
        assert result.recommendation == "decline_or_reduce_scope"
        assert result.difference_percent == 26.0

    def test_50_percent_is_decline(self):
        engine = CounterOfferEngine()
        result = engine.analyze(1000.0, 500.0)
        assert result.recommendation == "decline_or_reduce_scope"
        assert result.difference_percent == 50.0

    def test_decline_has_three_suggestions(self):
        engine = CounterOfferEngine()
        result = engine.analyze(1000.0, 500.0)
        assert len(result.suggested_responses) == 3

    def test_decline_mentions_scope(self):
        engine = CounterOfferEngine()
        result = engine.analyze(1000.0, 500.0)
        assert any("scope" in s.lower() for s in result.suggested_responses)


# ---------------------------------------------------------------------------
# Tests: Exact boundaries
# ---------------------------------------------------------------------------


class TestExactBoundaries:
    """Boundary precision at 10% and 25%."""

    def test_exactly_10_percent_is_accept(self):
        engine = CounterOfferEngine()
        result = engine.analyze(1000.0, 900.0)
        assert result.recommendation == "accept_possible"

    def test_10_point_1_percent_is_negotiate(self):
        engine = CounterOfferEngine()
        # 10.1% below = 899
        result = engine.analyze(1000.0, 899.0)
        assert result.recommendation == "negotiate"

    def test_exactly_25_percent_is_negotiate(self):
        engine = CounterOfferEngine()
        result = engine.analyze(1000.0, 750.0)
        assert result.recommendation == "negotiate"

    def test_25_point_1_percent_is_decline(self):
        engine = CounterOfferEngine()
        # 25.1% below = 749
        result = engine.analyze(1000.0, 749.0)
        assert result.recommendation == "decline_or_reduce_scope"


# ---------------------------------------------------------------------------
# Tests: CounterOfferPayload fields
# ---------------------------------------------------------------------------


class TestPayloadFields:
    """Test CounterOfferPayload fields are populated correctly."""

    def test_payload_fields_populated(self):
        engine = CounterOfferEngine()
        result = engine.analyze(
            1000.0,
            800.0,
            conversation_summary="Client wants discount",
            client_profile={"rating": 4.5, "hire_rate": 0.9},
        )

        assert isinstance(result, CounterOfferPayload)
        assert result.original_bid == 1000.0
        assert result.counter_amount == 800.0
        assert result.difference_percent == 20.0
        assert result.conversation_summary == "Client wants discount"
        assert result.client_profile == {"rating": 4.5, "hire_rate": 0.9}

    def test_payload_client_profile_default_none(self):
        engine = CounterOfferEngine()
        result = engine.analyze(1000.0, 900.0)
        assert result.client_profile is None

    def test_payload_conversation_summary_default_empty(self):
        engine = CounterOfferEngine()
        result = engine.analyze(1000.0, 900.0)
        assert result.conversation_summary == ""


# ---------------------------------------------------------------------------
# Tests: Zero/negative amounts -> ValueError
# ---------------------------------------------------------------------------


class TestInvalidAmounts:
    """Test that zero and negative amounts raise ValueError."""

    def test_zero_original_raises_value_error(self):
        engine = CounterOfferEngine()
        with pytest.raises(ValueError, match="positive"):
            engine.analyze(0.0, 500.0)

    def test_negative_original_raises_value_error(self):
        engine = CounterOfferEngine()
        with pytest.raises(ValueError, match="positive"):
            engine.analyze(-100.0, 500.0)

    def test_negative_counter_raises_value_error(self):
        engine = CounterOfferEngine()
        with pytest.raises(ValueError, match="non-negative"):
            engine.analyze(1000.0, -50.0)

    def test_zero_counter_is_valid(self):
        engine = CounterOfferEngine()
        result = engine.analyze(1000.0, 0.0)
        assert result.recommendation == "decline_or_reduce_scope"
        assert result.difference_percent == 100.0
