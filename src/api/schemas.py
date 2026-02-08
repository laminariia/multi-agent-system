"""Pydantic request / response schemas for the MAS REST API.

All schema classes carry the ``Schema`` suffix per project convention.
Field examples are provided for OpenAPI documentation generation.

Convention: SQLAlchemy models (``src.core.models``) have NO suffix;
            Pydantic schemas here have the ``Schema`` suffix.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field

# =============================================================================
# Base configuration shared by all schemas
# =============================================================================


class _BaseSchema(BaseModel):
    """Private base that sets model-wide config for all API schemas."""

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        json_schema_extra={"strip_whitespace": True},
    )


# =============================================================================
# Error schemas
# =============================================================================


class ErrorDetailSchema(_BaseSchema):
    """Structured details attached to an error response."""

    expired_at: datetime | None = Field(default=None, description="When the resource expired (HITL, tokens, etc.)")
    retry_after: int | None = Field(default=None, description="Seconds until the client may retry")
    field: str | None = Field(default=None, description="Name of the invalid field, if applicable")
    reason: str | None = Field(default=None, description="Additional context about the error")

    model_config = ConfigDict(extra="allow")


class ErrorSchema(_BaseSchema):
    """Standard error envelope returned by every non-2xx response."""

    code: str = Field(..., examples=["HITL_EXPIRED"], description="Machine-readable error code")
    message: str = Field(..., examples=["This HITL request has expired"], description="Human-readable message")
    details: dict[str, Any] = Field(default_factory=dict, description="Contextual details about the error")


class ErrorResponseSchema(_BaseSchema):
    """Top-level error wrapper matching the API specification."""

    error: ErrorSchema


# =============================================================================
# Auth schemas
# =============================================================================


class RegisterRequestSchema(_BaseSchema):
    """Fields submitted during user registration."""

    email: EmailStr = Field(..., examples=["user@example.com"], description="User email address")
    password: str = Field(..., min_length=6, max_length=128, examples=["secret123"], description="Account password")
    name: str | None = Field(default=None, max_length=100, examples=["John"], description="Display name (optional)")


class LoginRequestSchema(_BaseSchema):
    """Credentials submitted during login."""

    email: EmailStr = Field(..., examples=["user@example.com"], description="User email address")
    password: str = Field(..., min_length=6, max_length=128, examples=["secret123"], description="Account password")


class UserResponseSchema(_BaseSchema):
    """Public representation of a user (no password hash)."""

    id: uuid.UUID = Field(..., description="User unique identifier")
    email: str = Field(..., examples=["user@example.com"])
    name: str | None = Field(default=None, examples=["John"])
    role: str = Field(..., examples=["owner"], description="User role: owner | co_owner | viewer | moderator")
    status: str = Field(default="active", examples=["active"], description="Account status: active | pending_approval | rejected | suspended")
    telegram_chat_id: int | None = Field(default=None, description="Linked Telegram chat ID")
    created_at: datetime = Field(..., description="Account creation timestamp")
    last_login_at: datetime | None = Field(default=None, description="Most recent login")


class LoginResponseSchema(_BaseSchema):
    """Returned on successful authentication."""

    access_token: str = Field(..., examples=["eyJ..."], description="Short-lived JWT access token")
    refresh_token: str = Field(..., examples=["eyJ..."], description="Long-lived JWT refresh token")
    user: UserResponseSchema


class RegisterPendingResponseSchema(_BaseSchema):
    """Returned when a non-first user registers (requires owner approval)."""

    message: str = Field(..., examples=["Registration submitted. An administrator must approve your account before you can sign in."])
    status: str = Field(..., examples=["pending_approval"])


class UserListResponseSchema(_BaseSchema):
    """Paginated list of users."""

    users: list[UserResponseSchema]
    total: int = Field(..., ge=0, description="Total users matching the filter")


class UserApproveRequestSchema(_BaseSchema):
    """Body for approving a pending user."""

    role: str = Field(
        default="viewer",
        pattern=r"^(viewer|moderator|co_owner)$",
        description="Role to assign: viewer | moderator | co_owner",
    )


class UserRoleUpdateSchema(_BaseSchema):
    """Body for changing a user's role."""

    role: str = Field(
        ...,
        pattern=r"^(viewer|moderator|co_owner)$",
        description="New role: viewer | moderator | co_owner",
    )


class UserStatusUpdateSchema(_BaseSchema):
    """Body for suspending or reactivating a user."""

    status: str = Field(
        ...,
        pattern=r"^(active|suspended)$",
        description="New status: active | suspended",
    )


class OwnerTransferRequestSchema(_BaseSchema):
    """Body for transferring ownership to another user."""

    new_owner_id: uuid.UUID = Field(..., description="UUID of the user who will become the new owner")


class TokenRefreshSchema(_BaseSchema):
    """Request body for refreshing an access token."""

    refresh_token: str = Field(..., min_length=10, description="The refresh token to exchange")


class TokenRefreshResponseSchema(_BaseSchema):
    """New token pair returned after a successful refresh."""

    access_token: str = Field(..., description="Fresh access token")
    refresh_token: str = Field(..., description="Rotated refresh token")


# =============================================================================
# HITL schemas
# =============================================================================


class HITLItemSchema(_BaseSchema):
    """A single Human-in-the-Loop queue item."""

    id: uuid.UUID
    type: str = Field(
        ..., examples=["bid_approval"],
        description="bid_approval | code_review | delivery | revision | alert",
    )
    priority: str = Field(default="normal", examples=["urgent"], description="urgent | normal | low")
    title: str = Field(..., examples=["React Dashboard for Analytics"])
    description: str | None = Field(default=None)
    expires_at: datetime | None = Field(default=None)
    payload: dict[str, Any] = Field(default_factory=dict, description="Type-specific data blob")
    available_actions: list[str] = Field(
        default_factory=list,
        examples=[["approve", "edit", "skip", "later"]],
        description="Actions the user may take",
    )
    created_at: datetime


class HITLPendingResponseSchema(_BaseSchema):
    """Paginated list of pending HITL items."""

    items: list[HITLItemSchema]
    total: int = Field(..., ge=0, description="Total pending items (before pagination)")
    pending_urgent: int = Field(default=0, ge=0, description="Count of urgent-priority pending items")


class HITLResolveRequestSchema(_BaseSchema):
    """Body submitted when resolving a HITL item."""

    action: str = Field(
        ...,
        examples=["approve"],
        description="Resolution action: approve | reject | edit | skip | later",
    )
    note: str | None = Field(default=None, max_length=2000, description="Optional human note")
    edited_payload: dict[str, Any] | None = Field(
        default=None,
        description="Modified payload (only for 'edit' action)",
    )


class HITLResolveResponseSchema(_BaseSchema):
    """Confirmation of a resolved HITL item."""

    id: uuid.UUID
    status: str = Field(default="resolved", examples=["resolved"])
    resolution: str = Field(..., examples=["approve"])
    next_action: str = Field(
        ...,
        examples=["bid_will_be_submitted"],
        description="Downstream effect of the resolution",
    )


class HITLTypeStatsSchema(_BaseSchema):
    """Per-type breakdown of HITL counters."""

    pending: int = Field(default=0, ge=0)
    resolved: int = Field(default=0, ge=0)


class HITLTodayStatsSchema(_BaseSchema):
    """Today's aggregate HITL counters."""

    pending: int = Field(default=0, ge=0)
    resolved: int = Field(default=0, ge=0)
    expired: int = Field(default=0, ge=0)


class HITLStatsSchema(_BaseSchema):
    """Overall HITL statistics."""

    today: HITLTodayStatsSchema
    avg_resolution_time_minutes: float = Field(default=0.0, ge=0.0)
    by_type: dict[str, HITLTypeStatsSchema] = Field(default_factory=dict)


# =============================================================================
# Agent schemas
# =============================================================================


class AgentStatusSchema(_BaseSchema):
    """Real-time status of a single agent."""

    name: str = Field(..., examples=["scout"])
    display_name: str | None = Field(default=None, examples=["Scout Agent"])
    pipeline: str | None = Field(default=None, examples=["A"])
    status: str = Field(..., examples=["idle"], description="idle | working | error | dead")
    last_heartbeat: datetime | None = Field(default=None)
    current_task: str | None = Field(default=None, examples=["Drafting proposal for job #4521"])
    restart_count: int = Field(default=0, ge=0)
    error_message: str | None = Field(default=None)


class AgentStatusListSchema(_BaseSchema):
    """Aggregated agent status report."""

    system_health: str = Field(
        ..., examples=["healthy"],
        description="Overall system health: healthy | degraded | critical",
    )
    last_check: datetime
    agents: list[AgentStatusSchema]


class AgentLogSchema(_BaseSchema):
    """A single agent log entry."""

    id: uuid.UUID
    timestamp: datetime = Field(..., description="When the event occurred")
    level: str = Field(default="info", examples=["info"], description="info | warning | error")
    event_type: str = Field(..., examples=["llm_call"])
    message: str | None = Field(default=None, examples=["Generated 245 lines of React code"])
    details: dict[str, Any] | None = Field(default=None)


class AgentLogListSchema(_BaseSchema):
    """Paginated list of agent logs."""

    agent: str = Field(..., examples=["dev"])
    logs: list[AgentLogSchema]
    total: int = Field(default=0, ge=0)


class AgentActionResponseSchema(_BaseSchema):
    """Response for agent control actions (restart / pause / resume)."""

    agent: str = Field(..., examples=["scout"])
    action: str = Field(..., examples=["restart"], description="restart | pause | resume")
    status: str = Field(..., examples=["ok"])
    message: str = Field(..., examples=["Agent scout restart initiated"])


# =============================================================================
# Job schemas
# =============================================================================


class BidSummarySchema(_BaseSchema):
    """Abbreviated bid information embedded in a job response."""

    id: uuid.UUID
    bid_amount: Decimal = Field(..., examples=[2500.00])
    status: str = Field(..., examples=["sent"])
    created_at: datetime


class JobResponseSchema(_BaseSchema):
    """Full job detail including associated bids."""

    id: uuid.UUID
    platform: str = Field(..., examples=["freelancer"])
    external_id: str = Field(..., examples=["12345"])
    title: str = Field(..., examples=["React Dashboard for Analytics"])
    description: str | None = Field(default=None)
    budget_min: Decimal | None = Field(default=None, examples=[1000.00])
    budget_max: Decimal | None = Field(default=None, examples=[3000.00])
    budget_type: str | None = Field(default=None, examples=["fixed"])
    currency: str = Field(default="USD", examples=["USD"])
    client_info: dict[str, Any] | None = Field(default=None)
    skills_required: list[str] | None = Field(default=None, examples=[["react", "typescript"]])
    deadline: datetime | None = Field(default=None)
    status: str = Field(..., examples=["qualified"])
    score: Decimal | None = Field(default=None, examples=[0.87])
    disqualify_reason: str | None = Field(default=None)
    discovered_at: datetime
    url: str | None = Field(default=None)
    bids: list[BidSummarySchema] = Field(default_factory=list)


class JobListResponseSchema(_BaseSchema):
    """Paginated list of jobs."""

    jobs: list[JobResponseSchema]
    total: int = Field(default=0, ge=0)


class JobDisqualifySchema(_BaseSchema):
    """Request body for manually disqualifying a job."""

    reason: str = Field(
        ...,
        min_length=3,
        max_length=255,
        examples=["Client budget too low for scope"],
        description="Reason for disqualification",
    )


class JobDisqualifyResponseSchema(_BaseSchema):
    """Confirmation of job disqualification."""

    id: uuid.UUID
    status: str = Field(default="disqualified", examples=["disqualified"])
    disqualify_reason: str


# =============================================================================
# Health schemas
# =============================================================================


class HealthResponseSchema(_BaseSchema):
    """System health check response."""

    status: str = Field(..., examples=["healthy"], description="healthy | degraded | unhealthy")
    db_connected: bool = Field(..., description="PostgreSQL reachable")
    valkey_connected: bool = Field(..., description="Valkey / Redis reachable")
    version: str = Field(..., examples=["1.0.0"])
    timestamp: datetime


# =============================================================================
# Generic message schema
# =============================================================================


class MessageSchema(_BaseSchema):
    """Generic success message."""

    message: str = Field(..., examples=["Operation completed successfully"])


# =============================================================================
# Telegram link schemas
# =============================================================================


class TelegramLinkSchema(_BaseSchema):
    """Code submitted to link a Telegram account to a MAS user."""

    code: str = Field(
        ...,
        min_length=6,
        max_length=6,
        description="6-character link code obtained from the Telegram bot via /start",
    )
