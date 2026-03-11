"""Lead Scorer — point-based scoring for lead temperature classification.

Determines lead priority (hot/warm/cold) and analysis depth tier
based on business signals. Used by Pipeline B after GeoScout/WebScout.

Spec: docs/Full_work/specs/sales-agent-spec.md §Lead Scorer
Plan: docs/plans/2026-03-11-lead-scorer-plan.md
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Analysis result models (shared with Business Analyzer)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class WebsiteCheck:
    """Website existence and basic health."""

    exists: bool = False
    ssl: bool = False
    status_code: int | None = None
    load_time_ms: float | None = None


@dataclass(slots=True)
class RatingInfo:
    """Google/Yandex rating and review data."""

    google_rating: float | None = None
    yandex_rating: float | None = None
    review_count: int | None = None


@dataclass(slots=True)
class AnalysisResult:
    """Business analysis result from Business Analyzer."""

    lead_id: str = ""
    tier: str = "quick"
    website: WebsiteCheck = field(default_factory=WebsiteCheck)
    rating: RatingInfo = field(default_factory=RatingInfo)
    social: Any = None  # SocialPresence from business_analyzer
    social_activity: dict[str, Any] | None = None
    seo: dict[str, Any] | None = None
    lighthouse: dict[str, Any] | None = None
    traffic: dict[str, Any] | None = None
    technologies: list[str] | None = None
    competitors: list[dict[str, Any]] | None = None
    battlecard: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Scoring models
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScoringRule:
    """A single scoring rule with condition callable."""

    name: str
    points: int
    condition: Callable[[Any, AnalysisResult], bool]
    description: str


@dataclass(frozen=True, slots=True)
class MatchedRule:
    """A rule that matched during scoring."""

    name: str
    points: int
    description: str


@dataclass(frozen=True, slots=True)
class ScoringResult:
    """Final scoring output for a lead."""

    lead_id: str
    total_score: int
    temperature: str
    analysis_tier: str
    matched_rules: list[MatchedRule]


# ---------------------------------------------------------------------------
# Categories & Rules
# ---------------------------------------------------------------------------

HIGH_VALUE_CATEGORIES: frozenset[str] = frozenset(
    {
        "clinic",
        "dentist",
        "medical",
        "стоматология",
        "клиника",
        "restaurant",
        "cafe",
        "ресторан",
        "кафе",
        "auto_service",
        "car_dealer",
        "автосервис",
        "автосалон",
        "beauty_salon",
        "spa",
        "салон красоты",
        "hotel",
        "отель",
        "гостиница",
        "fitness",
        "gym",
        "фитнес",
    }
)

SCORING_RULES: list[ScoringRule] = [
    # Positive signals
    ScoringRule(
        name="no_website",
        points=3,
        condition=lambda lead, analysis: not analysis.website.exists,
        description="Нет сайта вообще",
    ),
    ScoringRule(
        name="no_mobile",
        points=2,
        condition=lambda lead, analysis: (
            analysis.website.exists and analysis.seo is not None and not analysis.seo.get("mobile_friendly", True)
        ),
        description="Сайт есть, нет мобильной версии",
    ),
    ScoringRule(
        name="active_business",
        points=2,
        condition=lambda lead, analysis: (
            (analysis.rating.review_count or 0) >= 10 and (analysis.rating.google_rating or 0) >= 4.0
        ),
        description="Много отзывов + высокий рейтинг (бизнес живой)",
    ),
    ScoringRule(
        name="dead_social",
        points=1,
        condition=lambda lead, analysis: (
            analysis.social_activity is not None and analysis.social_activity.get("is_dead", False)
        ),
        description="Мёртвые соцсети (>3 месяцев без поста)",
    ),
    ScoringRule(
        name="high_value_category",
        points=1,
        condition=lambda lead, _: (
            getattr(lead, "category", None) is not None and lead.category in HIGH_VALUE_CATEGORIES
        ),
        description="Высокочековая категория",
    ),
    # Negative signals
    ScoringRule(
        name="few_reviews",
        points=-1,
        condition=lambda lead, analysis: (analysis.rating.review_count or 0) < 10,
        description="Мало отзывов (<10) — возможно новый/мёртвый бизнес",
    ),
    ScoringRule(
        name="good_website",
        points=-2,
        condition=lambda lead, analysis: (
            analysis.lighthouse is not None and (analysis.lighthouse.get("performance", 0) or 0) > 80
        ),
        description="Хороший сайт (Lighthouse > 80) — не нуждается",
    ),
]


# ---------------------------------------------------------------------------
# Scorer
# ---------------------------------------------------------------------------


class LeadScorer:
    """Point-based lead scoring engine.

    Applies scoring rules to a lead + analysis result, producing
    a temperature classification (hot/warm/cold) and recommended
    analysis tier (deep/medium/quick).
    """

    def score(self, lead: Any, analysis: AnalysisResult) -> ScoringResult:
        """Score a lead based on business signals.

        Rules that raise due to missing data are silently skipped.
        """
        matched: list[MatchedRule] = []
        total = 0

        for rule in SCORING_RULES:
            try:
                if rule.condition(lead, analysis):
                    matched.append(
                        MatchedRule(
                            name=rule.name,
                            points=rule.points,
                            description=rule.description,
                        )
                    )
                    total += rule.points
            except (KeyError, TypeError, AttributeError):
                continue

        total = max(0, total)
        temperature = self._classify_temperature(total)
        analysis_tier = self._determine_analysis_tier(total)

        result = ScoringResult(
            lead_id=str(getattr(lead, "id", "")),
            total_score=total,
            temperature=temperature,
            analysis_tier=analysis_tier,
            matched_rules=matched,
        )

        logger.info(
            "lead.scored",
            lead_id=result.lead_id,
            score=total,
            temperature=temperature,
            tier=analysis_tier,
            rules=[r.name for r in matched],
        )

        return result

    def _classify_temperature(self, score: int) -> str:
        """Classify lead temperature from score."""
        if score >= 5:
            return "hot"
        if score >= 3:
            return "warm"
        return "cold"

    def _determine_analysis_tier(self, score: int) -> str:
        """Determine Business Analyzer tier from score."""
        if score >= 5:
            return "deep"
        if score >= 3:
            return "medium"
        return "quick"
