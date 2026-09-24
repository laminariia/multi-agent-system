"""Unit tests for src.adapters.fiverr.FiverrAdapter.

All Playwright interactions are mocked -- no real browser is launched.
Fiverr Buyer Requests scraping -- NO auto-submit (HITL mandatory).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.adapters.circuit_breaker import CircuitBreaker, CircuitBreakerOpen
from src.adapters.fiverr import (
    _SUBMIT_GUARD,
    FiverrAdapter,
)
from src.core.exceptions import (
    CaptchaDetectedError,
    CloudflareBlockError,
    PlatformBannedError,
    SessionExpiredError,
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
    has_login: bool = False,
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
        if "suspended" in selector.lower() or "disabled" in selector.lower():
            return AsyncMock() if has_ban else None
        if "login" in selector.lower() or "signin" in selector.lower():
            return AsyncMock() if has_login else None
        # Detail page selectors.
        if "h1" in selector:
            return _mock_element("Need a Logo Designer")
        if "description" in selector.lower():
            return _mock_element("I need a professional logo")
        if "budget" in selector.lower() or "price" in selector.lower():
            return _mock_element("$150")
        return None

    underlying.query_selector = AsyncMock(side_effect=query_selector)

    if cards:
        card_mocks = []
        for card_data in cards:
            card = AsyncMock()
            card.query_selector = AsyncMock(side_effect=_card_qs(card_data))
            card.query_selector_all = AsyncMock(return_value=[])
            # Add tag/skill elements.
            if "skills" in card_data:
                skill_mocks = [_mock_element(s) for s in card_data["skills"]]
                card.query_selector_all = AsyncMock(return_value=skill_mocks)
            card_mocks.append(card)
        underlying.query_selector_all = AsyncMock(return_value=card_mocks)
    else:
        underlying.query_selector_all = AsyncMock(return_value=[])

    return page


def _card_qs(data: dict[str, Any]):
    """Return a side_effect for card.query_selector."""

    async def _qs(selector: str) -> AsyncMock | None:
        if "title" in selector.lower() or "h3" in selector or "h2" in selector:
            return _mock_element(
                data.get("title", "Logo Design"),
                data.get("href", "/buyer-request/abc123"),
            )
        if "link" in selector.lower() or "h3" in selector:
            return _mock_element(
                data.get("title", "Logo Design"),
                data.get("href", "/buyer-request/abc123"),
            )
        if "description" in selector.lower() or "text" in selector.lower():
            return _mock_element(data.get("description", "I need a logo"))
        if "price" in selector.lower() or "budget" in selector.lower():
            return _mock_element(data.get("budget", "$200"))
        if "tag" in selector.lower() or "category" in selector.lower():
            return _mock_element(data.get("category", "Graphics & Design"))
        return None

    return _qs


def _mock_pool(page: AsyncMock) -> AsyncMock:
    """Create a mock BrowserPool."""
    pool = AsyncMock()
    pool.acquire = AsyncMock(return_value=page)
    pool.release = AsyncMock()
    return pool


# ---------------------------------------------------------------------------
# Tests: Submit Guard
# ---------------------------------------------------------------------------


class TestFiverrSubmitGuard:
    """Verify that FiverrAdapter cannot auto-submit bids."""

    def test_submit_guard_is_true(self) -> None:
        assert _SUBMIT_GUARD is True

    def test_no_submit_bid_method(self) -> None:
        pool = AsyncMock()
        client = FiverrAdapter(browser_pool=pool)
        assert not hasattr(client, "submit_bid")


# ---------------------------------------------------------------------------
# Tests: Fetch Jobs (Buyer Requests)
# ---------------------------------------------------------------------------


class TestFiverrFetchJobs:
    """Test buyer request fetching and parsing."""

    @pytest.mark.asyncio
    async def test_fetch_jobs_returns_normalised_jobs(self) -> None:
        cards = [
            {
                "title": "Need Logo Design",
                "href": "/buyer-request/abc123",
                "description": "Professional logo needed",
                "budget": "$200",
            },
            {
                "title": "Website Redesign",
                "href": "/buyer-request/def456",
                "description": "Modern website",
                "budget": "$500 - $1,000",
            },
        ]
        page = _mock_page(cards=cards)
        pool = _mock_pool(page)
        client = FiverrAdapter(browser_pool=pool)

        jobs = await client.fetch_jobs()

        assert len(jobs) == 2
        assert jobs[0]["platform"] == "fiverr"
        assert jobs[0]["title"] == "Need Logo Design"
        assert jobs[0]["external_id"] == "abc123"

    @pytest.mark.asyncio
    async def test_fetch_jobs_with_category(self) -> None:
        cards = [{"title": "Logo", "href": "/buyer-request/x1", "budget": "$100"}]
        page = _mock_page(cards=cards)
        pool = _mock_pool(page)
        client = FiverrAdapter(browser_pool=pool)

        jobs = await client.fetch_jobs(category="graphics-design")

        assert len(jobs) == 1
        pool.acquire.assert_awaited_once_with("fiverr")

    @pytest.mark.asyncio
    async def test_fetch_jobs_empty_page(self) -> None:
        page = _mock_page(cards=None)
        pool = _mock_pool(page)
        client = FiverrAdapter(browser_pool=pool)

        jobs = await client.fetch_jobs()
        assert jobs == []

    @pytest.mark.asyncio
    async def test_fetch_jobs_max_results_limits(self) -> None:
        cards = [{"title": f"Request {i}", "href": f"/buyer-request/r{i}", "budget": "$50"} for i in range(10)]
        page = _mock_page(cards=cards)
        pool = _mock_pool(page)
        client = FiverrAdapter(browser_pool=pool)

        jobs = await client.fetch_jobs(max_results=5)

        assert len(jobs) == 5

    @pytest.mark.asyncio
    async def test_fetch_jobs_releases_page_always(self) -> None:
        page = _mock_page(cards=[{"title": "Job", "href": "/buyer-request/x"}])
        pool = _mock_pool(page)
        client = FiverrAdapter(browser_pool=pool)

        await client.fetch_jobs()

        pool.acquire.assert_awaited_once_with("fiverr")
        pool.release.assert_awaited_once_with("fiverr", page)

    @pytest.mark.asyncio
    async def test_fetch_jobs_releases_page_on_error(self) -> None:
        page = _mock_page(has_captcha=True)
        pool = _mock_pool(page)
        client = FiverrAdapter(browser_pool=pool)

        with pytest.raises(CaptchaDetectedError):
            await client.fetch_jobs()

        pool.release.assert_awaited_once_with("fiverr", page)

    @pytest.mark.asyncio
    async def test_fetch_jobs_with_rate_limiter(self) -> None:
        page = _mock_page(cards=[{"title": "Job", "href": "/buyer-request/x"}])
        pool = _mock_pool(page)
        rate_limiter = AsyncMock()
        client = FiverrAdapter(browser_pool=pool, rate_limiter=rate_limiter)

        await client.fetch_jobs()

        rate_limiter.acquire.assert_awaited_once_with("fiverr")


# ---------------------------------------------------------------------------
# Tests: Get Request Details
# ---------------------------------------------------------------------------


class TestFiverrGetRequestDetails:
    """Test fetching details for a specific buyer request."""

    @pytest.mark.asyncio
    async def test_get_request_details_returns_details(self) -> None:
        page = _mock_page()
        pool = _mock_pool(page)
        client = FiverrAdapter(browser_pool=pool)

        result = await client.get_request_details("https://www.fiverr.com/buyer-request/abc123")

        assert result["platform"] == "fiverr"
        assert result["url"] == "https://www.fiverr.com/buyer-request/abc123"
        assert "title" in result
        assert result["currency"] == "USD"

    @pytest.mark.asyncio
    async def test_get_request_details_releases_page(self) -> None:
        page = _mock_page()
        pool = _mock_pool(page)
        client = FiverrAdapter(browser_pool=pool)

        await client.get_request_details("https://www.fiverr.com/buyer-request/x")

        pool.release.assert_awaited_once_with("fiverr", page)


# ---------------------------------------------------------------------------
# Tests: Block Detection
# ---------------------------------------------------------------------------


class TestFiverrBlockDetection:
    """Test captcha, Cloudflare, login redirect, and ban detection."""

    @pytest.mark.asyncio
    async def test_captcha_raises(self) -> None:
        page = _mock_page(has_captcha=True)
        pool = _mock_pool(page)
        client = FiverrAdapter(browser_pool=pool)

        with pytest.raises(CaptchaDetectedError):
            await client.fetch_jobs()

    @pytest.mark.asyncio
    async def test_cloudflare_raises(self) -> None:
        page = _mock_page(has_cloudflare=True)
        pool = _mock_pool(page)
        client = FiverrAdapter(browser_pool=pool)

        with pytest.raises(CloudflareBlockError):
            await client.fetch_jobs()

    @pytest.mark.asyncio
    async def test_ban_raises(self) -> None:
        page = _mock_page(has_ban=True)
        pool = _mock_pool(page)
        client = FiverrAdapter(browser_pool=pool)

        with pytest.raises(PlatformBannedError):
            await client.fetch_jobs()

    @pytest.mark.asyncio
    async def test_login_redirect_raises_session_expired(self) -> None:
        """Login redirect now raises SessionExpiredError (retryable), not CaptchaDetectedError."""
        page = _mock_page(has_login=True)
        pool = _mock_pool(page)
        client = FiverrAdapter(browser_pool=pool)

        with pytest.raises(SessionExpiredError):
            await client.fetch_jobs()


# ---------------------------------------------------------------------------
# Tests: Budget Parsing
# ---------------------------------------------------------------------------


class TestFiverrBudgetParsing:
    """Test USD budget text extraction."""

    def test_single_budget(self) -> None:
        result = FiverrAdapter._parse_budget_text("$200")
        assert result["min"] == 200.0
        assert result["max"] == 200.0

    def test_range_budget(self) -> None:
        result = FiverrAdapter._parse_budget_text("$100 - $500")
        assert result["min"] == 100.0
        assert result["max"] == 500.0

    def test_budget_with_commas(self) -> None:
        result = FiverrAdapter._parse_budget_text("$1,500")
        assert result["min"] == 1500.0
        assert result["max"] == 1500.0

    def test_empty_budget(self) -> None:
        result = FiverrAdapter._parse_budget_text("")
        assert result["min"] is None
        assert result["max"] is None

    def test_budget_range_with_en_dash(self) -> None:
        result = FiverrAdapter._parse_budget_text("$250\u2013$750")
        assert result["min"] == 250.0
        assert result["max"] == 750.0

    def test_budget_numeric_only(self) -> None:
        result = FiverrAdapter._parse_budget_text("500")
        assert result["min"] == 500.0


# ---------------------------------------------------------------------------
# Tests: Platform Field
# ---------------------------------------------------------------------------


class TestFiverrPlatformField:
    """Ensure normalised output always has platform='fiverr' and currency='USD'."""

    @pytest.mark.asyncio
    async def test_platform_field_in_results(self) -> None:
        cards = [{"title": "Job", "href": "/buyer-request/x", "budget": "$100"}]
        page = _mock_page(cards=cards)
        pool = _mock_pool(page)
        client = FiverrAdapter(browser_pool=pool)

        jobs = await client.fetch_jobs()
        for job in jobs:
            assert job["platform"] == "fiverr"
            assert job["currency"] == "USD"

    @pytest.mark.asyncio
    async def test_normalised_job_has_required_keys(self) -> None:
        cards = [{"title": "Job", "href": "/buyer-request/x", "budget": "$100"}]
        page = _mock_page(cards=cards)
        pool = _mock_pool(page)
        client = FiverrAdapter(browser_pool=pool)

        jobs = await client.fetch_jobs()
        assert len(jobs) == 1
        required_keys = {
            "external_id",
            "platform",
            "title",
            "description",
            "budget_min",
            "budget_max",
            "currency",
            "skills_required",
            "url",
            "raw_data",
        }
        assert required_keys.issubset(set(jobs[0].keys()))


# ---------------------------------------------------------------------------
# Tests: Close
# ---------------------------------------------------------------------------


class TestFiverrClose:
    """Test close method."""

    @pytest.mark.asyncio
    async def test_close_is_noop(self) -> None:
        pool = AsyncMock()
        client = FiverrAdapter(browser_pool=pool)
        await client.close()  # Should not raise


# ---------------------------------------------------------------------------
# Tests: Circuit Breaker Integration
# ---------------------------------------------------------------------------


class TestFiverrCircuitBreaker:
    """Test circuit breaker integration in FiverrAdapter."""

    @pytest.mark.asyncio
    async def test_fetch_jobs_without_circuit_breaker(self) -> None:
        """Adapter works normally when no circuit breaker is provided."""
        cards = [{"title": "Job", "href": "/buyer-request/x", "budget": "$100"}]
        page = _mock_page(cards=cards)
        pool = _mock_pool(page)
        client = FiverrAdapter(browser_pool=pool)

        jobs = await client.fetch_jobs()
        assert len(jobs) == 1

    @pytest.mark.asyncio
    async def test_circuit_breaker_open_raises(self) -> None:
        """CircuitBreakerOpen is raised when the circuit is open."""
        cb = CircuitBreaker("fiverr", failure_threshold=1)
        cb.record_failure()  # Opens the circuit.

        page = _mock_page(cards=[{"title": "Job", "href": "/buyer-request/x"}])
        pool = _mock_pool(page)
        client = FiverrAdapter(browser_pool=pool, circuit_breaker=cb)

        with pytest.raises(CircuitBreakerOpen):
            await client.fetch_jobs()

        # Page should NOT be acquired when circuit is open.
        pool.acquire.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_circuit_breaker_records_success(self) -> None:
        """Successful fetch records success on the circuit breaker."""
        cb = CircuitBreaker("fiverr", failure_threshold=5)
        cb.record_failure()
        cb.record_failure()

        cards = [{"title": "Job", "href": "/buyer-request/x", "budget": "$100"}]
        page = _mock_page(cards=cards)
        pool = _mock_pool(page)
        client = FiverrAdapter(browser_pool=pool, circuit_breaker=cb)

        await client.fetch_jobs()

        assert cb._failure_count == 0

    @pytest.mark.asyncio
    async def test_circuit_breaker_records_failure(self) -> None:
        """Failed fetch records failure on the circuit breaker."""
        cb = CircuitBreaker("fiverr", failure_threshold=5)

        page = _mock_page(has_captcha=True)
        pool = _mock_pool(page)
        client = FiverrAdapter(browser_pool=pool, circuit_breaker=cb)

        with pytest.raises(CaptchaDetectedError):
            await client.fetch_jobs()

        assert cb._failure_count == 1

    @pytest.mark.asyncio
    async def test_circuit_breaker_opens_after_threshold(self) -> None:
        """Circuit opens after reaching failure threshold."""
        cb = CircuitBreaker("fiverr", failure_threshold=2)

        page = _mock_page(has_captcha=True)
        pool = _mock_pool(page)
        client = FiverrAdapter(browser_pool=pool, circuit_breaker=cb)

        for _ in range(2):
            with pytest.raises(CaptchaDetectedError):
                await client.fetch_jobs()

        # Now the circuit should be open.
        with pytest.raises(CircuitBreakerOpen):
            await client.fetch_jobs()

    @pytest.mark.asyncio
    async def test_constructor_accepts_circuit_breaker_none(self) -> None:
        """Constructor defaults circuit_breaker to None."""
        pool = AsyncMock()
        client = FiverrAdapter(browser_pool=pool)
        assert client._cb is None
