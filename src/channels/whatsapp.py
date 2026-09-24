"""WhatsApp Business API channel for outreach messaging.

Sends template and text messages via the WhatsApp Cloud API (Meta Graph API).
Supports delivery status checks.

Rate limits are enforced by the WhatsApp Business API itself (tier-based),
but the client returns ``None`` on any failure for graceful degradation.

Pricing: ~$0.05 per conversation (template-initiated).

Usage::

    client = WhatsAppClient(api_key="...", phone_number_id="123456")
    result = await client.send_message("+1234567890", "hello_world", {"1": "John"})
    status = await client.check_delivery_status(result["message_id"])
    await client.close()
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

WHATSAPP_API_BASE = "https://graph.facebook.com/v19.0"


class WhatsAppClient:
    """WhatsApp Business Cloud API client.

    Uses Bearer token authentication (Meta Graph API access token).

    Args:
        api_key: WhatsApp Business API access token (from Meta).
            Pass empty string or ``None`` to disable.
        phone_number_id: The WhatsApp Business phone number ID
            (from Meta Business Suite).
        timeout: HTTP request timeout in seconds.
    """

    def __init__(
        self,
        api_key: str | None = None,
        phone_number_id: str | None = None,
        *,
        timeout: float = 15.0,
    ) -> None:
        self._api_key = api_key or ""
        self._phone_number_id = phone_number_id or ""
        self._timeout = timeout
        self._http: httpx.AsyncClient | None = None

    @property
    def is_configured(self) -> bool:
        """Return ``True`` if API key and phone number ID are set."""
        return bool(self._api_key) and bool(self._phone_number_id)

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazily create (or re-create) the HTTP client."""
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=self._timeout)
        return self._http

    # ------------------------------------------------------------------
    # Send message
    # ------------------------------------------------------------------

    async def send_message(
        self,
        phone: str,
        template: str | None = None,
        params: dict[str, str] | None = None,
        *,
        text_body: str | None = None,
        language_code: str = "en_US",
    ) -> dict[str, Any] | None:
        """Send a message via WhatsApp Business API.

        Supports two modes:

        - **Template message** (first contact): provide *template* name
          and optional *params* for template variables.
        - **Text message** (follow-up): provide *text_body* and set
          *template* to ``None``.

        Args:
            phone: Recipient phone number in international format
                (e.g. ``"+1234567890"``).
            template: WhatsApp message template name.  ``None`` for
                plain text messages.
            params: Template parameter values (``{"1": "John"}``).
            text_body: Plain text body for non-template messages.
            language_code: Language code for the template (default
                ``"en_US"``).

        Returns:
            A dict with ``message_id`` and ``status`` on success, or
            ``None`` on any failure.
        """
        if not self.is_configured:
            logger.warning("whatsapp_not_configured")
            return None

        if not phone:
            logger.warning("whatsapp_no_phone")
            return None

        try:
            client = await self._get_client()

            # Build the request body depending on message type.
            if template is not None:
                # Template message.
                template_obj: dict[str, Any] = {
                    "name": template,
                    "language": {"code": language_code},
                }
                if params:
                    components = [
                        {
                            "type": "body",
                            "parameters": [{"type": "text", "text": v} for v in params.values()],
                        },
                    ]
                    template_obj["components"] = components

                json_body: dict[str, Any] = {
                    "messaging_product": "whatsapp",
                    "to": phone,
                    "type": "template",
                    "template": template_obj,
                }
            else:
                # Plain text message.
                json_body = {
                    "messaging_product": "whatsapp",
                    "to": phone,
                    "type": "text",
                    "text": {"body": text_body or ""},
                }

            url = f"{WHATSAPP_API_BASE}/{self._phone_number_id}/messages"
            resp = await client.post(
                url,
                json=json_body,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
            )
            resp.raise_for_status()
            data = resp.json()

            messages = data.get("messages", [])
            message_id = messages[0]["id"] if messages else None

            logger.info(
                "whatsapp_message_sent",
                phone=phone,
                message_id=message_id,
                template=template,
            )

            return {
                "message_id": message_id,
                "status": "sent",
            }

        except httpx.HTTPStatusError as exc:
            logger.warning(
                "whatsapp_api_error",
                status=exc.response.status_code,
                phone=phone,
            )
            return None
        except httpx.HTTPError as exc:
            logger.warning(
                "whatsapp_request_failed",
                phone=phone,
                error=str(exc),
            )
            return None

    # ------------------------------------------------------------------
    # Delivery status
    # ------------------------------------------------------------------

    async def check_delivery_status(
        self,
        message_id: str,
    ) -> dict[str, Any] | None:
        """Check the delivery status of a sent message.

        Args:
            message_id: The WhatsApp message ID (``wamid.xxx``).

        Returns:
            A dict with ``message_id`` and ``status`` on success, or
            ``None`` on any failure.
        """
        if not self.is_configured:
            logger.warning("whatsapp_not_configured")
            return None

        if not message_id:
            logger.warning("whatsapp_no_message_id")
            return None

        try:
            client = await self._get_client()
            url = f"{WHATSAPP_API_BASE}/{message_id}"
            resp = await client.get(
                url,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                },
            )
            resp.raise_for_status()
            data = resp.json()

            return {
                "message_id": data.get("id", message_id),
                "status": data.get("status", "unknown"),
                "timestamp": data.get("timestamp"),
            }

        except httpx.HTTPStatusError as exc:
            logger.warning(
                "whatsapp_status_api_error",
                status=exc.response.status_code,
                message_id=message_id,
            )
            return None
        except httpx.HTTPError as exc:
            logger.warning(
                "whatsapp_status_request_failed",
                message_id=message_id,
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
