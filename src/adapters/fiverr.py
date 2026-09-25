"""Fiverr browser-based Buyer Requests scraping adapter.

Uses StealthBrowser + Playwright to scrape buyer requests from Fiverr.
Fiverr has aggressive Cloudflare + device fingerprinting anti-bot protection.

CRITICAL: Auto-submit is FORBIDDEN. Bid submission is manual only (HITL required).
The _SUBMIT_GUARD flag enforces this constraint.

Currency is always USD on Fiverr.

Recommended rate limits: 10 req/min for scraping, 5 req/min for detail pages.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import structlog

from src.adapters.circuit_breaker import CircuitBreaker, CircuitBreakerOpen
from src.adapters.rate_limiter import AdaptiveRateLimiter
from src.core.exceptions import (
    CaptchaDetectedError,
    CloudflareBlockError,
    PlatformBannedError,
    SessionExpiredError,
)

logger = structlog.get_logger(__name__)

# Hard-coded guard: this adapter is READ-ONLY -- no auto-submit.
_SUBMIT_GUARD = True

# Enhanced delays for Fiverr (aggressive anti-bot).
_MIN_DELAY = 2.5
_MAX_DELAY = 8.0

# Default page load timeout (ms).
_PAGE_TIMEOUT_MS = 30_000

# CSS selectors for Fiverr Buyer Requests pages.
_SELECTORS = {
    "request_card": (
        ".buyer-request-card, .request-card, .br-card, article.buyer-request, .manage-request-item, .request-list-item"
    ),
    "request_title": (
        ".buyer-request-card__title a, .request-card__title a, .br-card__title a, h3 a, h2 a, .request-title a"
    ),
    "request_description": (
        ".buyer-request-card__description, .request-card__description, "
        ".br-card__description, .request-description, .request-text"
    ),
    "request_budget": (
        ".buyer-request-card__budget, .request-card__budget, .br-card__price, .request-budget, .price, .budget-amount"
    ),
    "request_link": (
        ".buyer-request-card__title a, .request-card__title a, .br-card__title a, h3 a, h2 a, .request-title a"
    ),
    "request_tags": (".buyer-request-card__tag, .request-tag, .tag, .category-tag, .skill-tag"),
    "captcha": ("#challenge-running, .cf-challenge-running, .captcha-container, .g-recaptcha, .h-captcha"),
    "cloudflare": "#challenge-stage, .cf-browser-verification",
    "login_page": ('form[action*="login"], form[action*="signin"], #login_form, .login-container'),
    "ban_indicator": ".account-suspended, .account-disabled, .account-warning",
}


class FiverrAdapter:
    """Browser-based Fiverr Buyer Requests scraper (read-only).

    This adapter scrapes Fiverr's Buyer Requests section for job opportunities.
    It does NOT submit bids -- this is intentional to comply with Fiverr ToS.

    Parameters
    ----------
    browser_pool:
        A :class:`BrowserPool` instance used to acquire stealth pages.
    base_url:
        Fiverr buyer requests page URL.
    rate_limiter:
        Optional :class:`AdaptiveRateLimiter` for request throttling.
    circuit_breaker:
        Optional :class:`CircuitBreaker` for resilience against repeated failures.
    """

    _platform = "fiverr"

    def __init__(
        self,
        browser_pool: Any,
        base_url: str = "https://www.fiverr.com/users/manage_requests",
        rate_limiter: AdaptiveRateLimiter | None = None,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        self.browser_pool = browser_pool
        self.base_url = base_url
        self._log = logger.bind(platform=self._platform)
        self._rate_limiter = rate_limiter
        self._cb = circuit_breaker

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fetch_jobs(
        self,
        category: str | None = None,
        max_results: int = 50,
    ) -> list[dict[str, Any]]:
        """Fetch buyer requests from Fiverr.

        Parameters
        ----------
        category:
            Optional category filter for buyer requests.
        max_results:
            Maximum number of requests to return.

        Returns
        -------
        A list of normalised job dicts.

        Raises
        ------
        CircuitBreakerOpen
            If the circuit breaker is open due to repeated failures.
        """
        if self._cb and not self._cb.can_execute():
            raise CircuitBreakerOpen(self._platform)

        if self._rate_limiter:
            await self._rate_limiter.acquire("fiverr")

        page = await self.browser_pool.acquire("fiverr")
        try:
            url = self._build_search_url(category)
            self._log.info("fetch_jobs_start", url=url, max_results=max_results)

            start = time.monotonic()
            await asyncio.wait_for(
                page.goto(url, wait_until="domcontentloaded", timeout=_PAGE_TIMEOUT_MS),
                timeout=_PAGE_TIMEOUT_MS / 1000 + 5,
            )
            # Extra-long initial delay for Fiverr (aggressive anti-bot).
            await page.wait_random(min_s=_MIN_DELAY, max_s=_MAX_DELAY)

            # Detect blocking conditions.
            await self._detect_blocks(page)

            # Parse buyer request cards from the page.
            jobs = await self._parse_request_cards(page, max_results)

            elapsed_ms = int((time.monotonic() - start) * 1000)
            self._log.info("fetch_jobs_done", count=len(jobs), latency_ms=elapsed_ms)
            if self._cb:
                self._cb.record_success()
            return jobs
        except Exception:
            if self._cb:
                self._cb.record_failure()
            raise
        finally:
            await self.browser_pool.release("fiverr", page)

    async def get_request_details(self, request_url: str) -> dict[str, Any]:
        """Fetch details for a specific Fiverr buyer request.

        Parameters
        ----------
        request_url:
            Full URL to the Fiverr buyer request page.

        Returns
        -------
        A normalised job dict with extended details.
        """
        if self._rate_limiter:
            await self._rate_limiter.acquire("fiverr")

        page = await self.browser_pool.acquire("fiverr")
        try:
            self._log.info("get_request_details_start", url=request_url)
            await asyncio.wait_for(
                page.goto(request_url, wait_until="domcontentloaded", timeout=_PAGE_TIMEOUT_MS),
                timeout=_PAGE_TIMEOUT_MS / 1000 + 5,
            )
            await page.wait_random(min_s=_MIN_DELAY, max_s=_MAX_DELAY)

            await self._detect_blocks(page)

            content = await page.content()
            title = await self._extract_text(page, "h1")
            description = await self._extract_text(page, _SELECTORS["request_description"])
            budget_text = await self._extract_text(page, _SELECTORS["request_budget"])
            budget = self._parse_budget_text(budget_text)

            return {
                "title": title,
                "description": description,
                "budget_min": budget.get("min"),
                "budget_max": budget.get("max"),
                "currency": "USD",
                "url": request_url,
                "platform": "fiverr",
                "raw_html_length": len(content),
            }
        finally:
            await self.browser_pool.release("fiverr", page)

    async def close(self) -> None:
        """No-op for interface compatibility. Pool manages browser lifecycle."""

    # ------------------------------------------------------------------
    # URL building
    # ------------------------------------------------------------------

    def _build_search_url(self, category: str | None = None) -> str:
        """Build the Fiverr buyer requests URL."""
        if category:
            return f"{self.base_url}?category={category}"
        return self.base_url

    # ------------------------------------------------------------------
    # Block detection
    # ------------------------------------------------------------------

    async def _detect_blocks(self, page: Any) -> None:
        """Check for captcha, Cloudflare, login redirect, or account ban."""
        underlying = page.page if hasattr(page, "page") else page

        # Captcha detection (reCAPTCHA, hCaptcha).
        captcha = await underlying.query_selector(_SELECTORS["captcha"])
        if captcha:
            self._log.warning("captcha_detected")
            raise CaptchaDetectedError(platform="fiverr", operation="fetch_jobs")

        # Cloudflare challenge.
        cf = await underlying.query_selector(_SELECTORS["cloudflare"])
        if cf:
            self._log.warning("cloudflare_block_detected")
            raise CloudflareBlockError(platform="fiverr", operation="fetch_jobs")

        # Account suspension/ban.
        ban = await underlying.query_selector(_SELECTORS["ban_indicator"])
        if ban:
            self._log.error("account_banned")
            raise PlatformBannedError(
                platform="fiverr",
                operation="fetch_jobs",
                ban_reason="Account suspended/disabled on Fiverr",
            )

        # Login page redirect (session expired -- retryable after re-auth).
        login = await underlying.query_selector(_SELECTORS["login_page"])
        if login:
            self._log.warning("session_expired_login_redirect")
            raise SessionExpiredError(
                message="Session expired -- redirected to login page, re-authentication required",
                platform="fiverr",
                operation="fetch_jobs",
            )

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    async def _parse_request_cards(self, page: Any, max_results: int) -> list[dict[str, Any]]:
        """Extract normalised buyer requests from the page."""
        underlying = page.page if hasattr(page, "page") else page

        cards = await underlying.query_selector_all(_SELECTORS["request_card"])
        if not cards:
            self._log.warning("no_request_cards_found")
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
        """Parse a single Fiverr buyer request card into a normalised dict."""
        # Title.
        title_el = await card.query_selector(_SELECTORS["request_title"])
        title = (await title_el.inner_text()).strip() if title_el else ""
        if not title:
            return None

        # Link / external ID.
        link_el = await card.query_selector(_SELECTORS["request_link"])
        href = await link_el.get_attribute("href") if link_el else ""
        url = href if href and href.startswith("http") else f"https://www.fiverr.com{href}" if href else ""

        external_id = ""
        id_match = re.search(r"/buyer-request/([A-Za-z0-9_-]+)", href or "")
        if id_match:
            external_id = id_match.group(1)
        else:
            # Fallback: try any alphanumeric ID in the URL.
            fallback_match = re.search(r"/([A-Za-z0-9]{6,})", href or "")
            if fallback_match:
                external_id = fallback_match.group(1)
            else:
                external_id = re.sub(r"[^\w]", "", title[:40])

        # Description.
        desc_el = await card.query_selector(_SELECTORS["request_description"])
        description = (await desc_el.inner_text()).strip() if desc_el else ""

        # Budget.
        budget_el = await card.query_selector(_SELECTORS["request_budget"])
        budget_text = (await budget_el.inner_text()).strip() if budget_el else ""
        budget = self._parse_budget_text(budget_text)

        # Skills/tags.
        skills: list[str] = []
        try:
            tag_elements = await card.query_selector_all(_SELECTORS["request_tags"])
            for tag_el in tag_elements:
                tag_text = (await tag_el.inner_text()).strip()
                if tag_text:
                    skills.append(tag_text)
        except Exception:  # noqa: BLE001
            self._log.debug("parse_tags_failed", exc_info=True)

        return {
            "external_id": external_id,
            "platform": "fiverr",
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
        """Extract min/max budget from budget text.

        Fiverr budgets are always in USD. Supports:
        - "$200"
        - "$100 - $500"
        - "$1,500"
        - "500" (numeric only)
        """
        result: dict[str, float | None] = {"min": None, "max": None}
        if not text:
            return result

        # Range pattern: "$100 - $500" or "$100-$500" or "$250-$750".
        range_match = re.search(r"\$?([\d,]+)\s*[-\u2013\u2014]\s*\$?([\d,]+)", text)
        if range_match:
            result["min"] = float(range_match.group(1).replace(",", ""))
            result["max"] = float(range_match.group(2).replace(",", ""))
            return result

        # Single amount: "$250" or "250 USD" or just "500".
        single_match = re.search(r"\$?([\d,]+)", text)
        if single_match:
            amount = float(single_match.group(1).replace(",", ""))
            result["min"] = amount
            result["max"] = amount

        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _extract_text(self, page: Any, selector: str) -> str:
        """Safely extract inner text from the first matching element."""
        underlying = page.page if hasattr(page, "page") else page
        el = await underlying.query_selector(selector)
        if el:
            return (await el.inner_text()).strip()
        return ""
