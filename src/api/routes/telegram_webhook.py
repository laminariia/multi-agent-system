"""Telegram webhook endpoint for the Litestar API.

Provides ``POST /api/v1/telegram/webhook`` for receiving Telegram updates
via webhook mode (alternative to long polling).

Security:
    - Validates ``X-Telegram-Bot-Api-Secret-Token`` header against
      ``TELEGRAM_WEBHOOK_SECRET`` setting.
    - In dev mode (no secret configured), all requests are accepted.

Spec reference: ``docs/Full_work/specs/telegram-bot-spec.md`` (Webhook Mode).
"""

from __future__ import annotations

import hmac
from typing import Any

import structlog
from litestar import Controller, post
from litestar.exceptions import NotAuthorizedException
from litestar.params import Parameter

from src.core.config import Settings

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Validation helpers (importable for testing)
# ---------------------------------------------------------------------------


def _validate_webhook_secret(
    header_secret: str | None,
    expected_secret: str,
) -> bool:
    """Validate the webhook secret token from the request header.

    Uses ``hmac.compare_digest`` for constant-time comparison to prevent
    timing side-channel attacks.

    Args:
        header_secret: Value of the ``X-Telegram-Bot-Api-Secret-Token`` header.
        expected_secret: The expected secret from settings.

    Returns:
        ``True`` if the secret is valid (or no secret is configured).
    """
    # No secret configured -- dev mode, accept all
    if not expected_secret:
        return True

    if header_secret is None:
        return False

    return hmac.compare_digest(header_secret, expected_secret)


def _parse_telegram_update(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Parse and validate a Telegram update payload.

    Minimal validation: checks that ``update_id`` is present. The full
    parsing is done by the python-telegram-bot library.

    Args:
        payload: Raw JSON payload from the Telegram webhook.

    Returns:
        The validated payload dict, or ``None`` if invalid.
    """
    if "update_id" not in payload:
        return None
    return payload


# ---------------------------------------------------------------------------
# Webhook Controller
# ---------------------------------------------------------------------------


class TelegramWebhookController(Controller):
    """Handle incoming Telegram webhook updates.

    Mounted at ``/api/v1/telegram``.
    """

    path = "/api/v1/telegram"
    tags = ["telegram"]

    @post(
        "/webhook",
        summary="Telegram webhook receiver",
        description=(
            "Receives Telegram updates via webhook. "
            "Validates the secret token header and forwards updates "
            "to the bot application's update queue."
        ),
        exclude_from_auth=True,
        status_code=200,
    )
    async def webhook(
        self,
        data: dict[str, Any],
        settings: Settings,
        x_telegram_bot_api_secret_token: str | None = Parameter(
            header="X-Telegram-Bot-Api-Secret-Token",
            required=False,
            default=None,
        ),
    ) -> dict[str, str]:
        """Process an incoming Telegram webhook update.

        The endpoint validates the secret token, parses the update,
        and publishes it to a Valkey channel for the bot process to consume.
        """
        # Validate secret
        expected_secret = getattr(settings, "TELEGRAM_WEBHOOK_SECRET", "")
        if not _validate_webhook_secret(x_telegram_bot_api_secret_token, expected_secret):
            logger.warning("telegram_webhook.invalid_secret")
            raise NotAuthorizedException(detail="Invalid webhook secret")

        # Parse update
        update = _parse_telegram_update(data)
        if update is None:
            logger.warning("telegram_webhook.invalid_update", payload_keys=list(data.keys()))
            return {"status": "ignored", "reason": "invalid update payload"}

        # Publish to Valkey for the bot process to pick up
        try:
            import json  # noqa: PLC0415

            from src.core.database import get_valkey  # noqa: PLC0415

            valkey = get_valkey()
            await valkey.publish(
                "telegram:webhook:updates",
                json.dumps(update),
            )
            logger.debug(
                "telegram_webhook.update_published",
                update_id=update.get("update_id"),
            )
        except Exception:
            logger.warning("telegram_webhook.publish_failed", exc_info=True)

        return {"status": "ok"}
