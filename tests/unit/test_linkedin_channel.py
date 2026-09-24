"""Unit tests for LinkedIn outreach channel client.

Updated to reflect the OAuth 2.0 implementation (no longer a stub).
Validation tests (empty fields, not-configured) return None before API calls.
Send-success tests mock httpx to verify the real API integration path.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.channels.linkedin import LinkedInClient

# ============================================================================
# Construction & configuration
# ============================================================================


def test_linkedin_client_creation():
    """Test LinkedInClient creation with API key (backward compat)."""
    client = LinkedInClient(api_key="test_key")
    assert client._access_token == "test_key"
    assert client._timeout == 30.0


def test_linkedin_client_custom_timeout():
    """Test LinkedInClient creation with custom timeout."""
    client = LinkedInClient(api_key="test_key", timeout=45.0)
    assert client._timeout == 45.0


def test_linkedin_client_is_configured_true():
    """Test is_configured returns True when API key is set."""
    client = LinkedInClient(api_key="test_key")
    assert client.is_configured is True


def test_linkedin_client_is_configured_false_empty():
    """Test is_configured returns False when API key is empty."""
    client = LinkedInClient(api_key="")
    assert client.is_configured is False


def test_linkedin_client_is_configured_false_none():
    """Test is_configured returns False when API key is None."""
    client = LinkedInClient(api_key=None)
    assert client.is_configured is False


# ============================================================================
# Send InMail (real OAuth implementation)
# ============================================================================


@pytest.mark.asyncio
async def test_send_inmail_success():
    """Test send_inmail sends via LinkedIn Marketing API and returns message_id."""
    client = LinkedInClient(api_key="test_key")

    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_response.content = b'{"id": "msg_abc123"}'
    mock_response.json.return_value = {"id": "msg_abc123"}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        result = await client.send_inmail(
            profile_url="https://www.linkedin.com/in/johndoe",
            subject="Partnership Opportunity",
            body="Hi John, I'd like to discuss a potential partnership.",
        )

    assert result is not None
    assert result["status"] == "sent"
    assert result["message_id"] == "msg_abc123"

    await client.close()


@pytest.mark.asyncio
async def test_send_inmail_not_configured():
    """Test send_inmail returns None when not configured."""
    client = LinkedInClient(api_key="")

    result = await client.send_inmail(
        profile_url="https://www.linkedin.com/in/johndoe",
        subject="Hello",
        body="Test",
    )

    assert result is None

    await client.close()


@pytest.mark.asyncio
async def test_send_inmail_empty_profile_url():
    """Test send_inmail returns None for empty profile URL."""
    client = LinkedInClient(api_key="test_key")

    result = await client.send_inmail(
        profile_url="",
        subject="Hello",
        body="Test",
    )

    assert result is None

    await client.close()


@pytest.mark.asyncio
async def test_send_inmail_empty_subject():
    """Test send_inmail returns None for empty subject."""
    client = LinkedInClient(api_key="test_key")

    result = await client.send_inmail(
        profile_url="https://www.linkedin.com/in/johndoe",
        subject="",
        body="Test",
    )

    assert result is None

    await client.close()


@pytest.mark.asyncio
async def test_send_inmail_empty_body():
    """Test send_inmail returns None for empty body."""
    client = LinkedInClient(api_key="test_key")

    result = await client.send_inmail(
        profile_url="https://www.linkedin.com/in/johndoe",
        subject="Hello",
        body="",
    )

    assert result is None

    await client.close()


@pytest.mark.asyncio
async def test_send_inmail_returns_message_id():
    """Test send_inmail returns message_id from API response."""
    client = LinkedInClient(api_key="test_key")

    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_response.content = b'{"id": "msg_xyz789"}'
    mock_response.json.return_value = {"id": "msg_xyz789"}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        result = await client.send_inmail(
            profile_url="https://www.linkedin.com/in/janedoe",
            subject="Web Development Services",
            body="Dear Jane, we offer full-stack development.",
        )

    assert result is not None
    assert result["message_id"] == "msg_xyz789"
    assert result["status"] == "sent"

    await client.close()


# ============================================================================
# Check connection status
# ============================================================================


@pytest.mark.asyncio
async def test_check_connection_status_success():
    """Test checking LinkedIn connection status via API."""
    client = LinkedInClient(api_key="test_key")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "id": "urn:li:person:abc123",
        "firstName": {"localized": {"en_US": "John"}},
        "lastName": {"localized": {"en_US": "Doe"}},
    }
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        result = await client.check_connection_status(
            "https://www.linkedin.com/in/johndoe",
        )

        assert result is not None
        assert result["profile_url"] == "https://www.linkedin.com/in/johndoe"
        assert result["exists"] is True

    await client.close()


@pytest.mark.asyncio
async def test_check_connection_status_not_found():
    """Test check_connection_status returns not-found for invalid profile."""
    client = LinkedInClient(api_key="test_key")

    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Not Found",
        request=MagicMock(),
        response=mock_response,
    )

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        result = await client.check_connection_status(
            "https://www.linkedin.com/in/nobody",
        )

        assert result is not None
        assert result["exists"] is False

    await client.close()


@pytest.mark.asyncio
async def test_check_connection_status_not_configured():
    """Test check_connection_status returns None when not configured."""
    client = LinkedInClient(api_key="")

    result = await client.check_connection_status(
        "https://www.linkedin.com/in/johndoe",
    )

    assert result is None

    await client.close()


@pytest.mark.asyncio
async def test_check_connection_status_empty_url():
    """Test check_connection_status returns None for empty URL."""
    client = LinkedInClient(api_key="test_key")

    result = await client.check_connection_status("")

    assert result is None

    await client.close()


@pytest.mark.asyncio
async def test_check_connection_status_http_error():
    """Test check_connection_status handles HTTP errors gracefully."""
    client = LinkedInClient(api_key="test_key")

    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        side_effect=httpx.ConnectError("Connection refused"),
    ):
        result = await client.check_connection_status(
            "https://www.linkedin.com/in/johndoe",
        )

        assert result is None

    await client.close()


@pytest.mark.asyncio
async def test_check_connection_status_rate_limited():
    """Test check_connection_status handles 429 rate limit."""
    client = LinkedInClient(api_key="test_key")

    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Rate limited",
        request=MagicMock(),
        response=mock_response,
    )

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        result = await client.check_connection_status(
            "https://www.linkedin.com/in/johndoe",
        )

        assert result is None

    await client.close()


# ============================================================================
# HTTP client lifecycle
# ============================================================================


@pytest.mark.asyncio
async def test_linkedin_close_cleanup():
    """Test LinkedInClient.close() cleans up HTTP client."""
    client = LinkedInClient(api_key="test_key")

    client._http = MagicMock()
    client._http.is_closed = False
    client._http.aclose = AsyncMock()

    await client.close()

    client._http.aclose.assert_called_once()


@pytest.mark.asyncio
async def test_linkedin_close_noop_when_no_client():
    """Test close() is safe when no HTTP client exists."""
    client = LinkedInClient(api_key="test_key")
    await client.close()  # Should not raise


@pytest.mark.asyncio
async def test_linkedin_close_noop_when_already_closed():
    """Test close() is safe when HTTP client is already closed."""
    client = LinkedInClient(api_key="test_key")
    client._http = MagicMock()
    client._http.is_closed = True

    await client.close()  # Should not call aclose


@pytest.mark.asyncio
async def test_linkedin_uses_bearer_auth_for_status():
    """Test LinkedInClient uses Bearer token for connection status check."""
    client = LinkedInClient(api_key="li_test_token")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "id": "urn:li:person:test",
        "firstName": {"localized": {"en_US": "Test"}},
        "lastName": {"localized": {"en_US": "User"}},
    }
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        await client.check_connection_status(
            "https://www.linkedin.com/in/testuser",
        )

        call_args = mock_get.call_args
        headers = call_args.kwargs.get("headers", {})
        assert headers.get("Authorization") == "Bearer li_test_token"

    await client.close()
