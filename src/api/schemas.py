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
    status: str = Field(
        default="active", examples=["active"],
        description="Account status: active | pending_approval | rejected | suspended",
    )
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

    message: str = Field(
        ...,
        examples=["Registration submitted. An administrator must approve your account before you can sign in."],
    )
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


class HITLBulkResolveRequestSchema(_BaseSchema):
    """Bulk resolve multiple HITL items with the same action."""

    ids: list[uuid.UUID] = Field(..., min_length=1, max_length=50, description="HITL item IDs to resolve")
    action: str = Field(..., examples=["approve"], description="Resolution action applied to all items")
    note: str | None = Field(default=None, max_length=2000, description="Optional note")


class HITLBulkResolveResponseSchema(_BaseSchema):
    """Result of bulk resolution."""

    resolved: int = Field(default=0, ge=0, description="Successfully resolved count")
    failed: int = Field(default=0, ge=0, description="Failed count (already resolved, expired, etc)")
    errors: list[dict[str, Any]] = Field(default_factory=list, description="Per-item errors")


class HITLTrendDaySchema(_BaseSchema):
    """Single day in the HITL trends response."""

    date: str = Field(..., examples=["2026-02-12"], description="Date in YYYY-MM-DD format")
    created: int = Field(default=0, ge=0, description="Items created on this day")
    resolved: int = Field(default=0, ge=0, description="Items resolved on this day")


class HITLTrendTotalsSchema(_BaseSchema):
    """Totals across the entire trends window."""

    created: int = Field(default=0, ge=0)
    resolved: int = Field(default=0, ge=0)
    pending: int = Field(default=0, ge=0, description="Created minus resolved")


class HITLTrendsResponseSchema(_BaseSchema):
    """HITL resolution trends over a configurable window."""

    days: int = Field(..., ge=1, description="Number of days in the window")
    trends: list[HITLTrendDaySchema] = Field(default_factory=list)
    totals: HITLTrendTotalsSchema


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


# =============================================================================
# Orchestrator schemas
# =============================================================================


class OrchestratorStatusSchema(_BaseSchema):
    """Current state of the autonomous orchestrator runner."""

    alive: bool = Field(..., description="Whether the runner process is alive")
    pid: int | None = Field(default=None, description="Runner process ID")
    uptime_seconds: int | None = Field(default=None, description="Runner uptime in seconds")
    mode: str | None = Field(default=None, examples=["self-direct"])
    goals_pending: int = Field(default=0, ge=0)
    goals_completed: int = Field(default=0, ge=0)
    goals_failed: int = Field(default=0, ge=0)
    health_grade: str | None = Field(default=None, examples=["A"])
    health_score: int | None = Field(default=None, examples=[93])


class OrchestratorStartRequestSchema(_BaseSchema):
    """Start request — no time parameters.

    Sessions are agent-driven: the agent decides when to finish based on
    work completion (code review clean, goals done, etc.).
    """


class OrchestratorStartResponseSchema(_BaseSchema):
    """Response after starting the runner."""

    status: str = Field(default="started", examples=["started"])
    pid: int = Field(..., description="New runner PID")
    message: str = Field(..., examples=["Orchestrator started in self-direct mode"])


class OrchestratorStopResponseSchema(_BaseSchema):
    """Response after stopping the runner."""

    status: str = Field(default="stopped", examples=["stopped"])
    message: str = Field(..., examples=["Orchestrator stopped"])


class GoalSchema(_BaseSchema):
    """A single orchestrator goal."""

    id: str = Field(..., examples=["g_001"])
    title: str = Field(..., examples=["Fix login page CSS bug"])
    priority: str = Field(default="medium", examples=["high"])
    category: str = Field(default="feature", examples=["bugfix"])
    status: str = Field(default="pending", examples=["pending"])
    completed_at: str | None = Field(default=None)
    result: str | None = Field(default=None)
    created_at: str | None = Field(default=None)


class GoalListResponseSchema(_BaseSchema):
    """List of orchestrator goals with summary counts."""

    goals: list[GoalSchema]
    total: int = Field(default=0, ge=0)
    pending: int = Field(default=0, ge=0)
    completed: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)


class GoalAddRequestSchema(_BaseSchema):
    """Request body for adding a new goal."""

    title: str = Field(..., min_length=3, max_length=500, examples=["Fix login page CSS bug"])
    priority: str = Field(
        default="medium",
        pattern=r"^(critical|high|medium|low)$",
        description="Goal priority",
    )
    category: str = Field(
        default="feature",
        pattern=r"^(feature|bugfix|docs|testing|infra|refactor)$",
        description="Goal category",
    )


class GoalAddResponseSchema(_BaseSchema):
    """Response after adding a new goal."""

    id: str = Field(..., examples=["g_005"])
    title: str = Field(..., examples=["Fix login page CSS bug"])
    message: str = Field(default="Goal added successfully")


class GoalDeleteResponseSchema(_BaseSchema):
    """Response after deleting a goal."""

    goal_id: str = Field(..., examples=["g_005"])
    message: str = Field(default="Goal deleted successfully")


class HealthDimensionSchema(_BaseSchema):
    """A single health dimension."""

    grade: str = Field(..., examples=["A+"])
    notes: str | None = Field(default=None)


class HealthProblemSchema(_BaseSchema):
    """A detected health problem."""

    severity: str = Field(..., examples=["medium"])
    description: str = Field(default="", examples=["Missing integration tests"])


class HealthReportSchema(_BaseSchema):
    """Full health report with dimensions and problems."""

    overall_grade: str = Field(..., examples=["A"])
    score: int = Field(..., ge=0, le=100, examples=[93])
    dimensions: dict[str, HealthDimensionSchema] = Field(default_factory=dict)
    problems: list[HealthProblemSchema] = Field(default_factory=list)


class MilestoneSchema(_BaseSchema):
    """A single milestone within a phase."""

    text: str = Field(..., examples=["Implement Scout Agent"])
    done: bool = Field(default=False)


class PhaseSchema(_BaseSchema):
    """A project phase from vision.md."""

    number: int = Field(..., examples=[1])
    title: str = Field(..., examples=["Foundation"])
    is_future: bool = Field(default=False)
    milestones: list[MilestoneSchema] = Field(default_factory=list)


class LogLineSchema(_BaseSchema):
    """A single runner log line."""

    line: str = Field(...)
    level: str = Field(default="INFO", examples=["INFO"])
    timestamp: str | None = Field(default=None)


class LogResponseSchema(_BaseSchema):
    """Runner log tail response."""

    lines: list[LogLineSchema] = Field(default_factory=list)
    total: int = Field(default=0, ge=0)
    log_file: str | None = Field(default=None)


# =============================================================================
# Settings / Credentials schemas
# =============================================================================


class JobScanRequestSchema(_BaseSchema):
    """Request body for triggering a manual Scout scan."""

    platform: str = Field(
        default="all",
        pattern=r"^(freelancer|upwork|fl_ru|kwork|all)$",
        description="Platform to scan (default: all)",
    )


class PipelineBScanRequestSchema(_BaseSchema):
    """Request body for triggering a Pipeline B geo scan."""

    city: str = Field(
        ...,
        min_length=1,
        max_length=100,
        examples=["Berlin"],
        description="City name to scan for offline businesses",
    )


class CredentialTestRequestSchema(_BaseSchema):
    """Request to test a credential/API key."""

    key_name: str = Field(
        ...,
        examples=["gemini_api_key"],
        description="Name of the API key or platform to test",
    )


class CredentialTestResponseSchema(_BaseSchema):
    """Result of credential test."""

    key_name: str = Field(..., examples=["gemini_api_key"])
    success: bool = Field(..., description="Whether the credential is valid")
    message: str = Field(
        ...,
        examples=["API key is valid"],
        description="Human-readable result",
    )
    latency_ms: int | None = Field(
        default=None, description="Response time in milliseconds",
    )


class PlatformAccountCreateSchema(_BaseSchema):
    """Create a new platform account with encrypted credentials."""
    platform: str = Field(
        ...,
        pattern=r"^(freelancer|upwork|fl_ru|kwork)$",
        examples=["freelancer"],
        description="Platform identifier",
    )
    username: str | None = Field(default=None, max_length=255, examples=["myuser"])
    credentials: dict[str, Any] = Field(
        ...,
        description="Platform-specific credentials (will be encrypted at rest)",
        examples=[{"client_id": "abc", "client_secret": "xyz"}],
    )
    profile_url: str | None = Field(default=None, max_length=1000)


class PlatformAccountUpdateSchema(_BaseSchema):
    """Update an existing platform account."""
    username: str | None = Field(default=None, max_length=255)
    credentials: dict[str, Any] | None = Field(
        default=None,
        description="New credentials (will be encrypted); omit to keep existing",
    )
    profile_url: str | None = Field(default=None, max_length=1000)
    status: str | None = Field(
        default=None,
        pattern=r"^(active|suspended|rate_limited)$",
    )


class PlatformAccountResponseSchema(_BaseSchema):
    """Platform account with masked credentials."""
    id: uuid.UUID
    platform: str = Field(..., examples=["freelancer"])
    username: str | None = None
    status: str = Field(default="active", examples=["active"])
    profile_url: str | None = None
    stats: dict[str, Any] = Field(default_factory=dict)
    last_health_check: datetime | None = None
    created_at: datetime
    updated_at: datetime


class PlatformAccountListResponseSchema(_BaseSchema):
    """List of platform accounts."""
    accounts: list[PlatformAccountResponseSchema]
    total: int = Field(default=0, ge=0)


class APIKeyStatusSchema(_BaseSchema):
    """Status of a single API key (never exposes the actual key)."""
    key_name: str = Field(..., examples=["gemini_api_key"])
    display_name: str = Field(..., examples=["Gemini API"])
    configured: bool = Field(default=False)
    masked: str | None = Field(default=None, examples=["***abc123"])


class APIKeySaveSchema(_BaseSchema):
    """Save/update API keys. Only non-null fields are updated."""
    openrouter_api_key: str | None = Field(default=None, max_length=500)
    gemini_api_key: str | None = Field(default=None, max_length=500)
    anthropic_api_key: str | None = Field(default=None, max_length=500)
    openai_api_key: str | None = Field(default=None, max_length=500)
    e2b_api_key: str | None = Field(default=None, max_length=500)
    hunter_api_key: str | None = Field(default=None, max_length=500)
    apollo_api_key: str | None = Field(default=None, max_length=500)
    freelancer_client_id: str | None = Field(default=None, max_length=500)
    freelancer_client_secret: str | None = Field(default=None, max_length=500)
    brightdata_username: str | None = Field(default=None, max_length=500)
    brightdata_password: str | None = Field(default=None, max_length=500)


class CredentialsSummarySchema(_BaseSchema):
    """Overall credentials status for the settings page."""
    api_keys: list[APIKeyStatusSchema] = Field(default_factory=list)
    platform_accounts: list[PlatformAccountResponseSchema] = Field(default_factory=list)
