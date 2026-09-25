"""Unit tests for src.adapters.upwork.UpworkClient.

All Playwright interactions are mocked — no real browser is launched.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.adapters.upwork import _SUBMIT_GUARD, UpworkClient
from src.core.exceptions import (
    CaptchaDetectedError,
    CloudflareBlockError,
    PlatformBannedError,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_element(inner_text: str = "", href: str = "") -> AsyncMock:
    """Create a mock Playwright element handle."""
    el = AsyncMock()
    el.inner_text = AsyncMock(return_value=inner_text)
    el.get_attribute = AsyncMock(return_value=href)
    return el


def _mock_page(
    cards: list[dict[str, Any]] | None = None,
    has_captcha: bool = False,
    has_cloudflare: bool = False,
    has_ban: bool = False,
) -> AsyncMock:
    """Create a mock StealthPage with configurable DOM responses."""
    underlying = AsyncMock()
    page = AsyncMock()
    page.page = underlying
    page.goto = AsyncMock()
    page.wait_random = AsyncMock()
    page.content = AsyncMock(return_value="<html></html>")

    async def query_selector(selector: str) -> AsyncMock | None:
        if "captcha" in selector.lower() or "challenge-running" in selector:
            return AsyncMock() if has_captcha else None
        if "challenge-stage" in selector or "cf-browser-verification" in selector:
            return AsyncMock() if has_cloudflare else None
        if "account-suspended" in selector or "account-hold" in selector:
            return AsyncMock() if has_ban else None
        if "h1" in selector or "h2" in selector:
            return _mock_element("Test Job Title")
        if "budget" in selector.lower() or "fixed-price" in selector.lower():
            return _mock_element("$500 - $1,000")
        if "description" in selector.lower():
            return _mock_element("Build a landing page")
        return None

    underlying.query_selector = AsyncMock(side_effect=query_selector)

    if cards:
        card_mocks = []
        for card_data in cards:
            card = AsyncMock()
            card.query_selector = AsyncMock(side_effect=_card_query_selector(card_data))
            card.query_selector_all = AsyncMock(return_value=[])
            card_mocks.append(card)
        underlying.query_selector_all = AsyncMock(return_value=card_mocks)
    else:
        underlying.query_selector_all = AsyncMock(return_value=[])

    return page


def _card_query_selector(data: dict[str, Any]):
    """Return a side_effect function for a card's query_selector."""

    async def _qs(selector: str) -> AsyncMock | None:
        if "title" in selector.lower() or "h2" in selector or "h3" in selector:
            return _mock_element(data.get("title", "Test Job"), data.get("href", "/jobs/~01abc"))
        if "link" in selector.lower() or "UpLink" in selector:
            return _mock_element(data.get("title", "Test Job"), data.get("href", "/jobs/~01abc"))
        if "description" in selector.lower() or selector == "p":
            return _mock_element(data.get("description", "A test job"))
        if "budget" in selector.lower() or "fixed-price" in selector.lower():
            return _mock_element(data.get("budget", "$500"))
        if "token" in selector.lower() or "Skill" in selector.lower():
            return None
        return None

    return _qs


def _mock_pool(page: AsyncMock) -> AsyncMock:
    """Create a mock BrowserPool."""
    pool = AsyncMock()
    pool.acquire = AsyncMock(return_value=page)
    pool.release = AsyncMock()
    return pool


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestUpworkClientGuard:
    """Verify that UpworkClient cannot submit bids."""

    def test_submit_guard_is_true(self) -> None:
        assert _SUBMIT_GUARD is True

    def test_no_submit_bid_method(self) -> None:
        pool = AsyncMock()
        client = UpworkClient(browser_pool=pool)
        assert not hasattr(client, "submit_bid")


class TestUpworkFetchJobs:
    """Test job fetching and parsing."""

    @pytest.mark.asyncio
    async def test_fetch_jobs_returns_normalised_jobs(self) -> None:
        cards = [
            {
                "title": "React Developer Needed",
                "href": "/jobs/~01abc123",
                "description": "Build UI",
                "budget": "$500 - $1,000",
            },
            {"title": "Python Backend", "href": "/jobs/~02def456", "description": "API work", "budget": "$2,000"},
        ]
        page = _mock_page(cards=cards)
        pool = _mock_pool(page)
        client = UpworkClient(browser_pool=pool)

        jobs = await client.fetch_jobs(category="react")

        assert len(jobs) == 2
        assert jobs[0]["platform"] == "upwork"
        assert jobs[0]["title"] == "React Developer Needed"
        assert jobs[0]["external_id"] == "01abc123"

    @pytest.mark.asyncio
    async def test_fetch_jobs_empty_page(self) -> None:
        page = _mock_page(cards=None)
        pool = _mock_pool(page)
        client = UpworkClient(browser_pool=pool)

        jobs = await client.fetch_jobs()
        assert jobs == []

    @pytest.mark.asyncio
    async def test_fetch_jobs_releases_page_on_success(self) -> None:
        page = _mock_page(cards=[{"title": "Job", "href": "/jobs/~01x"}])
        pool = _mock_pool(page)
        client = UpworkClient(browser_pool=pool)

        await client.fetch_jobs()

        pool.acquire.assert_awaited_once_with("upwork")
        pool.release.assert_awaited_once_with("upwork", page)

    @pytest.mark.asyncio
    async def test_fetch_jobs_releases_page_on_error(self) -> None:
        page = _mock_page(has_captcha=True)
        pool = _mock_pool(page)
        client = UpworkClient(browser_pool=pool)

        with pytest.raises(CaptchaDetectedError):
            await client.fetch_jobs()

        pool.release.assert_awaited_once_with("upwork", page)


class TestUpworkBlockDetection:
    """Test captcha, Cloudflare, and ban detection."""

    @pytest.mark.asyncio
    async def test_captcha_detected_raises(self) -> None:
        page = _mock_page(has_captcha=True)
        pool = _mock_pool(page)
        client = UpworkClient(browser_pool=pool)

        with pytest.raises(CaptchaDetectedError):
            await client.fetch_jobs()

    @pytest.mark.asyncio
    async def test_cloudflare_block_raises(self) -> None:
        page = _mock_page(has_cloudflare=True)
        pool = _mock_pool(page)
        client = UpworkClient(browser_pool=pool)

        with pytest.raises(CloudflareBlockError):
            await client.fetch_jobs()

    @pytest.mark.asyncio
    async def test_ban_detected_raises(self) -> None:
        page = _mock_page(has_ban=True)
        pool = _mock_pool(page)
        client = UpworkClient(browser_pool=pool)

        with pytest.raises(PlatformBannedError):
            await client.fetch_jobs()


class TestUpworkBudgetParsing:
    """Test budget text extraction."""

    def test_range_budget(self) -> None:
        result = UpworkClient._parse_budget_text("$100 - $500")
        assert result["min"] == 100.0
        assert result["max"] == 500.0

    def test_single_budget(self) -> None:
        result = UpworkClient._parse_budget_text("$2,500")
        assert result["min"] == 2500.0
        assert result["max"] == 2500.0

    def test_empty_budget(self) -> None:
        result = UpworkClient._parse_budget_text("")
        assert result["min"] is None
        assert result["max"] is None

    def test_budget_with_commas(self) -> None:
        result = UpworkClient._parse_budget_text("$1,000 - $5,000")
        assert result["min"] == 1000.0
        assert result["max"] == 5000.0


class TestUpworkPlatformField:
    """Ensure normalised output always has platform='upwork'."""

    @pytest.mark.asyncio
    async def test_platform_field_in_results(self) -> None:
        cards = [{"title": "Job", "href": "/jobs/~01x", "budget": "$100"}]
        page = _mock_page(cards=cards)
        pool = _mock_pool(page)
        client = UpworkClient(browser_pool=pool)

        jobs = await client.fetch_jobs()
        for job in jobs:
            assert job["platform"] == "upwork"
