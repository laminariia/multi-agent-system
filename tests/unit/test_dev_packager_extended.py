"""Extended unit tests for src.agents.dev and src.agents.packager.

Covers uncovered paths: parse failures, semgrep blocked, sandbox failures,
_deserialize_plan, _build_fallback_delivery, _create_hitl_entry, _log_*.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage

from src.agents.dev import DevAgent
from src.agents.packager import PackagerAgent
from src.core.llm_client import CallMetrics
from src.core.state import AgentState, create_initial_state

pytestmark = pytest.mark.asyncio


def _state(agent="dev", **kw: Any) -> AgentState:
    project = {
        "project_id": "p1",
        "job_id": "j1",
        "platform": "freelancer",
        "client": {"name": "T"},
        "requirements": "Build page",
        "budget": 500.0,
        "deadline": datetime(2026, 3, 15, tzinfo=UTC),
    }
    s = create_initial_state(project=project, first_agent=agent, thread_id=f"t-{agent}-ext")
    s.update(kw)  # type: ignore[typeddict-item]
    return s


def _llm(content):
    return (AIMessage(content=content), CallMetrics(agent_name="dev", model_id="claude-opus-4-6", provider="anthropic"))


def _dev(llm, hb, ld):
    return DevAgent(llm_client=llm, heartbeat=hb, loop_detector=ld)


def _pkg(llm, hb, ld):
    return PackagerAgent(llm_client=llm, heartbeat=hb, loop_detector=ld)


# =============================================================================
# DevAgent — _parse_code_response edge cases
# =============================================================================


def test_parse_non_dict(mock_llm_client, mock_heartbeat, mock_loop_detector):
    agent = _dev(mock_llm_client, mock_heartbeat, mock_loop_detector)
    assert agent._parse_code_response("[1, 2, 3]") is None


def test_parse_no_files(mock_llm_client, mock_heartbeat, mock_loop_detector):
    agent = _dev(mock_llm_client, mock_heartbeat, mock_loop_detector)
    assert agent._parse_code_response('{"no_files": true}') is None


def test_parse_empty_files_list(mock_llm_client, mock_heartbeat, mock_loop_detector):
    agent = _dev(mock_llm_client, mock_heartbeat, mock_loop_detector)
    assert agent._parse_code_response('{"files": []}') is None


def test_parse_files_missing_path_content(mock_llm_client, mock_heartbeat, mock_loop_detector):
    agent = _dev(mock_llm_client, mock_heartbeat, mock_loop_detector)
    result = agent._parse_code_response('{"files": [{"path": "a.py"}, {"content": "x"}, "invalid"]}')
    assert result is None  # all invalid


def test_parse_infers_language(mock_llm_client, mock_heartbeat, mock_loop_detector):
    agent = _dev(mock_llm_client, mock_heartbeat, mock_loop_detector)
    raw = json.dumps({"files": [{"path": "app.tsx", "content": "export default {}"}]})
    result = agent._parse_code_response(raw)
    assert result is not None
    assert result["files"][0]["language"] == "typescript"


def test_parse_sets_defaults(mock_llm_client, mock_heartbeat, mock_loop_detector):
    agent = _dev(mock_llm_client, mock_heartbeat, mock_loop_detector)
    raw = json.dumps({"files": [{"path": "a.py", "content": "x"}]})
    result = agent._parse_code_response(raw)
    assert result["dependencies"] == []
    assert result["build_commands"] == []
    assert result["test_commands"] == []
    assert result["deployment_notes"] == ""


def test_parse_keeps_existing_language(mock_llm_client, mock_heartbeat, mock_loop_detector):
    agent = _dev(mock_llm_client, mock_heartbeat, mock_loop_detector)
    raw = json.dumps({"files": [{"path": "a.py", "content": "x", "language": "python"}]})
    result = agent._parse_code_response(raw)
    assert result["files"][0]["language"] == "python"


def test_infer_language_various():
    assert DevAgent._infer_language("x.js") == "javascript"
    assert DevAgent._infer_language("x.css") == "css"
    assert DevAgent._infer_language("x.sql") == "sql"
    assert DevAgent._infer_language("x.unknown") == "text"


# =============================================================================
# DevAgent — _deserialize_plan
# =============================================================================


def test_deserialize_plan_dev_task(mock_llm_client, mock_heartbeat, mock_loop_detector):
    agent = _dev(mock_llm_client, mock_heartbeat, mock_loop_detector)
    plan = json.dumps(
        {
            "phases": [
                {
                    "tasks": [
                        {"assigned_to": "content", "description": "write"},
                        {"assigned_to": "dev", "description": "code"},
                    ]
                }
            ],
        }
    )
    result = agent._deserialize_plan([plan])
    assert result["assigned_to"] == "dev"


def test_deserialize_plan_first_task_fallback(mock_llm_client, mock_heartbeat, mock_loop_detector):
    agent = _dev(mock_llm_client, mock_heartbeat, mock_loop_detector)
    plan = json.dumps(
        {
            "phases": [{"tasks": [{"assigned_to": "content", "description": "write"}]}],
        }
    )
    result = agent._deserialize_plan([plan])
    assert result["assigned_to"] == "content"  # first task


def test_deserialize_plan_no_phases(mock_llm_client, mock_heartbeat, mock_loop_detector):
    agent = _dev(mock_llm_client, mock_heartbeat, mock_loop_detector)
    plan = json.dumps({"summary": "do stuff"})
    result = agent._deserialize_plan([plan])
    assert result["summary"] == "do stuff"


def test_deserialize_plan_invalid_json(mock_llm_client, mock_heartbeat, mock_loop_detector):
    agent = _dev(mock_llm_client, mock_heartbeat, mock_loop_detector)
    assert agent._deserialize_plan(["not json"]) is None


def test_deserialize_plan_assigned_agent_key(mock_llm_client, mock_heartbeat, mock_loop_detector):
    agent = _dev(mock_llm_client, mock_heartbeat, mock_loop_detector)
    plan = json.dumps(
        {
            "phases": [{"tasks": [{"assigned_agent": "dev", "description": "code"}]}],
        }
    )
    result = agent._deserialize_plan([plan])
    assert result["assigned_agent"] == "dev"


# =============================================================================
# DevAgent — _build_user_prompt
# =============================================================================


def test_build_user_prompt_with_planner(mock_llm_client, mock_heartbeat, mock_loop_detector):
    agent = _dev(mock_llm_client, mock_heartbeat, mock_loop_detector)
    s = _state(artifacts={"planner": [json.dumps({"tasks": ["t1"]})]})
    prompt = agent._build_user_prompt(s, {"description": "build page"})
    assert "Full Project Plan" in prompt
    assert "t1" in prompt


def test_build_user_prompt_invalid_planner_json(mock_llm_client, mock_heartbeat, mock_loop_detector):
    agent = _dev(mock_llm_client, mock_heartbeat, mock_loop_detector)
    s = _state(artifacts={"planner": ["not json"]})
    prompt = agent._build_user_prompt(s, {"description": "build page"})
    assert "not json" in prompt


# =============================================================================
# DevAgent — _execute edge cases
# =============================================================================


@patch("src.agents.dev.get_db_session")
async def test_execute_parse_fails(mock_db, mock_llm_client, mock_heartbeat, mock_loop_detector):
    mock_llm_client.call = AsyncMock(return_value=_llm("garbage output"))
    mock_session = AsyncMock()
    mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

    agent = _dev(mock_llm_client, mock_heartbeat, mock_loop_detector)
    s = _state(current_task={"description": "code"})
    result = await agent._execute(s)
    assert result["next_agent"] is None
    assert any("failed to parse" in e for e in result["errors"])


@patch("src.agents.dev.SandboxManager")
@patch("src.agents.dev.SemgrepGate")
@patch("src.agents.dev.get_db_session")
async def test_execute_semgrep_blocked(
    mock_db, mock_gate_cls, mock_sandbox_cls, mock_llm_client, mock_heartbeat, mock_loop_detector
):
    from src.security.semgrep_gate import ScanResult, SemgrepFinding

    finding = SemgrepFinding(
        rule_id="dangerous-eval", severity="ERROR", path="test.py", line=1, message="eval", code_snippet="eval('x')"
    )
    blocked = ScanResult(findings=[finding], critical_count=1, warning_count=0, blocked=True)
    mock_gate_cls.return_value.scan_files = AsyncMock(return_value=blocked)

    code = json.dumps({"files": [{"path": "a.py", "content": "eval('x')"}]})
    mock_llm_client.call = AsyncMock(return_value=_llm(code))

    mock_session = AsyncMock()
    mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

    agent = _dev(mock_llm_client, mock_heartbeat, mock_loop_detector)
    s = _state(current_task={"description": "code"})
    result = await agent._execute(s)
    assert "_dev_semgrep_warnings" in result["artifacts"]
    # sandbox should NOT have been called
    mock_sandbox_cls.return_value.execute_code.assert_not_called()


@patch("src.agents.dev.SandboxManager")
@patch("src.agents.dev.SemgrepGate")
@patch("src.agents.dev.get_db_session")
async def test_execute_sandbox_fails(
    mock_db, mock_gate_cls, mock_sandbox_cls, mock_llm_client, mock_heartbeat, mock_loop_detector
):
    from src.security.semgrep_gate import ScanResult

    clean = ScanResult(findings=[], critical_count=0, warning_count=0, blocked=False)
    mock_gate_cls.return_value.scan_files = AsyncMock(return_value=clean)
    mock_sandbox_cls.return_value.execute_code = AsyncMock(side_effect=RuntimeError("no docker"))

    code = json.dumps({"files": [{"path": "a.py", "content": "print('hi')"}]})
    mock_llm_client.call = AsyncMock(return_value=_llm(code))

    mock_session = AsyncMock()
    mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

    agent = _dev(mock_llm_client, mock_heartbeat, mock_loop_detector)
    s = _state(current_task={"description": "code"})
    result = await agent._execute(s)
    assert result["artifacts"].get("_sandbox_skipped") is True
    assert result["current_sequence_index"] == 1  # advanced from default 0


@patch("src.agents.dev.SandboxManager")
@patch("src.agents.dev.SemgrepGate")
@patch("src.agents.dev.get_db_session")
async def test_execute_sandbox_nonzero_exit(
    mock_db, mock_gate_cls, mock_sandbox_cls, mock_llm_client, mock_heartbeat, mock_loop_detector
):
    from src.sandbox.base import ExecutionResult
    from src.security.semgrep_gate import ScanResult

    clean = ScanResult(findings=[], critical_count=0, warning_count=0, blocked=False)
    mock_gate_cls.return_value.scan_files = AsyncMock(return_value=clean)
    failed = ExecutionResult(stdout="", stderr="Error", exit_code=1, duration_ms=50)
    mock_sandbox_cls.return_value.execute_code = AsyncMock(return_value=failed)

    code = json.dumps({"files": [{"path": "a.py", "content": "print('hi')"}]})
    mock_llm_client.call = AsyncMock(return_value=_llm(code))

    mock_session = AsyncMock()
    mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

    agent = _dev(mock_llm_client, mock_heartbeat, mock_loop_detector)
    s = _state(current_task={"description": "code"})
    result = await agent._execute(s)
    assert result["artifacts"]["_dev_execution"]["success"] is False
    assert result["current_sequence_index"] == 1  # advanced from default 0


# =============================================================================
# DevAgent — _log_code_generated
# =============================================================================


@patch("src.agents.dev.get_db_session")
async def test_log_code_generated(mock_db, mock_llm_client, mock_heartbeat, mock_loop_detector):
    mock_session = AsyncMock()
    mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

    agent = _dev(mock_llm_client, mock_heartbeat, mock_loop_detector)
    await agent._log_code_generated(thread_id="t1", artifact_id="a1", files_count=3, dependencies=["react"])
    mock_session.add.assert_called_once()


# =============================================================================
# PackagerAgent — _parse_delivery_response
# =============================================================================


def test_parse_delivery_non_dict(mock_llm_client, mock_heartbeat, mock_loop_detector):
    agent = _pkg(mock_llm_client, mock_heartbeat, mock_loop_detector)
    assert agent._parse_delivery_response("[1]") is None


def test_parse_delivery_invalid_files_count(mock_llm_client, mock_heartbeat, mock_loop_detector):
    agent = _pkg(mock_llm_client, mock_heartbeat, mock_loop_detector)
    raw = json.dumps({"files_count": "not_a_number"})
    result = agent._parse_delivery_response(raw)
    assert result is not None
    assert result["files_count"] == 0


def test_parse_delivery_sets_defaults(mock_llm_client, mock_heartbeat, mock_loop_detector):
    agent = _pkg(mock_llm_client, mock_heartbeat, mock_loop_detector)
    raw = json.dumps({"summary": "done"})
    result = agent._parse_delivery_response(raw)
    assert result["delivery_message"] == "Your project delivery is ready for review."
    assert result["requires_hitl"] is True
    assert result["includes"] == []


# =============================================================================
# PackagerAgent — _build_fallback_delivery
# =============================================================================


def test_build_fallback_delivery():
    result = PackagerAgent._build_fallback_delivery("p1", {"dev": ["a", "b"], "content": ["c"]})
    assert result["project_id"] == "p1"
    assert result["files_count"] == 3
    assert "dev_artifacts" in result["includes"]
    assert result["requires_hitl"] is True


# =============================================================================
# PackagerAgent — _create_hitl_entry
# =============================================================================


@patch("src.agents.packager.get_db_session")
async def test_packager_create_hitl(mock_db, mock_llm_client, mock_heartbeat, mock_loop_detector):
    mock_session = AsyncMock()
    mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

    agent = _pkg(mock_llm_client, mock_heartbeat, mock_loop_detector)
    project = {"project_id": "p1"}
    delivery = {"files_count": 3, "includes": ["dev"], "missing_artifacts": []}
    hitl_id = await agent._create_hitl_entry(project, delivery)
    uuid.UUID(hitl_id)
    mock_session.add.assert_called_once()


# =============================================================================
# PackagerAgent — _log_packaging_action
# =============================================================================


@patch("src.agents.packager.get_db_session")
async def test_packager_log_with_note(mock_db, mock_llm_client, mock_heartbeat, mock_loop_detector):
    mock_session = AsyncMock()
    mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

    agent = _pkg(mock_llm_client, mock_heartbeat, mock_loop_detector)
    await agent._log_packaging_action(
        project_id="p1",
        delivery_info={"files_count": 2},
        thread_id="t1",
        note="test note",
    )
    mock_session.add.assert_called_once()


@patch("src.agents.packager.get_db_session")
async def test_packager_log_without_note(mock_db, mock_llm_client, mock_heartbeat, mock_loop_detector):
    mock_session = AsyncMock()
    mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

    agent = _pkg(mock_llm_client, mock_heartbeat, mock_loop_detector)
    await agent._log_packaging_action(
        project_id="p1",
        delivery_info={"files_count": 2},
        thread_id="t1",
    )
    mock_session.add.assert_called_once()


# =============================================================================
# PackagerAgent — _execute
# =============================================================================


@patch("src.agents.packager.get_db_session")
async def test_packager_execute_no_artifacts(mock_db, mock_llm_client, mock_heartbeat, mock_loop_detector):
    mock_session = AsyncMock()
    mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

    agent = _pkg(mock_llm_client, mock_heartbeat, mock_loop_detector)
    s = _state("packager", artifacts={})
    result = await agent._execute(s)
    assert result["status"] == "active"
    assert result["next_agent"] is None


@patch("src.agents.packager.get_db_session")
async def test_packager_execute_llm_fails_uses_fallback(mock_db, mock_llm_client, mock_heartbeat, mock_loop_detector):
    mock_llm_client.call = AsyncMock(return_value=_llm("not json"))
    mock_session = AsyncMock()
    mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

    agent = _pkg(mock_llm_client, mock_heartbeat, mock_loop_detector)
    s = _state("packager", artifacts={"dev": ["code"]})
    result = await agent._execute(s)
    assert result["status"] == "paused"
    assert result["requires_hitl"] is True


@patch("src.agents.packager.get_db_session")
async def test_packager_execute_success(mock_db, mock_llm_client, mock_heartbeat, mock_loop_detector):
    delivery = json.dumps(
        {
            "delivery_id": "d1",
            "project_id": "p1",
            "files_count": 3,
            "includes": ["dev"],
            "delivery_message": "Ready",
            "readme_content": "# README",
            "missing_artifacts": [],
            "quality_notes": "Good",
        }
    )
    mock_llm_client.call = AsyncMock(return_value=_llm(delivery))
    mock_session = AsyncMock()
    mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
    mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

    agent = _pkg(mock_llm_client, mock_heartbeat, mock_loop_detector)
    s = _state("packager", artifacts={"dev": ["code"], "content": ["text"]})
    result = await agent._execute(s)
    assert result["status"] == "paused"
    assert result["requires_hitl"] is True
    assert "packager" in result["artifacts"]
