"""Unit tests for 2GIS business directory search client.

Tests cover:
- Geocoding (region resolution) with mocked httpx responses
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

from src.geo.dgis import (
    DGisBusiness,
    DGisClient,
)

# ============================================================================
# Fixtures
# ============================================================================


def _region_response(region_id: str = "32") -> dict:
    """Build a mock 2GIS region search response."""
    return {"result": {"items": [{"id": region_id, "name": "Moscow", "type": "region"}]}}


def _search_response(count: int = 2) -> dict:
    """Build a mock 2GIS catalog search response."""
    items = []
    for i in range(count):
        items.append(
            {
                "id": str(1000 + i),
                "name": f"Business {i + 1}",
                "point": {"lat": 55.75 + i * 0.01, "lon": 37.6 + i * 0.01},
                "address_name": f"Street {i + 1}, bld {i + 10}",
                "rubrics": [{"name": "restaurant"}],
                "contact_groups": [
                    {
                        "contacts": [
                            {"type": "phone", "value": f"+7 495 {i + 1:07d}"},
                            {"type": "website", "value": f"https://biz{i + 1}.ru"},
                        ]
                    }
                ],
                "reviews": {
                    "general_rating": 4.5 - i * 0.5,
                    "general_review_count": 100 + i * 50,
                },
            }
        )
    return {"result": {"items": items}}


# ============================================================================
# Dataclass tests
# ============================================================================


def test_dgis_business_creation():
    """DGisBusiness dataclass stores all fields correctly."""
    biz = DGisBusiness(
        id="12345",
        name="Test Restaurant",
        category="restaurant",
        lat=55.75,
        lon=37.62,
        address="Tverskaya 1",
        phone="+7 495 1234567",
        website="https://test.ru",
        rating=4.5,
        review_count=120,
    )
    assert biz.id == "12345"
    assert biz.name == "Test Restaurant"
    assert biz.category == "restaurant"
    assert biz.lat == 55.75
    assert biz.lon == 37.62
    assert biz.address == "Tverskaya 1"
    assert biz.phone == "+7 495 1234567"
    assert biz.website == "https://test.ru"
    assert biz.rating == 4.5
    assert biz.review_count == 120


def test_dgis_business_optional_fields():
    """DGisBusiness works with only required fields."""
    biz = DGisBusiness(
        id="99",
        name="Minimal",
        category="cafe",
        lat=55.0,
        lon=37.0,
    )
    assert biz.name == "Minimal"
    assert biz.address is None
    assert biz.phone is None
    assert biz.website is None
    assert biz.rating is None
    assert biz.review_count == 0


# ============================================================================
# Geocoding tests
# ============================================================================


@pytest.mark.asyncio
async def test_geocode_city_success():
    """geocode_city returns region ID for valid city."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = _region_response("32")
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        client = DGisClient(api_key="test-key", rate_limit=0.0)
        result = await client.geocode_city("Moscow")
        await client.close()

    assert result == "32"
    mock_get.assert_called_once()


@pytest.mark.asyncio
async def test_geocode_city_no_api_key():
    """geocode_city returns None when API key is not set."""
    client = DGisClient(api_key=None)
    result = await client.geocode_city("Moscow")
    assert result is None


@pytest.mark.asyncio
async def test_geocode_city_empty_api_key():
    """geocode_city returns None when API key is empty string."""
    client = DGisClient(api_key="")
    result = await client.geocode_city("Moscow")
    assert result is None


@pytest.mark.asyncio
async def test_geocode_city_no_results():
    """geocode_city returns None when city not found."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"result": {"items": []}}
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        client = DGisClient(api_key="test-key", rate_limit=0.0)
        result = await client.geocode_city("NonexistentCity")
        await client.close()

    assert result is None


@pytest.mark.asyncio
async def test_geocode_city_timeout():
    """geocode_city returns None on timeout."""
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = httpx.TimeoutException("Connection timed out")
        client = DGisClient(api_key="test-key", rate_limit=0.0)
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
        client = DGisClient(api_key="test-key", rate_limit=0.0)
        with pytest.raises(httpx.HTTPStatusError):
            await client.geocode_city("Moscow")
        await client.close()


# ============================================================================
# Business search tests
# ============================================================================


@pytest.mark.asyncio
async def test_search_businesses_success():
    """search_businesses returns list of DGisBusiness for valid query."""
    # geocode_city mock returns region ID, then search returns items.
    region_resp = MagicMock()
    region_resp.json.return_value = _region_response("32")
    region_resp.raise_for_status = MagicMock()

    search_resp = MagicMock()
    search_resp.json.return_value = _search_response(2)
    search_resp.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = [region_resp, search_resp]
        client = DGisClient(api_key="test-key", rate_limit=0.0)
        results = await client.search_businesses("Moscow", "restaurant")
        await client.close()

    assert len(results) == 2
    assert all(isinstance(b, DGisBusiness) for b in results)
    assert results[0].name == "Business 1"
    assert results[0].id == "1000"
    assert results[0].category == "restaurant"
    assert results[0].phone == "+7 495 0000001"
    assert results[0].website == "https://biz1.ru"
    assert results[0].rating == 4.5
    assert results[0].review_count == 100


@pytest.mark.asyncio
async def test_search_businesses_no_api_key():
    """search_businesses returns empty list when API key is not set."""
    client = DGisClient(api_key=None)
    results = await client.search_businesses("Moscow", "restaurant")
    assert results == []


@pytest.mark.asyncio
async def test_search_businesses_no_region():
    """search_businesses returns empty list when city cannot be resolved."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"result": {"items": []}}
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_resp
        client = DGisClient(api_key="test-key", rate_limit=0.0)
        results = await client.search_businesses("Nowhere", "restaurant")
        await client.close()

    assert results == []


@pytest.mark.asyncio
async def test_search_businesses_timeout_on_search():
    """search_businesses returns empty list when search request times out."""
    region_resp = MagicMock()
    region_resp.json.return_value = _region_response("32")
    region_resp.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = [region_resp, httpx.TimeoutException("Timed out")]
        client = DGisClient(api_key="test-key", rate_limit=0.0)
        results = await client.search_businesses("Moscow", "restaurant")
        await client.close()

    assert results == []


# ============================================================================
# Parsing edge cases
# ============================================================================


@pytest.mark.asyncio
async def test_parse_item_missing_name():
    """Items without a name are skipped."""
    region_resp = MagicMock()
    region_resp.json.return_value = _region_response("32")
    region_resp.raise_for_status = MagicMock()

    search_resp = MagicMock()
    search_resp.json.return_value = {
        "result": {"items": [{"id": "1", "name": "", "point": {"lat": 55.75, "lon": 37.6}}]}
    }
    search_resp.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = [region_resp, search_resp]
        client = DGisClient(api_key="test-key", rate_limit=0.0)
        results = await client.search_businesses("Moscow", "restaurant")
        await client.close()

    assert results == []


@pytest.mark.asyncio
async def test_parse_item_zero_coordinates():
    """Items with (0, 0) coordinates are skipped."""
    region_resp = MagicMock()
    region_resp.json.return_value = _region_response("32")
    region_resp.raise_for_status = MagicMock()

    search_resp = MagicMock()
    search_resp.json.return_value = {
        "result": {"items": [{"id": "1", "name": "Zero Biz", "point": {"lat": 0.0, "lon": 0.0}}]}
    }
    search_resp.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = [region_resp, search_resp]
        client = DGisClient(api_key="test-key", rate_limit=0.0)
        results = await client.search_businesses("Moscow", "restaurant")
        await client.close()

    assert results == []


# ============================================================================
# Rate limiting tests
# ============================================================================


@pytest.mark.asyncio
async def test_rate_wait_enforces_delay():
    """_rate_wait enforces minimum delay between requests."""
    client = DGisClient(api_key="test-key", rate_limit=0.5)

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
    client = DGisClient(api_key="test-key")
    await client._get_client()
    assert client._http is not None
    assert not client._http.is_closed

    await client.close()
    assert client._http.is_closed


@pytest.mark.asyncio
async def test_lazy_client_initialization():
    """Client lazily creates httpx.AsyncClient on first use."""
    client = DGisClient(api_key="test-key")
    assert client._http is None

    http = await client._get_client()
    assert client._http is not None
    assert http is client._http
    await client.close()
