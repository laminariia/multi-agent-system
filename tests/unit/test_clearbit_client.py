"""Unit tests for ClearbitClient enrichment integration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.enrichment.clearbit import ClearbitClient

# ============================================================================
# Construction & configuration
# ============================================================================


def test_clearbit_client_creation():
    """Test ClearbitClient creation with API key."""
    client = ClearbitClient(api_key="test_key")
    assert client._api_key == "test_key"
    assert client._timeout == 15.0


def test_clearbit_client_custom_timeout():
    """Test ClearbitClient creation with custom timeout."""
    client = ClearbitClient(api_key="test_key", timeout=30.0)
    assert client._timeout == 30.0


def test_clearbit_client_is_configured_true():
    """Test is_configured returns True when API key is set."""
    client = ClearbitClient(api_key="test_key")
    assert client.is_configured is True


def test_clearbit_client_is_configured_false_empty():
    """Test is_configured returns False when API key is empty."""
    client = ClearbitClient(api_key="")
    assert client.is_configured is False


def test_clearbit_client_is_configured_false_none():
    """Test is_configured returns False when API key is None."""
    client = ClearbitClient(api_key=None)
    assert client.is_configured is False


# ============================================================================
# Person lookup
# ============================================================================


@pytest.mark.asyncio
async def test_person_lookup_success():
    """Test ClearbitClient person lookup with valid email."""
    client = ClearbitClient(api_key="test_key")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "id": "abc123",
        "email": "ceo@example.com",
        "name": {
            "fullName": "John Doe",
            "givenName": "John",
            "familyName": "Doe",
        },
        "employment": {
            "name": "Example Corp",
            "title": "CEO",
            "role": "leadership",
            "seniority": "executive",
        },
        "location": "San Francisco, CA",
        "linkedin": {"handle": "johndoe"},
        "twitter": {"handle": "johndoe"},
    }
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        result = await client.person_lookup("ceo@example.com")

        assert result is not None
        assert result["email"] == "ceo@example.com"
        assert result["full_name"] == "John Doe"
        assert result["first_name"] == "John"
        assert result["last_name"] == "Doe"
        assert result["company"] == "Example Corp"
        assert result["title"] == "CEO"
        assert result["role"] == "leadership"
        assert result["seniority"] == "executive"
        assert result["location"] == "San Francisco, CA"
        assert result["linkedin"] == "johndoe"
        assert result["twitter"] == "johndoe"
        assert result["source"] == "clearbit"

        # Verify the API was called with correct auth
        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert "people/find" in call_args.args[0] or "people/find" in str(call_args)

    await client.close()


@pytest.mark.asyncio
async def test_person_lookup_not_found():
    """Test ClearbitClient person lookup returns None for unknown email."""
    client = ClearbitClient(api_key="test_key")

    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Not Found",
        request=MagicMock(),
        response=mock_response,
    )

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        result = await client.person_lookup("unknown@nowhere.com")

        assert result is None

    await client.close()


@pytest.mark.asyncio
async def test_person_lookup_no_api_key():
    """Test person lookup returns None when API key is empty."""
    client = ClearbitClient(api_key="")

    result = await client.person_lookup("ceo@example.com")

    assert result is None

    await client.close()


@pytest.mark.asyncio
async def test_person_lookup_http_error():
    """Test person lookup handles HTTP errors gracefully."""
    client = ClearbitClient(api_key="test_key")

    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        side_effect=httpx.ConnectError("Connection failed"),
    ):
        result = await client.person_lookup("ceo@example.com")

        assert result is None

    await client.close()


@pytest.mark.asyncio
async def test_person_lookup_partial_data():
    """Test person lookup handles partial API response gracefully."""
    client = ClearbitClient(api_key="test_key")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "email": "sparse@example.com",
        "name": {"fullName": "Jane"},
        # No employment, location, linkedin, twitter
    }
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        result = await client.person_lookup("sparse@example.com")

        assert result is not None
        assert result["email"] == "sparse@example.com"
        assert result["full_name"] == "Jane"
        assert result["company"] is None
        assert result["title"] is None
        assert result["linkedin"] is None
        assert result["source"] == "clearbit"

    await client.close()


@pytest.mark.asyncio
async def test_person_lookup_rate_limited():
    """Test person lookup handles 429 rate limit gracefully."""
    client = ClearbitClient(api_key="test_key")

    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Rate limited",
        request=MagicMock(),
        response=mock_response,
    )

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        result = await client.person_lookup("ceo@example.com")

        assert result is None

    await client.close()


# ============================================================================
# Company lookup
# ============================================================================


@pytest.mark.asyncio
async def test_company_lookup_success():
    """Test ClearbitClient company lookup with valid domain."""
    client = ClearbitClient(api_key="test_key")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "id": "company123",
        "name": "Example Corp",
        "domain": "example.com",
        "category": {"industry": "Technology", "sector": "Software"},
        "metrics": {
            "employees": 150,
            "employeesRange": "101-250",
            "raised": 50000000,
            "annualRevenue": "$10M-$50M",
        },
        "geo": {
            "city": "San Francisco",
            "state": "California",
            "country": "US",
        },
        "description": "A technology company.",
        "url": "https://example.com",
        "logo": "https://logo.clearbit.com/example.com",
        "linkedin": {"handle": "example-corp"},
        "twitter": {"handle": "examplecorp"},
        "tech": ["python", "react", "postgresql"],
    }
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        result = await client.company_lookup("example.com")

        assert result is not None
        assert result["name"] == "Example Corp"
        assert result["domain"] == "example.com"
        assert result["industry"] == "Technology"
        assert result["sector"] == "Software"
        assert result["employees"] == 150
        assert result["employees_range"] == "101-250"
        assert result["annual_revenue"] == "$10M-$50M"
        assert result["city"] == "San Francisco"
        assert result["state"] == "California"
        assert result["country"] == "US"
        assert result["description"] == "A technology company."
        assert result["url"] == "https://example.com"
        assert result["linkedin"] == "example-corp"
        assert result["twitter"] == "examplecorp"
        assert result["tech"] == ["python", "react", "postgresql"]
        assert result["source"] == "clearbit"

    await client.close()


@pytest.mark.asyncio
async def test_company_lookup_not_found():
    """Test company lookup returns None for unknown domain."""
    client = ClearbitClient(api_key="test_key")

    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Not Found",
        request=MagicMock(),
        response=mock_response,
    )

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        result = await client.company_lookup("unknown-domain-xyz.com")

        assert result is None

    await client.close()


@pytest.mark.asyncio
async def test_company_lookup_no_api_key():
    """Test company lookup returns None when API key is empty."""
    client = ClearbitClient(api_key="")

    result = await client.company_lookup("example.com")

    assert result is None

    await client.close()


@pytest.mark.asyncio
async def test_company_lookup_http_error():
    """Test company lookup handles HTTP errors gracefully."""
    client = ClearbitClient(api_key="test_key")

    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        side_effect=httpx.TimeoutException("Timeout"),
    ):
        result = await client.company_lookup("example.com")

        assert result is None

    await client.close()


@pytest.mark.asyncio
async def test_company_lookup_partial_data():
    """Test company lookup handles partial API response gracefully."""
    client = ClearbitClient(api_key="test_key")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "name": "Tiny Startup",
        "domain": "tiny.io",
        # No category, metrics, geo, etc.
    }
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        result = await client.company_lookup("tiny.io")

        assert result is not None
        assert result["name"] == "Tiny Startup"
        assert result["domain"] == "tiny.io"
        assert result["industry"] is None
        assert result["employees"] is None
        assert result["city"] is None
        assert result["source"] == "clearbit"

    await client.close()


# ============================================================================
# HTTP client lifecycle
# ============================================================================


@pytest.mark.asyncio
async def test_clearbit_close_cleanup():
    """Test ClearbitClient.close() cleans up HTTP client."""
    client = ClearbitClient(api_key="test_key")

    # Simulate a client being created
    client._http = MagicMock()
    client._http.is_closed = False
    client._http.aclose = AsyncMock()

    await client.close()

    client._http.aclose.assert_called_once()


@pytest.mark.asyncio
async def test_clearbit_close_noop_when_no_client():
    """Test close() is safe when no HTTP client exists."""
    client = ClearbitClient(api_key="test_key")
    # _http is None by default
    await client.close()  # Should not raise


@pytest.mark.asyncio
async def test_clearbit_close_noop_when_already_closed():
    """Test close() is safe when HTTP client already closed."""
    client = ClearbitClient(api_key="test_key")
    client._http = MagicMock()
    client._http.is_closed = True

    await client.close()  # Should not call aclose


@pytest.mark.asyncio
async def test_clearbit_get_client_creates_on_demand():
    """Test _get_client creates httpx.AsyncClient lazily."""
    client = ClearbitClient(api_key="test_key")
    assert client._http is None

    http_client = await client._get_client()
    assert http_client is not None
    assert isinstance(http_client, httpx.AsyncClient)

    await client.close()


@pytest.mark.asyncio
async def test_clearbit_get_client_reuses_existing():
    """Test _get_client reuses existing client when not closed."""
    client = ClearbitClient(api_key="test_key")

    first = await client._get_client()
    second = await client._get_client()
    assert first is second

    await client.close()


# ============================================================================
# Auth header
# ============================================================================


@pytest.mark.asyncio
async def test_clearbit_uses_bearer_auth():
    """Test ClearbitClient uses Bearer token authentication."""
    client = ClearbitClient(api_key="sk_test_abc123")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "email": "test@example.com",
        "name": {"fullName": "Test"},
    }
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        await client.person_lookup("test@example.com")

        call_args = mock_get.call_args
        headers = call_args.kwargs.get("headers", {})
        assert headers.get("Authorization") == "Bearer sk_test_abc123"

    await client.close()
