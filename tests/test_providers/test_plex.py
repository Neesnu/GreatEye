"""Tests for the Plex provider."""

import pytest

from tests.conftest import MockTransport, load_fixture
from src.providers.plex import PlexProvider
from src.providers.base import HealthStatus

import httpx


def _make_provider(responses: dict | None = None, **config_overrides) -> PlexProvider:
    """Create a PlexProvider with a mock HTTP client."""
    config = {
        "url": "http://10.0.0.45:32400",
        "api_key": "test-plex-token",
        **config_overrides,
    }
    provider = PlexProvider(
        instance_id=1,
        display_name="Test Plex",
        config=config,
    )
    if responses is None:
        responses = _default_responses()
    provider.http_client = httpx.AsyncClient(
        transport=MockTransport(responses),
        base_url="http://10.0.0.45:32400",
    )
    return provider


def _default_responses() -> dict:
    """Standard mock responses for a healthy Plex instance."""
    identity = load_fixture("plex", "identity")
    sections = load_fixture("plex", "sections")
    sessions = load_fixture("plex", "sessions")
    return {
        "/identity": {"status": 200, "json": identity},
        "/library/sections": {"status": 200, "json": sections},
        "/status/sessions": {"status": 200, "json": sessions},
        "/library/sections/1/refresh": {"status": 200, "json": {}},
        "/status/sessions/terminate": {"status": 200, "json": {}},
    }


# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------

class TestMeta:
    def test_type_id(self):
        meta = PlexProvider.meta()
        assert meta.type_id == "plex"

    def test_category(self):
        meta = PlexProvider.meta()
        assert meta.category == "media"

    def test_permissions_count(self):
        meta = PlexProvider.meta()
        assert len(meta.permissions) == 3

    def test_config_schema_fields(self):
        meta = PlexProvider.meta()
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
        assert "MyPlexServer" in result.message
        assert "1.40.4.8679" in result.message

    @pytest.mark.asyncio
    async def test_invalid_token(self):
        responses = _default_responses()
        responses["/identity"] = {"status": 401, "json": {}}
        provider = _make_provider(responses)
        result = await provider.health_check()
        assert result.status == HealthStatus.DOWN
        assert "Invalid Plex token" in result.message

    @pytest.mark.asyncio
    async def test_unexpected_status(self):
        responses = _default_responses()
        responses["/identity"] = {"status": 503, "json": {}}
        provider = _make_provider(responses)
        result = await provider.health_check()
        assert result.status == HealthStatus.DOWN
        assert "503" in result.message

    @pytest.mark.asyncio
    async def test_connection_refused(self):
        provider = PlexProvider(
            instance_id=1,
            display_name="Test",
            config={"url": "http://badhost:32400", "api_key": "key"},
        )
        transport = _ErrorTransport(httpx.ConnectError("refused"))
        provider.http_client = httpx.AsyncClient(
            transport=transport, base_url="http://badhost:32400"
        )
        result = await provider.health_check()
        assert result.status == HealthStatus.DOWN
        assert "refused" in result.message.lower()


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

class TestSummary:
    @pytest.mark.asyncio
    async def test_summary_shape(self):
        provider = _make_provider()
        result = await provider.get_summary()

        assert result.data["library_count"] == 3
        assert result.data["stream_count"] == 2
        assert result.data["transcode_count"] == 1

    @pytest.mark.asyncio
    async def test_libraries(self):
        provider = _make_provider()
        result = await provider.get_summary()

        libs = result.data["libraries"]
        assert len(libs) == 3
        assert libs[0]["title"] == "Movies"
        assert libs[0]["type"] == "movie"
        assert libs[0]["count"] == 450

    @pytest.mark.asyncio
    async def test_sessions(self):
        provider = _make_provider()
        result = await provider.get_summary()

        sessions = result.data["sessions"]
        assert len(sessions) == 2
        assert sessions[0]["user"] == "admin"
        assert sessions[0]["title"] == "The Matrix"
        assert sessions[0]["state"] == "playing"
        assert sessions[0]["is_transcode"] is False

    @pytest.mark.asyncio
    async def test_transcode_session(self):
        provider = _make_provider()
        result = await provider.get_summary()

        sessions = result.data["sessions"]
        assert sessions[1]["user"] == "john"
        assert sessions[1]["is_transcode"] is True
        assert sessions[1]["state"] == "paused"

    @pytest.mark.asyncio
    async def test_session_progress(self):
        provider = _make_provider()
        result = await provider.get_summary()

        sessions = result.data["sessions"]
        # 3600000 / 8160000 * 100 ≈ 44.12%
        assert 44 <= sessions[0]["progress"] <= 45

    @pytest.mark.asyncio
    async def test_empty_on_error(self):
        responses = _default_responses()
        responses["/library/sections"] = {"status": 500, "json": {}}
        provider = _make_provider(responses)
        result = await provider.get_summary()
        assert result.data["library_count"] is None or result.data["libraries"] == []


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------

class TestDetail:
    @pytest.mark.asyncio
    async def test_detail_shape(self):
        provider = _make_provider()
        result = await provider.get_detail()

        assert "libraries" in result.data
        assert "sessions" in result.data
        assert "server" in result.data

    @pytest.mark.asyncio
    async def test_libraries_detail(self):
        provider = _make_provider()
        result = await provider.get_detail()

        libs = result.data["libraries"]
        assert len(libs) == 3
        assert libs[0]["title"] == "Movies"
        assert libs[0]["agent"] == "tv.plex.agents.movie"

    @pytest.mark.asyncio
    async def test_sessions_detail(self):
        provider = _make_provider()
        result = await provider.get_detail()

        sessions = result.data["sessions"]
        assert len(sessions) == 2
        assert sessions[0]["video_resolution"] == "1080"
        assert sessions[0]["video_codec"] == "h264"
        assert sessions[0]["bandwidth"] == 12000

    @pytest.mark.asyncio
    async def test_server_info(self):
        provider = _make_provider()
        result = await provider.get_detail()

        server = result.data["server"]
        assert server["name"] == "MyPlexServer"
        assert server["version"] == "1.40.4.8679"
        assert server["platform"] == "Linux"


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

class TestActions:
    def test_action_definitions(self):
        provider = _make_provider()
        actions = provider.get_actions()
        keys = [a.key for a in actions]
        assert "scan_library" in keys
        assert "kill_stream" in keys

    @pytest.mark.asyncio
    async def test_scan_library(self):
        provider = _make_provider()
        result = await provider.execute_action("scan_library", {"section_id": "1"})
        assert result.success is True
        assert "scan" in result.message.lower()

    @pytest.mark.asyncio
    async def test_scan_library_no_id(self):
        provider = _make_provider()
        result = await provider.execute_action("scan_library", {})
        assert result.success is False

    @pytest.mark.asyncio
    async def test_kill_stream(self):
        provider = _make_provider()
        result = await provider.execute_action("kill_stream", {"session_id": "sess-001"})
        assert result.success is True
        assert "terminated" in result.message.lower()

    @pytest.mark.asyncio
    async def test_kill_stream_no_id(self):
        provider = _make_provider()
        result = await provider.execute_action("kill_stream", {})
        assert result.success is False

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
        assert "MyPlexServer" in msg
        assert "1.40.4.8679" in msg

    @pytest.mark.asyncio
    async def test_invalid_token(self):
        responses = _default_responses()
        responses["/identity"] = {"status": 401, "json": {}}
        provider = _make_provider(responses)
        ok, msg = await provider.validate_config()
        assert ok is False
        assert "Invalid Plex token" in msg

    @pytest.mark.asyncio
    async def test_connection_refused(self):
        provider = PlexProvider(
            instance_id=1,
            display_name="Test",
            config={"url": "http://badhost:32400", "api_key": "key"},
        )
        transport = _ErrorTransport(httpx.ConnectError("refused"))
        provider.http_client = httpx.AsyncClient(
            transport=transport, base_url="http://badhost:32400"
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
