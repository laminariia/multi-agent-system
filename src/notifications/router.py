"""Notification router — deterministic severity-based alert routing.

The :class:`NotificationRouter` is the main entry point for all notifications.
It examines the severity of each :class:`Notification` and dispatches it to
the appropriate channel(s) according to the routing table from the spec:

+----------+------------------------------------------+
| Severity | Action                                   |
+==========+==========================================+
| critical | Telegram immediately + dashboard         |
| error    | Telegram immediately + dashboard         |
| warning  | Batch (30 min) + dashboard               |
| info     | Dashboard only                           |
+----------+------------------------------------------+

Usage::

    router = NotificationRouter(
        telegram=TelegramChannel(token, chat_id),
        websocket=WebSocketChannel(channels_plugin),
        email=EmailChannel(smtp_host, ...),
        batch=BatchCollector(valkey),
    )
    await router.notify(Notification(
        severity=Severity.ERROR,
        title="Circuit breaker tripped",
        message="Freelancer.com API returned 5 consecutive 500s",
        source="circuit_breaker",
    ))
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from src.notifications.batch import BatchCollector
from src.notifications.channels import (
    EmailChannel,
    TelegramChannel,
    WebSocketChannel,
)
from src.notifications.models import Notification, Severity

if TYPE_CHECKING:
    from src.bot.quiet_hours import QuietHoursManager

logger = structlog.get_logger(__name__)


class NotificationRouter:
    """Central notification dispatcher with severity-based routing.

    All channel arguments are optional.  Missing channels are silently
    skipped (with a debug log), so the router works in partial-config
    environments (dev, testing, CI).

    Args:
        telegram: Telegram channel for critical/error alerts.
        websocket: WebSocket channel for dashboard (all severities).
        email: Email channel for batch digests.
        batch: Batch collector for warning aggregation.
        quiet_hours: Optional quiet hours manager for Telegram suppression.
    """

    def __init__(
        self,
        *,
        telegram: TelegramChannel | None = None,
        websocket: WebSocketChannel | None = None,
        email: EmailChannel | None = None,
        batch: BatchCollector | None = None,
        quiet_hours: QuietHoursManager | None = None,
    ) -> None:
        self._telegram = telegram
        self._websocket = websocket
        self._email = email
        self._batch = batch
        self._quiet_hours = quiet_hours
        self._log = logger.bind(component="notification_router")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def notify(self, notification: Notification) -> dict[str, Any]:
        """Route a notification to the appropriate channels.

        Returns:
            A dict with channel names as keys and delivery results (bool)
            as values.  Example: ``{"telegram": True, "websocket": True}``.
        """
        results: dict[str, Any] = {}

        self._log.info(
            "notification.routing",
            notification_id=notification.id,
            severity=notification.severity.value,
            title=notification.title,
            source=notification.source,
        )

        # Dashboard WebSocket — ALL severities.
        results["websocket"] = await self._send_websocket(notification)

        if notification.severity == Severity.CRITICAL:
            results["telegram"] = await self._send_telegram(notification)

        elif notification.severity == Severity.ERROR:
            results["telegram"] = await self._send_telegram(notification)

        elif notification.severity == Severity.WARNING:
            results["batched"] = await self._add_to_batch(notification)

        # INFO — dashboard only, already handled above.

        self._log.info(
            "notification.routed",
            notification_id=notification.id,
            severity=notification.severity.value,
            results=results,
        )
        return results

    async def flush_warnings(self) -> dict[str, Any]:
        """Flush batched warnings and deliver via Telegram + Email.

        Should be called periodically (every 30 minutes) by the scheduler
        or manually for testing.

        Returns:
            Dict with ``count``, ``telegram``, and ``email`` delivery results.
        """
        results: dict[str, Any] = {"count": 0, "telegram": False, "email": False}

        if self._batch is None:
            self._log.debug("flush_warnings.no_batch_collector")
            return results

        notifications = await self._batch.flush(Severity.WARNING)
        results["count"] = len(notifications)

        if not notifications:
            self._log.debug("flush_warnings.empty")
            return results

        self._log.info("flush_warnings.flushing", count=len(notifications))

        # Send batch summary to Telegram.
        if self._telegram is not None:
            try:
                results["telegram"] = await self._telegram.send_batch(notifications)
            except Exception as exc:  # noqa: BLE001
                self._log.error("flush_warnings.telegram_error", error=str(exc))
                results["telegram"] = False

        # Send digest email.
        if self._email is not None:
            try:
                results["email"] = await self._email.send_batch(notifications)
            except Exception as exc:  # noqa: BLE001
                self._log.error("flush_warnings.email_error", error=str(exc))
                results["email"] = False

        return results

    async def flush_if_ready(self) -> dict[str, Any]:
        """Flush warnings only if the batch window has elapsed.

        Designed to be called by the scheduler tick without worrying
        about timing — the :class:`BatchCollector` tracks the window.

        Returns:
            Same as :meth:`flush_warnings` or empty results if not ready.
        """
        if self._batch is None:
            return {"count": 0, "telegram": False, "email": False}

        if not await self._batch.is_window_elapsed(Severity.WARNING):
            return {"count": 0, "telegram": False, "email": False}

        queue_size = await self._batch.size(Severity.WARNING)
        if queue_size == 0:
            return {"count": 0, "telegram": False, "email": False}

        return await self.flush_warnings()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _send_telegram(self, notification: Notification) -> bool:
        """Deliver to Telegram, swallowing errors.

        When a :class:`QuietHoursManager` is configured and the notification
        is not urgent (critical), non-critical messages are suppressed during
        quiet hours and queued for later delivery.
        """
        if self._telegram is None:
            self._log.debug(
                "telegram.not_configured",
                notification_id=notification.id,
            )
            return False

        # Check quiet hours (critical notifications always bypass)
        if self._quiet_hours is not None and notification.severity != Severity.CRITICAL:
            from datetime import UTC, datetime  # noqa: PLC0415

            priority = "urgent" if notification.severity == Severity.CRITICAL else "normal"
            if not self._quiet_hours.should_deliver(datetime.now(UTC), priority=priority):
                self._log.debug(
                    "telegram.quiet_hours_suppressed",
                    notification_id=notification.id,
                )
                return False

        try:
            return await self._telegram.send(notification)
        except Exception as exc:  # noqa: BLE001
            self._log.error(
                "telegram.delivery_error",
                notification_id=notification.id,
                error=str(exc),
            )
            return False

    async def _send_websocket(self, notification: Notification) -> bool:
        """Deliver to dashboard WebSocket, swallowing errors."""
        if self._websocket is None:
            self._log.debug(
                "websocket.not_configured",
                notification_id=notification.id,
            )
            return False
        try:
            return await self._websocket.send(notification)
        except (OSError, ConnectionError) as exc:
            self._log.warning(
                "websocket.delivery_error",
                notification_id=notification.id,
                error=str(exc),
            )
            return False

    async def _add_to_batch(self, notification: Notification) -> bool:
        """Add to batch collector for deferred delivery."""
        if self._batch is None:
            self._log.debug(
                "batch.not_configured",
                notification_id=notification.id,
            )
            return False
        try:
            await self._batch.add(notification)
            return True
        except Exception as exc:  # noqa: BLE001
            self._log.error(
                "batch.add_error",
                notification_id=notification.id,
                error=str(exc),
            )
            return False
