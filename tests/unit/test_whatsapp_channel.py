"""Unit tests for WhatsApp Business API channel client."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.channels.whatsapp import WhatsAppClient

# ============================================================================
# Construction & configuration
# ============================================================================


def test_whatsapp_client_creation():
    """Test WhatsAppClient creation with API key and phone number ID."""
    client = WhatsAppClient(
        api_key="test_key",
        phone_number_id="123456789",
    )
    assert client._api_key == "test_key"
    assert client._phone_number_id == "123456789"


def test_whatsapp_client_custom_timeout():
    """Test WhatsAppClient creation with custom timeout."""
    client = WhatsAppClient(
        api_key="test_key",
        phone_number_id="123",
        timeout=30.0,
    )
    assert client._timeout == 30.0


def test_whatsapp_client_default_timeout():
    """Test WhatsAppClient default timeout is 15s."""
    client = WhatsAppClient(api_key="test_key", phone_number_id="123")
    assert client._timeout == 15.0


def test_whatsapp_client_is_configured_true():
    """Test is_configured returns True when API key and phone ID are set."""
    client = WhatsAppClient(api_key="test_key", phone_number_id="123")
    assert client.is_configured is True


def test_whatsapp_client_is_configured_false_no_key():
    """Test is_configured returns False when API key is empty."""
    client = WhatsAppClient(api_key="", phone_number_id="123")
    assert client.is_configured is False


def test_whatsapp_client_is_configured_false_no_phone():
    """Test is_configured returns False when phone_number_id is empty."""
    client = WhatsAppClient(api_key="test_key", phone_number_id="")
    assert client.is_configured is False


def test_whatsapp_client_is_configured_false_none():
    """Test is_configured returns False when both are None."""
    client = WhatsAppClient(api_key=None, phone_number_id=None)
    assert client.is_configured is False


# ============================================================================
# Send message
# ============================================================================


@pytest.mark.asyncio
async def test_send_message_template_success():
    """Test sending a template message via WhatsApp Business API."""
    client = WhatsAppClient(api_key="test_key", phone_number_id="123456")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "messaging_product": "whatsapp",
        "contacts": [{"input": "+1234567890", "wa_id": "1234567890"}],
        "messages": [{"id": "wamid.abc123def456"}],
    }
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        result = await client.send_message(
            phone="+1234567890",
            template="hello_world",
            params={"1": "John"},
        )

        assert result is not None
        assert result["message_id"] == "wamid.abc123def456"
        assert result["status"] == "sent"

        # Verify API call
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        json_body = call_args.kwargs["json"]
        assert json_body["messaging_product"] == "whatsapp"
        assert json_body["to"] == "+1234567890"
        assert json_body["type"] == "template"
        assert json_body["template"]["name"] == "hello_world"

    await client.close()


@pytest.mark.asyncio
async def test_send_message_text_success():
    """Test sending a plain text message via WhatsApp Business API."""
    client = WhatsAppClient(api_key="test_key", phone_number_id="123456")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "messaging_product": "whatsapp",
        "contacts": [{"input": "+1234567890", "wa_id": "1234567890"}],
        "messages": [{"id": "wamid.text789"}],
    }
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        result = await client.send_message(
            phone="+1234567890",
            template=None,
            params=None,
            text_body="Hello, this is a follow-up.",
        )

        assert result is not None
        assert result["message_id"] == "wamid.text789"

        call_args = mock_post.call_args
        json_body = call_args.kwargs["json"]
        assert json_body["type"] == "text"
        assert json_body["text"]["body"] == "Hello, this is a follow-up."

    await client.close()


@pytest.mark.asyncio
async def test_send_message_not_configured():
    """Test send_message returns None when client is not configured."""
    client = WhatsAppClient(api_key="", phone_number_id="")

    result = await client.send_message(
        phone="+1234567890",
        template="hello_world",
    )

    assert result is None

    await client.close()


@pytest.mark.asyncio
async def test_send_message_empty_phone():
    """Test send_message returns None for empty phone number."""
    client = WhatsAppClient(api_key="test_key", phone_number_id="123")

    result = await client.send_message(phone="", template="hello_world")

    assert result is None

    await client.close()


@pytest.mark.asyncio
async def test_send_message_http_error():
    """Test send_message handles HTTP errors gracefully."""
    client = WhatsAppClient(api_key="test_key", phone_number_id="123")

    with patch(
        "httpx.AsyncClient.post",
        new_callable=AsyncMock,
        side_effect=httpx.ConnectError("Connection failed"),
    ):
        result = await client.send_message(
            phone="+1234567890",
            template="hello_world",
        )

        assert result is None

    await client.close()


@pytest.mark.asyncio
async def test_send_message_api_error_response():
    """Test send_message handles API error responses gracefully."""
    client = WhatsAppClient(api_key="test_key", phone_number_id="123")

    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Bad Request",
        request=MagicMock(),
        response=mock_response,
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        result = await client.send_message(
            phone="+1234567890",
            template="invalid_template",
        )

        assert result is None

    await client.close()


@pytest.mark.asyncio
async def test_send_message_rate_limited():
    """Test send_message handles 429 rate limit gracefully."""
    client = WhatsAppClient(api_key="test_key", phone_number_id="123")

    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Rate limited",
        request=MagicMock(),
        response=mock_response,
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        result = await client.send_message(
            phone="+1234567890",
            template="hello_world",
        )

        assert result is None

    await client.close()


@pytest.mark.asyncio
async def test_send_message_with_language():
    """Test send_message includes language_code in template."""
    client = WhatsAppClient(api_key="test_key", phone_number_id="123")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "messaging_product": "whatsapp",
        "contacts": [{"input": "+7999", "wa_id": "7999"}],
        "messages": [{"id": "wamid.lang123"}],
    }
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        result = await client.send_message(
            phone="+7999",
            template="welcome_ru",
            params={"1": "World"},
            language_code="ru",
        )

        assert result is not None
        call_args = mock_post.call_args
        json_body = call_args.kwargs["json"]
        assert json_body["template"]["language"]["code"] == "ru"

    await client.close()


# ============================================================================
# Check delivery status
# ============================================================================


@pytest.mark.asyncio
async def test_check_delivery_status_success():
    """Test checking delivery status of a sent message."""
    client = WhatsAppClient(api_key="test_key", phone_number_id="123")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "id": "wamid.abc123",
        "status": "delivered",
        "timestamp": "1678901234",
    }
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        result = await client.check_delivery_status("wamid.abc123")

        assert result is not None
        assert result["message_id"] == "wamid.abc123"
        assert result["status"] == "delivered"

    await client.close()


@pytest.mark.asyncio
async def test_check_delivery_status_not_found():
    """Test check_delivery_status returns None for unknown message."""
    client = WhatsAppClient(api_key="test_key", phone_number_id="123")

    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Not Found",
        request=MagicMock(),
        response=mock_response,
    )

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        result = await client.check_delivery_status("wamid.unknown")

        assert result is None

    await client.close()


@pytest.mark.asyncio
async def test_check_delivery_status_not_configured():
    """Test check_delivery_status returns None when not configured."""
    client = WhatsAppClient(api_key="", phone_number_id="")

    result = await client.check_delivery_status("wamid.abc123")

    assert result is None

    await client.close()


@pytest.mark.asyncio
async def test_check_delivery_status_http_error():
    """Test check_delivery_status handles HTTP errors gracefully."""
    client = WhatsAppClient(api_key="test_key", phone_number_id="123")

    with patch(
        "httpx.AsyncClient.get",
        new_callable=AsyncMock,
        side_effect=httpx.TimeoutException("Timeout"),
    ):
        result = await client.check_delivery_status("wamid.abc123")

        assert result is None

    await client.close()


# ============================================================================
# HTTP client lifecycle
# ============================================================================


@pytest.mark.asyncio
async def test_whatsapp_close_cleanup():
    """Test WhatsAppClient.close() cleans up HTTP client."""
    client = WhatsAppClient(api_key="test_key", phone_number_id="123")

    client._http = MagicMock()
    client._http.is_closed = False
    client._http.aclose = AsyncMock()

    await client.close()

    client._http.aclose.assert_called_once()


@pytest.mark.asyncio
async def test_whatsapp_close_noop_when_no_client():
    """Test close() is safe when no HTTP client exists."""
    client = WhatsAppClient(api_key="test_key", phone_number_id="123")
    await client.close()  # Should not raise


@pytest.mark.asyncio
async def test_whatsapp_close_noop_when_already_closed():
    """Test close() is safe when HTTP client is already closed."""
    client = WhatsAppClient(api_key="test_key", phone_number_id="123")
    client._http = MagicMock()
    client._http.is_closed = True

    await client.close()  # Should not call aclose


@pytest.mark.asyncio
async def test_whatsapp_uses_bearer_auth():
    """Test WhatsAppClient uses Bearer token authentication."""
    client = WhatsAppClient(api_key="wa_test_token", phone_number_id="123")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "messaging_product": "whatsapp",
        "contacts": [{"input": "+1", "wa_id": "1"}],
        "messages": [{"id": "wamid.auth_test"}],
    }
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        await client.send_message(phone="+1", template="t")

        call_args = mock_post.call_args
        headers = call_args.kwargs.get("headers", {})
        assert headers.get("Authorization") == "Bearer wa_test_token"

    await client.close()
