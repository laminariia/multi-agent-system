"""LinkedIn outreach channel for Pipeline B.

Provides InMail sending via the LinkedIn Marketing API with OAuth 2.0
three-legged flow, and connection status checking.

OAuth tokens are encrypted at rest via Fernet.  Rate limits (20/day,
100/month) are enforced via Valkey counters with automatic TTL expiry.

.. note::

    Full implementation requires LinkedIn Marketing API access (partner
    program) and OAuth 2.0 authorization.  See:
    https://learn.microsoft.com/en-us/linkedin/marketing/

Usage::

    client = LinkedInClient(
        client_id="...",
        client_secret="...",
        encrypted_tokens="<fernet-token>",
    )
    result = await client.send_inmail(
        "https://www.linkedin.com/in/johndoe",
        "Partnership",
        "Hello, let's connect.",
    )
    status = await client.check_connection_status(
        "https://www.linkedin.com/in/johndoe",
    )
    await client.close()
"""

from __future__ import annotations

import re
import time
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog

from src.core.exceptions import PlatformAPIError, PlatformRateLimitError
from src.security.encryption import decrypt_dict, encrypt_dict

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# LinkedIn API configuration
# ---------------------------------------------------------------------------

LINKEDIN_API_BASE = "https://api.linkedin.com/v2"
LINKEDIN_AUTH_URL = "https://www.linkedin.com/oauth/v2"
LINKEDIN_RATE_LIMIT_DAILY = 20
LINKEDIN_RATE_LIMIT_MONTHLY = 100

# Valkey key prefixes for rate limiting.
_VALKEY_PREFIX_DAILY = "linkedin:inmail:daily"
_VALKEY_PREFIX_MONTHLY = "linkedin:inmail:monthly"

# TTLs for rate limit counters.
_DAILY_TTL = 86400  # 24 hours
_MONTHLY_TTL = 2_592_000  # 30 days

# Pattern for extracting vanity name from LinkedIn profile URLs.
_PROFILE_URL_PATTERN = re.compile(
    r"^https?://(?:www\.)?linkedin\.com/in/([a-zA-Z0-9\-_.%]+)/?$",
)


class LinkedInClient:
    """LinkedIn outreach client with OAuth 2.0 and rate limiting.

    Uses OAuth 2.0 Bearer token authentication for API calls.  Tokens
    are stored encrypted via Fernet and refreshed automatically when
    expired.  Rate limits are tracked in Valkey (20/day, 100/month).

    Args:
        client_id: LinkedIn OAuth application client ID.
        client_secret: LinkedIn OAuth application client secret.
        encrypted_tokens: Fernet-encrypted token dict containing
            ``access_token``, ``refresh_token``, and ``expires_at``
            (Unix timestamp).  Pass ``None`` to disable.
        valkey: Optional Valkey/Redis client for rate-limit counters.
            When ``None``, rate limiting is skipped (suitable for tests).
        api_key: Legacy API key for backward compatibility.  If set
            (and no OAuth tokens provided), used as a static Bearer token.
        timeout: HTTP request timeout in seconds.
    """

    def __init__(
        self,
        client_id: str = "",
        client_secret: str = "",
        encrypted_tokens: str | None = None,
        valkey: Any | None = None,
        api_key: str | None = None,
        *,
        timeout: float = 30.0,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._encrypted_tokens = encrypted_tokens
        self._valkey = valkey
        self._timeout = timeout
        self._http: httpx.AsyncClient | None = None

        # Decrypt tokens if provided.
        self._access_token: str = ""
        self._refresh_token: str = ""
        self._token_expires_at: float = 0.0

        if encrypted_tokens:
            try:
                tokens = decrypt_dict(encrypted_tokens)
                self._access_token = tokens.get("access_token", "")
                self._refresh_token = tokens.get("refresh_token", "")
                self._token_expires_at = float(tokens.get("expires_at", 0))
            except Exception:  # noqa: BLE001
                logger.warning("linkedin_token_decrypt_failed")

        # Backward compatibility: use api_key as static Bearer token.
        if not self._access_token and api_key:
            self._access_token = api_key

    @property
    def is_configured(self) -> bool:
        """Return ``True`` if a valid access token is available."""
        return bool(self._access_token)

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazily create (or re-create) the HTTP client."""
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=self._timeout)
        return self._http

    # ------------------------------------------------------------------
    # OAuth token management
    # ------------------------------------------------------------------

    async def _refresh_token_flow(self) -> str:
        """Refresh OAuth access token using the stored refresh_token.

        Posts to the LinkedIn OAuth token endpoint with
        ``grant_type=refresh_token``.  On success, updates the internal
        token state and persists the new tokens encrypted via Fernet.

        Returns:
            The new access token.

        Raises:
            PlatformAPIError: If the refresh request fails.
        """
        if not self._refresh_token:
            raise PlatformAPIError(
                "No refresh token available for LinkedIn OAuth",
                platform="linkedin",
                operation="refresh_token",
            )

        if not self._client_id or not self._client_secret:
            raise PlatformAPIError(
                "LinkedIn client_id and client_secret required for token refresh",
                platform="linkedin",
                operation="refresh_token",
            )

        client = await self._get_client()

        try:
            resp = await client.post(
                f"{LINKEDIN_AUTH_URL}/accessToken",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self._refresh_token,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as exc:
            logger.warning(
                "linkedin_token_refresh_failed",
                error=str(exc),
            )
            raise PlatformAPIError(
                f"LinkedIn token refresh failed: {exc}",
                platform="linkedin",
                operation="refresh_token",
            ) from exc

        self._access_token = data.get("access_token", "")
        new_refresh = data.get("refresh_token", self._refresh_token)
        self._refresh_token = new_refresh
        expires_in = int(data.get("expires_in", 3600))
        self._token_expires_at = time.time() + expires_in

        # Persist encrypted tokens for next startup.
        self._encrypted_tokens = encrypt_dict(
            {
                "access_token": self._access_token,
                "refresh_token": self._refresh_token,
                "expires_at": self._token_expires_at,
            }
        )

        logger.info(
            "linkedin_token_refreshed",
            expires_in=expires_in,
        )

        return self._access_token

    async def _ensure_valid_token(self) -> str:
        """Get a valid access token, refreshing if expired.

        Returns:
            A valid access token string.

        Raises:
            PlatformAPIError: If no token is available or refresh fails.
        """
        if not self._access_token:
            raise PlatformAPIError(
                "LinkedIn access token not configured",
                platform="linkedin",
                operation="ensure_token",
            )

        # Check if token is expired (with 60s buffer for clock skew).
        if self._token_expires_at > 0 and time.time() >= self._token_expires_at - 60 and self._refresh_token:
            return await self._refresh_token_flow()

        return self._access_token

    @property
    def encrypted_tokens(self) -> str | None:
        """Return the current encrypted token string for persistence."""
        return self._encrypted_tokens

    # ------------------------------------------------------------------
    # Rate limiting (Valkey-backed)
    # ------------------------------------------------------------------

    async def _check_rate_limit(self) -> None:
        """Check daily (20) and monthly (100) InMail limits.

        Uses Valkey counters with automatic TTL expiry.

        Raises:
            PlatformRateLimitError: If either limit is exceeded.
        """
        if self._valkey is None:
            return

        today = date.today().isoformat()
        month = datetime.now(tz=UTC).strftime("%Y-%m")

        daily_key = f"{_VALKEY_PREFIX_DAILY}:{today}"
        monthly_key = f"{_VALKEY_PREFIX_MONTHLY}:{month}"

        try:
            daily_count = await self._valkey.get(daily_key)
            monthly_count = await self._valkey.get(monthly_key)

            daily_val = int(daily_count) if daily_count else 0
            monthly_val = int(monthly_count) if monthly_count else 0

            if daily_val >= LINKEDIN_RATE_LIMIT_DAILY:
                raise PlatformRateLimitError(
                    f"LinkedIn daily InMail limit exceeded ({daily_val}/{LINKEDIN_RATE_LIMIT_DAILY})",
                    platform="linkedin",
                    operation="send_inmail",
                )

            if monthly_val >= LINKEDIN_RATE_LIMIT_MONTHLY:
                raise PlatformRateLimitError(
                    f"LinkedIn monthly InMail limit exceeded ({monthly_val}/{LINKEDIN_RATE_LIMIT_MONTHLY})",
                    platform="linkedin",
                    operation="send_inmail",
                )
        except PlatformRateLimitError:
            raise
        except (OSError, ConnectionError):
            # Valkey unavailable -- fail open (allow the send).
            logger.debug("linkedin_rate_limit_check_failed", exc_info=True)

    async def _increment_rate_counter(self) -> None:
        """Increment daily and monthly send counters in Valkey."""
        if self._valkey is None:
            return

        today = date.today().isoformat()
        month = datetime.now(tz=UTC).strftime("%Y-%m")

        daily_key = f"{_VALKEY_PREFIX_DAILY}:{today}"
        monthly_key = f"{_VALKEY_PREFIX_MONTHLY}:{month}"

        try:
            pipe = self._valkey.pipeline()
            pipe.incr(daily_key)
            pipe.expire(daily_key, _DAILY_TTL)
            pipe.incr(monthly_key)
            pipe.expire(monthly_key, _MONTHLY_TTL)
            await pipe.execute()
        except (OSError, ConnectionError):
            logger.debug("linkedin_rate_counter_increment_failed", exc_info=True)

    # ------------------------------------------------------------------
    # Helper: extract profile URN
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_profile_urn(profile_url: str) -> str:
        """Extract a LinkedIn URN from a profile URL.

        Args:
            profile_url: Full LinkedIn profile URL, e.g.
                ``"https://www.linkedin.com/in/johndoe"``.

        Returns:
            A LinkedIn URN string, e.g. ``"urn:li:person:johndoe"``.

        Raises:
            ValueError: If the URL format is not a valid LinkedIn profile URL.
        """
        match = _PROFILE_URL_PATTERN.match(profile_url.strip())
        if match:
            vanity = match.group(1)
            return f"urn:li:person:{vanity}"

        # Fallback: try to parse any URL with /in/ path segment.
        parsed = urlparse(profile_url.strip())
        path_parts = [p for p in parsed.path.split("/") if p]
        if len(path_parts) >= 2 and path_parts[0] == "in":  # noqa: PLR2004
            vanity = path_parts[1]
            return f"urn:li:person:{vanity}"

        raise ValueError(
            f"Invalid LinkedIn profile URL: {profile_url!r}. Expected format: https://www.linkedin.com/in/<vanity-name>"
        )

    # ------------------------------------------------------------------
    # Send InMail
    # ------------------------------------------------------------------

    async def send_inmail(
        self,
        profile_url: str,
        subject: str,
        body: str,
    ) -> dict[str, Any] | None:
        """Send an InMail to a LinkedIn profile via the Marketing API.

        Validates inputs, checks rate limits, ensures a valid OAuth token,
        sends the message, and increments rate counters on success.

        Args:
            profile_url: Full LinkedIn profile URL
                (e.g. ``"https://www.linkedin.com/in/johndoe"``).
            subject: InMail subject line.
            body: InMail message body.

        Returns:
            A dict with ``message_id`` and ``status`` on success, or
            ``None`` if validation fails or client is not configured.

        Raises:
            PlatformRateLimitError: If daily or monthly limit is exceeded.
            PlatformAPIError: If the API call fails with a non-retryable error.
        """
        if not self.is_configured:
            logger.warning("linkedin_not_configured")
            return None

        if not profile_url:
            logger.warning("linkedin_no_profile_url")
            return None

        if not subject:
            logger.warning("linkedin_no_subject")
            return None

        if not body:
            logger.warning("linkedin_no_body")
            return None

        # 1. Check rate limits (daily + monthly).
        await self._check_rate_limit()

        # 2. Extract profile URN from URL.
        try:
            profile_urn = self._extract_profile_urn(profile_url)
        except ValueError as exc:
            logger.warning("linkedin_invalid_profile_url", error=str(exc))
            return None

        # 3. Get valid access token.
        token = await self._ensure_valid_token()

        # 4. Send message via API.
        client = await self._get_client()

        try:
            response = await client.post(
                f"{LINKEDIN_API_BASE}/messages",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "X-Restli-Protocol-Version": "2.0.0",
                },
                json={
                    "recipients": [{"person": {"profileUrn": profile_urn}}],
                    "subject": subject,
                    "body": body,
                },
            )
        except httpx.HTTPError as exc:
            logger.warning(
                "linkedin_inmail_request_failed",
                profile_url=profile_url,
                error=str(exc),
            )
            return None

        # 5. Handle response.
        if response.status_code == 429:  # noqa: PLR2004
            raise PlatformRateLimitError(
                "LinkedIn API rate limit exceeded (HTTP 429)",
                platform="linkedin",
                operation="send_inmail",
            )

        if response.status_code == 403:  # noqa: PLR2004
            # Token may have been revoked or expired mid-request.
            logger.warning(
                "linkedin_inmail_forbidden",
                profile_url=profile_url,
            )
            if self._refresh_token:
                await self._refresh_token_flow()
            raise PlatformAPIError(
                "LinkedIn API returned 403 Forbidden -- token may be expired",
                platform="linkedin",
                operation="send_inmail",
            )

        if response.status_code >= 400:  # noqa: PLR2004
            logger.warning(
                "linkedin_inmail_api_error",
                status=response.status_code,
                profile_url=profile_url,
            )
            return None

        # 6. Increment rate counters on success.
        await self._increment_rate_counter()

        resp_data = response.json() if response.content else {}
        message_id = resp_data.get("id", "")

        logger.info(
            "linkedin_inmail_sent",
            profile_url=profile_url,
            message_id=message_id,
        )

        return {"message_id": message_id, "status": "sent"}

    # ------------------------------------------------------------------
    # Check connection status
    # ------------------------------------------------------------------

    async def check_connection_status(
        self,
        profile_url: str,
    ) -> dict[str, Any] | None:
        """Check whether a LinkedIn profile exists and is accessible.

        Uses the LinkedIn API to verify the profile.  This is a
        lightweight check that does not require Marketing API access.

        Args:
            profile_url: Full LinkedIn profile URL.

        Returns:
            A dict with ``profile_url`` and ``exists`` (bool) on
            success, or ``None`` on failure.
        """
        if not self.is_configured:
            logger.warning("linkedin_not_configured")
            return None

        if not profile_url:
            logger.warning("linkedin_no_profile_url")
            return None

        try:
            client = await self._get_client()
            token = await self._ensure_valid_token()

            # Extract the vanity name from the profile URL for the API call.
            vanity = profile_url.rstrip("/").rsplit("/", 1)[-1]

            resp = await client.get(
                f"{LINKEDIN_API_BASE}/people/(id:{vanity})",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Restli-Protocol-Version": "2.0.0",
                },
            )
            resp.raise_for_status()

            logger.info(
                "linkedin_profile_found",
                profile_url=profile_url,
            )

            return {
                "profile_url": profile_url,
                "exists": True,
            }

        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:  # noqa: PLR2004
                logger.info(
                    "linkedin_profile_not_found",
                    profile_url=profile_url,
                )
                return {
                    "profile_url": profile_url,
                    "exists": False,
                }

            logger.warning(
                "linkedin_api_error",
                status=exc.response.status_code,
                profile_url=profile_url,
            )
            return None

        except httpx.HTTPError as exc:
            logger.warning(
                "linkedin_request_failed",
                profile_url=profile_url,
                error=str(exc),
            )
            return None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._http and not self._http.is_closed:
            await self._http.aclose()
