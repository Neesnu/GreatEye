"""Tests for the Prowlarr provider."""

import pytest

from tests.conftest import MockTransport, load_fixture
from src.providers.prowlarr import ProwlarrProvider
from src.providers.base import HealthStatus

import httpx


def _make_provider(responses: dict | None = None, **config_overrides) -> ProwlarrProvider:
    """Create a ProwlarrProvider with a mock HTTP client."""
    config = {
        "url": "http://10.0.0.45:9696",
        "api_key": "test-api-key-prowlarr",
        **config_overrides,
    }
    provider = ProwlarrProvider(
        instance_id=1,
        display_name="Test Prowlarr",
        config=config,
    )
    if responses is None:
        responses = _default_responses()
    provider.http_client = httpx.AsyncClient(
        transport=MockTransport(responses),
        base_url="http://10.0.0.45:9696",
    )
    return provider


def _default_responses() -> dict:
    """Standard mock responses for a healthy Prowlarr instance."""
    system_status = load_fixture("prowlarr", "system_status")
    health = load_fixture("prowlarr", "health")
    indexer = load_fixture("prowlarr", "indexer")
    indexerstats = load_fixture("prowlarr", "indexerstats")
    application = load_fixture("prowlarr", "application")
    history = load_fixture("prowlarr", "history")
    command = load_fixture("prowlarr", "command")
    return {
        "/api/v1/system/status": {"status": 200, "json": system_status},
        "/api/v1/health": {"status": 200, "json": health},
        "/api/v1/indexer": {"status": 200, "json": indexer},
        "/api/v1/indexerstats": {"status": 200, "json": indexerstats},
        "/api/v1/application": {"status": 200, "json": application},
        "/api/v1/history": {"status": 200, "json": history},
        "/api/v1/command": {"status": 201, "json": command},
        "/api/v1/indexer/test": {"status": 200, "json": []},
        "/api/v1/indexer/testall": {"status": 200, "json": []},
    }


# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------

class TestMeta:
    def test_type_id(self):
        meta = ProwlarrProvider.meta()
        assert meta.type_id == "prowlarr"

    def test_category(self):
        meta = ProwlarrProvider.meta()
        assert meta.category == "media"

    def test_permissions_count(self):
        meta = ProwlarrProvider.meta()
        assert len(meta.permissions) == 4

    def test_config_schema_fields(self):
        meta = ProwlarrProvider.meta()
        keys = [f["key"] for f in meta.config_schema["fields"]]
        assert "url" in keys
        assert "api_key" in keys

    def test_api_version(self):
        provider = _make_provider()
        assert provider.api_version == "v1"
        assert provider.api_base == "/api/v1"


# ---------------------------------------------------------------------------
# Health Check (inherited from ArrBaseProvider)
# ---------------------------------------------------------------------------

class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_healthy(self):
        provider = _make_provider()
        result = await provider.health_check()
        assert result.status == HealthStatus.UP
        assert "1.12.2.4211" in result.message

    @pytest.mark.asyncio
    async def test_invalid_api_key(self):
        responses = _default_responses()
        responses["/api/v1/system/status"] = {"status": 401, "json": {}}
        provider = _make_provider(responses)
        result = await provider.health_check()
        assert result.status == HealthStatus.DOWN
        assert "Invalid API key" in result.message

    @pytest.mark.asyncio
    async def test_wrong_app(self):
        responses = _default_responses()
        status = load_fixture("prowlarr", "system_status")
        status["appName"] = "Sonarr"
        responses["/api/v1/system/status"] = {"status": 200, "json": status}
        provider = _make_provider(responses)
        result = await provider.health_check()
        assert result.status == HealthStatus.DOWN
        assert "Expected Prowlarr" in result.message

    @pytest.mark.asyncio
    async def test_connection_refused(self):
        provider = ProwlarrProvider(
            instance_id=1,
            display_name="Test",
            config={"url": "http://badhost:9696", "api_key": "key"},
        )
        transport = _ErrorTransport(httpx.ConnectError("refused"))
        provider.http_client = httpx.AsyncClient(
            transport=transport, base_url="http://badhost:9696"
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

        assert result.data["indexer_count"] == 3
        assert result.data["indexers_healthy"] == 1  # NZBgeek healthy
        assert result.data["indexers_failing"] == 1  # 1337x >50% fail rate
        assert result.data["indexers_disabled"] == 1  # OldIndexer disabled

    @pytest.mark.asyncio
    async def test_total_stats(self):
        provider = _make_provider()
        result = await provider.get_summary()

        assert result.data["total_grabs_today"] == 35  # 25 + 10
        assert result.data["total_queries_today"] == 250  # 150 + 100

    @pytest.mark.asyncio
    async def test_indexer_list(self):
        provider = _make_provider()
        result = await provider.get_summary()

        indexers = result.data["indexers"]
        assert len(indexers) == 3
        nzbgeek = next(i for i in indexers if i["name"] == "NZBgeek")
        assert nzbgeek["status"] == "healthy"
        assert nzbgeek["protocol"] == "usenet"
        assert nzbgeek["number_of_grabs"] == 25

    @pytest.mark.asyncio
    async def test_failing_indexer(self):
        provider = _make_provider()
        result = await provider.get_summary()

        indexers = result.data["indexers"]
        leet = next(i for i in indexers if i["name"] == "1337x")
        assert leet["status"] == "failing"  # 65 failures / 100 queries > 50%

    @pytest.mark.asyncio
    async def test_apps(self):
        provider = _make_provider()
        result = await provider.get_summary()

        apps = result.data["apps"]
        assert len(apps) == 2
        assert apps[0]["name"] == "Sonarr"

    @pytest.mark.asyncio
    async def test_health_warnings_empty(self):
        provider = _make_provider()
        result = await provider.get_summary()
        assert result.data["health_warnings"] == []

    @pytest.mark.asyncio
    async def test_empty_on_error(self):
        responses = _default_responses()
        responses["/api/v1/indexer"] = {"status": 500, "json": {}}
        provider = _make_provider(responses)
        result = await provider.get_summary()
        assert result.data["indexer_count"] == 0


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------

class TestDetail:
    @pytest.mark.asyncio
    async def test_detail_shape(self):
        provider = _make_provider()
        result = await provider.get_detail()

        assert "indexers" in result.data
        assert "apps" in result.data
        assert "history" in result.data
        assert "health" in result.data

    @pytest.mark.asyncio
    async def test_indexer_detail(self):
        provider = _make_provider()
        result = await provider.get_detail()

        indexers = result.data["indexers"]
        assert len(indexers) == 3
        nzbgeek = next(i for i in indexers if i["name"] == "NZBgeek")
        assert nzbgeek["derived_status"] == "healthy"
        assert nzbgeek["stats"]["number_of_queries"] == 150

    @pytest.mark.asyncio
    async def test_apps_detail(self):
        provider = _make_provider()
        result = await provider.get_detail()

        apps = result.data["apps"]
        assert len(apps) == 2
        assert apps[0]["implementation"] == "Sonarr"
        assert apps[0]["sync_level"] == "fullSync"

    @pytest.mark.asyncio
    async def test_history_detail(self):
        provider = _make_provider()
        result = await provider.get_detail()

        history = result.data["history"]
        assert len(history) == 2
        assert history[0]["event_type"] == "indexerQuery"
        assert history[0]["successful"] is True
        assert history[1]["successful"] is False


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

class TestActions:
    def test_action_definitions(self):
        provider = _make_provider()
        actions = provider.get_actions()
        keys = [a.key for a in actions]
        assert "test_indexer" in keys
        assert "test_all_indexers" in keys
        assert "sync_apps" in keys

    @pytest.mark.asyncio
    async def test_test_indexer(self):
        provider = _make_provider()
        result = await provider.execute_action("test_indexer", {"indexer_id": "1"})
        assert result.success is True
        assert "passed" in result.message.lower()

    @pytest.mark.asyncio
    async def test_test_indexer_no_id(self):
        provider = _make_provider()
        result = await provider.execute_action("test_indexer", {})
        assert result.success is False

    @pytest.mark.asyncio
    async def test_test_all_indexers(self):
        provider = _make_provider()
        result = await provider.execute_action("test_all_indexers", {})
        assert result.success is True
        assert "passed" in result.message.lower()

    @pytest.mark.asyncio
    async def test_sync_apps(self):
        provider = _make_provider()
        result = await provider.execute_action("sync_apps", {})
        assert result.success is True

    @pytest.mark.asyncio
    async def test_unknown_action(self):
        provider = _make_provider()
        result = await provider.execute_action("nonexistent", {})
        assert result.success is False


# ---------------------------------------------------------------------------
# Validate Config (inherited from ArrBaseProvider)
# ---------------------------------------------------------------------------

class TestValidateConfig:
    @pytest.mark.asyncio
    async def test_valid(self):
        provider = _make_provider()
        ok, msg = await provider.validate_config()
        assert ok is True
        assert "Prowlarr" in msg
        assert "1.12.2.4211" in msg

    @pytest.mark.asyncio
    async def test_invalid_api_key(self):
        responses = _default_responses()
        responses["/api/v1/system/status"] = {"status": 401, "json": {}}
        provider = _make_provider(responses)
        ok, msg = await provider.validate_config()
        assert ok is False
        assert "Invalid API key" in msg

    @pytest.mark.asyncio
    async def test_wrong_app(self):
        responses = _default_responses()
        status = load_fixture("prowlarr", "system_status")
        status["appName"] = "Radarr"
        responses["/api/v1/system/status"] = {"status": 200, "json": status}
        provider = _make_provider(responses)
        ok, msg = await provider.validate_config()
        assert ok is False
        assert "Radarr" in msg
        assert "Prowlarr" in msg


# ---------------------------------------------------------------------------
# Test utilities
# ---------------------------------------------------------------------------

class _ErrorTransport(httpx.AsyncBaseTransport):
    """Transport that raises a given exception on every request."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise self._error
