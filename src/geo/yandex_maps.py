"""Yandex Maps Geocoder and Organizations search client.

Provides geocoding and business discovery via Yandex HTTP API.
Requires YANDEX_MAPS_API_KEY in environment/settings.
Falls back gracefully when API key is not configured.

Usage::

    from src.geo.yandex_maps import YandexMapsClient

    client = YandexMapsClient(api_key="your-key")
    coords = await client.geocode_city("Moscow")
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

YANDEX_GEOCODE_URL = "https://geocode-maps.yandex.ru/1.x/"
YANDEX_SEARCH_URL = "https://search-maps.yandex.ru/v1/"

_USER_AGENT = "MultiAgentService/4.2 (yandex-maps-client)"


@dataclass
class YandexBusiness:
    """A business discovered from Yandex Maps Organizations API."""

    name: str
    category: str
    lat: float
    lon: float
    address: str | None = None
    phone: str | None = None
    url: str | None = None
    hours: str | None = None


class YandexMapsClient:
    """Rate-limited Yandex Maps API client.

    Parameters
    ----------
    api_key:
        Yandex Maps API key.  When ``None``, all methods return empty
        results gracefully (no exceptions).
    rate_limit:
        Minimum seconds between consecutive requests (default ``0.5``).
    timeout:
        HTTP request timeout in seconds (default ``15.0``).
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        rate_limit: float = 0.5,
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

    async def geocode_city(self, city: str) -> tuple[float, float] | None:
        """Geocode a city name to ``(lat, lon)``.

        Returns ``None`` when the API key is missing or the city cannot
        be resolved.

        Parameters
        ----------
        city:
            Human-readable city name (e.g. ``"Moscow"``).
        """
        if not self._api_key:
            logger.debug("yandex_geocode_skipped", reason="no API key")
            return None

        await self._rate_wait()
        client = await self._get_client()

        try:
            resp = await client.get(
                YANDEX_GEOCODE_URL,
                params={
                    "apikey": self._api_key,
                    "geocode": city,
                    "format": "json",
                    "results": 1,
                },
            )
            resp.raise_for_status()
        except httpx.TimeoutException:
            logger.warning("yandex_geocode_timeout", city=city)
            return None
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "yandex_geocode_http_error",
                city=city,
                status=exc.response.status_code,
            )
            raise

        data: dict[str, Any] = resp.json()
        members = data.get("response", {}).get("GeoObjectCollection", {}).get("featureMember", [])
        if not members:
            logger.info("yandex_geocode_no_results", city=city)
            return None

        pos_str: str = members[0].get("GeoObject", {}).get("Point", {}).get("pos", "")
        if not pos_str:
            return None

        # Yandex returns "lon lat" (note: longitude first).
        parts = pos_str.split()
        if len(parts) != 2:
            return None

        lon, lat = float(parts[0]), float(parts[1])
        logger.info("yandex_geocoded", city=city, lat=lat, lon=lon)
        return (lat, lon)

    async def search_businesses(
        self,
        city: str,
        category: str,
        *,
        max_results: int = 50,
    ) -> list[YandexBusiness]:
        """Search for businesses in a city by category.

        Parameters
        ----------
        city:
            City name to search within.
        category:
            Business category or search query (e.g. ``"restaurant"``).
        max_results:
            Maximum number of results to return (capped at 500).

        Returns
        -------
        list[YandexBusiness]:
            Parsed business results.  Empty list when API key is absent.
        """
        if not self._api_key:
            logger.debug("yandex_search_skipped", reason="no API key")
            return []

        max_results = min(max_results, 500)
        await self._rate_wait()
        client = await self._get_client()

        try:
            resp = await client.get(
                YANDEX_SEARCH_URL,
                params={
                    "apikey": self._api_key,
                    "text": f"{category} {city}",
                    "type": "biz",
                    "lang": "ru_RU",
                    "results": min(max_results, 50),
                },
            )
            resp.raise_for_status()
        except httpx.TimeoutException:
            logger.warning("yandex_search_timeout", city=city, category=category)
            return []
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "yandex_search_http_error",
                city=city,
                category=category,
                status=exc.response.status_code,
            )
            raise

        data: dict[str, Any] = resp.json()
        features: list[dict[str, Any]] = data.get("features", [])

        businesses: list[YandexBusiness] = []
        for feature in features[:max_results]:
            biz = self._parse_feature(feature)
            if biz is not None:
                businesses.append(biz)

        logger.info(
            "yandex_search_results",
            city=city,
            category=category,
            raw_features=len(features),
            parsed=len(businesses),
        )
        return businesses

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse_feature(self, feature: dict[str, Any]) -> YandexBusiness | None:
        """Parse a single GeoJSON feature into a ``YandexBusiness``.

        Returns ``None`` if the feature lacks required fields.
        """
        properties: dict[str, Any] = feature.get("properties", {})
        geometry: dict[str, Any] = feature.get("geometry", {})

        name = properties.get("name", "")
        if not name:
            return None

        # Geometry coordinates are [lon, lat].
        coords: list[float] = geometry.get("coordinates", [])
        if len(coords) < 2:
            return None

        lon, lat = coords[0], coords[1]

        # Category from CompanyMetaData.
        meta: dict[str, Any] = properties.get("CompanyMetaData", {})
        category = ""
        categories: list[dict[str, str]] = meta.get("Categories", [])
        if categories:
            category = categories[0].get("name", "")

        # Address.
        address = meta.get("address") or properties.get("description", None)

        # Phone.
        phone: str | None = None
        phones: list[dict[str, str]] = meta.get("Phones", [])
        if phones:
            phone = phones[0].get("formatted")

        # URL.
        url = meta.get("url")

        # Hours.
        hours: str | None = None
        hours_data: dict[str, Any] = meta.get("Hours", {})
        hours_text = hours_data.get("text")
        if hours_text:
            hours = hours_text

        return YandexBusiness(
            name=name,
            category=category,
            lat=lat,
            lon=lon,
            address=address,
            phone=phone,
            url=url,
            hours=hours,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._http and not self._http.is_closed:
            await self._http.aclose()
