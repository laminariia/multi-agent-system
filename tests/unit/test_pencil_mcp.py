"""Unit tests for src.integrations.pencil_mcp — Pencil.dev MCP integration.

Tests cover:
- PencilMCPClient initialization and availability detection
- Design spec building (project_type, style, sections, responsive, export_format)
- create_design() with MCP available and unavailable (graceful fallback)
- export_screenshot() with MCP available and unavailable
- export_code() with MCP available and unavailable
- build_design_spec() JSON structure validation
- Error handling for MCP tool failures
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from src.integrations.pencil_mcp import (
    PencilDesignResult,
    PencilMCPClient,
    build_design_spec,
)

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# build_design_spec tests
# ---------------------------------------------------------------------------


def test_build_design_spec_landing_page():
    """build_design_spec produces correct structure for landing page."""
    spec = build_design_spec(
        project_type="landing_page",
        style={
            "theme": "modern_minimal",
            "colors": ["#1a1a2e", "#16213e", "#0f3460", "#e94560"],
            "typography": "Inter / system-ui",
        },
        sections=[
            {
                "type": "hero",
                "content": "AI-powered analytics dashboard",
                "elements": ["headline", "subtext", "cta_button", "hero_image"],
            },
            {
                "type": "features",
                "items": 3,
                "layout": "grid",
            },
        ],
        responsive=True,
        export_format="react",
    )

    assert spec["project_type"] == "landing_page"
    assert spec["style"]["theme"] == "modern_minimal"
    assert len(spec["style"]["colors"]) == 4
    assert spec["style"]["typography"] == "Inter / system-ui"
    assert len(spec["sections"]) == 2
    assert spec["sections"][0]["type"] == "hero"
    assert spec["responsive"] is True
    assert spec["export_format"] == "react"


def test_build_design_spec_defaults():
    """build_design_spec uses sensible defaults for optional fields."""
    spec = build_design_spec(project_type="dashboard")

    assert spec["project_type"] == "dashboard"
    assert spec["style"]["theme"] == "modern_minimal"
    assert isinstance(spec["style"]["colors"], list)
    assert len(spec["style"]["colors"]) > 0
    assert spec["responsive"] is True
    assert spec["export_format"] == "html"
    assert spec["sections"] == []


def test_build_design_spec_custom_export_format():
    """build_design_spec respects custom export format."""
    spec = build_design_spec(project_type="web_app", export_format="react")
    assert spec["export_format"] == "react"


def test_build_design_spec_with_empty_sections():
    """build_design_spec handles empty sections list."""
    spec = build_design_spec(project_type="landing_page", sections=[])
    assert spec["sections"] == []


def test_build_design_spec_non_responsive():
    """build_design_spec supports non-responsive designs."""
    spec = build_design_spec(project_type="email_template", responsive=False)
    assert spec["responsive"] is False


# ---------------------------------------------------------------------------
# PencilDesignResult tests
# ---------------------------------------------------------------------------


def test_pencil_design_result_creation():
    """PencilDesignResult stores all fields correctly."""
    result = PencilDesignResult(
        pen_file_path="/designs/v1.pen",
        screenshot_path="/designs/screenshot.png",
        design_spec={"project_type": "landing_page"},
        preview_link="https://pencil.dev/preview/abc123",
        code_export="<div>Hello</div>",
    )
    assert result.pen_file_path == "/designs/v1.pen"
    assert result.screenshot_path == "/designs/screenshot.png"
    assert result.design_spec["project_type"] == "landing_page"
    assert result.preview_link == "https://pencil.dev/preview/abc123"
    assert result.code_export == "<div>Hello</div>"


def test_pencil_design_result_to_dict():
    """PencilDesignResult.to_dict() returns serializable dictionary."""
    result = PencilDesignResult(
        pen_file_path="/designs/v1.pen",
        screenshot_path="/designs/screenshot.png",
        design_spec={"project_type": "landing_page"},
    )
    d = result.to_dict()
    assert d["pen_file_path"] == "/designs/v1.pen"
    assert d["screenshot_path"] == "/designs/screenshot.png"
    assert d["design_spec"]["project_type"] == "landing_page"
    assert d["preview_link"] is None
    assert d["code_export"] is None
    # Must be JSON-serializable
    json.dumps(d)


def test_pencil_design_result_fallback_only():
    """PencilDesignResult with spec only (MCP unavailable fallback)."""
    result = PencilDesignResult(
        pen_file_path=None,
        screenshot_path=None,
        design_spec={"project_type": "dashboard", "deliverables": []},
    )
    assert result.pen_file_path is None
    assert result.screenshot_path is None
    assert result.design_spec["project_type"] == "dashboard"
    assert result.is_fallback is True


def test_pencil_design_result_not_fallback():
    """PencilDesignResult with pen_file is NOT a fallback."""
    result = PencilDesignResult(
        pen_file_path="/designs/v1.pen",
        screenshot_path="/designs/screenshot.png",
        design_spec={"project_type": "landing_page"},
    )
    assert result.is_fallback is False


# ---------------------------------------------------------------------------
# PencilMCPClient — initialization and availability
# ---------------------------------------------------------------------------


def test_client_init_defaults():
    """PencilMCPClient initializes with default state."""
    client = PencilMCPClient()
    assert client._mcp_available is None  # not yet checked


async def test_client_check_availability_true():
    """check_availability returns True when MCP tools respond."""
    client = PencilMCPClient()
    with patch.object(client, "_call_mcp_tool", new_callable=AsyncMock, return_value={"status": "ok"}):
        available = await client.check_availability()
    assert available is True
    assert client._mcp_available is True


async def test_client_check_availability_false():
    """check_availability returns False when MCP tools fail."""
    client = PencilMCPClient()
    with patch.object(client, "_call_mcp_tool", new_callable=AsyncMock, side_effect=Exception("MCP not found")):
        available = await client.check_availability()
    assert available is False
    assert client._mcp_available is False


# ---------------------------------------------------------------------------
# PencilMCPClient — create_design
# ---------------------------------------------------------------------------


async def test_create_design_mcp_available():
    """create_design calls MCP tools and returns full result when available."""
    client = PencilMCPClient()
    client._mcp_available = True

    spec = build_design_spec(project_type="landing_page")

    # Mock the internal MCP tool calls
    mock_design_response = {
        "pen_file_path": "/project/design/v1.pen",
        "preview_link": "https://pencil.dev/preview/abc123",
    }
    mock_screenshot = b"\x89PNG screenshot data"
    mock_code = "<div>Hello</div>"

    with patch.object(
        client,
        "_call_mcp_tool",
        new_callable=AsyncMock,
        side_effect=[
            mock_design_response,  # batch_design call
            {"screenshot_data": "base64data", "path": "/project/design/screenshot.png"},  # export_nodes / screenshot
            {"code": mock_code, "format": "html"},  # export_nodes / code
        ],
    ):
        with patch.object(
            client,
            "_export_screenshot_bytes",
            new_callable=AsyncMock,
            return_value=mock_screenshot,
        ):
            result = await client.create_design(spec)

    assert isinstance(result, PencilDesignResult)
    assert result.pen_file_path == "/project/design/v1.pen"
    assert result.design_spec == spec
    assert result.is_fallback is False
    assert result.code_export == mock_code


async def test_create_design_mcp_unavailable_fallback():
    """create_design returns spec-only fallback when MCP is unavailable."""
    client = PencilMCPClient()
    client._mcp_available = False

    spec = build_design_spec(project_type="dashboard")
    result = await client.create_design(spec)

    assert isinstance(result, PencilDesignResult)
    assert result.pen_file_path is None
    assert result.screenshot_path is None
    assert result.design_spec == spec
    assert result.is_fallback is True


async def test_create_design_mcp_error_fallback():
    """create_design falls back gracefully when MCP call raises an exception."""
    client = PencilMCPClient()
    client._mcp_available = True

    spec = build_design_spec(project_type="landing_page")

    with patch.object(
        client,
        "_call_mcp_tool",
        new_callable=AsyncMock,
        side_effect=Exception("MCP connection lost"),
    ):
        result = await client.create_design(spec)

    assert isinstance(result, PencilDesignResult)
    assert result.is_fallback is True
    assert result.design_spec == spec


async def test_create_design_auto_checks_availability():
    """create_design auto-checks availability if not yet determined."""
    client = PencilMCPClient()
    assert client._mcp_available is None  # not yet checked

    spec = build_design_spec(project_type="landing_page")

    # check_availability will fail -> fallback path
    with patch.object(client, "check_availability", new_callable=AsyncMock, return_value=False):
        result = await client.create_design(spec)

    assert result.is_fallback is True


# ---------------------------------------------------------------------------
# PencilMCPClient — export_screenshot
# ---------------------------------------------------------------------------


async def test_export_screenshot_mcp_available():
    """export_screenshot returns bytes when MCP is available."""
    client = PencilMCPClient()
    client._mcp_available = True

    with patch.object(
        client,
        "_call_mcp_tool",
        new_callable=AsyncMock,
        return_value={"path": "/designs/screenshot.png"},
    ):
        with patch.object(
            client,
            "_export_screenshot_bytes",
            new_callable=AsyncMock,
            return_value=b"\x89PNG data",
        ):
            data = await client.export_screenshot("/designs/v1.pen")

    assert data == b"\x89PNG data"


async def test_export_screenshot_mcp_unavailable():
    """export_screenshot returns None when MCP is unavailable."""
    client = PencilMCPClient()
    client._mcp_available = False

    result = await client.export_screenshot("/designs/v1.pen")
    assert result is None


async def test_export_screenshot_mcp_error():
    """export_screenshot returns None on MCP error (graceful)."""
    client = PencilMCPClient()
    client._mcp_available = True

    with patch.object(
        client,
        "_call_mcp_tool",
        new_callable=AsyncMock,
        side_effect=Exception("MCP error"),
    ):
        result = await client.export_screenshot("/designs/v1.pen")
    assert result is None


# ---------------------------------------------------------------------------
# PencilMCPClient — export_code
# ---------------------------------------------------------------------------


async def test_export_code_mcp_available():
    """export_code returns HTML/CSS string when MCP is available."""
    client = PencilMCPClient()
    client._mcp_available = True

    mock_code = "<div class='container'><h1>Hello</h1></div>"
    with patch.object(
        client,
        "_call_mcp_tool",
        new_callable=AsyncMock,
        return_value={"code": mock_code, "format": "html"},
    ):
        result = await client.export_code("/designs/v1.pen")

    assert result == mock_code


async def test_export_code_mcp_unavailable():
    """export_code returns None when MCP is unavailable."""
    client = PencilMCPClient()
    client._mcp_available = False

    result = await client.export_code("/designs/v1.pen")
    assert result is None


async def test_export_code_mcp_error():
    """export_code returns None on MCP error (graceful)."""
    client = PencilMCPClient()
    client._mcp_available = True

    with patch.object(
        client,
        "_call_mcp_tool",
        new_callable=AsyncMock,
        side_effect=Exception("MCP error"),
    ):
        result = await client.export_code("/designs/v1.pen")
    assert result is None


# ---------------------------------------------------------------------------
# Integration: Design Agent uses PencilMCPClient
# ---------------------------------------------------------------------------


async def test_design_agent_uses_pencil_mcp(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """Design Agent calls PencilMCPClient after generating spec, stores design_mockup."""
    from langchain_core.messages import AIMessage

    from src.agents.design import DesignAgent
    from src.core.llm_client import CallMetrics
    from src.core.state import create_initial_state

    # Build a valid design JSON response from LLM
    design_json = json.dumps(
        {
            "design_type": "ui_mockup",
            "deliverables": [
                {
                    "name": "homepage_desktop",
                    "format": "spec",
                    "dimensions": "1440x900",
                    "specs": {
                        "colors": ["#3B82F6"],
                        "fonts": ["Inter"],
                        "components_used": ["hero"],
                        "layout": "Full-width hero",
                        "responsive_notes": "Stack on mobile",
                        "dark_mode": "Invert bg",
                    },
                }
            ],
        }
    )

    mock_llm_client.call = AsyncMock(
        return_value=(
            AIMessage(content=design_json),
            CallMetrics(agent_name="design", model_id="claude-sonnet-4-5", provider="anthropic"),
        )
    )

    agent = DesignAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    project = {
        "project_id": "p1",
        "job_id": "j1",
        "platform": "freelancer",
        "client": {"name": "Test"},
        "requirements": "Build a responsive landing page",
        "budget": 500.0,
        "deadline": "2026-03-15",
    }
    state = create_initial_state(project=project, first_agent="design", thread_id="t-pencil")

    # Mock get_pencil_client so we don't need real MCP
    mock_pencil_result = PencilDesignResult(
        pen_file_path=None,
        screenshot_path=None,
        design_spec={"project_type": "ui_mockup"},
    )
    mock_pencil = AsyncMock()
    mock_pencil.create_design = AsyncMock(return_value=mock_pencil_result)

    with patch("src.integrations.pencil_mcp.get_pencil_client", return_value=mock_pencil):
        with patch.object(agent, "_log_design_generated", new_callable=AsyncMock):
            result = await agent._execute(state)

    # design_mockup artifact should be present
    assert "design_mockup" in result["artifacts"]
    mockup = json.loads(result["artifacts"]["design_mockup"][0])
    assert "design_spec" in mockup


async def test_design_agent_pencil_failure_still_succeeds(
    mock_llm_client: AsyncMock,
    mock_heartbeat: Any,
    mock_loop_detector: Any,
):
    """Design Agent succeeds even when PencilMCPClient raises an exception."""
    from langchain_core.messages import AIMessage

    from src.agents.design import DesignAgent
    from src.core.llm_client import CallMetrics
    from src.core.state import create_initial_state

    design_json = json.dumps(
        {
            "design_type": "ui_mockup",
            "deliverables": [
                {
                    "name": "page_desktop",
                    "format": "spec",
                    "dimensions": "1440x900",
                    "specs": {
                        "colors": ["#3B82F6"],
                        "fonts": ["Inter"],
                        "components_used": ["hero"],
                        "layout": "Hero section",
                        "responsive_notes": "Stack mobile",
                        "dark_mode": "Dark bg",
                    },
                }
            ],
        }
    )

    mock_llm_client.call = AsyncMock(
        return_value=(
            AIMessage(content=design_json),
            CallMetrics(agent_name="design", model_id="claude-sonnet-4-5", provider="anthropic"),
        )
    )

    agent = DesignAgent(
        llm_client=mock_llm_client,
        heartbeat=mock_heartbeat,
        loop_detector=mock_loop_detector,
    )

    project = {
        "project_id": "p1",
        "job_id": "j1",
        "platform": "freelancer",
        "client": {"name": "Test"},
        "requirements": "Build a landing page",
        "budget": 500.0,
        "deadline": "2026-03-15",
    }
    state = create_initial_state(project=project, first_agent="design", thread_id="t-pencil-fail")

    mock_pencil = AsyncMock()
    mock_pencil.create_design = AsyncMock(side_effect=Exception("MCP crashed"))

    with patch("src.integrations.pencil_mcp.get_pencil_client", return_value=mock_pencil):
        with patch.object(agent, "_log_design_generated", new_callable=AsyncMock):
            result = await agent._execute(state)

    # Agent must still succeed with design artifacts (without design_mockup)
    assert "design" in result["artifacts"]
    assert result["status"] == "active"


# ---------------------------------------------------------------------------
# Path validation tests
# ---------------------------------------------------------------------------


def test_validate_pen_path_valid():
    """_validate_pen_path accepts a valid .pen path."""
    from src.integrations.pencil_mcp import _validate_pen_path

    # Should not raise
    _validate_pen_path("/designs/v1.pen")
    _validate_pen_path("project/mockup.pen")


def test_validate_pen_path_rejects_traversal():
    """_validate_pen_path rejects paths containing '..'."""
    from src.integrations.pencil_mcp import _validate_pen_path

    with pytest.raises(ValueError, match="directory traversal"):
        _validate_pen_path("../../etc/passwd.pen")

    with pytest.raises(ValueError, match="directory traversal"):
        _validate_pen_path("/designs/../secret.pen")


def test_validate_pen_path_rejects_wrong_extension():
    """_validate_pen_path rejects paths not ending in .pen."""
    from src.integrations.pencil_mcp import _validate_pen_path

    with pytest.raises(ValueError, match="must end with .pen"):
        _validate_pen_path("/designs/v1.png")

    with pytest.raises(ValueError, match="must end with .pen"):
        _validate_pen_path("/designs/v1.pen.exe")


async def test_export_screenshot_rejects_bad_path():
    """export_screenshot raises ValueError for invalid paths."""
    client = PencilMCPClient()
    client._mcp_available = True

    with pytest.raises(ValueError, match="directory traversal"):
        await client.export_screenshot("../../etc/passwd.pen")

    with pytest.raises(ValueError, match="must end with .pen"):
        await client.export_screenshot("/designs/v1.png")


async def test_export_code_rejects_bad_path():
    """export_code raises ValueError for invalid paths."""
    client = PencilMCPClient()
    client._mcp_available = True

    with pytest.raises(ValueError, match="directory traversal"):
        await client.export_code("../../../secret.pen")

    with pytest.raises(ValueError, match="must end with .pen"):
        await client.export_code("/designs/v1.txt")


# ---------------------------------------------------------------------------
# Singleton tests
# ---------------------------------------------------------------------------


def test_get_pencil_client_returns_singleton():
    """get_pencil_client returns the same instance on repeated calls."""
    import src.integrations.pencil_mcp as mod
    from src.integrations.pencil_mcp import get_pencil_client

    # Reset the singleton so the test is isolated.
    mod._pencil_client = None

    client1 = get_pencil_client()
    client2 = get_pencil_client()
    assert client1 is client2
    assert isinstance(client1, PencilMCPClient)

    # Clean up.
    mod._pencil_client = None


def test_get_pencil_client_creates_instance():
    """get_pencil_client creates a PencilMCPClient when none exists."""
    import src.integrations.pencil_mcp as mod
    from src.integrations.pencil_mcp import get_pencil_client

    mod._pencil_client = None
    client = get_pencil_client()
    assert client is not None
    assert mod._pencil_client is client

    # Clean up.
    mod._pencil_client = None


# ---------------------------------------------------------------------------
# create_design exports code after screenshot
# ---------------------------------------------------------------------------


async def test_create_design_exports_code():
    """create_design calls export_code after screenshot and stores result."""
    client = PencilMCPClient()
    client._mcp_available = True

    spec = build_design_spec(project_type="landing_page")
    expected_code = "<div class='hero'>Landing</div>"

    with patch.object(
        client,
        "_call_mcp_tool",
        new_callable=AsyncMock,
        side_effect=[
            {"pen_file_path": "/project/v1.pen", "preview_link": None},  # batch_design
            {"path": "/project/screenshot.png"},  # export_nodes / screenshot
            {"code": expected_code, "format": "html"},  # export_nodes / code
        ],
    ):
        result = await client.create_design(spec)

    assert result.code_export == expected_code
    assert result.pen_file_path == "/project/v1.pen"


async def test_create_design_code_export_failure_still_succeeds():
    """create_design succeeds even when code export fails."""
    client = PencilMCPClient()
    client._mcp_available = True

    spec = build_design_spec(project_type="landing_page")

    call_count = 0

    async def _side_effect(tool_name, params):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {"pen_file_path": "/project/v1.pen", "preview_link": None}
        if call_count == 2:
            return {"path": "/project/screenshot.png"}
        # Third call (export_code) fails
        raise Exception("code export failed")

    with patch.object(client, "_call_mcp_tool", new_callable=AsyncMock, side_effect=_side_effect):
        result = await client.create_design(spec)

    assert result.pen_file_path == "/project/v1.pen"
    assert result.code_export is None  # graceful degradation
    assert result.is_fallback is False
