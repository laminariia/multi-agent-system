"""Unit tests for src.adapters.fl_ru.FlRuClient.

Tests RSS feed parsing, rate limiting, budget extraction, and error handling.
All HTTP requests are mocked — no real network calls are made.
"""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.adapters.fl_ru import _CURRENCY_TO_USD, _MAX_REQUESTS_PER_HOUR, FlRuClient
from src.core.exceptions import PlatformAPIError, PlatformRateLimitError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_xml_item(
    title: str = "Test Job",
    description: str = "Test description",
    link: str = "https://www.fl.ru/projects/12345/test-job/",
    guid: str = "https://www.fl.ru/projects/12345/",
    pub_date: str = "Mon, 09 Feb 2026 01:00:00 +0300",
) -> ET.Element:
    """Create a test RSS <item> XML element."""
    item = ET.Element("item")
    ET.SubElement(item, "title").text = title
    ET.SubElement(item, "description").text = description
    ET.SubElement(item, "link").text = link
    ET.SubElement(item, "guid").text = guid
    ET.SubElement(item, "pubDate").text = pub_date
    return item


def _create_rss_xml(items: list[ET.Element]) -> str:
    """Create a complete RSS XML document from item elements."""
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = "FL.ru — новые проекты"
    for item in items:
        channel.append(item)
    return ET.tostring(rss, encoding="unicode")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestParseRssEntry:
    """Test RSS <item> parsing into normalised job dicts."""

    def test_basic_entry(self) -> None:
        """Parse standard item with all fields present."""
        item = _create_xml_item(
            title="Создать лендинг",
            description="Нужен лендинг для фитнес-студии",
            link="https://www.fl.ru/projects/67890/landing/",
            guid="https://www.fl.ru/projects/67890/",
            pub_date="Mon, 09 Feb 2026 10:00:00 +0300",
        )

        result = FlRuClient.parse_rss_entry(item)

        assert result["external_id"] == "67890"
        assert result["platform"] == "flru"
        assert result["title"] == "Создать лендинг"
        assert result["description"] == "Нужен лендинг для фитнес-студии"
        assert result["url"] == "https://www.fl.ru/projects/67890/landing/"
        assert result["pub_date"] == "Mon, 09 Feb 2026 10:00:00 +0300"
        assert result["raw_data"]["guid"] == "https://www.fl.ru/projects/67890/"

    def test_budget_extraction_rub(self) -> None:
        """Extract budget in rubles and convert to USD."""
        item = _create_xml_item(
            description="Бюджет: 50000 руб"
        )

        result = FlRuClient.parse_rss_entry(item)

        # 50000 RUB * 0.011 = 550.0 USD
        assert result["budget_min"] == 550.0
        assert result["budget_max"] == 550.0
        assert result["currency"] == "RUB"

    def test_budget_extraction_rub_with_spaces(self) -> None:
        """Extract budget with spaces in number."""
        item = _create_xml_item(
            description="Бюджет: 100 000 руб"
        )

        result = FlRuClient.parse_rss_entry(item)

        # 100000 RUB * 0.011 = 1100.0 USD
        assert result["budget_min"] == 1100.0
        assert result["budget_max"] == 1100.0
        assert result["currency"] == "RUB"

    def test_budget_extraction_usd(self) -> None:
        """Extract budget in USD."""
        item = _create_xml_item(
            description="Budget: 500 USD"
        )

        result = FlRuClient.parse_rss_entry(item)

        assert result["budget_min"] == 500.0
        assert result["budget_max"] == 500.0
        assert result["currency"] == "USD"

    def test_budget_extraction_eur(self) -> None:
        """Extract budget in EUR and convert to USD."""
        item = _create_xml_item(
            description="100 EUR"
        )

        result = FlRuClient.parse_rss_entry(item)

        # 100 EUR * 1.08 = 108.0 USD
        assert result["budget_min"] == 108.0
        assert result["budget_max"] == 108.0
        assert result["currency"] == "EUR"

    def test_budget_extraction_ruble_symbol(self) -> None:
        """Extract budget with Russian ruble notation 'р.'."""
        item = _create_xml_item(
            description="30000 р."
        )

        result = FlRuClient.parse_rss_entry(item)

        # 30000 RUB * 0.011 = 330.0 USD
        assert result["budget_min"] == 330.0
        assert result["budget_max"] == 330.0
        assert result["currency"] == "RUB"

    def test_no_budget(self) -> None:
        """Parse entry without budget information."""
        item = _create_xml_item(
            description="Создать сайт без указания бюджета"
        )

        result = FlRuClient.parse_rss_entry(item)

        assert result["budget_min"] is None
        assert result["budget_max"] is None
        assert result["currency"] == "RUB"  # Default currency

    def test_external_id_from_link(self) -> None:
        """Extract external_id from project link."""
        item = _create_xml_item(
            link="https://www.fl.ru/projects/99887/some-project/",
            guid="guid-99887"
        )

        result = FlRuClient.parse_rss_entry(item)

        assert result["external_id"] == "99887"

    def test_external_id_fallback_to_guid(self) -> None:
        """Fall back to guid when link doesn't contain project ID."""
        item = _create_xml_item(
            link="https://www.fl.ru/some-other-page/",
            guid="fallback-guid-12345"
        )

        result = FlRuClient.parse_rss_entry(item)

        assert result["external_id"] == "fallback-guid-12345"

    def test_empty_fields(self) -> None:
        """Handle missing title, description, link gracefully."""
        item = ET.Element("item")

        result = FlRuClient.parse_rss_entry(item)

        assert result["title"] == ""
        assert result["description"] == ""
        assert result["url"] == ""
        assert result["pub_date"] == ""
        assert result["external_id"] == ""


class TestRateLimit:
    """Test internal rate limiting (10 requests/hour)."""

    def test_allows_under_limit(self) -> None:
        """Allow requests under the rate limit."""
        client = FlRuClient()

        # First 9 requests should pass
        for _ in range(9):
            client._check_rate_limit()

        # Should have 9 timestamps
        assert len(client._request_timestamps) == 9

    def test_blocks_at_limit(self) -> None:
        """Block 11th request when limit is reached."""
        client = FlRuClient()

        # Fill up to the limit (10 requests)
        for _ in range(_MAX_REQUESTS_PER_HOUR):
            client._check_rate_limit()

        # 11th request should raise error
        with pytest.raises(PlatformRateLimitError) as exc_info:
            client._check_rate_limit()

        assert exc_info.value.platform == "fl_ru"
        assert exc_info.value.operation == "fetch_rss"
        assert exc_info.value.retry_after_seconds is not None
        assert exc_info.value.retry_after_seconds > 0

    def test_window_slides(self) -> None:
        """Old timestamps expire, allowing new requests."""
        client = FlRuClient()

        # Manually set timestamps from 3601 seconds ago (outside window)
        old_time = time.monotonic() - 3601
        client._request_timestamps = [old_time] * 10

        # These old timestamps should be pruned, allowing new request
        client._check_rate_limit()

        # Only the new timestamp should remain
        assert len(client._request_timestamps) == 1
        assert client._request_timestamps[0] > old_time

    def test_partial_window_cleanup(self) -> None:
        """Prune only expired timestamps, keep recent ones."""
        client = FlRuClient()

        now = time.monotonic()
        # 5 old timestamps (expired) + 4 recent (valid)
        client._request_timestamps = [
            now - 3700,  # Expired
            now - 3650,  # Expired
            now - 3610,  # Expired
            now - 3605,  # Expired
            now - 3601,  # Expired
            now - 100,   # Valid
            now - 50,    # Valid
            now - 20,    # Valid
            now - 5,     # Valid
        ]

        # Should prune 5 expired, keep 4 valid, add 1 new = 5 total
        client._check_rate_limit()

        assert len(client._request_timestamps) == 5


class TestFetchJobs:
    """Test job fetching and parsing from RSS feed."""

    @pytest.mark.asyncio
    async def test_successful_fetch(self) -> None:
        """Fetch and parse RSS feed successfully."""
        client = FlRuClient()

        sample_rss = _create_rss_xml([
            _create_xml_item(
                title="Landing Page Project",
                description="Бюджет: 25000 руб",
                link="https://www.fl.ru/projects/11111/landing/",
            ),
            _create_xml_item(
                title="WordPress Site",
                description="Budget: 300 USD",
                link="https://www.fl.ru/projects/22222/wordpress/",
            ),
        ])

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = sample_rss
        mock_response.headers = {}

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        mock_http.is_closed = False

        with patch.object(client, "_get_http", return_value=mock_http):
            jobs = await client.fetch_jobs()

        assert len(jobs) == 2
        assert jobs[0]["external_id"] == "11111"
        assert jobs[0]["title"] == "Landing Page Project"
        assert jobs[0]["budget_min"] == 275.0  # 25000 * 0.011
        assert jobs[1]["external_id"] == "22222"
        assert jobs[1]["title"] == "WordPress Site"
        assert jobs[1]["budget_min"] == 300.0

    @pytest.mark.asyncio
    async def test_category_url(self) -> None:
        """Use category-specific URL when category provided."""
        client = FlRuClient(base_url="https://www.fl.ru/rss/all.xml")

        sample_rss = _create_rss_xml([
            _create_xml_item(title="Web Dev Job"),
        ])

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = sample_rss
        mock_response.headers = {}

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        mock_http.is_closed = False

        with patch.object(client, "_get_http", return_value=mock_http):
            await client.fetch_jobs(category="web")

        # Verify the URL was changed to web.xml
        mock_http.get.assert_called_once()
        call_url = mock_http.get.call_args[0][0]
        assert call_url == "https://www.fl.ru/rss/web.xml"

    @pytest.mark.asyncio
    async def test_rate_limit_blocks(self) -> None:
        """Raise error when rate limit is hit before HTTP request."""
        client = FlRuClient()

        # Fill up the rate limit
        for _ in range(_MAX_REQUESTS_PER_HOUR):
            client._request_timestamps.append(time.monotonic())

        with pytest.raises(PlatformRateLimitError) as exc_info:
            await client.fetch_jobs()

        assert exc_info.value.platform == "fl_ru"

    @pytest.mark.asyncio
    async def test_http_429(self) -> None:
        """Handle HTTP 429 rate limit response from server."""
        client = FlRuClient()

        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.headers = {"Retry-After": "3600"}

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        mock_http.is_closed = False

        with patch.object(client, "_get_http", return_value=mock_http):
            with pytest.raises(PlatformRateLimitError) as exc_info:
                await client.fetch_jobs()

        assert exc_info.value.platform == "fl_ru"
        assert exc_info.value.retry_after_seconds == 3600.0

    @pytest.mark.asyncio
    async def test_http_500(self) -> None:
        """Handle HTTP 500 server error."""
        client = FlRuClient()

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_response.headers = {}

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        mock_http.is_closed = False

        with patch.object(client, "_get_http", return_value=mock_http):
            with pytest.raises(PlatformAPIError) as exc_info:
                await client.fetch_jobs()

        assert exc_info.value.platform == "fl_ru"
        assert "500" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_http_404(self) -> None:
        """Handle HTTP 404 not found."""
        client = FlRuClient()

        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "Not Found"
        mock_response.headers = {}

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        mock_http.is_closed = False

        with patch.object(client, "_get_http", return_value=mock_http):
            with pytest.raises(PlatformAPIError) as exc_info:
                await client.fetch_jobs()

        assert exc_info.value.platform == "fl_ru"
        assert "404" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_timeout(self) -> None:
        """Handle HTTP timeout."""
        client = FlRuClient()

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(side_effect=httpx.TimeoutException("Request timed out"))
        mock_http.is_closed = False

        with patch.object(client, "_get_http", return_value=mock_http):
            with pytest.raises(PlatformAPIError) as exc_info:
                await client.fetch_jobs()

        assert exc_info.value.platform == "fl_ru"
        assert "timed out" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_http_error(self) -> None:
        """Handle generic HTTP errors."""
        client = FlRuClient()

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(side_effect=httpx.HTTPError("Connection failed"))
        mock_http.is_closed = False

        with patch.object(client, "_get_http", return_value=mock_http):
            with pytest.raises(PlatformAPIError) as exc_info:
                await client.fetch_jobs()

        assert exc_info.value.platform == "fl_ru"

    @pytest.mark.asyncio
    async def test_invalid_xml(self) -> None:
        """Handle malformed XML response."""
        client = FlRuClient()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "This is not XML <invalid>"
        mock_response.headers = {}

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        mock_http.is_closed = False

        with patch.object(client, "_get_http", return_value=mock_http):
            with pytest.raises(PlatformAPIError) as exc_info:
                await client.fetch_jobs()

        assert exc_info.value.platform == "fl_ru"
        assert "parse error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_no_channel(self) -> None:
        """Return empty list when RSS has no <channel> element."""
        client = FlRuClient()

        # Valid XML but missing <channel>
        invalid_rss = '<?xml version="1.0"?><rss version="2.0"></rss>'

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = invalid_rss
        mock_response.headers = {}

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        mock_http.is_closed = False

        with patch.object(client, "_get_http", return_value=mock_http):
            jobs = await client.fetch_jobs()

        assert jobs == []

    @pytest.mark.asyncio
    async def test_partial_parse_failure(self) -> None:
        """Continue parsing when one item fails, return successful items."""
        client = FlRuClient()

        # Create RSS with valid item and a broken one
        rss = ET.Element("rss", version="2.0")
        channel = ET.SubElement(rss, "channel")

        # Good item
        good_item = _create_xml_item(
            title="Good Job",
            link="https://www.fl.ru/projects/99999/good/",
        )
        channel.append(good_item)

        # Bad item - will cause parse error when processing
        bad_item = ET.SubElement(channel, "item")
        ET.SubElement(bad_item, "title").text = "Bad Job"
        # Missing other required fields, and we'll make parse_rss_entry fail

        rss_text = ET.tostring(rss, encoding="unicode")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = rss_text
        mock_response.headers = {}

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        mock_http.is_closed = False

        # Mock parse_rss_entry to fail on second call
        original_parse = FlRuClient.parse_rss_entry
        call_count = 0

        def parse_side_effect(item):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise ValueError("Parse failed")
            return original_parse(item)

        with patch.object(client, "_get_http", return_value=mock_http):
            with patch.object(FlRuClient, "parse_rss_entry", side_effect=parse_side_effect):
                jobs = await client.fetch_jobs()

        # Should return only the good job
        assert len(jobs) == 1
        assert jobs[0]["title"] == "Good Job"

    @pytest.mark.asyncio
    async def test_empty_channel(self) -> None:
        """Return empty list when channel has no items."""
        client = FlRuClient()

        empty_rss = _create_rss_xml([])

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = empty_rss
        mock_response.headers = {}

        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=mock_response)
        mock_http.is_closed = False

        with patch.object(client, "_get_http", return_value=mock_http):
            jobs = await client.fetch_jobs()

        assert jobs == []


class TestLifecycle:
    """Test client lifecycle (creation, close, lazy HTTP)."""

    @pytest.mark.asyncio
    async def test_close(self) -> None:
        """Close HTTP client properly."""
        client = FlRuClient()

        # Create HTTP client
        await client._get_http()
        assert client._http is not None
        assert not client._http.is_closed

        # Close it
        await client.close()

        assert client._http is None

    @pytest.mark.asyncio
    async def test_lazy_http_creation(self) -> None:
        """HTTP client not created until first request."""
        client = FlRuClient()

        # Initially None
        assert client._http is None

        # First call creates it
        http1 = await client._get_http()
        assert client._http is not None

        # Second call reuses it
        http2 = await client._get_http()
        assert http2 is http1

    @pytest.mark.asyncio
    async def test_http_recreated_after_close(self) -> None:
        """HTTP client recreated if accessed after close."""
        client = FlRuClient()

        # Create, close, create again
        http1 = await client._get_http()
        await client.close()
        http2 = await client._get_http()

        assert http2 is not http1
        assert client._http is not None

    @pytest.mark.asyncio
    async def test_close_when_already_none(self) -> None:
        """Close is safe when client is already None."""
        client = FlRuClient()

        # Close without ever creating HTTP client
        await client.close()

        assert client._http is None

    @pytest.mark.asyncio
    async def test_http_client_has_correct_headers(self) -> None:
        """HTTP client created with proper User-Agent and Accept headers."""
        client = FlRuClient()

        http = await client._get_http()

        assert "User-Agent" in http.headers
        assert "Mozilla" in http.headers["User-Agent"]
        assert "Accept" in http.headers
        assert "xml" in http.headers["Accept"].lower()


class TestCurrencyConversion:
    """Test currency conversion rates."""

    def test_currency_rates_defined(self) -> None:
        """All expected currencies have conversion rates."""
        expected_currencies = ["руб", "р.", "RUB", "USD", "$", "EUR", "€"]

        for curr in expected_currencies:
            assert curr in _CURRENCY_TO_USD
            assert _CURRENCY_TO_USD[curr] > 0

    def test_usd_rate_is_one(self) -> None:
        """USD to USD conversion is 1.0."""
        assert _CURRENCY_TO_USD["USD"] == 1.0
        assert _CURRENCY_TO_USD["$"] == 1.0

    def test_budget_uses_conversion_rate(self) -> None:
        """Budget extraction applies correct conversion rates."""
        item = _create_xml_item(
            description="Бюджет: 1000 руб"
        )

        result = FlRuClient.parse_rss_entry(item)

        expected = 1000 * _CURRENCY_TO_USD["руб"]
        assert result["budget_min"] == round(expected, 2)


class TestEdgeCases:
    """Test edge cases and corner scenarios."""

    def test_unicode_budget(self) -> None:
        """Handle unicode characters in budget (non-breaking space)."""
        item = _create_xml_item(
            description="Бюджет: 50\u00a0000 руб"  # \u00a0 is non-breaking space
        )

        result = FlRuClient.parse_rss_entry(item)

        # Should strip non-breaking space and parse correctly
        assert result["budget_min"] == 550.0  # 50000 * 0.011

    def test_multiple_budgets_in_description(self) -> None:
        """Use first budget found when multiple mentioned."""
        item = _create_xml_item(
            description="Первая часть: 10000 руб, вторая: 20000 руб"
        )

        result = FlRuClient.parse_rss_entry(item)

        # Should extract first match (10000 руб)
        assert result["budget_min"] == 110.0  # 10000 * 0.011

    def test_budget_in_title(self) -> None:
        """Extract budget from title when not in description."""
        item = _create_xml_item(
            title="Web Development - 1000 USD",
            description="Build a website"
        )

        result = FlRuClient.parse_rss_entry(item)

        assert result["budget_min"] == 1000.0

    def test_whitespace_stripping(self) -> None:
        """Strip leading/trailing whitespace from all text fields."""
        item = _create_xml_item(
            title="  Title with spaces  ",
            description="  Description with spaces  ",
            link="  https://www.fl.ru/projects/11111/  ",
        )

        result = FlRuClient.parse_rss_entry(item)

        assert result["title"] == "Title with spaces"
        assert result["description"] == "Description with spaces"
        assert result["url"] == "https://www.fl.ru/projects/11111/"

    def test_rate_limit_constant(self) -> None:
        """Verify rate limit constant is set correctly."""
        assert _MAX_REQUESTS_PER_HOUR == 10

    @pytest.mark.asyncio
    async def test_base_url_customization(self) -> None:
        """Allow custom base URL in constructor."""
        custom_url = "https://custom.fl.ru/rss/custom.xml"
        client = FlRuClient(base_url=custom_url)

        assert client.base_url == custom_url
