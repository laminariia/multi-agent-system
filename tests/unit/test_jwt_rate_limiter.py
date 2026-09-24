"""Tests for per-user JWT rate limiting middleware.

Covers:
- JWT token extraction and user_id parsing
- Valkey INCR+EXPIRE per-user sliding window
- 300/min general, 60/min auth endpoint limits
- Fallback to IP-based when no JWT present
- 429 response with Retry-After header
- Exclude paths bypass rate limiting
- Expired/invalid JWT falls back to IP
- Non-HTTP scopes are passed through
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.api.middleware.jwt_rate_limiter import (
    AUTH_PATHS,
    AUTH_RATE_LIMIT,
    DEFAULT_RATE_LIMIT,
    EXCLUDE_PATHS,
    WINDOW_SECONDS,
    JWTRateLimiterMiddleware,
    _decode_jwt_sub,
    _extract_bearer_token,
    _get_client_ip,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_scope(
    path: str = "/api/v1/jobs",
    headers: list[tuple[bytes, bytes]] | None = None,
    client: tuple[str, int] | None = ("10.0.0.1", 12345),
    scope_type: str = "http",
) -> dict[str, Any]:
    """Build a minimal ASGI scope dict for testing."""
    return {
        "type": scope_type,
        "path": path,
        "headers": headers or [],
        "client": client,
    }


def _bearer_header(token: str) -> list[tuple[bytes, bytes]]:
    """Create an Authorization: Bearer header."""
    return [(b"authorization", f"Bearer {token}".encode())]


def _make_jwt_payload(sub: str = "user-123", exp: int | None = None) -> str:
    """Create a minimal JWT-like token (base64-encoded payload section).

    This creates a token whose middle segment is a valid base64-encoded JSON
    payload with the given sub claim. It is NOT cryptographically signed --
    the middleware only reads sub, it does not verify the signature (that is
    done by Litestar's JWTAuth layer).
    """
    import base64

    payload = {"sub": sub}
    if exp is not None:
        payload["exp"] = exp
    payload_bytes = json.dumps(payload).encode()
    # JWT uses base64url without padding
    b64 = base64.urlsafe_b64encode(payload_bytes).rstrip(b"=").decode()
    # header.payload.signature
    return f"eyJhbGciOiJIUzI1NiJ9.{b64}.fakesig"


# ---------------------------------------------------------------------------
# _extract_bearer_token
# ---------------------------------------------------------------------------


class TestExtractBearerToken:
    """Test extraction of bearer token from ASGI scope headers."""

    def test_valid_bearer(self) -> None:
        scope = _make_scope(headers=_bearer_header("abc123"))
        assert _extract_bearer_token(scope) == "abc123"

    def test_no_auth_header(self) -> None:
        scope = _make_scope(headers=[])
        assert _extract_bearer_token(scope) is None

    def test_non_bearer_scheme(self) -> None:
        scope = _make_scope(headers=[(b"authorization", b"Basic abc123")])
        assert _extract_bearer_token(scope) is None

    def test_bearer_case_insensitive(self) -> None:
        scope = _make_scope(headers=[(b"authorization", b"bearer tok")])
        assert _extract_bearer_token(scope) == "tok"

    def test_empty_bearer(self) -> None:
        scope = _make_scope(headers=[(b"authorization", b"Bearer ")])
        assert _extract_bearer_token(scope) is None


# ---------------------------------------------------------------------------
# _decode_jwt_sub
# ---------------------------------------------------------------------------


class TestDecodeJwtSub:
    """Test JWT sub claim extraction (without signature verification)."""

    def test_valid_token(self) -> None:
        token = _make_jwt_payload(sub="user-abc")
        assert _decode_jwt_sub(token) == "user-abc"

    def test_missing_sub(self) -> None:
        import base64

        payload = json.dumps({"email": "test@example.com"}).encode()
        b64 = base64.urlsafe_b64encode(payload).rstrip(b"=").decode()
        token = f"header.{b64}.sig"
        assert _decode_jwt_sub(token) is None

    def test_invalid_base64(self) -> None:
        token = "header.!!!invalid!!!.sig"
        assert _decode_jwt_sub(token) is None

    def test_invalid_json(self) -> None:
        import base64

        bad = base64.urlsafe_b64encode(b"not json").rstrip(b"=").decode()
        token = f"header.{bad}.sig"
        assert _decode_jwt_sub(token) is None

    def test_malformed_token_no_dots(self) -> None:
        assert _decode_jwt_sub("nodots") is None

    def test_expired_token_still_returns_sub(self) -> None:
        """Middleware reads sub for rate-limiting key; signature/expiry is JWTAuth's job."""
        token = _make_jwt_payload(sub="user-expired", exp=1)
        assert _decode_jwt_sub(token) == "user-expired"


# ---------------------------------------------------------------------------
# _get_client_ip
# ---------------------------------------------------------------------------


class TestGetClientIp:
    """Test IP extraction from ASGI scope."""

    def test_from_client(self) -> None:
        scope = _make_scope(client=("192.168.1.1", 8080))
        assert _get_client_ip(scope) == "192.168.1.1"

    def test_no_client(self) -> None:
        scope = _make_scope(client=None)
        assert _get_client_ip(scope) == "unknown"

    def test_x_forwarded_for(self) -> None:
        scope = _make_scope(
            headers=[(b"x-forwarded-for", b"203.0.113.5, 10.0.0.1")],
            client=("10.0.0.1", 80),
        )
        assert _get_client_ip(scope) == "203.0.113.5"


# ---------------------------------------------------------------------------
# JWTRateLimiterMiddleware
# ---------------------------------------------------------------------------


class TestJWTRateLimiterMiddleware:
    """Test the ASGI middleware integration."""

    @pytest.fixture()
    def valkey(self) -> AsyncMock:
        """Mock Valkey client."""
        mock = AsyncMock()
        mock.incr = AsyncMock(return_value=1)
        mock.expire = AsyncMock()
        return mock

    @pytest.fixture()
    def middleware(self, valkey: AsyncMock) -> JWTRateLimiterMiddleware:
        """Create middleware with mock Valkey and a dummy ASGI app."""
        app = AsyncMock()
        return JWTRateLimiterMiddleware(app=app, valkey=valkey)

    @pytest.mark.asyncio()
    async def test_non_http_passthrough(self, middleware: JWTRateLimiterMiddleware) -> None:
        """Non-HTTP scopes (WebSocket) pass through without rate limiting."""
        scope = _make_scope(scope_type="websocket")
        receive = AsyncMock()
        send = AsyncMock()

        await middleware(scope, receive, send)

        middleware.app.assert_awaited_once_with(scope, receive, send)

    @pytest.mark.asyncio()
    async def test_excluded_path_passthrough(self, middleware: JWTRateLimiterMiddleware) -> None:
        """Excluded paths bypass rate limiting."""
        scope = _make_scope(path="/health")
        receive = AsyncMock()
        send = AsyncMock()

        await middleware(scope, receive, send)

        middleware.app.assert_awaited_once_with(scope, receive, send)
        middleware._valkey.incr.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_authenticated_user_uses_jwt_key(
        self,
        middleware: JWTRateLimiterMiddleware,
        valkey: AsyncMock,
    ) -> None:
        """Authenticated requests use user_id from JWT for the rate limit key."""
        token = _make_jwt_payload(sub="user-42")
        scope = _make_scope(headers=_bearer_header(token))
        receive = AsyncMock()
        send = AsyncMock()

        await middleware(scope, receive, send)

        # Verify Valkey was called with a user-based key
        call_args = valkey.incr.call_args[0][0]
        assert "jwt:user-42" in call_args
        middleware.app.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_unauthenticated_uses_ip_key(
        self,
        middleware: JWTRateLimiterMiddleware,
        valkey: AsyncMock,
    ) -> None:
        """Unauthenticated requests fall back to IP-based rate limiting."""
        scope = _make_scope(headers=[], client=("1.2.3.4", 80))
        receive = AsyncMock()
        send = AsyncMock()

        await middleware(scope, receive, send)

        call_args = valkey.incr.call_args[0][0]
        assert "ip:1.2.3.4" in call_args
        middleware.app.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_general_rate_limit_300(
        self,
        middleware: JWTRateLimiterMiddleware,
        valkey: AsyncMock,
    ) -> None:
        """General endpoints allow up to 300 requests per minute."""
        valkey.incr.return_value = 300  # at the limit
        scope = _make_scope(path="/api/v1/jobs")
        receive = AsyncMock()
        send = AsyncMock()

        await middleware(scope, receive, send)

        middleware.app.assert_awaited_once()  # still allowed

    @pytest.mark.asyncio()
    async def test_general_rate_limit_exceeded(
        self,
        middleware: JWTRateLimiterMiddleware,
        valkey: AsyncMock,
    ) -> None:
        """Exceeding 300/min on general endpoints returns 429."""
        valkey.incr.return_value = 301
        scope = _make_scope(path="/api/v1/jobs")
        receive = AsyncMock()
        send = AsyncMock()

        await middleware(scope, receive, send)

        # Should NOT call the app
        middleware.app.assert_not_awaited()
        # Should send 429 response
        _assert_429_sent(send)

    @pytest.mark.asyncio()
    async def test_auth_rate_limit_60(
        self,
        middleware: JWTRateLimiterMiddleware,
        valkey: AsyncMock,
    ) -> None:
        """Auth endpoints allow up to 60 requests per minute."""
        valkey.incr.return_value = 60  # at the limit
        scope = _make_scope(path="/api/v1/auth/login")
        receive = AsyncMock()
        send = AsyncMock()

        await middleware(scope, receive, send)

        middleware.app.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_auth_rate_limit_exceeded(
        self,
        middleware: JWTRateLimiterMiddleware,
        valkey: AsyncMock,
    ) -> None:
        """Exceeding 60/min on auth endpoints returns 429."""
        valkey.incr.return_value = 61
        scope = _make_scope(path="/api/v1/auth/login")
        receive = AsyncMock()
        send = AsyncMock()

        await middleware(scope, receive, send)

        middleware.app.assert_not_awaited()
        _assert_429_sent(send)

    @pytest.mark.asyncio()
    async def test_first_request_sets_expire(
        self,
        middleware: JWTRateLimiterMiddleware,
        valkey: AsyncMock,
    ) -> None:
        """First request in a window sets TTL via EXPIRE."""
        valkey.incr.return_value = 1
        scope = _make_scope()
        receive = AsyncMock()
        send = AsyncMock()

        await middleware(scope, receive, send)

        valkey.expire.assert_awaited_once()
        # TTL should be WINDOW_SECONDS
        expire_ttl = valkey.expire.call_args[0][1]
        assert expire_ttl == WINDOW_SECONDS

    @pytest.mark.asyncio()
    async def test_subsequent_requests_skip_expire(
        self,
        middleware: JWTRateLimiterMiddleware,
        valkey: AsyncMock,
    ) -> None:
        """Subsequent requests (count > 1) skip EXPIRE to avoid TTL extension."""
        valkey.incr.return_value = 5
        scope = _make_scope()
        receive = AsyncMock()
        send = AsyncMock()

        await middleware(scope, receive, send)

        valkey.expire.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_invalid_jwt_falls_back_to_ip(
        self,
        middleware: JWTRateLimiterMiddleware,
        valkey: AsyncMock,
    ) -> None:
        """Invalid JWT token falls back to IP-based rate limiting."""
        scope = _make_scope(
            headers=_bearer_header("totally.invalid.token"),
            client=("5.6.7.8", 80),
        )
        receive = AsyncMock()
        send = AsyncMock()

        await middleware(scope, receive, send)

        call_args = valkey.incr.call_args[0][0]
        assert "ip:5.6.7.8" in call_args

    @pytest.mark.asyncio()
    async def test_valkey_error_allows_request(
        self,
        middleware: JWTRateLimiterMiddleware,
        valkey: AsyncMock,
    ) -> None:
        """If Valkey is unreachable, fail open -- allow the request."""
        valkey.incr.side_effect = ConnectionError("Valkey down")
        scope = _make_scope()
        receive = AsyncMock()
        send = AsyncMock()

        await middleware(scope, receive, send)

        middleware.app.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_429_includes_retry_after_header(
        self,
        middleware: JWTRateLimiterMiddleware,
        valkey: AsyncMock,
    ) -> None:
        """429 response includes Retry-After header."""
        valkey.incr.return_value = 500
        scope = _make_scope()
        receive = AsyncMock()
        send = AsyncMock()

        await middleware(scope, receive, send)

        # Find the http.response.start message
        for call in send.call_args_list:
            msg = call[0][0]
            if msg.get("type") == "http.response.start":
                headers = dict(msg.get("headers", []))
                assert b"retry-after" in headers
                retry_val = int(headers[b"retry-after"])
                assert 0 < retry_val <= WINDOW_SECONDS
                break
        else:
            pytest.fail("No http.response.start message sent")

    @pytest.mark.asyncio()
    async def test_auth_register_uses_auth_limit(
        self,
        middleware: JWTRateLimiterMiddleware,
        valkey: AsyncMock,
    ) -> None:
        """Registration endpoint uses the stricter auth rate limit."""
        valkey.incr.return_value = 61
        scope = _make_scope(path="/api/v1/auth/register")
        receive = AsyncMock()
        send = AsyncMock()

        await middleware(scope, receive, send)

        middleware.app.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_metrics_excluded(self, middleware: JWTRateLimiterMiddleware) -> None:
        """Metrics endpoint is excluded from rate limiting."""
        scope = _make_scope(path="/metrics")
        receive = AsyncMock()
        send = AsyncMock()

        await middleware(scope, receive, send)

        middleware.app.assert_awaited_once()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    """Verify rate-limit constants are correct."""

    def test_default_rate_limit(self) -> None:
        assert DEFAULT_RATE_LIMIT == 300

    def test_auth_rate_limit(self) -> None:
        assert AUTH_RATE_LIMIT == 60

    def test_window_seconds(self) -> None:
        assert WINDOW_SECONDS == 60

    def test_exclude_paths(self) -> None:
        assert "/health" in EXCLUDE_PATHS
        assert "/schema" in EXCLUDE_PATHS
        assert "/metrics" in EXCLUDE_PATHS

    def test_auth_paths(self) -> None:
        assert "/api/v1/auth/" in AUTH_PATHS or any("/api/v1/auth" in p for p in AUTH_PATHS)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _assert_429_sent(send: AsyncMock) -> None:
    """Assert that a 429 Too Many Requests response was sent."""
    start_sent = False
    for call in send.call_args_list:
        msg = call[0][0]
        if msg.get("type") == "http.response.start":
            assert msg["status"] == 429
            start_sent = True
        if msg.get("type") == "http.response.body":
            body = json.loads(msg["body"])
            assert (
                "rate" in body.get("error", {}).get("code", "").lower()
                or "rate" in body.get("detail", "").lower()
                or "rate" in body.get("error", "").lower()
                or "Too Many Requests" in body.get("detail", "")
                or "rate_limit" in json.dumps(body).lower()
            )
    assert start_sent, "No http.response.start with 429 status was sent"
