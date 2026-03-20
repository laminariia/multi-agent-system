"""Message poller for active negotiations -- polls platforms for client replies.

Runs as a cron job (APScheduler, every 5 min) outside LangGraph.
Per-bid asyncio.Lock prevents duplicate polls.  Interval enforcement
skips bids polled too recently.

Spec: docs/Full_work/specs/negotiation-spec.md  (Section "Message Poller")
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.core.database import get_db_session
from src.core.models import Bid, ClientMessage, Job
from src.core.models import Negotiation as NegotiationORM

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Platform polling intervals (seconds) -- spec-defined
# ---------------------------------------------------------------------------

POLLING_INTERVALS: dict[str, int] = {
    "freelancer": 300,  # 5 min  (REST API)
    "fl_ru": 600,  # 10 min (scraping -- gentle)
    "kwork": 600,  # 10 min (scraping)
    "telegram": 0,  # real-time via Telethon listener; poll is fallback
}

_TERMINAL_NEGOTIATION_STATES: frozenset[str] = frozenset(
    {
        "won",
        "lost",
        "stale",
        "accepted",
        "declined",
    }
)


# ---------------------------------------------------------------------------
# Adapter protocol -- any platform adapter must satisfy this
# ---------------------------------------------------------------------------


@runtime_checkable
class PlatformMessageAdapter(Protocol):
    """Minimal interface an adapter must expose for message polling."""

    async def get_thread_messages(
        self,
        thread_id: str,
        *,
        since: datetime | None = None,
    ) -> list[dict[str, Any]]: ...  # pragma: no cover

    async def get_direct_messages(
        self,
        user_id: int,
        *,
        since: datetime | None = None,
    ) -> list[dict[str, Any]]: ...  # pragma: no cover


# ---------------------------------------------------------------------------
# MessagePoller
# ---------------------------------------------------------------------------


class MessagePoller:
    """Polls platform adapters for new client messages on active negotiations.

    Parameters
    ----------
    adapters:
        Mapping of platform name to adapter instance.  Each adapter must
        expose ``get_thread_messages`` and/or ``get_direct_messages``.
    """

    def __init__(self, adapters: dict[str, Any] | None = None) -> None:
        self._adapters: dict[str, Any] = adapters or {}
        # Per-bid lock prevents parallel polls for the same bid
        self._bid_locks: dict[str, asyncio.Lock] = {}

    # -- public entry point --------------------------------------------------

    async def poll_all_active(self) -> int:
        """Main cron entry point -- poll all active negotiations.

        Returns the total number of new messages saved.
        """
        total_new = 0
        async with get_db_session() as session:
            rows = await self._get_active_negotiations(session)
            for neg, bid, job in rows:
                platform: str = job.platform if job else "freelancer"
                if platform not in self._adapters:
                    continue

                # Enforce minimum interval between polls
                interval = POLLING_INTERVALS.get(platform, 300)
                if interval and bid.last_polled_at:
                    elapsed = (datetime.now(UTC) - bid.last_polled_at).total_seconds()
                    if elapsed < interval:
                        continue

                # One poll at a time per bid
                lock = self._bid_locks.setdefault(str(bid.id), asyncio.Lock())
                if lock.locked():
                    continue
                async with lock:
                    count = await self._poll_platform(
                        session,
                        neg,
                        bid,
                        platform,
                    )
                    total_new += count

            await session.commit()

        return total_new

    # -- internals -----------------------------------------------------------

    async def _get_active_negotiations(
        self,
        session: AsyncSession,
    ) -> list[tuple[NegotiationORM, Bid, Job | None]]:
        """Fetch negotiations in non-terminal states with their bids + jobs."""
        stmt = (
            select(NegotiationORM, Bid, Job)
            .join(Bid, NegotiationORM.bid_id == Bid.id)
            .outerjoin(Job, Bid.job_id == Job.id)
            .where(
                NegotiationORM.state.notin_(list(_TERMINAL_NEGOTIATION_STATES)),
            )
            .options(joinedload(Bid.job))
        )
        result = await session.execute(stmt)
        return result.unique().all()  # type: ignore[return-value]

    async def _poll_platform(
        self,
        session: AsyncSession,
        neg: NegotiationORM,
        bid: Bid,
        platform: str,
    ) -> int:
        """Poll a single platform for new messages on *bid*.

        Returns the number of new messages saved.
        """
        adapter = self._adapters.get(platform)
        if adapter is None:
            return 0

        try:
            raw_messages = await self._fetch_messages(adapter, bid, platform)
        except (OSError, ConnectionError) as exc:
            logger.warning(
                "poll_platform_error",
                platform=platform,
                bid_id=str(bid.id),
                error=str(exc),
            )
            return 0
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "poll_platform_unexpected_error",
                platform=platform,
                bid_id=str(bid.id),
                error=str(exc),
            )
            return 0

        saved = 0
        for msg in raw_messages:
            ext_id = msg.get("external_id") or msg.get("id")
            if not ext_id:
                continue

            # Deduplicate by external_id
            exists = await session.execute(
                select(ClientMessage.id).where(
                    ClientMessage.bid_id == bid.id,
                    ClientMessage.external_id == str(ext_id),
                )
            )
            if exists.scalar_one_or_none() is not None:
                continue

            cm = ClientMessage(
                bid_id=bid.id,
                project_id=bid.job_id,
                direction="inbound",
                sender="client",
                content=msg.get("content", msg.get("text", "")),
                platform=platform,
                external_id=str(ext_id),
                auto_generated=False,
                hitl_reviewed=False,
            )
            session.add(cm)
            saved += 1

        # Update last_polled_at regardless of whether messages were found
        bid.last_polled_at = datetime.now(UTC)

        if saved:
            logger.info(
                "poll_platform_new_messages",
                platform=platform,
                bid_id=str(bid.id),
                count=saved,
            )

        return saved

    async def _fetch_messages(
        self,
        adapter: Any,
        bid: Bid,
        platform: str,
    ) -> list[dict[str, Any]]:
        """Dispatch to the platform-specific adapter method.

        Uses the spec-defined method names per platform:
        - freelancer: ``get_thread_messages(thread_id, since=...)``
        - fl_ru:      ``scrape_inbox_thread(project_url, since=...)``
        - kwork:      ``scrape_chat(order_id, since=...)``
        - telegram:   ``get_direct_messages(user_id, since=...)``
        """
        since = bid.last_polled_at

        match platform:
            case "freelancer":
                thread_id = bid.platform_thread_id or bid.platform_bid_id
                if not thread_id:
                    return []
                return await adapter.get_thread_messages(
                    thread_id=thread_id,
                    since=since,
                )

            case "fl_ru":
                # FL.ru uses project URL for inbox scraping
                project_url = getattr(bid, "platform_url", None) or bid.platform_bid_id
                if not project_url:
                    return []
                return await adapter.scrape_inbox_thread(
                    project_url=project_url,
                    since=since,
                )

            case "kwork":
                order_id = bid.platform_bid_id
                if not order_id:
                    return []
                return await adapter.scrape_chat(
                    order_id=order_id,
                    since=since,
                )

            case "telegram":
                # Telegram listener handles real-time; this is the fallback
                user_id = bid.client_telegram_id
                if not user_id:
                    return []
                return await adapter.get_direct_messages(
                    user_id=user_id,
                    since=since,
                )

            case _:
                return []

    # -- outbound helper -----------------------------------------------------

    async def save_outbound_message(
        self,
        session: AsyncSession,
        bid_id: Any,
        content: str,
        *,
        sender: str = "ai",
        platform: str | None = None,
        auto_generated: bool = True,
    ) -> ClientMessage:
        """Persist an outbound message (AI-generated or operator-written)."""
        cm = ClientMessage(
            bid_id=bid_id,
            direction="outbound",
            sender=sender,
            content=content,
            platform=platform,
            auto_generated=auto_generated,
            hitl_reviewed=False,
        )
        session.add(cm)
        return cm


# ---------------------------------------------------------------------------
# Module-level convenience for APScheduler cron registration
# ---------------------------------------------------------------------------

_default_poller: MessagePoller | None = None


def get_poller() -> MessagePoller:
    """Return the module-level singleton (created lazily)."""
    global _default_poller  # noqa: PLW0603
    if _default_poller is None:
        _default_poller = MessagePoller()
    return _default_poller


async def poll_all_active_negotiations() -> int:
    """APScheduler-compatible cron function.

    Usage with APScheduler::

        scheduler.add_job(
            poll_all_active_negotiations,
            "interval",
            seconds=300,
            id="negotiation_poller",
        )
    """
    poller = get_poller()
    return await poller.poll_all_active()
