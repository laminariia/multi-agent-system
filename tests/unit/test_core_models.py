"""Unit tests for src/core/models.py — SQLAlchemy ORM model metadata.

Tests model table names, column presence/types, relationships, constraints, and indexes
WITHOUT requiring a database connection. Tests only model metadata.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, Boolean, Date, DateTime, Integer, LargeBinary, Numeric, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from src.core.models import (
    ABTestResult,
    AgentHeartbeat,
    AgentLog,
    Artifact,
    Base,
    Bid,
    CampaignLead,
    EmailCampaign,
    HITLQueue,
    Job,
    KnowledgeBase,
    LanggraphCheckpoint,
    Lead,
    PlatformAccount,
    Project,
    Revision,
    SemanticCache,
    Task,
    User,
    _utcnow,
)

pytestmark = pytest.mark.filterwarnings("ignore::RuntimeWarning")


class TestUtcNow:
    """Test _utcnow helper function."""

    def test_returns_utc_aware_datetime(self):
        """_utcnow returns a timezone-aware datetime in UTC."""
        now = _utcnow()
        assert isinstance(now, datetime)
        assert now.tzinfo == UTC


class TestBase:
    """Test Base declarative class."""

    def test_type_annotation_map_has_jsonb(self):
        """Base.type_annotation_map maps dict[str, Any] to JSONB."""
        assert dict[str, Any] in Base.type_annotation_map
        annotation_type = Base.type_annotation_map[dict[str, Any]]
        assert annotation_type is JSONB


class TestModelTableNames:
    """Test all model __tablename__ attributes."""

    def test_user_tablename(self):
        """User model has tablename 'users'."""
        assert User.__tablename__ == "users"

    def test_platform_account_tablename(self):
        """PlatformAccount model has tablename 'platform_accounts'."""
        assert PlatformAccount.__tablename__ == "platform_accounts"

    def test_job_tablename(self):
        """Job model has tablename 'jobs'."""
        assert Job.__tablename__ == "jobs"

    def test_bid_tablename(self):
        """Bid model has tablename 'bids'."""
        assert Bid.__tablename__ == "bids"

    def test_project_tablename(self):
        """Project model has tablename 'projects'."""
        assert Project.__tablename__ == "projects"

    def test_task_tablename(self):
        """Task model has tablename 'tasks'."""
        assert Task.__tablename__ == "tasks"

    def test_artifact_tablename(self):
        """Artifact model has tablename 'artifacts'."""
        assert Artifact.__tablename__ == "artifacts"

    def test_hitl_queue_tablename(self):
        """HITLQueue model has tablename 'hitl_queue'."""
        assert HITLQueue.__tablename__ == "hitl_queue"

    def test_revision_tablename(self):
        """Revision model has tablename 'revisions'."""
        assert Revision.__tablename__ == "revisions"

    def test_lead_tablename(self):
        """Lead model has tablename 'leads'."""
        assert Lead.__tablename__ == "leads"

    def test_email_campaign_tablename(self):
        """EmailCampaign model has tablename 'email_campaigns'."""
        assert EmailCampaign.__tablename__ == "email_campaigns"

    def test_campaign_lead_tablename(self):
        """CampaignLead model has tablename 'campaign_leads'."""
        assert CampaignLead.__tablename__ == "campaign_leads"

    def test_agent_log_tablename(self):
        """AgentLog model has tablename 'agent_logs'."""
        assert AgentLog.__tablename__ == "agent_logs"

    def test_agent_heartbeat_tablename(self):
        """AgentHeartbeat model has tablename 'agent_heartbeats'."""
        assert AgentHeartbeat.__tablename__ == "agent_heartbeats"

    def test_langgraph_checkpoint_tablename(self):
        """LanggraphCheckpoint model has tablename 'langgraph_checkpoints'."""
        assert LanggraphCheckpoint.__tablename__ == "langgraph_checkpoints"

    def test_knowledge_base_tablename(self):
        """KnowledgeBase model has tablename 'knowledge_base'."""
        assert KnowledgeBase.__tablename__ == "knowledge_base"

    def test_semantic_cache_tablename(self):
        """SemanticCache model has tablename 'semantic_cache'."""
        assert SemanticCache.__tablename__ == "semantic_cache"

    def test_ab_test_result_tablename(self):
        """ABTestResult model has tablename 'ab_test_results'."""
        assert ABTestResult.__tablename__ == "ab_test_results"


class TestUserModel:
    """Test User model metadata."""

    def test_has_id_column(self):
        """User has id column (UUID primary key)."""
        assert "id" in User.__table__.columns
        col = User.__table__.columns["id"]
        assert col.primary_key is True

    def test_has_email_column(self):
        """User has email column (String, unique, not null)."""
        assert "email" in User.__table__.columns
        col = User.__table__.columns["email"]
        assert isinstance(col.type, String)
        assert col.unique is True
        assert col.nullable is False

    def test_has_password_hash_column(self):
        """User has password_hash column (nullable String)."""
        assert "password_hash" in User.__table__.columns
        col = User.__table__.columns["password_hash"]
        assert isinstance(col.type, String)

    def test_has_name_column(self):
        """User has name column (nullable String)."""
        assert "name" in User.__table__.columns
        col = User.__table__.columns["name"]
        assert isinstance(col.type, String)

    def test_has_role_column(self):
        """User has role column (String)."""
        assert "role" in User.__table__.columns
        col = User.__table__.columns["role"]
        assert isinstance(col.type, String)

    def test_has_status_column(self):
        """User has status column (String)."""
        assert "status" in User.__table__.columns
        col = User.__table__.columns["status"]
        assert isinstance(col.type, String)

    def test_has_telegram_chat_id_column(self):
        """User has telegram_chat_id column (BigInteger)."""
        assert "telegram_chat_id" in User.__table__.columns
        col = User.__table__.columns["telegram_chat_id"]
        assert isinstance(col.type, BigInteger)

    def test_has_settings_column(self):
        """User has settings column (JSONB)."""
        assert "settings" in User.__table__.columns
        col = User.__table__.columns["settings"]
        assert isinstance(col.type, JSONB)

    def test_has_created_at_column(self):
        """User has created_at column (DateTime with timezone)."""
        assert "created_at" in User.__table__.columns
        col = User.__table__.columns["created_at"]
        assert isinstance(col.type, DateTime)

    def test_has_platform_accounts_relationship(self):
        """User has platform_accounts relationship."""
        assert "platform_accounts" in User.__mapper__.relationships

    def test_has_hitl_resolutions_relationship(self):
        """User has hitl_resolutions relationship."""
        assert "hitl_resolutions" in User.__mapper__.relationships

    def test_has_email_index(self):
        """User has index on email column."""
        indexes = {idx.name for idx in User.__table__.indexes}
        assert "idx_users_email" in indexes

    def test_has_status_index(self):
        """User has index on status column."""
        indexes = {idx.name for idx in User.__table__.indexes}
        assert "idx_users_status" in indexes


class TestJobModel:
    """Test Job model metadata."""

    def test_has_platform_column(self):
        """Job has platform column (String, not null)."""
        assert "platform" in Job.__table__.columns
        col = Job.__table__.columns["platform"]
        assert isinstance(col.type, String)
        assert col.nullable is False

    def test_has_external_id_column(self):
        """Job has external_id column (String, not null)."""
        assert "external_id" in Job.__table__.columns
        col = Job.__table__.columns["external_id"]
        assert isinstance(col.type, String)
        assert col.nullable is False

    def test_has_title_column(self):
        """Job has title column (String, not null)."""
        assert "title" in Job.__table__.columns
        col = Job.__table__.columns["title"]
        assert isinstance(col.type, String)
        assert col.nullable is False

    def test_has_description_column(self):
        """Job has description column (Text)."""
        assert "description" in Job.__table__.columns
        col = Job.__table__.columns["description"]
        assert isinstance(col.type, Text)

    def test_has_budget_columns(self):
        """Job has budget_min and budget_max columns (Numeric)."""
        assert "budget_min" in Job.__table__.columns
        assert "budget_max" in Job.__table__.columns
        assert isinstance(Job.__table__.columns["budget_min"].type, Numeric)
        assert isinstance(Job.__table__.columns["budget_max"].type, Numeric)

    def test_has_unique_constraint_on_platform_external_id(self):
        """Job has unique constraint on (platform, external_id)."""
        constraints = {c.name for c in Job.__table__.constraints if hasattr(c, "name")}
        assert "uq_jobs_platform_external_id" in constraints

    def test_has_bids_relationship(self):
        """Job has bids relationship."""
        assert "bids" in Job.__mapper__.relationships

    def test_has_projects_relationship(self):
        """Job has projects relationship."""
        assert "projects" in Job.__mapper__.relationships


class TestHITLQueueModel:
    """Test HITLQueue model metadata."""

    def test_has_type_column(self):
        """HITLQueue has type column (String, not null)."""
        assert "type" in HITLQueue.__table__.columns
        col = HITLQueue.__table__.columns["type"]
        assert isinstance(col.type, String)
        assert col.nullable is False

    def test_has_priority_column(self):
        """HITLQueue has priority column (String)."""
        assert "priority" in HITLQueue.__table__.columns
        col = HITLQueue.__table__.columns["priority"]
        assert isinstance(col.type, String)

    def test_has_title_column(self):
        """HITLQueue has title column (String, not null)."""
        assert "title" in HITLQueue.__table__.columns
        col = HITLQueue.__table__.columns["title"]
        assert isinstance(col.type, String)
        assert col.nullable is False

    def test_has_payload_column(self):
        """HITLQueue has payload column (JSONB, not null)."""
        assert "payload" in HITLQueue.__table__.columns
        col = HITLQueue.__table__.columns["payload"]
        assert isinstance(col.type, JSONB)
        assert col.nullable is False

    def test_has_available_actions_column(self):
        """HITLQueue has available_actions column (ARRAY, not null)."""
        assert "available_actions" in HITLQueue.__table__.columns
        col = HITLQueue.__table__.columns["available_actions"]
        assert isinstance(col.type, ARRAY)
        assert col.nullable is False

    def test_has_status_column(self):
        """HITLQueue has status column (String)."""
        assert "status" in HITLQueue.__table__.columns
        col = HITLQueue.__table__.columns["status"]
        assert isinstance(col.type, String)

    def test_has_resolution_column(self):
        """HITLQueue has resolution column (nullable String)."""
        assert "resolution" in HITLQueue.__table__.columns
        col = HITLQueue.__table__.columns["resolution"]
        assert isinstance(col.type, String)

    def test_has_telegram_sent_column(self):
        """HITLQueue has telegram_sent column (Boolean)."""
        assert "telegram_sent" in HITLQueue.__table__.columns
        col = HITLQueue.__table__.columns["telegram_sent"]
        assert isinstance(col.type, Boolean)

    def test_has_status_index(self):
        """HITLQueue has index on (status, priority, created_at)."""
        indexes = {idx.name for idx in HITLQueue.__table__.indexes}
        assert "idx_hitl_status" in indexes


class TestKnowledgeBaseModel:
    """Test KnowledgeBase model metadata."""

    def test_has_type_column(self):
        """KnowledgeBase has type column (String, not null)."""
        assert "type" in KnowledgeBase.__table__.columns
        col = KnowledgeBase.__table__.columns["type"]
        assert isinstance(col.type, String)
        assert col.nullable is False

    def test_has_title_column(self):
        """KnowledgeBase has title column (String, not null)."""
        assert "title" in KnowledgeBase.__table__.columns
        col = KnowledgeBase.__table__.columns["title"]
        assert isinstance(col.type, String)
        assert col.nullable is False

    def test_has_content_column(self):
        """KnowledgeBase has content column (Text, not null)."""
        assert "content" in KnowledgeBase.__table__.columns
        col = KnowledgeBase.__table__.columns["content"]
        assert isinstance(col.type, Text)
        assert col.nullable is False

    def test_has_embedding_column(self):
        """KnowledgeBase has embedding column (Vector(3072))."""
        assert "embedding" in KnowledgeBase.__table__.columns
        col = KnowledgeBase.__table__.columns["embedding"]
        assert isinstance(col.type, Vector)
        assert col.type.dim == 3072

    def test_has_usage_count_column(self):
        """KnowledgeBase has usage_count column (Integer)."""
        assert "usage_count" in KnowledgeBase.__table__.columns
        col = KnowledgeBase.__table__.columns["usage_count"]
        assert isinstance(col.type, Integer)

    def test_has_type_index(self):
        """KnowledgeBase has index on (type, category)."""
        indexes = {idx.name for idx in KnowledgeBase.__table__.indexes}
        assert "idx_knowledge_type" in indexes


class TestAgentHeartbeatModel:
    """Test AgentHeartbeat model metadata."""

    def test_primary_key_is_agent_name(self):
        """AgentHeartbeat primary key is agent_name (String, not UUID)."""
        assert "agent_name" in AgentHeartbeat.__table__.columns
        col = AgentHeartbeat.__table__.columns["agent_name"]
        assert col.primary_key is True
        assert isinstance(col.type, String)

    def test_has_status_column(self):
        """AgentHeartbeat has status column (String)."""
        assert "status" in AgentHeartbeat.__table__.columns
        col = AgentHeartbeat.__table__.columns["status"]
        assert isinstance(col.type, String)

    def test_has_current_task_column(self):
        """AgentHeartbeat has current_task column (nullable Text)."""
        assert "current_task" in AgentHeartbeat.__table__.columns
        col = AgentHeartbeat.__table__.columns["current_task"]
        assert isinstance(col.type, Text)

    def test_has_last_heartbeat_column(self):
        """AgentHeartbeat has last_heartbeat column (DateTime)."""
        assert "last_heartbeat" in AgentHeartbeat.__table__.columns
        col = AgentHeartbeat.__table__.columns["last_heartbeat"]
        assert isinstance(col.type, DateTime)

    def test_has_restart_count_column(self):
        """AgentHeartbeat has restart_count column (Integer)."""
        assert "restart_count" in AgentHeartbeat.__table__.columns
        col = AgentHeartbeat.__table__.columns["restart_count"]
        assert isinstance(col.type, Integer)

    def test_has_metadata_column(self):
        """AgentHeartbeat has metadata column (mapped as JSONB)."""
        assert "metadata" in AgentHeartbeat.__table__.columns
        col = AgentHeartbeat.__table__.columns["metadata"]
        assert isinstance(col.type, JSONB)


class TestProjectModel:
    """Test Project model metadata."""

    def test_has_title_column(self):
        """Project has title column (String, not null)."""
        assert "title" in Project.__table__.columns
        col = Project.__table__.columns["title"]
        assert isinstance(col.type, String)
        assert col.nullable is False

    def test_has_agreed_amount_column(self):
        """Project has agreed_amount column (Numeric)."""
        assert "agreed_amount" in Project.__table__.columns
        col = Project.__table__.columns["agreed_amount"]
        assert isinstance(col.type, Numeric)

    def test_has_deadline_column(self):
        """Project has deadline column (Date)."""
        assert "deadline" in Project.__table__.columns
        col = Project.__table__.columns["deadline"]
        assert isinstance(col.type, Date)

    def test_has_revision_count_column(self):
        """Project has revision_count column (Integer)."""
        assert "revision_count" in Project.__table__.columns
        col = Project.__table__.columns["revision_count"]
        assert isinstance(col.type, Integer)

    def test_has_tasks_relationship(self):
        """Project has tasks relationship."""
        assert "tasks" in Project.__mapper__.relationships

    def test_has_artifacts_relationship(self):
        """Project has artifacts relationship."""
        assert "artifacts" in Project.__mapper__.relationships

    def test_has_revisions_relationship(self):
        """Project has revisions relationship."""
        assert "revisions" in Project.__mapper__.relationships


class TestArtifactModel:
    """Test Artifact model metadata."""

    def test_has_filename_column(self):
        """Artifact has filename column (String, not null)."""
        assert "filename" in Artifact.__table__.columns
        col = Artifact.__table__.columns["filename"]
        assert isinstance(col.type, String)
        assert col.nullable is False

    def test_has_content_column(self):
        """Artifact has content column (LargeBinary)."""
        assert "content" in Artifact.__table__.columns
        col = Artifact.__table__.columns["content"]
        assert isinstance(col.type, LargeBinary)

    def test_has_version_column(self):
        """Artifact has version column (Integer)."""
        assert "version" in Artifact.__table__.columns
        col = Artifact.__table__.columns["version"]
        assert isinstance(col.type, Integer)

    def test_has_quality_score_column(self):
        """Artifact has quality_score column (Numeric)."""
        assert "quality_score" in Artifact.__table__.columns
        col = Artifact.__table__.columns["quality_score"]
        assert isinstance(col.type, Numeric)


class TestSemanticCacheModel:
    """Test SemanticCache model metadata."""

    def test_has_query_column(self):
        """SemanticCache has query column (Text, not null)."""
        assert "query" in SemanticCache.__table__.columns
        col = SemanticCache.__table__.columns["query"]
        assert isinstance(col.type, Text)
        assert col.nullable is False

    def test_has_response_column(self):
        """SemanticCache has response column (Text, not null)."""
        assert "response" in SemanticCache.__table__.columns
        col = SemanticCache.__table__.columns["response"]
        assert isinstance(col.type, Text)
        assert col.nullable is False

    def test_has_embedding_column(self):
        """SemanticCache has embedding column (Vector(3072))."""
        assert "embedding" in SemanticCache.__table__.columns
        col = SemanticCache.__table__.columns["embedding"]
        assert isinstance(col.type, Vector)
        assert col.type.dim == 3072

    def test_has_expires_at_column(self):
        """SemanticCache has expires_at column (DateTime, not null)."""
        assert "expires_at" in SemanticCache.__table__.columns
        col = SemanticCache.__table__.columns["expires_at"]
        assert isinstance(col.type, DateTime)
        assert col.nullable is False

    def test_has_query_hash_unique_column(self):
        """SemanticCache has query_hash column (Text, unique)."""
        assert "query_hash" in SemanticCache.__table__.columns
        col = SemanticCache.__table__.columns["query_hash"]
        assert col.unique is True


class TestLeadModel:
    """Test Lead model metadata."""

    def test_has_name_column(self):
        """Lead has name column (String, not null)."""
        assert "name" in Lead.__table__.columns
        col = Lead.__table__.columns["name"]
        assert isinstance(col.type, String)
        assert col.nullable is False

    def test_has_geo_columns(self):
        """Lead has latitude and longitude columns (Numeric)."""
        assert "latitude" in Lead.__table__.columns
        assert "longitude" in Lead.__table__.columns
        assert isinstance(Lead.__table__.columns["latitude"].type, Numeric)
        assert isinstance(Lead.__table__.columns["longitude"].type, Numeric)

    def test_has_h3_index_column(self):
        """Lead has h3_index column (String)."""
        assert "h3_index" in Lead.__table__.columns
        col = Lead.__table__.columns["h3_index"]
        assert isinstance(col.type, String)

    def test_has_osm_id_unique_column(self):
        """Lead has osm_id column (BigInteger, unique)."""
        assert "osm_id" in Lead.__table__.columns
        col = Lead.__table__.columns["osm_id"]
        assert isinstance(col.type, BigInteger)
        assert col.unique is True


class TestLanggraphCheckpointModel:
    """Test LanggraphCheckpoint model metadata."""

    def test_has_thread_id_primary_key(self):
        """LanggraphCheckpoint has thread_id as part of composite primary key."""
        assert "thread_id" in LanggraphCheckpoint.__table__.columns
        col = LanggraphCheckpoint.__table__.columns["thread_id"]
        assert col.primary_key is True
        assert isinstance(col.type, String)

    def test_has_checkpoint_id_primary_key(self):
        """LanggraphCheckpoint has checkpoint_id as part of composite primary key."""
        assert "checkpoint_id" in LanggraphCheckpoint.__table__.columns
        col = LanggraphCheckpoint.__table__.columns["checkpoint_id"]
        assert col.primary_key is True

    def test_has_state_data_column(self):
        """LanggraphCheckpoint has state_data column (JSONB, not null)."""
        assert "state_data" in LanggraphCheckpoint.__table__.columns
        col = LanggraphCheckpoint.__table__.columns["state_data"]
        assert isinstance(col.type, JSONB)
        assert col.nullable is False

    def test_has_parent_checkpoint_id_column(self):
        """LanggraphCheckpoint has parent_checkpoint_id column."""
        assert "parent_checkpoint_id" in LanggraphCheckpoint.__table__.columns

    def test_has_current_agent_column(self):
        """LanggraphCheckpoint has current_agent column."""
        assert "current_agent" in LanggraphCheckpoint.__table__.columns

    def test_has_status_column(self):
        """LanggraphCheckpoint has status column."""
        assert "status" in LanggraphCheckpoint.__table__.columns

    def test_has_requires_hitl_column(self):
        """LanggraphCheckpoint has requires_hitl boolean column."""
        assert "requires_hitl" in LanggraphCheckpoint.__table__.columns
        col = LanggraphCheckpoint.__table__.columns["requires_hitl"]
        assert col.nullable is False
