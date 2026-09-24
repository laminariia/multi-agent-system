"""Unit tests for Freelancer.com API adapter.

Tests FreelancerClient class methods:
- Initialization and configuration
- Auth header building
- Response handling and error mapping
- Job fetching and normalization
- Bid submission
- Client profile retrieval
- HTTP client lifecycle
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.adapters.freelancer import FreelancerClient
from src.core.exceptions import (
    PlatformAPIError,
    PlatformBannedError,
    PlatformRateLimitError,
)

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def client() -> FreelancerClient:
    """Create FreelancerClient with test credentials."""
    return FreelancerClient(
        client_id="test-client-id",  # noqa: S106
        client_secret="test-client-secret",  # noqa: S106
        access_token="test-access-token",  # noqa: S106
    )


@pytest.fixture
def client_no_token() -> FreelancerClient:
    """Create FreelancerClient without access token."""
    return FreelancerClient(
        client_id="test-client-id",  # noqa: S106
        client_secret="test-client-secret",  # noqa: S106
    )


@pytest.fixture
def mock_http() -> AsyncMock:
    """Create mock httpx.AsyncClient."""
    http = AsyncMock(spec=httpx.AsyncClient)
    http.is_closed = False
    http.headers = {}
    return http


# ============================================================================
# Test Initialization
# ============================================================================


class TestInit:
    """Test FreelancerClient initialization."""

    def test_stores_credentials(self) -> None:
        """Stores client_id, client_secret, access_token."""
        client = FreelancerClient(
            client_id="my-client",  # noqa: S106
            client_secret="my-secret",  # noqa: S106
            access_token="my-token",  # noqa: S106
        )
        assert client.client_id == "my-client"
        assert client.client_secret == "my-secret"  # noqa: S105
        assert client._access_token == "my-token"  # noqa: S105, SLF001

    def test_strips_trailing_slash_from_base_url(self) -> None:
        """Strips trailing slash from base_url."""
        client = FreelancerClient(
            client_id="test",  # noqa: S106
            client_secret="test",  # noqa: S106
            base_url="https://api.example.com/",
        )
        assert client.base_url == "https://api.example.com"

    def test_defaults_to_freelancer_api_url(self) -> None:
        """Defaults to freelancer.com API URL."""
        client = FreelancerClient(
            client_id="test",  # noqa: S106
            client_secret="test",  # noqa: S106
        )
        assert client.base_url == "https://www.freelancer.com/api"

    def test_access_token_optional(self) -> None:
        """Access token is optional."""
        client = FreelancerClient(
            client_id="test",  # noqa: S106
            client_secret="test",  # noqa: S106
        )
        assert client._access_token == ""  # noqa: SLF001


# ============================================================================
# Test Auth Headers
# ============================================================================


class TestAuthHeaders:
    """Test _auth_headers method."""

    def test_includes_content_type(self, client: FreelancerClient) -> None:
        """Includes Content-Type header."""
        headers = client._auth_headers()  # noqa: SLF001
        assert headers["Content-Type"] == "application/json"

    def test_includes_oauth_header_when_token_set(self, client: FreelancerClient) -> None:
        """Includes OAuth header when access_token is set."""
        headers = client._auth_headers()  # noqa: SLF001
        assert headers["Freelancer-OAuth-V1"] == "test-access-token"  # noqa: S105

    def test_no_oauth_header_when_token_empty(self, client_no_token: FreelancerClient) -> None:
        """No OAuth header when access_token is empty."""
        headers = client_no_token._auth_headers()  # noqa: SLF001
        assert "Freelancer-OAuth-V1" not in headers


# ============================================================================
# Test Set Access Token
# ============================================================================


class TestSetAccessToken:
    """Test set_access_token method."""

    def test_updates_internal_token(self, client: FreelancerClient) -> None:
        """Updates internal _access_token."""
        client.set_access_token("new-token")  # noqa: S106
        assert client._access_token == "new-token"  # noqa: S105, SLF001

    @pytest.mark.asyncio
    async def test_updates_http_client_header_if_exists(self, client: FreelancerClient, mock_http: AsyncMock) -> None:
        """Updates http client header if client exists."""
        # Set up client with mock http
        with patch.object(client, "_get_http", return_value=mock_http):
            await client._get_http()  # noqa: SLF001
            client._http = mock_http  # noqa: SLF001

            # Update token
            client.set_access_token("new-token")  # noqa: S106

            # Verify header updated
            assert mock_http.headers["Freelancer-OAuth-V1"] == "new-token"  # noqa: S105


# ============================================================================
# Test Handle Response
# ============================================================================


class TestHandleResponse:
    """Test _handle_response method."""

    def test_429_raises_rate_limit_error_with_retry_after(self, client: FreelancerClient) -> None:
        """429 raises PlatformRateLimitError with retry_after."""
        response = MagicMock(spec=httpx.Response)
        response.status_code = 429
        response.headers = {"Retry-After": "60"}

        with pytest.raises(PlatformRateLimitError) as exc_info:
            client._handle_response(response, operation="test_op")  # noqa: SLF001

        assert exc_info.value.platform == "freelancer"
        assert exc_info.value.operation == "test_op"
        assert exc_info.value.retry_after_seconds == 60.0

    def test_429_without_retry_after_header(self, client: FreelancerClient) -> None:
        """429 without Retry-After header sets retry_after to None."""
        response = MagicMock(spec=httpx.Response)
        response.status_code = 429
        response.headers = {}

        with pytest.raises(PlatformRateLimitError) as exc_info:
            client._handle_response(response, operation="test_op")  # noqa: SLF001

        assert exc_info.value.retry_after_seconds is None

    def test_403_raises_banned_error(self, client: FreelancerClient) -> None:
        """403 raises PlatformBannedError."""
        response = MagicMock(spec=httpx.Response)
        response.status_code = 403

        with pytest.raises(PlatformBannedError) as exc_info:
            client._handle_response(response, operation="test_op")  # noqa: SLF001

        assert exc_info.value.platform == "freelancer"
        assert exc_info.value.operation == "test_op"
        assert "403 Forbidden" in str(exc_info.value)

    def test_400_raises_api_error(self, client: FreelancerClient) -> None:
        """400+ raises PlatformAPIError with body text."""
        response = MagicMock(spec=httpx.Response)
        response.status_code = 400
        response.text = "Bad Request: invalid parameter"

        with pytest.raises(PlatformAPIError) as exc_info:
            client._handle_response(response, operation="test_op")  # noqa: SLF001

        assert exc_info.value.platform == "freelancer"
        assert exc_info.value.operation == "test_op"
        assert "400" in str(exc_info.value)
        assert "Bad Request" in str(exc_info.value)

    def test_500_raises_api_error(self, client: FreelancerClient) -> None:
        """500+ raises PlatformAPIError."""
        response = MagicMock(spec=httpx.Response)
        response.status_code = 500
        response.text = "Internal Server Error"

        with pytest.raises(PlatformAPIError) as exc_info:
            client._handle_response(response, operation="test_op")  # noqa: SLF001

        assert "500" in str(exc_info.value)

    def test_200_with_status_error_raises_api_error(self, client: FreelancerClient) -> None:
        """200 with status=error raises PlatformAPIError."""
        response = MagicMock(spec=httpx.Response)
        response.status_code = 200
        response.json.return_value = {
            "status": "error",
            "message": "Invalid project ID",
        }

        with pytest.raises(PlatformAPIError) as exc_info:
            client._handle_response(response, operation="test_op")  # noqa: SLF001

        assert "Invalid project ID" in str(exc_info.value)
        assert exc_info.value.details["api_error"] == "Invalid project ID"  # type: ignore[index]

    def test_200_with_valid_json_returns_data(self, client: FreelancerClient) -> None:
        """200 with valid JSON returns data dict."""
        response = MagicMock(spec=httpx.Response)
        response.status_code = 200
        response.json.return_value = {
            "status": "success",
            "result": {"projects": []},
        }

        data = client._handle_response(response, operation="test_op")  # noqa: SLF001

        assert data["status"] == "success"
        assert "result" in data

    def test_non_json_response_raises_api_error(self, client: FreelancerClient) -> None:
        """Non-JSON response raises PlatformAPIError."""
        response = MagicMock(spec=httpx.Response)
        response.status_code = 200
        response.json.side_effect = ValueError("Not JSON")

        with pytest.raises(PlatformAPIError) as exc_info:
            client._handle_response(response, operation="test_op")  # noqa: SLF001

        assert "Failed to decode" in str(exc_info.value)


# ============================================================================
# Test Fetch Jobs
# ============================================================================


class TestFetchJobs:
    """Test fetch_jobs method."""

    @pytest.mark.asyncio
    async def test_calls_get_with_correct_params(self, client: FreelancerClient, mock_http: AsyncMock) -> None:
        """Calls GET with correct params (category, min_budget, limit)."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": {"projects": []}}
        mock_http.get = AsyncMock(return_value=mock_response)

        with patch.object(client, "_get_http", return_value=mock_http):
            await client.fetch_jobs("python", 100, max_results=25)

        # Verify GET called with correct endpoint and params
        mock_http.get.assert_called_once()
        call_args = mock_http.get.call_args
        assert call_args[0][0] == "/projects/0.1/projects/active/"

        params = call_args[1]["params"]
        assert params["jobs[]"] == "python"
        assert params["min_avg_price"] == 100
        assert params["limit"] == 25
        assert params["full_description"] is True
        assert params["job_details"] is True

    @pytest.mark.asyncio
    async def test_normalizes_projects_into_standard_format(
        self, client: FreelancerClient, mock_http: AsyncMock
    ) -> None:
        """Normalizes projects into standard format."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "result": {
                "projects": [
                    {
                        "id": 12345,
                        "title": "Python Developer Needed",
                        "description": "Build a web scraper",
                        "preview_description": "Short desc",
                        "budget": {"minimum": 100, "maximum": 500},
                        "currency": {"code": "USD"},
                        "jobs": [{"name": "Python"}, {"name": "Web Scraping"}],
                        "owner": {
                            "id": 67890,
                            "username": "johndoe",
                            "employer_reputation": {"overall": 4.5},
                            "location": {"country": {"name": "United States"}},
                        },
                        "seo_url": "python-developer-needed",
                    }
                ]
            }
        }
        mock_http.get = AsyncMock(return_value=mock_response)

        with patch.object(client, "_get_http", return_value=mock_http):
            jobs = await client.fetch_jobs("python", 100)

        assert len(jobs) == 1
        job = jobs[0]
        assert job["external_id"] == "12345"
        assert job["platform"] == "freelancer"
        assert job["title"] == "Python Developer Needed"
        assert job["description"] == "Build a web scraper"

    @pytest.mark.asyncio
    async def test_extracts_budget_min_max_from_budget_object(
        self, client: FreelancerClient, mock_http: AsyncMock
    ) -> None:
        """Extracts budget_min, budget_max from budget object."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "result": {
                "projects": [
                    {
                        "id": 1,
                        "title": "Test",
                        "budget": {"minimum": 250, "maximum": 1000},
                        "currency": {"code": "EUR"},
                        "owner": {},
                        "jobs": [],
                    }
                ]
            }
        }
        mock_http.get = AsyncMock(return_value=mock_response)

        with patch.object(client, "_get_http", return_value=mock_http):
            jobs = await client.fetch_jobs("test", 100)

        assert jobs[0]["budget_min"] == 250
        assert jobs[0]["budget_max"] == 1000
        assert jobs[0]["currency"] == "EUR"

    @pytest.mark.asyncio
    async def test_extracts_client_info_from_owner_object(self, client: FreelancerClient, mock_http: AsyncMock) -> None:
        """Extracts client_info from owner object."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "result": {
                "projects": [
                    {
                        "id": 1,
                        "title": "Test",
                        "owner": {
                            "id": 999,
                            "username": "testuser",
                            "employer_reputation": {"overall": 4.8},
                            "location": {"country": {"name": "Canada"}},
                        },
                        "budget": {},
                        "currency": {"code": "USD"},
                        "jobs": [],
                    }
                ]
            }
        }
        mock_http.get = AsyncMock(return_value=mock_response)

        with patch.object(client, "_get_http", return_value=mock_http):
            jobs = await client.fetch_jobs("test", 100)

        client_info = jobs[0]["client_info"]
        assert client_info["user_id"] == 999
        assert client_info["username"] == "testuser"
        assert client_info["rating"] == 4.8
        assert client_info["country"] == "Canada"

    @pytest.mark.asyncio
    async def test_returns_empty_list_for_no_results(self, client: FreelancerClient, mock_http: AsyncMock) -> None:
        """Returns empty list for no results."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": {"projects": []}}
        mock_http.get = AsyncMock(return_value=mock_response)

        with patch.object(client, "_get_http", return_value=mock_http):
            jobs = await client.fetch_jobs("test", 100)

        assert jobs == []

    @pytest.mark.asyncio
    async def test_respects_max_results_cap_at_100(self, client: FreelancerClient, mock_http: AsyncMock) -> None:
        """Respects max_results cap at 100."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": {"projects": []}}
        mock_http.get = AsyncMock(return_value=mock_response)

        with patch.object(client, "_get_http", return_value=mock_http):
            await client.fetch_jobs("test", 100, max_results=500)

        # Verify limit capped at 100
        call_args = mock_http.get.call_args
        params = call_args[1]["params"]
        assert params["limit"] == 100

    @pytest.mark.asyncio
    async def test_uses_preview_description_fallback(self, client: FreelancerClient, mock_http: AsyncMock) -> None:
        """Uses preview_description if description is missing."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "result": {
                "projects": [
                    {
                        "id": 1,
                        "title": "Test",
                        "preview_description": "Preview only",
                        "owner": {},
                        "budget": {},
                        "currency": {"code": "USD"},
                        "jobs": [],
                    }
                ]
            }
        }
        mock_http.get = AsyncMock(return_value=mock_response)

        with patch.object(client, "_get_http", return_value=mock_http):
            jobs = await client.fetch_jobs("test", 100)

        assert jobs[0]["description"] == "Preview only"


# ============================================================================
# Test Submit Bid
# ============================================================================


class TestSubmitBid:
    """Test submit_bid method."""

    @pytest.mark.asyncio
    async def test_calls_post_with_correct_payload(self, client: FreelancerClient, mock_http: AsyncMock) -> None:
        """Calls POST with correct payload."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": {"id": 54321, "status": "active"}}
        mock_http.post = AsyncMock(return_value=mock_response)

        with patch.object(client, "_get_http", return_value=mock_http):
            await client.submit_bid(
                project_id=12345,
                description="I can help with this project",
                amount=500.0,
                period=7,
                milestone_percentage=50,
            )

        # Verify POST called
        mock_http.post.assert_called_once()
        call_args = mock_http.post.call_args
        assert call_args[0][0] == "/projects/0.1/bids/"

        payload = call_args[1]["json"]
        assert payload["project_id"] == 12345
        assert payload["description"] == "I can help with this project"
        assert payload["amount"] == 500.0
        assert payload["period"] == 7
        assert payload["milestone_percentage"] == 50

    @pytest.mark.asyncio
    async def test_returns_api_response_data(self, client: FreelancerClient, mock_http: AsyncMock) -> None:
        """Returns API response data."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": {"id": 99999, "status": "pending"}}
        mock_http.post = AsyncMock(return_value=mock_response)

        with patch.object(client, "_get_http", return_value=mock_http):
            result = await client.submit_bid(
                project_id=1,
                description="test",
                amount=100.0,
                period=5,
            )

        assert result["result"]["id"] == 99999
        assert result["result"]["status"] == "pending"

    @pytest.mark.asyncio
    async def test_handles_rate_limit_429(self, client: FreelancerClient, mock_http: AsyncMock) -> None:
        """Handles rate limit (429)."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 429
        mock_response.headers = {"Retry-After": "120"}
        mock_http.post = AsyncMock(return_value=mock_response)

        with patch.object(client, "_get_http", return_value=mock_http):
            with pytest.raises(PlatformRateLimitError) as exc_info:
                await client.submit_bid(
                    project_id=1,
                    description="test",
                    amount=100.0,
                    period=5,
                )

        assert exc_info.value.operation == "submit_bid"
        assert exc_info.value.retry_after_seconds == 120.0


# ============================================================================
# Test Get Client Profile
# ============================================================================


class TestGetClientProfile:
    """Test get_client_profile method."""

    @pytest.mark.asyncio
    async def test_calls_get_with_correct_params(self, client: FreelancerClient, mock_http: AsyncMock) -> None:
        """Calls GET with correct params."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": {}}
        mock_http.get = AsyncMock(return_value=mock_response)

        with patch.object(client, "_get_http", return_value=mock_http):
            await client.get_client_profile(12345)

        # Verify GET called with correct URL and params
        mock_http.get.assert_called_once()
        call_args = mock_http.get.call_args
        assert call_args[0][0] == "/users/0.1/users/12345/"

        params = call_args[1]["params"]
        assert params["reputation"] is True
        assert params["employer_reputation"] is True
        assert params["jobs"] is True
        assert params["portfolio"] is True

    @pytest.mark.asyncio
    async def test_normalizes_profile_data(self, client: FreelancerClient, mock_http: AsyncMock) -> None:
        """Normalizes profile data."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "result": {
                "id": 67890,
                "username": "johndoe",
                "display_name": "John Doe",
                "employer_reputation": {
                    "overall": 4.9,
                    "completion_rate": 0.95,
                    "reviews": 50,
                    "earnings": 25000,
                },
                "reputation": {"overall": 4.5, "reviews": 30},
                "location": {"country": {"name": "Australia"}},
                "registration_date": 1609459200,
            }
        }
        mock_http.get = AsyncMock(return_value=mock_response)

        with patch.object(client, "_get_http", return_value=mock_http):
            profile = await client.get_client_profile(67890)

        assert profile["user_id"] == 67890
        assert profile["username"] == "johndoe"
        assert profile["display_name"] == "John Doe"
        assert profile["rating"] == 4.9
        assert profile["hire_rate"] == 0.95
        assert profile["total_spent"] == 25000
        assert profile["country"] == "Australia"

    @pytest.mark.asyncio
    async def test_extracts_employer_reputation_and_reputation(
        self, client: FreelancerClient, mock_http: AsyncMock
    ) -> None:
        """Extracts employer_reputation and reputation."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "result": {
                "id": 1,
                "employer_reputation": {"overall": 4.8, "reviews": 20},
                "reputation": {"overall": 4.2, "reviews": 10},
            }
        }
        mock_http.get = AsyncMock(return_value=mock_response)

        with patch.object(client, "_get_http", return_value=mock_http):
            profile = await client.get_client_profile(1)

        # Should prefer employer_reputation for rating
        assert profile["rating"] == 4.8
        # Total reviews should sum both
        assert profile["total_reviews"] == 30

    @pytest.mark.asyncio
    async def test_fallback_to_reputation_when_no_employer_rep(
        self, client: FreelancerClient, mock_http: AsyncMock
    ) -> None:
        """Falls back to reputation when no employer_reputation."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "result": {
                "id": 1,
                "reputation": {"overall": 3.5, "reviews": 5},
            }
        }
        mock_http.get = AsyncMock(return_value=mock_response)

        with patch.object(client, "_get_http", return_value=mock_http):
            profile = await client.get_client_profile(1)

        # Should use reputation.overall as fallback
        assert profile["rating"] == 3.5
        assert profile["total_reviews"] == 5


# ============================================================================
# Test Lifecycle
# ============================================================================


class TestLifecycle:
    """Test HTTP client lifecycle."""

    @pytest.mark.asyncio
    async def test_close_shuts_down_http_client(self, client: FreelancerClient) -> None:
        """close() shuts down http client."""
        # Create a real http client
        await client._get_http()  # noqa: SLF001
        assert client._http is not None  # noqa: SLF001

        # Close it
        await client.close()

        # Should be set to None
        assert client._http is None  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_get_http_creates_new_client_after_close(self, client: FreelancerClient) -> None:
        """_get_http() creates new client after close."""
        # Create and close
        http1 = await client._get_http()  # noqa: SLF001
        await client.close()

        # Create again
        http2 = await client._get_http()  # noqa: SLF001

        # Should be different instances
        assert http2 is not http1

    @pytest.mark.asyncio
    async def test_close_handles_already_closed_client(self, client: FreelancerClient) -> None:
        """close() handles already closed client gracefully."""
        # Create http client
        await client._get_http()  # noqa: SLF001

        # Close twice - should not raise
        await client.close()
        await client.close()

    @pytest.mark.asyncio
    async def test_get_http_returns_existing_client(self, client: FreelancerClient) -> None:
        """_get_http() returns existing client if not closed."""
        http1 = await client._get_http()  # noqa: SLF001
        http2 = await client._get_http()  # noqa: SLF001

        # Should be same instance
        assert http1 is http2
