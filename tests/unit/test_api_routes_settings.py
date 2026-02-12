"""Unit tests for the Settings API endpoints.

Tests the ``SettingsController`` route handlers (``src.api.routes.settings``)
with mocked database dependencies.  Handler functions are tested directly
via ``.fn(controller, ...)`` to bypass Litestar DI, following the established
pattern from ``test_api_routes_hitl.py``.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from litestar.exceptions import ClientException, NotFoundException

from src.api.routes.settings import (
    _ALLOWED_PLATFORMS,
    _API_KEYS_PLATFORM,
    _KNOWN_API_KEYS,
    SettingsController,
    _build_api_key_summary,
    _mask_account,
)

pytestmark = [pytest.mark.asyncio]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TEST_USER_ID = uuid.uuid4()
_TEST_ACCOUNT_ID = uuid.uuid4()


def _create_mock_db_session() -> AsyncMock:
    """Create a mock AsyncSession for database testing."""
    session = AsyncMock()
    # db_session.add() is sync in SQLAlchemy — use MagicMock to avoid RuntimeWarning
    session.add = MagicMock()
    return session


def _create_mock_request(
    user_id: uuid.UUID | None = None,
    role: str = "owner",
) -> MagicMock:
    """Create a mock Request with auth and user attributes."""
    if user_id is None:
        user_id = _TEST_USER_ID

    mock_request = MagicMock()
    mock_request.auth = MagicMock()
    mock_request.auth.sub = str(user_id)
    mock_request.user = MagicMock()
    mock_request.user.id = user_id
    mock_request.user.role = role
    return mock_request


def _create_mock_account(
    account_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    platform: str = "freelancer",
    username: str | None = "test_user",
    credentials: dict | None = None,
    status: str = "active",
    profile_url: str | None = "https://freelancer.com/u/test",
    stats: dict | None = None,
) -> MagicMock:
    """Create a mock PlatformAccount ORM object."""
    if account_id is None:
        account_id = _TEST_ACCOUNT_ID
    if user_id is None:
        user_id = _TEST_USER_ID
    if credentials is None:
        credentials = {"_encrypted": "gAAAAABk_fake_token"}
    if stats is None:
        stats = {}

    mock = MagicMock()
    mock.id = account_id
    mock.user_id = user_id
    mock.platform = platform
    mock.username = username
    mock.credentials = credentials
    mock.status = status
    mock.profile_url = profile_url
    mock.stats = stats
    mock.last_health_check = None
    mock.created_at = datetime(2026, 1, 15, 10, 0, 0, tzinfo=UTC)
    mock.updated_at = datetime(2026, 1, 15, 10, 0, 0, tzinfo=UTC)
    return mock


# ---------------------------------------------------------------------------
# Tests for _mask_account helper
# ---------------------------------------------------------------------------


class TestMaskAccountHelper:
    """Tests for the _mask_account serialisation helper."""

    def test_masks_decryptable_credentials(self) -> None:
        """Should mask credentials when decryption succeeds."""
        account = _create_mock_account(
            credentials={"api_key": "sk-live-1234567890"},
        )
        with patch(
            "src.api.routes.settings.decrypt_credentials",
            return_value={"api_key": "sk-live-1234567890"},
        ):
            result = _mask_account(account)

        assert result["credentials_masked"]["api_key"].endswith("7890")
        assert result["credentials_masked"]["api_key"].startswith("*")

    def test_shows_opaque_marker_on_decryption_failure(self) -> None:
        """Should show opaque marker when decryption fails."""
        account = _create_mock_account()
        with patch(
            "src.api.routes.settings.decrypt_credentials",
            side_effect=Exception("decryption failed"),
        ):
            result = _mask_account(account)

        assert result["credentials_masked"] == {"_status": "encrypted (cannot preview)"}

    def test_serialises_all_fields(self) -> None:
        """Should include all expected fields in the output dict."""
        account = _create_mock_account()
        with patch(
            "src.api.routes.settings.decrypt_credentials",
            return_value={"key": "value1234"},
        ):
            result = _mask_account(account)

        assert result["id"] == str(account.id)
        assert result["platform"] == "freelancer"
        assert result["username"] == "test_user"
        assert result["status"] == "active"
        assert result["profile_url"] == "https://freelancer.com/u/test"
        assert result["stats"] == {}
        assert result["last_health_check"] is None
        assert result["created_at"] is not None
        assert result["updated_at"] is not None

    def test_formats_last_health_check_as_iso(self) -> None:
        """Should format last_health_check as ISO string when present."""
        account = _create_mock_account()
        account.last_health_check = datetime(2026, 2, 1, 12, 0, 0, tzinfo=UTC)
        with patch(
            "src.api.routes.settings.decrypt_credentials",
            return_value={},
        ):
            result = _mask_account(account)

        assert result["last_health_check"] == "2026-02-01T12:00:00+00:00"


# ---------------------------------------------------------------------------
# Tests for _build_api_key_summary helper
# ---------------------------------------------------------------------------


class TestBuildApiKeySummary:
    """Tests for the _build_api_key_summary helper."""

    def test_env_keys_configured(self) -> None:
        """Should show configured=True and source=environment for set env keys."""
        settings = MagicMock()
        settings.GEMINI_API_KEY = "gk-abcdef1234"
        settings.ANTHROPIC_API_KEY = ""
        settings.OPENAI_API_KEY = None
        settings.OPENROUTER_API_KEY = ""
        settings.E2B_API_KEY = ""
        settings.HUNTER_API_KEY = ""
        settings.APOLLO_API_KEY = ""
        settings.LANGSMITH_API_KEY = ""

        result = _build_api_key_summary(settings, user_account=None)

        assert result["gemini_api_key"]["configured"] is True
        assert result["gemini_api_key"]["source"] == "environment"
        assert result["gemini_api_key"]["masked"].endswith("1234")
        assert result["anthropic_api_key"]["configured"] is False
        assert result["anthropic_api_key"]["source"] is None

    def test_user_keys_override_env(self) -> None:
        """User-stored keys should override environment keys in the summary."""
        settings = MagicMock()
        settings.GEMINI_API_KEY = "env-key-1234"
        settings.ANTHROPIC_API_KEY = ""
        settings.OPENAI_API_KEY = ""
        settings.OPENROUTER_API_KEY = ""
        settings.E2B_API_KEY = ""
        settings.HUNTER_API_KEY = ""
        settings.APOLLO_API_KEY = ""
        settings.LANGSMITH_API_KEY = ""

        user_account = _create_mock_account(
            platform=_API_KEYS_PLATFORM,
            credentials={"_encrypted": "token"},
        )

        with patch(
            "src.api.routes.settings.decrypt_credentials",
            return_value={"gemini_api_key": "user-key-5678"},
        ):
            result = _build_api_key_summary(settings, user_account)

        assert result["gemini_api_key"]["source"] == "user"
        assert result["gemini_api_key"]["masked"].endswith("5678")

    def test_handles_decrypt_failure_gracefully(self) -> None:
        """Should fall back to env-only when user account decryption fails."""
        settings = MagicMock()
        settings.GEMINI_API_KEY = "env-key-1234"
        settings.ANTHROPIC_API_KEY = ""
        settings.OPENAI_API_KEY = ""
        settings.OPENROUTER_API_KEY = ""
        settings.E2B_API_KEY = ""
        settings.HUNTER_API_KEY = ""
        settings.APOLLO_API_KEY = ""
        settings.LANGSMITH_API_KEY = ""

        user_account = _create_mock_account(
            platform=_API_KEYS_PLATFORM,
        )

        with patch(
            "src.api.routes.settings.decrypt_credentials",
            side_effect=Exception("bad key"),
        ):
            result = _build_api_key_summary(settings, user_account)

        # Should still have env key, not crash
        assert result["gemini_api_key"]["configured"] is True
        assert result["gemini_api_key"]["source"] == "environment"

    def test_all_known_keys_present_in_summary(self) -> None:
        """All _KNOWN_API_KEYS should be present in the summary dict."""
        settings = MagicMock()
        for attr in [
            "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
            "OPENROUTER_API_KEY", "E2B_API_KEY", "HUNTER_API_KEY",
            "APOLLO_API_KEY", "LANGSMITH_API_KEY",
        ]:
            setattr(settings, attr, "")

        result = _build_api_key_summary(settings, user_account=None)
        for key_name in _KNOWN_API_KEYS:
            assert key_name in result


# ---------------------------------------------------------------------------
# Tests for get_credentials_overview
# ---------------------------------------------------------------------------


def _mock_settings_obj() -> MagicMock:
    """Build a mock Settings with all API key attributes empty."""
    mock_settings = MagicMock()
    for attr in [
        "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
        "OPENROUTER_API_KEY", "E2B_API_KEY", "HUNTER_API_KEY",
        "APOLLO_API_KEY", "LANGSMITH_API_KEY",
    ]:
        setattr(mock_settings, attr, "")
    return mock_settings


def _make_controller_self() -> MagicMock:
    """Build a MagicMock that delegates static helpers to the real class."""
    ctrl = MagicMock()
    ctrl._get_api_keys_account = SettingsController._get_api_keys_account
    ctrl._get_account = SettingsController._get_account
    return ctrl


class TestGetCredentialsOverview:
    """Tests for the GET /credentials endpoint."""

    async def test_returns_api_keys_and_platform_accounts(self) -> None:
        """Should return both api_keys and platform_accounts sections."""
        db_session = _create_mock_db_session()
        mock_request = _create_mock_request()

        # Mock _get_api_keys_account returns None (first execute)
        api_keys_result = MagicMock()
        api_keys_result.scalar_one_or_none.return_value = None

        # Mock platform accounts query (second execute)
        account = _create_mock_account()
        accounts_result = MagicMock()
        accounts_result.scalars.return_value.all.return_value = [account]

        db_session.execute.side_effect = [api_keys_result, accounts_result]

        with patch("src.api.routes.settings.get_settings", return_value=_mock_settings_obj(), create=True), \
             patch("src.api.routes.settings.decrypt_credentials", return_value={"key": "val1234"}):
            result = await SettingsController.get_credentials_overview.fn(
                _make_controller_self(),
                request=mock_request,
                db_session=db_session,
            )

        assert "api_keys" in result
        assert "platform_accounts" in result
        assert len(result["platform_accounts"]) == 1

    async def test_returns_empty_accounts_list(self) -> None:
        """Should return empty platform_accounts when none exist."""
        db_session = _create_mock_db_session()
        mock_request = _create_mock_request()

        api_keys_result = MagicMock()
        api_keys_result.scalar_one_or_none.return_value = None

        accounts_result = MagicMock()
        accounts_result.scalars.return_value.all.return_value = []

        db_session.execute.side_effect = [api_keys_result, accounts_result]

        with patch("src.api.routes.settings.get_settings", return_value=_mock_settings_obj(), create=True), \
             patch("src.api.routes.settings.decrypt_credentials", return_value={}):
            result = await SettingsController.get_credentials_overview.fn(
                _make_controller_self(),
                request=mock_request,
                db_session=db_session,
            )

        assert result["platform_accounts"] == []


# ---------------------------------------------------------------------------
# Tests for list_platform_accounts
# ---------------------------------------------------------------------------


class TestListPlatformAccounts:
    """Tests for the GET /platform-accounts endpoint."""

    async def test_returns_accounts_with_total(self) -> None:
        """Should return accounts list and total count."""
        db_session = _create_mock_db_session()
        mock_request = _create_mock_request()

        account1 = _create_mock_account(account_id=uuid.uuid4(), platform="freelancer")
        account2 = _create_mock_account(account_id=uuid.uuid4(), platform="upwork")

        query_result = MagicMock()
        query_result.scalars.return_value.all.return_value = [account1, account2]
        db_session.execute.return_value = query_result

        with patch("src.api.routes.settings.decrypt_credentials", return_value={"k": "v1234"}):
            result = await SettingsController.list_platform_accounts.fn(
                self=None,
                request=mock_request,
                db_session=db_session,
            )

        assert result["total"] == 2
        assert len(result["accounts"]) == 2

    async def test_returns_empty_when_no_accounts(self) -> None:
        """Should return empty list and zero total when no accounts exist."""
        db_session = _create_mock_db_session()
        mock_request = _create_mock_request()

        query_result = MagicMock()
        query_result.scalars.return_value.all.return_value = []
        db_session.execute.return_value = query_result

        result = await SettingsController.list_platform_accounts.fn(
            self=None,
            request=mock_request,
            db_session=db_session,
        )

        assert result["total"] == 0
        assert result["accounts"] == []


# ---------------------------------------------------------------------------
# Tests for create_platform_account
# ---------------------------------------------------------------------------


class TestCreatePlatformAccount:
    """Tests for the POST /platform-accounts endpoint."""

    async def test_creates_account_successfully(self) -> None:
        """Should create and return a masked account for a valid platform."""
        db_session = _create_mock_db_session()
        mock_request = _create_mock_request()

        # No duplicate found
        dup_result = MagicMock()
        dup_result.scalar_one_or_none.return_value = None
        db_session.execute.return_value = dup_result

        data = {
            "platform": "freelancer",
            "username": "my_user",
            "credentials": {"client_id": "abc", "client_secret": "xyz"},
            "profile_url": "https://freelancer.com/u/my_user",
        }

        with patch(
            "src.api.routes.settings.encrypt_credentials",
            return_value={"_encrypted": "fake_token"},  # noqa: S106
        ), patch(
            "src.api.routes.settings.decrypt_credentials",
            return_value={"client_id": "abc", "client_secret": "xyz"},
        ):
            result = await SettingsController.create_platform_account.fn(
                self=None,
                data=data,
                request=mock_request,
                db_session=db_session,
            )

        assert result["platform"] == "freelancer"
        assert result["username"] == "my_user"
        db_session.add.assert_called_once()
        db_session.flush.assert_awaited_once()

    async def test_rejects_invalid_platform(self) -> None:
        """Should raise 400 for an unknown platform name."""
        db_session = _create_mock_db_session()
        mock_request = _create_mock_request()

        data = {
            "platform": "invalid_platform",
            "credentials": {"key": "val"},
        }

        with pytest.raises(ClientException) as exc_info:
            await SettingsController.create_platform_account.fn(
                self=None,
                data=data,
                request=mock_request,
                db_session=db_session,
            )

        assert exc_info.value.status_code == 400
        assert "Invalid platform" in str(exc_info.value.detail)

    async def test_rejects_missing_credentials(self) -> None:
        """Should raise 400 when credentials field is missing."""
        db_session = _create_mock_db_session()
        mock_request = _create_mock_request()

        data = {"platform": "freelancer"}

        with pytest.raises(ClientException) as exc_info:
            await SettingsController.create_platform_account.fn(
                self=None,
                data=data,
                request=mock_request,
                db_session=db_session,
            )

        assert exc_info.value.status_code == 400
        assert "credentials" in str(exc_info.value.detail).lower()

    async def test_rejects_non_dict_credentials(self) -> None:
        """Should raise 400 when credentials is not a dict."""
        db_session = _create_mock_db_session()
        mock_request = _create_mock_request()

        data = {"platform": "freelancer", "credentials": "not-a-dict"}

        with pytest.raises(ClientException) as exc_info:
            await SettingsController.create_platform_account.fn(
                self=None,
                data=data,
                request=mock_request,
                db_session=db_session,
            )

        assert exc_info.value.status_code == 400

    async def test_rejects_duplicate_platform_account(self) -> None:
        """Should raise 409 when a platform account already exists for the user."""
        db_session = _create_mock_db_session()
        mock_request = _create_mock_request()

        # Duplicate found
        existing = _create_mock_account(platform="upwork")
        dup_result = MagicMock()
        dup_result.scalar_one_or_none.return_value = existing
        db_session.execute.return_value = dup_result

        data = {
            "platform": "upwork",
            "credentials": {"email": "x@y.com", "password": "pass"},  # noqa: S106
        }

        with pytest.raises(ClientException) as exc_info:
            await SettingsController.create_platform_account.fn(
                self=None,
                data=data,
                request=mock_request,
                db_session=db_session,
            )

        assert exc_info.value.status_code == 409
        assert "already exists" in str(exc_info.value.detail)

    async def test_all_allowed_platforms_accepted(self) -> None:
        """Every platform in _ALLOWED_PLATFORMS should be accepted."""
        for platform in _ALLOWED_PLATFORMS:
            db_session = _create_mock_db_session()
            mock_request = _create_mock_request()

            dup_result = MagicMock()
            dup_result.scalar_one_or_none.return_value = None
            db_session.execute.return_value = dup_result

            data = {
                "platform": platform,
                "credentials": {"key": "val"},
            }

            with patch(
                "src.api.routes.settings.encrypt_credentials",
                return_value={"_encrypted": "tok"},  # noqa: S106
            ), patch(
                "src.api.routes.settings.decrypt_credentials",
                return_value={"key": "val"},
            ):
                result = await SettingsController.create_platform_account.fn(
                    self=None,
                    data=data,
                    request=mock_request,
                    db_session=db_session,
                )

            assert result["platform"] == platform


# ---------------------------------------------------------------------------
# Tests for update_platform_account
# ---------------------------------------------------------------------------


class TestUpdatePlatformAccount:
    """Tests for the PUT /platform-accounts/{account_id} endpoint."""

    async def test_updates_username_and_status(self) -> None:
        """Should update optional fields on the account."""
        db_session = _create_mock_db_session()
        user_id = uuid.uuid4()
        mock_request = _create_mock_request(user_id=user_id)
        account_id = uuid.uuid4()

        account = _create_mock_account(account_id=account_id, user_id=user_id)

        query_result = MagicMock()
        query_result.scalar_one_or_none.return_value = account
        db_session.execute.return_value = query_result

        data = {"username": "new_username", "status": "inactive"}

        with patch("src.api.routes.settings.decrypt_credentials", return_value={"k": "v1234"}):
            await SettingsController.update_platform_account.fn(
                _make_controller_self(),
                account_id=account_id,
                data=data,
                request=mock_request,
                db_session=db_session,
            )

        assert account.username == "new_username"
        assert account.status == "inactive"
        db_session.flush.assert_awaited_once()

    async def test_re_encrypts_credentials(self) -> None:
        """Should re-encrypt credentials when provided in update data."""
        db_session = _create_mock_db_session()
        user_id = uuid.uuid4()
        mock_request = _create_mock_request(user_id=user_id)
        account_id = uuid.uuid4()

        account = _create_mock_account(account_id=account_id, user_id=user_id)

        query_result = MagicMock()
        query_result.scalar_one_or_none.return_value = account
        db_session.execute.return_value = query_result

        new_creds = {"new_key": "new_secret"}
        data = {"credentials": new_creds}

        with patch(
            "src.api.routes.settings.encrypt_credentials",
            return_value={"_encrypted": "new_token"},  # noqa: S106
        ) as mock_encrypt, patch(
            "src.api.routes.settings.decrypt_credentials",
            return_value=new_creds,
        ):
            await SettingsController.update_platform_account.fn(
                _make_controller_self(),
                account_id=account_id,
                data=data,
                request=mock_request,
                db_session=db_session,
            )

        mock_encrypt.assert_called_once_with(new_creds)
        assert account.credentials == {"_encrypted": "new_token"}

    async def test_rejects_update_by_non_owner(self) -> None:
        """Should raise 403 when a different user tries to update."""
        db_session = _create_mock_db_session()
        owner_id = uuid.uuid4()
        other_id = uuid.uuid4()
        mock_request = _create_mock_request(user_id=other_id)
        account_id = uuid.uuid4()

        account = _create_mock_account(account_id=account_id, user_id=owner_id)

        query_result = MagicMock()
        query_result.scalar_one_or_none.return_value = account
        db_session.execute.return_value = query_result

        with pytest.raises(ClientException) as exc_info:
            await SettingsController.update_platform_account.fn(
                _make_controller_self(),
                account_id=account_id,
                data={"username": "hacker"},
                request=mock_request,
                db_session=db_session,
            )

        assert exc_info.value.status_code == 403

    async def test_rejects_update_of_sentinel_account(self) -> None:
        """Should raise 400 when trying to update the __api_keys__ sentinel."""
        db_session = _create_mock_db_session()
        user_id = uuid.uuid4()
        mock_request = _create_mock_request(user_id=user_id)
        account_id = uuid.uuid4()

        account = _create_mock_account(
            account_id=account_id,
            user_id=user_id,
            platform=_API_KEYS_PLATFORM,
        )

        query_result = MagicMock()
        query_result.scalar_one_or_none.return_value = account
        db_session.execute.return_value = query_result

        with pytest.raises(ClientException) as exc_info:
            await SettingsController.update_platform_account.fn(
                _make_controller_self(),
                account_id=account_id,
                data={"username": "test"},
                request=mock_request,
                db_session=db_session,
            )

        assert exc_info.value.status_code == 400
        assert "api-keys" in str(exc_info.value.detail).lower()

    async def test_raises_404_for_nonexistent_account(self) -> None:
        """Should raise NotFoundException when account does not exist."""
        db_session = _create_mock_db_session()
        mock_request = _create_mock_request()
        account_id = uuid.uuid4()

        query_result = MagicMock()
        query_result.scalar_one_or_none.return_value = None
        db_session.execute.return_value = query_result

        with pytest.raises(NotFoundException):
            await SettingsController.update_platform_account.fn(
                _make_controller_self(),
                account_id=account_id,
                data={"username": "test"},
                request=mock_request,
                db_session=db_session,
            )

    async def test_rejects_non_dict_credentials_in_update(self) -> None:
        """Should raise 400 when credentials in update is not a dict."""
        db_session = _create_mock_db_session()
        user_id = uuid.uuid4()
        mock_request = _create_mock_request(user_id=user_id)
        account_id = uuid.uuid4()

        account = _create_mock_account(account_id=account_id, user_id=user_id)

        query_result = MagicMock()
        query_result.scalar_one_or_none.return_value = account
        db_session.execute.return_value = query_result

        with pytest.raises(ClientException) as exc_info:
            await SettingsController.update_platform_account.fn(
                _make_controller_self(),
                account_id=account_id,
                data={"credentials": "not-a-dict"},
                request=mock_request,
                db_session=db_session,
            )

        assert exc_info.value.status_code == 400

    async def test_updates_profile_url(self) -> None:
        """Should update profile_url when provided."""
        db_session = _create_mock_db_session()
        user_id = uuid.uuid4()
        mock_request = _create_mock_request(user_id=user_id)
        account_id = uuid.uuid4()

        account = _create_mock_account(account_id=account_id, user_id=user_id)

        query_result = MagicMock()
        query_result.scalar_one_or_none.return_value = account
        db_session.execute.return_value = query_result

        data = {"profile_url": "https://new-url.com/profile"}

        with patch("src.api.routes.settings.decrypt_credentials", return_value={"k": "v1234"}):
            await SettingsController.update_platform_account.fn(
                _make_controller_self(),
                account_id=account_id,
                data=data,
                request=mock_request,
                db_session=db_session,
            )

        assert account.profile_url == "https://new-url.com/profile"

    async def test_ignores_invalid_status_value(self) -> None:
        """Should not update status if the value is not in the allowed set."""
        db_session = _create_mock_db_session()
        user_id = uuid.uuid4()
        mock_request = _create_mock_request(user_id=user_id)
        account_id = uuid.uuid4()

        account = _create_mock_account(account_id=account_id, user_id=user_id, status="active")

        query_result = MagicMock()
        query_result.scalar_one_or_none.return_value = account
        db_session.execute.return_value = query_result

        data = {"status": "invalid_status"}

        with patch("src.api.routes.settings.decrypt_credentials", return_value={"k": "v1234"}):
            await SettingsController.update_platform_account.fn(
                _make_controller_self(),
                account_id=account_id,
                data=data,
                request=mock_request,
                db_session=db_session,
            )

        # Status should remain unchanged
        assert account.status == "active"


# ---------------------------------------------------------------------------
# Tests for delete_platform_account
# ---------------------------------------------------------------------------


class TestDeletePlatformAccount:
    """Tests for the DELETE /platform-accounts/{account_id} endpoint."""

    async def test_deletes_own_account(self) -> None:
        """Should delete the account when the caller owns it."""
        db_session = _create_mock_db_session()
        user_id = uuid.uuid4()
        mock_request = _create_mock_request(user_id=user_id)
        account_id = uuid.uuid4()

        account = _create_mock_account(
            account_id=account_id, user_id=user_id, platform="upwork",
        )

        query_result = MagicMock()
        query_result.scalar_one_or_none.return_value = account
        db_session.execute.return_value = query_result

        result = await SettingsController.delete_platform_account.fn(
            _make_controller_self(),
            account_id=account_id,
            request=mock_request,
            db_session=db_session,
        )

        assert result["id"] == str(account_id)
        assert "upwork" in result["message"]
        db_session.delete.assert_awaited_once_with(account)
        db_session.flush.assert_awaited_once()

    async def test_owner_role_can_delete_others_account(self) -> None:
        """An owner should be able to delete another user's account."""
        db_session = _create_mock_db_session()
        owner_id = uuid.uuid4()
        other_user_id = uuid.uuid4()
        mock_request = _create_mock_request(user_id=owner_id, role="owner")
        account_id = uuid.uuid4()

        account = _create_mock_account(
            account_id=account_id, user_id=other_user_id, platform="kwork",
        )

        query_result = MagicMock()
        query_result.scalar_one_or_none.return_value = account
        db_session.execute.return_value = query_result

        result = await SettingsController.delete_platform_account.fn(
            _make_controller_self(),
            account_id=account_id,
            request=mock_request,
            db_session=db_session,
        )

        assert result["id"] == str(account_id)
        db_session.delete.assert_awaited_once()

    async def test_co_owner_role_can_delete_others_account(self) -> None:
        """A co_owner should be able to delete another user's account."""
        db_session = _create_mock_db_session()
        co_owner_id = uuid.uuid4()
        other_user_id = uuid.uuid4()
        mock_request = _create_mock_request(user_id=co_owner_id, role="co_owner")
        account_id = uuid.uuid4()

        account = _create_mock_account(
            account_id=account_id, user_id=other_user_id, platform="fl_ru",
        )

        query_result = MagicMock()
        query_result.scalar_one_or_none.return_value = account
        db_session.execute.return_value = query_result

        result = await SettingsController.delete_platform_account.fn(
            _make_controller_self(),
            account_id=account_id,
            request=mock_request,
            db_session=db_session,
        )

        assert result["id"] == str(account_id)

    async def test_non_owner_cannot_delete_others_account(self) -> None:
        """A viewer/moderator should not be able to delete another user's account."""
        db_session = _create_mock_db_session()
        viewer_id = uuid.uuid4()
        other_user_id = uuid.uuid4()
        mock_request = _create_mock_request(user_id=viewer_id, role="viewer")
        account_id = uuid.uuid4()

        account = _create_mock_account(
            account_id=account_id, user_id=other_user_id, platform="freelancer",
        )

        query_result = MagicMock()
        query_result.scalar_one_or_none.return_value = account
        db_session.execute.return_value = query_result

        with pytest.raises(ClientException) as exc_info:
            await SettingsController.delete_platform_account.fn(
                _make_controller_self(),
                account_id=account_id,
                request=mock_request,
                db_session=db_session,
            )

        assert exc_info.value.status_code == 403

    async def test_rejects_deletion_of_sentinel_account(self) -> None:
        """Should raise 400 when trying to delete the __api_keys__ sentinel."""
        db_session = _create_mock_db_session()
        user_id = uuid.uuid4()
        mock_request = _create_mock_request(user_id=user_id)
        account_id = uuid.uuid4()

        account = _create_mock_account(
            account_id=account_id, user_id=user_id, platform=_API_KEYS_PLATFORM,
        )

        query_result = MagicMock()
        query_result.scalar_one_or_none.return_value = account
        db_session.execute.return_value = query_result

        with pytest.raises(ClientException) as exc_info:
            await SettingsController.delete_platform_account.fn(
                _make_controller_self(),
                account_id=account_id,
                request=mock_request,
                db_session=db_session,
            )

        assert exc_info.value.status_code == 400
        assert "API keys" in str(exc_info.value.detail)

    async def test_raises_404_for_nonexistent_account(self) -> None:
        """Should raise NotFoundException when account does not exist."""
        db_session = _create_mock_db_session()
        mock_request = _create_mock_request()
        account_id = uuid.uuid4()

        query_result = MagicMock()
        query_result.scalar_one_or_none.return_value = None
        db_session.execute.return_value = query_result

        with pytest.raises(NotFoundException):
            await SettingsController.delete_platform_account.fn(
                _make_controller_self(),
                account_id=account_id,
                request=mock_request,
                db_session=db_session,
            )


# ---------------------------------------------------------------------------
# Tests for update_api_keys
# ---------------------------------------------------------------------------


class TestUpdateApiKeys:
    """Tests for the PUT /api-keys endpoint."""

    async def test_creates_new_api_keys_account(self) -> None:
        """Should create a new sentinel row when none exists."""
        db_session = _create_mock_db_session()
        mock_request = _create_mock_request()

        # No existing api keys account
        api_keys_result = MagicMock()
        api_keys_result.scalar_one_or_none.return_value = None
        db_session.execute.return_value = api_keys_result

        data = {"gemini_api_key": "gk-new-1234", "anthropic_api_key": "ak-new-5678"}

        with patch(
            "src.api.routes.settings.encrypt_credentials",
            return_value={"_encrypted": "new_token"},  # noqa: S106
        ), patch(
            "src.api.routes.settings.decrypt_credentials",
            return_value={"gemini_api_key": "gk-new-1234", "anthropic_api_key": "ak-new-5678"},
        ):
            result = await SettingsController.update_api_keys.fn(
                _make_controller_self(),
                data=data,
                request=mock_request,
                db_session=db_session,
            )

        assert "api_keys" in result
        assert result["message"] == "API keys updated"
        db_session.add.assert_called_once()
        db_session.flush.assert_awaited_once()

    async def test_merges_with_existing_keys(self) -> None:
        """Should merge new keys with existing ones."""
        db_session = _create_mock_db_session()
        mock_request = _create_mock_request()

        existing_account = _create_mock_account(
            platform=_API_KEYS_PLATFORM,
            credentials={"_encrypted": "old_token"},
        )

        api_keys_result = MagicMock()
        api_keys_result.scalar_one_or_none.return_value = existing_account
        db_session.execute.return_value = api_keys_result

        # Existing has gemini, we add anthropic
        data = {"anthropic_api_key": "ak-new-5678"}

        decrypt_calls = [
            # First decrypt: existing credentials
            {"gemini_api_key": "gk-existing-1234"},
            # Second decrypt: final response
            {"gemini_api_key": "gk-existing-1234", "anthropic_api_key": "ak-new-5678"},
        ]

        with patch(
            "src.api.routes.settings.encrypt_credentials",
            return_value={"_encrypted": "merged_token"},  # noqa: S106
        ), patch(
            "src.api.routes.settings.decrypt_credentials",
            side_effect=decrypt_calls,
        ):
            result = await SettingsController.update_api_keys.fn(
                _make_controller_self(),
                data=data,
                request=mock_request,
                db_session=db_session,
            )

        assert result["api_keys"]["gemini_api_key"]["configured"] is True
        assert result["api_keys"]["anthropic_api_key"]["configured"] is True

    async def test_rejects_unknown_key_names(self) -> None:
        """Should raise 400 for unrecognised API key names."""
        db_session = _create_mock_db_session()
        mock_request = _create_mock_request()

        data = {"unknown_key": "value", "another_bad": "value"}

        with pytest.raises(ClientException) as exc_info:
            await SettingsController.update_api_keys.fn(
                _make_controller_self(),
                data=data,
                request=mock_request,
                db_session=db_session,
            )

        assert exc_info.value.status_code == 400
        assert "Unknown API key names" in str(exc_info.value.detail)

    async def test_filters_out_empty_values(self) -> None:
        """Empty string values should be filtered out before storage."""
        db_session = _create_mock_db_session()
        mock_request = _create_mock_request()

        api_keys_result = MagicMock()
        api_keys_result.scalar_one_or_none.return_value = None
        db_session.execute.return_value = api_keys_result

        data = {"gemini_api_key": "gk-real-1234", "anthropic_api_key": ""}

        with patch(
            "src.api.routes.settings.encrypt_credentials",
            return_value={"_encrypted": "tok"},  # noqa: S106
        ) as mock_encrypt, patch(
            "src.api.routes.settings.decrypt_credentials",
            return_value={"gemini_api_key": "gk-real-1234"},
        ):
            await SettingsController.update_api_keys.fn(
                _make_controller_self(),
                data=data,
                request=mock_request,
                db_session=db_session,
            )

        # encrypt_credentials should only receive the non-empty key
        encrypted_data = mock_encrypt.call_args[0][0]
        assert "anthropic_api_key" not in encrypted_data
        assert "gemini_api_key" in encrypted_data

    async def test_removes_keys_set_to_empty_on_merge(self) -> None:
        """Setting a key to empty string should remove it from the merged result."""
        db_session = _create_mock_db_session()
        mock_request = _create_mock_request()

        existing_account = _create_mock_account(
            platform=_API_KEYS_PLATFORM,
            credentials={"_encrypted": "old_token"},
        )

        api_keys_result = MagicMock()
        api_keys_result.scalar_one_or_none.return_value = existing_account
        db_session.execute.return_value = api_keys_result

        # User explicitly clears gemini key
        data = {"gemini_api_key": ""}

        decrypt_calls = [
            # First call: existing
            {"gemini_api_key": "gk-old-1234", "anthropic_api_key": "ak-1234"},
            # Second call: after merge (gemini removed)
            {"anthropic_api_key": "ak-1234"},
        ]

        with patch(
            "src.api.routes.settings.encrypt_credentials",
            return_value={"_encrypted": "updated_token"},  # noqa: S106
        ) as mock_encrypt, patch(
            "src.api.routes.settings.decrypt_credentials",
            side_effect=decrypt_calls,
        ):
            await SettingsController.update_api_keys.fn(
                _make_controller_self(),
                data=data,
                request=mock_request,
                db_session=db_session,
            )

        # The merged dict passed to encrypt should NOT contain gemini_api_key
        encrypted_data = mock_encrypt.call_args[0][0]
        assert "gemini_api_key" not in encrypted_data

    async def test_handles_decrypt_failure_on_merge(self) -> None:
        """Should treat existing credentials as empty dict when decryption fails."""
        db_session = _create_mock_db_session()
        mock_request = _create_mock_request()

        existing_account = _create_mock_account(
            platform=_API_KEYS_PLATFORM,
            credentials={"_encrypted": "corrupted_token"},
        )

        api_keys_result = MagicMock()
        api_keys_result.scalar_one_or_none.return_value = existing_account
        db_session.execute.return_value = api_keys_result

        data = {"gemini_api_key": "gk-fresh-1234"}

        decrypt_effects = [
            Exception("decryption failed"),  # First call: existing fails
            {"gemini_api_key": "gk-fresh-1234"},  # Second call: response
        ]

        with patch(
            "src.api.routes.settings.encrypt_credentials",
            return_value={"_encrypted": "new_token"},  # noqa: S106
        ), patch(
            "src.api.routes.settings.decrypt_credentials",
            side_effect=decrypt_effects,
        ):
            result = await SettingsController.update_api_keys.fn(
                _make_controller_self(),
                data=data,
                request=mock_request,
                db_session=db_session,
            )

        # Should succeed despite decryption failure
        assert result["message"] == "API keys updated"

    async def test_response_includes_all_known_keys(self) -> None:
        """Response should include status for all _KNOWN_API_KEYS."""
        db_session = _create_mock_db_session()
        mock_request = _create_mock_request()

        api_keys_result = MagicMock()
        api_keys_result.scalar_one_or_none.return_value = None
        db_session.execute.return_value = api_keys_result

        data = {"gemini_api_key": "gk-1234"}

        with patch(
            "src.api.routes.settings.encrypt_credentials",
            return_value={"_encrypted": "tok"},  # noqa: S106
        ), patch(
            "src.api.routes.settings.decrypt_credentials",
            return_value={"gemini_api_key": "gk-1234"},
        ):
            result = await SettingsController.update_api_keys.fn(
                _make_controller_self(),
                data=data,
                request=mock_request,
                db_session=db_session,
            )

        for key_name in _KNOWN_API_KEYS:
            assert key_name in result["api_keys"]
            assert "configured" in result["api_keys"][key_name]
            assert "masked" in result["api_keys"][key_name]

    async def test_configured_key_has_masked_value(self) -> None:
        """A configured key should show a masked value in the response."""
        db_session = _create_mock_db_session()
        mock_request = _create_mock_request()

        api_keys_result = MagicMock()
        api_keys_result.scalar_one_or_none.return_value = None
        db_session.execute.return_value = api_keys_result

        data = {"gemini_api_key": "gk-live-abcdef1234"}

        with patch(
            "src.api.routes.settings.encrypt_credentials",
            return_value={"_encrypted": "tok"},  # noqa: S106
        ), patch(
            "src.api.routes.settings.decrypt_credentials",
            return_value={"gemini_api_key": "gk-live-abcdef1234"},
        ):
            result = await SettingsController.update_api_keys.fn(
                _make_controller_self(),
                data=data,
                request=mock_request,
                db_session=db_session,
            )

        gemini = result["api_keys"]["gemini_api_key"]
        assert gemini["configured"] is True
        assert gemini["masked"].endswith("1234")
        assert gemini["masked"].startswith("*")

    async def test_unconfigured_key_shows_none_mask(self) -> None:
        """An unconfigured key should show configured=False and masked=None."""
        db_session = _create_mock_db_session()
        mock_request = _create_mock_request()

        api_keys_result = MagicMock()
        api_keys_result.scalar_one_or_none.return_value = None
        db_session.execute.return_value = api_keys_result

        data = {"gemini_api_key": "gk-1234"}

        with patch(
            "src.api.routes.settings.encrypt_credentials",
            return_value={"_encrypted": "tok"},  # noqa: S106
        ), patch(
            "src.api.routes.settings.decrypt_credentials",
            return_value={"gemini_api_key": "gk-1234"},
        ):
            result = await SettingsController.update_api_keys.fn(
                _make_controller_self(),
                data=data,
                request=mock_request,
                db_session=db_session,
            )

        # anthropic was never set
        anthropic = result["api_keys"]["anthropic_api_key"]
        assert anthropic["configured"] is False
        assert anthropic["masked"] is None


# ---------------------------------------------------------------------------
# Tests for _get_account helper (via update/delete)
# ---------------------------------------------------------------------------


class TestGetAccountHelper:
    """Tests for the static _get_account helper."""

    async def test_returns_account_when_found(self) -> None:
        """Should return the PlatformAccount when found by ID."""
        db_session = _create_mock_db_session()
        account_id = uuid.uuid4()
        account = _create_mock_account(account_id=account_id)

        query_result = MagicMock()
        query_result.scalar_one_or_none.return_value = account
        db_session.execute.return_value = query_result

        result = await SettingsController._get_account(db_session, account_id)
        assert result is account

    async def test_raises_404_when_not_found(self) -> None:
        """Should raise NotFoundException when account does not exist."""
        db_session = _create_mock_db_session()
        account_id = uuid.uuid4()

        query_result = MagicMock()
        query_result.scalar_one_or_none.return_value = None
        db_session.execute.return_value = query_result

        with pytest.raises(NotFoundException) as exc_info:
            await SettingsController._get_account(db_session, account_id)

        assert "not found" in str(exc_info.value.detail).lower()


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------


class TestModuleConstants:
    """Tests that module-level constants are properly defined."""

    def test_api_keys_platform_sentinel(self) -> None:
        """The sentinel platform name should be __api_keys__."""
        assert _API_KEYS_PLATFORM == "__api_keys__"

    def test_allowed_platforms_contains_expected(self) -> None:
        """All four freelance platforms should be in the allowed set."""
        assert _ALLOWED_PLATFORMS == {"freelancer", "upwork", "fl_ru", "kwork"}

    def test_known_api_keys_count(self) -> None:
        """Should have 8 known API key names."""
        assert len(_KNOWN_API_KEYS) == 8

    def test_known_api_keys_include_major_providers(self) -> None:
        """Should include keys for all major LLM and service providers."""
        assert "gemini_api_key" in _KNOWN_API_KEYS
        assert "anthropic_api_key" in _KNOWN_API_KEYS
        assert "openai_api_key" in _KNOWN_API_KEYS
        assert "e2b_api_key" in _KNOWN_API_KEYS
        assert "hunter_api_key" in _KNOWN_API_KEYS
        assert "apollo_api_key" in _KNOWN_API_KEYS
        assert "langsmith_api_key" in _KNOWN_API_KEYS
        assert "openrouter_api_key" in _KNOWN_API_KEYS
