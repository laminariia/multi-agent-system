"""Unit tests for Business Analyzer (src/core/business_analyzer.py).

Tests cover: tier routing, quick/medium/deep analysis,
edge cases, and data models.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.core.business_analyzer import BusinessAnalyzer, SocialPresence
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
) -> object:
    """Create a minimal lead-like object."""

    class _FakeLead:
        def __init__(self):
            self.id = "lead-001"
            self.website_url = website_url
            self.google_rating = google_rating
            self.review_count = review_count
            self.category = category
            self.instagram_url = "https://instagram.com/test"
            self.vk_url = None
            self.telegram_username = "@test"

    return _FakeLead()


# ---------------------------------------------------------------------------
# Tier routing
# ---------------------------------------------------------------------------


class TestTierRouting:
    """Verify correct tier execution."""

    async def test_quick_tier_runs_basic_checks(self):
        analyzer = BusinessAnalyzer()
        lead = _lead()

        with patch.object(analyzer, "_check_website", new_callable=AsyncMock) as mock_web:
            mock_web.return_value = WebsiteCheck(exists=True)
            with patch.object(analyzer, "_get_rating", new_callable=AsyncMock) as mock_rating:
                mock_rating.return_value = RatingInfo(google_rating=4.5, review_count=20)

                result = await analyzer.analyze(lead, tier="quick")

                mock_web.assert_awaited_once()
                mock_rating.assert_awaited_once()
                assert isinstance(result, AnalysisResult)

    async def test_quick_tier_skips_lighthouse(self):
        analyzer = BusinessAnalyzer()
        lead = _lead()

        with patch.object(analyzer, "_check_website", new_callable=AsyncMock) as mock_web:
            mock_web.return_value = WebsiteCheck(exists=True)
            with patch.object(analyzer, "_get_rating", new_callable=AsyncMock) as mock_rating:
                mock_rating.return_value = RatingInfo()
                with patch.object(analyzer, "_run_lighthouse", new_callable=AsyncMock) as mock_lh:
                    result = await analyzer.analyze(lead, tier="quick")
                    mock_lh.assert_not_awaited()
                    assert result.lighthouse is None

    async def test_medium_tier_includes_lighthouse(self):
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
            mock_lh.return_value = {"performance": 75}
            mock_seo.return_value = {"mobile_friendly": True}
            mock_social.return_value = {"is_dead": False}

            result = await analyzer.analyze(lead, tier="medium")

            mock_lh.assert_awaited_once()
            mock_seo.assert_awaited_once()
            mock_social.assert_awaited_once()
            assert result.lighthouse is not None

    async def test_medium_skips_lighthouse_when_no_website(self):
        analyzer = BusinessAnalyzer()
        lead = _lead(website_url=None)

        with (
            patch.object(analyzer, "_check_website", new_callable=AsyncMock) as mock_web,
            patch.object(analyzer, "_get_rating", new_callable=AsyncMock) as mock_rating,
            patch.object(analyzer, "_run_lighthouse", new_callable=AsyncMock) as mock_lh,
            patch.object(analyzer, "_check_basic_seo", new_callable=AsyncMock) as mock_seo,
            patch.object(analyzer, "_check_social_activity", new_callable=AsyncMock) as mock_social,
        ):
            mock_web.return_value = WebsiteCheck(exists=False)
            mock_rating.return_value = RatingInfo()
            mock_social.return_value = {"is_dead": False}

            result = await analyzer.analyze(lead, tier="medium")

            mock_lh.assert_not_awaited()
            mock_seo.assert_not_awaited()
            assert result.lighthouse is None

    async def test_deep_tier_includes_competitors(self):
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
            mock_lh.return_value = {"performance": 60}
            mock_seo.return_value = {"mobile_friendly": True}
            mock_social.return_value = {"is_dead": False}
            mock_traffic.return_value = {"monthly_visits": 1000}
            mock_tech.return_value = ["WordPress"]
            mock_comp.return_value = [{"name": "Competitor A"}]
            mock_bc.return_value = {"key_selling_points": ["test"]}

            await analyzer.analyze(lead, tier="deep")

            mock_traffic.assert_awaited_once()
            mock_tech.assert_awaited_once()
            mock_comp.assert_awaited_once()
            mock_bc.assert_awaited_once()

    async def test_unknown_tier_defaults_to_quick(self):
        analyzer = BusinessAnalyzer()
        lead = _lead()

        with (
            patch.object(analyzer, "_check_website", new_callable=AsyncMock) as mock_web,
            patch.object(analyzer, "_get_rating", new_callable=AsyncMock) as mock_rating,
            patch.object(analyzer, "_run_lighthouse", new_callable=AsyncMock) as mock_lh,
        ):
            mock_web.return_value = WebsiteCheck(exists=True)
            mock_rating.return_value = RatingInfo()

            result = await analyzer.analyze(lead, tier="invalid")

            mock_lh.assert_not_awaited()
            assert result.tier == "quick"


# ---------------------------------------------------------------------------
# Quick tier tools
# ---------------------------------------------------------------------------


class TestCheckWebsite:
    """Website existence check."""

    async def test_no_url_returns_not_exists(self):
        analyzer = BusinessAnalyzer()
        result = await analyzer._check_website(None)
        assert result.exists is False

    async def test_empty_url_returns_not_exists(self):
        analyzer = BusinessAnalyzer()
        result = await analyzer._check_website("")
        assert result.exists is False

    async def test_valid_url_returns_exists(self):
        analyzer = BusinessAnalyzer()
        from unittest.mock import MagicMock

        with patch("src.core.business_analyzer.httpx.AsyncClient") as mock_client_cls:
            mock_elapsed = MagicMock()
            mock_elapsed.total_seconds.return_value = 0.5

            mock_resp = AsyncMock()
            mock_resp.status_code = 200
            mock_resp.elapsed = mock_elapsed
            mock_resp.url = "https://example.com"

            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_client

            result = await analyzer._check_website("https://example.com")
            assert result.exists is True
            assert result.ssl is True
            assert result.status_code == 200

    async def test_connection_error_returns_not_exists(self):
        analyzer = BusinessAnalyzer()
        import httpx

        with patch("src.core.business_analyzer.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(side_effect=httpx.ConnectError("fail"))
            mock_client_cls.return_value = mock_client

            result = await analyzer._check_website("https://unreachable.com")
            assert result.exists is False


class TestCheckSocialPresence:
    """Social media presence detection."""

    async def test_detects_instagram(self):
        lead = _lead()
        analyzer = BusinessAnalyzer()
        result = await analyzer._check_social_presence(lead)
        assert isinstance(result, SocialPresence)
        assert result.instagram is True

    async def test_detects_no_vk(self):
        lead = _lead()
        analyzer = BusinessAnalyzer()
        result = await analyzer._check_social_presence(lead)
        assert result.vk is False

    async def test_detects_telegram(self):
        lead = _lead()
        analyzer = BusinessAnalyzer()
        result = await analyzer._check_social_presence(lead)
        assert result.telegram is True


class TestGetRating:
    """Rating extraction."""

    async def test_returns_rating_from_lead(self):
        lead = _lead(google_rating=4.2, review_count=15)
        analyzer = BusinessAnalyzer()
        result = await analyzer._get_rating(lead)
        assert isinstance(result, RatingInfo)
        assert result.google_rating == 4.2
        assert result.review_count == 15

    async def test_returns_none_when_no_rating(self):
        lead = _lead(google_rating=None, review_count=None)
        analyzer = BusinessAnalyzer()
        result = await analyzer._get_rating(lead)
        assert result.google_rating is None


# ---------------------------------------------------------------------------
# Result structure
# ---------------------------------------------------------------------------


class TestAnalysisResult:
    """Verify analysis result structure."""

    async def test_result_has_lead_id(self):
        analyzer = BusinessAnalyzer()
        lead = _lead()

        with (
            patch.object(analyzer, "_check_website", new_callable=AsyncMock) as mock_web,
            patch.object(analyzer, "_get_rating", new_callable=AsyncMock) as mock_rating,
        ):
            mock_web.return_value = WebsiteCheck(exists=True)
            mock_rating.return_value = RatingInfo()

            result = await analyzer.analyze(lead, tier="quick")
            assert result.lead_id == "lead-001"

    async def test_result_has_tier(self):
        analyzer = BusinessAnalyzer()
        lead = _lead()

        with (
            patch.object(analyzer, "_check_website", new_callable=AsyncMock) as mock_web,
            patch.object(analyzer, "_get_rating", new_callable=AsyncMock) as mock_rating,
        ):
            mock_web.return_value = WebsiteCheck(exists=True)
            mock_rating.return_value = RatingInfo()

            result = await analyzer.analyze(lead, tier="quick")
            assert result.tier == "quick"


# ---------------------------------------------------------------------------
# Social presence model
# ---------------------------------------------------------------------------


class TestSocialPresenceModel:
    """Verify SocialPresence dataclass."""

    def test_defaults_to_false(self):
        sp = SocialPresence()
        assert sp.instagram is False
        assert sp.vk is False
        assert sp.telegram is False
        assert sp.facebook is False
