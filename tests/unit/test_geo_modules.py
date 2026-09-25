"""Unit tests for geo scanning modules (Pipeline B).

Tests cover:
- h3_scanner.py: BoundingBox, geocode_city, generate_hexagons, hex_to_bbox
- overpass.py: OverpassClient query building, parsing, rate limiting
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import h3
import httpx
import pytest

from src.geo.h3_scanner import BoundingBox, generate_hexagons, geocode_city, hex_to_bbox
from src.geo.overpass import BUSINESS_AMENITIES, BUSINESS_SHOPS, GeoLead, OverpassClient

# ============================================================================
# h3_scanner tests
# ============================================================================


def test_bounding_box_creation():
    """BoundingBox dataclass stores coordinates correctly."""
    bbox = BoundingBox(min_lat=52.0, min_lon=13.0, max_lat=53.0, max_lon=14.0)
    assert bbox.min_lat == 52.0
    assert bbox.min_lon == 13.0
    assert bbox.max_lat == 53.0
    assert bbox.max_lon == 14.0


def test_bounding_box_center():
    """BoundingBox.center() calculates midpoint correctly."""
    bbox = BoundingBox(min_lat=50.0, min_lon=10.0, max_lat=60.0, max_lon=20.0)
    center = bbox.center()
    assert center == (55.0, 15.0)


@pytest.mark.asyncio
async def test_geocode_city_success():
    """geocode_city returns BoundingBox for valid city."""
    mock_response = MagicMock()
    mock_response.json.return_value = [
        {
            "lat": "52.5200",
            "lon": "13.4050",
            "boundingbox": ["52.3382", "52.6755", "13.0884", "13.7612"],
            "display_name": "Berlin, Germany",
        }
    ]
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        bbox = await geocode_city("Berlin")

        assert bbox.min_lat == 52.3382
        assert bbox.max_lat == 52.6755
        assert bbox.min_lon == 13.0884
        assert bbox.max_lon == 13.7612
        mock_get.assert_called_once()


@pytest.mark.asyncio
async def test_geocode_city_not_found():
    """geocode_city raises ValueError when city not found."""
    mock_response = MagicMock()
    mock_response.json.return_value = []
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        with pytest.raises(ValueError, match="City not found: 'NonexistentCity'"):
            await geocode_city("NonexistentCity")


@pytest.mark.asyncio
async def test_geocode_city_http_error():
    """geocode_city propagates httpx.HTTPError on network failure."""
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.side_effect = httpx.HTTPError("Network error")

        with pytest.raises(httpx.HTTPError, match="Network error"):
            await geocode_city("Berlin")


def test_generate_hexagons_returns_non_empty():
    """generate_hexagons returns non-empty list for real bounding box."""
    bbox = BoundingBox(min_lat=52.5, min_lon=13.3, max_lat=52.6, max_lon=13.5)
    hexagons = generate_hexagons(bbox, resolution=8)
    assert len(hexagons) > 0


def test_generate_hexagons_valid_h3_indexes():
    """generate_hexagons returns valid H3 index strings."""
    bbox = BoundingBox(min_lat=52.5, min_lon=13.3, max_lat=52.55, max_lon=13.35)
    hexagons = generate_hexagons(bbox, resolution=8)
    for hex_id in hexagons:
        assert h3.is_valid_cell(hex_id)


def test_generate_hexagons_different_resolutions():
    """generate_hexagons with higher resolution returns more cells."""
    bbox = BoundingBox(min_lat=52.5, min_lon=13.3, max_lat=52.6, max_lon=13.5)
    hexagons_res7 = generate_hexagons(bbox, resolution=7)
    hexagons_res8 = generate_hexagons(bbox, resolution=8)
    # Higher resolution = smaller cells = more cells for same area
    assert len(hexagons_res8) > len(hexagons_res7)


def test_hex_to_bbox_returns_valid_bbox():
    """hex_to_bbox converts H3 cell to valid BoundingBox."""
    # Generate a test hex at resolution 8 near Berlin
    test_hex = h3.latlng_to_cell(52.52, 13.405, 8)
    bbox = hex_to_bbox(test_hex)

    assert isinstance(bbox, BoundingBox)
    assert bbox.min_lat < bbox.max_lat
    assert bbox.min_lon < bbox.max_lon
    # Sanity check: bbox is roughly near Berlin
    assert 52.0 < bbox.min_lat < 53.0
    assert 13.0 < bbox.min_lon < 14.0


# ============================================================================
# overpass tests
# ============================================================================


def test_overpass_build_query_contains_bbox():
    """_build_query includes bbox coordinates in Overpass QL query."""
    client = OverpassClient()
    bbox = BoundingBox(min_lat=52.49, min_lon=13.28, max_lat=52.55, max_lon=13.47)
    query = client._build_query(bbox)

    assert "52.49" in query
    assert "13.28" in query
    assert "52.55" in query
    assert "13.47" in query


def test_overpass_build_query_contains_filters():
    """_build_query includes amenity and shop filters."""
    client = OverpassClient()
    bbox = BoundingBox(min_lat=52.49, min_lon=13.28, max_lat=52.55, max_lon=13.47)
    query = client._build_query(bbox)

    # Check that query includes some business amenities/shops
    assert "restaurant" in query
    assert "cafe" in query
    assert "bakery" in query
    # Check structure
    assert "node[" in query
    assert "amenity" in query
    assert "shop" in query


def test_overpass_parse_element_valid_amenity():
    """_parse_element with amenity tag and no website returns GeoLead."""
    client = OverpassClient()
    element = {
        "type": "node",
        "id": 123456,
        "lat": 52.52,
        "lon": 13.405,
        "tags": {
            "amenity": "restaurant",
            "name": "Test Restaurant",
            "addr:street": "Main St",
            "addr:housenumber": "42",
        },
    }
    lead = client._parse_element(element, city="Berlin")

    assert lead is not None
    assert lead.osm_id == 123456
    assert lead.name == "Test Restaurant"
    assert lead.category == "restaurant"
    assert lead.lat == 52.52
    assert lead.lon == 13.405
    assert lead.address == "Main St, 42"
    assert lead.city == "Berlin"
    assert lead.h3_index is not None


def test_overpass_parse_element_with_website_filtered():
    """_parse_element filters out elements with website tag."""
    client = OverpassClient()
    element = {
        "type": "node",
        "id": 789012,
        "lat": 52.53,
        "lon": 13.41,
        "tags": {
            "shop": "bakery",
            "name": "Test Bakery",
            "website": "https://testbakery.de",
        },
    }
    lead = client._parse_element(element, city="Berlin")
    assert lead is None


def test_overpass_parse_element_with_contact_website_filtered():
    """_parse_element filters out elements with contact:website tag."""
    client = OverpassClient()
    element = {
        "type": "node",
        "id": 999999,
        "lat": 52.51,
        "lon": 13.39,
        "tags": {
            "amenity": "cafe",
            "name": "Test Cafe",
            "contact:website": "https://testcafe.com",
        },
    }
    lead = client._parse_element(element, city="Berlin")
    assert lead is None


def test_overpass_parse_element_without_name_filtered():
    """_parse_element filters out elements without name tag."""
    client = OverpassClient()
    element = {
        "type": "node",
        "id": 111111,
        "lat": 52.52,
        "lon": 13.40,
        "tags": {
            "amenity": "restaurant",
            # No name tag
        },
    }
    lead = client._parse_element(element, city="Berlin")
    assert lead is None


def test_overpass_parse_element_address_parsing():
    """_parse_element correctly parses addr:* tags into address field."""
    client = OverpassClient()
    element = {
        "type": "node",
        "id": 222222,
        "lat": 52.52,
        "lon": 13.405,
        "tags": {
            "amenity": "cafe",
            "name": "Test Cafe",
            "addr:street": "Example Street",
            "addr:housenumber": "123A",
            "addr:city": "Berlin",
        },
    }
    lead = client._parse_element(element, city=None)

    assert lead is not None
    assert lead.address == "Example Street, 123A"
    assert lead.city == "Berlin"  # From OSM tags


def test_overpass_parse_element_phone_extraction():
    """_parse_element extracts phone tag correctly."""
    client = OverpassClient()
    element = {
        "type": "node",
        "id": 333333,
        "lat": 52.52,
        "lon": 13.405,
        "tags": {
            "shop": "bakery",
            "name": "Test Bakery",
            "phone": "+49 30 1234567",
        },
    }
    lead = client._parse_element(element, city="Berlin")

    assert lead is not None
    assert lead.phone == "+49 30 1234567"


def test_overpass_parse_element_h3_index_computed():
    """_parse_element computes H3 index from lat/lon."""
    client = OverpassClient()
    element = {
        "type": "node",
        "id": 444444,
        "lat": 52.52,
        "lon": 13.405,
        "tags": {
            "amenity": "restaurant",
            "name": "Test Restaurant",
        },
    }
    lead = client._parse_element(element, city="Berlin")

    assert lead is not None
    assert lead.h3_index is not None
    # Verify it's a valid H3 index
    assert h3.is_valid_cell(lead.h3_index)
    # Verify it's at resolution 8 (as per code)
    assert h3.get_resolution(lead.h3_index) == 8


@pytest.mark.asyncio
async def test_overpass_query_businesses_success():
    """query_businesses returns list of GeoLead for successful query."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "elements": [
            {
                "type": "node",
                "id": 123456,
                "lat": 52.52,
                "lon": 13.405,
                "tags": {
                    "amenity": "restaurant",
                    "name": "Test Restaurant",
                },
            },
            {
                "type": "node",
                "id": 789012,
                "lat": 52.53,
                "lon": 13.41,
                "tags": {
                    "shop": "bakery",
                    "name": "Test Bakery",
                },
            },
        ]
    }
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        client = OverpassClient(rate_limit=0.01)  # Fast for testing
        bbox = BoundingBox(min_lat=52.49, min_lon=13.28, max_lat=52.55, max_lon=13.47)
        leads = await client.query_businesses(bbox, city="Berlin")

        assert len(leads) == 2
        assert all(isinstance(lead, GeoLead) for lead in leads)
        assert leads[0].name == "Test Restaurant"
        assert leads[1].name == "Test Bakery"


@pytest.mark.asyncio
async def test_overpass_query_businesses_filters_websites():
    """query_businesses filters out businesses with websites."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "elements": [
            {
                "type": "node",
                "id": 123456,
                "lat": 52.52,
                "lon": 13.405,
                "tags": {
                    "amenity": "restaurant",
                    "name": "No Website Restaurant",
                },
            },
            {
                "type": "node",
                "id": 789012,
                "lat": 52.53,
                "lon": 13.41,
                "tags": {
                    "shop": "bakery",
                    "name": "Has Website Bakery",
                    "website": "https://example.com",
                },
            },
        ]
    }
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        client = OverpassClient(rate_limit=0.01)
        bbox = BoundingBox(min_lat=52.49, min_lon=13.28, max_lat=52.55, max_lon=13.47)
        leads = await client.query_businesses(bbox, city="Berlin")

        # Only the one without website should be returned
        assert len(leads) == 1
        assert leads[0].name == "No Website Restaurant"


@pytest.mark.asyncio
async def test_overpass_query_businesses_http_error():
    """query_businesses propagates httpx.HTTPError on failure."""
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.HTTPError("Overpass API error")

        client = OverpassClient(rate_limit=0.01)
        bbox = BoundingBox(min_lat=52.49, min_lon=13.28, max_lat=52.55, max_lon=13.47)

        with pytest.raises(httpx.HTTPError, match="Overpass API error"):
            await client.query_businesses(bbox, city="Berlin")


@pytest.mark.asyncio
async def test_overpass_rate_wait_enforces_delay():
    """_rate_wait enforces minimum delay between requests."""
    client = OverpassClient(rate_limit=0.5)  # 500ms between requests

    # First call should not wait
    start = time.monotonic()
    await client._rate_wait()
    first_duration = time.monotonic() - start
    assert first_duration < 0.1  # Should be nearly instant

    # Second call immediately after should wait ~500ms
    start = time.monotonic()
    await client._rate_wait()
    second_duration = time.monotonic() - start
    # Allow some tolerance for timing precision
    assert 0.4 < second_duration < 0.7


@pytest.mark.asyncio
async def test_overpass_close_cleans_up_client():
    """close() closes the httpx client."""
    client = OverpassClient()
    # Trigger client creation
    await client._get_client()
    assert client._http is not None
    assert not client._http.is_closed

    await client.close()
    assert client._http.is_closed


@pytest.mark.asyncio
async def test_overpass_client_lazy_initialization():
    """OverpassClient lazily creates httpx.AsyncClient on first use."""
    client = OverpassClient()
    assert client._http is None

    http_client = await client._get_client()
    assert client._http is not None
    assert http_client is client._http


@pytest.mark.asyncio
async def test_overpass_client_reuses_client():
    """OverpassClient reuses the same httpx.AsyncClient across calls."""
    client = OverpassClient()
    first_client = await client._get_client()
    second_client = await client._get_client()
    assert first_client is second_client


def test_business_amenities_and_shops_defined():
    """BUSINESS_AMENITIES and BUSINESS_SHOPS constants are non-empty lists."""
    assert isinstance(BUSINESS_AMENITIES, list)
    assert len(BUSINESS_AMENITIES) > 0
    assert "restaurant" in BUSINESS_AMENITIES

    assert isinstance(BUSINESS_SHOPS, list)
    assert len(BUSINESS_SHOPS) > 0
    assert "bakery" in BUSINESS_SHOPS


def test_geolead_dataclass_creation():
    """GeoLead dataclass can be instantiated with all fields."""
    lead = GeoLead(
        osm_id=123456,
        name="Test Business",
        category="restaurant",
        lat=52.52,
        lon=13.405,
        address="Main St, 42",
        city="Berlin",
        phone="+49 30 1234567",
        h3_index="882a100d21bffff",
        raw_tags={"amenity": "restaurant", "name": "Test Business"},
    )
    assert lead.osm_id == 123456
    assert lead.name == "Test Business"
    assert lead.category == "restaurant"
    assert lead.raw_tags["amenity"] == "restaurant"


def test_geolead_dataclass_optional_fields():
    """GeoLead dataclass works with minimal required fields only."""
    lead = GeoLead(
        osm_id=123456,
        name="Minimal Business",
        category="cafe",
        lat=52.52,
        lon=13.405,
    )
    assert lead.osm_id == 123456
    assert lead.address is None
    assert lead.city is None
    assert lead.phone is None
    assert lead.h3_index is None
    assert lead.raw_tags == {}
