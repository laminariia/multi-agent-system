"""Business Analyzer — three-tier automated business analysis.

Analyzes lead businesses at quick/medium/deep depth, producing
AnalysisResult consumed by Lead Scorer for temperature classification.

Spec: docs/Full_work/specs/sales-agent-spec.md §Business Analyzer
Plan: docs/plans/2026-03-11-business-analyzer-plan.md
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from src.core.lead_scorer import AnalysisResult, RatingInfo, WebsiteCheck

logger = structlog.get_logger(__name__)

_VALID_TIERS = frozenset({"quick", "medium", "deep"})

# Timeout for HTTP requests in medium/deep tier helpers (seconds).
_HTTP_TIMEOUT = 10

# Social activity threshold: posts older than 90 days => "dead".
_SOCIAL_DEAD_DAYS = 90


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

    Args:
        llm_client: Optional ``LLMClient`` instance used for battlecard
            generation in the deep tier.  When *None*, a static template
            is returned instead.
    """

    def __init__(self, *, llm_client: Any | None = None) -> None:
        self._llm_client = llm_client

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
                result.lighthouse = await self._run_lighthouse(
                    getattr(lead, "website_url", ""),
                )
                result.seo = await self._check_basic_seo(
                    getattr(lead, "website_url", ""),
                )
            result.social_activity = await self._check_social_activity(lead)

        if tier == "deep":
            if result.website.exists:
                result.traffic = await self._estimate_traffic(
                    getattr(lead, "website_url", ""),
                )
                result.technologies = await self._detect_technologies(
                    getattr(lead, "website_url", ""),
                )
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
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
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
        """Run Lighthouse performance audit.

        Returns a stub result with the interface ready for
        Google Lighthouse CLI / PageSpeed Insights API integration.
        """
        logger.debug("business.lighthouse_stub", url=url)
        return {
            "performance": None,
            "accessibility": None,
            "seo": None,
            "best_practices": None,
            "note": "Lighthouse integration pending",
        }

    async def _check_basic_seo(self, url: str) -> dict[str, Any]:
        """Basic SEO check by fetching the page and parsing key HTML elements.

        Checks for: <title>, <meta name="description">, <h1>,
        and mobile viewport meta tag.

        Returns:
            Dict with boolean flags and an aggregate 0-100 score.
        """
        if not url:
            return {"error": "no_url", "score": 0}

        try:
            async with httpx.AsyncClient(
                timeout=_HTTP_TIMEOUT,
                follow_redirects=True,
            ) as client:
                resp = await client.get(url)
                html = resp.text

            has_title = bool(re.search(r"<title[^>]*>.+?</title>", html, re.IGNORECASE | re.DOTALL))
            has_description = bool(
                re.search(
                    r'<meta\s[^>]*name=["\']description["\'][^>]*>',
                    html,
                    re.IGNORECASE,
                ),
            )
            has_h1 = bool(re.search(r"<h1[^>]*>.+?</h1>", html, re.IGNORECASE | re.DOTALL))
            mobile_friendly = bool(
                re.search(r'<meta\s[^>]*name=["\']viewport["\'][^>]*>', html, re.IGNORECASE),
            )

            # Simple weighted score: title 30, description 25, h1 20, mobile 25
            score = 0
            if has_title:
                score += 30
            if has_description:
                score += 25
            if has_h1:
                score += 20
            if mobile_friendly:
                score += 25

            seo_result: dict[str, Any] = {
                "has_title": has_title,
                "has_description": has_description,
                "has_h1": has_h1,
                "mobile_friendly": mobile_friendly,
                "score": score,
            }

            logger.debug(
                "business.seo_checked",
                url=url,
                score=score,
            )
            return seo_result

        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError) as exc:
            logger.warning("business.seo_check_failed", url=url, error=str(exc))
            return {"error": str(exc), "score": 0}

    async def _check_social_activity(self, lead: Any) -> dict[str, Any]:
        """Check social media activity by looking at known social URLs.

        For each platform where the lead has a URL, attempts to fetch the
        page and look for date-like patterns indicating recent activity.
        When no social URLs are available, returns defaults.

        Returns:
            Dict with platforms found, estimated last activity, and
            active/dead classification.
        """
        platforms_found: list[str] = []
        last_activity_days: int | None = None

        social_urls: dict[str, str | None] = {
            "instagram": getattr(lead, "instagram_url", None),
            "vk": getattr(lead, "vk_url", None),
            "facebook": getattr(lead, "facebook_url", None),
        }

        for platform, platform_url in social_urls.items():
            if platform_url:
                platforms_found.append(platform)
                days = await self._probe_social_page(platform_url)
                if days is not None:
                    if last_activity_days is None or days < last_activity_days:
                        last_activity_days = days

        is_dead = (last_activity_days is not None and last_activity_days > _SOCIAL_DEAD_DAYS) or (
            len(platforms_found) > 0 and last_activity_days is None
        )

        result: dict[str, Any] = {
            "platforms_found": platforms_found,
            "last_activity_days": last_activity_days,
            "active": not is_dead,
            "is_dead": is_dead,
        }

        logger.debug(
            "business.social_activity_checked",
            platforms=platforms_found,
            last_activity_days=last_activity_days,
            is_dead=is_dead,
        )
        return result

    async def _probe_social_page(self, url: str) -> int | None:
        """Attempt to fetch a social page and extract last activity age.

        Looks for ISO-like date patterns (YYYY-MM-DD) in the HTML and
        returns the age in days of the most recent one found.

        Returns:
            Number of days since last activity, or None if unable to
            determine.
        """
        try:
            async with httpx.AsyncClient(
                timeout=_HTTP_TIMEOUT,
                follow_redirects=True,
            ) as client:
                resp = await client.get(url)
                html = resp.text

            # Find ISO date patterns like 2026-03-15
            date_matches = re.findall(r"\b(\d{4}-\d{2}-\d{2})\b", html)
            if not date_matches:
                return None

            now = datetime.now(tz=UTC)
            min_days: int | None = None

            for date_str in date_matches:
                try:
                    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(  # noqa: DTZ007
                        tzinfo=UTC,
                    )
                    delta = (now - dt).days
                    if delta >= 0 and (min_days is None or delta < min_days):
                        min_days = delta
                except ValueError:
                    continue

            return min_days

        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError):
            logger.debug("business.social_probe_failed", url=url)
            return None

    # ------------------------------------------------------------------
    # Deep tier tools
    # ------------------------------------------------------------------

    async def _estimate_traffic(self, url: str) -> dict[str, Any]:
        """Estimate website traffic.

        Returns a stub with the interface ready for SimilarWeb API
        integration.
        """
        logger.debug("business.traffic_stub", url=url)
        return {
            "monthly_visits": None,
            "bounce_rate": None,
            "avg_visit_duration": None,
            "source": "stub",
            "note": "SimilarWeb integration pending",
        }

    async def _detect_technologies(self, url: str) -> list[str]:
        """Detect website technologies.

        Returns a stub with the interface ready for Wappalyzer / BuiltWith
        integration.
        """
        logger.debug("business.tech_stub", url=url)
        # Return empty list; real implementation will call Wappalyzer API
        return []

    async def _get_competitors_nearby(self, lead: Any) -> list[dict[str, Any]]:
        """Find nearby competitors in the same category and area.

        Returns a stub with the interface ready for geo-based competitor
        scanning (H3 index + Yandex/2GIS API).
        """
        logger.debug(
            "business.competitors_stub",
            category=getattr(lead, "category", None),
        )
        return []

    async def _generate_battlecard(
        self,
        lead: Any,
        result: AnalysisResult,
    ) -> dict[str, Any]:
        """Generate AI Battlecard from accumulated analysis data.

        When an ``LLMClient`` is available, uses it to produce a rich
        battlecard with feature matrix, weak points, objection handlers,
        and value estimate.  Falls back to a static template otherwise.

        Returns:
            Battlecard dict matching the spec schema.
        """
        if self._llm_client is None:
            return self._static_battlecard(lead, result)

        try:
            return await self._llm_battlecard(lead, result)
        except Exception:
            logger.warning(
                "business.battlecard_llm_failed",
                lead_id=result.lead_id,
                exc_info=True,
            )
            return self._static_battlecard(lead, result)

    async def _llm_battlecard(
        self,
        lead: Any,
        result: AnalysisResult,
    ) -> dict[str, Any]:
        """Call LLM to generate a battlecard."""
        from langchain_core.messages import HumanMessage  # noqa: PLC0415

        company = getattr(lead, "company_name", None) or getattr(lead, "name", "Unknown")
        category = getattr(lead, "category", "unknown")
        website_exists = result.website.exists if result.website else False
        seo_score = (result.seo or {}).get("score", "N/A")
        mobile_friendly = (result.seo or {}).get("mobile_friendly", "N/A")
        social_active = (result.social_activity or {}).get("active", "N/A")
        social_dead = (result.social_activity or {}).get("is_dead", False)
        rating = result.rating.google_rating if result.rating else None
        review_count = result.rating.review_count if result.rating else None

        prompt = (
            "Generate a sales battlecard as JSON for the following business lead.\n\n"
            f"Company: {company}\n"
            f"Category: {category}\n"
            f"Has website: {website_exists}\n"
            f"SEO score: {seo_score}/100\n"
            f"Mobile friendly: {mobile_friendly}\n"
            f"Social media active: {social_active} (dead: {social_dead})\n"
            f"Google rating: {rating}\n"
            f"Review count: {review_count}\n\n"
            "Return a JSON object with these exact keys:\n"
            '- "feature_matrix": dict mapping feature names to '
            '{"lead": bool, "ideal": bool}\n'
            '- "weak_points": list of identified weaknesses from the analysis\n'
            '- "objection_handlers": list of dicts with "objection" and '
            '"response" keys\n'
            '- "key_selling_points": list of key selling points\n'
            '- "estimated_value": string estimate of project size\n'
            "Respond ONLY with the JSON object, no markdown fences."
        )

        response, _metrics = await self._llm_client.call(
            "sales_agent",
            [HumanMessage(content=prompt)],
            temperature=0.7,
            max_tokens=800,
        )

        content = response.content.strip()
        # Strip markdown code fences if present
        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1]) if len(lines) > 2 else content

        battlecard = json.loads(content)

        # Validate required keys, fill missing with defaults
        battlecard.setdefault("feature_matrix", {})
        battlecard.setdefault("weak_points", [])
        battlecard.setdefault("objection_handlers", [])
        battlecard.setdefault("key_selling_points", [])
        battlecard.setdefault("estimated_value", "unknown")
        battlecard["generated_at"] = datetime.now(tz=UTC).isoformat()

        logger.info(
            "business.battlecard_generated",
            lead_id=result.lead_id,
            source="llm",
        )
        return battlecard

    @staticmethod
    def _static_battlecard(lead: Any, result: AnalysisResult) -> dict[str, Any]:
        """Produce a static battlecard template when no LLM is available."""
        website_exists = result.website.exists if result.website else False
        mobile_friendly = (result.seo or {}).get("mobile_friendly", False)
        social_dead = (result.social_activity or {}).get("is_dead", False)

        weak_points: list[str] = []
        key_selling_points: list[str] = []

        if not website_exists:
            weak_points.append("No website present")
            key_selling_points.append(
                "A modern website can increase customer reach by 40%+",
            )
        else:
            seo_score = (result.seo or {}).get("score", 0)
            if seo_score < 50:
                weak_points.append(f"Low SEO score ({seo_score}/100)")
                key_selling_points.append(
                    "SEO optimization can significantly improve search visibility",
                )
            if not mobile_friendly:
                weak_points.append("Website is not mobile-friendly")
                key_selling_points.append(
                    "60%+ of web traffic comes from mobile devices",
                )

        if social_dead:
            weak_points.append("Social media accounts are inactive (>90 days)")
            key_selling_points.append(
                "Active social media presence drives customer engagement",
            )

        feature_matrix: dict[str, dict[str, bool]] = {
            "Website": {"lead": website_exists, "ideal": True},
            "Mobile-friendly": {"lead": mobile_friendly, "ideal": True},
            "Active social media": {"lead": not social_dead, "ideal": True},
        }

        objection_handlers: list[dict[str, str]] = [
            {
                "objection": "We already have enough customers",
                "response": (
                    "That is great, but a strong online presence helps "
                    "retain existing customers and attract new ones "
                    "as competitors grow."
                ),
            },
            {
                "objection": "We do not need a website",
                "response": (
                    "Studies show that 80% of consumers research "
                    "businesses online before visiting. Without a "
                    "website, you are invisible to these potential "
                    "customers."
                ),
            },
        ]

        return {
            "feature_matrix": feature_matrix,
            "weak_points": weak_points,
            "objection_handlers": objection_handlers,
            "key_selling_points": key_selling_points,
            "estimated_value": "unknown",
            "generated_at": datetime.now(tz=UTC).isoformat(),
        }
