"""Tests for P2.11 — Scout Categories + Free-Text Custom Rules.

Covers:
- ScoutConfigSchema validation
- GET /api/v1/settings/scout-config endpoint
- PUT /api/v1/settings/scout-config endpoint
- Scout agent config loading and score modification
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULT_AUTO = ["landings", "bots", "api_backend", "design", "content", "small_fixes"]
_DEFAULT_SUGGEST = [
    "mobile_apps",
    "ml_ai",
    "devops",
    "consulting",
    "ecommerce",
    "system_integration",
]


def _mock_request(user_id: str | None = None) -> MagicMock:
    """Create a mock Litestar request with auth token."""
    req = MagicMock()
    req.auth = MagicMock()
    req.auth.sub = user_id or str(uuid.uuid4())
    req.user = MagicMock()
    req.user.id = uuid.UUID(req.auth.sub)
    req.user.email = "test@example.com"
    return req


# ===========================================================================
# 1. Schema tests
# ===========================================================================


class TestScoutConfigSchema:
    """Tests for ScoutConfigSchema validation."""

    def test_valid_config(self) -> None:
        """Valid scout config passes validation."""
        from src.api.schemas import ScoutConfigSchema

        schema = ScoutConfigSchema(
            categories_auto=["landings", "bots"],
            categories_suggest=["mobile_apps"],
            custom_rules=["Если в заказе Figma — брать"],
        )
        assert schema.categories_auto == ["landings", "bots"]
        assert len(schema.custom_rules) == 1

    def test_empty_config(self) -> None:
        """Empty lists are valid (clear all categories)."""
        from src.api.schemas import ScoutConfigSchema

        schema = ScoutConfigSchema(
            categories_auto=[],
            categories_suggest=[],
            custom_rules=[],
        )
        assert schema.categories_auto == []

    def test_defaults(self) -> None:
        """Default factory provides standard categories."""
        from src.api.schemas import ScoutConfigSchema

        schema = ScoutConfigSchema()
        assert len(schema.categories_auto) > 0
        assert len(schema.categories_suggest) > 0
        assert schema.custom_rules == []

    def test_custom_rules_max_length(self) -> None:
        """Custom rules list limited to 50 entries."""
        from src.api.schemas import ScoutConfigSchema

        with pytest.raises(ValueError):
            ScoutConfigSchema(
                categories_auto=[],
                categories_suggest=[],
                custom_rules=["rule"] * 51,
            )

    def test_custom_rule_max_text_length(self) -> None:
        """Individual custom rule text limited to 500 chars."""
        from src.api.schemas import ScoutConfigSchema

        with pytest.raises(ValueError):
            ScoutConfigSchema(
                categories_auto=[],
                categories_suggest=[],
                custom_rules=["x" * 501],
            )

    def test_serialization(self) -> None:
        """Schema serializes to dict correctly."""
        from src.api.schemas import ScoutConfigSchema

        schema = ScoutConfigSchema(
            categories_auto=["landings"],
            categories_suggest=["ml_ai"],
            custom_rules=["test rule"],
        )
        data = schema.model_dump()
        assert "categories_auto" in data
        assert "categories_suggest" in data
        assert "custom_rules" in data


# ===========================================================================
# 2. GET /api/v1/settings/scout-config
# ===========================================================================


class TestGetScoutConfig:
    """Tests for GET /api/v1/settings/scout-config."""

    @pytest.mark.asyncio
    async def test_returns_defaults_when_no_config(self) -> None:
        """Returns default categories when no config stored."""
        from src.api.routes.settings import SettingsController

        controller = SettingsController(owner=MagicMock())
        db_session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        db_session.execute.return_value = result_mock

        request = _mock_request()
        result = await controller.get_scout_config.fn(
            controller,
            request=request,
            db_session=db_session,
        )

        assert "categories_auto" in result
        assert "categories_suggest" in result
        assert "custom_rules" in result
        assert len(result["categories_auto"]) > 0
        assert result["custom_rules"] == []

    @pytest.mark.asyncio
    async def test_returns_stored_config(self) -> None:
        """Returns stored config when it exists."""
        from src.api.routes.settings import SettingsController

        controller = SettingsController(owner=MagicMock())
        db_session = AsyncMock()

        stored_config = {
            "categories_auto": ["landings", "bots"],
            "categories_suggest": ["ml_ai"],
            "custom_rules": ["Заказы до $50 — пропускать"],
        }

        account = MagicMock()
        account.credentials = stored_config  # Will be handled by decryption mock

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = account
        db_session.execute.return_value = result_mock

        request = _mock_request()

        with patch("src.api.routes.settings.decrypt_credentials", return_value=stored_config):
            result = await controller.get_scout_config.fn(
                controller,
                request=request,
                db_session=db_session,
            )

        assert result["categories_auto"] == ["landings", "bots"]
        assert result["categories_suggest"] == ["ml_ai"]
        assert result["custom_rules"] == ["Заказы до $50 — пропускать"]


# ===========================================================================
# 3. PUT /api/v1/settings/scout-config
# ===========================================================================


class TestUpdateScoutConfig:
    """Tests for PUT /api/v1/settings/scout-config."""

    @pytest.mark.asyncio
    async def test_creates_config_when_none_exists(self) -> None:
        """Creates new scout config when no existing record."""
        from src.api.routes.settings import SettingsController
        from src.api.schemas import ScoutConfigSchema

        controller = SettingsController(owner=MagicMock())
        db_session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        db_session.execute.return_value = result_mock
        db_session.add = MagicMock()

        request = _mock_request()
        data = ScoutConfigSchema(
            categories_auto=["landings"],
            categories_suggest=["ml_ai"],
            custom_rules=["test rule"],
        )

        with patch("src.api.routes.settings.encrypt_credentials") as mock_encrypt:
            mock_encrypt.return_value = b"encrypted-data"
            result = await controller.update_scout_config.fn(
                controller,
                data=data,
                request=request,
                db_session=db_session,
            )

        assert result["status"] == "saved"
        db_session.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_updates_existing_config(self) -> None:
        """Updates existing scout config record."""
        from src.api.routes.settings import SettingsController
        from src.api.schemas import ScoutConfigSchema

        controller = SettingsController(owner=MagicMock())
        db_session = AsyncMock()

        existing_account = MagicMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = existing_account
        db_session.execute.return_value = result_mock

        request = _mock_request()
        data = ScoutConfigSchema(
            categories_auto=["bots"],
            categories_suggest=["devops"],
            custom_rules=[],
        )

        with patch("src.api.routes.settings.encrypt_credentials") as mock_encrypt:
            mock_encrypt.return_value = b"encrypted-data"
            result = await controller.update_scout_config.fn(
                controller,
                data=data,
                request=request,
                db_session=db_session,
            )

        assert result["status"] == "saved"
        assert existing_account.credentials == b"encrypted-data"

    @pytest.mark.asyncio
    async def test_rejects_overlapping_categories(self) -> None:
        """Cannot have same category in both auto and suggest."""
        from src.api.schemas import ScoutConfigSchema

        with pytest.raises(ValueError):
            ScoutConfigSchema(
                categories_auto=["landings", "bots"],
                categories_suggest=["landings"],  # overlap
                custom_rules=[],
            )


# ===========================================================================
# 4. Scout agent config loading
# ===========================================================================


class TestScoutConfigLoading:
    """Tests for Scout agent loading and applying config."""

    @pytest.mark.asyncio
    async def test_load_scout_config_from_db(self) -> None:
        """load_scout_config retrieves config from DB."""
        from src.agents.scout import load_scout_config

        mock_session = AsyncMock()
        stored_config = {
            "categories_auto": ["landings"],
            "categories_suggest": ["ml_ai"],
            "custom_rules": ["test rule"],
        }

        account = MagicMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = account
        mock_session.execute.return_value = result_mock

        with patch("src.agents.scout.decrypt_credentials", return_value=stored_config):
            config = await load_scout_config(mock_session)

        assert config["categories_auto"] == ["landings"]
        assert config["categories_suggest"] == ["ml_ai"]
        assert config["custom_rules"] == ["test rule"]

    @pytest.mark.asyncio
    async def test_load_scout_config_returns_defaults_when_missing(self) -> None:
        """load_scout_config returns defaults when no config stored."""
        from src.agents.scout import load_scout_config

        mock_session = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = result_mock

        config = await load_scout_config(mock_session)

        assert len(config["categories_auto"]) > 0
        assert len(config["categories_suggest"]) > 0
        assert config["custom_rules"] == []

    def test_apply_category_score_modifier_auto(self) -> None:
        """Auto-take category gets no score modifier."""
        from src.agents.scout import apply_category_modifier

        config = {
            "categories_auto": ["landings", "bots"],
            "categories_suggest": ["ml_ai"],
        }
        modifier = apply_category_modifier("landings", config)
        assert modifier == 0.0

    def test_apply_category_score_modifier_suggest(self) -> None:
        """Suggest category gets -0.2 score modifier."""
        from src.agents.scout import apply_category_modifier

        config = {
            "categories_auto": ["landings"],
            "categories_suggest": ["ml_ai", "mobile_apps"],
        }
        modifier = apply_category_modifier("ml_ai", config)
        assert modifier == -0.2

    def test_apply_category_score_modifier_unknown(self) -> None:
        """Unknown category gets no modifier."""
        from src.agents.scout import apply_category_modifier

        config = {
            "categories_auto": ["landings"],
            "categories_suggest": ["ml_ai"],
        }
        modifier = apply_category_modifier("unknown_category", config)
        assert modifier == 0.0

    def test_apply_category_modifier_empty_config(self) -> None:
        """Empty config returns no modifier."""
        from src.agents.scout import apply_category_modifier

        config = {
            "categories_auto": [],
            "categories_suggest": [],
        }
        modifier = apply_category_modifier("landings", config)
        assert modifier == 0.0
