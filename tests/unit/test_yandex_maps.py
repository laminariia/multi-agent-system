"""Unit tests for Yandex Maps geocoding and business search client.

Tests cover:
- Geocoding with mocked httpx responses
- Business search with mocked responses
- Graceful degradation when no API key
- Rate limiting (interval enforcement)
- Error handling (timeout, HTTP errors)
- Dataclass field mapping and parsing edge cases
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.geo.yandex_maps import (
    YandexBusiness,
    YandexMapsClient,
)

# ============================================================================
# Fixtures
# ============================================================================


def _geocode_response(lat: float = 55.7558, lon: float = 37.6173) -> dict:
    """Build a mock Yandex geocode API response."""
    return {
        "response": {
            "GeoObjectCollection": {
                "featureMember": [
                    {
                        "GeoObject": {
                            "Point": {"pos": f"{lon} {lat}"},
                            "name": "Moscow",
                        }
                    }
                ]
            }
        }
    }


def _search_response(count: int = 2) -> dict:
    """Build a mock Yandex Organizations search API response."""
    features = []
    for i in range(count):
        features.append(
            {
                "geometry": {"coordinates": [37.6 + i * 0.01, 55.75 + i * 0.01]},
                "properties": {
                    "name": f"Business {i + 1}",
                    "description": f"Address {i + 1}",
                    "CompanyMetaData": {
                        "Categories": [{"name": "restaurant"}],
                        "address": f"Street {i + 1}, {i + 10}",
                        "Phones": [{"formatted": f"+7 495 {i + 1:07d}"}],
                        "url": f"https://business{i + 1}.ru",
                        "Hours": {"text": "Mon-Fri 09:00-18:00"},
                    },
                },
            }
        )
    return {"features": features}


# ============================================================================
# Dataclass tests
# ============================================================================


def test_yandex_business_creation():
    """YandexBusiness dataclass stores all fields correctly."""
    biz = YandexBusiness(
        name="Test Restaurant",
        category="restaurant",
        lat=55.75,
        lon=37.62,
        address="Tverskaya 1",
        phone="+7 495 1234567",
        url="https://test.ru",
        hours="Mon-Fri 09:00-18:00",
    )
    assert biz.name == "Test Restaurant"
    assert biz.category == "restaurant"
    assert biz.lat == 55.75
    assert biz.lon == 37.62
    assert biz.address == "Tverskaya 1"
    assert biz.phone == "+7 495 1234567"
    assert biz.url == "https://test.ru"
    assert biz.hours == "Mon-Fri 09:00-18:00"


def test_yandex_business_optional_fields():
    """YandexBusiness works with only required fields."""
    biz = YandexBusiness(
        name="Minimal",
        category="cafe",
        lat=55.0,
        lon=37.0,
    )
    assert biz.name == "Minimal"
    assert biz.address is None
    assert biz.phone is None
    assert biz.url is None
    assert biz.hours is None


# ============================================================================
# Geocoding tests
# ============================================================================


@pytest.mark.asyncio
async def test_geocode_city_success():
    """geocode_city returns (lat, lon) for valid city."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = _geocode_response(55.7558, 37.6173)
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        client = YandexMapsClient(api_key="test-key", rate_limit=0.0)
        result = await client.geocode_city("Moscow")
        await client.close()

    assert result is not None
    assert result == (55.7558, 37.6173)
    mock_get.assert_called_once()


@pytest.mark.asyncio
async def test_geocode_city_no_api_key():
    """geocode_city returns None when API key is not set."""
    client = YandexMapsClient(api_key=None)
    result = await client.geocode_city("Moscow")
    assert result is None


@pytest.mark.asyncio
async def test_geocode_city_empty_api_key():
    """geocode_city returns None when API key is empty string."""
    client = YandexMapsClient(api_key="")
    result = await client.geocode_city("Moscow")
    assert result is None


@pytest.mark.asyncio
async def test_geocode_city_no_results():
    """geocode_city returns None when city not found."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"response": {"GeoObjectCollection": {"featureMember": []}}}
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        client = YandexMapsClient(api_key="test-key", rate_limit=0.0)
        result = await client.geocode_city("NonexistentCity")
        await client.close()

    assert result is None


@pytest.mark.asyncio
async def test_geocode_city_timeout():
    """geocode_city returns None on timeout."""
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = httpx.TimeoutException("Connection timed out")
        client = YandexMapsClient(api_key="test-key", rate_limit=0.0)
        result = await client.geocode_city("Moscow")
        await client.close()

    assert result is None


@pytest.mark.asyncio
async def test_geocode_city_http_error():
    """geocode_city raises on HTTP status errors."""
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 403

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = httpx.HTTPStatusError("Forbidden", request=MagicMock(), response=mock_resp)
        client = YandexMapsClient(api_key="test-key", rate_limit=0.0)
        with pytest.raises(httpx.HTTPStatusError):
            await client.geocode_city("Moscow")
        await client.close()


# ============================================================================
# Business search tests
# ============================================================================


@pytest.mark.asyncio
async def test_search_businesses_success():
    """search_businesses returns list of YandexBusiness for valid query."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = _search_response(2)
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        client = YandexMapsClient(api_key="test-key", rate_limit=0.0)
        results = await client.search_businesses("Moscow", "restaurant")
        await client.close()

    assert len(results) == 2
    assert all(isinstance(b, YandexBusiness) for b in results)
    assert results[0].name == "Business 1"
    assert results[0].category == "restaurant"
    assert results[0].phone == "+7 495 0000001"
    assert results[0].url == "https://business1.ru"
    assert results[0].hours == "Mon-Fri 09:00-18:00"
    assert results[0].address == "Street 1, 10"


@pytest.mark.asyncio
async def test_search_businesses_no_api_key():
    """search_businesses returns empty list when API key is not set."""
    client = YandexMapsClient(api_key=None)
    results = await client.search_businesses("Moscow", "restaurant")
    assert results == []


@pytest.mark.asyncio
async def test_search_businesses_timeout():
    """search_businesses returns empty list on timeout."""
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = httpx.TimeoutException("Timed out")
        client = YandexMapsClient(api_key="test-key", rate_limit=0.0)
        results = await client.search_businesses("Moscow", "restaurant")
        await client.close()

    assert results == []


@pytest.mark.asyncio
async def test_search_businesses_http_error():
    """search_businesses raises on HTTP status errors."""
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 500

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = httpx.HTTPStatusError("Server Error", request=MagicMock(), response=mock_resp)
        client = YandexMapsClient(api_key="test-key", rate_limit=0.0)
        with pytest.raises(httpx.HTTPStatusError):
            await client.search_businesses("Moscow", "restaurant")
        await client.close()


# ============================================================================
# Parsing edge cases
# ============================================================================


@pytest.mark.asyncio
async def test_parse_feature_missing_name():
    """Features without a name are skipped."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "features": [
            {
                "geometry": {"coordinates": [37.6, 55.75]},
                "properties": {"name": "", "CompanyMetaData": {}},
            }
        ]
    }
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        client = YandexMapsClient(api_key="test-key", rate_limit=0.0)
        results = await client.search_businesses("Moscow", "restaurant")
        await client.close()

    assert results == []


@pytest.mark.asyncio
async def test_parse_feature_missing_coordinates():
    """Features without coordinates are skipped."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "features": [
            {
                "geometry": {"coordinates": []},
                "properties": {"name": "No Coords Biz", "CompanyMetaData": {}},
            }
        ]
    }
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        client = YandexMapsClient(api_key="test-key", rate_limit=0.0)
        results = await client.search_businesses("Moscow", "restaurant")
        await client.close()

    assert results == []


# ============================================================================
# Rate limiting tests
# ============================================================================


@pytest.mark.asyncio
async def test_rate_wait_enforces_delay():
    """_rate_wait enforces minimum delay between requests."""
    client = YandexMapsClient(api_key="test-key", rate_limit=0.5)

    # First call should be nearly instant.
    start = time.monotonic()
    await client._rate_wait()
    first_duration = time.monotonic() - start
    assert first_duration < 0.1

    # Second call immediately after should wait ~500ms.
    start = time.monotonic()
    await client._rate_wait()
    second_duration = time.monotonic() - start
    assert 0.4 < second_duration < 0.7


# ============================================================================
# Lifecycle tests
# ============================================================================


@pytest.mark.asyncio
async def test_close_cleans_up_client():
    """close() closes the httpx client."""
    client = YandexMapsClient(api_key="test-key")
    await client._get_client()
    assert client._http is not None
    assert not client._http.is_closed

    await client.close()
    assert client._http.is_closed


@pytest.mark.asyncio
async def test_lazy_client_initialization():
    """Client lazily creates httpx.AsyncClient on first use."""
    client = YandexMapsClient(api_key="test-key")
    assert client._http is None

    http = await client._get_client()
    assert client._http is not None
    assert http is client._http
    await client.close()
