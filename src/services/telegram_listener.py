"""Telegram channel listener — monitors channels for new job posts.

Runs as a separate process using Telethon (MTProto User API).
Discovered posts are parsed via LLM and pushed to a Valkey queue
for consumption by :class:`TelegramChannelAdapter`.

Entry point::

    python -m src.services.telegram_listener
"""

from __future__ import annotations

import asyncio
import json
import signal
import sys
from pathlib import Path
from typing import Any

import structlog
from telethon import TelegramClient, events
from telethon.sessions import StringSession

logger = structlog.get_logger(__name__)

# Valkey key matching the adapter.
_VALKEY_QUEUE_KEY = "mas:tg_jobs:pending"

# How often to refresh the channel list from DB (seconds).
_CHANNEL_REFRESH_INTERVAL = 5 * 60  # 5 minutes


class TelegramListener:
    """Listens to Telegram channels for new messages and pushes parsed jobs to Valkey.

    Parameters
    ----------
    api_id:
        Telegram API ID from https://my.telegram.org/apps.
    api_hash:
        Telegram API hash.
    session_string:
        Telethon StringSession for persistent auth.
    valkey:
        ``redis.asyncio.Redis`` client.
    db_session_factory:
        Async callable returning an ``AsyncSession`` context manager.
    llm_client:
        Shared LLM client for post parsing.
    """

    def __init__(
        self,
        api_id: int,
        api_hash: str,
        session_string: str,
        valkey: Any,
        db_session_factory: Any,
        llm_client: Any,
    ) -> None:
        self._api_id = api_id
        self._api_hash = api_hash
        self._session_string = session_string
        self._valkey = valkey
        self._db_session_factory = db_session_factory
        self._llm_client = llm_client

        self._client: TelegramClient | None = None
        self._active_channels: list[str] = []
        self._channel_entities: dict[str, Any] = {}
        self._refresh_task: asyncio.Task[None] | None = None
        self._running = False
        self._log = logger.bind(component="telegram_listener")

    async def start(self) -> None:
        """Connect to Telegram, load channels, and start listening."""
        self._log.info("starting")

        self._client = TelegramClient(
            StringSession(self._session_string),
            self._api_id,
            self._api_hash,
        )
        await self._client.connect()

        if not await self._client.is_user_authorized():
            self._log.error("not_authorized", hint="Run scripts/generate_telegram_session.py first")
            raise RuntimeError("Telegram session is not authorized")

        me = await self._client.get_me()
        self._log.info("authenticated", user=me.username or me.id)

        # Load initial channel list from DB.
        await self._refresh_channels()

        if not self._active_channels:
            self._log.warning("no_active_channels", hint="Add channels via Dashboard API")

        # Register the message handler.
        self._client.add_event_handler(
            self._on_new_message,
            events.NewMessage(chats=self._active_channels if self._active_channels else None),
        )

        # Start periodic channel refresh.
        self._running = True
        self._refresh_task = asyncio.create_task(self._periodic_refresh())

        self._log.info("started", channels=len(self._active_channels))

    async def run_until_stopped(self) -> None:
        """Block until the listener is stopped."""
        if self._client is None:
            raise RuntimeError("Call start() first")
        await self._client.run_until_disconnected()

    async def stop(self) -> None:
        """Gracefully disconnect."""
        self._running = False
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
        if self._client and self._client.is_connected():
            await self._client.disconnect()
        self._log.info("stopped")

    # ------------------------------------------------------------------
    # Channel management
    # ------------------------------------------------------------------

    async def _refresh_channels(self) -> None:
        """Reload active channels from the database."""
        from sqlalchemy import select  # noqa: PLC0415

        from src.core.models import TelegramChannel  # noqa: PLC0415

        try:
            async with self._db_session_factory() as session:
                stmt = select(TelegramChannel.username).where(TelegramChannel.active.is_(True))
                result = await session.execute(stmt)
                usernames = [row[0] for row in result.all()]
        except Exception:
            self._log.exception("channel_refresh_failed")
            return

        if set(usernames) != set(self._active_channels):
            self._log.info(
                "channels_updated",
                old_count=len(self._active_channels),
                new_count=len(usernames),
                channels=usernames,
            )
            self._active_channels = usernames

            # Resolve entities for the new channels.
            if self._client:
                self._channel_entities.clear()
                for username in usernames:
                    try:
                        entity = await self._client.get_entity(username)
                        self._channel_entities[username] = entity
                    except Exception:
                        self._log.warning("channel_resolve_failed", username=username, exc_info=True)

                # Re-register the event handler with updated channel list.
                self._client.remove_event_handler(self._on_new_message)
                chats = list(self._channel_entities.values()) or None
                self._client.add_event_handler(
                    self._on_new_message,
                    events.NewMessage(chats=chats),
                )

    async def _periodic_refresh(self) -> None:
        """Periodically refresh channel list from DB."""
        while self._running:
            await asyncio.sleep(_CHANNEL_REFRESH_INTERVAL)
            if not self._running:
                break
            await self._refresh_channels()

    # ------------------------------------------------------------------
    # Message handling
    # ------------------------------------------------------------------

    async def _on_new_message(self, event: events.NewMessage.Event) -> None:
        """Handle a new message from a monitored channel."""
        message = event.message
        text = message.text or ""

        # Determine channel name.
        chat = await event.get_chat()
        channel_name = getattr(chat, "username", None) or str(chat.id)

        # Verify this channel is in our active list.
        if self._active_channels and channel_name not in self._active_channels:
            # Could be a channel resolved by ID; check entity map.
            if chat.id not in {getattr(e, "id", None) for e in self._channel_entities.values()}:
                return

        self._log.debug(
            "new_message",
            channel=channel_name,
            message_id=message.id,
            text_len=len(text),
        )

        # Parse with LLM.
        from src.services.telegram_parser import parse_telegram_post  # noqa: PLC0415

        parsed = await parse_telegram_post(text, channel_name, self._llm_client)
        if parsed is None:
            return

        # Build normalised job dict matching the adapter interface.
        channel_id = chat.id
        job_data: dict[str, Any] = {
            "platform": "telegram",
            "external_id": f"tg_{channel_id}_{message.id}",
            "title": parsed["title"],
            "description": parsed["description"],
            "budget_min": parsed["budget_min"],
            "budget_max": parsed["budget_max"],
            "currency": parsed["currency"],
            "skills_required": parsed["skills"],
            "url": f"https://t.me/{channel_name}/{message.id}" if channel_name else None,
            "raw_data": {
                "telegram_channel_id": channel_id,
                "telegram_channel_name": channel_name,
                "telegram_message_id": message.id,
                "original_text": text[:5000],
                "parsed_deadline": parsed.get("deadline"),
            },
        }

        # Push to Valkey queue.
        try:
            await self._valkey.lpush(_VALKEY_QUEUE_KEY, json.dumps(job_data, ensure_ascii=False, default=str))
            self._log.info(
                "job_queued",
                channel=channel_name,
                title=parsed["title"][:80],
                external_id=job_data["external_id"],
            )
        except Exception:
            self._log.exception("valkey_push_failed", channel=channel_name)


# ======================================================================
# Entry point for running as a standalone process
# ======================================================================


async def _main() -> None:
    """Entry point for ``python -m src.services.telegram_listener``."""
    # Ensure project root is on sys.path.
    project_root = str(Path(__file__).resolve().parents[2])
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from src.core.config import get_settings  # noqa: PLC0415
    from src.core.database import get_db_session, get_valkey  # noqa: PLC0415
    from src.core.llm_client import LLMClient  # noqa: PLC0415

    settings = get_settings()

    if not settings.TELEGRAM_API_ID or not settings.TELEGRAM_API_HASH:
        logger.error("TELEGRAM_API_ID and TELEGRAM_API_HASH must be set")
        sys.exit(1)
    if not settings.TELEGRAM_SESSION_STRING:
        logger.error("TELEGRAM_SESSION_STRING must be set (run scripts/generate_telegram_session.py)")
        sys.exit(1)

    valkey = get_valkey()
    llm_client = LLMClient(
        api_key=settings.OPENROUTER_API_KEY,
        base_url=settings.OPENROUTER_BASE_URL,
    )

    listener = TelegramListener(
        api_id=settings.TELEGRAM_API_ID,
        api_hash=settings.TELEGRAM_API_HASH,
        session_string=settings.TELEGRAM_SESSION_STRING,
        valkey=valkey,
        db_session_factory=get_db_session,
        llm_client=llm_client,
    )

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(listener.stop()))
        except NotImplementedError:
            # Windows doesn't support add_signal_handler.
            pass

    await listener.start()
    await listener.run_until_stopped()


if __name__ == "__main__":
    asyncio.run(_main())
