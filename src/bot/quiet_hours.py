"""Quiet hours notification suppression for Telegram bot.

Suppresses non-urgent notifications during configurable quiet hours
(default 22:00-08:00). Urgent notifications bypass quiet hours.

Queued notifications are stored in Valkey and delivered when quiet hours end.

Configurable via environment variables:
    - ``TELEGRAM_QUIET_HOURS_START`` (default: 22)
    - ``TELEGRAM_QUIET_HOURS_END`` (default: 8)

Per-user overrides are supported via ``user_override`` dicts.

Spec reference: ``docs/Full_work/specs/telegram-bot-spec.md`` (Quiet Hours section).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_QUEUE_KEY = "tg:quiet_hours:queue"


class QuietHoursManager:
    """Manages notification suppression during quiet hours.

    Args:
        start_hour: Hour when quiet period begins (0-23, inclusive).
        end_hour: Hour when quiet period ends (0-23, exclusive).
        valkey: Optional async Valkey client for queue persistence.

    Raises:
        ValueError: If hours are outside the valid 0-23 range.
    """

    def __init__(
        self,
        start_hour: int = 23,
        end_hour: int = 8,
        *,
        valkey: Any | None = None,
    ) -> None:
        if not (0 <= start_hour <= 23):
            msg = f"start_hour must be 0-23, got {start_hour}"
            raise ValueError(msg)
        if not (0 <= end_hour <= 23):
            msg = f"end_hour must be 0-23, got {end_hour}"
            raise ValueError(msg)

        self.start_hour = start_hour
        self.end_hour = end_hour
        self._valkey = valkey

    # ------------------------------------------------------------------
    # Class methods
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls) -> QuietHoursManager:
        """Create a ``QuietHoursManager`` from environment variables.

        Reads:
            - ``TELEGRAM_QUIET_HOURS_START`` (default: 22)
            - ``TELEGRAM_QUIET_HOURS_END`` (default: 8)
        """
        start = int(os.environ.get("TELEGRAM_QUIET_HOURS_START", "23"))
        end = int(os.environ.get("TELEGRAM_QUIET_HOURS_END", "8"))
        return cls(start_hour=start, end_hour=end)

    # ------------------------------------------------------------------
    # Core logic
    # ------------------------------------------------------------------

    def is_quiet(self, dt: datetime) -> bool:
        """Check whether the given datetime falls within quiet hours.

        Handles overnight ranges correctly (e.g. 22:00-08:00 crosses midnight).
        When ``start_hour == end_hour``, quiet hours are disabled (never quiet).
        """
        if self.start_hour == self.end_hour:
            return False

        hour = dt.hour

        if self.start_hour > self.end_hour:
            # Overnight range (e.g. 22-8): quiet if hour >= start OR hour < end
            return hour >= self.start_hour or hour < self.end_hour
        else:
            # Same-day range (e.g. 13-15): quiet if start <= hour < end
            return self.start_hour <= hour < self.end_hour

    def should_deliver(
        self,
        dt: datetime,
        *,
        priority: str = "normal",
        user_override: dict[str, Any] | None = None,
    ) -> bool:
        """Determine whether a notification should be delivered now.

        Args:
            dt: Current datetime (timezone-aware).
            priority: Notification priority. ``"urgent"`` always delivers.
            user_override: Optional per-user quiet hours settings:
                - ``quiet_hours_enabled``: bool (False disables quiet hours)
                - ``quiet_hours_start``: int (custom start hour)
                - ``quiet_hours_end``: int (custom end hour)

        Returns:
            ``True`` if the notification should be sent now, ``False`` to queue it.
        """
        # Urgent notifications always go through
        if priority == "urgent":
            return True

        # Per-user override
        if user_override is not None:
            enabled = user_override.get("quiet_hours_enabled", True)
            if not enabled:
                return True

            custom_start = user_override.get("quiet_hours_start")
            custom_end = user_override.get("quiet_hours_end")
            if custom_start is not None and custom_end is not None:
                # Use a temporary manager with user-specific hours
                user_mgr = QuietHoursManager(
                    start_hour=custom_start,
                    end_hour=custom_end,
                )
                return not user_mgr.is_quiet(dt)

        return not self.is_quiet(dt)

    def next_delivery_time(self, dt: datetime) -> datetime:
        """Calculate the next delivery time for queued notifications.

        If currently outside quiet hours, returns ``dt`` (deliver now).
        If during quiet hours, returns the datetime when quiet hours end.

        Args:
            dt: Current datetime (timezone-aware).

        Returns:
            The earliest datetime when notifications should be delivered.
        """
        if not self.is_quiet(dt):
            return dt

        # Calculate when quiet hours end
        hour = dt.hour

        if self.start_hour > self.end_hour:
            # Overnight range
            if hour >= self.start_hour:
                # Currently after start (same day) -- delivery next day at end_hour
                next_day = dt + timedelta(days=1)
                return next_day.replace(
                    hour=self.end_hour,
                    minute=0,
                    second=0,
                    microsecond=0,
                )
            else:
                # Currently before end (early morning) -- delivery today at end_hour
                return dt.replace(
                    hour=self.end_hour,
                    minute=0,
                    second=0,
                    microsecond=0,
                )
        else:
            # Same-day range -- delivery today at end_hour
            return dt.replace(
                hour=self.end_hour,
                minute=0,
                second=0,
                microsecond=0,
            )

    # ------------------------------------------------------------------
    # Queue management (Valkey-backed)
    # ------------------------------------------------------------------

    async def queue_notification(self, notification: dict[str, Any]) -> None:
        """Queue a notification for later delivery.

        Args:
            notification: Dict with at least ``chat_id``, ``text``, ``priority``.

        Raises:
            RuntimeError: If no Valkey client is configured.
        """
        if self._valkey is None:
            msg = "Cannot queue notifications without a Valkey client"
            raise RuntimeError(msg)

        data = json.dumps(notification)
        await self._valkey.rpush(_QUEUE_KEY, data)
        logger.debug(
            "quiet_hours.notification_queued",
            chat_id=notification.get("chat_id"),
        )

    async def deliver_queued(self, notifier: Any) -> int:
        """Deliver all queued notifications.

        Uses LPOP loop instead of LRANGE+DELETE to avoid a TOCTOU race
        where new items added between LRANGE and DELETE would be lost.

        Args:
            notifier: Object with an async ``send_message(chat_id, text, **kwargs)`` method
                (e.g. ``TelegramNotifier``).

        Returns:
            Number of notifications successfully delivered.
        """
        if self._valkey is None:
            return 0

        delivered = 0
        total = 0
        while True:
            raw = await self._valkey.lpop(_QUEUE_KEY)
            if raw is None:
                break
            total += 1
            try:
                notification = json.loads(raw)
                chat_id = notification.get("chat_id")
                text = notification.get("text", "")
                # Remove internal fields before forwarding
                kwargs: dict[str, Any] = {}
                if "parse_mode" in notification:
                    kwargs["parse_mode"] = notification["parse_mode"]
                if "reply_markup" in notification:
                    kwargs["reply_markup"] = notification["reply_markup"]

                await notifier.send_message(chat_id=chat_id, text=text, **kwargs)
                delivered += 1
            except Exception:
                logger.warning("quiet_hours.deliver_failed", exc_info=True)

        if total > 0:
            logger.info(
                "quiet_hours.delivered_queued",
                total=total,
                delivered=delivered,
            )
        return delivered
