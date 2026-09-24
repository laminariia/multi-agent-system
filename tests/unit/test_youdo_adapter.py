"""Unit tests for src.adapters.youdo.YouDoAdapter.

All Playwright interactions are mocked -- no real browser is launched.
YouDo is a Russian local services marketplace (youdo.com).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.adapters.circuit_breaker import CircuitBreaker, CircuitBreakerOpen
from src.adapters.youdo import (
    _MAX_DELAY,
    _MIN_DELAY,
    _RUB_TO_USD,
    _SUBMIT_GUARD,
    YouDoAdapter,
)
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
        if "blocked" in selector.lower() or "banned" in selector.lower():
            return AsyncMock() if has_ban else None
        # Detail page selectors.
        if "h1" in selector:
            return _mock_element("Test Task Title")
        if "description" in selector.lower():
            return _mock_element("Need help moving furniture")
        if "price" in selector.lower() or "budget" in selector.lower():
            return _mock_element("5 000 руб")
        return None

    underlying.query_selector = AsyncMock(side_effect=query_selector)

    if cards:
        card_mocks = []
        for card_data in cards:
            card = AsyncMock()
            card.query_selector = AsyncMock(side_effect=_card_qs(card_data))
            card_mocks.append(card)
        underlying.query_selector_all = AsyncMock(return_value=card_mocks)
    else:
        underlying.query_selector_all = AsyncMock(return_value=[])

    return page


def _card_qs(data: dict[str, Any]):
    """Return a side_effect for card.query_selector."""

    async def _qs(selector: str) -> AsyncMock | None:
        if "title" in selector.lower() or "h3" in selector or "name" in selector.lower():
            return _mock_element(
                data.get("title", "Помощь с переездом"),
                data.get("href", "/task/12345"),
            )
        if "link" in selector.lower() or "h3" in selector:
            return _mock_element(
                data.get("title", "Помощь с переездом"),
                data.get("href", "/task/12345"),
            )
        if "description" in selector.lower() or "text" in selector.lower():
            return _mock_element(data.get("description", "Описание задачи"))
        if "price" in selector.lower() or "budget" in selector.lower():
            return _mock_element(data.get("budget", "5 000 руб"))
        if "category" in selector.lower() or "tag" in selector.lower():
            return _mock_element(data.get("category", "Ремонт"))
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


class TestYouDoSubmitGuard:
    """Verify that YouDoAdapter cannot auto-submit bids."""

    def test_submit_guard_is_true(self) -> None:
        assert _SUBMIT_GUARD is True

    def test_no_submit_bid_method(self) -> None:
        pool = AsyncMock()
        client = YouDoAdapter(browser_pool=pool)
        assert not hasattr(client, "submit_bid")


# ---------------------------------------------------------------------------
# Tests: Fetch Jobs
# ---------------------------------------------------------------------------


class TestYouDoFetchJobs:
    """Test job fetching and parsing."""

    @pytest.mark.asyncio
    async def test_fetch_jobs_returns_normalised_jobs(self) -> None:
        cards = [
            {
                "title": "Помощь с переездом",
                "href": "/task/12345",
                "description": "Нужна помощь с мебелью",
                "budget": "5 000 руб",
            },
            {
                "title": "Уборка квартиры",
                "href": "/task/67890",
                "description": "Генеральная уборка",
                "budget": "3 000 руб",
            },
        ]
        page = _mock_page(cards=cards)
        pool = _mock_pool(page)
        client = YouDoAdapter(browser_pool=pool)

        jobs = await client.fetch_jobs()

        assert len(jobs) == 2
        assert jobs[0]["platform"] == "youdo"
        assert jobs[0]["title"] == "Помощь с переездом"
        assert jobs[0]["external_id"] == "12345"

    @pytest.mark.asyncio
    async def test_fetch_jobs_with_category(self) -> None:
        cards = [{"title": "Ремонт крана", "href": "/task/111", "budget": "2000 руб"}]
        page = _mock_page(cards=cards)
        pool = _mock_pool(page)
        client = YouDoAdapter(browser_pool=pool)

        jobs = await client.fetch_jobs(category="remont")

        assert len(jobs) == 1
        pool.acquire.assert_awaited_once_with("youdo")

    @pytest.mark.asyncio
    async def test_fetch_jobs_empty_page(self) -> None:
        page = _mock_page(cards=None)
        pool = _mock_pool(page)
        client = YouDoAdapter(browser_pool=pool)

        jobs = await client.fetch_jobs()
        assert jobs == []

    @pytest.mark.asyncio
    async def test_fetch_jobs_max_results_limits(self) -> None:
        cards = [{"title": f"Task {i}", "href": f"/task/{i}", "budget": "1000 руб"} for i in range(10)]
        page = _mock_page(cards=cards)
        pool = _mock_pool(page)
        client = YouDoAdapter(browser_pool=pool)

        jobs = await client.fetch_jobs(max_results=3)

        assert len(jobs) == 3

    @pytest.mark.asyncio
    async def test_fetch_jobs_releases_page_always(self) -> None:
        page = _mock_page(cards=[{"title": "Task", "href": "/task/1"}])
        pool = _mock_pool(page)
        client = YouDoAdapter(browser_pool=pool)

        await client.fetch_jobs()

        pool.acquire.assert_awaited_once_with("youdo")
        pool.release.assert_awaited_once_with("youdo", page)

    @pytest.mark.asyncio
    async def test_fetch_jobs_releases_page_on_error(self) -> None:
        page = _mock_page(has_captcha=True)
        pool = _mock_pool(page)
        client = YouDoAdapter(browser_pool=pool)

        with pytest.raises(CaptchaDetectedError):
            await client.fetch_jobs()

        pool.release.assert_awaited_once_with("youdo", page)

    @pytest.mark.asyncio
    async def test_fetch_jobs_with_rate_limiter(self) -> None:
        page = _mock_page(cards=[{"title": "Task", "href": "/task/1"}])
        pool = _mock_pool(page)
        rate_limiter = AsyncMock()
        client = YouDoAdapter(browser_pool=pool, rate_limiter=rate_limiter)

        await client.fetch_jobs()

        rate_limiter.acquire.assert_awaited_once_with("youdo")


# ---------------------------------------------------------------------------
# Tests: Get Job Details
# ---------------------------------------------------------------------------


class TestYouDoGetJobDetails:
    """Test fetching details for a specific task."""

    @pytest.mark.asyncio
    async def test_get_job_details_returns_details(self) -> None:
        page = _mock_page()
        pool = _mock_pool(page)
        client = YouDoAdapter(browser_pool=pool)

        result = await client.get_job_details("https://youdo.com/task/99999")

        assert result["platform"] == "youdo"
        assert result["url"] == "https://youdo.com/task/99999"
        assert "title" in result

    @pytest.mark.asyncio
    async def test_get_job_details_releases_page(self) -> None:
        page = _mock_page()
        pool = _mock_pool(page)
        client = YouDoAdapter(browser_pool=pool)

        await client.get_job_details("https://youdo.com/task/1")

        pool.release.assert_awaited_once_with("youdo", page)


# ---------------------------------------------------------------------------
# Tests: Block Detection
# ---------------------------------------------------------------------------


class TestYouDoBlockDetection:
    """Test captcha, Cloudflare, and ban detection."""

    @pytest.mark.asyncio
    async def test_captcha_raises(self) -> None:
        page = _mock_page(has_captcha=True)
        pool = _mock_pool(page)
        client = YouDoAdapter(browser_pool=pool)

        with pytest.raises(CaptchaDetectedError):
            await client.fetch_jobs()

    @pytest.mark.asyncio
    async def test_cloudflare_raises(self) -> None:
        page = _mock_page(has_cloudflare=True)
        pool = _mock_pool(page)
        client = YouDoAdapter(browser_pool=pool)

        with pytest.raises(CloudflareBlockError):
            await client.fetch_jobs()

    @pytest.mark.asyncio
    async def test_ban_raises(self) -> None:
        page = _mock_page(has_ban=True)
        pool = _mock_pool(page)
        client = YouDoAdapter(browser_pool=pool)

        with pytest.raises(PlatformBannedError):
            await client.fetch_jobs()


# ---------------------------------------------------------------------------
# Tests: Budget Parsing
# ---------------------------------------------------------------------------


class TestYouDoBudgetParsing:
    """Test Russian budget text extraction and RUB->USD conversion."""

    def test_rub_budget_conversion(self) -> None:
        result = YouDoAdapter._parse_budget_text("5 000 руб")
        assert result["min"] is not None
        assert result["min"] == pytest.approx(5000 * _RUB_TO_USD, rel=0.1)
        assert result["currency"] == "RUB"

    def test_ruble_symbol(self) -> None:
        result = YouDoAdapter._parse_budget_text("10000\u20bd")
        assert result["min"] is not None
        assert result["min"] == pytest.approx(10000 * _RUB_TO_USD, rel=0.1)

    def test_usd_budget(self) -> None:
        result = YouDoAdapter._parse_budget_text("500 USD")
        assert result["min"] == 500.0
        assert result["currency"] == "USD"

    def test_range_budget(self) -> None:
        result = YouDoAdapter._parse_budget_text("от 3000 до 10000 руб")
        assert result["min"] is not None
        assert result["max"] is not None
        assert result["min"] < result["max"]

    def test_empty_budget(self) -> None:
        result = YouDoAdapter._parse_budget_text("")
        assert result["min"] is None
        assert result["max"] is None

    def test_budget_do_prefix(self) -> None:
        result = YouDoAdapter._parse_budget_text("до 15 000 руб")
        assert result["max"] is not None
        assert result["max"] == pytest.approx(15000 * _RUB_TO_USD, rel=0.1)

    def test_euro_budget(self) -> None:
        result = YouDoAdapter._parse_budget_text("200 EUR")
        assert result["min"] == pytest.approx(200 * 1.08, rel=0.01)
        assert result["currency"] == "EUR"


# ---------------------------------------------------------------------------
# Tests: URL Building
# ---------------------------------------------------------------------------


class TestYouDoURLBuilding:
    """Test search URL construction."""

    def test_default_url(self) -> None:
        pool = AsyncMock()
        client = YouDoAdapter(browser_pool=pool)
        url = client._build_search_url(None)
        assert "youdo.com" in url

    def test_url_with_category(self) -> None:
        pool = AsyncMock()
        client = YouDoAdapter(browser_pool=pool)
        url = client._build_search_url("remont")
        assert "remont" in url


# ---------------------------------------------------------------------------
# Tests: Delays
# ---------------------------------------------------------------------------


class TestYouDoDelays:
    """Ensure YouDo has enhanced delays for anti-bot protection."""

    def test_min_delay_reasonable(self) -> None:
        assert _MIN_DELAY >= 2.0

    def test_max_delay_reasonable(self) -> None:
        assert _MAX_DELAY >= 5.0


# ---------------------------------------------------------------------------
# Tests: Platform Field
# ---------------------------------------------------------------------------


class TestYouDoPlatformField:
    """Ensure platform is always 'youdo'."""

    @pytest.mark.asyncio
    async def test_platform_field_in_results(self) -> None:
        cards = [{"title": "Task", "href": "/task/1", "budget": "1000 руб"}]
        page = _mock_page(cards=cards)
        pool = _mock_pool(page)
        client = YouDoAdapter(browser_pool=pool)

        jobs = await client.fetch_jobs()
        for job in jobs:
            assert job["platform"] == "youdo"

    @pytest.mark.asyncio
    async def test_normalised_job_has_required_keys(self) -> None:
        cards = [{"title": "Task", "href": "/task/1", "budget": "2000 руб"}]
        page = _mock_page(cards=cards)
        pool = _mock_pool(page)
        client = YouDoAdapter(browser_pool=pool)

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


class TestYouDoClose:
    """Test close method."""

    @pytest.mark.asyncio
    async def test_close_is_noop(self) -> None:
        pool = AsyncMock()
        client = YouDoAdapter(browser_pool=pool)
        await client.close()  # Should not raise


# ---------------------------------------------------------------------------
# Tests: Circuit Breaker Integration
# ---------------------------------------------------------------------------


class TestYouDoCircuitBreaker:
    """Test circuit breaker integration in YouDoAdapter."""

    @pytest.mark.asyncio
    async def test_fetch_jobs_without_circuit_breaker(self) -> None:
        """Adapter works normally when no circuit breaker is provided."""
        cards = [{"title": "Task", "href": "/task/1", "budget": "1000 руб"}]
        page = _mock_page(cards=cards)
        pool = _mock_pool(page)
        client = YouDoAdapter(browser_pool=pool)

        jobs = await client.fetch_jobs()
        assert len(jobs) == 1

    @pytest.mark.asyncio
    async def test_circuit_breaker_open_raises(self) -> None:
        """CircuitBreakerOpen is raised when the circuit is open."""
        cb = CircuitBreaker("youdo", failure_threshold=1)
        cb.record_failure()  # Opens the circuit.

        page = _mock_page(cards=[{"title": "Task", "href": "/task/1"}])
        pool = _mock_pool(page)
        client = YouDoAdapter(browser_pool=pool, circuit_breaker=cb)

        with pytest.raises(CircuitBreakerOpen):
            await client.fetch_jobs()

        # Page should NOT be acquired when circuit is open.
        pool.acquire.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_circuit_breaker_records_success(self) -> None:
        """Successful fetch records success on the circuit breaker."""
        cb = CircuitBreaker("youdo", failure_threshold=5)
        # Pre-load some failures to verify success resets them.
        cb.record_failure()
        cb.record_failure()

        cards = [{"title": "Task", "href": "/task/1", "budget": "1000 руб"}]
        page = _mock_page(cards=cards)
        pool = _mock_pool(page)
        client = YouDoAdapter(browser_pool=pool, circuit_breaker=cb)

        await client.fetch_jobs()

        assert cb._failure_count == 0

    @pytest.mark.asyncio
    async def test_circuit_breaker_records_failure(self) -> None:
        """Failed fetch records failure on the circuit breaker."""
        cb = CircuitBreaker("youdo", failure_threshold=5)

        page = _mock_page(has_captcha=True)
        pool = _mock_pool(page)
        client = YouDoAdapter(browser_pool=pool, circuit_breaker=cb)

        with pytest.raises(CaptchaDetectedError):
            await client.fetch_jobs()

        assert cb._failure_count == 1

    @pytest.mark.asyncio
    async def test_circuit_breaker_opens_after_threshold(self) -> None:
        """Circuit opens after reaching failure threshold."""
        cb = CircuitBreaker("youdo", failure_threshold=2)

        page = _mock_page(has_captcha=True)
        pool = _mock_pool(page)
        client = YouDoAdapter(browser_pool=pool, circuit_breaker=cb)

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
        client = YouDoAdapter(browser_pool=pool)
        assert client._cb is None
