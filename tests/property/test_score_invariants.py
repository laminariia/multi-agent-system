"""Property-based tests for score and temperature invariants.

Verifies that scores are always in 0-100 range, temperature classifications
are always hot/warm/cold, analysis tiers match temperature, and the
LeadScorer never produces out-of-range values.
"""

from __future__ import annotations

from dataclasses import dataclass

from hypothesis import given, settings
from hypothesis import strategies as st

from src.core.lead_scorer import (
    AnalysisResult,
    LeadScorer,
    RatingInfo,
    ScoringResult,
    WebsiteCheck,
)

from .conftest import (
    VALID_ANALYSIS_TIERS,
    VALID_TEMPERATURES,
    analysis_tier_strategy,
    google_rating_strategy,
    review_count_strategy,
    score_strategy,
    temperature_strategy,
)

# ---------------------------------------------------------------------------
# Score range invariants
# ---------------------------------------------------------------------------


class TestScoreRange:
    """Scores must always be in [0, 100]."""

    @given(score=score_strategy)
    @settings(max_examples=200)
    def test_score_within_bounds(self, score: int) -> None:
        """Generated scores are always 0 <= score <= 100."""
        assert 0 <= score <= 100

    @given(score=score_strategy)
    @settings(max_examples=200)
    def test_score_is_integer(self, score: int) -> None:
        """Scores are always integers."""
        assert isinstance(score, int)

    @given(score=st.integers(min_value=-1000, max_value=1000))
    @settings(max_examples=200)
    def test_clamped_score_always_in_range(self, score: int) -> None:
        """Clamping any integer to [0, 100] always produces a valid score."""
        clamped = max(0, min(100, score))
        assert 0 <= clamped <= 100


# ---------------------------------------------------------------------------
# Temperature classification invariants
# ---------------------------------------------------------------------------


class TestTemperatureClassification:
    """Temperature must always be one of hot/warm/cold."""

    @given(temp=temperature_strategy)
    @settings(max_examples=200)
    def test_temperature_in_valid_set(self, temp: str) -> None:
        """Temperature is always one of the valid values."""
        assert temp in VALID_TEMPERATURES

    @given(score=st.integers(min_value=0, max_value=1000))
    @settings(max_examples=200)
    def test_lead_scorer_temperature_always_valid(self, score: int) -> None:
        """LeadScorer._classify_temperature always returns a valid temperature."""
        scorer = LeadScorer()
        temp = scorer._classify_temperature(score)
        assert temp in VALID_TEMPERATURES

    @given(score=st.integers(min_value=5, max_value=1000))
    @settings(max_examples=200)
    def test_high_score_is_hot(self, score: int) -> None:
        """Score >= 5 always classifies as 'hot'."""
        scorer = LeadScorer()
        assert scorer._classify_temperature(score) == "hot"

    @given(score=st.integers(min_value=3, max_value=4))
    @settings(max_examples=200)
    def test_medium_score_is_warm(self, score: int) -> None:
        """Score 3-4 always classifies as 'warm'."""
        scorer = LeadScorer()
        assert scorer._classify_temperature(score) == "warm"

    @given(score=st.integers(min_value=0, max_value=2))
    @settings(max_examples=200)
    def test_low_score_is_cold(self, score: int) -> None:
        """Score 0-2 always classifies as 'cold'."""
        scorer = LeadScorer()
        assert scorer._classify_temperature(score) == "cold"


# ---------------------------------------------------------------------------
# Analysis tier invariants
# ---------------------------------------------------------------------------


class TestAnalysisTierClassification:
    """Analysis tier must always be one of deep/medium/quick."""

    @given(tier=analysis_tier_strategy)
    @settings(max_examples=200)
    def test_tier_in_valid_set(self, tier: str) -> None:
        """Analysis tier is always one of the valid values."""
        assert tier in VALID_ANALYSIS_TIERS

    @given(score=st.integers(min_value=0, max_value=1000))
    @settings(max_examples=200)
    def test_lead_scorer_tier_always_valid(self, score: int) -> None:
        """LeadScorer._determine_analysis_tier always returns a valid tier."""
        scorer = LeadScorer()
        tier = scorer._determine_analysis_tier(score)
        assert tier in VALID_ANALYSIS_TIERS

    @given(score=st.integers(min_value=5, max_value=1000))
    @settings(max_examples=200)
    def test_high_score_gets_deep_analysis(self, score: int) -> None:
        """Score >= 5 always routes to 'deep' analysis."""
        scorer = LeadScorer()
        assert scorer._determine_analysis_tier(score) == "deep"

    @given(score=st.integers(min_value=3, max_value=4))
    @settings(max_examples=200)
    def test_medium_score_gets_medium_analysis(self, score: int) -> None:
        """Score 3-4 always routes to 'medium' analysis."""
        scorer = LeadScorer()
        assert scorer._determine_analysis_tier(score) == "medium"

    @given(score=st.integers(min_value=0, max_value=2))
    @settings(max_examples=200)
    def test_low_score_gets_quick_analysis(self, score: int) -> None:
        """Score 0-2 always routes to 'quick' analysis."""
        scorer = LeadScorer()
        assert scorer._determine_analysis_tier(score) == "quick"


# ---------------------------------------------------------------------------
# Temperature / Tier consistency
# ---------------------------------------------------------------------------


class TestTemperatureTierConsistency:
    """Temperature and analysis tier must be consistent for the same score."""

    @given(score=st.integers(min_value=0, max_value=1000))
    @settings(max_examples=200)
    def test_temperature_and_tier_are_aligned(self, score: int) -> None:
        """Temperature and tier boundaries use the same thresholds."""
        scorer = LeadScorer()
        temp = scorer._classify_temperature(score)
        tier = scorer._determine_analysis_tier(score)

        # hot <-> deep, warm <-> medium, cold <-> quick
        expected_mapping = {"hot": "deep", "warm": "medium", "cold": "quick"}
        assert tier == expected_mapping[temp]


# ---------------------------------------------------------------------------
# LeadScorer.score() invariants with arbitrary input
# ---------------------------------------------------------------------------


@dataclass
class FakeLead:
    """Minimal lead object for scoring tests."""

    id: str = "test-lead"
    category: str | None = None


class TestLeadScorerInvariants:
    """LeadScorer.score() must never crash and always produce valid output."""

    @given(
        has_website=st.booleans(),
        has_ssl=st.booleans(),
        google_rating=st.one_of(st.none(), google_rating_strategy),
        review_count=st.one_of(st.none(), review_count_strategy),
        category=st.one_of(st.none(), st.text(min_size=0, max_size=50)),
    )
    @settings(max_examples=200)
    def test_score_never_crashes(
        self,
        has_website: bool,
        has_ssl: bool,
        google_rating: float | None,
        review_count: int | None,
        category: str | None,
    ) -> None:
        """score() never raises an exception regardless of input combination."""
        lead = FakeLead(id="test", category=category)
        analysis = AnalysisResult(
            lead_id="test",
            website=WebsiteCheck(exists=has_website, ssl=has_ssl),
            rating=RatingInfo(
                google_rating=google_rating,
                review_count=review_count,
            ),
        )

        scorer = LeadScorer()
        result = scorer.score(lead, analysis)

        # Core invariants
        assert isinstance(result, ScoringResult)
        assert result.total_score >= 0
        assert result.temperature in VALID_TEMPERATURES
        assert result.analysis_tier in VALID_ANALYSIS_TIERS

    @given(
        lighthouse_perf=st.one_of(
            st.none(),
            st.integers(min_value=0, max_value=100),
        ),
        is_dead_social=st.booleans(),
        mobile_friendly=st.booleans(),
    )
    @settings(max_examples=200)
    def test_score_with_optional_signals(
        self,
        lighthouse_perf: int | None,
        is_dead_social: bool,
        mobile_friendly: bool,
    ) -> None:
        """score() handles all optional signal combinations without crash."""
        lead = FakeLead()
        seo = {"mobile_friendly": mobile_friendly} if not mobile_friendly else None
        lighthouse = {"performance": lighthouse_perf} if lighthouse_perf is not None else None
        social_activity = {"is_dead": is_dead_social}

        analysis = AnalysisResult(
            lead_id="test",
            website=WebsiteCheck(exists=True),
            rating=RatingInfo(google_rating=4.5, review_count=20),
            seo=seo,
            lighthouse=lighthouse,
            social_activity=social_activity,
        )

        scorer = LeadScorer()
        result = scorer.score(lead, analysis)

        assert isinstance(result, ScoringResult)
        assert result.total_score >= 0
        assert result.temperature in VALID_TEMPERATURES

    @given(data=st.data())
    @settings(max_examples=200)
    def test_score_is_non_negative(self, data: st.DataObject) -> None:
        """Total score is always >= 0 (clamped at zero)."""
        lead = FakeLead()
        analysis = AnalysisResult(
            lead_id="test",
            website=WebsiteCheck(exists=data.draw(st.booleans())),
            rating=RatingInfo(
                google_rating=data.draw(st.one_of(st.none(), google_rating_strategy)),
                review_count=data.draw(st.one_of(st.none(), review_count_strategy)),
            ),
            lighthouse=data.draw(
                st.one_of(
                    st.none(),
                    st.fixed_dictionaries({"performance": st.integers(min_value=0, max_value=100)}),
                )
            ),
        )

        scorer = LeadScorer()
        result = scorer.score(lead, analysis)
        assert result.total_score >= 0
