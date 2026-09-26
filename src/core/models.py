"""SQLAlchemy 2.0 ORM models for the Multi-Agent Service.

All 21 tables from ``docs/Full_work/specs/database-spec.md`` are defined here using the
modern ``Mapped`` / ``mapped_column`` annotation style.

Key conventions:
- UUIDs as primary keys (``gen_random_uuid()`` server-side default)
- ``Vector(3072)`` for qwen3-embedding-8b embedding columns (Matryoshka)
- HNSW indexes for vector columns (pgvector)
- ``datetime.now(timezone.utc)`` for Python-side timestamp defaults
- Async Alembic with asyncpg for migrations
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""

    type_annotation_map = {
        dict[str, Any]: JSONB,
    }


# ---------------------------------------------------------------------------
# Helper for UTC-now defaults
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Soft-delete mixin
# ---------------------------------------------------------------------------


class SoftDeleteMixin:
    """Mixin adding soft-delete support via ``deleted_at`` column.

    Models using this mixin can be soft-deleted by setting ``deleted_at`` to a
    UTC timestamp.  A ``NULL`` value means the row is live.  The column is
    indexed with a partial index (``WHERE deleted_at IS NOT NULL``) so that
    queries filtering on soft-deleted rows remain fast without penalising
    normal reads.
    """

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )


# ---------------------------------------------------------------------------
# 1. users
# ---------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str | None] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(20), default="owner", server_default="owner")
    status: Mapped[str] = mapped_column(String(20), default="active", server_default="active")
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships
    platform_accounts: Mapped[list[PlatformAccount]] = relationship(back_populates="user", cascade="all, delete-orphan")
    hitl_resolutions: Mapped[list[HITLQueue]] = relationship(back_populates="resolved_by_user")

    __table_args__ = (
        Index("idx_users_email", "email"),
        Index("idx_users_status", "status"),
    )


# ---------------------------------------------------------------------------
# 2. platform_accounts
# ---------------------------------------------------------------------------


class PlatformAccount(Base):
    __tablename__ = "platform_accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    username: Mapped[str | None] = mapped_column(String(255))
    credentials: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active", server_default="active")
    profile_url: Mapped[str | None] = mapped_column(Text)
    stats: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    last_health_check: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    # Relationships
    user: Mapped[User] = relationship(back_populates="platform_accounts")
    bids: Mapped[list[Bid]] = relationship(back_populates="account")

    __table_args__ = (Index("idx_accounts_platform", "platform", "status"),)


# ---------------------------------------------------------------------------
# 3. jobs
# ---------------------------------------------------------------------------


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    platform: Mapped[str] = mapped_column(String(50), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    budget_min: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    budget_max: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    budget_type: Mapped[str | None] = mapped_column(String(20))
    currency: Mapped[str] = mapped_column(String(3), default="USD", server_default="USD")
    client_info: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    skills_required: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    platform_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Processing status
    status: Mapped[str] = mapped_column(String(30), default="new", server_default="new")
    score: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    disqualify_reason: Mapped[str | None] = mapped_column(String(255))

    # Metadata
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    url: Mapped[str | None] = mapped_column(Text)
    raw_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # Relationships
    bids: Mapped[list[Bid]] = relationship(back_populates="job", cascade="all, delete-orphan")
    projects: Mapped[list[Project]] = relationship(back_populates="job")
    agent_logs: Mapped[list[AgentLog]] = relationship(back_populates="job")
    ab_test_results: Mapped[list[ABTestResult]] = relationship(back_populates="job")

    __table_args__ = (
        UniqueConstraint("platform", "external_id", name="uq_jobs_platform_external_id"),
        Index("idx_jobs_status", "status", "discovered_at"),
        Index("idx_jobs_platform", "platform", "status"),
        Index("idx_jobs_score", "score", postgresql_where="status = 'qualified'"),
    )


# ---------------------------------------------------------------------------
# 4. bids
# ---------------------------------------------------------------------------


class Bid(Base):
    __tablename__ = "bids"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("platform_accounts.id"))

    # Proposal content
    proposal_text: Mapped[str] = mapped_column(Text, nullable=False)
    bid_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    estimated_days: Mapped[int | None] = mapped_column(Integer)
    portfolio_items: Mapped[list[uuid.UUID] | None] = mapped_column(ARRAY(Text))

    # Platform reference
    platform_bid_id: Mapped[str | None] = mapped_column(String(255))
    platform_thread_id: Mapped[str | None] = mapped_column(String(255))
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    client_telegram_id: Mapped[int | None] = mapped_column(BigInteger)

    # Status
    status: Mapped[str] = mapped_column(String(30), default="draft", server_default="draft")
    hitl_request_id: Mapped[uuid.UUID | None] = mapped_column()

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    response_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Analytics
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    generation_model: Mapped[str | None] = mapped_column(String(50))

    # Relationships
    job: Mapped[Job] = relationship(back_populates="bids")
    account: Mapped[PlatformAccount | None] = relationship(back_populates="bids")
    project: Mapped[Project | None] = relationship(back_populates="bid", uselist=False)
    ab_test_results: Mapped[list[ABTestResult]] = relationship(back_populates="bid")

    __table_args__ = (
        UniqueConstraint("job_id", "account_id", name="uq_bids_job_account"),
        Index("idx_bids_status", "status"),
        Index("idx_bids_job", "job_id"),
        Index("idx_bids_platform_bid_id", "platform_bid_id"),
    )


# ---------------------------------------------------------------------------
# 5. projects
# ---------------------------------------------------------------------------


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    bid_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bids.id"))
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id"))

    # Project info
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    type: Mapped[str] = mapped_column(String(30), default="freelance", server_default="freelance")
    client_name: Mapped[str | None] = mapped_column(String(255))
    client_contact: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # Financial
    agreed_amount: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3), default="USD", server_default="USD")
    paid_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), default=Decimal("0"), server_default="0")

    # Timeline
    start_date: Mapped[date | None] = mapped_column(Date)
    deadline: Mapped[date | None] = mapped_column(Date)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Status
    status: Mapped[str] = mapped_column(String(30), default="planning", server_default="planning")
    progress: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    revision_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # Kanban position
    kanban_column: Mapped[str | None] = mapped_column(String(30))
    kanban_order: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    # Relationships
    bid: Mapped[Bid | None] = relationship(back_populates="project")
    job: Mapped[Job | None] = relationship(back_populates="projects")
    tasks: Mapped[list[Task]] = relationship(back_populates="project", cascade="all, delete-orphan")
    artifacts: Mapped[list[Artifact]] = relationship(back_populates="project", cascade="all, delete-orphan")
    revisions: Mapped[list[Revision]] = relationship(back_populates="project", cascade="all, delete-orphan")
    hitl_items: Mapped[list[HITLQueue]] = relationship(back_populates="project")
    agent_logs: Mapped[list[AgentLog]] = relationship(back_populates="project")

    __table_args__ = (
        Index("idx_projects_status", "status"),
        Index("idx_projects_type", "type", "status"),
        Index("idx_projects_deadline", "deadline", postgresql_where="status NOT IN ('completed', 'cancelled')"),
    )


# ---------------------------------------------------------------------------
# 6. tasks
# ---------------------------------------------------------------------------


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    parent_task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"))

    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    assigned_agent: Mapped[str | None] = mapped_column(String(30))

    # Execution
    status: Mapped[str] = mapped_column(String(30), default="pending", server_default="pending")
    priority: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    estimated_hours: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    actual_hours: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))

    # Dependencies
    depends_on: Mapped[list[str] | None] = mapped_column(ARRAY(Text))

    # Output
    artifact_ids: Mapped[list[str] | None] = mapped_column(ARRAY(Text))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships
    project: Mapped[Project] = relationship(back_populates="tasks")
    parent_task: Mapped[Task | None] = relationship(remote_side=[id], backref="subtasks")
    artifacts: Mapped[list[Artifact]] = relationship(back_populates="task")
    hitl_items: Mapped[list[HITLQueue]] = relationship(back_populates="task")
    agent_logs: Mapped[list[AgentLog]] = relationship(back_populates="task")

    __table_args__ = (
        Index("idx_tasks_project", "project_id", "status"),
        Index("idx_tasks_agent", "assigned_agent", "status"),
    )


# ---------------------------------------------------------------------------
# 7. artifacts
# ---------------------------------------------------------------------------


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"))

    type: Mapped[str] = mapped_column(String(30), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(100))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)

    # Storage
    storage_type: Mapped[str] = mapped_column(String(20), default="db", server_default="db")
    content: Mapped[bytes | None] = mapped_column(LargeBinary)
    storage_url: Mapped[str | None] = mapped_column(Text)

    # Versioning
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    previous_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("artifacts.id"))

    # Metadata
    generated_by: Mapped[str | None] = mapped_column(String(30))
    llm_model: Mapped[str | None] = mapped_column(String(50))
    quality_score: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    security_scan: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    # Relationships
    project: Mapped[Project] = relationship(back_populates="artifacts")
    task: Mapped[Task | None] = relationship(back_populates="artifacts")
    previous_version: Mapped[Artifact | None] = relationship(remote_side=[id])

    __table_args__ = (
        Index("idx_artifacts_project", "project_id"),
        Index("idx_artifacts_type", "type"),
    )


# ---------------------------------------------------------------------------
# 8. hitl_queue
# ---------------------------------------------------------------------------


class HITLQueue(SoftDeleteMixin, Base):
    __tablename__ = "hitl_queue"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    type: Mapped[str] = mapped_column(String(30), nullable=False)
    priority: Mapped[str] = mapped_column(String(10), default="normal", server_default="normal")

    # Related entities
    bid_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bids.id"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"))
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"))

    # Content
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    # Actions
    available_actions: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)

    # Status
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    resolution: Mapped[str | None] = mapped_column(String(30))
    resolution_note: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))

    # Timing
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Notifications
    telegram_sent: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    email_sent: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    # Relationships
    project: Mapped[Project | None] = relationship(back_populates="hitl_items")
    task: Mapped[Task | None] = relationship(back_populates="hitl_items")
    resolved_by_user: Mapped[User | None] = relationship(back_populates="hitl_resolutions")
    edit_history: Mapped[list[HITLEditHistory]] = relationship(
        back_populates="hitl_item",
        cascade="all, delete-orphan",
        order_by="HITLEditHistory.edited_at",
    )

    __table_args__ = (
        Index("idx_hitl_status", "status", "priority", "created_at"),
        Index("idx_hitl_type", "type", "status"),
        Index("idx_hitl_queue_deleted_at", "deleted_at", postgresql_where=text("deleted_at IS NOT NULL")),
    )


# ---------------------------------------------------------------------------
# 8b. hitl_edit_history — tracks payload changes on HITL edits
# ---------------------------------------------------------------------------


class HITLEditHistory(Base):
    """Tracks payload changes when HITL items are edited during resolution."""

    __tablename__ = "hitl_edit_history"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    hitl_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("hitl_queue.id", ondelete="CASCADE"),
        nullable=False,
    )
    edited_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    before_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    after_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    edit_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        doc="field_edit | full_replace | action_edit",
    )
    edited_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    # Relationships
    hitl_item: Mapped[HITLQueue] = relationship(
        back_populates="edit_history",
        foreign_keys=[hitl_id],
    )
    editor: Mapped[User | None] = relationship(foreign_keys=[edited_by])

    __table_args__ = (
        Index("idx_hitl_edit_history_hitl_id", "hitl_id"),
        Index("idx_hitl_edit_history_edited_at", "edited_at"),
    )


# ---------------------------------------------------------------------------
# 9. revisions
# ---------------------------------------------------------------------------


class Revision(Base):
    __tablename__ = "revisions"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)

    # Classification
    type: Mapped[str] = mapped_column(String(20), nullable=False)

    # Content
    client_feedback: Mapped[str] = mapped_column(Text, nullable=False)
    parsed_items: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # Processing
    status: Mapped[str] = mapped_column(String(20), default="new", server_default="new")
    assigned_agent: Mapped[str | None] = mapped_column(String(30))
    hitl_required: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    # Resolution
    resolution_note: Mapped[str | None] = mapped_column(Text)
    tasks_created: Mapped[list[str] | None] = mapped_column(ARRAY(Text))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships
    project: Mapped[Project] = relationship(back_populates="revisions")

    __table_args__ = (Index("idx_revisions_project", "project_id", "status"),)


# ---------------------------------------------------------------------------
# 10. leads
# ---------------------------------------------------------------------------


class Lead(Base):
    __tablename__ = "leads"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    # Business info
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str | None] = mapped_column(String(100))
    address: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(String(100))
    country: Mapped[str | None] = mapped_column(String(50))

    # Geo
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 8))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(11, 8))
    h3_index: Mapped[str | None] = mapped_column(String(20))

    # Contact
    phone: Mapped[str | None] = mapped_column(String(50))
    email: Mapped[str | None] = mapped_column(String(255))
    website: Mapped[str | None] = mapped_column(String(255))
    telegram_username: Mapped[str | None] = mapped_column(String(100))
    social_links: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # Enrichment
    enrichment_source: Mapped[str | None] = mapped_column(String(20))
    enrichment_cost: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    enrichment_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # Status
    status: Mapped[str] = mapped_column(String(20), default="new", server_default="new")

    # Scoring (pipeline-b-spec)
    lead_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    temperature: Mapped[str | None] = mapped_column(String(20), nullable=True)  # hot/warm/cold

    # Touch tracking
    touch_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_contacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    channel_used: Mapped[str | None] = mapped_column(String(50), nullable=True)  # email/telegram/platform

    # External data
    google_rating: Mapped[Decimal | None] = mapped_column(Numeric(2, 1), nullable=True)
    review_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Source
    source: Mapped[str | None] = mapped_column(String(100), nullable=True)  # geo_scanner/web_search/telegram
    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    osm_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), onupdate=_utcnow, nullable=True)

    # Pipeline B extensions
    touch_state: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )  # pending/active/replied/completed/stopped/paused
    next_touch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    battlecard_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    scoring_rules: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    analysis_tier: Mapped[str | None] = mapped_column(String(10), default="quick", nullable=True)

    # Relationships
    campaign_links: Mapped[list[CampaignLead]] = relationship(back_populates="lead", cascade="all, delete-orphan")
    touch_history: Mapped[list[TouchHistory]] = relationship(back_populates="lead", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_leads_city", "city", "category", "status"),
        Index("idx_leads_h3", "h3_index"),
        Index("idx_leads_status", "status"),
        Index("idx_leads_temperature", "temperature"),
        Index("idx_leads_touch_state", "touch_state"),
    )


# ---------------------------------------------------------------------------
# 11. email_campaigns
# ---------------------------------------------------------------------------


class EmailCampaign(Base):
    __tablename__ = "email_campaigns"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    subject_template: Mapped[str] = mapped_column(Text, nullable=False)
    body_template: Mapped[str] = mapped_column(Text, nullable=False)

    # Targeting
    target_cities: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    target_categories: Mapped[list[str] | None] = mapped_column(ARRAY(Text))

    # Status
    status: Mapped[str] = mapped_column(String(20), default="draft", server_default="draft")

    # Stats
    total_leads: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    sent_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    open_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    reply_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    bounce_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships
    campaign_leads: Mapped[list[CampaignLead]] = relationship(
        back_populates="campaign",
        cascade="all, delete-orphan",
    )


# ---------------------------------------------------------------------------
# 11b. campaign_leads (association table)
# ---------------------------------------------------------------------------


class CampaignLead(Base):
    __tablename__ = "campaign_leads"

    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("email_campaigns.id", ondelete="CASCADE"),
        primary_key=True,
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"),
        primary_key=True,
    )

    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    channel_type: Mapped[str] = mapped_column(String(20), default="email", server_default="email")
    personalized_subject: Mapped[str | None] = mapped_column(Text)
    personalized_body: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships
    campaign: Mapped[EmailCampaign] = relationship(back_populates="campaign_leads")
    lead: Mapped[Lead] = relationship(back_populates="campaign_links")

    __table_args__ = (Index("idx_campaign_leads_channel", "channel_type"),)


# ---------------------------------------------------------------------------
# 12. agent_logs
# ---------------------------------------------------------------------------


class AgentLog(SoftDeleteMixin, Base):
    __tablename__ = "agent_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    agent_name: Mapped[str] = mapped_column(String(30), nullable=False)

    # Context
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id"))
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"))
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id"))

    # Event
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    message: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    # LLM tracking
    llm_model: Mapped[str | None] = mapped_column(String(50))
    tokens_input: Mapped[int | None] = mapped_column(Integer)
    tokens_output: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    latency_ms: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    # Relationships
    project: Mapped[Project | None] = relationship(back_populates="agent_logs")
    task: Mapped[Task | None] = relationship(back_populates="agent_logs")
    job: Mapped[Job | None] = relationship(back_populates="agent_logs")

    __table_args__ = (
        Index("idx_agent_logs_agent", "agent_name", "created_at"),
        Index("idx_agent_logs_project", "project_id", "created_at"),
        Index("idx_agent_logs_event", "event_type", "created_at"),
        Index("idx_agent_logs_deleted_at", "deleted_at", postgresql_where=text("deleted_at IS NOT NULL")),
    )


# ---------------------------------------------------------------------------
# 13. agent_heartbeats
# ---------------------------------------------------------------------------


class AgentHeartbeat(Base):
    __tablename__ = "agent_heartbeats"

    agent_name: Mapped[str] = mapped_column(String(30), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), default="idle", server_default="idle")
    current_task: Mapped[str | None] = mapped_column(Text)
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    restart_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error_message: Mapped[str | None] = mapped_column(Text)
    heartbeat_metadata: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB)


# ---------------------------------------------------------------------------
# 14. langgraph_checkpoints
# ---------------------------------------------------------------------------


class LanggraphCheckpoint(Base):
    __tablename__ = "langgraph_checkpoints"

    thread_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    checkpoint_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    parent_checkpoint_id: Mapped[str | None] = mapped_column(String(255))
    state_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB)
    current_agent: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str | None] = mapped_column(String(50))
    requires_hitl: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (Index("idx_checkpoints_thread", "thread_id", "created_at"),)


# ---------------------------------------------------------------------------
# 14b. langgraph_checkpoint_history
# ---------------------------------------------------------------------------


class LanggraphCheckpointHistory(Base):
    __tablename__ = "langgraph_checkpoint_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    thread_id: Mapped[str] = mapped_column(String(255), nullable=False)
    checkpoint_id: Mapped[str] = mapped_column(String(255), nullable=False)
    state_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        default=_utcnow,
    )

    __table_args__ = (
        Index("idx_checkpoint_history_thread", "thread_id", "created_at"),
        Index("idx_checkpoint_history_checkpoint", "thread_id", "checkpoint_id"),
    )


# ---------------------------------------------------------------------------
# 15. knowledge_base
# ---------------------------------------------------------------------------


class KnowledgeBase(Base):
    __tablename__ = "knowledge_base"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    type: Mapped[str] = mapped_column(String(30), nullable=False)
    category: Mapped[str | None] = mapped_column(String(100))

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Embedding for RAG — Qwen3-Embedding-8B (3072 dim, Matryoshka)
    embedding = mapped_column(Vector(3072))

    # Stats
    usage_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    success_rate: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        Index("idx_knowledge_type", "type", "category"),
        # HNSW index for vector search (pgvector).
        # Created via raw DDL in init migration or init.sql.
    )


# ---------------------------------------------------------------------------
# 16. semantic_cache
# ---------------------------------------------------------------------------


class SemanticCache(Base):
    __tablename__ = "semantic_cache"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    query_hash: Mapped[str | None] = mapped_column(Text, unique=True)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    response: Mapped[str] = mapped_column(Text, nullable=False)
    query_type: Mapped[str] = mapped_column(Text, default="default", server_default="default")

    # Embedding — Qwen3-Embedding-8B (3072 dim, Matryoshka)
    embedding = mapped_column(Vector(3072))

    hit_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("idx_cache_expires", "expires_at"),
        Index("idx_cache_type", "query_type"),
        # HNSW index for embedding created via raw DDL (see init migration).
    )


# ---------------------------------------------------------------------------
# 17. ab_test_results
# ---------------------------------------------------------------------------


class ABTestResult(SoftDeleteMixin, Base):
    __tablename__ = "ab_test_results"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    test_name: Mapped[str] = mapped_column(String(100), nullable=False)
    variant_id: Mapped[str] = mapped_column(String(50), nullable=False)
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id"))
    bid_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bids.id"))

    # Metrics
    impressions: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    responses: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    hires: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # Metadata
    template_name: Mapped[str | None] = mapped_column(String(100))
    opening_style: Mapped[str | None] = mapped_column(String(30))
    tone: Mapped[str | None] = mapped_column(String(30))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    # Relationships
    job: Mapped[Job | None] = relationship(back_populates="ab_test_results")
    bid: Mapped[Bid | None] = relationship(back_populates="ab_test_results")

    __table_args__ = (
        Index("idx_ab_test", "test_name", "variant_id"),
        Index("idx_ab_test_results_deleted_at", "deleted_at", postgresql_where=text("deleted_at IS NOT NULL")),
    )


# ---------------------------------------------------------------------------
# 18. orchestrator_goals
# ---------------------------------------------------------------------------


class OrchestratorGoal(Base):
    """Orchestrator goal, shared between dashboard (Railway) and bot (local)."""

    __tablename__ = "orchestrator_goals"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    goal_id: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    priority: Mapped[str] = mapped_column(String(20), default="medium", server_default="medium")
    category: Mapped[str] = mapped_column(String(30), default="feature", server_default="feature")
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    result: Mapped[str | None] = mapped_column(Text)
    context: Mapped[str | None] = mapped_column(Text)
    success_criteria: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    depends_on: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("idx_orch_goals_status", "status"),
        Index("idx_orch_goals_goal_id", "goal_id"),
    )


# ---------------------------------------------------------------------------
# 19. portfolio_projects
# ---------------------------------------------------------------------------


class PortfolioProject(Base):
    """Portfolio project used as social proof in bids."""

    __tablename__ = "portfolio_projects"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    platform: Mapped[str] = mapped_column(String(50), default="direct", server_default="direct")
    status: Mapped[str] = mapped_column(String(20), default="draft", server_default="draft")
    description: Mapped[str | None] = mapped_column(Text)
    tech_stack: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    url: Mapped[str | None] = mapped_column(Text)
    thumbnail_url: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    __table_args__ = (Index("idx_portfolio_platform", "platform", "status"),)


# ---------------------------------------------------------------------------
# 20. telegram_channels
# ---------------------------------------------------------------------------


class TelegramChannel(Base):
    """Telegram channels monitored for freelance job postings."""

    __tablename__ = "telegram_channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    title: Mapped[str | None] = mapped_column(String(500))
    category: Mapped[str | None] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    __table_args__ = (Index("idx_tg_channels_active", "active"),)


# ---------------------------------------------------------------------------
# 20. deals
# ---------------------------------------------------------------------------


class Deal(Base):
    """Pipeline B deal — links a won lead to a Pipeline A development cycle."""

    __tablename__ = "deals"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )

    # Link to originating lead (optional — deals can be created manually)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("leads.id", ondelete="SET NULL"),
    )

    # Deal info
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        default="new",
        server_default="new",
    )
    agreed_scope: Mapped[str | None] = mapped_column(Text)
    budget: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Context from Pipeline B conversations
    client_context: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    design_versions: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    conversation_history: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)

    # Pipeline A linkage
    pipeline_a_thread_id: Mapped[str | None] = mapped_column(String(100))

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    # Relationships
    lead: Mapped[Lead | None] = relationship()

    __table_args__ = (
        Index("idx_deals_status", "status"),
        Index("idx_deals_lead_id", "lead_id"),
    )


# ---------------------------------------------------------------------------
# 19. scheduled_messages
# ---------------------------------------------------------------------------


class ScheduledMessage(SoftDeleteMixin, Base):
    """Scheduled progress messages for delivery throttling (execution cloaking)."""

    __tablename__ = "scheduled_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    project_id: Mapped[str] = mapped_column(String(255), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(255), nullable=False)
    send_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str] = mapped_column(String(20), default="platform", server_default="platform")
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_scheduled_messages_status_send_at", "status", "send_at"),
        Index("idx_scheduled_messages_thread", "thread_id"),
        Index("idx_scheduled_messages_deleted_at", "deleted_at", postgresql_where=text("deleted_at IS NOT NULL")),
    )


# ---------------------------------------------------------------------------
# 22. email_suppression_list
# ---------------------------------------------------------------------------


class EmailSuppressionEntry(Base):
    """CAN-SPAM / GDPR email suppression list."""

    __tablename__ = "email_suppression_list"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    reason: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # hard_bounce | unsubscribe | complaint | manual | gdpr_erasure
    source: Mapped[str] = mapped_column(String(100), nullable=False)  # bounce_handler | user_request | admin | webhook
    suppressed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("idx_suppression_email", "email"),)


# ---------------------------------------------------------------------------
# 23. client_messages
# ---------------------------------------------------------------------------


class ClientMessage(Base):
    """Inbound/outbound messages exchanged during bid negotiation.

    Captures every platform message, AI-generated response, and operator
    intervention for the Negotiation Engine.  Linked to a bid and optionally
    to a project (once the deal is accepted).
    """

    __tablename__ = "client_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    bid_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("bids.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"),
    )
    direction: Mapped[str] = mapped_column(String(10))  # inbound / outbound
    sender: Mapped[str] = mapped_column(String(20))  # client / ai / operator
    message_type: Mapped[str | None] = mapped_column(String(30))  # classification result
    content: Mapped[str] = mapped_column(Text, nullable=False)
    platform: Mapped[str | None] = mapped_column(String(50))
    external_id: Mapped[str | None] = mapped_column(String(255))
    auto_generated: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    hitl_reviewed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    hitl_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("hitl_queue.id", ondelete="SET NULL"),
    )
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata",
        type_=JSONB,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    __table_args__ = (
        Index("idx_client_messages_bid_created", "bid_id", "created_at"),
        Index("idx_client_messages_platform_ext", "platform", "external_id"),
        Index("idx_client_messages_bid_dir_created", "bid_id", "direction", "created_at"),
    )


# ---------------------------------------------------------------------------
# 24. negotiations
# ---------------------------------------------------------------------------


class Negotiation(Base):
    """State-machine record tracking negotiation progress per bid.

    One negotiation per bid (unique constraint).  Stores the FSM state,
    financial terms, round counter, follow-up tracking, and a JSONB history
    of all state transitions for audit.
    """

    __tablename__ = "negotiations"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    bid_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("bids.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    state: Mapped[str] = mapped_column(String(30), default="initial", server_default="initial", nullable=False)
    previous_state: Mapped[str | None] = mapped_column(String(30))
    state_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    state_reason: Mapped[str | None] = mapped_column(Text)

    # Financial terms
    original_amount: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    current_amount: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    final_amount: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))

    # Counters
    rounds: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    followup_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_followup_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Outcome
    outcome: Mapped[str | None] = mapped_column(String(20))  # won / lost / stale / cancelled

    # Audit
    history: Mapped[dict[str, Any]] = mapped_column(JSONB, default=list, server_default="[]")
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata",
        type_=JSONB,
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "idx_negotiations_active_state",
            "state",
            postgresql_where=text("state NOT IN ('won', 'lost')"),
        ),
        Index("idx_negotiations_bid", "bid_id"),
    )


# ---------------------------------------------------------------------------
# 25. telegram_user_profiles
# ---------------------------------------------------------------------------


class TelegramUserProfile(Base):
    """Telegram user profile aggregated from channel messages."""

    __tablename__ = "telegram_user_profiles"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(255))
    first_name: Mapped[str | None] = mapped_column(String(255))
    last_name: Mapped[str | None] = mapped_column(String(255))
    messages_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    channels: Mapped[dict[str, Any] | None] = mapped_column(JSONB, server_default="[]")
    score: Mapped[float] = mapped_column(Float, default=0.0, server_default="0.0")
    needs: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="new", server_default="new")
    bio: Mapped[str | None] = mapped_column(Text)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), onupdate=_utcnow, nullable=True)

    __table_args__ = (Index("idx_tg_profile_status_score", "status", "score"),)


# ---------------------------------------------------------------------------
# 26. touch_history
# ---------------------------------------------------------------------------


class TouchHistory(Base):
    """Tracks each outreach touch in a multi-step sequence for a lead."""

    __tablename__ = "touch_history"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"),
        nullable=False,
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    template: Mapped[str] = mapped_column(String(50), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    external_id: Mapped[str | None] = mapped_column(String(255))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    # Relationships
    lead: Mapped[Lead] = relationship(back_populates="touch_history")

    __table_args__ = (Index("idx_touch_history_lead_created", "lead_id", "created_at"),)


# ---------------------------------------------------------------------------
# 27. telegram_notification_prefs
# ---------------------------------------------------------------------------


class TelegramNotificationPref(Base):
    """Per-user notification preferences for Telegram alerts."""

    __tablename__ = "telegram_notification_prefs"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    notification_types: Mapped[list[str] | None] = mapped_column(
        ARRAY(Text),
        server_default=text(
            "ARRAY['bid_approval','dev_launch','final_review','design_review','concept_review','escalation']::text[]"
        ),
    )
    quiet_hours_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    quiet_hours_start: Mapped[int] = mapped_column(Integer, default=23, server_default="23")
    quiet_hours_end: Mapped[int] = mapped_column(Integer, default=8, server_default="8")
    timezone: Mapped[str] = mapped_column(String(50), default="Europe/Moscow", server_default="Europe/Moscow")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), onupdate=_utcnow, nullable=True)

    # Relationships
    user: Mapped[User] = relationship()


# ---------------------------------------------------------------------------
# 28. telegram_notification_log
# ---------------------------------------------------------------------------


class TelegramNotificationLog(Base):
    """Audit log for Telegram notifications sent to users."""

    __tablename__ = "telegram_notification_log"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"),
    )
    hitl_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("hitl_queue.id", ondelete="SET NULL"),
    )
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    chat_id: Mapped[int | None] = mapped_column(BigInteger)
    notification_type: Mapped[str] = mapped_column(String(50), nullable=False)
    content_preview: Mapped[str | None] = mapped_column(String(200))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    action_taken: Mapped[str | None] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    # Relationships
    user: Mapped[User | None] = relationship()
    hitl_item: Mapped[HITLQueue | None] = relationship()

    __table_args__ = (
        Index("idx_tg_notif_log_user", "user_id"),
        Index("idx_tg_notif_log_hitl", "hitl_id"),
        Index("idx_tg_notif_log_sent", "sent_at"),
    )


# ---------------------------------------------------------------------------
# 29. client_context
# ---------------------------------------------------------------------------


class ClientContext(Base):
    """Accumulated client context for a deal in Pipeline B negotiations."""

    __tablename__ = "client_context"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"),
        nullable=False,
    )
    deal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("deals.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    key_facts: Mapped[dict[str, Any] | None] = mapped_column(JSONB, server_default="{}")
    agreed_scope: Mapped[str | None] = mapped_column(Text)
    decisions: Mapped[dict[str, Any] | None] = mapped_column(JSONB, server_default="[]")
    design_versions: Mapped[dict[str, Any] | None] = mapped_column(JSONB, server_default="[]")
    client_preferences: Mapped[dict[str, Any] | None] = mapped_column(JSONB, server_default="{}")
    conversation_summary: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    # Relationships
    lead: Mapped[Lead] = relationship()
    deal: Mapped[Deal] = relationship()

    __table_args__ = (Index("idx_client_context_lead", "lead_id"),)
