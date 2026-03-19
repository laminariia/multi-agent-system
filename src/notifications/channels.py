"""Notification delivery channels.

Three concrete channel implementations:

* :class:`TelegramChannel` — sends alerts via Telegram Bot API (httpx).
* :class:`WebSocketChannel` — pushes to dashboard via ``publish_event()``.
* :class:`EmailChannel` — sends HTML email summaries via ``aiosmtplib``.

All channels implement the same ``send`` / ``send_batch`` async interface
and swallow delivery errors (log + return False) so that one channel's
failure never blocks the others.
"""

from __future__ import annotations

import abc
import json
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import httpx
import structlog

from src.notifications.models import Notification, Severity

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class BaseChannel(abc.ABC):
    """Interface that every notification channel must implement."""

    @abc.abstractmethod
    async def send(self, notification: Notification) -> bool:
        """Deliver a single notification.  Return ``True`` on success."""

    async def send_batch(self, notifications: list[Notification]) -> bool:
        """Deliver a batch of notifications.  Default: send each individually."""
        if not notifications:
            return True
        results = [await self.send(n) for n in notifications]
        return all(results)


# ---------------------------------------------------------------------------
# Telegram channel
# ---------------------------------------------------------------------------

_SEVERITY_EMOJI: dict[Severity, str] = {
    Severity.CRITICAL: "\U0001f6a8",  # rotating light
    Severity.ERROR: "\u274c",  # cross mark
    Severity.WARNING: "\u26a0\ufe0f",  # warning
    Severity.INFO: "\u2139\ufe0f",  # info
}


def _escape_html(text: str) -> str:
    """Escape HTML special characters for Telegram HTML parse mode."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_telegram_message(notification: Notification) -> str:
    """Format a :class:`Notification` as an HTML string for Telegram."""
    emoji = _SEVERITY_EMOJI.get(notification.severity, "")
    lines = [
        f"{emoji} <b>[{notification.severity.value.upper()}]</b> {_escape_html(notification.title)}",
    ]
    if notification.source:
        lines.append(f"Source: <code>{_escape_html(notification.source)}</code>")
    if notification.agent_name:
        lines.append(f"Agent: <code>{_escape_html(notification.agent_name)}</code>")
    if notification.message:
        msg = notification.message[:500]
        if len(notification.message) > 500:
            msg += "..."
        lines.append(f"\n{_escape_html(msg)}")
    lines.append(f"\nID: <code>{notification.id}</code>")
    return "\n".join(lines)


def format_telegram_batch(notifications: list[Notification]) -> str:
    """Format a batch of notifications as a single Telegram message."""
    severity_counts: dict[str, int] = {}
    for n in notifications:
        key = n.severity.value
        severity_counts[key] = severity_counts.get(key, 0) + 1

    count_parts = ", ".join(f"{v} {k}" for k, v in severity_counts.items())
    lines = [
        f"\u26a0\ufe0f <b>Batch Summary</b> ({len(notifications)} alerts: {count_parts})",
        "",
    ]
    for n in sorted(notifications, key=lambda x: x.severity_order, reverse=True):
        emoji = _SEVERITY_EMOJI.get(n.severity, "")
        source = f" [{n.source}]" if n.source else ""
        lines.append(f"{emoji} {_escape_html(n.title)}{source}")

    return "\n".join(lines)


class TelegramChannel(BaseChannel):
    """Delivers notifications via the Telegram Bot API.

    Uses ``httpx.AsyncClient`` directly (no python-telegram-bot dependency),
    matching the pattern in ``src.bot.notifications``.
    """

    BASE_URL = "https://api.telegram.org"

    def __init__(
        self,
        token: str,
        chat_id: str,
        *,
        timeout: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not token:
            raise ValueError("Telegram bot token is required")
        if not chat_id:
            raise ValueError("Telegram chat_id is required")
        self._token = token
        self._chat_id = chat_id
        self._timeout = timeout
        self._client = client
        self._owns_client = client is None

    async def close(self) -> None:
        """Close the underlying httpx client if this instance owns it."""
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    def _get_client(self) -> httpx.AsyncClient:
        """Return the shared client, creating one lazily if needed."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
            self._owns_client = True
        return self._client

    async def _post(self, text: str) -> bool:
        """Low-level send via Bot API ``sendMessage``."""
        url = f"{self.BASE_URL}/bot{self._token}/sendMessage"
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "HTML",
        }
        try:
            client = self._get_client()
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                logger.error(
                    "telegram_channel.send_failed",
                    chat_id=self._chat_id,
                    error=data.get("description"),
                )
                return False
            return True
        except httpx.HTTPError as exc:
            logger.error("telegram_channel.send_error", error=str(exc))
            return False

    async def send(self, notification: Notification) -> bool:
        """Send a single notification to Telegram."""
        text = format_telegram_message(notification)
        success = await self._post(text)
        if success:
            logger.info(
                "telegram_channel.sent",
                notification_id=notification.id,
                severity=notification.severity.value,
            )
        return success

    async def send_batch(self, notifications: list[Notification]) -> bool:
        """Send a batch summary to Telegram as a single message."""
        if not notifications:
            return True
        if len(notifications) == 1:
            return await self.send(notifications[0])
        text = format_telegram_batch(notifications)
        success = await self._post(text)
        if success:
            logger.info(
                "telegram_channel.batch_sent",
                count=len(notifications),
            )
        return success


# ---------------------------------------------------------------------------
# WebSocket / Dashboard channel
# ---------------------------------------------------------------------------


class WebSocketChannel(BaseChannel):
    """Pushes notifications to the dashboard via Litestar ChannelsPlugin.

    The ``channels_plugin`` is optional — when ``None`` (e.g. in background
    workers without a running Litestar app) notifications are silently
    skipped with a debug log.
    """

    CHANNEL_NAME = "notification"

    def __init__(self, channels_plugin: Any | None = None) -> None:
        self._channels = channels_plugin

    async def send(self, notification: Notification) -> bool:
        """Publish a notification event to the WebSocket channel."""
        if self._channels is None:
            logger.debug(
                "ws_channel.no_plugin",
                notification_id=notification.id,
            )
            return False
        try:
            payload = json.dumps(notification.to_dict(), default=str)
            self._channels.publish(payload, [self.CHANNEL_NAME])
            logger.debug(
                "ws_channel.published",
                notification_id=notification.id,
                severity=notification.severity.value,
            )
            return True
        except (OSError, ConnectionError) as exc:
            logger.warning(
                "ws_channel.publish_failed",
                notification_id=notification.id,
                error=str(exc),
            )
            return False


# ---------------------------------------------------------------------------
# Email channel
# ---------------------------------------------------------------------------


def format_email_html(notifications: list[Notification]) -> str:
    """Build an HTML email body from a list of notifications."""
    rows: list[str] = []
    for n in sorted(notifications, key=lambda x: x.severity_order, reverse=True):
        colour = {
            Severity.CRITICAL: "#dc2626",
            Severity.ERROR: "#ea580c",
            Severity.WARNING: "#ca8a04",
            Severity.INFO: "#2563eb",
        }.get(n.severity, "#6b7280")
        rows.append(
            f'<tr><td style="color:{colour};font-weight:bold">'
            f"{n.severity.value.upper()}</td>"
            f"<td>{_escape_html(n.title)}</td>"
            f"<td>{_escape_html(n.source)}</td>"
            f"<td>{n.created_at.strftime('%H:%M:%S')}</td></tr>"
        )

    table_rows = "\n".join(rows)
    return f"""\
<html>
<body>
<h2>MAS Alert Summary ({len(notifications)} notifications)</h2>
<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse">
<tr><th>Severity</th><th>Title</th><th>Source</th><th>Time</th></tr>
{table_rows}
</table>
<p style="color:#6b7280;font-size:12px">Multi-Agent System — automated alert digest</p>
</body>
</html>"""


class EmailChannel(BaseChannel):
    """Sends notification digests via SMTP.

    Uses ``aiosmtplib`` for async delivery.  Falls back gracefully when
    SMTP credentials are not configured.
    """

    def __init__(
        self,
        smtp_host: str,
        smtp_port: int = 587,
        smtp_user: str = "",
        smtp_password: str = "",
        from_addr: str = "",
        to_addr: str = "",
        *,
        use_tls: bool = True,
    ) -> None:
        self._host = smtp_host
        self._port = smtp_port
        self._user = smtp_user
        self._password = smtp_password
        self._from = from_addr
        self._to = to_addr
        self._use_tls = use_tls

    @property
    def configured(self) -> bool:
        """Return ``True`` if SMTP credentials are set."""
        return bool(self._host and self._from and self._to)

    async def send(self, notification: Notification) -> bool:
        """Send a single notification as an email."""
        return await self.send_batch([notification])

    async def send_batch(self, notifications: list[Notification]) -> bool:
        """Send a batch of notifications as a single digest email."""
        if not notifications:
            return True
        if not self.configured:
            logger.debug("email_channel.not_configured")
            return False

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"MAS Alert Digest ({len(notifications)} notifications)"
        msg["From"] = self._from
        msg["To"] = self._to

        html_body = format_email_html(notifications)
        msg.attach(MIMEText(html_body, "html"))

        try:
            import aiosmtplib  # noqa: PLC0415

            await aiosmtplib.send(
                msg,
                hostname=self._host,
                port=self._port,
                username=self._user or None,
                password=self._password or None,
                use_tls=self._use_tls,
            )
            logger.info(
                "email_channel.sent",
                count=len(notifications),
                to=self._to,
            )
            return True
        except ImportError:
            logger.warning("email_channel.aiosmtplib_not_installed")
            return False
        except Exception as exc:  # noqa: BLE001
            logger.error("email_channel.send_error", error=str(exc))
            return False
