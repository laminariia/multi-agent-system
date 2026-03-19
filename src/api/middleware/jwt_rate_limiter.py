"""Per-user JWT rate limiting middleware.

Solves the Railway reverse proxy problem where all client IPs are collapsed
into a single internal address (e.g. ``100.64.0.3``), making IP-based rate
limiting effectively global.

This middleware extracts the ``sub`` claim from the JWT bearer token and
uses it as the Valkey rate-limit key. When no JWT is present (unauthenticated
requests), it falls back to IP-based limiting.

Rate limits:
- General endpoints: 300 requests/minute per user (or IP)
- Auth endpoints:     60 requests/minute per user (or IP)

Usage::

    from src.api.middleware.jwt_rate_limiter import JWTRateLimiterMiddleware
    from src.core.database import get_valkey

    app = Litestar(
        middleware=[JWTRateLimiterMiddleware.create(valkey=get_valkey())],
        ...
    )
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_RATE_LIMIT: int = 300  # requests per minute for general endpoints
AUTH_RATE_LIMIT: int = 60  # requests per minute for auth endpoints
WINDOW_SECONDS: int = 60  # sliding window duration

EXCLUDE_PATHS: frozenset[str] = frozenset(
    {
        "/health",
        "/schema",
        "/swagger",
        "/redoc",
        "/metrics",
    }
)

AUTH_PATHS: frozenset[str] = frozenset(
    {
        "/api/v1/auth/login",
        "/api/v1/auth/register",
        "/api/v1/auth/refresh",
    }
)


# ---------------------------------------------------------------------------
# Token / IP helpers
# ---------------------------------------------------------------------------


def _extract_bearer_token(scope: dict[str, Any]) -> str | None:
    """Extract the bearer token from the ASGI scope Authorization header.

    Returns ``None`` if no valid bearer token is found.
    """
    headers: list[tuple[bytes, bytes]] = scope.get("headers", [])
    for name, value in headers:
        if name.lower() == b"authorization":
            decoded = value.decode("utf-8", errors="replace")
            if decoded.lower().startswith("bearer "):
                token = decoded[7:].strip()
                return token if token else None
    return None


def _decode_jwt_sub(token: str) -> str | None:
    """Extract the ``sub`` claim from a JWT token without verifying the signature.

    The middleware only needs the user identifier for rate-limit keying.
    Signature verification is handled by Litestar's ``JWTAuth`` layer.

    Returns ``None`` on any parsing error.
    """
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None

        # JWT payload is the second segment, base64url-encoded
        payload_b64 = parts[1]
        # Add padding if needed
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding

        payload_bytes = base64.urlsafe_b64decode(payload_b64)
        payload = json.loads(payload_bytes)

        sub = payload.get("sub")
        return str(sub) if sub is not None else None
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError, Exception):
        return None


def _get_client_ip(scope: dict[str, Any]) -> str:
    """Extract the client IP address from the ASGI scope.

    Checks ``X-Forwarded-For`` first (for reverse proxy setups), then
    falls back to the ASGI ``client`` tuple.
    """
    # Check X-Forwarded-For header
    headers: list[tuple[bytes, bytes]] = scope.get("headers", [])
    for name, value in headers:
        if name.lower() == b"x-forwarded-for":
            # Take the first (leftmost) IP -- the original client
            ips = value.decode("utf-8", errors="replace").split(",")
            first_ip = ips[0].strip()
            if first_ip:
                return first_ip

    # Fallback to ASGI client
    client = scope.get("client")
    if client and len(client) >= 1:
        return str(client[0])

    return "unknown"


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------


class JWTRateLimiterMiddleware:
    """ASGI middleware for per-user JWT-based rate limiting via Valkey.

    Args:
        app: The next ASGI application in the middleware chain.
        valkey: Async Valkey/Redis client instance.
    """

    def __init__(self, app: Any, *, valkey: Any) -> None:
        self.app = app
        self._valkey = valkey

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Any,
        send: Any,
    ) -> None:
        """Process an ASGI request with rate limiting."""
        # Only rate-limit HTTP requests
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")

        # Skip excluded paths
        if any(path.startswith(excluded) for excluded in EXCLUDE_PATHS):
            await self.app(scope, receive, send)
            return

        # Determine rate limit based on path
        limit = AUTH_RATE_LIMIT if path in AUTH_PATHS else DEFAULT_RATE_LIMIT

        # Build the rate-limit key: prefer JWT sub, fall back to IP
        identity = self._resolve_identity(scope)
        window_key = self._build_key(identity, path)

        # Check rate limit via Valkey
        try:
            count = await self._valkey.incr(window_key)

            # Set TTL on first request in window
            if count == 1:
                await self._valkey.expire(window_key, WINDOW_SECONDS)

            if count > limit:
                logger.warning(
                    "rate_limit.exceeded",
                    identity=identity,
                    path=path,
                    count=count,
                    limit=limit,
                )
                await self._send_429(send)
                return

        except Exception:
            # Fail open: if Valkey is unavailable, allow the request through.
            # Security trade-off: brief rate-limit bypass is preferable to
            # a complete service outage.
            logger.warning("rate_limit.valkey_error", exc_info=True)

        await self.app(scope, receive, send)

    def _resolve_identity(self, scope: dict[str, Any]) -> str:
        """Determine the rate-limit identity: JWT user_id or client IP."""
        token = _extract_bearer_token(scope)
        if token:
            sub = _decode_jwt_sub(token)
            if sub:
                return f"jwt:{sub}"

        ip = _get_client_ip(scope)
        return f"ip:{ip}"

    def _build_key(self, identity: str, path: str) -> str:
        """Build a Valkey key scoped to identity and current time window."""
        # Use minute-level granularity for the sliding window
        minute_stamp = int(time.time()) // WINDOW_SECONDS
        # Separate auth and general buckets
        bucket = "auth" if path in AUTH_PATHS else "general"
        return f"rate_limit:{bucket}:{identity}:{minute_stamp}"

    @staticmethod
    async def _send_429(send: Any) -> None:
        """Send a 429 Too Many Requests response."""
        body = json.dumps(
            {
                "error": {
                    "code": "RATE_LIMIT_EXCEEDED",
                    "message": "Too Many Requests. Please slow down.",
                    "details": {},
                },
            }
        ).encode()

        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"retry-after", str(WINDOW_SECONDS).encode()),
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": body,
            }
        )
