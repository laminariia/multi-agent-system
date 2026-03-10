"""Telegram DM sender for Pipeline B approved outreach messages.

Uses Telethon (MTProto) for direct message delivery with aggressive
rate limiting to avoid Telegram anti-spam bans.

Rate limits (conservative defaults):
- 5 DMs per hour
- 12 minutes (720s) between messages

Usage::

    sender = TelegramDMSender(api_id=..., api_hash=..., session_string=...)
    await sender.connect()
    result = await sender.send_dm("username", "Hello!")
    await sender.disconnect()

    # Bulk send for approved campaign DMs:
    stats = await send_approved_telegram_dms(campaign_id, db_session)
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Rate-limit state (module-level, resets hourly)
# ---------------------------------------------------------------------------

_hourly_count: int = 0
_hourly_reset_time: float = 0.0
_last_send_time: float = 0.0


def _reset_hourly_counter() -> None:
    """Reset counters (for testing)."""
    global _hourly_count, _hourly_reset_time, _last_send_time  # noqa: PLW0603
    _hourly_count = 0
    _hourly_reset_time = 0.0
    _last_send_time = 0.0


def _check_hourly_rate_limit(max_per_hour: int) -> bool:
    """Check the hourly rate limit. Returns True if send is allowed."""
    global _hourly_count, _hourly_reset_time  # noqa: PLW0603

    now = time.monotonic()
    if now - _hourly_reset_time >= 3600:
        _hourly_count = 0
        _hourly_reset_time = now

    if _hourly_count >= max_per_hour:
        return False

    _hourly_count += 1
    return True


# ---------------------------------------------------------------------------
# TelegramDMSender
# ---------------------------------------------------------------------------


class TelegramDMSender:
    """Telethon DM sender with rate limiting and error handling.

    Parameters
    ----------
    api_id:
        Telegram API ID from https://my.telegram.org/apps
    api_hash:
        Telegram API hash.
    session_string:
        Telethon StringSession for auth without phone prompt.
    max_dms_per_hour:
        Max DMs per hour (default 5).
    min_interval_seconds:
        Min seconds between DMs (default 720 = 12 minutes).
    """

    def __init__(
        self,
        *,
        api_id: int | None = None,
        api_hash: str | None = None,
        session_string: str | None = None,
        max_dms_per_hour: int = 5,
        min_interval_seconds: int = 720,
    ) -> None:
        # Load from settings if not provided.
        if api_id is None or api_hash is None or session_string is None:
            try:
                from src.core.config import get_settings  # noqa: PLC0415

                settings = get_settings()
                api_id = api_id or settings.TELEGRAM_API_ID
                api_hash = api_hash or settings.TELEGRAM_API_HASH
                session_string = session_string or settings.TELEGRAM_SESSION_STRING
            except Exception:  # noqa: BLE001
                logger.debug("telegram_dm_settings_load_skipped", exc_info=True)

        self.api_id: int | None = api_id
        self.api_hash: str | None = api_hash
        self.session_string: str | None = session_string
        self.max_dms_per_hour: int = max_dms_per_hour
        self.min_interval_seconds: int = min_interval_seconds
        self._client: Any = None

    @property
    def is_configured(self) -> bool:
        """Return True if Telegram credentials are set."""
        return bool(self.api_id and self.api_hash and self.session_string)

    async def connect(self) -> None:
        """Connect the Telethon client."""
        if not self.is_configured:
            logger.warning("telegram_dm_not_configured")
            return

        try:
            from telethon import TelegramClient  # noqa: PLC0415
            from telethon.sessions import StringSession  # noqa: PLC0415

            self._client = TelegramClient(
                StringSession(self.session_string),
                self.api_id,
                self.api_hash,
            )
            await self._client.connect()
            logger.info("telegram_dm_connected")
        except ImportError:
            logger.warning("telegram_dm_telethon_not_installed")
        except Exception:  # noqa: BLE001
            logger.error("telegram_dm_connect_error", exc_info=True)

    async def send_dm(
        self, user_identifier: str | int, message_text: str,
    ) -> bool | str:
        """Send a direct message to a Telegram user.

        Parameters
        ----------
        user_identifier:
            Username (str) or user ID (int).
        message_text:
            The message text to send.

        Returns
        -------
        bool | str
            ``True`` if sent successfully, ``"flood_wait"`` if rate-limited
            by Telegram, ``"privacy_restricted"`` if user blocks DMs,
            ``False`` on generic failure.
        """
        if self._client is None:
            logger.warning("telegram_dm_not_connected")
            return False

        # Rate limit check.
        if not _check_hourly_rate_limit(self.max_dms_per_hour):
            logger.warning(
                "telegram_dm_hourly_limit",
                max=self.max_dms_per_hour,
                user=str(user_identifier),
            )
            return "flood_wait"

        # Enforce minimum interval between sends.
        global _last_send_time  # noqa: PLW0603
        now = time.monotonic()
        elapsed = now - _last_send_time
        if _last_send_time > 0 and elapsed < self.min_interval_seconds:
            wait_time = self.min_interval_seconds - elapsed
            logger.info(
                "telegram_dm_interval_wait",
                wait_seconds=round(wait_time, 1),
                user=str(user_identifier),
            )
            await asyncio.sleep(wait_time)

        try:
            from telethon.errors import (  # noqa: PLC0415
                FloodWaitError,
                UserPrivacyRestrictedError,
            )

            await self._client.send_message(user_identifier, message_text)
            _last_send_time = time.monotonic()

            logger.info("telegram_dm_sent", user=str(user_identifier))
            return True

        except FloodWaitError as e:
            logger.warning(
                "telegram_dm_flood_wait",
                seconds=e.seconds,
                user=str(user_identifier),
            )
            # Wait and retry once.
            await asyncio.sleep(e.seconds)
            try:
                await self._client.send_message(user_identifier, message_text)
                _last_send_time = time.monotonic()
                return True
            except Exception:  # noqa: BLE001
                logger.error("telegram_dm_retry_failed", user=str(user_identifier), exc_info=True)
                return "flood_wait"

        except UserPrivacyRestrictedError:
            logger.warning(
                "telegram_dm_privacy_restricted",
                user=str(user_identifier),
            )
            return "privacy_restricted"

        except Exception:  # noqa: BLE001
            logger.error(
                "telegram_dm_send_failed",
                user=str(user_identifier),
                exc_info=True,
            )
            return False

    async def disconnect(self) -> None:
        """Disconnect the Telethon client."""
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:  # noqa: BLE001
                logger.debug("telegram_dm_disconnect_error", exc_info=True)
            self._client = None


# ---------------------------------------------------------------------------
# Bulk send for approved campaign Telegram DMs
# ---------------------------------------------------------------------------


async def send_approved_telegram_dms(
    campaign_id: str | uuid.UUID,
    db_session: Any,
) -> dict[str, int]:
    """Send all approved Telegram DMs for a campaign.

    Queries ``CampaignLead`` records with ``status='approved'`` and
    ``channel_type='telegram'``, sends each via :class:`TelegramDMSender`,
    and updates status to ``'sent'``, ``'failed'``, or ``'privacy_restricted'``.

    Parameters
    ----------
    campaign_id:
        UUID of the :class:`EmailCampaign`.
    db_session:
        An async SQLAlchemy session.

    Returns
    -------
    dict
        ``{"sent": N, "failed": M, "privacy_restricted": P, "rate_limited": R}``
    """
    from sqlalchemy import select, update  # noqa: PLC0415

    from src.core.models import CampaignLead, Lead  # noqa: PLC0415

    sender = TelegramDMSender()
    stats: dict[str, int] = {"sent": 0, "failed": 0, "privacy_restricted": 0, "rate_limited": 0}

    if not sender.is_configured:
        logger.warning("send_approved_telegram_dms_skipped", reason="Telegram not configured")
        return stats

    await sender.connect()

    try:
        result = await db_session.execute(
            select(CampaignLead, Lead)
            .join(Lead, CampaignLead.lead_id == Lead.id)
            .where(
                CampaignLead.campaign_id == campaign_id,
                CampaignLead.status == "approved",
                CampaignLead.channel_type == "telegram",
            )
        )
        rows = result.all()

        for campaign_lead, lead in rows:
            tg_username = getattr(lead, "telegram_username", None)
            if not tg_username:
                logger.warning("telegram_dm_no_username", lead_id=str(lead.id))
                stats["failed"] += 1
                continue

            msg_body = campaign_lead.personalized_body or ""
            result_code = await sender.send_dm(tg_username, msg_body)

            if result_code is True:
                await db_session.execute(
                    update(CampaignLead)
                    .where(
                        CampaignLead.campaign_id == campaign_lead.campaign_id,
                        CampaignLead.lead_id == campaign_lead.lead_id,
                    )
                    .values(status="sent", sent_at=datetime.now(UTC))
                )
                stats["sent"] += 1

            elif result_code == "privacy_restricted":
                await db_session.execute(
                    update(CampaignLead)
                    .where(
                        CampaignLead.campaign_id == campaign_lead.campaign_id,
                        CampaignLead.lead_id == campaign_lead.lead_id,
                    )
                    .values(status="failed")
                )
                stats["privacy_restricted"] += 1

            elif result_code == "flood_wait":
                stats["rate_limited"] += 1
                # Stop sending — Telegram is rate limiting us.
                logger.warning("telegram_dm_stopping_flood", campaign_id=str(campaign_id))
                break

            else:
                await db_session.execute(
                    update(CampaignLead)
                    .where(
                        CampaignLead.campaign_id == campaign_lead.campaign_id,
                        CampaignLead.lead_id == campaign_lead.lead_id,
                    )
                    .values(status="failed")
                )
                stats["failed"] += 1

        await db_session.commit()

    finally:
        await sender.disconnect()

    logger.info(
        "send_approved_telegram_dms_complete",
        campaign_id=str(campaign_id),
        **stats,
    )
    return stats
