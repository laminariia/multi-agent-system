"""Upwork browser-based monitoring adapter (read-only).

Uses StealthBrowser + Playwright to scrape job listings from Upwork.
**DOES NOT** submit bids — this is intentional to comply with Upwork ToS.

CRITICAL: Upwork PROHIBITS automated bid submission.  Any attempt to add
a ``submit_bid`` method to this class is a ToS violation that will result
in account suspension.
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any
from urllib.parse import urlencode

import structlog

from src.adapters.rate_limiter import AdaptiveRateLimiter
from src.core.exceptions import (
    CaptchaDetectedError,
    CloudflareBlockError,
    PlatformBannedError,
)

logger = structlog.get_logger(__name__)

# Hard-coded guard: this adapter is READ-ONLY.
_SUBMIT_GUARD = True

# Default page load timeout (ms).
_PAGE_TIMEOUT_MS = 30_000

# Selectors for Upwork job listing pages.
_SELECTORS = {
    "job_card": 'section[data-test="JobTile"], article[data-ev-label="search_results_impression"]',
    "job_title": 'a[data-test="job-tile-title-link"] h2, a[data-test="UpLink"] h2',
    "job_description": '[data-test="JobDescription"], [data-test="job-description-text"]',
    "job_budget": '[data-test="budget"], [data-test="is-fixed-price"]',
    "job_skills": '[data-test="token"], [data-test="Skill"]',
    "job_link": 'a[data-test="job-tile-title-link"], a[data-test="UpLink"]',
    "captcha": "#challenge-running, .cf-challenge-running, #captcha-container",
    "cloudflare": "#challenge-stage, .cf-browser-verification",
    "login_page": 'form[action*="login"], #login_username',
    "ban_indicator": ".account-suspended, .account-hold",
}


class UpworkClient:
    """Browser-based Upwork job monitor (read-only).

    Parameters
    ----------
    browser_pool:
        A :class:`BrowserPool` instance used to acquire stealth pages.
    base_url:
        Upwork search URL root.
    """

    def __init__(
        self,
        browser_pool: Any,
        base_url: str = "https://www.upwork.com/nx/search/jobs",
        rate_limiter: AdaptiveRateLimiter | None = None,
    ) -> None:
        self.browser_pool = browser_pool
        self.base_url = base_url
        self._log = logger.bind(platform="upwork")
        self._rate_limiter = rate_limiter

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fetch_jobs(
        self,
        category: str | None = None,
        max_results: int = 50,
    ) -> list[dict[str, Any]]:
        """Fetch job listings from Upwork search page.

        Parameters
        ----------
        category:
            Optional search query / category filter.
        max_results:
            Maximum number of jobs to return.

        Returns
        -------
        A list of normalised job dicts.
        """
        if self._rate_limiter:
            await self._rate_limiter.acquire("upwork")

        page = await self.browser_pool.acquire("upwork")
        try:
            url = self._build_search_url(category)
            self._log.info("fetch_jobs_start", url=url, max_results=max_results)

            start = time.monotonic()
            await asyncio.wait_for(
                page.goto(url, wait_until="domcontentloaded", timeout=_PAGE_TIMEOUT_MS),
                timeout=_PAGE_TIMEOUT_MS / 1000 + 5,
            )
            await page.wait_random()

            # Detect blocking conditions.
            await self._detect_blocks(page)

            # Parse job cards from the page.
            jobs = await self._parse_job_cards(page, max_results)

            elapsed_ms = int((time.monotonic() - start) * 1000)
            self._log.info("fetch_jobs_done", count=len(jobs), latency_ms=elapsed_ms)
            return jobs
        finally:
            await self.browser_pool.release("upwork", page)

    async def get_job_details(self, job_url: str) -> dict[str, Any]:
        """Fetch details for a specific Upwork job posting.

        Parameters
        ----------
        job_url:
            Full URL to the Upwork job posting.

        Returns
        -------
        A normalised job dict with extended details.
        """
        if self._rate_limiter:
            await self._rate_limiter.acquire("upwork")

        page = await self.browser_pool.acquire("upwork")
        try:
            self._log.info("get_job_details_start", url=job_url)
            await asyncio.wait_for(
                page.goto(job_url, wait_until="domcontentloaded", timeout=_PAGE_TIMEOUT_MS),
                timeout=_PAGE_TIMEOUT_MS / 1000 + 5,
            )
            await page.wait_random()

            await self._detect_blocks(page)

            content = await page.content()
            title = await self._extract_text(page, "h1")
            description = await self._extract_text(page, _SELECTORS["job_description"])
            budget = await self._extract_budget(page)

            return {
                "title": title,
                "description": description,
                "budget_min": budget.get("min"),
                "budget_max": budget.get("max"),
                "currency": "USD",
                "url": job_url,
                "platform": "upwork",
                "raw_html_length": len(content),
            }
        finally:
            await self.browser_pool.release("upwork", page)

    async def close(self) -> None:
        """No-op for interface compatibility. Pool manages browser lifecycle."""

    # ------------------------------------------------------------------
    # URL building
    # ------------------------------------------------------------------

    def _build_search_url(self, category: str | None = None) -> str:
        """Build the Upwork job search URL."""
        params: dict[str, str] = {"sort": "recency", "per_page": "50"}
        if category:
            params["q"] = category
        return f"{self.base_url}?{urlencode(params)}"

    # ------------------------------------------------------------------
    # Block detection
    # ------------------------------------------------------------------

    async def _detect_blocks(self, page: Any) -> None:
        """Check for captcha, Cloudflare, login redirect, or account ban."""
        underlying = page.page if hasattr(page, "page") else page

        # Captcha detection.
        captcha = await underlying.query_selector(_SELECTORS["captcha"])
        if captcha:
            self._log.warning("captcha_detected")
            raise CaptchaDetectedError(platform="upwork", operation="fetch_jobs")

        # Cloudflare challenge.
        cf = await underlying.query_selector(_SELECTORS["cloudflare"])
        if cf:
            self._log.warning("cloudflare_block_detected")
            raise CloudflareBlockError(platform="upwork", operation="fetch_jobs")

        # Login page redirect (session expired).
        login = await underlying.query_selector(_SELECTORS["login_page"])
        if login:
            self._log.warning("session_expired_login_redirect")
            raise CaptchaDetectedError(
                message="Session expired — redirected to login page, HITL required",
                platform="upwork",
                operation="fetch_jobs",
            )

        # Account ban.
        ban = await underlying.query_selector(_SELECTORS["ban_indicator"])
        if ban:
            self._log.error("account_banned")
            raise PlatformBannedError(
                platform="upwork",
                operation="fetch_jobs",
                ban_reason="Account suspended/hold detected on page",
            )

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    async def _parse_job_cards(self, page: Any, max_results: int) -> list[dict[str, Any]]:
        """Extract normalised jobs from the search results page."""
        underlying = page.page if hasattr(page, "page") else page

        cards = await underlying.query_selector_all(_SELECTORS["job_card"])
        if not cards:
            self._log.warning("no_job_cards_found")
            return []

        jobs: list[dict[str, Any]] = []
        for card in cards[:max_results]:
            try:
                job = await self._parse_single_card(card)
                if job:
                    jobs.append(job)
            except Exception as exc:  # noqa: BLE001
                self._log.warning("parse_card_failed", error=str(exc))
                continue

        return jobs

    async def _parse_single_card(self, card: Any) -> dict[str, Any] | None:
        """Parse a single job card element into a normalised dict."""
        # Title.
        title_el = await card.query_selector(
            'a[data-test="job-tile-title-link"] h2, a[data-test="UpLink"] h2, h2'
        )
        title = (await title_el.inner_text()).strip() if title_el else ""
        if not title:
            return None

        # Link / external ID.
        link_el = await card.query_selector(
            'a[data-test="job-tile-title-link"], a[data-test="UpLink"], a'
        )
        href = await link_el.get_attribute("href") if link_el else ""
        url = f"https://www.upwork.com{href}" if href and not href.startswith("http") else (href or "")

        external_id = ""
        id_match = re.search(r"/jobs/~([A-Za-z0-9]+)", href or "")
        if id_match:
            external_id = id_match.group(1)
        else:
            external_id = re.sub(r"[^\w]", "", title[:40])

        # Description (snippet).
        desc_el = await card.query_selector(
            '[data-test="JobDescription"], [data-test="job-description-text"], p'
        )
        description = (await desc_el.inner_text()).strip() if desc_el else ""

        # Budget.
        budget_el = await card.query_selector(
            '[data-test="budget"], [data-test="is-fixed-price"]'
        )
        budget_text = (await budget_el.inner_text()).strip() if budget_el else ""
        budget = self._parse_budget_text(budget_text)

        # Skills.
        skill_elements = await card.query_selector_all(
            '[data-test="token"], [data-test="Skill"]'
        )
        skills: list[str] = []
        for se in skill_elements:
            text = (await se.inner_text()).strip()
            if text:
                skills.append(text)

        return {
            "external_id": external_id,
            "platform": "upwork",
            "title": title,
            "description": description[:2000],
            "budget_min": budget.get("min"),
            "budget_max": budget.get("max"),
            "currency": "USD",
            "skills_required": skills,
            "url": url,
            "raw_data": {"title": title, "budget_text": budget_text, "href": href},
        }

    @staticmethod
    def _parse_budget_text(text: str) -> dict[str, float | None]:
        """Extract min/max budget from budget text like '$100-$500' or '$250'."""
        result: dict[str, float | None] = {"min": None, "max": None}
        if not text:
            return result

        # Range pattern: "$100 - $500" or "$100-$500".
        range_match = re.search(r"\$?([\d,]+)\s*[-–]\s*\$?([\d,]+)", text)
        if range_match:
            result["min"] = float(range_match.group(1).replace(",", ""))
            result["max"] = float(range_match.group(2).replace(",", ""))
            return result

        # Single amount: "$250" or "250 USD".
        single_match = re.search(r"\$?([\d,]+)", text)
        if single_match:
            amount = float(single_match.group(1).replace(",", ""))
            result["min"] = amount
            result["max"] = amount

        return result

    async def _extract_text(self, page: Any, selector: str) -> str:
        """Safely extract inner text from the first matching element."""
        underlying = page.page if hasattr(page, "page") else page
        el = await underlying.query_selector(selector)
        if el:
            return (await el.inner_text()).strip()
        return ""

    async def _extract_budget(self, page: Any) -> dict[str, float | None]:
        """Extract budget from a job detail page."""
        text = await self._extract_text(page, _SELECTORS["job_budget"])
        return self._parse_budget_text(text)
