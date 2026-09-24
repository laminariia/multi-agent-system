"""YouDo browser-based scraping adapter.

Uses StealthBrowser + Playwright to scrape task listings from YouDo.com.
YouDo is a Russian local services marketplace -- budgets are in rubles (RUB).
Enhanced delays (2-7s) due to anti-bot measures.

No auto-submit -- bid submission is manual only (HITL required).
The _SUBMIT_GUARD flag enforces this constraint.

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
)

logger = structlog.get_logger(__name__)

# Defence-in-depth: if True, no automated bid/response submission is allowed.
# Must be explicitly set to False AND pass HITL review before any submission.
_SUBMIT_GUARD = True

# Enhanced delays for YouDo (anti-bot protection).
_MIN_DELAY = 2.0
_MAX_DELAY = 7.0

# Default page load timeout (ms).
_PAGE_TIMEOUT_MS = 30_000

# Approximate RUB to USD conversion rate.
_RUB_TO_USD = 0.011

# Budget regex patterns (Russian + numeric).
_BUDGET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(\d[\d\s\u00a0]*)\s*(руб|₽|р\.|RUB)", re.IGNORECASE),
    re.compile(r"(\d[\d\s\u00a0]*)\s*(USD|\$|EUR|€)", re.IGNORECASE),
    re.compile(
        r"от\s*(\d[\d\s\u00a0]*)\s*до\s*(\d[\d\s\u00a0]*)\s*(руб|₽|р\.|RUB|\$|USD|EUR|€)",
        re.IGNORECASE,
    ),
    re.compile(
        r"до\s*(\d[\d\s\u00a0]*)\s*(руб|₽|р\.|RUB|\$|USD|EUR|€)",
        re.IGNORECASE,
    ),
]

_CURRENCY_TO_USD: dict[str, float] = {
    "руб": _RUB_TO_USD,
    "₽": _RUB_TO_USD,
    "р.": _RUB_TO_USD,
    "RUB": _RUB_TO_USD,
    "USD": 1.0,
    "$": 1.0,
    "EUR": 1.08,
    "€": 1.08,
}

# CSS selectors for YouDo task pages.
_SELECTORS = {
    "task_card": ".TasksList__task, .task-card, .TaskCard, article.task-item, .listItem",
    "task_title": ".TasksList__task-title a, .task-card__title a, .TaskCard__title a, h3 a, .task-name a",
    "task_description": ".TasksList__task-description, .task-card__description, .TaskCard__description, .task-text",
    "task_budget": ".TasksList__task-price, .task-card__price, .TaskCard__price, .price, .task-budget",
    "task_link": ".TasksList__task-title a, .task-card__title a, .TaskCard__title a, h3 a, .task-name a",
    "task_category": ".TasksList__task-category, .task-card__category, .tag, .category-label",
    "captcha": "#challenge-running, .cf-challenge-running, .captcha-container, .g-recaptcha, .h-captcha",
    "cloudflare": "#challenge-stage, .cf-browser-verification",
    "ban_indicator": ".blocked-user, .account-blocked, .user-banned",
}


class YouDoAdapter:
    """Browser-based YouDo.com task scraper.

    Parameters
    ----------
    browser_pool:
        A :class:`BrowserPool` instance used to acquire stealth pages.
    base_url:
        YouDo tasks listing page URL.
    rate_limiter:
        Optional :class:`AdaptiveRateLimiter` for request throttling.
    circuit_breaker:
        Optional :class:`CircuitBreaker` for resilience against repeated failures.
    """

    _platform = "youdo"

    def __init__(
        self,
        browser_pool: Any,
        base_url: str = "https://youdo.com/tasks-all",
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
        """Fetch task listings from YouDo tasks page.

        Parameters
        ----------
        category:
            Optional category filter appended to URL.
        max_results:
            Maximum number of tasks to return.

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
            await self._rate_limiter.acquire("youdo")

        page = await self.browser_pool.acquire("youdo")
        try:
            url = self._build_search_url(category)
            self._log.info("fetch_jobs_start", url=url, max_results=max_results)

            start = time.monotonic()
            await asyncio.wait_for(
                page.goto(url, wait_until="domcontentloaded", timeout=_PAGE_TIMEOUT_MS),
                timeout=_PAGE_TIMEOUT_MS / 1000 + 5,
            )
            # Extra-long initial delay for YouDo.
            await page.wait_random(min_s=_MIN_DELAY, max_s=_MAX_DELAY)

            # Detect blocking conditions.
            await self._detect_blocks(page)

            # Parse task cards from the page.
            jobs = await self._parse_task_cards(page, max_results)

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
            await self.browser_pool.release("youdo", page)

    async def get_job_details(self, task_url: str) -> dict[str, Any]:
        """Fetch details for a specific YouDo task.

        Parameters
        ----------
        task_url:
            Full URL to the YouDo task page.

        Returns
        -------
        A normalised job dict with extended details.
        """
        if self._rate_limiter:
            await self._rate_limiter.acquire("youdo")

        page = await self.browser_pool.acquire("youdo")
        try:
            self._log.info("get_job_details_start", url=task_url)
            await asyncio.wait_for(
                page.goto(task_url, wait_until="domcontentloaded", timeout=_PAGE_TIMEOUT_MS),
                timeout=_PAGE_TIMEOUT_MS / 1000 + 5,
            )
            await page.wait_random(min_s=_MIN_DELAY, max_s=_MAX_DELAY)

            await self._detect_blocks(page)

            content = await page.content()
            title = await self._extract_text(page, "h1")
            description = await self._extract_text(page, _SELECTORS["task_description"])
            budget_text = await self._extract_text(page, _SELECTORS["task_budget"])
            budget = self._parse_budget_text(budget_text)

            return {
                "title": title,
                "description": description,
                "budget_min": budget.get("min"),
                "budget_max": budget.get("max"),
                "currency": budget.get("currency", "RUB"),
                "url": task_url,
                "platform": "youdo",
                "raw_html_length": len(content),
            }
        finally:
            await self.browser_pool.release("youdo", page)

    async def close(self) -> None:
        """No-op for interface compatibility. Pool manages browser lifecycle."""

    # ------------------------------------------------------------------
    # URL building
    # ------------------------------------------------------------------

    def _build_search_url(self, category: str | None = None) -> str:
        """Build the YouDo tasks search URL."""
        if category:
            return f"{self.base_url}-{category}"
        return self.base_url

    # ------------------------------------------------------------------
    # Block detection
    # ------------------------------------------------------------------

    async def _detect_blocks(self, page: Any) -> None:
        """Check for captcha, Cloudflare, or account ban."""
        underlying = page.page if hasattr(page, "page") else page

        # Captcha detection.
        captcha = await underlying.query_selector(_SELECTORS["captcha"])
        if captcha:
            self._log.warning("captcha_detected")
            raise CaptchaDetectedError(platform="youdo", operation="fetch_jobs")

        # Cloudflare challenge.
        cf = await underlying.query_selector(_SELECTORS["cloudflare"])
        if cf:
            self._log.warning("cloudflare_block_detected")
            raise CloudflareBlockError(platform="youdo", operation="fetch_jobs")

        # Account ban.
        ban = await underlying.query_selector(_SELECTORS["ban_indicator"])
        if ban:
            self._log.error("account_banned")
            raise PlatformBannedError(
                platform="youdo",
                operation="fetch_jobs",
                ban_reason="Account blocked on YouDo",
            )

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    async def _parse_task_cards(self, page: Any, max_results: int) -> list[dict[str, Any]]:
        """Extract normalised tasks from the listing page."""
        underlying = page.page if hasattr(page, "page") else page

        cards = await underlying.query_selector_all(_SELECTORS["task_card"])
        if not cards:
            self._log.warning("no_task_cards_found")
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
        """Parse a single YouDo task card into a normalised dict."""
        # Title.
        title_el = await card.query_selector(_SELECTORS["task_title"])
        title = (await title_el.inner_text()).strip() if title_el else ""
        if not title:
            return None

        # Link / external ID.
        link_el = await card.query_selector(_SELECTORS["task_link"])
        href = await link_el.get_attribute("href") if link_el else ""
        url = href if href and href.startswith("http") else f"https://youdo.com{href}" if href else ""

        external_id = ""
        id_match = re.search(r"/task/(\d+)", href or "")
        if id_match:
            external_id = id_match.group(1)
        else:
            external_id = re.sub(r"[^\w]", "", title[:40])

        # Description.
        desc_el = await card.query_selector(_SELECTORS["task_description"])
        description = (await desc_el.inner_text()).strip() if desc_el else ""

        # Budget.
        budget_el = await card.query_selector(_SELECTORS["task_budget"])
        budget_text = (await budget_el.inner_text()).strip() if budget_el else ""
        budget = self._parse_budget_text(budget_text)

        # Category (used as skills proxy).
        category_el = await card.query_selector(_SELECTORS["task_category"])
        category = (await category_el.inner_text()).strip() if category_el else ""
        skills = [category] if category else []

        return {
            "external_id": external_id,
            "platform": "youdo",
            "title": title,
            "description": description[:2000],
            "budget_min": budget.get("min"),
            "budget_max": budget.get("max"),
            "currency": budget.get("currency", "RUB"),
            "skills_required": skills,
            "url": url,
            "raw_data": {"title": title, "budget_text": budget_text, "href": href, "category": category},
        }

    @staticmethod
    def _parse_budget_text(text: str) -> dict[str, Any]:
        """Extract budget from Russian text, converting to USD.

        Supports patterns like:
        - "5 000 руб"
        - "от 3000 до 10000 руб"
        - "до 15 000 руб"
        - "$500"
        - "10000₽"
        - "200 EUR"
        """
        result: dict[str, Any] = {"min": None, "max": None, "currency": "RUB"}
        if not text:
            return result

        # Range pattern: "от X до Y руб".
        range_match = _BUDGET_PATTERNS[2].search(text)
        if range_match:
            raw_min = range_match.group(1).replace(" ", "").replace("\u00a0", "")
            raw_max = range_match.group(2).replace(" ", "").replace("\u00a0", "")
            currency_label = range_match.group(3)
            rate = _CURRENCY_TO_USD.get(currency_label, _RUB_TO_USD)
            try:
                result["min"] = round(float(raw_min) * rate, 2)
                result["max"] = round(float(raw_max) * rate, 2)
            except ValueError:
                pass
            if currency_label in ("USD", "$"):
                result["currency"] = "USD"
            elif currency_label in ("EUR", "€"):
                result["currency"] = "EUR"
            return result

        # "до X руб" pattern (upper bound only).
        do_match = _BUDGET_PATTERNS[3].search(text)
        if do_match:
            raw_amount = do_match.group(1).replace(" ", "").replace("\u00a0", "")
            currency_label = do_match.group(2)
            rate = _CURRENCY_TO_USD.get(currency_label, _RUB_TO_USD)
            try:
                amount = round(float(raw_amount) * rate, 2)
                result["max"] = amount
            except ValueError:
                pass
            if currency_label in ("USD", "$"):
                result["currency"] = "USD"
            elif currency_label in ("EUR", "€"):
                result["currency"] = "EUR"
            return result

        # Single amount patterns.
        for pattern in _BUDGET_PATTERNS[:2]:
            match = pattern.search(text)
            if match:
                raw_amount = match.group(1).replace(" ", "").replace("\u00a0", "")
                currency_label = match.group(2)
                rate = _CURRENCY_TO_USD.get(currency_label, _RUB_TO_USD)
                try:
                    amount_usd = round(float(raw_amount) * rate, 2)
                    result["min"] = amount_usd
                    result["max"] = amount_usd
                except ValueError:
                    continue
                if currency_label in ("USD", "$"):
                    result["currency"] = "USD"
                elif currency_label in ("EUR", "€"):
                    result["currency"] = "EUR"
                return result

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
