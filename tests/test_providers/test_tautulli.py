"""Tests for the Tautulli provider."""

import json
from urllib.parse import parse_qs, urlparse

import pytest

from tests.conftest import load_fixture
from src.providers.tautulli import TautulliProvider
from src.providers.base import HealthStatus

import httpx


class TautulliMockTransport(httpx.AsyncBaseTransport):
    """Mock transport for Tautulli that routes by cmd= query parameter.

    Tautulli uses a single endpoint /api/v2 with ?cmd=xxx to dispatch,
    so the standard path-based MockTransport won't work.
    """

    def __init__(self, responses: dict[str, dict]) -> None:
        self.responses = responses  # keyed by cmd name

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        cmd = params.get("cmd", [""])[0]

        if cmd in self.responses:
            resp_data = self.responses[cmd]
            return httpx.Response(
                status_code=resp_data.get("status", 200),
                json=resp_data.get("json", {}),
            )
        return httpx.Response(status_code=404)


def _make_provider(responses: dict | None = None, **config_overrides) -> TautulliProvider:
    """Create a TautulliProvider with a mock HTTP client."""
    config = {
        "url": "http://10.0.0.45:8181",
        "api_key": "test-api-key-tautulli",
        **config_overrides,
    }
    provider = TautulliProvider(
        instance_id=1,
        display_name="Test Tautulli",
        config=config,
    )
    if responses is None:
        responses = _default_responses()
    provider.http_client = httpx.AsyncClient(
        transport=TautulliMockTransport(responses),
        base_url="http://10.0.0.45:8181",
    )
    return provider


def _default_responses() -> dict:
    """Standard mock responses keyed by Tautulli cmd name."""
    server_info = load_fixture("tautulli", "server_info")
    get_activity = load_fixture("tautulli", "get_activity")
    get_recently_added = load_fixture("tautulli", "get_recently_added")
    get_libraries = load_fixture("tautulli", "get_libraries")
    get_history = load_fixture("tautulli", "get_history")
    refresh_libraries = load_fixture("tautulli", "refresh_libraries")
    return {
        "get_server_info": {"status": 200, "json": server_info},
        "get_activity": {"status": 200, "json": get_activity},
        "get_recently_added": {"status": 200, "json": get_recently_added},
        "get_libraries": {"status": 200, "json": get_libraries},
        "get_history": {"status": 200, "json": get_history},
        "refresh_libraries_list": {"status": 200, "json": refresh_libraries},
    }


# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------

class TestMeta:
    def test_type_id(self):
        meta = TautulliProvider.meta()
        assert meta.type_id == "tautulli"

    def test_category(self):
        meta = TautulliProvider.meta()
        assert meta.category == "media"

    def test_permissions_count(self):
        meta = TautulliProvider.meta()
        assert len(meta.permissions) == 2

    def test_config_schema_fields(self):
        meta = TautulliProvider.meta()
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
        assert "2.13.4" in result.message
        assert "MyPlexServer" in result.message

    @pytest.mark.asyncio
    async def test_empty_response(self):
        """Test when server_info returns empty data (e.g. bad API key)."""
        responses = _default_responses()
        responses["get_server_info"] = {
            "status": 200,
            "json": {"response": {"result": "error", "message": "Invalid API key", "data": {}}},
        }
        provider = _make_provider(responses)
        result = await provider.health_check()
        assert result.status == HealthStatus.DOWN

    @pytest.mark.asyncio
    async def test_connection_refused(self):
        provider = TautulliProvider(
            instance_id=1,
            display_name="Test",
            config={"url": "http://badhost:8181", "api_key": "key"},
        )
        transport = _ErrorTransport(httpx.ConnectError("refused"))
        provider.http_client = httpx.AsyncClient(
            transport=transport, base_url="http://badhost:8181"
        )
        result = await provider.health_check()
        assert result.status == HealthStatus.DOWN
        assert "refused" in result.message.lower()

    @pytest.mark.asyncio
    async def test_timeout(self):
        provider = TautulliProvider(
            instance_id=1,
            display_name="Test",
            config={"url": "http://slow:8181", "api_key": "key"},
        )
        transport = _ErrorTransport(httpx.TimeoutException("timed out"))
        provider.http_client = httpx.AsyncClient(
            transport=transport, base_url="http://slow:8181"
        )
        result = await provider.health_check()
        assert result.status == HealthStatus.DOWN
        assert "timed out" in result.message.lower()


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

class TestSummary:
    @pytest.mark.asyncio
    async def test_summary_shape(self):
        provider = _make_provider()
        result = await provider.get_summary()

        assert result.data["stream_count"] == 2
        assert result.data["transcode_count"] == 1
        assert result.data["total_bandwidth"] == 20000

    @pytest.mark.asyncio
    async def test_sessions(self):
        provider = _make_provider()
        result = await provider.get_summary()

        sessions = result.data["sessions"]
        assert len(sessions) == 2
        assert sessions[0]["user"] == "admin"
        assert sessions[0]["title"] == "The Matrix (1999)"
        assert sessions[0]["state"] == "playing"
        assert sessions[0]["transcode_decision"] == "direct play"

    @pytest.mark.asyncio
    async def test_transcode_session(self):
        provider = _make_provider()
        result = await provider.get_summary()

        sessions = result.data["sessions"]
        assert sessions[1]["user"] == "john"
        assert sessions[1]["transcode_decision"] == "transcode"
        assert sessions[1]["state"] == "paused"

    @pytest.mark.asyncio
    async def test_recently_added(self):
        provider = _make_provider()
        result = await provider.get_summary()

        recent = result.data["recently_added"]
        assert len(recent) == 3
        assert recent[0]["title"] == "Oppenheimer"
        assert recent[0]["media_type"] == "movie"

    @pytest.mark.asyncio
    async def test_recently_added_episode(self):
        provider = _make_provider()
        result = await provider.get_summary()

        recent = result.data["recently_added"]
        assert recent[1]["grandparent_title"] == "Better Call Saul"
        assert recent[1]["media_type"] == "episode"

    @pytest.mark.asyncio
    async def test_stream_count_string_parsing(self):
        """Tautulli returns stream_count as a string."""
        provider = _make_provider()
        result = await provider.get_summary()
        assert isinstance(result.data["stream_count"], int)
        assert result.data["stream_count"] == 2

    @pytest.mark.asyncio
    async def test_empty_on_error(self):
        responses = _default_responses()
        responses["get_activity"] = {"status": 500, "json": {}}
        provider = _make_provider(responses)
        result = await provider.get_summary()
        # Should still work but with 0 streams since activity failed
        assert result.data["stream_count"] == 0


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------

class TestDetail:
    @pytest.mark.asyncio
    async def test_detail_shape(self):
        provider = _make_provider()
        result = await provider.get_detail()

        assert "activity" in result.data
        assert "recently_added" in result.data
        assert "libraries" in result.data
        assert "history" in result.data

    @pytest.mark.asyncio
    async def test_activity_detail(self):
        provider = _make_provider()
        result = await provider.get_detail()

        activity = result.data["activity"]
        assert isinstance(activity, dict)
        # Should be the raw activity data
        assert "sessions" in activity or "stream_count" in activity

    @pytest.mark.asyncio
    async def test_recently_added_detail(self):
        provider = _make_provider()
        result = await provider.get_detail()

        recent = result.data["recently_added"]
        assert len(recent) >= 1
        assert recent[0]["title"] == "Oppenheimer"
        assert recent[0]["library_name"] == "Movies"

    @pytest.mark.asyncio
    async def test_libraries_detail(self):
        provider = _make_provider()
        result = await provider.get_detail()

        libraries = result.data["libraries"]
        assert len(libraries) == 2
        assert libraries[0]["section_name"] == "Movies"
        assert libraries[0]["section_type"] == "movie"

    @pytest.mark.asyncio
    async def test_history_detail(self):
        provider = _make_provider()
        result = await provider.get_detail()

        history = result.data["history"]
        assert len(history) == 2
        assert history[0]["user"] == "admin"
        assert history[0]["title"] == "The Matrix (1999)"
        assert history[0]["media_type"] == "movie"
        assert history[0]["play_duration"] == 7200
        assert history[0]["player"] == "Plex Web"


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

class TestActions:
    def test_action_definitions(self):
        provider = _make_provider()
        actions = provider.get_actions()
        keys = [a.key for a in actions]
        assert "refresh_libraries" in keys

    @pytest.mark.asyncio
    async def test_refresh_libraries(self):
        provider = _make_provider()
        result = await provider.execute_action("refresh_libraries", {})
        assert result.success is True
        assert "refreshed" in result.message.lower()

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
        assert "2.13.4" in msg
        assert "MyPlexServer" in msg

    @pytest.mark.asyncio
    async def test_bad_api_key(self):
        responses = _default_responses()
        responses["get_server_info"] = {
            "status": 200,
            "json": {"response": {"result": "error", "message": "Invalid API key", "data": {}}},
        }
        provider = _make_provider(responses)
        ok, msg = await provider.validate_config()
        assert ok is False
        assert "authenticate" in msg.lower() or "API key" in msg

    @pytest.mark.asyncio
    async def test_connection_refused(self):
        provider = TautulliProvider(
            instance_id=1,
            display_name="Test",
            config={"url": "http://badhost:8181", "api_key": "key"},
        )
        transport = _ErrorTransport(httpx.ConnectError("refused"))
        provider.http_client = httpx.AsyncClient(
            transport=transport, base_url="http://badhost:8181"
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
