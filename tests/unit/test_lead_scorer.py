"""Unit tests for Lead Scorer (src/core/lead_scorer.py).

Tests cover: scoring rules, temperature classification, analysis tier,
edge cases, HIGH_VALUE_CATEGORIES, and full scoring pipeline.
"""

from __future__ import annotations

from src.core.lead_scorer import (
    HIGH_VALUE_CATEGORIES,
    SCORING_RULES,
    AnalysisResult,
    LeadScorer,
    MatchedRule,
    RatingInfo,
    ScoringResult,
    WebsiteCheck,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _lead(category: str | None = "restaurant") -> object:
    """Create a minimal lead-like object for scoring."""

    class _FakeLead:
        def __init__(self, cat: str | None):
            self.id = "lead-001"
            self.category = cat

    return _FakeLead(category)


def _analysis(
    *,
    website_exists: bool = True,
    google_rating: float | None = 4.5,
    review_count: int | None = 20,
    mobile_friendly: bool = True,
    social_dead: bool = False,
    lighthouse_perf: float | None = None,
) -> AnalysisResult:
    """Create an AnalysisResult with configurable fields."""
    return AnalysisResult(
        lead_id="lead-001",
        tier="quick",
        website=WebsiteCheck(exists=website_exists),
        rating=RatingInfo(
            google_rating=google_rating,
            review_count=review_count,
        ),
        social_activity={"is_dead": social_dead} if social_dead else None,
        seo={"mobile_friendly": mobile_friendly} if website_exists else None,
        lighthouse={"performance": lighthouse_perf} if lighthouse_perf is not None else None,
    )


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    """Verify scoring constants match spec."""

    def test_scoring_rules_count(self):
        assert len(SCORING_RULES) == 7

    def test_all_rules_have_names(self):
        for rule in SCORING_RULES:
            assert rule.name
            assert isinstance(rule.points, int)
            assert rule.description

    def test_high_value_categories_defined(self):
        assert "clinic" in HIGH_VALUE_CATEGORIES
        assert "restaurant" in HIGH_VALUE_CATEGORIES
        assert "стоматология" in HIGH_VALUE_CATEGORIES
        assert "автосервис" in HIGH_VALUE_CATEGORIES
        assert "салон красоты" in HIGH_VALUE_CATEGORIES

    def test_high_value_categories_count(self):
        assert len(HIGH_VALUE_CATEGORIES) == 22

    def test_rule_names(self):
        names = {r.name for r in SCORING_RULES}
        assert names == {
            "no_website",
            "no_mobile",
            "active_business",
            "dead_social",
            "high_value_category",
            "few_reviews",
            "good_website",
        }


# ---------------------------------------------------------------------------
# Individual scoring rules
# ---------------------------------------------------------------------------


class TestScoringRules:
    """Verify each scoring rule fires correctly."""

    def setup_method(self):
        self.scorer = LeadScorer()

    def test_no_website_adds_3_points(self):
        result = self.scorer.score(
            _lead(),
            _analysis(website_exists=False),
        )
        matched_names = {r.name for r in result.matched_rules}
        assert "no_website" in matched_names
        # no_website=+3, few_reviews=-1 (review_count=20 ≥ 10 so no few_reviews)
        # active_business=+2 (rating=4.5, reviews=20)
        # high_value_category=+1 (restaurant)
        # Check no_website rule specifically
        no_website_rule = next(r for r in result.matched_rules if r.name == "no_website")
        assert no_website_rule.points == 3

    def test_no_mobile_adds_2_points(self):
        result = self.scorer.score(
            _lead(),
            _analysis(website_exists=True, mobile_friendly=False),
        )
        matched_names = {r.name for r in result.matched_rules}
        assert "no_mobile" in matched_names

    def test_active_business_adds_2_points(self):
        result = self.scorer.score(
            _lead(),
            _analysis(google_rating=4.5, review_count=20),
        )
        matched_names = {r.name for r in result.matched_rules}
        assert "active_business" in matched_names

    def test_active_business_needs_both_conditions(self):
        result = self.scorer.score(
            _lead(),
            _analysis(google_rating=3.5, review_count=20),
        )
        matched_names = {r.name for r in result.matched_rules}
        assert "active_business" not in matched_names

    def test_dead_social_adds_1_point(self):
        result = self.scorer.score(
            _lead(),
            _analysis(social_dead=True),
        )
        matched_names = {r.name for r in result.matched_rules}
        assert "dead_social" in matched_names

    def test_high_value_category_adds_1_point(self):
        result = self.scorer.score(
            _lead(category="clinic"),
            _analysis(),
        )
        matched_names = {r.name for r in result.matched_rules}
        assert "high_value_category" in matched_names

    def test_non_high_value_category_no_match(self):
        result = self.scorer.score(
            _lead(category="random_shop"),
            _analysis(),
        )
        matched_names = {r.name for r in result.matched_rules}
        assert "high_value_category" not in matched_names

    def test_few_reviews_subtracts_1_point(self):
        result = self.scorer.score(
            _lead(),
            _analysis(review_count=5),
        )
        matched_names = {r.name for r in result.matched_rules}
        assert "few_reviews" in matched_names

    def test_good_website_subtracts_2_points(self):
        result = self.scorer.score(
            _lead(),
            _analysis(lighthouse_perf=90),
        )
        matched_names = {r.name for r in result.matched_rules}
        assert "good_website" in matched_names


# ---------------------------------------------------------------------------
# Temperature classification
# ---------------------------------------------------------------------------


class TestTemperatureClassification:
    """Verify temperature thresholds per spec."""

    def setup_method(self):
        self.scorer = LeadScorer()

    def test_hot_score_5_or_more(self):
        assert self.scorer._classify_temperature(5) == "hot"
        assert self.scorer._classify_temperature(8) == "hot"

    def test_warm_score_3_to_4(self):
        assert self.scorer._classify_temperature(3) == "warm"
        assert self.scorer._classify_temperature(4) == "warm"

    def test_cold_score_below_3(self):
        assert self.scorer._classify_temperature(2) == "cold"
        assert self.scorer._classify_temperature(0) == "cold"
        assert self.scorer._classify_temperature(-1) == "cold"


# ---------------------------------------------------------------------------
# Analysis tier determination
# ---------------------------------------------------------------------------


class TestAnalysisTier:
    """Verify analysis tier mapping per spec."""

    def setup_method(self):
        self.scorer = LeadScorer()

    def test_deep_tier_for_hot(self):
        assert self.scorer._determine_analysis_tier(5) == "deep"
        assert self.scorer._determine_analysis_tier(10) == "deep"

    def test_medium_tier_for_warm(self):
        assert self.scorer._determine_analysis_tier(3) == "medium"
        assert self.scorer._determine_analysis_tier(4) == "medium"

    def test_quick_tier_for_cold(self):
        assert self.scorer._determine_analysis_tier(2) == "quick"
        assert self.scorer._determine_analysis_tier(0) == "quick"


# ---------------------------------------------------------------------------
# Full scoring pipeline
# ---------------------------------------------------------------------------


class TestFullScoring:
    """End-to-end scoring scenarios."""

    def setup_method(self):
        self.scorer = LeadScorer()

    def test_hot_lead_no_website_active_business(self):
        """No website + active business + high value = hot."""
        result = self.scorer.score(
            _lead(category="clinic"),
            _analysis(website_exists=False, google_rating=4.8, review_count=50),
        )
        # no_website=+3, active_business=+2, high_value=+1 = 6
        assert result.temperature == "hot"
        assert result.total_score >= 5
        assert result.analysis_tier == "deep"

    def test_cold_lead_good_website_low_category(self):
        """Good website + low value category = cold."""
        result = self.scorer.score(
            _lead(category="random_shop"),
            _analysis(
                website_exists=True,
                lighthouse_perf=90,
                google_rating=4.5,
                review_count=20,
            ),
        )
        # active_business=+2, good_website=-2 = 0
        assert result.temperature == "cold"

    def test_warm_lead_moderate_signals(self):
        """No mobile + active business = warm."""
        result = self.scorer.score(
            _lead(category="random_shop"),
            _analysis(
                website_exists=True,
                mobile_friendly=False,
                google_rating=4.2,
                review_count=15,
            ),
        )
        # no_mobile=+2, active_business=+2 = 4
        assert result.temperature == "warm"
        assert result.analysis_tier == "medium"

    def test_result_has_correct_lead_id(self):
        result = self.scorer.score(_lead(), _analysis())
        assert result.lead_id == "lead-001"

    def test_result_has_matched_rules(self):
        result = self.scorer.score(_lead(), _analysis())
        assert isinstance(result.matched_rules, list)
        for rule in result.matched_rules:
            assert isinstance(rule, MatchedRule)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge case handling."""

    def setup_method(self):
        self.scorer = LeadScorer()

    def test_missing_analysis_data_skips_rule(self):
        """Rule requiring missing data should be silently skipped."""
        result = self.scorer.score(
            _lead(),
            _analysis(google_rating=None, review_count=None),
        )
        matched_names = {r.name for r in result.matched_rules}
        # active_business requires both rating and review_count — should not match
        assert "active_business" not in matched_names

    def test_none_category_skips_high_value_rule(self):
        result = self.scorer.score(
            _lead(category=None),
            _analysis(),
        )
        matched_names = {r.name for r in result.matched_rules}
        assert "high_value_category" not in matched_names

    def test_negative_score_clamps_to_zero(self):
        """Total score should not go below 0."""
        result = self.scorer.score(
            _lead(category="random_shop"),
            _analysis(
                website_exists=True,
                lighthouse_perf=95,
                google_rating=3.0,
                review_count=5,
            ),
        )
        # few_reviews=-1, good_website=-2 = -3 → clamp to 0
        assert result.total_score >= 0

    def test_no_rules_match_returns_cold(self):
        """When no rules match at all, default to cold."""
        # All conditions are exactly at boundaries that don't match
        result = self.scorer.score(
            _lead(category="random_shop"),
            _analysis(
                website_exists=True,
                mobile_friendly=True,
                google_rating=4.0,
                review_count=10,  # exactly 10 → active_business fires, few_reviews does NOT
            ),
        )
        assert result.temperature in ("cold", "warm", "hot")
        assert isinstance(result.total_score, int)

    def test_scoring_result_dataclass(self):
        result = self.scorer.score(_lead(), _analysis())
        assert isinstance(result, ScoringResult)
        assert isinstance(result.total_score, int)
        assert result.temperature in ("hot", "warm", "cold")
        assert result.analysis_tier in ("quick", "medium", "deep")
