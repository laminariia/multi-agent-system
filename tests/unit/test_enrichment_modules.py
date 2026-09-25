"""Unit tests for enrichment modules (OSINT, Hunter, Apollo, Waterfall)."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.enrichment.apollo import ApolloEnricher
from src.enrichment.hunter import HunterEnricher
from src.enrichment.osint import EnrichmentResult, OsintEnricher
from src.enrichment.waterfall import EnrichmentWaterfall

# ============================================================================
# EnrichmentResult tests
# ============================================================================


def test_enrichment_result_default_values():
    """Test EnrichmentResult dataclass with default values."""
    result = EnrichmentResult()
    assert result.email is None
    assert result.phone is None
    assert result.website is None
    assert result.source == ""
    assert result.confidence == 0.0
    assert result.raw_data is None


def test_enrichment_result_with_values():
    """Test EnrichmentResult creation with specific values."""
    result = EnrichmentResult(
        email="test@example.com",
        phone="+1234567890",
        website="https://example.com",
        source="test_source",
        confidence=0.85,
        raw_data={"key": "value"},
    )
    assert result.email == "test@example.com"
    assert result.phone == "+1234567890"
    assert result.website == "https://example.com"
    assert result.source == "test_source"
    assert result.confidence == 0.85
    assert result.raw_data == {"key": "value"}


# ============================================================================
# OsintEnricher tests
# ============================================================================


@pytest.mark.asyncio
async def test_osint_enricher_finds_email():
    """Test OsintEnricher finds email in DuckDuckGo results."""
    osint = OsintEnricher()

    html_response = """
    <html>
        <body>
            <div>Contact us at owner@business.com</div>
            <div>Phone: +1234567890</div>
        </body>
    </html>
    """

    mock_response = MagicMock()
    mock_response.text = html_response
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        result = await osint.enrich("Test Business", "New York")

        assert result.email == "owner@business.com"
        assert result.phone == "+1234567890"
        assert result.source == "osint_duckduckgo"
        assert result.confidence == 0.5  # Both email and phone found
        assert result.raw_data["emails_found"] == 1
        assert result.raw_data["phones_found"] == 1

    await osint.close()


@pytest.mark.asyncio
async def test_osint_enricher_finds_phone():
    """Test OsintEnricher finds phone in search results."""
    osint = OsintEnricher()

    html_response = """
    <html>
        <body>
            <div>Call us: +1-555-123-4567</div>
        </body>
    </html>
    """

    mock_response = MagicMock()
    mock_response.text = html_response
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        result = await osint.enrich("Test Business", "New York")

        assert result.email is None
        assert result.phone == "+1-555-123-4567"
        assert result.confidence == 0.0  # No email found

    await osint.close()


@pytest.mark.asyncio
async def test_osint_enricher_no_results():
    """Test OsintEnricher returns empty result when nothing found."""
    osint = OsintEnricher()

    html_response = "<html><body><div>No contact info here</div></body></html>"

    mock_response = MagicMock()
    mock_response.text = html_response
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        result = await osint.enrich("Test Business", "New York")

        assert result.email is None
        assert result.phone is None
        assert result.confidence == 0.0
        assert result.source == "osint_duckduckgo"

    await osint.close()


@pytest.mark.asyncio
async def test_osint_enricher_http_error():
    """Test OsintEnricher handles HTTP errors gracefully."""
    osint = OsintEnricher()

    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        side_effect=httpx.TimeoutException("Timeout"),
    ):
        result = await osint.enrich("Test Business", "New York")

        assert result.email is None
        assert result.confidence == 0.0
        assert result.source == "osint_duckduckgo"

    await osint.close()


@pytest.mark.asyncio
async def test_osint_enricher_filters_false_positives():
    """Test OsintEnricher filters out false positive emails."""
    osint = OsintEnricher()

    html_response = """
    <html>
        <body>
            <div>logo@example.com</div>
            <div>image.png@duckduckgo.com</div>
            <div>icon.jpg</div>
            <div>valid@business.com</div>
        </body>
    </html>
    """

    mock_response = MagicMock()
    mock_response.text = html_response
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        result = await osint.enrich("Test Business", "New York")

        # Should only find valid@business.com (filters out example.com, .png, duckduckgo)
        assert result.email == "valid@business.com"
        assert result.confidence == 0.4

    await osint.close()


@pytest.mark.asyncio
async def test_osint_enricher_close_cleanup():
    """Test OsintEnricher.close() cleans up HTTP client."""
    osint = OsintEnricher()

    # Trigger client creation
    html_response = "<html></html>"
    mock_response = MagicMock()
    mock_response.text = html_response
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response
        await osint.enrich("Test", "NY")

    # Mock the client to verify close is called
    osint._http = MagicMock()
    osint._http.is_closed = False
    osint._http.aclose = AsyncMock()

    await osint.close()

    osint._http.aclose.assert_called_once()


# ============================================================================
# HunterEnricher tests
# ============================================================================


@pytest.mark.asyncio
async def test_hunter_enricher_success():
    """Test HunterEnricher successfully finds emails."""
    hunter = HunterEnricher(api_key="test_key")

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "data": {
            "emails": [
                {"value": "owner@example.com", "type": "personal", "confidence": 92},
                {"value": "info@example.com", "type": "generic", "confidence": 45},
            ]
        }
    }
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        result = await hunter.enrich("example.com")

        assert result.email == "owner@example.com"
        assert result.source == "hunter"
        assert result.confidence == 0.92  # 92/100
        assert result.raw_data["domain"] == "example.com"
        assert result.raw_data["total_found"] == 2

    await hunter.close()


@pytest.mark.asyncio
async def test_hunter_enricher_no_emails_found():
    """Test HunterEnricher returns empty result when no emails found."""
    hunter = HunterEnricher(api_key="test_key")

    mock_response = MagicMock()
    mock_response.json.return_value = {"data": {"emails": []}}
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        result = await hunter.enrich("example.com")

        assert result.email is None
        assert result.confidence == 0.0
        assert result.source == "hunter"

    await hunter.close()


@pytest.mark.asyncio
async def test_hunter_enricher_no_api_key():
    """Test HunterEnricher returns empty result when no API key."""
    hunter = HunterEnricher(api_key="")

    result = await hunter.enrich("example.com")

    assert result.email is None
    assert result.confidence == 0.0
    assert result.source == "hunter"

    await hunter.close()


@pytest.mark.asyncio
async def test_hunter_enricher_http_error():
    """Test HunterEnricher handles HTTP errors gracefully."""
    hunter = HunterEnricher(api_key="test_key")

    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        side_effect=httpx.ConnectError("Connection failed"),
    ):
        result = await hunter.enrich("example.com")

        assert result.email is None
        assert result.confidence == 0.0
        assert result.source == "hunter"

    await hunter.close()


@pytest.mark.asyncio
async def test_hunter_enricher_picks_highest_confidence():
    """Test HunterEnricher picks email with highest confidence."""
    hunter = HunterEnricher(api_key="test_key")

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "data": {
            "emails": [
                {"value": "info@example.com", "confidence": 30},
                {"value": "ceo@example.com", "confidence": 95},
                {"value": "contact@example.com", "confidence": 50},
            ]
        }
    }
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        result = await hunter.enrich("example.com")

        assert result.email == "ceo@example.com"
        assert result.confidence == 0.95

    await hunter.close()


@pytest.mark.asyncio
async def test_hunter_enricher_close_cleanup():
    """Test HunterEnricher.close() cleans up HTTP client."""
    hunter = HunterEnricher(api_key="test_key")

    # Trigger client creation
    mock_response = MagicMock()
    mock_response.json.return_value = {"data": {"emails": []}}
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response
        await hunter.enrich("example.com")

    # Mock the client to verify close is called
    hunter._http = MagicMock()
    hunter._http.is_closed = False
    hunter._http.aclose = AsyncMock()

    await hunter.close()

    hunter._http.aclose.assert_called_once()


# ============================================================================
# ApolloEnricher tests
# ============================================================================


@pytest.mark.asyncio
async def test_apollo_enricher_success():
    """Test ApolloEnricher successfully finds people."""
    apollo = ApolloEnricher(api_key="test_key")

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "people": [
            {
                "name": "John Doe",
                "title": "Owner",
                "email": "john@example.com",
                "phone_numbers": [{"raw_number": "+1234567890"}],
                "organization": {"name": "Test Business"},
            }
        ]
    }
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        result = await apollo.enrich("Test Business")

        assert result.email == "john@example.com"
        assert result.phone == "+1234567890"
        assert result.source == "apollo"
        assert result.confidence == 0.75
        assert result.raw_data["name"] == "John Doe"
        assert result.raw_data["title"] == "Owner"
        assert result.raw_data["organization"] == "Test Business"

    await apollo.close()


@pytest.mark.asyncio
async def test_apollo_enricher_no_people_found():
    """Test ApolloEnricher returns empty result when no people found."""
    apollo = ApolloEnricher(api_key="test_key")

    mock_response = MagicMock()
    mock_response.json.return_value = {"people": []}
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        result = await apollo.enrich("Test Business")

        assert result.email is None
        assert result.confidence == 0.0
        assert result.source == "apollo"

    await apollo.close()


@pytest.mark.asyncio
async def test_apollo_enricher_no_api_key():
    """Test ApolloEnricher returns empty result when no API key."""
    apollo = ApolloEnricher(api_key="")

    result = await apollo.enrich("Test Business")

    assert result.email is None
    assert result.confidence == 0.0
    assert result.source == "apollo"

    await apollo.close()


@pytest.mark.asyncio
async def test_apollo_enricher_http_error():
    """Test ApolloEnricher handles HTTP errors gracefully."""
    apollo = ApolloEnricher(api_key="test_key")

    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Server error", request=MagicMock(), response=mock_response
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        result = await apollo.enrich("Test Business")

        assert result.email is None
        assert result.confidence == 0.0
        assert result.source == "apollo"

    await apollo.close()


@pytest.mark.asyncio
async def test_apollo_enricher_with_city_filter():
    """Test ApolloEnricher includes city in search params."""
    apollo = ApolloEnricher(api_key="test_key")

    mock_response = MagicMock()
    mock_response.json.return_value = {
        "people": [
            {
                "name": "Jane Smith",
                "email": "jane@example.com",
                "phone_numbers": [],
                "organization": {},
            }
        ]
    }
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        result = await apollo.enrich("Test Business", city="New York")

        # Verify city was included in the request
        call_args = mock_post.call_args
        json_payload = call_args.kwargs["json"]
        assert json_payload["person_locations[]"] == "New York"

        assert result.email == "jane@example.com"

    await apollo.close()


@pytest.mark.asyncio
async def test_apollo_enricher_close_cleanup():
    """Test ApolloEnricher.close() cleans up HTTP client."""
    apollo = ApolloEnricher(api_key="test_key")

    # Trigger client creation
    mock_response = MagicMock()
    mock_response.json.return_value = {"people": []}
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        await apollo.enrich("Test Business")

    # Mock the client to verify close is called
    apollo._http = MagicMock()
    apollo._http.is_closed = False
    apollo._http.aclose = AsyncMock()

    await apollo.close()

    apollo._http.aclose.assert_called_once()


# ============================================================================
# EnrichmentWaterfall tests
# ============================================================================


@pytest.mark.asyncio
async def test_waterfall_stops_at_osint():
    """Test waterfall stops at OSINT when email found (doesn't call Hunter/Apollo)."""
    waterfall = EnrichmentWaterfall(hunter_api_key="hunter_key", apollo_api_key="apollo_key")

    osint_result = EnrichmentResult(
        email="found@osint.com",
        source="osint_duckduckgo",
        confidence=0.5,
    )

    with (
        patch.object(waterfall._osint, "enrich", new_callable=AsyncMock, return_value=osint_result) as mock_osint,
        patch.object(waterfall._hunter, "enrich", new_callable=AsyncMock) as mock_hunter,
        patch.object(waterfall._apollo, "enrich", new_callable=AsyncMock) as mock_apollo,
    ):
        result = await waterfall.enrich("Test Business", "New York", domain="test.com")

        # OSINT was called
        mock_osint.assert_called_once_with("Test Business", "New York")

        # Hunter and Apollo were NOT called (stopped early)
        mock_hunter.assert_not_called()
        mock_apollo.assert_not_called()

        assert result.email == "found@osint.com"
        assert waterfall.total_cost == Decimal("0")

    await waterfall.close()


@pytest.mark.asyncio
async def test_waterfall_falls_through_to_hunter():
    """Test waterfall falls through to Hunter when OSINT fails."""
    waterfall = EnrichmentWaterfall(hunter_api_key="hunter_key", apollo_api_key="apollo_key")

    osint_result = EnrichmentResult(source="osint_duckduckgo", confidence=0.0)
    hunter_result = EnrichmentResult(email="found@hunter.com", source="hunter", confidence=0.8)

    with (
        patch.object(waterfall._osint, "enrich", new_callable=AsyncMock, return_value=osint_result),
        patch.object(
            waterfall._hunter,
            "enrich",
            new_callable=AsyncMock,
            return_value=hunter_result,
        ) as mock_hunter,
        patch.object(waterfall._apollo, "enrich", new_callable=AsyncMock) as mock_apollo,
    ):
        result = await waterfall.enrich("Test Business", "New York", domain="test.com")

        # Hunter was called
        mock_hunter.assert_called_once_with("test.com")

        # Apollo was NOT called (stopped at Hunter)
        mock_apollo.assert_not_called()

        assert result.email == "found@hunter.com"
        assert waterfall.total_cost == Decimal("0.01")

    await waterfall.close()


@pytest.mark.asyncio
async def test_waterfall_falls_through_to_apollo():
    """Test waterfall falls through to Apollo when OSINT and Hunter fail."""
    waterfall = EnrichmentWaterfall(hunter_api_key="hunter_key", apollo_api_key="apollo_key")

    osint_result = EnrichmentResult(source="osint_duckduckgo", confidence=0.0)
    hunter_result = EnrichmentResult(source="hunter", confidence=0.0)
    apollo_result = EnrichmentResult(
        email="found@apollo.com",
        source="apollo",
        confidence=0.75,
    )

    with (
        patch.object(waterfall._osint, "enrich", new_callable=AsyncMock, return_value=osint_result),
        patch.object(
            waterfall._hunter,
            "enrich",
            new_callable=AsyncMock,
            return_value=hunter_result,
        ),
        patch.object(
            waterfall._apollo,
            "enrich",
            new_callable=AsyncMock,
            return_value=apollo_result,
        ) as mock_apollo,
    ):
        result = await waterfall.enrich("Test Business", "New York", domain="test.com")

        # Apollo was called
        mock_apollo.assert_called_once_with("Test Business", city="New York")

        assert result.email == "found@apollo.com"
        # Hunter + Apollo costs
        assert waterfall.total_cost == Decimal("0.06")

    await waterfall.close()


@pytest.mark.asyncio
async def test_waterfall_all_sources_fail():
    """Test waterfall returns empty result when all sources fail."""
    waterfall = EnrichmentWaterfall(hunter_api_key="hunter_key", apollo_api_key="apollo_key")

    empty_result = EnrichmentResult(confidence=0.0)

    with (
        patch.object(waterfall._osint, "enrich", new_callable=AsyncMock, return_value=empty_result),
        patch.object(waterfall._hunter, "enrich", new_callable=AsyncMock, return_value=empty_result),
        patch.object(waterfall._apollo, "enrich", new_callable=AsyncMock, return_value=empty_result),
    ):
        result = await waterfall.enrich("Test Business", "New York", domain="test.com")

        assert result.email is None
        assert result.source == "none"
        assert result.confidence == 0.0
        # All sources tried
        assert waterfall.total_cost == Decimal("0.06")

    await waterfall.close()


@pytest.mark.asyncio
async def test_waterfall_skips_hunter_no_api_key():
    """Test waterfall skips Hunter when no API key provided."""
    waterfall = EnrichmentWaterfall(apollo_api_key="apollo_key")  # No Hunter key

    osint_result = EnrichmentResult(source="osint_duckduckgo", confidence=0.0)
    apollo_result = EnrichmentResult(
        email="found@apollo.com",
        source="apollo",
        confidence=0.75,
    )

    with (
        patch.object(waterfall._osint, "enrich", new_callable=AsyncMock, return_value=osint_result),
        patch.object(
            waterfall._apollo,
            "enrich",
            new_callable=AsyncMock,
            return_value=apollo_result,
        ),
    ):
        result = await waterfall.enrich("Test Business", "New York", domain="test.com")

        # Went straight from OSINT to Apollo (Hunter skipped)
        assert result.email == "found@apollo.com"
        assert waterfall.total_cost == Decimal("0.05")  # Only Apollo cost

    await waterfall.close()


@pytest.mark.asyncio
async def test_waterfall_skips_apollo_no_api_key():
    """Test waterfall skips Apollo when no API key provided."""
    waterfall = EnrichmentWaterfall(hunter_api_key="hunter_key")  # No Apollo key

    osint_result = EnrichmentResult(source="osint_duckduckgo", confidence=0.0)
    hunter_result = EnrichmentResult(source="hunter", confidence=0.0)

    with (
        patch.object(waterfall._osint, "enrich", new_callable=AsyncMock, return_value=osint_result),
        patch.object(
            waterfall._hunter,
            "enrich",
            new_callable=AsyncMock,
            return_value=hunter_result,
        ),
    ):
        result = await waterfall.enrich("Test Business", "New York", domain="test.com")

        # Failed at Hunter, Apollo skipped
        assert result.email is None
        assert result.source == "none"
        assert waterfall.total_cost == Decimal("0.01")  # Only Hunter cost

    await waterfall.close()


@pytest.mark.asyncio
async def test_waterfall_total_cost_accumulation():
    """Test waterfall accumulates costs across multiple enrichment calls."""
    waterfall = EnrichmentWaterfall(hunter_api_key="hunter_key", apollo_api_key="apollo_key")

    osint_result = EnrichmentResult(source="osint_duckduckgo", confidence=0.0)
    hunter_result = EnrichmentResult(email="found@hunter.com", source="hunter", confidence=0.8)

    with (
        patch.object(waterfall._osint, "enrich", new_callable=AsyncMock, return_value=osint_result),
        patch.object(
            waterfall._hunter,
            "enrich",
            new_callable=AsyncMock,
            return_value=hunter_result,
        ),
    ):
        # First enrichment
        await waterfall.enrich("Business 1", "NY", domain="test1.com")
        assert waterfall.total_cost == Decimal("0.01")

        # Second enrichment
        await waterfall.enrich("Business 2", "LA", domain="test2.com")
        assert waterfall.total_cost == Decimal("0.02")

        # Third enrichment
        await waterfall.enrich("Business 3", "SF", domain="test3.com")
        assert waterfall.total_cost == Decimal("0.03")

    await waterfall.close()


@pytest.mark.asyncio
async def test_waterfall_close_all_enrichers():
    """Test waterfall closes all HTTP clients."""
    waterfall = EnrichmentWaterfall(hunter_api_key="hunter_key", apollo_api_key="apollo_key")

    # Mock all close methods
    waterfall._osint.close = AsyncMock()
    waterfall._hunter.close = AsyncMock()
    waterfall._apollo.close = AsyncMock()

    await waterfall.close()

    waterfall._osint.close.assert_called_once()
    waterfall._hunter.close.assert_called_once()
    waterfall._apollo.close.assert_called_once()
