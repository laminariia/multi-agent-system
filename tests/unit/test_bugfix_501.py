"""Tests for g_501 bugfixes: FreelancerClient instantiation, email sending node, goal_id generation.

Covers:
1. FreelancerClient gets proper credentials from DB-stored Settings (credential_loader)
2. FreelancerClient falls back to env vars when DB credentials absent
3. FreelancerClient works with missing credentials (empty strings)
4. email_sending_node calls send_approved_emails when approved
5. email_sending_node skips when not approved
6. email_sending_node handles missing campaign_id
7. _route_after_hitl_email routes to email_sending_node when approved
8. _route_after_hitl_email routes to END when not approved or failed
9. goal_id generation with IDs like g_099, g_100, g_101
10. goal_id generation with empty table
11. goal_id generation in API service with 100+ goals
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.graph import END

# ---------------------------------------------------------------------------
# Bug 1: FreelancerClient instantiation in bid_submission_node
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bid_submission_node_uses_db_credentials():
    """bid_submission_node prefers DB-stored credentials from credential_loader."""
    from src.core.graph import bid_submission_node

    mock_client_instance = MagicMock()
    mock_client_instance.submit_bid = AsyncMock(return_value={"result": {"id": 42}})
    mock_client_instance.close = AsyncMock()

    state = {
        "thread_id": "t1",
        "status": "active",
        "project": {"platform": "freelancer", "job_id": "123"},
        "artifacts": {"bid": {"proposal": "Hello", "amount": 100}},
        "current_agent": "bid",
        "next_agent": None,
        "errors": [],
        "requires_hitl": False,
        "hitl_request_id": None,
    }

    # DB credentials available — should be used instead of env vars
    db_creds = {"client_id": "db_client_id", "client_secret": "db_secret"}  # noqa: S105

    with (
        patch(
            "src.core.credential_loader.load_platform_credentials",
            new_callable=AsyncMock,
            return_value=db_creds,
        ),
        patch(
            "src.adapters.freelancer.FreelancerClient",
            return_value=mock_client_instance,
        ) as mock_cls,
    ):
        result = await bid_submission_node(state)

    mock_cls.assert_called_once_with(
        client_id="db_client_id",
        client_secret="db_secret",  # noqa: S106
    )
    assert result["artifacts"]["bid_submitted"] is True


@pytest.mark.asyncio
async def test_bid_submission_node_falls_back_to_env_credentials():
    """bid_submission_node uses env vars when DB credentials are absent."""
    from src.core.graph import bid_submission_node

    mock_settings = MagicMock()
    mock_settings.FREELANCER_CLIENT_ID = "env_client_id"  # noqa: S105
    mock_settings.FREELANCER_CLIENT_SECRET = "env_secret"  # noqa: S105

    mock_client_instance = MagicMock()
    mock_client_instance.submit_bid = AsyncMock(return_value={"result": {"id": 1}})

    state = {
        "thread_id": "t1",
        "status": "active",
        "project": {"platform": "freelancer", "job_id": "456"},
        "artifacts": {"bid": {"proposal": "Hi", "amount": 50}},
        "current_agent": "bid",
        "next_agent": None,
        "errors": [],
        "requires_hitl": False,
        "hitl_request_id": None,
    }

    # DB credentials not available — fall back to env vars
    with (
        patch(
            "src.core.credential_loader.load_platform_credentials",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("src.core.graph.get_settings", return_value=mock_settings),
        patch(
            "src.adapters.freelancer.FreelancerClient",
            return_value=mock_client_instance,
        ) as mock_cls,
    ):
        result = await bid_submission_node(state)

    mock_cls.assert_called_once_with(
        client_id="env_client_id",
        client_secret="env_secret",  # noqa: S106
    )
    assert result["artifacts"]["bid_submitted"] is True


@pytest.mark.asyncio
async def test_bid_submission_node_handles_missing_credentials():
    """bid_submission_node works with None credentials (empty strings)."""
    from src.core.graph import bid_submission_node

    mock_settings = MagicMock()
    mock_settings.FREELANCER_CLIENT_ID = None
    mock_settings.FREELANCER_CLIENT_SECRET = None

    mock_client_instance = MagicMock()
    mock_client_instance.submit_bid = AsyncMock(return_value={"result": {"id": 1}})

    state = {
        "thread_id": "t1",
        "status": "active",
        "project": {"platform": "freelancer", "job_id": "456"},
        "artifacts": {"bid": {"proposal": "Hi", "amount": 50}},
        "current_agent": "bid",
        "next_agent": None,
        "errors": [],
        "requires_hitl": False,
        "hitl_request_id": None,
    }

    # No DB creds, no env creds → empty strings
    with (
        patch(
            "src.core.credential_loader.load_platform_credentials",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("src.core.graph.get_settings", return_value=mock_settings),
        patch(
            "src.adapters.freelancer.FreelancerClient",
            return_value=mock_client_instance,
        ) as mock_cls,
    ):
        await bid_submission_node(state)

    # Empty string fallback for None
    mock_cls.assert_called_once_with(client_id="", client_secret="")


@pytest.mark.asyncio
async def test_bid_submission_node_non_freelancer_platform():
    """bid_submission_node marks non-freelancer platforms as manual_submit_required."""
    from src.core.graph import bid_submission_node

    state = {
        "thread_id": "t1",
        "status": "active",
        "project": {"platform": "upwork", "job_id": "789"},
        "artifacts": {"bid": {"proposal": "Test", "amount": 200}},
        "current_agent": "bid",
        "next_agent": None,
        "errors": [],
        "requires_hitl": False,
        "hitl_request_id": None,
    }

    result = await bid_submission_node(state)

    assert result["artifacts"]["bid_submitted"] is False
    assert result["artifacts"]["manual_submit_required"] is True
    assert result["next_agent"] == "planner"


@pytest.mark.asyncio
async def test_bid_submission_node_submission_failure():
    """bid_submission_node handles submit_bid failure gracefully."""
    from src.core.graph import bid_submission_node

    mock_client_instance = MagicMock()
    mock_client_instance.submit_bid = AsyncMock(side_effect=RuntimeError("API down"))

    state = {
        "thread_id": "t1",
        "status": "active",
        "project": {"platform": "freelancer", "job_id": "999"},
        "artifacts": {"bid": {"proposal": "Hello", "amount": 100}},
        "current_agent": "bid",
        "next_agent": None,
        "errors": [],
        "requires_hitl": False,
        "hitl_request_id": None,
    }

    with (
        patch(
            "src.core.credential_loader.load_platform_credentials",
            new_callable=AsyncMock,
            return_value={"client_id": "x", "client_secret": "y"},
        ),
        patch(
            "src.adapters.freelancer.FreelancerClient",
            return_value=mock_client_instance,
        ),
    ):
        result = await bid_submission_node(state)

    assert result["artifacts"]["bid_submitted"] is False
    assert "API down" in result["artifacts"]["bid_submission_error"]
    # Pipeline continues to planner despite failure
    assert result["next_agent"] == "planner"


# ---------------------------------------------------------------------------
# Bug 2: email_sending_node
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_email_sending_node_calls_send_when_approved():
    """email_sending_node calls send_approved_emails when emails_approved=True."""
    from src.core.graph import email_sending_node

    state = {
        "thread_id": "t1",
        "status": "active",
        "artifacts": {"emails_approved": True, "campaign_id": "camp-123"},
        "current_agent": "hitl_email",
        "next_agent": None,
        "errors": [],
        "requires_hitl": False,
        "hitl_request_id": None,
    }

    mock_send = AsyncMock(return_value={"sent": 5, "failed": 1, "rate_limited": 0})
    mock_session = AsyncMock()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    with (
        patch(
            "src.enrichment.email_sender.send_approved_emails",
            mock_send,
        ),
        patch(
            "src.core.database.get_db_session",
            return_value=mock_ctx,
        ),
    ):
        result = await email_sending_node(state)

    assert result["current_agent"] == "email_sending"
    assert result["status"] == "completed"
    assert result["artifacts"]["email_send_result"]["sent"] == 5
    mock_send.assert_called_once_with("camp-123", mock_session)


@pytest.mark.asyncio
async def test_email_sending_node_skips_when_not_approved():
    """email_sending_node skips sending when emails_approved is not True."""
    from src.core.graph import email_sending_node

    state = {
        "thread_id": "t1",
        "status": "active",
        "artifacts": {},
        "current_agent": "hitl_email",
        "next_agent": None,
        "errors": [],
        "requires_hitl": False,
        "hitl_request_id": None,
    }

    result = await email_sending_node(state)

    assert result["current_agent"] == "email_sending"
    assert result["status"] == "completed"
    assert "email_send_result" not in result.get("artifacts", {})


@pytest.mark.asyncio
async def test_email_sending_node_no_campaign_id():
    """email_sending_node handles missing campaign_id gracefully."""
    from src.core.graph import email_sending_node

    state = {
        "thread_id": "t1",
        "status": "active",
        "artifacts": {"emails_approved": True},
        "current_agent": "hitl_email",
        "next_agent": None,
        "errors": [],
        "requires_hitl": False,
        "hitl_request_id": None,
    }

    result = await email_sending_node(state)

    assert result["current_agent"] == "email_sending"
    assert result["status"] == "completed"
    assert result["artifacts"]["email_send_result"]["error"] == "no campaign_id"
    assert result["artifacts"]["email_send_result"]["sent"] == 0


@pytest.mark.asyncio
async def test_email_sending_node_handles_send_exception():
    """email_sending_node captures exception from send_approved_emails gracefully."""
    from src.core.graph import email_sending_node

    state = {
        "thread_id": "t1",
        "status": "active",
        "artifacts": {"emails_approved": True, "campaign_id": "camp-err"},
        "current_agent": "hitl_email",
        "next_agent": None,
        "errors": [],
        "requires_hitl": False,
        "hitl_request_id": None,
    }

    mock_send = AsyncMock(side_effect=RuntimeError("SMTP connection refused"))
    mock_session = AsyncMock()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("src.enrichment.email_sender.send_approved_emails", mock_send),
        patch("src.core.database.get_db_session", return_value=mock_ctx),
    ):
        result = await email_sending_node(state)

    assert result["status"] == "completed"
    assert "SMTP connection refused" in result["artifacts"]["email_send_result"]["error"]
    assert result["artifacts"]["email_send_result"]["sent"] == 0


# ---------------------------------------------------------------------------
# Bug 2: _route_after_hitl_email routing
# ---------------------------------------------------------------------------


def test_route_after_hitl_email_approved():
    """Route to email_sending_node when emails are approved."""
    from src.core.graph import _route_after_hitl_email

    state = {
        "thread_id": "t1",
        "status": "completed",
        "artifacts": {"emails_approved": True},
    }
    assert _route_after_hitl_email(state) == "email_sending_node"


def test_route_after_hitl_email_not_approved():
    """Route to END when emails are not approved."""
    from src.core.graph import _route_after_hitl_email

    state = {
        "thread_id": "t1",
        "status": "completed",
        "artifacts": {},
    }
    assert _route_after_hitl_email(state) == END


def test_route_after_hitl_email_failed():
    """Route to END when status is failed."""
    from src.core.graph import _route_after_hitl_email

    state = {
        "thread_id": "t1",
        "status": "failed",
        "artifacts": {"emails_approved": True},
    }
    assert _route_after_hitl_email(state) == END


def test_route_after_hitl_email_rejected():
    """Route to END when emails_approved is False (rejected)."""
    from src.core.graph import _route_after_hitl_email

    state = {
        "thread_id": "t1",
        "status": "completed",
        "artifacts": {"emails_approved": False},
    }
    assert _route_after_hitl_email(state) == END


# ---------------------------------------------------------------------------
# Bug 3: goal_id lexicographic MAX bug
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_goal_id_generation_over_099():
    """goal_id correctly generates g_102 after g_099, g_100, g_101."""
    from src.bot.orchestrator_commands import _add_goal_to_db

    mock_rows = [("g_099",), ("g_100",), ("g_101",)]
    mock_result = MagicMock()
    mock_result.all.return_value = mock_rows

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.add = MagicMock()

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("src.bot.orchestrator_commands.get_db_session", return_value=mock_ctx):
        new_id = await _add_goal_to_db("Test goal")

    assert new_id == "g_102"


@pytest.mark.asyncio
async def test_goal_id_generation_empty_table():
    """goal_id starts at g_001 when the table is empty."""
    from src.bot.orchestrator_commands import _add_goal_to_db

    mock_result = MagicMock()
    mock_result.all.return_value = []

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.add = MagicMock()

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("src.bot.orchestrator_commands.get_db_session", return_value=mock_ctx):
        new_id = await _add_goal_to_db("First goal")

    assert new_id == "g_001"


@pytest.mark.asyncio
async def test_goal_id_generation_numeric_max_not_lexicographic():
    """Numeric max ensures g_100 > g_099, unlike lexicographic comparison."""
    from src.bot.orchestrator_commands import _add_goal_to_db

    # Lexicographic max of these would be "g_099" (wrong!)
    # Numeric max is 100 -> next is 101
    mock_rows = [("g_001",), ("g_050",), ("g_099",), ("g_100",)]
    mock_result = MagicMock()
    mock_result.all.return_value = mock_rows

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.add = MagicMock()

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("src.bot.orchestrator_commands.get_db_session", return_value=mock_ctx):
        new_id = await _add_goal_to_db("Should be 101")

    assert new_id == "g_101"


@pytest.mark.asyncio
async def test_api_service_goal_id_generation_over_099():
    """OrchestratorService.add_goal correctly handles 100+ goals."""
    from src.api.services.orchestrator import OrchestratorService

    mock_rows = [("g_098",), ("g_099",), ("g_100",)]
    mock_id_result = MagicMock()
    mock_id_result.all.return_value = mock_rows

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_id_result)
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()

    result = await OrchestratorService.add_goal(mock_session, "Test goal from API")

    assert result["id"] == "g_101"


@pytest.mark.asyncio
async def test_api_service_goal_id_generation_empty():
    """OrchestratorService.add_goal starts at g_001 with empty table."""
    from src.api.services.orchestrator import OrchestratorService

    mock_id_result = MagicMock()
    mock_id_result.all.return_value = []

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_id_result)
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()

    result = await OrchestratorService.add_goal(mock_session, "First API goal")

    assert result["id"] == "g_001"


# ---------------------------------------------------------------------------
# Bug 2: _apply_email_approval sets status="active" for pipeline re-invocation
# ---------------------------------------------------------------------------


def test_apply_email_approval_approve_sets_active():
    """_apply_email_approval sets status='active' on approve so pipeline continues."""
    from src.core.graph import _apply_email_approval

    saved = {
        "thread_id": "t1",
        "status": "paused",
        "requires_hitl": True,
        "hitl_request_id": "req-1",
        "current_agent": "hitl_email",
        "next_agent": None,
        "errors": [],
        "artifacts": {"campaign_id": "camp-1"},
    }
    result = _apply_email_approval(saved, "approve", {}, "t1")
    assert result["status"] == "active"
    assert result["artifacts"]["emails_approved"] is True
    assert result["current_agent"] == "hitl_email"


def test_apply_email_approval_edit_sets_active():
    """_apply_email_approval sets status='active' on edit so pipeline continues."""
    from src.core.graph import _apply_email_approval

    saved = {
        "thread_id": "t1",
        "status": "paused",
        "requires_hitl": True,
        "hitl_request_id": "req-1",
        "current_agent": "hitl_email",
        "next_agent": None,
        "errors": [],
        "artifacts": {"campaign_id": "camp-1"},
    }
    result = _apply_email_approval(saved, "edit", {"edits": {"body": "new"}}, "t1")
    assert result["status"] == "active"
    assert result["artifacts"]["emails_approved"] is True
    assert result["artifacts"]["hitl_edits"] == [{"body": "new"}]


def test_apply_email_approval_reject_sets_failed():
    """_apply_email_approval sets status='failed' on reject."""
    from src.core.graph import _apply_email_approval

    saved = {
        "thread_id": "t1",
        "status": "paused",
        "requires_hitl": True,
        "hitl_request_id": "req-1",
        "current_agent": "hitl_email",
        "next_agent": None,
        "errors": [],
        "artifacts": {},
    }
    result = _apply_email_approval(saved, "reject", {}, "t1")
    assert result["status"] == "failed"


# ---------------------------------------------------------------------------
# Bug 3: goal_id with invalid/corrupt entries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_goal_id_generation_skips_invalid_ids():
    """goal_id generation ignores malformed goal IDs gracefully."""
    from src.bot.orchestrator_commands import _add_goal_to_db

    mock_rows = [("g_001",), ("invalid",), ("g_abc",), ("g_050",), ("nounder",)]
    mock_result = MagicMock()
    mock_result.all.return_value = mock_rows

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.add = MagicMock()

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    with patch("src.bot.orchestrator_commands.get_db_session", return_value=mock_ctx):
        new_id = await _add_goal_to_db("Test with bad IDs")

    # max(1, 50) + 1 = 51
    assert new_id == "g_051"


@pytest.mark.asyncio
async def test_api_service_goal_id_skips_invalid_ids():
    """OrchestratorService.add_goal ignores malformed goal IDs."""
    from src.api.services.orchestrator import OrchestratorService

    mock_rows = [("g_005",), ("bad",), ("g_xyz",), ("g_010",)]
    mock_id_result = MagicMock()
    mock_id_result.all.return_value = mock_rows

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_id_result)
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()

    result = await OrchestratorService.add_goal(mock_session, "Test with bad IDs")

    # max(5, 10) + 1 = 11
    assert result["id"] == "g_011"


# ---------------------------------------------------------------------------
# Pipeline B graph includes email_sending_node
# ---------------------------------------------------------------------------


def test_pipeline_b_graph_has_email_sending_node():
    """build_pipeline_b_graph includes the email_sending_node."""
    from src.core.graph import build_pipeline_b_graph

    graph = build_pipeline_b_graph()
    # The compiled graph should have the email_sending_node
    node_names = set(graph.nodes.keys())
    assert "email_sending_node" in node_names
    assert "hitl_email_node" in node_names
