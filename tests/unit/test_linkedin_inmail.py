"""Unit tests for LinkedIn InMail OAuth implementation.

Tests cover:
- OAuth token refresh flow
- Token expiry detection and auto-refresh
- send_inmail with mocked httpx (success path)
- Rate limit enforcement (daily + monthly via Valkey)
- Profile URN extraction from various URL formats
- HTTP 429 handling (raises PlatformRateLimitError)
- HTTP 403 handling (token expired + refresh)
- Connection status check
- Input validation (empty fields)
- Encrypted token loading
- Backward compatibility with api_key
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.channels.linkedin import (
    LINKEDIN_API_BASE,
    LINKEDIN_RATE_LIMIT_DAILY,
    LINKEDIN_RATE_LIMIT_MONTHLY,
    LinkedInClient,
)
from src.core.exceptions import PlatformAPIError, PlatformRateLimitError

# ============================================================================
# Helpers
# ============================================================================


def _make_client(
    *,
    api_key: str = "test_token",
    client_id: str = "cid",
    client_secret: str = "csecret",
    valkey: AsyncMock | None = None,
    access_token: str = "",
    refresh_token: str = "",
    expires_at: float = 0.0,
) -> LinkedInClient:
    """Create a LinkedInClient with direct token injection (bypassing Fernet)."""
    client = LinkedInClient(
        client_id=client_id,
        client_secret=client_secret,
        api_key=api_key,
        valkey=valkey,
    )
    if access_token:
        client._access_token = access_token
    if refresh_token:
        client._refresh_token = refresh_token
    if expires_at:
        client._token_expires_at = expires_at
    return client


def _mock_valkey(daily: int = 0, monthly: int = 0) -> AsyncMock:
    """Create a mock Valkey client with configurable counter values."""
    valkey = AsyncMock()

    async def _get(key: str) -> str | None:
        if "daily" in key:
            return str(daily) if daily else None
        if "monthly" in key:
            return str(monthly) if monthly else None
        return None

    valkey.get = AsyncMock(side_effect=_get)

    pipe = AsyncMock()
    pipe.incr = MagicMock(return_value=pipe)
    pipe.expire = MagicMock(return_value=pipe)
    pipe.execute = AsyncMock(return_value=[1, True, 1, True])
    valkey.pipeline = MagicMock(return_value=pipe)

    return valkey


# ============================================================================
# send_inmail -- success path
# ============================================================================


@pytest.mark.asyncio
async def test_send_inmail_success():
    """Test successful InMail send with mocked httpx."""
    valkey = _mock_valkey()
    client = _make_client(access_token="valid_token", valkey=valkey)

    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_response.content = b'{"id": "msg_123"}'
    mock_response.json.return_value = {"id": "msg_123"}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        result = await client.send_inmail(
            profile_url="https://www.linkedin.com/in/johndoe",
            subject="Partnership",
            body="Hello John, let's connect.",
        )

    assert result is not None
    assert result["message_id"] == "msg_123"
    assert result["status"] == "sent"

    # Verify rate counter was incremented.
    valkey.pipeline.assert_called_once()

    await client.close()


@pytest.mark.asyncio
async def test_send_inmail_success_no_valkey():
    """Test send_inmail succeeds without Valkey (rate limiting skipped)."""
    client = _make_client(access_token="valid_token", valkey=None)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b'{"id": "msg_456"}'
    mock_response.json.return_value = {"id": "msg_456"}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        result = await client.send_inmail(
            profile_url="https://www.linkedin.com/in/janedoe",
            subject="Opportunity",
            body="Hi Jane!",
        )

    assert result is not None
    assert result["status"] == "sent"

    await client.close()


# ============================================================================
# OAuth token refresh
# ============================================================================


@pytest.mark.asyncio
async def test_refresh_token_flow():
    """Test OAuth token refresh via LinkedIn endpoint."""
    client = _make_client(
        access_token="old_token",
        refresh_token="refresh_abc",
    )

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "access_token": "new_token_xyz",
        "refresh_token": "new_refresh_abc",
        "expires_in": 3600,
    }
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        new_token = await client._refresh_token_flow()

    assert new_token == "new_token_xyz"
    assert client._access_token == "new_token_xyz"
    assert client._refresh_token == "new_refresh_abc"
    assert client._token_expires_at > time.time()
    # Encrypted tokens should be updated for persistence.
    assert client.encrypted_tokens is not None

    await client.close()


@pytest.mark.asyncio
async def test_refresh_token_no_refresh_token_raises():
    """Test _refresh_token_flow raises when no refresh token is available."""
    client = _make_client(access_token="token", refresh_token="")

    with pytest.raises(PlatformAPIError, match="No refresh token"):
        await client._refresh_token_flow()

    await client.close()


@pytest.mark.asyncio
async def test_refresh_token_no_credentials_raises():
    """Test _refresh_token_flow raises when client_id/secret are missing."""
    client = _make_client(
        access_token="token",
        refresh_token="refresh",
        client_id="",
        client_secret="",
    )

    with pytest.raises(PlatformAPIError, match="client_id and client_secret"):
        await client._refresh_token_flow()

    await client.close()


@pytest.mark.asyncio
async def test_refresh_token_http_failure_raises():
    """Test _refresh_token_flow raises PlatformAPIError on HTTP failure."""
    client = _make_client(
        access_token="token",
        refresh_token="refresh",
    )

    with patch(
        "httpx.AsyncClient.post",
        new_callable=AsyncMock,
        side_effect=httpx.ConnectError("Connection refused"),
    ):
        with pytest.raises(PlatformAPIError, match="token refresh failed"):
            await client._refresh_token_flow()

    await client.close()


# ============================================================================
# Token expiry and auto-refresh
# ============================================================================


@pytest.mark.asyncio
async def test_ensure_valid_token_not_expired():
    """Test _ensure_valid_token returns current token when not expired."""
    client = _make_client(
        access_token="current_token",
        expires_at=time.time() + 3600,
    )

    token = await client._ensure_valid_token()
    assert token == "current_token"

    await client.close()


@pytest.mark.asyncio
async def test_ensure_valid_token_expired_triggers_refresh():
    """Test _ensure_valid_token refreshes when token is expired."""
    client = _make_client(
        access_token="expired_token",
        refresh_token="refresh_abc",
        expires_at=time.time() - 100,  # Expired 100s ago.
    )

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "access_token": "fresh_token",
        "expires_in": 3600,
    }
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        token = await client._ensure_valid_token()

    assert token == "fresh_token"

    await client.close()


@pytest.mark.asyncio
async def test_ensure_valid_token_no_token_raises():
    """Test _ensure_valid_token raises when no access token exists."""
    client = LinkedInClient()

    with pytest.raises(PlatformAPIError, match="not configured"):
        await client._ensure_valid_token()

    await client.close()


# ============================================================================
# Rate limit enforcement
# ============================================================================


@pytest.mark.asyncio
async def test_rate_limit_daily_exceeded():
    """Test send_inmail raises PlatformRateLimitError when daily limit hit."""
    valkey = _mock_valkey(daily=LINKEDIN_RATE_LIMIT_DAILY)
    client = _make_client(access_token="token", valkey=valkey)

    with pytest.raises(PlatformRateLimitError, match="daily"):
        await client.send_inmail(
            profile_url="https://www.linkedin.com/in/johndoe",
            subject="Test",
            body="Test body",
        )

    await client.close()


@pytest.mark.asyncio
async def test_rate_limit_monthly_exceeded():
    """Test send_inmail raises PlatformRateLimitError when monthly limit hit."""
    valkey = _mock_valkey(daily=5, monthly=LINKEDIN_RATE_LIMIT_MONTHLY)
    client = _make_client(access_token="token", valkey=valkey)

    with pytest.raises(PlatformRateLimitError, match="monthly"):
        await client.send_inmail(
            profile_url="https://www.linkedin.com/in/johndoe",
            subject="Test",
            body="Test body",
        )

    await client.close()


@pytest.mark.asyncio
async def test_rate_limit_valkey_unavailable_fails_open():
    """Test that Valkey connection failure allows the send (fail-open)."""
    valkey = AsyncMock()
    valkey.get = AsyncMock(side_effect=ConnectionError("Valkey down"))
    client = _make_client(access_token="token", valkey=valkey)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b'{"id": "msg_789"}'
    mock_response.json.return_value = {"id": "msg_789"}

    # Increment should also fail silently.
    pipe = AsyncMock()
    pipe.incr = MagicMock(return_value=pipe)
    pipe.expire = MagicMock(return_value=pipe)
    pipe.execute = AsyncMock(side_effect=ConnectionError("Valkey down"))
    valkey.pipeline = MagicMock(return_value=pipe)

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        result = await client.send_inmail(
            profile_url="https://www.linkedin.com/in/johndoe",
            subject="Test",
            body="Test body",
        )

    assert result is not None
    assert result["status"] == "sent"

    await client.close()


# ============================================================================
# Profile URN extraction
# ============================================================================


def test_extract_profile_urn_standard():
    """Test URN extraction from standard LinkedIn URL."""
    urn = LinkedInClient._extract_profile_urn("https://www.linkedin.com/in/johndoe")
    assert urn == "urn:li:person:johndoe"


def test_extract_profile_urn_trailing_slash():
    """Test URN extraction with trailing slash."""
    urn = LinkedInClient._extract_profile_urn("https://www.linkedin.com/in/johndoe/")
    assert urn == "urn:li:person:johndoe"


def test_extract_profile_urn_no_www():
    """Test URN extraction without www prefix."""
    urn = LinkedInClient._extract_profile_urn("https://linkedin.com/in/johndoe")
    assert urn == "urn:li:person:johndoe"


def test_extract_profile_urn_http():
    """Test URN extraction with http (not https)."""
    urn = LinkedInClient._extract_profile_urn("http://www.linkedin.com/in/johndoe")
    assert urn == "urn:li:person:johndoe"


def test_extract_profile_urn_hyphenated_name():
    """Test URN extraction with hyphenated vanity name."""
    urn = LinkedInClient._extract_profile_urn("https://www.linkedin.com/in/john-doe-123")
    assert urn == "urn:li:person:john-doe-123"


def test_extract_profile_urn_invalid_url():
    """Test URN extraction raises ValueError for invalid URL."""
    with pytest.raises(ValueError, match="Invalid LinkedIn profile URL"):
        LinkedInClient._extract_profile_urn("https://example.com/not-linkedin")


def test_extract_profile_urn_empty():
    """Test URN extraction raises ValueError for empty string."""
    with pytest.raises(ValueError, match="Invalid LinkedIn profile URL"):
        LinkedInClient._extract_profile_urn("")


# ============================================================================
# HTTP 429 handling
# ============================================================================


@pytest.mark.asyncio
async def test_send_inmail_429_raises_rate_limit_error():
    """Test send_inmail raises PlatformRateLimitError on HTTP 429."""
    client = _make_client(access_token="token")

    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_response.content = b""

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        with pytest.raises(PlatformRateLimitError, match="HTTP 429"):
            await client.send_inmail(
                profile_url="https://www.linkedin.com/in/johndoe",
                subject="Test",
                body="Test body",
            )

    await client.close()


# ============================================================================
# HTTP 403 handling (token expired + refresh)
# ============================================================================


@pytest.mark.asyncio
async def test_send_inmail_403_triggers_refresh_and_raises():
    """Test send_inmail refreshes token on 403 then raises PlatformAPIError."""
    client = _make_client(
        access_token="expired_token",
        refresh_token="refresh_abc",
    )

    mock_403_response = MagicMock()
    mock_403_response.status_code = 403
    mock_403_response.content = b""

    mock_refresh_response = MagicMock()
    mock_refresh_response.status_code = 200
    mock_refresh_response.json.return_value = {
        "access_token": "new_token",
        "expires_in": 3600,
    }
    mock_refresh_response.raise_for_status = MagicMock()

    call_count = 0

    async def _post_side_effect(url: str, **kwargs: object) -> MagicMock:
        nonlocal call_count
        call_count += 1
        if "accessToken" in url:
            return mock_refresh_response
        return mock_403_response

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = _post_side_effect

        with pytest.raises(PlatformAPIError, match="403 Forbidden"):
            await client.send_inmail(
                profile_url="https://www.linkedin.com/in/johndoe",
                subject="Test",
                body="Test body",
            )

    # Token should have been refreshed before raising.
    assert client._access_token == "new_token"

    await client.close()


@pytest.mark.asyncio
async def test_send_inmail_403_no_refresh_token():
    """Test send_inmail on 403 without refresh token still raises."""
    client = _make_client(access_token="token", refresh_token="")

    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.content = b""

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        with pytest.raises(PlatformAPIError, match="403 Forbidden"):
            await client.send_inmail(
                profile_url="https://www.linkedin.com/in/johndoe",
                subject="Test",
                body="Test body",
            )

    await client.close()


# ============================================================================
# Connection status check
# ============================================================================


@pytest.mark.asyncio
async def test_check_connection_status_success():
    """Test connection status check returns exists=True."""
    client = _make_client(access_token="token")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"id": "urn:li:person:abc123"}
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        result = await client.check_connection_status(
            "https://www.linkedin.com/in/johndoe",
        )

    assert result is not None
    assert result["exists"] is True
    assert result["profile_url"] == "https://www.linkedin.com/in/johndoe"

    await client.close()


@pytest.mark.asyncio
async def test_check_connection_status_not_found():
    """Test connection status check returns exists=False for 404."""
    client = _make_client(access_token="token")

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


# ============================================================================
# Input validation
# ============================================================================


@pytest.mark.asyncio
async def test_send_inmail_not_configured():
    """Test send_inmail returns None when not configured."""
    client = LinkedInClient()
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
    client = _make_client(access_token="token")
    result = await client.send_inmail(profile_url="", subject="Hello", body="Test")
    assert result is None
    await client.close()


@pytest.mark.asyncio
async def test_send_inmail_empty_subject():
    """Test send_inmail returns None for empty subject."""
    client = _make_client(access_token="token")
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
    client = _make_client(access_token="token")
    result = await client.send_inmail(
        profile_url="https://www.linkedin.com/in/johndoe",
        subject="Hello",
        body="",
    )
    assert result is None
    await client.close()


@pytest.mark.asyncio
async def test_send_inmail_invalid_profile_url():
    """Test send_inmail returns None for unparseable profile URL."""
    client = _make_client(access_token="token")
    result = await client.send_inmail(
        profile_url="https://example.com/not-linkedin",
        subject="Hello",
        body="Test",
    )
    assert result is None
    await client.close()


# ============================================================================
# Backward compatibility (api_key)
# ============================================================================


def test_backward_compat_api_key():
    """Test LinkedInClient works with legacy api_key parameter."""
    client = LinkedInClient(api_key="legacy_token")
    assert client.is_configured is True
    assert client._access_token == "legacy_token"


def test_backward_compat_api_key_none():
    """Test LinkedInClient with None api_key is not configured."""
    client = LinkedInClient(api_key=None)
    assert client.is_configured is False


# ============================================================================
# Encrypted token loading
# ============================================================================


@pytest.mark.asyncio
async def test_encrypted_token_loading():
    """Test LinkedInClient decrypts tokens from Fernet-encrypted string."""
    from src.security.encryption import encrypt_dict

    tokens = encrypt_dict(
        {
            "access_token": "encrypted_access",
            "refresh_token": "encrypted_refresh",
            "expires_at": time.time() + 3600,
        }
    )

    client = LinkedInClient(encrypted_tokens=tokens)
    assert client.is_configured is True
    assert client._access_token == "encrypted_access"
    assert client._refresh_token == "encrypted_refresh"
    assert client._token_expires_at > time.time()

    await client.close()


@pytest.mark.asyncio
async def test_encrypted_token_invalid_graceful():
    """Test LinkedInClient handles invalid encrypted tokens gracefully."""
    client = LinkedInClient(encrypted_tokens="not-a-valid-fernet-token")
    assert client.is_configured is False
    assert client._access_token == ""

    await client.close()


# ============================================================================
# HTTP error handling
# ============================================================================


@pytest.mark.asyncio
async def test_send_inmail_network_error_returns_none():
    """Test send_inmail returns None on network error."""
    client = _make_client(access_token="token")

    with patch(
        "httpx.AsyncClient.post",
        new_callable=AsyncMock,
        side_effect=httpx.ConnectError("Connection refused"),
    ):
        result = await client.send_inmail(
            profile_url="https://www.linkedin.com/in/johndoe",
            subject="Test",
            body="Test body",
        )

    assert result is None

    await client.close()


@pytest.mark.asyncio
async def test_send_inmail_server_error_returns_none():
    """Test send_inmail returns None on 500 server error."""
    client = _make_client(access_token="token")

    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.content = b""

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        result = await client.send_inmail(
            profile_url="https://www.linkedin.com/in/johndoe",
            subject="Test",
            body="Test body",
        )

    assert result is None

    await client.close()


# ============================================================================
# API call structure verification
# ============================================================================


@pytest.mark.asyncio
async def test_send_inmail_correct_api_call():
    """Test send_inmail sends correct request to LinkedIn API."""
    client = _make_client(access_token="bearer_token")

    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_response.content = b'{"id": "msg_verify"}'
    mock_response.json.return_value = {"id": "msg_verify"}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        await client.send_inmail(
            profile_url="https://www.linkedin.com/in/testuser",
            subject="Subject Line",
            body="Message body here",
        )

        call_args = mock_post.call_args
        assert f"{LINKEDIN_API_BASE}/messages" in call_args.args[0]

        headers = call_args.kwargs.get("headers", {})
        assert headers["Authorization"] == "Bearer bearer_token"
        assert headers["X-Restli-Protocol-Version"] == "2.0.0"

        json_body = call_args.kwargs.get("json", {})
        assert json_body["subject"] == "Subject Line"
        assert json_body["body"] == "Message body here"
        recipients = json_body["recipients"]
        assert len(recipients) == 1
        assert recipients[0]["person"]["profileUrn"] == "urn:li:person:testuser"

    await client.close()
