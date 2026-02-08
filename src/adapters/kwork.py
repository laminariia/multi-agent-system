"""Kwork browser-based scraping adapter.

Uses StealthBrowser + Playwright to scrape job listings from Kwork.ru.
Enhanced delays (2-7s instead of 1-5s) due to aggressive anti-bot measures.

Kwork is a Russian-language platform — budgets are in rubles (RUB).
"""
from __future__ import annotations

import re
import time
from typing import Any

import structlog

from src.core.exceptions import (
    CaptchaDetectedError,
    CloudflareBlockError,
    PlatformBannedError,
)

logger = structlog.get_logger(__name__)

# Enhanced delays for Kwork (higher ban risk).
_MIN_DELAY = 2.0
_MAX_DELAY = 7.0

# Default page load timeout (ms).
_PAGE_TIMEOUT_MS = 30_000

# Approximate RUB to USD conversion rate.
_RUB_TO_USD = 0.011

# Budget regex patterns (Russian + numeric).
_BUDGET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(\d[\d\s]*)\s*(руб|₽|р\.|RUB)", re.IGNORECASE),
    re.compile(r"(\d[\d\s]*)\s*(USD|\$|EUR|€)", re.IGNORECASE),
    re.compile(r"от\s*(\d[\d\s]*)\s*до\s*(\d[\d\s]*)\s*(руб|₽|р\.|RUB|\$|USD|EUR|€)", re.IGNORECASE),
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

# Selectors for Kwork pages.
_SELECTORS = {
    "job_card": ".card__content, .wants-card, .kwork-card, article.js-want-wrapper",
    "job_title": ".wants-card__header-title a, .card__title a, .wants-card__title a, h3 a",
    "job_description": ".wants-card__description, .card__description, .breakwords",
    "job_budget": ".wants-card__header-price, .card__price, .wants-card__price, .price",
    "job_link": ".wants-card__header-title a, .card__title a, .wants-card__title a, h3 a",
    "captcha": "#challenge-running, .cf-challenge-running, .captcha-container, .g-recaptcha",
    "cloudflare": "#challenge-stage, .cf-browser-verification",
    "ban_indicator": ".blocked-user, .account-blocked",
}


class KworkClient:
    """Browser-based Kwork.ru job scraper.

    Parameters
    ----------
    browser_pool:
        A :class:`BrowserPool` instance used to acquire stealth pages.
    base_url:
        Kwork wants (job requests) page URL.
    """

    def __init__(
        self,
        browser_pool: Any,
        base_url: str = "https://kwork.ru/projects",
    ) -> None:
        self.browser_pool = browser_pool
        self.base_url = base_url
        self._log = logger.bind(platform="kwork")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fetch_jobs(
        self,
        category: str | None = None,
        max_results: int = 50,
    ) -> list[dict[str, Any]]:
        """Fetch job listings from Kwork projects page.

        Parameters
        ----------
        category:
            Optional category filter appended to URL.
        max_results:
            Maximum number of jobs to return.

        Returns
        -------
        A list of normalised job dicts.
        """
        page = await self.browser_pool.acquire("kwork")
        try:
            url = self._build_search_url(category)
            self._log.info("fetch_jobs_start", url=url, max_results=max_results)

            start = time.monotonic()
            await page.goto(url, wait_until="domcontentloaded", timeout=_PAGE_TIMEOUT_MS)
            # Extra-long initial delay for Kwork.
            await page.wait_random(min_s=_MIN_DELAY, max_s=_MAX_DELAY)

            # Detect blocking conditions.
            await self._detect_blocks(page)

            # Parse job cards from the page.
            jobs = await self._parse_job_cards(page, max_results)

            elapsed_ms = int((time.monotonic() - start) * 1000)
            self._log.info("fetch_jobs_done", count=len(jobs), latency_ms=elapsed_ms)
            return jobs
        finally:
            await self.browser_pool.release("kwork", page)

    async def close(self) -> None:
        """No-op for interface compatibility. Pool manages browser lifecycle."""

    # ------------------------------------------------------------------
    # URL building
    # ------------------------------------------------------------------

    def _build_search_url(self, category: str | None = None) -> str:
        """Build the Kwork projects search URL."""
        url = self.base_url
        if category:
            url = f"{url}?c={category}"
        return url

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
            raise CaptchaDetectedError(platform="kwork", operation="fetch_jobs")

        # Cloudflare challenge.
        cf = await underlying.query_selector(_SELECTORS["cloudflare"])
        if cf:
            self._log.warning("cloudflare_block_detected")
            raise CloudflareBlockError(platform="kwork", operation="fetch_jobs")

        # Account ban.
        ban = await underlying.query_selector(_SELECTORS["ban_indicator"])
        if ban:
            self._log.error("account_banned")
            raise PlatformBannedError(
                platform="kwork",
                operation="fetch_jobs",
                ban_reason="Account blocked on Kwork",
            )

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    async def _parse_job_cards(self, page: Any, max_results: int) -> list[dict[str, Any]]:
        """Extract normalised jobs from the projects page."""
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
        """Parse a single Kwork job card into a normalised dict."""
        # Title.
        title_el = await card.query_selector(_SELECTORS["job_title"])
        title = (await title_el.inner_text()).strip() if title_el else ""
        if not title:
            return None

        # Link / external ID.
        link_el = await card.query_selector(_SELECTORS["job_link"])
        href = await link_el.get_attribute("href") if link_el else ""
        url = href if href and href.startswith("http") else f"https://kwork.ru{href}" if href else ""

        external_id = ""
        id_match = re.search(r"/projects/(\d+)", href or "")
        if id_match:
            external_id = id_match.group(1)
        else:
            external_id = re.sub(r"[^\w]", "", title[:40])

        # Description.
        desc_el = await card.query_selector(_SELECTORS["job_description"])
        description = (await desc_el.inner_text()).strip() if desc_el else ""

        # Budget.
        budget_el = await card.query_selector(_SELECTORS["job_budget"])
        budget_text = (await budget_el.inner_text()).strip() if budget_el else ""
        budget = self._parse_budget_text(budget_text)

        return {
            "external_id": external_id,
            "platform": "kwork",
            "title": title,
            "description": description[:2000],
            "budget_min": budget.get("min"),
            "budget_max": budget.get("max"),
            "currency": budget.get("currency", "RUB"),
            "skills_required": [],
            "url": url,
            "raw_data": {"title": title, "budget_text": budget_text, "href": href},
        }

    @staticmethod
    def _parse_budget_text(text: str) -> dict[str, Any]:
        """Extract budget from Russian text, converting to USD.

        Supports patterns like:
        - "5 000 руб"
        - "от 3000 до 10000 руб"
        - "$500"
        - "5000₽"
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
