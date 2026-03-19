"""Pencil.dev MCP integration for the Design Agent.

Provides a pluggable client that calls Pencil.dev MCP tools to create
visual mockups (.pen files), export PNG screenshots, and generate code.

When the MCP server is unavailable, all methods fall back gracefully:
- create_design() returns the JSON spec only (current behavior)
- export_screenshot() returns None
- export_code() returns None

Usage::

    client = PencilMCPClient()
    spec = build_design_spec(project_type="landing_page", ...)
    result = await client.create_design(spec)
    # result.pen_file_path, result.screenshot_path, result.design_spec

Architecture:
    The MCP tools are called via ``_call_mcp_tool`` which wraps the
    actual MCP invocation.  This indirection makes it easy to mock
    in tests and to swap transport (stdio, HTTP, etc.) later.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Default style when none is provided by the caller.
_DEFAULT_STYLE: dict[str, Any] = {
    "theme": "modern_minimal",
    "colors": ["#F8FAFC", "#0F172A", "#3B82F6", "#10B981"],
    "typography": "Inter / system-ui",
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class PencilDesignResult:
    """Result of a Pencil.dev design generation operation.

    Attributes:
        pen_file_path: Path to the generated .pen file, or None if MCP
            was unavailable (fallback mode).
        screenshot_path: Path to the exported PNG screenshot, or None.
        design_spec: The Pencil.dev-compatible design specification dict
            that was used (always present).
        preview_link: Public preview URL from Pencil.dev, or None.
        code_export: Exported HTML/CSS/React code string, or None.
    """

    pen_file_path: str | None
    screenshot_path: str | None
    design_spec: dict[str, Any]
    preview_link: str | None = None
    code_export: str | None = None

    @property
    def is_fallback(self) -> bool:
        """True when the result is a fallback (no .pen file generated)."""
        return self.pen_file_path is None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary for state storage."""
        return {
            "pen_file_path": self.pen_file_path,
            "screenshot_path": self.screenshot_path,
            "design_spec": self.design_spec,
            "preview_link": self.preview_link,
            "code_export": self.code_export,
        }


# ---------------------------------------------------------------------------
# Spec builder
# ---------------------------------------------------------------------------


def build_design_spec(
    *,
    project_type: str,
    style: dict[str, Any] | None = None,
    sections: list[dict[str, Any]] | None = None,
    responsive: bool = True,
    export_format: str = "html",
) -> dict[str, Any]:
    """Build a Pencil.dev-compatible design specification JSON.

    This is the structured input that drives Pencil.dev's AI design
    generation via its MCP ``batch_design`` tool.

    Args:
        project_type: Type of project (e.g. "landing_page", "dashboard",
            "web_app", "email_template").
        style: Style configuration with theme, colors, typography.
            Defaults to modern_minimal with neutral palette.
        sections: List of section dicts describing the page structure.
            Each section has at minimum a ``type`` key.
        responsive: Whether to generate responsive variants.
        export_format: Target export format ("html", "react", "vue").

    Returns:
        A dict matching the Pencil.dev design spec schema.
    """
    resolved_style = dict(_DEFAULT_STYLE)
    if style:
        resolved_style.update(style)

    return {
        "project_type": project_type,
        "style": resolved_style,
        "sections": list(sections) if sections else [],
        "responsive": responsive,
        "export_format": export_format,
    }


# ---------------------------------------------------------------------------
# MCP Client
# ---------------------------------------------------------------------------


class PencilMCPClient:
    """Client for Pencil.dev MCP tools.

    Wraps MCP tool invocations with graceful fallback when the Pencil.dev
    MCP server is not available.  All public methods are safe to call
    regardless of MCP availability -- they return ``None`` or fallback
    results instead of raising.

    Usage::

        client = get_pencil_client()
        spec = build_design_spec(project_type="landing_page")
        result = await client.create_design(spec)
    """

    def __init__(self) -> None:
        self._mcp_available: bool | None = None  # None = not yet checked
        self._log = logger.bind(component="pencil_mcp")

    # ------------------------------------------------------------------
    # Availability check
    # ------------------------------------------------------------------

    async def check_availability(self) -> bool:
        """Check whether the Pencil.dev MCP server is reachable.

        Calls a lightweight MCP tool (``get_editor_state``) to probe
        connectivity.  Caches the result for the lifetime of this client
        instance.

        Returns:
            True if MCP is available, False otherwise.
        """
        try:
            await self._call_mcp_tool("get_editor_state", {})
            self._mcp_available = True
            self._log.info("pencil_mcp_available")
            return True
        except Exception:
            self._mcp_available = False
            self._log.info("pencil_mcp_unavailable", exc_info=True)
            return False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create_design(self, spec: dict[str, Any]) -> PencilDesignResult:
        """Create a visual design from a Pencil.dev spec.

        When MCP is available:
            1. Calls ``batch_design`` to create the .pen file.
            2. Calls ``export_nodes`` to generate a screenshot.
            3. Returns full PencilDesignResult with paths.

        When MCP is unavailable:
            Returns a fallback result containing only the spec.

        Args:
            spec: A Pencil.dev design specification dict (from
                :func:`build_design_spec`).

        Returns:
            A :class:`PencilDesignResult` (always non-None).
        """
        # Auto-check availability if not yet determined.
        if self._mcp_available is None:
            await self.check_availability()

        if not self._mcp_available:
            self._log.info("pencil_create_design_fallback", project_type=spec.get("project_type"))
            return PencilDesignResult(
                pen_file_path=None,
                screenshot_path=None,
                design_spec=spec,
            )

        try:
            # Step 1: Create the design via batch_design MCP tool.
            design_response = await self._call_mcp_tool(
                "batch_design",
                {
                    "designs": [
                        {
                            "prompt": self._spec_to_prompt(spec),
                            "style": spec.get("style", {}),
                            "responsive": spec.get("responsive", True),
                        }
                    ],
                },
            )

            pen_file_path = design_response.get("pen_file_path")
            preview_link = design_response.get("preview_link")

            # Step 2: Export screenshot.
            screenshot_path = None
            try:
                screenshot_response = await self._call_mcp_tool(
                    "export_nodes",
                    {
                        "pen_file": pen_file_path,
                        "format": "png",
                        "scale": 2,
                    },
                )
                screenshot_path = screenshot_response.get("path")
            except Exception:
                self._log.warning("pencil_screenshot_export_failed", exc_info=True)

            # Step 3: Export code (HTML/CSS/React).
            code_export = None
            if pen_file_path:
                try:
                    code_export = await self.export_code(pen_file_path)
                except Exception:
                    self._log.warning("pencil_code_export_failed", exc_info=True)

            self._log.info(
                "pencil_design_created",
                pen_file=pen_file_path,
                screenshot=screenshot_path,
                has_code=code_export is not None,
            )

            return PencilDesignResult(
                pen_file_path=pen_file_path,
                screenshot_path=screenshot_path,
                design_spec=spec,
                preview_link=preview_link,
                code_export=code_export,
            )

        except Exception:
            self._log.warning(
                "pencil_create_design_error_fallback",
                project_type=spec.get("project_type"),
                exc_info=True,
            )
            return PencilDesignResult(
                pen_file_path=None,
                screenshot_path=None,
                design_spec=spec,
            )

    async def export_screenshot(self, pen_file_path: str) -> bytes | None:
        """Export a PNG screenshot from an existing .pen file.

        Args:
            pen_file_path: Path to the .pen file.

        Returns:
            PNG bytes, or None if MCP is unavailable or export fails.

        Raises:
            ValueError: If ``pen_file_path`` contains ``..`` or does not
                end with ``.pen``.
        """
        _validate_pen_path(pen_file_path)

        if not self._mcp_available:
            return None

        try:
            await self._call_mcp_tool(
                "export_nodes",
                {"pen_file": pen_file_path, "format": "png", "scale": 2},
            )
            return await self._export_screenshot_bytes(pen_file_path)
        except Exception:
            self._log.warning("pencil_export_screenshot_failed", pen_file=pen_file_path, exc_info=True)
            return None

    async def export_code(self, pen_file_path: str) -> str | None:
        """Export HTML/CSS/React code from an existing .pen file.

        Args:
            pen_file_path: Path to the .pen file.

        Returns:
            Code string, or None if MCP is unavailable or export fails.

        Raises:
            ValueError: If ``pen_file_path`` contains ``..`` or does not
                end with ``.pen``.
        """
        _validate_pen_path(pen_file_path)

        if not self._mcp_available:
            return None

        try:
            response = await self._call_mcp_tool(
                "export_nodes",
                {"pen_file": pen_file_path, "format": "code"},
            )
            return response.get("code")
        except Exception:
            self._log.warning("pencil_export_code_failed", pen_file=pen_file_path, exc_info=True)
            return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _call_mcp_tool(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        """Invoke a Pencil.dev MCP tool.

        This is the single point of MCP communication.  In production,
        this calls the actual MCP server.  In tests, it is mocked.

        Args:
            tool_name: Name of the MCP tool (e.g. "batch_design",
                "export_nodes", "get_editor_state").
            params: Tool parameters dict.

        Returns:
            The tool's response dict.

        Raises:
            Exception: On MCP communication failure.
        """
        # The actual MCP invocation would go here.  For now, we raise
        # to signal that MCP is not connected (the check_availability
        # path catches this and sets _mcp_available=False).
        raise NotImplementedError(
            f"Pencil.dev MCP tool '{tool_name}' not connected. Configure MCP server to enable design generation."
        )

    async def _export_screenshot_bytes(self, pen_file_path: str) -> bytes | None:
        """Read the exported screenshot file and return its bytes.

        This is separated from _call_mcp_tool for easier mocking.
        In production, reads the file from the path returned by
        export_nodes.

        Args:
            pen_file_path: Path to the .pen file (screenshot path is
                derived from it).

        Returns:
            PNG bytes, or None if the file doesn't exist.
        """
        # In production this would read the file from disk/cloud.
        # For now returns None as the MCP integration is pluggable.
        return None

    @staticmethod
    def _spec_to_prompt(spec: dict[str, Any]) -> str:
        """Convert a design spec dict to a text prompt for Pencil.dev.

        Args:
            spec: Design specification dict.

        Returns:
            A descriptive text prompt for the AI design tool.
        """
        parts: list[str] = []

        project_type = spec.get("project_type", "web page")
        parts.append(f"Create a {project_type} design")

        style = spec.get("style", {})
        if style.get("theme"):
            parts.append(f"with a {style['theme']} theme")
        if style.get("colors"):
            color_str = ", ".join(style["colors"][:4])
            parts.append(f"using colors: {color_str}")
        if style.get("typography"):
            parts.append(f"with {style['typography']} typography")

        sections = spec.get("sections", [])
        if sections:
            section_types = [s.get("type", "section") for s in sections]
            parts.append(f"including sections: {', '.join(section_types)}")

        if spec.get("responsive"):
            parts.append("with responsive design for desktop, tablet, and mobile")

        return ". ".join(parts) + "."


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------


def _validate_pen_path(pen_file_path: str) -> None:
    """Validate that a .pen file path is safe to use.

    Rejects paths containing ``..`` (directory traversal) and paths that
    do not end with the ``.pen`` extension.

    Args:
        pen_file_path: Path to validate.

    Raises:
        ValueError: If the path is invalid.
    """
    if ".." in pen_file_path:
        raise ValueError(f"Invalid pen file path (directory traversal): {pen_file_path}")
    if not pen_file_path.endswith(".pen"):
        raise ValueError(f"Invalid pen file path (must end with .pen): {pen_file_path}")


# ---------------------------------------------------------------------------
# Module-level lazy singleton
# ---------------------------------------------------------------------------

_pencil_client: PencilMCPClient | None = None


def get_pencil_client() -> PencilMCPClient:
    """Return the module-level :class:`PencilMCPClient` singleton.

    Creates the instance on first call (lazy initialization).  Subsequent
    calls return the same instance, preserving MCP availability state and
    avoiding per-call overhead.
    """
    global _pencil_client  # noqa: PLW0603
    if _pencil_client is None:
        _pencil_client = PencilMCPClient()
    return _pencil_client
