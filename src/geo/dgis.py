"""2GIS API client for Russian business directory search.

Provides business discovery via 2GIS public API.
Requires DGIS_API_KEY in environment/settings.
Falls back gracefully when API key is not configured.

Usage::

    from src.geo.dgis import DGisClient

    client = DGisClient(api_key="your-key")
    businesses = await client.search_businesses("Moscow", "restaurant")
    await client.close()
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

DGIS_API_URL = "https://catalog.api.2gis.com/3.0/items"
DGIS_GEOCODE_URL = "https://catalog.api.2gis.com/3.0/items/geocode"
DGIS_REGION_URL = "https://catalog.api.2gis.com/2.0/region/search"

_USER_AGENT = "MultiAgentService/4.2 (dgis-client)"


@dataclass
class DGisBusiness:
    """A business discovered from 2GIS directory API."""

    id: str
    name: str
    category: str
    lat: float
    lon: float
    address: str | None = None
    phone: str | None = None
    website: str | None = None
    rating: float | None = None
    review_count: int = 0


class DGisClient:
    """Rate-limited 2GIS API client.

    Parameters
    ----------
    api_key:
        2GIS API key.  When ``None``, all methods return empty results
        gracefully (no exceptions).
    rate_limit:
        Minimum seconds between consecutive requests (default ``1.0``).
    timeout:
        HTTP request timeout in seconds (default ``15.0``).
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        rate_limit: float = 1.0,
        timeout: float = 15.0,
    ) -> None:
        self._api_key = api_key
        self._rate_limit = rate_limit
        self._timeout = timeout
        self._last_request: float = 0.0
        self._http: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        """Return (or lazily create) the shared ``httpx.AsyncClient``."""
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                timeout=self._timeout,
                headers={"User-Agent": _USER_AGENT},
            )
        return self._http

    async def _rate_wait(self) -> None:
        """Enforce rate limiting between requests."""
        elapsed = time.monotonic() - self._last_request
        if elapsed < self._rate_limit:
            await asyncio.sleep(self._rate_limit - elapsed)
        self._last_request = time.monotonic()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def geocode_city(self, city: str) -> str | None:
        """Get 2GIS region ID for a city name.

        Returns ``None`` when the API key is missing or the city cannot
        be resolved.

        Parameters
        ----------
        city:
            Human-readable city name (e.g. ``"Moscow"``).
        """
        if not self._api_key:
            logger.debug("dgis_geocode_skipped", reason="no API key")
            return None

        await self._rate_wait()
        client = await self._get_client()

        try:
            resp = await client.get(
                DGIS_REGION_URL,
                params={
                    "key": self._api_key,
                    "q": city,
                },
            )
            resp.raise_for_status()
        except httpx.TimeoutException:
            logger.warning("dgis_geocode_timeout", city=city)
            return None
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "dgis_geocode_http_error",
                city=city,
                status=exc.response.status_code,
            )
            raise

        data: dict[str, Any] = resp.json()
        items: list[dict[str, Any]] = data.get("result", {}).get("items", [])
        if not items:
            logger.info("dgis_geocode_no_results", city=city)
            return None

        region_id = str(items[0].get("id", ""))
        if not region_id:
            return None

        logger.info("dgis_geocoded", city=city, region_id=region_id)
        return region_id

    async def search_businesses(
        self,
        city: str,
        category: str,
        *,
        max_results: int = 50,
    ) -> list[DGisBusiness]:
        """Search for businesses in a city by category.

        First resolves the city to a 2GIS region ID, then queries the
        catalog API for businesses matching the category.

        Parameters
        ----------
        city:
            City name to search within.
        category:
            Business category or search query (e.g. ``"restaurant"``).
        max_results:
            Maximum number of results to return (capped at 250).

        Returns
        -------
        list[DGisBusiness]:
            Parsed business results.  Empty list when API key is absent.
        """
        if not self._api_key:
            logger.debug("dgis_search_skipped", reason="no API key")
            return []

        max_results = min(max_results, 250)

        # Resolve city to region ID first.
        region_id = await self.geocode_city(city)
        if not region_id:
            logger.info("dgis_search_no_region", city=city, category=category)
            return []

        await self._rate_wait()
        client = await self._get_client()

        try:
            resp = await client.get(
                DGIS_API_URL,
                params={
                    "key": self._api_key,
                    "q": category,
                    "region_id": region_id,
                    "page_size": min(max_results, 50),
                    "fields": "items.point,items.adm_div,items.contact_groups,items.reviews",
                },
            )
            resp.raise_for_status()
        except httpx.TimeoutException:
            logger.warning("dgis_search_timeout", city=city, category=category)
            return []
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "dgis_search_http_error",
                city=city,
                category=category,
                status=exc.response.status_code,
            )
            raise

        data: dict[str, Any] = resp.json()
        items: list[dict[str, Any]] = data.get("result", {}).get("items", [])

        businesses: list[DGisBusiness] = []
        for item in items[:max_results]:
            biz = self._parse_item(item)
            if biz is not None:
                businesses.append(biz)

        logger.info(
            "dgis_search_results",
            city=city,
            category=category,
            raw_items=len(items),
            parsed=len(businesses),
        )
        return businesses

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse_item(self, item: dict[str, Any]) -> DGisBusiness | None:
        """Parse a single 2GIS catalog item into a ``DGisBusiness``.

        Returns ``None`` if the item lacks required fields.
        """
        name = item.get("name", "")
        if not name:
            return None

        item_id = str(item.get("id", ""))
        if not item_id:
            return None

        # Coordinates from point object.
        point: dict[str, float] = item.get("point", {})
        lat = point.get("lat", 0.0)
        lon = point.get("lon", 0.0)
        if lat == 0.0 and lon == 0.0:
            return None

        # Category from rubrics.
        category = ""
        rubrics: list[dict[str, Any]] = item.get("rubrics", [])
        if rubrics:
            category = rubrics[0].get("name", "")

        # Address.
        address = item.get("address_name")

        # Phone and website from contact_groups.
        phone: str | None = None
        website: str | None = None
        contact_groups: list[dict[str, Any]] = item.get("contact_groups", [])
        for group in contact_groups:
            contacts: list[dict[str, Any]] = group.get("contacts", [])
            for contact in contacts:
                ctype = contact.get("type", "")
                cvalue = contact.get("value", "")
                if ctype == "phone" and not phone:
                    phone = cvalue
                elif ctype == "website" and not website:
                    website = cvalue

        # Rating and review count from reviews.
        rating: float | None = None
        review_count = 0
        reviews: dict[str, Any] = item.get("reviews", {})
        if reviews:
            rating_val = reviews.get("general_rating")
            if rating_val is not None:
                try:
                    rating = float(rating_val)
                except (ValueError, TypeError):
                    pass
            review_count = int(reviews.get("general_review_count", 0))

        return DGisBusiness(
            id=item_id,
            name=name,
            category=category,
            lat=lat,
            lon=lon,
            address=address,
            phone=phone,
            website=website,
            rating=rating,
            review_count=review_count,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._http and not self._http.is_closed:
            await self._http.aclose()
