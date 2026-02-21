"""CRUD routes for Telegram channel management.

Allows the Dashboard to add, list, toggle, and remove Telegram channels
that the :class:`TelegramListener` monitors for job postings.

Mounted at ``/api/v1/telegram-channels``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog
from litestar import Controller, delete, get, patch, post
from litestar.exceptions import NotFoundException
from litestar.params import Parameter
from litestar.status_codes import HTTP_201_CREATED
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.guards import require_role
from src.core.models import TelegramChannel

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class TelegramChannelCreateSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    username: str = Field(..., max_length=255, description="Channel username, e.g. @freelance_jobs")
    title: str | None = Field(None, max_length=500, description="Human-readable channel title")
    category: str | None = Field(None, max_length=100, description="Category for filtering, e.g. 'web_dev'")


class TelegramChannelUpdateSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    active: bool | None = None
    title: str | None = Field(None, max_length=500)
    category: str | None = Field(None, max_length=100)


class TelegramChannelResponseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    title: str | None
    category: str | None
    active: bool
    created_at: datetime
    updated_at: datetime


class TelegramChannelListResponseSchema(BaseModel):
    channels: list[TelegramChannelResponseSchema]
    total: int


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------


class TelegramChannelController(Controller):
    """Manage Telegram channels monitored for job postings."""

    path = "/api/v1/telegram-channels"
    tags = ["telegram-channels"]

    @get(
        "/",
        summary="List all Telegram channels",
    )
    async def list_channels(
        self,
        db_session: AsyncSession,
        active_only: bool = Parameter(default=False, description="Filter to active channels only"),
    ) -> TelegramChannelListResponseSchema:
        """Return all configured Telegram channels."""
        stmt = select(TelegramChannel).order_by(TelegramChannel.id)
        if active_only:
            stmt = stmt.where(TelegramChannel.active.is_(True))

        result = await db_session.execute(stmt)
        rows = result.scalars().all()

        channels = [TelegramChannelResponseSchema.model_validate(row) for row in rows]
        return TelegramChannelListResponseSchema(channels=channels, total=len(channels))

    @post(
        "/",
        summary="Add a Telegram channel",
        guards=[require_role("owner", "co_owner")],
        status_code=HTTP_201_CREATED,
    )
    async def create_channel(
        self,
        data: TelegramChannelCreateSchema,
        db_session: AsyncSession,
    ) -> TelegramChannelResponseSchema:
        """Add a new Telegram channel to monitor.

        The username is normalised (leading ``@`` is stripped).
        The listener will pick up the new channel on its next refresh cycle (~5 min).
        """
        username = data.username.lstrip("@").strip()

        channel = TelegramChannel(
            username=username,
            title=data.title,
            category=data.category,
        )
        db_session.add(channel)
        await db_session.flush()
        await db_session.refresh(channel)

        logger.info("telegram_channel.created", username=username, id=channel.id)
        return TelegramChannelResponseSchema.model_validate(channel)

    @patch(
        "/{channel_id:int}",
        summary="Update a Telegram channel",
        guards=[require_role("owner", "co_owner")],
    )
    async def update_channel(
        self,
        channel_id: int,
        data: TelegramChannelUpdateSchema,
        db_session: AsyncSession,
    ) -> TelegramChannelResponseSchema:
        """Toggle active status or update metadata for a channel."""
        stmt = select(TelegramChannel).where(TelegramChannel.id == channel_id)
        result = await db_session.execute(stmt)
        channel = result.scalar_one_or_none()

        if channel is None:
            raise NotFoundException(detail=f"Telegram channel {channel_id} not found")

        if data.active is not None:
            channel.active = data.active
        if data.title is not None:
            channel.title = data.title
        if data.category is not None:
            channel.category = data.category

        await db_session.flush()
        await db_session.refresh(channel)

        logger.info("telegram_channel.updated", id=channel_id, active=channel.active)
        return TelegramChannelResponseSchema.model_validate(channel)

    @delete(
        "/{channel_id:int}",
        summary="Delete a Telegram channel",
        guards=[require_role("owner")],
        status_code=200,
    )
    async def delete_channel(
        self,
        channel_id: int,
        db_session: AsyncSession,
    ) -> dict[str, Any]:
        """Remove a Telegram channel from monitoring."""
        stmt = select(TelegramChannel).where(TelegramChannel.id == channel_id)
        result = await db_session.execute(stmt)
        channel = result.scalar_one_or_none()

        if channel is None:
            raise NotFoundException(detail=f"Telegram channel {channel_id} not found")

        await db_session.delete(channel)
        await db_session.flush()

        logger.info("telegram_channel.deleted", id=channel_id, username=channel.username)
        return {"status": "deleted", "id": channel_id}
