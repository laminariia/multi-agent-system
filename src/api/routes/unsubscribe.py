"""Unsubscribe endpoint for email opt-out (L9).

Provides a ``POST /api/v1/unsubscribe/{token}`` endpoint that:
1. Decodes and validates an HMAC-signed unsubscribe token.
2. Adds the email to the suppression list.
3. Returns a JSON confirmation.

Tokens are generated with :func:`generate_unsubscribe_token` and embedded
in the ``List-Unsubscribe`` header of outbound emails.

Security: tokens are HMAC-SHA256 signed with a server-side secret and
include a timestamp for expiration (default 90 days).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Any

import structlog
from litestar import Controller, MediaType, Response, post
from litestar.params import Parameter

logger = structlog.get_logger(__name__)

# Default token TTL: 90 days (long-lived for email unsubscribe links).
_DEFAULT_MAX_AGE = 90 * 86400


# ---------------------------------------------------------------------------
# Token encoding / decoding
# ---------------------------------------------------------------------------


def _encode_token(email: str, *, secret: str, timestamp: int | None = None) -> str:
    """Encode an email into a signed unsubscribe token.

    Format: ``base64(email|timestamp|signature)``
    where signature = HMAC-SHA256(secret, email|timestamp).
    """
    email = email.strip().lower()
    ts = timestamp if timestamp is not None else int(time.time())
    payload = f"{email}|{ts}"
    sig = hmac.new(
        secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    raw = f"{payload}|{sig}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def generate_unsubscribe_token(email: str, *, secret: str) -> str:
    """Generate an unsubscribe token for the given email.

    Args:
        email: Recipient email address.
        secret: Server-side HMAC secret.

    Returns:
        A URL-safe base64-encoded token string.
    """
    return _encode_token(email, secret=secret)


def validate_unsubscribe_token(
    token: str,
    *,
    secret: str,
    max_age: int = _DEFAULT_MAX_AGE,
) -> str | None:
    """Validate an unsubscribe token and return the email if valid.

    Returns ``None`` if the token is invalid, tampered, or expired.
    """
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
    except Exception:
        return None

    parts = raw.split("|")
    if len(parts) != 3:
        return None

    email, ts_str, sig = parts

    try:
        ts = int(ts_str)
    except ValueError:
        return None

    # Verify signature
    expected_payload = f"{email}|{ts_str}"
    expected_sig = hmac.new(
        secret.encode("utf-8"),
        expected_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(sig, expected_sig):
        return None

    # Check expiration
    if time.time() - ts > max_age:
        return None

    return email


def build_unsubscribe_url(
    email: str,
    *,
    base_url: str,
    secret: str,
) -> str:
    """Build a full unsubscribe URL for embedding in emails.

    Args:
        email: Recipient email address.
        base_url: API base URL (e.g. ``https://api.example.com``).
        secret: Server-side HMAC secret.

    Returns:
        Full URL like ``https://api.example.com/api/v1/unsubscribe/<token>``.
    """
    token = generate_unsubscribe_token(email, secret=secret)
    return f"{base_url.rstrip('/')}/api/v1/unsubscribe/{token}"


# ---------------------------------------------------------------------------
# Suppression helper
# ---------------------------------------------------------------------------


def _get_unsubscribe_secret() -> str:
    """Load the unsubscribe HMAC secret from settings."""
    try:
        from src.core.config import get_settings  # noqa: PLC0415

        settings = get_settings()
        secret = getattr(settings, "UNSUBSCRIBE_SECRET", "") or getattr(settings, "SECRET_KEY", "")
        return secret or "mas-default-unsubscribe-secret"
    except Exception:
        return "mas-default-unsubscribe-secret"


async def _suppress_email(email: str) -> bool:
    """Add an email to the suppression list.

    Returns ``True`` on success, ``False`` on error.
    """
    try:
        from src.core.database import get_db_session  # noqa: PLC0415
        from src.enrichment.suppression import SuppressionList  # noqa: PLC0415

        async with get_db_session() as session:
            sl = SuppressionList(session)
            await sl.suppress(
                email,
                reason="unsubscribe",
                source="unsubscribe_link",
            )
            await session.commit()

        logger.info("unsubscribe.email_suppressed", email=email)
        return True

    except Exception:
        logger.error("unsubscribe.suppression_failed", email=email, exc_info=True)
        return False


async def handle_unsubscribe(token: str) -> dict[str, Any]:
    """Core handler logic for unsubscribe requests.

    Returns a dict with ``success``, ``email``, and optionally ``error``.
    """
    secret = _get_unsubscribe_secret()
    email = validate_unsubscribe_token(token, secret=secret)

    if email is None:
        return {
            "success": False,
            "error": "Invalid or expired unsubscribe token.",
        }

    suppressed = await _suppress_email(email)

    if not suppressed:
        return {
            "success": False,
            "email": email,
            "error": "Failed to process unsubscribe request. Please try again.",
        }

    return {
        "success": True,
        "email": email,
        "message": f"You have been successfully unsubscribed ({email}).",
    }


# ---------------------------------------------------------------------------
# Litestar Controller
# ---------------------------------------------------------------------------


class UnsubscribeController(Controller):
    """Handles email unsubscribe requests."""

    path = "/api/v1/unsubscribe"

    @post(
        "/{token:str}",
        opt={"exclude_from_auth": True},
        media_type=MediaType.JSON,
        status_code=200,
    )
    async def unsubscribe(
        self,
        token: str = Parameter(description="Signed unsubscribe token"),
    ) -> Response[dict[str, Any]]:
        """Process an unsubscribe request.

        Decodes the token, validates the signature and expiration,
        adds the email to the suppression list, and returns a
        confirmation response.
        """
        result = await handle_unsubscribe(token)

        if result["success"]:
            return Response(
                content=result,
                status_code=200,
                media_type=MediaType.JSON,
            )

        return Response(
            content=result,
            status_code=400,
            media_type=MediaType.JSON,
        )
