"""Unit tests for src.adapters.kwork.KworkClient.

All Playwright interactions are mocked — no real browser is launched.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.adapters.kwork import KworkClient, _MIN_DELAY, _MAX_DELAY
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
        if "blocked-user" in selector or "account-blocked" in selector:
            return AsyncMock() if has_ban else None
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
        if "title" in selector.lower() or "h3" in selector:
            return _mock_element(data.get("title", "Тестовая работа"), data.get("href", "/projects/12345"))
        if "link" in selector.lower() or "h3" in selector:
            return _mock_element(data.get("title", "Тестовая работа"), data.get("href", "/projects/12345"))
        if "description" in selector.lower() or "breakwords" in selector:
            return _mock_element(data.get("description", "Описание работы"))
        if "price" in selector.lower() or "budget" in selector.lower():
            return _mock_element(data.get("budget", "5 000 руб"))
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

class TestKworkFetchJobs:
    """Test job fetching and parsing."""

    @pytest.mark.asyncio
    async def test_fetch_jobs_returns_normalised_jobs(self) -> None:
        cards = [
            {"title": "Разработка сайта", "href": "/projects/12345", "description": "Нужен сайт", "budget": "10 000 руб"},
            {"title": "Логотип компании", "href": "/projects/67890", "description": "Дизайн", "budget": "3 000 руб"},
        ]
        page = _mock_page(cards=cards)
        pool = _mock_pool(page)
        client = KworkClient(browser_pool=pool)

        jobs = await client.fetch_jobs()

        assert len(jobs) == 2
        assert jobs[0]["platform"] == "kwork"
        assert jobs[0]["title"] == "Разработка сайта"
        assert jobs[0]["external_id"] == "12345"

    @pytest.mark.asyncio
    async def test_fetch_jobs_empty_page(self) -> None:
        page = _mock_page(cards=None)
        pool = _mock_pool(page)
        client = KworkClient(browser_pool=pool)

        jobs = await client.fetch_jobs()
        assert jobs == []

    @pytest.mark.asyncio
    async def test_fetch_jobs_releases_page_always(self) -> None:
        page = _mock_page(cards=[{"title": "Job", "href": "/projects/1"}])
        pool = _mock_pool(page)
        client = KworkClient(browser_pool=pool)

        await client.fetch_jobs()

        pool.acquire.assert_awaited_once_with("kwork")
        pool.release.assert_awaited_once_with("kwork", page)

    @pytest.mark.asyncio
    async def test_fetch_jobs_releases_page_on_error(self) -> None:
        page = _mock_page(has_captcha=True)
        pool = _mock_pool(page)
        client = KworkClient(browser_pool=pool)

        with pytest.raises(CaptchaDetectedError):
            await client.fetch_jobs()

        pool.release.assert_awaited_once_with("kwork", page)


class TestKworkBlockDetection:
    """Test captcha, Cloudflare, and ban detection."""

    @pytest.mark.asyncio
    async def test_captcha_raises(self) -> None:
        page = _mock_page(has_captcha=True)
        pool = _mock_pool(page)
        client = KworkClient(browser_pool=pool)

        with pytest.raises(CaptchaDetectedError):
            await client.fetch_jobs()

    @pytest.mark.asyncio
    async def test_cloudflare_raises(self) -> None:
        page = _mock_page(has_cloudflare=True)
        pool = _mock_pool(page)
        client = KworkClient(browser_pool=pool)

        with pytest.raises(CloudflareBlockError):
            await client.fetch_jobs()

    @pytest.mark.asyncio
    async def test_ban_raises(self) -> None:
        page = _mock_page(has_ban=True)
        pool = _mock_pool(page)
        client = KworkClient(browser_pool=pool)

        with pytest.raises(PlatformBannedError):
            await client.fetch_jobs()


class TestKworkBudgetParsing:
    """Test Russian budget text extraction and RUB→USD conversion."""

    def test_rub_budget_conversion(self) -> None:
        result = KworkClient._parse_budget_text("5 000 руб")
        assert result["min"] is not None
        assert result["min"] == pytest.approx(55.0, rel=0.1)  # 5000 * 0.011
        assert result["currency"] == "RUB"

    def test_ruble_symbol(self) -> None:
        result = KworkClient._parse_budget_text("10000₽")
        assert result["min"] is not None
        assert result["min"] == pytest.approx(110.0, rel=0.1)

    def test_usd_budget(self) -> None:
        result = KworkClient._parse_budget_text("500 USD")
        assert result["min"] == 500.0
        assert result["currency"] == "USD"

    def test_range_budget(self) -> None:
        result = KworkClient._parse_budget_text("от 3000 до 10000 руб")
        assert result["min"] is not None
        assert result["max"] is not None
        assert result["min"] < result["max"]

    def test_empty_budget(self) -> None:
        result = KworkClient._parse_budget_text("")
        assert result["min"] is None
        assert result["max"] is None


class TestKworkExtraDelays:
    """Ensure Kwork has enhanced delays compared to standard 1-5s."""

    def test_min_delay_higher_than_standard(self) -> None:
        assert _MIN_DELAY >= 2.0

    def test_max_delay_higher_than_standard(self) -> None:
        assert _MAX_DELAY >= 5.0


class TestKworkPlatformField:
    """Ensure platform is always 'kwork'."""

    @pytest.mark.asyncio
    async def test_platform_field_in_results(self) -> None:
        cards = [{"title": "Job", "href": "/projects/1", "budget": "1000 руб"}]
        page = _mock_page(cards=cards)
        pool = _mock_pool(page)
        client = KworkClient(browser_pool=pool)

        jobs = await client.fetch_jobs()
        for job in jobs:
            assert job["platform"] == "kwork"
