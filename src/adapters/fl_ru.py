"""FL.ru RSS feed adapter.

Parses the FL.ru public RSS feed to discover new freelance job postings.
Rate limit: maximum 10 requests per hour (tracked internally via a
monotonic-clock sliding window).

FL.ru is a primary platform for the MAS system.
"""
from __future__ import annotations

import asyncio
import re
import time
import xml.etree.ElementTree as ET
from typing import Any

import httpx
import structlog

from src.adapters.rate_limiter import AdaptiveRateLimiter
from src.core.exceptions import (
    PlatformAPIError,
    PlatformRateLimitError,
)

logger = structlog.get_logger(__name__)

# Internal rate-limit: max 10 requests per 3600 seconds.
_MAX_REQUESTS_PER_HOUR = 10
_WINDOW_SECONDS = 3600

# Default timeout for RSS fetches.
_DEFAULT_TIMEOUT = 20.0

# Regex patterns for extracting budget from FL.ru descriptions (Russian text).
_BUDGET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?:Бюджет|бюджет|Budget|budget)[:\s]*(\d[\d\s]*)\s*(руб|р\.|RUB|USD|\$|EUR|€)", re.IGNORECASE),
    re.compile(r"(\d[\d\s]*)\s*(руб|р\.|RUB|USD|\$|EUR|€)", re.IGNORECASE),
]

# Approximate conversion rates to USD (updated periodically).
_CURRENCY_TO_USD: dict[str, float] = {
    "руб": 0.011,
    "р.": 0.011,
    "RUB": 0.011,
    "USD": 1.0,
    "$": 1.0,
    "EUR": 1.08,
    "€": 1.08,
}


class FlRuClient:
    """Async client for FL.ru RSS feed parsing.

    Parameters
    ----------
    base_url:
        URL of the FL.ru RSS feed.
    """

    def __init__(
        self,
        base_url: str = "https://www.fl.ru/rss/all.xml",
        rate_limiter: AdaptiveRateLimiter | None = None,
    ) -> None:
        self.base_url = base_url
        self._http: httpx.AsyncClient | None = None
        self._log = logger.bind(platform="fl_ru")
        self._rate_limiter = rate_limiter
        # Sliding-window rate limiter: list of monotonic timestamps.
        self._request_timestamps: list[float] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _get_http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(_DEFAULT_TIMEOUT),
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"
                    ),
                    "Accept": "application/rss+xml, application/xml, text/xml, */*",
                },
            )
        return self._http

    async def close(self) -> None:
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()
            self._http = None

    # ------------------------------------------------------------------
    # Internal rate limiter
    # ------------------------------------------------------------------

    def _check_rate_limit(self) -> None:
        """Enforce the 10-requests-per-hour internal limit.

        Raises :class:`PlatformRateLimitError` if the window is exhausted.
        """
        now = time.monotonic()
        # Prune timestamps older than the window.
        self._request_timestamps = [
            ts for ts in self._request_timestamps if (now - ts) < _WINDOW_SECONDS
        ]
        if len(self._request_timestamps) >= _MAX_REQUESTS_PER_HOUR:
            oldest = self._request_timestamps[0]
            retry_after = _WINDOW_SECONDS - (now - oldest)
            self._log.warning("internal_rate_limit", retry_after=retry_after)
            raise PlatformRateLimitError(
                message=f"FL.ru internal rate limit: {_MAX_REQUESTS_PER_HOUR} req/hour exceeded",
                platform="fl_ru",
                operation="fetch_rss",
                retry_after_seconds=retry_after,
            )
        self._request_timestamps.append(now)

    # ------------------------------------------------------------------
    # RSS parsing
    # ------------------------------------------------------------------

    @staticmethod
    def parse_rss_entry(entry: ET.Element) -> dict[str, Any]:
        """Parse a single RSS ``<item>`` element into a normalised job dict.

        Parameters
        ----------
        entry:
            An ``xml.etree.ElementTree.Element`` representing ``<item>``.

        Returns
        -------
        dict with keys: ``external_id``, ``platform``, ``title``,
        ``description``, ``budget_min``, ``budget_max``, ``currency``,
        ``url``, ``raw_data``.
        """
        title = (entry.findtext("title") or "").strip()
        description = (entry.findtext("description") or "").strip()
        link = (entry.findtext("link") or "").strip()
        pub_date = (entry.findtext("pubDate") or "").strip()
        guid = (entry.findtext("guid") or link).strip()

        # Derive external_id from the GUID or link.
        external_id = guid
        # FL.ru links typically look like https://www.fl.ru/projects/NNNNNNN/slug/
        id_match = re.search(r"/projects/(\d+)", link)
        if id_match:
            external_id = id_match.group(1)

        # Attempt to extract budget from description text.
        budget_min: float | None = None
        budget_max: float | None = None
        currency = "RUB"

        combined_text = f"{title} {description}"
        for pattern in _BUDGET_PATTERNS:
            match = pattern.search(combined_text)
            if match:
                raw_amount = match.group(1).replace(" ", "").replace("\u00a0", "")
                try:
                    amount = float(raw_amount)
                except ValueError:
                    continue
                curr_label = match.group(2)
                usd_rate = _CURRENCY_TO_USD.get(curr_label, 0.011)
                amount_usd = round(amount * usd_rate, 2)
                budget_min = amount_usd
                budget_max = amount_usd
                if curr_label in ("USD", "$"):
                    currency = "USD"
                elif curr_label in ("EUR", "\u20ac"):
                    currency = "EUR"
                else:
                    currency = "RUB"
                break

        return {
            "external_id": external_id,
            "platform": "flru",
            "title": title,
            "description": description,
            "budget_min": budget_min,
            "budget_max": budget_max,
            "currency": currency,
            "url": link,
            "pub_date": pub_date,
            "raw_data": {
                "guid": guid,
                "title": title,
                "description": description,
                "link": link,
                "pubDate": pub_date,
            },
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fetch_jobs(self, category: str | None = None) -> list[dict[str, Any]]:
        """Fetch and parse jobs from the FL.ru RSS feed.

        Parameters
        ----------
        category:
            Optional category slug to filter by (e.g. ``"web-development"``).
            If provided, it is appended to the feed URL path.  If ``None``,
            the default ``all.xml`` feed is used.

        Returns
        -------
        A list of normalised job dicts suitable for insertion into the
        ``jobs`` table.
        """
        self._check_rate_limit()

        if self._rate_limiter:
            await self._rate_limiter.acquire("fl_ru")

        url = self.base_url
        if category:
            # FL.ru supports category-specific RSS feeds.
            url = self.base_url.replace("all.xml", f"{category}.xml")

        http = await self._get_http()
        self._log.info("fetch_jobs", url=url, category=category)

        start = time.monotonic()
        try:
            response = await asyncio.wait_for(http.get(url), timeout=_DEFAULT_TIMEOUT)
        except TimeoutError as exc:
            raise PlatformAPIError(
                f"FL.ru RSS fetch timed out: {exc}",
                platform="fl_ru",
                operation="fetch_jobs",
            ) from exc
        except httpx.HTTPError as exc:
            raise PlatformAPIError(
                f"FL.ru RSS HTTP error: {exc}",
                platform="fl_ru",
                operation="fetch_jobs",
            ) from exc

        elapsed_ms = int((time.monotonic() - start) * 1000)
        self._log.debug("fetch_jobs_response", status=response.status_code, latency_ms=elapsed_ms)

        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            raise PlatformRateLimitError(
                platform="fl_ru",
                operation="fetch_jobs",
                retry_after_seconds=float(retry_after) if retry_after else None,
            )

        if response.status_code >= 400:
            raise PlatformAPIError(
                f"FL.ru RSS returned HTTP {response.status_code}",
                platform="fl_ru",
                operation="fetch_jobs",
                details={"status_code": response.status_code, "body": response.text[:500]},
            )

        # Parse XML.
        try:
            root = ET.fromstring(response.text)  # noqa: S314 — trusted source
        except ET.ParseError as exc:
            raise PlatformAPIError(
                f"FL.ru RSS XML parse error: {exc}",
                platform="fl_ru",
                operation="fetch_jobs",
            ) from exc

        # RSS 2.0 structure: <rss><channel><item>...</item></channel></rss>
        channel = root.find("channel")
        if channel is None:
            self._log.warning("no_channel_element")
            return []

        items = channel.findall("item")
        jobs: list[dict[str, Any]] = []
        for item in items:
            try:
                job = self.parse_rss_entry(item)
                jobs.append(job)
            except Exception as exc:  # noqa: BLE001
                self._log.warning("parse_entry_failed", error=str(exc))
                continue

        self._log.info("fetch_jobs_done", count=len(jobs), category=category)
        return jobs
