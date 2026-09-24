"""Notification system — deterministic alert routing with 3 channels.

This is NOT an LLM agent.  It is a pure infrastructure component that routes
alerts based on severity to the appropriate delivery channel(s):

* **Telegram** — realtime (critical + error)
* **Dashboard WebSocket** — all notifications via ``publish_event()``
* **Email** — batch summaries (30-minute window for warnings)

Severity routing table::

    critical → Telegram immediately + dashboard
    error    → Telegram immediately + dashboard
    warning  → Batch (30 min) + dashboard
    info     → Dashboard only
"""

from src.notifications.models import Notification, Severity
from src.notifications.router import NotificationRouter

__all__ = [
    "Notification",
    "NotificationRouter",
    "Severity",
]
