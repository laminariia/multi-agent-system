"""Integration test: Browser pool → Stealth → Platform adapter → Normalised output.

Verifies the full flow from BrowserPool.acquire() through the adapter's
fetch_jobs() to normalised job dicts — all with mocked Playwright.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.adapters.kwork import KworkClient
from src.adapters.upwork import UpworkClient

# ---------------------------------------------------------------------------
# Helpers — shared mock infrastructure
# ---------------------------------------------------------------------------

def _mock_element(inner_text: str = "", href: str = "") -> AsyncMock:
    el = AsyncMock()
    el.inner_text = AsyncMock(return_value=inner_text)
    el.get_attribute = AsyncMock(return_value=href)
    return el


def _make_mock_pool(platform: str, cards: list[dict[str, Any]]) -> tuple[AsyncMock, AsyncMock]:
    """Build a mock BrowserPool that returns a StealthPage with job cards."""
    underlying = AsyncMock()
    page = AsyncMock()
    page.page = underlying
    page.goto = AsyncMock()
    page.wait_random = AsyncMock()
    page.content = AsyncMock(return_value="<html>mock</html>")

    # No blocking elements.
    underlying.query_selector = AsyncMock(return_value=None)

    card_mocks = []
    for data in cards:
        card = AsyncMock()

        # Bind data via default argument to avoid closure issues.
        def _make_qs(d: dict[str, Any] = data):
            async def _qs(selector: str) -> AsyncMock | None:
                if "title" in selector.lower() or "h2" in selector or "h3" in selector:
                    return _mock_element(d.get("title", ""), d.get("href", ""))
                if "link" in selector.lower() or "UpLink" in selector:
                    return _mock_element(d.get("title", ""), d.get("href", ""))
                if "description" in selector.lower() or "breakwords" in selector or selector == "p":
                    return _mock_element(d.get("description", ""))
                if "budget" in selector.lower() or "price" in selector.lower() or "fixed-price" in selector.lower():
                    return _mock_element(d.get("budget", ""))
                return None
            return _qs

        card.query_selector = AsyncMock(side_effect=_make_qs())
        card.query_selector_all = AsyncMock(return_value=[])
        card_mocks.append(card)

    underlying.query_selector_all = AsyncMock(return_value=card_mocks)

    pool = AsyncMock()
    pool.acquire = AsyncMock(return_value=page)
    pool.release = AsyncMock()

    return pool, page


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBrowserAdapterIntegration:
    """Full flow: pool → stealth page → adapter → normalised output."""

    @pytest.mark.asyncio
    async def test_upwork_full_flow(self) -> None:
        """Upwork: acquire page → fetch → parse → normalise → release."""
        cards = [
            {
                "title": "Full-Stack React Developer",
                "href": "/jobs/~01react123",
                "description": "Build a modern SPA with React and Node.js",
                "budget": "$2,000 - $5,000",
            },
        ]
        pool, page = _make_mock_pool("upwork", cards)
        client = UpworkClient(browser_pool=pool)

        jobs = await client.fetch_jobs(category="react")

        assert len(jobs) == 1
        job = jobs[0]
        assert job["platform"] == "upwork"
        assert job["external_id"] == "01react123"
        assert job["title"] == "Full-Stack React Developer"
        assert job["budget_min"] == 2000.0
        assert job["budget_max"] == 5000.0
        assert job["currency"] == "USD"
        assert "url" in job

        # Pool lifecycle.
        pool.acquire.assert_awaited_once_with("upwork")
        pool.release.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_kwork_full_flow(self) -> None:
        """Kwork: acquire page → fetch → parse → normalise with RUB→USD → release."""
        cards = [
            {
                "title": "Создание лендинга",
                "href": "/projects/98765",
                "description": "Нужен лендинг на WordPress",
                "budget": "15 000 руб",
            },
        ]
        pool, page = _make_mock_pool("kwork", cards)
        client = KworkClient(browser_pool=pool)

        jobs = await client.fetch_jobs()

        assert len(jobs) == 1
        job = jobs[0]
        assert job["platform"] == "kwork"
        assert job["external_id"] == "98765"
        assert job["title"] == "Создание лендинга"
        assert job["budget_min"] is not None
        assert job["budget_min"] == pytest.approx(165.0, rel=0.1)  # 15000 * 0.011
        assert job["currency"] == "RUB"

        pool.acquire.assert_awaited_once_with("kwork")
        pool.release.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_multiple_jobs_normalised(self) -> None:
        """Multiple jobs from a single page are all normalised correctly."""
        cards = [
            {"title": f"Job {i}", "href": f"/jobs/~0{i}abc", "description": f"Desc {i}", "budget": f"${i * 100}"}
            for i in range(1, 6)
        ]
        pool, page = _make_mock_pool("upwork", cards)
        client = UpworkClient(browser_pool=pool)

        jobs = await client.fetch_jobs()

        assert len(jobs) == 5
        for job in jobs:
            assert job["platform"] == "upwork"
            assert "external_id" in job
            assert "title" in job

    @pytest.mark.asyncio
    async def test_pool_release_on_exception(self) -> None:
        """Adapter releases the browser page even when an exception occurs."""
        pool = AsyncMock()
        page = AsyncMock()
        page.page = AsyncMock()
        page.goto = AsyncMock(side_effect=Exception("Network error"))
        page.wait_random = AsyncMock()
        pool.acquire = AsyncMock(return_value=page)
        pool.release = AsyncMock()

        client = UpworkClient(browser_pool=pool)

        with pytest.raises(Exception, match="Network error"):
            await client.fetch_jobs()

        pool.release.assert_awaited_once()
