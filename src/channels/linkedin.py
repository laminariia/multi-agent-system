"""LinkedIn outreach channel for Pipeline B.

Provides InMail sending (stub -- needs OAuth implementation) and
connection status checking via the LinkedIn Marketing API.

.. note::

    ``send_inmail`` is a **stub** that returns a placeholder response.
    Full implementation requires LinkedIn OAuth 2.0 three-legged flow
    and Marketing API approval from LinkedIn.  See:
    https://learn.microsoft.com/en-us/linkedin/marketing/

Usage::

    client = LinkedInClient(api_key="...")
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

from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

LINKEDIN_API_BASE = "https://api.linkedin.com/v2"


class LinkedInClient:
    """LinkedIn outreach client.

    Uses Bearer token (OAuth 2.0) authentication for API calls.
    The ``send_inmail`` method is currently a stub -- it validates
    inputs and returns a placeholder response indicating that full
    OAuth integration is required.

    Args:
        api_key: LinkedIn API access token.  Pass empty string or
            ``None`` to disable.
        timeout: HTTP request timeout in seconds.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        timeout: float = 15.0,
    ) -> None:
        self._api_key = api_key or ""
        self._timeout = timeout
        self._http: httpx.AsyncClient | None = None

    @property
    def is_configured(self) -> bool:
        """Return ``True`` if the API key is set."""
        return bool(self._api_key)

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazily create (or re-create) the HTTP client."""
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=self._timeout)
        return self._http

    # ------------------------------------------------------------------
    # Send InMail (stub)
    # ------------------------------------------------------------------

    async def send_inmail(
        self,
        profile_url: str,
        subject: str,
        body: str,
    ) -> dict[str, Any] | None:
        """Send an InMail to a LinkedIn profile.

        .. warning::

            This is a **stub** implementation.  Actual InMail delivery
            requires:

            1. LinkedIn Marketing API access (partner program).
            2. OAuth 2.0 three-legged flow for user authorization.
            3. ``w_member_social`` and ``r_liteprofile`` scopes.

            The stub validates inputs and returns a placeholder response
            so the rest of the pipeline can be tested end-to-end.

        TODO: Implement real InMail via LinkedIn Marketing API once
        OAuth flow is set up.

        Args:
            profile_url: Full LinkedIn profile URL
                (e.g. ``"https://www.linkedin.com/in/johndoe"``).
            subject: InMail subject line.
            body: InMail message body.

        Returns:
            A dict with stub status on success, or ``None`` if
            validation fails or client is not configured.
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

        # TODO: Replace with real LinkedIn Marketing API call once OAuth
        # is implemented.  The stub logs the intent and returns a
        # placeholder so callers can integrate without the real API.
        logger.info(
            "linkedin_inmail_stub",
            profile_url=profile_url,
            subject=subject,
            body_length=len(body),
        )

        return {
            "status": "stub",
            "message": "InMail sending not yet implemented -- needs OAuth",
            "not_implemented": True,
            "profile_url": profile_url,
            "subject": subject,
            "body": body,
        }

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

            # Extract the vanity name from the profile URL for the API call.
            # e.g. "https://www.linkedin.com/in/johndoe" -> "johndoe"
            vanity = profile_url.rstrip("/").rsplit("/", 1)[-1]

            resp = await client.get(
                f"{LINKEDIN_API_BASE}/people/(id:{vanity})",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
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
            if exc.response.status_code == 404:
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
