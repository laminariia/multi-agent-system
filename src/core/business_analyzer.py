"""Business Analyzer — three-tier automated business analysis.

Analyzes lead businesses at quick/medium/deep depth, producing
AnalysisResult consumed by Lead Scorer for temperature classification.

Spec: docs/Full_work/specs/sales-agent-spec.md §Business Analyzer
Plan: docs/plans/2026-03-11-business-analyzer-plan.md
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from src.core.lead_scorer import AnalysisResult, RatingInfo, WebsiteCheck

logger = structlog.get_logger(__name__)

_VALID_TIERS = frozenset({"quick", "medium", "deep"})


# ---------------------------------------------------------------------------
# Additional models
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SocialPresence:
    """Social media presence flags."""

    instagram: bool = False
    vk: bool = False
    telegram: bool = False
    facebook: bool = False


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------


class BusinessAnalyzer:
    """Three-tier business analysis engine.

    Tiers:
    - quick (~5s): website check, rating, social presence
    - medium (~30s): + Lighthouse, SEO, social activity
    - deep (2-3m): + traffic, technologies, competitors, Battlecard
    """

    async def analyze(self, lead: Any, tier: str = "quick") -> AnalysisResult:
        """Analyze a lead's business at the specified depth tier."""
        if tier not in _VALID_TIERS:
            tier = "quick"

        result = AnalysisResult(
            lead_id=str(getattr(lead, "id", "")),
            tier=tier,
        )

        # Quick tier (always runs)
        result.website = await self._check_website(getattr(lead, "website_url", None))
        result.social = await self._check_social_presence(lead)
        result.rating = await self._get_rating(lead)

        if tier in ("medium", "deep"):
            if result.website.exists:
                result.lighthouse = await self._run_lighthouse(getattr(lead, "website_url", ""))
                result.seo = await self._check_basic_seo(getattr(lead, "website_url", ""))
            result.social_activity = await self._check_social_activity(lead)

        if tier == "deep":
            if result.website.exists:
                result.traffic = await self._estimate_traffic(getattr(lead, "website_url", ""))
                result.technologies = await self._detect_technologies(getattr(lead, "website_url", ""))
            result.competitors = await self._get_competitors_nearby(lead)
            result.battlecard = await self._generate_battlecard(lead, result)

        logger.info(
            "business.analyzed",
            lead_id=result.lead_id,
            tier=tier,
            website_exists=result.website.exists,
        )

        return result

    # ------------------------------------------------------------------
    # Quick tier tools
    # ------------------------------------------------------------------

    async def _check_website(self, url: str | None) -> WebsiteCheck:
        """Probe website existence and basic health."""
        if not url:
            return WebsiteCheck(exists=False)

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, follow_redirects=True)
                return WebsiteCheck(
                    exists=True,
                    ssl=url.startswith("https"),
                    status_code=resp.status_code,
                    load_time_ms=resp.elapsed.total_seconds() * 1000,
                )
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError):
            return WebsiteCheck(exists=False)

    async def _check_social_presence(self, lead: Any) -> SocialPresence:
        """Detect social media account presence from lead fields."""
        return SocialPresence(
            instagram=bool(getattr(lead, "instagram_url", None)),
            vk=bool(getattr(lead, "vk_url", None)),
            telegram=bool(getattr(lead, "telegram_username", None)),
            facebook=False,
        )

    async def _get_rating(self, lead: Any) -> RatingInfo:
        """Extract rating data from lead fields."""
        return RatingInfo(
            google_rating=getattr(lead, "google_rating", None),
            review_count=getattr(lead, "review_count", None),
        )

    # ------------------------------------------------------------------
    # Medium tier tools
    # ------------------------------------------------------------------

    async def _run_lighthouse(self, url: str) -> dict[str, Any]:
        """Run Lighthouse performance audit. Placeholder for external API."""
        logger.debug("business.lighthouse_placeholder", url=url)
        return {}

    async def _check_basic_seo(self, url: str) -> dict[str, Any]:
        """Basic SEO check. Placeholder for external probe."""
        logger.debug("business.seo_placeholder", url=url)
        return {}

    async def _check_social_activity(self, lead: Any) -> dict[str, Any]:
        """Check social media activity. Placeholder for scraping."""
        logger.debug("business.social_activity_placeholder")
        return {}

    # ------------------------------------------------------------------
    # Deep tier tools
    # ------------------------------------------------------------------

    async def _estimate_traffic(self, url: str) -> dict[str, Any]:
        """Estimate traffic via SimilarWeb. Placeholder."""
        logger.debug("business.traffic_placeholder", url=url)
        return {}

    async def _detect_technologies(self, url: str) -> list[str]:
        """Detect website technologies. Placeholder for Wappalyzer."""
        logger.debug("business.tech_placeholder", url=url)
        return []

    async def _get_competitors_nearby(self, lead: Any) -> list[dict[str, Any]]:
        """Find nearby competitors. Placeholder for geo query."""
        logger.debug("business.competitors_placeholder")
        return []

    async def _generate_battlecard(self, lead: Any, result: AnalysisResult) -> dict[str, Any]:
        """Generate AI Battlecard. Placeholder for LLM call."""
        logger.debug("business.battlecard_placeholder")
        return {}
