"""Tests for the Seerr provider."""

import pytest

from tests.conftest import MockTransport, load_fixture
from src.providers.seerr import SeerrProvider, REQUEST_STATUS_MAP, MEDIA_STATUS_MAP
from src.providers.base import HealthStatus

import httpx


def _make_provider(responses: dict | None = None, **config_overrides) -> SeerrProvider:
    """Create a SeerrProvider with a mock HTTP client."""
    config = {
        "url": "http://10.0.0.45:5055",
        "api_key": "test-api-key-seerr",
        **config_overrides,
    }
    provider = SeerrProvider(
        instance_id=1,
        display_name="Test Seerr",
        config=config,
    )
    if responses is None:
        responses = _default_responses()
    provider.http_client = httpx.AsyncClient(
        transport=MockTransport(responses),
        base_url="http://10.0.0.45:5055",
    )
    return provider


def _default_responses() -> dict:
    """Standard mock responses for a healthy Seerr instance."""
    status = load_fixture("seerr", "status")
    requests_data = load_fixture("seerr", "requests")
    request_count = load_fixture("seerr", "request_count")
    settings_radarr = load_fixture("seerr", "settings_radarr")
    settings_sonarr = load_fixture("seerr", "settings_sonarr")
    return {
        "/api/v1/status": {"status": 200, "json": status},
        "/api/v1/request": {"status": 200, "json": requests_data},
        "/api/v1/request/count": {"status": 200, "json": request_count},
        "/api/v1/request/1/approve": {"status": 200, "json": {}},
        "/api/v1/request/1/decline": {"status": 200, "json": {}},
        "/api/v1/settings/radarr": {"status": 200, "json": settings_radarr},
        "/api/v1/settings/sonarr": {"status": 200, "json": settings_sonarr},
    }


# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------

class TestMeta:
    def test_type_id(self):
        meta = SeerrProvider.meta()
        assert meta.type_id == "seerr"

    def test_category(self):
        meta = SeerrProvider.meta()
        assert meta.category == "media"

    def test_permissions_count(self):
        meta = SeerrProvider.meta()
        assert len(meta.permissions) == 3

    def test_config_schema_fields(self):
        meta = SeerrProvider.meta()
        keys = [f["key"] for f in meta.config_schema["fields"]]
        assert "url" in keys
        assert "api_key" in keys


# ---------------------------------------------------------------------------
# Health Check
# ---------------------------------------------------------------------------

class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_healthy(self):
        provider = _make_provider()
        result = await provider.health_check()
        assert result.status == HealthStatus.UP
        assert "1.33.2" in result.message

    @pytest.mark.asyncio
    async def test_invalid_api_key(self):
        responses = _default_responses()
        responses["/api/v1/status"] = {"status": 401, "json": {}}
        provider = _make_provider(responses)
        result = await provider.health_check()
        assert result.status == HealthStatus.DOWN
        assert "Invalid API key" in result.message

    @pytest.mark.asyncio
    async def test_unexpected_status(self):
        responses = _default_responses()
        responses["/api/v1/status"] = {"status": 503, "json": {}}
        provider = _make_provider(responses)
        result = await provider.health_check()
        assert result.status == HealthStatus.DOWN
        assert "503" in result.message

    @pytest.mark.asyncio
    async def test_connection_refused(self):
        provider = SeerrProvider(
            instance_id=1,
            display_name="Test",
            config={"url": "http://badhost:5055", "api_key": "key"},
        )
        transport = _ErrorTransport(httpx.ConnectError("refused"))
        provider.http_client = httpx.AsyncClient(
            transport=transport, base_url="http://badhost:5055"
        )
        result = await provider.health_check()
        assert result.status == HealthStatus.DOWN
        assert "refused" in result.message.lower()


# ---------------------------------------------------------------------------
# Status Maps
# ---------------------------------------------------------------------------

class TestStatusMaps:
    def test_request_status_map(self):
        assert REQUEST_STATUS_MAP[1] == "Pending"
        assert REQUEST_STATUS_MAP[2] == "Approved"
        assert REQUEST_STATUS_MAP[3] == "Declined"

    def test_media_status_map(self):
        assert MEDIA_STATUS_MAP[1] == "Unknown"
        assert MEDIA_STATUS_MAP[5] == "Available"


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

class TestSummary:
    @pytest.mark.asyncio
    async def test_summary_shape(self):
        provider = _make_provider()
        result = await provider.get_summary()

        assert result.data["version"] == "1.33.2"
        assert result.data["app_name"] == "Seerr"

    @pytest.mark.asyncio
    async def test_request_counts(self):
        provider = _make_provider()
        result = await provider.get_summary()

        counts = result.data["request_counts"]
        assert counts["total"] == 25
        assert counts["pending"] == 3
        assert counts["approved"] == 15
        assert counts["available"] == 4

    @pytest.mark.asyncio
    async def test_recent_requests(self):
        provider = _make_provider()
        result = await provider.get_summary()

        recent = result.data["recent_requests"]
        assert len(recent) == 3
        assert recent[0]["media_title"] == "Dune: Part Three"
        assert recent[0]["status"] == "Pending"
        assert recent[0]["requested_by"] == "John Doe"
        assert recent[0]["media_year"] == "2026"

    @pytest.mark.asyncio
    async def test_approved_request_status(self):
        provider = _make_provider()
        result = await provider.get_summary()

        recent = result.data["recent_requests"]
        assert recent[1]["status"] == "Approved"
        assert recent[1]["media_status"] == "Processing"

    @pytest.mark.asyncio
    async def test_available_media_status(self):
        provider = _make_provider()
        result = await provider.get_summary()

        recent = result.data["recent_requests"]
        assert recent[2]["media_status"] == "Available"

    @pytest.mark.asyncio
    async def test_empty_on_error(self):
        responses = _default_responses()
        responses["/api/v1/status"] = {"status": 500, "json": {}}
        provider = _make_provider(responses)
        result = await provider.get_summary()
        assert result.data["version"] == "unknown"


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------

class TestDetail:
    @pytest.mark.asyncio
    async def test_detail_shape(self):
        provider = _make_provider()
        result = await provider.get_detail()

        assert "requests" in result.data
        assert "services" in result.data

    @pytest.mark.asyncio
    async def test_requests_detail(self):
        provider = _make_provider()
        result = await provider.get_detail()

        reqs = result.data["requests"]
        assert reqs["total_records"] == 3
        assert len(reqs["records"]) == 3
        assert reqs["records"][0]["media_title"] == "Dune: Part Three"
        assert reqs["records"][0]["request_status"] == "Pending"

    @pytest.mark.asyncio
    async def test_radarr_services(self):
        provider = _make_provider()
        result = await provider.get_detail()

        radarr = result.data["services"]["radarr"]
        assert len(radarr) == 2
        assert radarr[0]["name"] == "Radarr (1080p)"
        assert radarr[0]["is_default"] is True
        assert radarr[1]["is_4k"] is True

    @pytest.mark.asyncio
    async def test_sonarr_services(self):
        provider = _make_provider()
        result = await provider.get_detail()

        sonarr = result.data["services"]["sonarr"]
        assert len(sonarr) == 1
        assert sonarr[0]["name"] == "Sonarr"


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

class TestActions:
    def test_action_definitions(self):
        provider = _make_provider()
        actions = provider.get_actions()
        keys = [a.key for a in actions]
        assert "approve_request" in keys
        assert "decline_request" in keys

    @pytest.mark.asyncio
    async def test_approve_request(self):
        provider = _make_provider()
        result = await provider.execute_action("approve_request", {"request_id": "1"})
        assert result.success is True
        assert "approved" in result.message.lower()

    @pytest.mark.asyncio
    async def test_approve_no_id(self):
        provider = _make_provider()
        result = await provider.execute_action("approve_request", {})
        assert result.success is False

    @pytest.mark.asyncio
    async def test_decline_request(self):
        provider = _make_provider()
        result = await provider.execute_action("decline_request", {"request_id": "1"})
        assert result.success is True
        assert "declined" in result.message.lower()

    @pytest.mark.asyncio
    async def test_unknown_action(self):
        provider = _make_provider()
        result = await provider.execute_action("nonexistent", {})
        assert result.success is False


# ---------------------------------------------------------------------------
# Validate Config
# ---------------------------------------------------------------------------

class TestValidateConfig:
    @pytest.mark.asyncio
    async def test_valid(self):
        provider = _make_provider()
        ok, msg = await provider.validate_config()
        assert ok is True
        assert "Seerr" in msg
        assert "1.33.2" in msg

    @pytest.mark.asyncio
    async def test_invalid_api_key(self):
        responses = _default_responses()
        responses["/api/v1/status"] = {"status": 401, "json": {}}
        provider = _make_provider(responses)
        ok, msg = await provider.validate_config()
        assert ok is False
        assert "Invalid API key" in msg

    @pytest.mark.asyncio
    async def test_connection_refused(self):
        provider = SeerrProvider(
            instance_id=1,
            display_name="Test",
            config={"url": "http://badhost:5055", "api_key": "key"},
        )
        transport = _ErrorTransport(httpx.ConnectError("refused"))
        provider.http_client = httpx.AsyncClient(
            transport=transport, base_url="http://badhost:5055"
        )
        ok, msg = await provider.validate_config()
        assert ok is False
        assert "Cannot connect" in msg


# ---------------------------------------------------------------------------
# Test utilities
# ---------------------------------------------------------------------------

class _ErrorTransport(httpx.AsyncBaseTransport):
    """Transport that raises a given exception on every request."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise self._error
