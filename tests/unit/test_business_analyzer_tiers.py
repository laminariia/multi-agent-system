"""Unit tests for BusinessAnalyzer medium and deep tiers.

Tests cover: SEO check, social activity, Lighthouse stub, traffic/tech stubs,
competitor stub, battlecard generation (LLM + static fallback), tier routing,
and error handling.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.core.business_analyzer import _SOCIAL_DEAD_DAYS, BusinessAnalyzer
from src.core.lead_scorer import AnalysisResult, RatingInfo, WebsiteCheck

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _lead(
    *,
    website_url: str | None = "https://example.com",
    google_rating: float | None = 4.5,
    review_count: int | None = 20,
    category: str = "restaurant",
    instagram_url: str | None = "https://instagram.com/test",
    vk_url: str | None = None,
    facebook_url: str | None = None,
    company_name: str = "Test Corp",
) -> object:
    """Create a minimal lead-like object for testing."""

    class _FakeLead:
        def __init__(self) -> None:
            self.id = "lead-001"
            self.website_url = website_url
            self.google_rating = google_rating
            self.review_count = review_count
            self.category = category
            self.instagram_url = instagram_url
            self.vk_url = vk_url
            self.facebook_url = facebook_url
            self.telegram_username = "@test"
            self.company_name = company_name
            self.name = company_name

    return _FakeLead()


def _mock_httpx_response(
    *,
    status_code: int = 200,
    text: str = "",
    elapsed_seconds: float = 0.5,
) -> AsyncMock:
    """Build a mock httpx response."""
    mock_elapsed = MagicMock()
    mock_elapsed.total_seconds.return_value = elapsed_seconds
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.elapsed = mock_elapsed
    resp.url = "https://example.com"
    return resp


def _mock_async_client(response: object) -> AsyncMock:
    """Build a mock httpx.AsyncClient context manager."""
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.get = AsyncMock(return_value=response)
    return client


# ---------------------------------------------------------------------------
# Medium tier — _check_basic_seo
# ---------------------------------------------------------------------------


class TestCheckBasicSeo:
    """SEO check parses HTML for key elements."""

    async def test_full_seo_page_returns_score_100(self):
        html = (
            "<html><head>"
            "<title>My Site</title>"
            '<meta name="description" content="desc">'
            '<meta name="viewport" content="width=device-width">'
            "</head><body><h1>Hello</h1></body></html>"
        )
        resp = _mock_httpx_response(text=html)
        client = _mock_async_client(resp)

        analyzer = BusinessAnalyzer()
        with patch("src.core.business_analyzer.httpx.AsyncClient", return_value=client):
            result = await analyzer._check_basic_seo("https://example.com")

        assert result["has_title"] is True
        assert result["has_description"] is True
        assert result["has_h1"] is True
        assert result["mobile_friendly"] is True
        assert result["score"] == 100

    async def test_missing_title_reduces_score(self):
        html = (
            "<html><head>"
            '<meta name="description" content="desc">'
            '<meta name="viewport" content="width=device-width">'
            "</head><body><h1>Hello</h1></body></html>"
        )
        resp = _mock_httpx_response(text=html)
        client = _mock_async_client(resp)

        analyzer = BusinessAnalyzer()
        with patch("src.core.business_analyzer.httpx.AsyncClient", return_value=client):
            result = await analyzer._check_basic_seo("https://example.com")

        assert result["has_title"] is False
        assert result["score"] == 70  # 25 + 20 + 25

    async def test_empty_page_returns_score_0(self):
        resp = _mock_httpx_response(text="<html><body></body></html>")
        client = _mock_async_client(resp)

        analyzer = BusinessAnalyzer()
        with patch("src.core.business_analyzer.httpx.AsyncClient", return_value=client):
            result = await analyzer._check_basic_seo("https://example.com")

        assert result["score"] == 0
        assert result["has_title"] is False
        assert result["has_description"] is False
        assert result["has_h1"] is False
        assert result["mobile_friendly"] is False

    async def test_no_url_returns_error(self):
        analyzer = BusinessAnalyzer()
        result = await analyzer._check_basic_seo("")
        assert result["error"] == "no_url"
        assert result["score"] == 0

    async def test_timeout_returns_error(self):
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))

        analyzer = BusinessAnalyzer()
        with patch("src.core.business_analyzer.httpx.AsyncClient", return_value=client):
            result = await analyzer._check_basic_seo("https://example.com")

        assert "error" in result
        assert result["score"] == 0

    async def test_connect_error_returns_error(self):
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

        analyzer = BusinessAnalyzer()
        with patch("src.core.business_analyzer.httpx.AsyncClient", return_value=client):
            result = await analyzer._check_basic_seo("https://example.com")

        assert "error" in result
        assert result["score"] == 0

    async def test_case_insensitive_tag_matching(self):
        html = (
            "<html><head>"
            "<TITLE>My Site</TITLE>"
            '<META NAME="DESCRIPTION" CONTENT="desc">'
            '<META NAME="VIEWPORT" CONTENT="width=device-width">'
            "</head><body><H1>Hello</H1></body></html>"
        )
        resp = _mock_httpx_response(text=html)
        client = _mock_async_client(resp)

        analyzer = BusinessAnalyzer()
        with patch("src.core.business_analyzer.httpx.AsyncClient", return_value=client):
            result = await analyzer._check_basic_seo("https://example.com")

        assert result["score"] == 100


# ---------------------------------------------------------------------------
# Medium tier — _run_lighthouse (stub)
# ---------------------------------------------------------------------------


class TestRunLighthouse:
    """Lighthouse returns a well-structured stub."""

    async def test_returns_stub_with_expected_keys(self):
        analyzer = BusinessAnalyzer()
        result = await analyzer._run_lighthouse("https://example.com")

        assert result["performance"] is None
        assert result["accessibility"] is None
        assert result["seo"] is None
        assert result["best_practices"] is None
        assert "note" in result


# ---------------------------------------------------------------------------
# Medium tier — _check_social_activity
# ---------------------------------------------------------------------------


class TestCheckSocialActivity:
    """Social activity detection from platform URLs."""

    async def test_no_social_urls_returns_empty(self):
        lead = _lead(instagram_url=None, vk_url=None, facebook_url=None)
        analyzer = BusinessAnalyzer()
        result = await analyzer._check_social_activity(lead)

        assert result["platforms_found"] == []
        assert result["last_activity_days"] is None
        assert result["active"] is True
        assert result["is_dead"] is False

    async def test_platforms_found_lists_present_urls(self):
        lead = _lead(
            instagram_url="https://instagram.com/test",
            vk_url="https://vk.com/test",
        )
        analyzer = BusinessAnalyzer()

        with patch.object(analyzer, "_probe_social_page", new_callable=AsyncMock) as mock_probe:
            mock_probe.return_value = 10  # 10 days ago
            result = await analyzer._check_social_activity(lead)

        assert "instagram" in result["platforms_found"]
        assert "vk" in result["platforms_found"]
        assert result["active"] is True

    async def test_dead_social_when_old_activity(self):
        lead = _lead(instagram_url="https://instagram.com/test")
        analyzer = BusinessAnalyzer()

        with patch.object(analyzer, "_probe_social_page", new_callable=AsyncMock) as mock_probe:
            mock_probe.return_value = _SOCIAL_DEAD_DAYS + 10
            result = await analyzer._check_social_activity(lead)

        assert result["is_dead"] is True
        assert result["active"] is False

    async def test_dead_when_probe_returns_none(self):
        """If we have social URLs but cannot determine activity, mark as dead."""
        lead = _lead(instagram_url="https://instagram.com/test")
        analyzer = BusinessAnalyzer()

        with patch.object(analyzer, "_probe_social_page", new_callable=AsyncMock) as mock_probe:
            mock_probe.return_value = None
            result = await analyzer._check_social_activity(lead)

        assert result["is_dead"] is True
        assert result["last_activity_days"] is None

    async def test_picks_most_recent_activity(self):
        """When multiple platforms, use the most recent activity."""
        lead = _lead(
            instagram_url="https://instagram.com/test",
            vk_url="https://vk.com/test",
        )
        analyzer = BusinessAnalyzer()

        with patch.object(analyzer, "_probe_social_page", new_callable=AsyncMock) as mock_probe:
            # Instagram: 5 days, VK: 50 days
            mock_probe.side_effect = [5, 50]
            result = await analyzer._check_social_activity(lead)

        assert result["last_activity_days"] == 5
        assert result["active"] is True


# ---------------------------------------------------------------------------
# Medium tier — _probe_social_page
# ---------------------------------------------------------------------------


class TestProbeSocialPage:
    """Page probe extracts dates from HTML."""

    async def test_extracts_recent_date(self):
        html = '<div class="post">Posted on 2026-03-18</div>'
        resp = _mock_httpx_response(text=html)
        client = _mock_async_client(resp)

        analyzer = BusinessAnalyzer()
        with patch("src.core.business_analyzer.httpx.AsyncClient", return_value=client):
            days = await analyzer._probe_social_page("https://instagram.com/test")

        assert days is not None
        assert days >= 0

    async def test_returns_none_when_no_dates(self):
        html = "<html><body>No dates here</body></html>"
        resp = _mock_httpx_response(text=html)
        client = _mock_async_client(resp)

        analyzer = BusinessAnalyzer()
        with patch("src.core.business_analyzer.httpx.AsyncClient", return_value=client):
            days = await analyzer._probe_social_page("https://instagram.com/test")

        assert days is None

    async def test_returns_none_on_connection_error(self):
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.get = AsyncMock(side_effect=httpx.ConnectError("fail"))

        analyzer = BusinessAnalyzer()
        with patch("src.core.business_analyzer.httpx.AsyncClient", return_value=client):
            days = await analyzer._probe_social_page("https://instagram.com/test")

        assert days is None


# ---------------------------------------------------------------------------
# Deep tier — _estimate_traffic (stub)
# ---------------------------------------------------------------------------


class TestEstimateTraffic:
    """Traffic estimation returns a well-structured stub."""

    async def test_returns_stub_with_expected_keys(self):
        analyzer = BusinessAnalyzer()
        result = await analyzer._estimate_traffic("https://example.com")

        assert result["monthly_visits"] is None
        assert result["source"] == "stub"
        assert "note" in result

    async def test_stub_has_bounce_rate_field(self):
        analyzer = BusinessAnalyzer()
        result = await analyzer._estimate_traffic("https://example.com")
        assert "bounce_rate" in result


# ---------------------------------------------------------------------------
# Deep tier — _detect_technologies (stub)
# ---------------------------------------------------------------------------


class TestDetectTechnologies:
    """Technology detection returns empty stub list."""

    async def test_returns_empty_list(self):
        analyzer = BusinessAnalyzer()
        result = await analyzer._detect_technologies("https://example.com")
        assert result == []
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Deep tier — _get_competitors_nearby (stub)
# ---------------------------------------------------------------------------


class TestGetCompetitorsNearby:
    """Competitor scan returns empty stub list."""

    async def test_returns_empty_list(self):
        lead = _lead()
        analyzer = BusinessAnalyzer()
        result = await analyzer._get_competitors_nearby(lead)
        assert result == []
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Deep tier — _generate_battlecard
# ---------------------------------------------------------------------------


class TestGenerateBattlecard:
    """Battlecard generation with LLM and static fallback."""

    async def test_static_battlecard_without_llm(self):
        """When no LLM client, returns static template."""
        analyzer = BusinessAnalyzer()
        result_data = AnalysisResult(
            lead_id="lead-001",
            tier="deep",
            website=WebsiteCheck(exists=False),
            seo=None,
            social_activity={"is_dead": True, "active": False},
        )

        battlecard = await analyzer._generate_battlecard(_lead(), result_data)

        assert "feature_matrix" in battlecard
        assert "weak_points" in battlecard
        assert "objection_handlers" in battlecard
        assert "key_selling_points" in battlecard
        assert "generated_at" in battlecard
        assert "No website present" in battlecard["weak_points"]

    async def test_static_battlecard_with_website_no_mobile(self):
        """Static template identifies non-mobile-friendly sites."""
        analyzer = BusinessAnalyzer()
        result_data = AnalysisResult(
            lead_id="lead-001",
            tier="deep",
            website=WebsiteCheck(exists=True),
            seo={"mobile_friendly": False, "score": 30},
            social_activity={"is_dead": False, "active": True},
        )

        battlecard = await analyzer._generate_battlecard(_lead(), result_data)

        assert "Website is not mobile-friendly" in battlecard["weak_points"]
        assert any("Low SEO" in wp for wp in battlecard["weak_points"])

    async def test_static_battlecard_with_good_seo(self):
        """Static template does not flag good SEO."""
        analyzer = BusinessAnalyzer()
        result_data = AnalysisResult(
            lead_id="lead-001",
            tier="deep",
            website=WebsiteCheck(exists=True),
            seo={"mobile_friendly": True, "score": 80},
            social_activity={"is_dead": False, "active": True},
        )

        battlecard = await analyzer._generate_battlecard(_lead(), result_data)

        # No weak points about SEO or mobile since both are good
        assert not any("Low SEO" in wp for wp in battlecard["weak_points"])
        assert "Website is not mobile-friendly" not in battlecard["weak_points"]

    async def test_llm_battlecard_parses_json_response(self):
        """When LLM is available, calls it and parses JSON."""
        llm_response_content = json.dumps(
            {
                "feature_matrix": {"Website": {"lead": True, "ideal": True}},
                "weak_points": ["No mobile"],
                "objection_handlers": [{"objection": "Too expensive", "response": "ROI is 3x"}],
                "key_selling_points": ["Modern design"],
                "estimated_value": "$5,000",
            }
        )

        mock_response = MagicMock()
        mock_response.content = llm_response_content

        mock_llm = AsyncMock()
        mock_llm.call = AsyncMock(return_value=(mock_response, MagicMock()))

        analyzer = BusinessAnalyzer(llm_client=mock_llm)
        result_data = AnalysisResult(
            lead_id="lead-001",
            tier="deep",
            website=WebsiteCheck(exists=True),
            rating=RatingInfo(google_rating=4.5, review_count=20),
            seo={"score": 60, "mobile_friendly": True},
            social_activity={"active": True, "is_dead": False},
        )

        battlecard = await analyzer._generate_battlecard(_lead(), result_data)

        assert battlecard["estimated_value"] == "$5,000"
        assert "No mobile" in battlecard["weak_points"]
        assert "generated_at" in battlecard
        mock_llm.call.assert_awaited_once()

    async def test_llm_battlecard_strips_markdown_fences(self):
        """LLM response wrapped in markdown fences is handled."""
        inner = json.dumps(
            {
                "feature_matrix": {},
                "weak_points": [],
                "objection_handlers": [],
                "key_selling_points": [],
                "estimated_value": "unknown",
            }
        )
        llm_response_content = f"```json\n{inner}\n```"

        mock_response = MagicMock()
        mock_response.content = llm_response_content

        mock_llm = AsyncMock()
        mock_llm.call = AsyncMock(return_value=(mock_response, MagicMock()))

        analyzer = BusinessAnalyzer(llm_client=mock_llm)
        result_data = AnalysisResult(
            lead_id="lead-001",
            tier="deep",
            website=WebsiteCheck(exists=True),
            rating=RatingInfo(),
        )

        battlecard = await analyzer._generate_battlecard(_lead(), result_data)
        assert isinstance(battlecard, dict)
        assert "generated_at" in battlecard

    async def test_llm_failure_falls_back_to_static(self):
        """When LLM call raises, falls back to static template."""
        mock_llm = AsyncMock()
        mock_llm.call = AsyncMock(side_effect=RuntimeError("LLM down"))

        analyzer = BusinessAnalyzer(llm_client=mock_llm)
        result_data = AnalysisResult(
            lead_id="lead-001",
            tier="deep",
            website=WebsiteCheck(exists=False),
        )

        battlecard = await analyzer._generate_battlecard(_lead(), result_data)

        assert "feature_matrix" in battlecard
        assert "No website present" in battlecard["weak_points"]

    async def test_llm_invalid_json_falls_back_to_static(self):
        """When LLM returns non-JSON, falls back to static."""
        mock_response = MagicMock()
        mock_response.content = "This is not JSON at all"

        mock_llm = AsyncMock()
        mock_llm.call = AsyncMock(return_value=(mock_response, MagicMock()))

        analyzer = BusinessAnalyzer(llm_client=mock_llm)
        result_data = AnalysisResult(
            lead_id="lead-001",
            tier="deep",
            website=WebsiteCheck(exists=False),
        )

        battlecard = await analyzer._generate_battlecard(_lead(), result_data)

        assert "feature_matrix" in battlecard
        assert "generated_at" in battlecard

    async def test_llm_partial_json_fills_defaults(self):
        """When LLM returns partial JSON, missing keys get defaults."""
        partial = json.dumps({"weak_points": ["Some issue"]})
        mock_response = MagicMock()
        mock_response.content = partial

        mock_llm = AsyncMock()
        mock_llm.call = AsyncMock(return_value=(mock_response, MagicMock()))

        analyzer = BusinessAnalyzer(llm_client=mock_llm)
        result_data = AnalysisResult(
            lead_id="lead-001",
            tier="deep",
            website=WebsiteCheck(exists=True),
            rating=RatingInfo(),
        )

        battlecard = await analyzer._generate_battlecard(_lead(), result_data)

        assert battlecard["weak_points"] == ["Some issue"]
        assert battlecard["feature_matrix"] == {}  # default
        assert battlecard["objection_handlers"] == []  # default
        assert battlecard["estimated_value"] == "unknown"  # default


# ---------------------------------------------------------------------------
# Tier routing — medium tier runs correct methods
# ---------------------------------------------------------------------------


class TestMediumTierRouting:
    """Medium tier calls SEO, Lighthouse, and social activity."""

    async def test_medium_calls_seo_check(self):
        analyzer = BusinessAnalyzer()
        lead = _lead()

        with (
            patch.object(analyzer, "_check_website", new_callable=AsyncMock) as mock_web,
            patch.object(analyzer, "_get_rating", new_callable=AsyncMock) as mock_rating,
            patch.object(analyzer, "_run_lighthouse", new_callable=AsyncMock) as mock_lh,
            patch.object(analyzer, "_check_basic_seo", new_callable=AsyncMock) as mock_seo,
            patch.object(analyzer, "_check_social_activity", new_callable=AsyncMock) as mock_social,
        ):
            mock_web.return_value = WebsiteCheck(exists=True)
            mock_rating.return_value = RatingInfo()
            mock_lh.return_value = {"performance": None}
            mock_seo.return_value = {"score": 50, "mobile_friendly": True}
            mock_social.return_value = {"is_dead": False}

            result = await analyzer.analyze(lead, tier="medium")

            mock_seo.assert_awaited_once()
            assert result.seo is not None
            assert result.tier == "medium"

    async def test_medium_skips_deep_methods(self):
        analyzer = BusinessAnalyzer()
        lead = _lead()

        with (
            patch.object(analyzer, "_check_website", new_callable=AsyncMock) as mock_web,
            patch.object(analyzer, "_get_rating", new_callable=AsyncMock) as mock_rating,
            patch.object(analyzer, "_run_lighthouse", new_callable=AsyncMock) as mock_lh,
            patch.object(analyzer, "_check_basic_seo", new_callable=AsyncMock) as mock_seo,
            patch.object(analyzer, "_check_social_activity", new_callable=AsyncMock) as mock_social,
            patch.object(analyzer, "_estimate_traffic", new_callable=AsyncMock) as mock_traffic,
            patch.object(analyzer, "_generate_battlecard", new_callable=AsyncMock) as mock_bc,
        ):
            mock_web.return_value = WebsiteCheck(exists=True)
            mock_rating.return_value = RatingInfo()
            mock_lh.return_value = {}
            mock_seo.return_value = {}
            mock_social.return_value = {}

            await analyzer.analyze(lead, tier="medium")

            mock_traffic.assert_not_awaited()
            mock_bc.assert_not_awaited()


# ---------------------------------------------------------------------------
# Tier routing — deep tier runs all methods
# ---------------------------------------------------------------------------


class TestDeepTierRouting:
    """Deep tier calls all medium + deep methods."""

    async def test_deep_calls_traffic_and_tech(self):
        analyzer = BusinessAnalyzer()
        lead = _lead()

        with (
            patch.object(analyzer, "_check_website", new_callable=AsyncMock) as mock_web,
            patch.object(analyzer, "_get_rating", new_callable=AsyncMock) as mock_rating,
            patch.object(analyzer, "_run_lighthouse", new_callable=AsyncMock) as mock_lh,
            patch.object(analyzer, "_check_basic_seo", new_callable=AsyncMock) as mock_seo,
            patch.object(analyzer, "_check_social_activity", new_callable=AsyncMock) as mock_social,
            patch.object(analyzer, "_estimate_traffic", new_callable=AsyncMock) as mock_traffic,
            patch.object(analyzer, "_detect_technologies", new_callable=AsyncMock) as mock_tech,
            patch.object(analyzer, "_get_competitors_nearby", new_callable=AsyncMock) as mock_comp,
            patch.object(analyzer, "_generate_battlecard", new_callable=AsyncMock) as mock_bc,
        ):
            mock_web.return_value = WebsiteCheck(exists=True)
            mock_rating.return_value = RatingInfo()
            mock_lh.return_value = {}
            mock_seo.return_value = {}
            mock_social.return_value = {}
            mock_traffic.return_value = {"monthly_visits": None}
            mock_tech.return_value = []
            mock_comp.return_value = []
            mock_bc.return_value = {"feature_matrix": {}}

            result = await analyzer.analyze(lead, tier="deep")

            mock_traffic.assert_awaited_once()
            mock_tech.assert_awaited_once()
            mock_comp.assert_awaited_once()
            mock_bc.assert_awaited_once()
            assert result.tier == "deep"

    async def test_deep_skips_traffic_when_no_website(self):
        analyzer = BusinessAnalyzer()
        lead = _lead(website_url=None)

        with (
            patch.object(analyzer, "_check_website", new_callable=AsyncMock) as mock_web,
            patch.object(analyzer, "_get_rating", new_callable=AsyncMock) as mock_rating,
            patch.object(analyzer, "_check_social_activity", new_callable=AsyncMock) as mock_social,
            patch.object(analyzer, "_estimate_traffic", new_callable=AsyncMock) as mock_traffic,
            patch.object(analyzer, "_detect_technologies", new_callable=AsyncMock) as mock_tech,
            patch.object(analyzer, "_get_competitors_nearby", new_callable=AsyncMock) as mock_comp,
            patch.object(analyzer, "_generate_battlecard", new_callable=AsyncMock) as mock_bc,
        ):
            mock_web.return_value = WebsiteCheck(exists=False)
            mock_rating.return_value = RatingInfo()
            mock_social.return_value = {}
            mock_comp.return_value = []
            mock_bc.return_value = {}

            result = await analyzer.analyze(lead, tier="deep")

            mock_traffic.assert_not_awaited()
            mock_tech.assert_not_awaited()
            # Competitors and battlecard still run (not website-dependent)
            mock_comp.assert_awaited_once()
            mock_bc.assert_awaited_once()
            assert result.traffic is None
            assert result.technologies is None


# ---------------------------------------------------------------------------
# Constructor — llm_client injection
# ---------------------------------------------------------------------------


class TestConstructor:
    """Verify llm_client is properly stored."""

    def test_default_no_llm(self):
        analyzer = BusinessAnalyzer()
        assert analyzer._llm_client is None

    def test_accepts_llm_client(self):
        mock_llm = MagicMock()
        analyzer = BusinessAnalyzer(llm_client=mock_llm)
        assert analyzer._llm_client is mock_llm


# ---------------------------------------------------------------------------
# Static battlecard edge cases
# ---------------------------------------------------------------------------


class TestStaticBattlecardEdgeCases:
    """Edge cases for the static battlecard fallback."""

    async def test_dead_social_adds_weak_point(self):
        analyzer = BusinessAnalyzer()
        result_data = AnalysisResult(
            lead_id="lead-001",
            tier="deep",
            website=WebsiteCheck(exists=True),
            seo={"mobile_friendly": True, "score": 80},
            social_activity={"is_dead": True, "active": False},
        )

        battlecard = await analyzer._generate_battlecard(_lead(), result_data)

        assert any("inactive" in wp.lower() for wp in battlecard["weak_points"])

    async def test_battlecard_has_objection_handlers(self):
        analyzer = BusinessAnalyzer()
        result_data = AnalysisResult(
            lead_id="lead-001",
            tier="deep",
            website=WebsiteCheck(exists=False),
        )

        battlecard = await analyzer._generate_battlecard(_lead(), result_data)

        assert len(battlecard["objection_handlers"]) >= 2
        handler = battlecard["objection_handlers"][0]
        assert "objection" in handler
        assert "response" in handler

    async def test_battlecard_feature_matrix_structure(self):
        analyzer = BusinessAnalyzer()
        result_data = AnalysisResult(
            lead_id="lead-001",
            tier="deep",
            website=WebsiteCheck(exists=True),
            seo={"mobile_friendly": True, "score": 80},
            social_activity={"is_dead": False, "active": True},
        )

        battlecard = await analyzer._generate_battlecard(_lead(), result_data)

        fm = battlecard["feature_matrix"]
        assert "Website" in fm
        assert fm["Website"]["lead"] is True
        assert fm["Website"]["ideal"] is True
