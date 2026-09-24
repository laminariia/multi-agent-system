"""Per-user notification preferences for Telegram bot.

Queries the ``users`` table for accounts with linked Telegram chat IDs
and filters by notification type, quiet hours, and role permissions.

Also provides HITL expiry reminder detection for the cron-based reminder
system.

Spec reference: ``docs/Full_work/specs/telegram-bot-spec.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Notification preference model
# ---------------------------------------------------------------------------

# Roles that can receive HITL action notifications (approve/reject/etc.)
_HITL_ACTION_ROLES = frozenset({"owner", "admin", "moderator"})

# Notification types that viewers are allowed to receive
_VIEWER_ALLOWED_TYPES = frozenset({"alert", "system"})

# Default enabled notification types -- "all" means no filtering
_DEFAULT_ENABLED_TYPES = ["all"]


@dataclass
class NotificationPrefs:
    """Per-user notification preference settings.

    Attributes:
        user_id: UUID of the user.
        enabled_types: List of notification types the user wants.
            ``["all"]`` means all types are enabled.
        quiet_start: Hour (0-23) when quiet period begins, or ``None``.
        quiet_end: Hour (0-23) when quiet period ends, or ``None``.
        urgent_override: Whether urgent notifications bypass quiet hours.
    """

    user_id: str
    enabled_types: list[str] = field(default_factory=lambda: list(_DEFAULT_ENABLED_TYPES))
    quiet_start: int | None = None
    quiet_end: int | None = None
    urgent_override: bool = True


def _extract_prefs(settings: dict[str, Any]) -> NotificationPrefs:
    """Extract notification preferences from a user's settings JSONB.

    The ``settings`` column on the ``User`` model is a JSONB dict.  This
    function reads the ``notification_prefs`` sub-key and returns a
    ``NotificationPrefs`` dataclass.  Missing keys fall back to safe
    defaults.
    """
    prefs_data = settings.get("notification_prefs", {}) if settings else {}
    return NotificationPrefs(
        user_id=prefs_data.get("user_id", ""),
        enabled_types=prefs_data.get("enabled_types", list(_DEFAULT_ENABLED_TYPES)),
        quiet_start=prefs_data.get("quiet_start"),
        quiet_end=prefs_data.get("quiet_end"),
        urgent_override=prefs_data.get("urgent_override", True),
    )


def _is_in_quiet_hours(
    prefs: NotificationPrefs,
    now: datetime | None = None,
) -> bool:
    """Check if the current time falls within the user's quiet hours.

    Returns ``False`` if quiet hours are not configured (both start and
    end are ``None``).
    """
    if prefs.quiet_start is None or prefs.quiet_end is None:
        return False

    dt = now or datetime.now(UTC)
    hour = dt.hour

    if prefs.quiet_start == prefs.quiet_end:
        return False

    if prefs.quiet_start > prefs.quiet_end:
        # Overnight range (e.g. 22-8): quiet if hour >= start OR hour < end
        return hour >= prefs.quiet_start or hour < prefs.quiet_end
    # Same-day range (e.g. 13-15): quiet if start <= hour < end
    return prefs.quiet_start <= hour < prefs.quiet_end


def _type_matches(prefs: NotificationPrefs, hitl_type: str) -> bool:
    """Check whether the user's enabled_types list matches the given type."""
    if "all" in prefs.enabled_types:
        return True
    return hitl_type in prefs.enabled_types


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def get_notification_targets(
    hitl_type: str,
    priority: str = "normal",
    *,
    now: datetime | None = None,
) -> list[int]:
    """Get list of telegram_chat_ids that should receive this notification.

    Queries all users with linked Telegram accounts and filters by:
    - notification preferences (type matching from user settings JSONB)
    - quiet hours (skip unless ``urgent_override`` and ``priority="urgent"``)
    - role permissions (moderator+ for HITL actions, viewer only for alerts)

    Args:
        hitl_type: The HITL notification type (e.g. ``"bid_approval"``).
        priority: Notification priority. ``"urgent"`` bypasses quiet hours.
        now: Optional datetime for testing. Defaults to UTC now.

    Returns:
        List of Telegram chat IDs that should receive the notification.
    """
    try:
        from sqlalchemy import select  # noqa: PLC0415

        from src.core.database import get_db_session  # noqa: PLC0415
        from src.core.models import User  # noqa: PLC0415

        dt = now or datetime.now(UTC)
        chat_ids: list[int] = []

        async with get_db_session() as session:
            stmt = select(User).where(
                User.telegram_chat_id.isnot(None),
                User.status == "active",
            )
            result = await session.execute(stmt)
            users = result.scalars().all()

        for user in users:
            chat_id = user.telegram_chat_id
            if chat_id is None:
                continue

            # Role-based filtering
            role = user.role or "viewer"
            if role not in _HITL_ACTION_ROLES and hitl_type not in _VIEWER_ALLOWED_TYPES:
                logger.debug(
                    "notification_target_skipped_role",
                    user_id=str(user.id),
                    role=role,
                    hitl_type=hitl_type,
                )
                continue

            # Extract prefs from user settings JSONB
            prefs = _extract_prefs(user.settings)
            prefs.user_id = str(user.id)

            # Type filtering
            if not _type_matches(prefs, hitl_type):
                logger.debug(
                    "notification_target_skipped_type",
                    user_id=str(user.id),
                    hitl_type=hitl_type,
                    enabled_types=prefs.enabled_types,
                )
                continue

            # Quiet hours filtering
            if _is_in_quiet_hours(prefs, dt):
                if priority == "urgent" and prefs.urgent_override:
                    # Urgent overrides quiet hours
                    pass
                else:
                    logger.debug(
                        "notification_target_skipped_quiet",
                        user_id=str(user.id),
                        hitl_type=hitl_type,
                        priority=priority,
                    )
                    continue

            chat_ids.append(chat_id)

        logger.info(
            "notification_targets_resolved",
            hitl_type=hitl_type,
            priority=priority,
            target_count=len(chat_ids),
        )
        return chat_ids

    except Exception:
        logger.warning(
            "notification_targets_failed",
            hitl_type=hitl_type,
            exc_info=True,
        )
        return []


async def get_all_linked_chat_ids() -> list[int]:
    """Get all telegram_chat_ids from the users table.

    Returns only active users with a non-null ``telegram_chat_id``.

    Returns:
        List of Telegram chat IDs.
    """
    try:
        from sqlalchemy import select  # noqa: PLC0415

        from src.core.database import get_db_session  # noqa: PLC0415
        from src.core.models import User  # noqa: PLC0415

        async with get_db_session() as session:
            stmt = select(User.telegram_chat_id).where(
                User.telegram_chat_id.isnot(None),
                User.status == "active",
            )
            result = await session.execute(stmt)
            return [row[0] for row in result.all()]

    except Exception:
        logger.warning("get_all_linked_chat_ids_failed", exc_info=True)
        return []


# ---------------------------------------------------------------------------
# HITL expiry reminders
# ---------------------------------------------------------------------------


async def check_expiring_hitl_items(threshold_percent: float = 0.5) -> list[dict[str, Any]]:
    """Find HITL items approaching expiry.

    Scans the ``hitl_queue`` table for pending items with an ``expires_at``
    timestamp and returns those that have passed the given threshold
    percentage of their TTL.

    Args:
        threshold_percent: Fraction of TTL elapsed before an item is
            considered "expiring" (default 0.5 = 50%).

    Returns:
        List of dicts with ``id``, ``type``, ``title``, ``expires_at``,
        ``elapsed_percent`` for items past the threshold.
    """
    try:
        from sqlalchemy import select  # noqa: PLC0415

        from src.core.database import get_db_session  # noqa: PLC0415
        from src.core.models import HITLQueue  # noqa: PLC0415

        now = datetime.now(UTC)
        expiring: list[dict[str, Any]] = []

        async with get_db_session() as session:
            stmt = select(HITLQueue).where(
                HITLQueue.status == "pending",
                HITLQueue.expires_at.isnot(None),
            )
            result = await session.execute(stmt)
            items = result.scalars().all()

        for item in items:
            if item.expires_at is None or item.created_at is None:
                continue

            total_seconds = (item.expires_at - item.created_at).total_seconds()
            if total_seconds <= 0:
                continue

            elapsed_seconds = (now - item.created_at).total_seconds()
            elapsed_percent = elapsed_seconds / total_seconds

            if elapsed_percent >= threshold_percent:
                expiring.append(
                    {
                        "id": str(item.id),
                        "type": item.type,
                        "title": item.title,
                        "expires_at": item.expires_at.isoformat(),
                        "elapsed_percent": round(elapsed_percent, 3),
                        "priority": item.priority or "normal",
                    }
                )

        logger.info(
            "expiring_hitl_items_checked",
            total_pending=len(items),
            expiring_count=len(expiring),
            threshold=threshold_percent,
        )
        return expiring

    except Exception:
        logger.warning("check_expiring_hitl_items_failed", exc_info=True)
        return []


async def send_expiry_reminders() -> int:
    """Send reminder notifications for HITL items near expiry.

    Intended to be called by a cron job. Finds items past 50% of their
    TTL and sends reminder notifications to all eligible users.

    Returns:
        Number of reminder notifications sent.
    """
    try:
        from src.bot.notifications import TelegramNotifier  # noqa: PLC0415

        expiring = await check_expiring_hitl_items(threshold_percent=0.5)
        if not expiring:
            return 0

        notifier = TelegramNotifier()
        total_sent = 0

        for item in expiring:
            targets = await get_notification_targets(
                hitl_type=item["type"],
                priority="normal",
            )
            if not targets:
                continue

            pct = int(item["elapsed_percent"] * 100)
            text = (
                f"\u23f0 <b>HITL Expiry Reminder</b>\n"
                f"<b>{item['title']}</b>\n"
                f"Type: {item['type']} | {pct}% of TTL elapsed\n"
                f"Expires: {item['expires_at']}\n"
                f"ID: <code>{item['id']}</code>"
            )

            for chat_id in targets:
                try:
                    result = await notifier.send_message(chat_id=chat_id, text=text)
                    if result is not None:
                        total_sent += 1
                except Exception:
                    logger.warning(
                        "expiry_reminder_send_failed",
                        chat_id=chat_id,
                        hitl_id=item["id"],
                    )

        logger.info(
            "expiry_reminders_sent",
            expiring_count=len(expiring),
            notifications_sent=total_sent,
        )
        return total_sent

    except Exception:
        logger.warning("send_expiry_reminders_failed", exc_info=True)
        return 0
