"""Tests for the Sonarr provider."""

import pytest

from tests.conftest import MockTransport, load_fixture
from src.providers.sonarr import SonarrProvider
from src.providers.base import HealthStatus

import httpx


def _make_provider(responses: dict | None = None, **config_overrides) -> SonarrProvider:
    """Create a SonarrProvider with a mock HTTP client."""
    config = {
        "url": "http://10.0.0.45:8989",
        "api_key": "test-api-key-123",
        **config_overrides,
    }
    provider = SonarrProvider(
        instance_id=1,
        display_name="Test Sonarr",
        config=config,
    )
    if responses is None:
        responses = _default_responses()
    provider.http_client = httpx.AsyncClient(
        transport=MockTransport(responses),
        base_url="http://10.0.0.45:8989",
    )
    return provider


def _default_responses() -> dict:
    """Standard mock responses for a healthy Sonarr instance."""
    system_status = load_fixture("sonarr", "system_status")
    health = load_fixture("sonarr", "health")
    series = load_fixture("sonarr", "series")
    queue = load_fixture("sonarr", "queue")
    wanted = load_fixture("sonarr", "wanted_missing")
    calendar = load_fixture("sonarr", "calendar")
    diskspace = load_fixture("sonarr", "diskspace")
    rootfolder = load_fixture("sonarr", "rootfolder")
    qualityprofile = load_fixture("sonarr", "qualityprofile")
    tag = load_fixture("sonarr", "tag")
    command = load_fixture("sonarr", "command")
    return {
        "/api/v3/system/status": {"status": 200, "json": system_status},
        "/api/v3/health": {"status": 200, "json": health},
        "/api/v3/series": {"status": 200, "json": series},
        "/api/v3/queue": {"status": 200, "json": queue},
        "/api/v3/wanted/missing": {"status": 200, "json": wanted},
        "/api/v3/calendar": {"status": 200, "json": calendar},
        "/api/v3/diskspace": {"status": 200, "json": diskspace},
        "/api/v3/rootfolder": {"status": 200, "json": rootfolder},
        "/api/v3/qualityprofile": {"status": 200, "json": qualityprofile},
        "/api/v3/tag": {"status": 200, "json": tag},
        "/api/v3/command": {"status": 201, "json": command},
        "/api/v3/series/1": {"status": 200, "json": {}},
    }


# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------

class TestMeta:
    def test_type_id(self):
        meta = SonarrProvider.meta()
        assert meta.type_id == "sonarr"

    def test_category(self):
        meta = SonarrProvider.meta()
        assert meta.category == "media"

    def test_permissions_count(self):
        meta = SonarrProvider.meta()
        assert len(meta.permissions) == 5

    def test_config_schema_fields(self):
        meta = SonarrProvider.meta()
        keys = [f["key"] for f in meta.config_schema["fields"]]
        assert "url" in keys
        assert "api_key" in keys


# ---------------------------------------------------------------------------
# Health Check (inherited from ArrBaseProvider)
# ---------------------------------------------------------------------------

class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_healthy(self):
        provider = _make_provider()
        result = await provider.health_check()
        # Has 1 warning so should be DEGRADED
        assert result.status == HealthStatus.DEGRADED
        assert "1 warning" in result.message

    @pytest.mark.asyncio
    async def test_healthy_no_warnings(self):
        responses = _default_responses()
        responses["/api/v3/health"] = {"status": 200, "json": []}
        provider = _make_provider(responses)
        result = await provider.health_check()
        assert result.status == HealthStatus.UP
        assert "4.0.9.2244" in result.message

    @pytest.mark.asyncio
    async def test_invalid_api_key(self):
        responses = _default_responses()
        responses["/api/v3/system/status"] = {"status": 401, "json": {}}
        provider = _make_provider(responses)
        result = await provider.health_check()
        assert result.status == HealthStatus.DOWN
        assert "Invalid API key" in result.message

    @pytest.mark.asyncio
    async def test_wrong_app(self):
        responses = _default_responses()
        status = load_fixture("sonarr", "system_status")
        status["appName"] = "Radarr"
        responses["/api/v3/system/status"] = {"status": 200, "json": status}
        provider = _make_provider(responses)
        result = await provider.health_check()
        assert result.status == HealthStatus.DOWN
        assert "Expected Sonarr" in result.message

    @pytest.mark.asyncio
    async def test_connection_refused(self):
        provider = SonarrProvider(
            instance_id=1,
            display_name="Test",
            config={"url": "http://badhost:8989", "api_key": "key"},
        )
        transport = _ErrorTransport(httpx.ConnectError("refused"))
        provider.http_client = httpx.AsyncClient(
            transport=transport, base_url="http://badhost:8989"
        )
        result = await provider.health_check()
        assert result.status == HealthStatus.DOWN
        assert "refused" in result.message.lower()

    @pytest.mark.asyncio
    async def test_health_with_errors(self):
        responses = _default_responses()
        responses["/api/v3/health"] = {
            "status": 200,
            "json": [{"source": "Test", "type": "error", "message": "Critical"}],
        }
        provider = _make_provider(responses)
        result = await provider.health_check()
        assert result.status == HealthStatus.DEGRADED
        assert "1 error" in result.message


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

class TestSummary:
    @pytest.mark.asyncio
    async def test_summary_shape(self):
        provider = _make_provider()
        result = await provider.get_summary()

        assert result.data["series_count"] == 3
        assert result.data["series_monitored"] == 2
        # 20 + 12 + 8 = 40 episode files
        assert result.data["episode_file_count"] == 40
        # 20 + 16 + 8 = 44 total episodes
        assert result.data["episode_count"] == 44
        assert result.data["missing_count"] == 4

    @pytest.mark.asyncio
    async def test_queue_in_summary(self):
        provider = _make_provider()
        result = await provider.get_summary()

        queue = result.data["queue"]
        assert queue["total"] == 2
        assert queue["downloading"] == 2
        assert len(queue["records"]) == 2

    @pytest.mark.asyncio
    async def test_queue_item_normalization(self):
        provider = _make_provider()
        result = await provider.get_summary()

        item = result.data["queue"]["records"][0]
        assert item["media_title"] == "The Last of Us"
        assert item["detail_title"] == "S02E04 - Kin"
        assert item["quality"] == "HDTV-1080p"
        assert item["progress"] == 50.0

    @pytest.mark.asyncio
    async def test_queue_error_messages(self):
        provider = _make_provider()
        result = await provider.get_summary()

        errored = result.data["queue"]["records"][1]
        assert errored["error_message"] == "Not enough seeders"
        assert errored["tracked_download_status"] == "warning"

    @pytest.mark.asyncio
    async def test_calendar_upcoming(self):
        provider = _make_provider()
        result = await provider.get_summary()

        cal = result.data["calendar_upcoming"]
        assert len(cal) == 2
        assert cal[0]["series_title"] == "The Last of Us"
        assert cal[0]["season_number"] == 2
        assert cal[0]["episode_number"] == 6

    @pytest.mark.asyncio
    async def test_health_warnings_in_summary(self):
        provider = _make_provider()
        result = await provider.get_summary()
        assert len(result.data["health_warnings"]) == 1
        assert result.data["health_warnings"][0]["type"] == "warning"

    @pytest.mark.asyncio
    async def test_size_on_disk(self):
        provider = _make_provider()
        result = await provider.get_summary()
        # 25769803776 + 55834574848 + 21474836480
        expected = 25769803776 + 55834574848 + 21474836480
        assert result.data["size_on_disk"] == expected
        assert "TB" in result.data["size_on_disk_formatted"] or "GB" in result.data["size_on_disk_formatted"]

    @pytest.mark.asyncio
    async def test_empty_on_error(self):
        responses = _default_responses()
        responses["/api/v3/series"] = {"status": 500, "json": {}}
        provider = _make_provider(responses)
        result = await provider.get_summary()
        # Still works because gather handles partial failures
        assert result.data["series_count"] == 0


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------

class TestDetail:
    @pytest.mark.asyncio
    async def test_detail_shape(self):
        provider = _make_provider()
        result = await provider.get_detail()

        assert "series" in result.data
        assert "queue" in result.data
        assert "missing" in result.data
        assert "calendar" in result.data
        assert "health" in result.data
        assert "disk_space" in result.data
        assert "root_folders" in result.data
        assert "quality_profiles" in result.data
        assert "tags" in result.data

    @pytest.mark.asyncio
    async def test_series_data(self):
        provider = _make_provider()
        result = await provider.get_detail()

        series = result.data["series"]
        assert len(series) == 3
        assert series[0]["title"] == "Breaking Bad"
        assert series[0]["episode_count"] == 20
        assert series[0]["percent_complete"] == 100.0

    @pytest.mark.asyncio
    async def test_series_seasons(self):
        provider = _make_provider()
        result = await provider.get_detail()

        seasons = result.data["series"][0]["seasons"]
        assert len(seasons) == 2
        assert seasons[0]["season_number"] == 1
        assert seasons[0]["episode_file_count"] == 7

    @pytest.mark.asyncio
    async def test_missing_data(self):
        provider = _make_provider()
        result = await provider.get_detail()

        missing = result.data["missing"]
        assert missing["total_records"] == 4
        assert len(missing["records"]) == 1
        assert missing["records"][0]["series_title"] == "The Last of Us"

    @pytest.mark.asyncio
    async def test_disk_space(self):
        provider = _make_provider()
        result = await provider.get_detail()

        disks = result.data["disk_space"]
        assert len(disks) == 2
        assert disks[0]["path"] == "/00_media"
        assert disks[0]["free_space"] == 1099511627776

    @pytest.mark.asyncio
    async def test_quality_profiles(self):
        provider = _make_provider()
        result = await provider.get_detail()

        profiles = result.data["quality_profiles"]
        assert len(profiles) == 2
        assert profiles[0]["name"] == "HD-1080p"

    @pytest.mark.asyncio
    async def test_tags(self):
        provider = _make_provider()
        result = await provider.get_detail()

        tags = result.data["tags"]
        assert len(tags) == 2
        assert tags[0]["label"] == "important"


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

class TestActions:
    def test_action_definitions(self):
        provider = _make_provider()
        actions = provider.get_actions()
        keys = [a.key for a in actions]
        assert "search_episode" in keys
        assert "search_season" in keys
        assert "search_series" in keys
        assert "search_missing" in keys
        assert "refresh_series" in keys
        assert "delete_series" in keys
        assert "remove_from_queue" in keys

    @pytest.mark.asyncio
    async def test_search_episode(self):
        provider = _make_provider()
        result = await provider.execute_action("search_episode", {"episode_ids": "501,502"})
        assert result.success is True
        assert "EpisodeSearch" in result.message

    @pytest.mark.asyncio
    async def test_search_episode_no_ids(self):
        provider = _make_provider()
        result = await provider.execute_action("search_episode", {})
        assert result.success is False

    @pytest.mark.asyncio
    async def test_search_season(self):
        provider = _make_provider()
        result = await provider.execute_action(
            "search_season", {"series_id": "1", "season_number": "2"}
        )
        assert result.success is True

    @pytest.mark.asyncio
    async def test_search_series(self):
        provider = _make_provider()
        result = await provider.execute_action("search_series", {"series_id": "1"})
        assert result.success is True

    @pytest.mark.asyncio
    async def test_search_missing(self):
        provider = _make_provider()
        result = await provider.execute_action("search_missing", {})
        assert result.success is True
        assert "MissingEpisodeSearch" in result.message

    @pytest.mark.asyncio
    async def test_refresh_series(self):
        provider = _make_provider()
        result = await provider.execute_action("refresh_series", {"series_id": "1"})
        assert result.success is True

    @pytest.mark.asyncio
    async def test_delete_series(self):
        responses = _default_responses()
        responses["/api/v3/series/1"] = {"status": 200, "json": {}}
        provider = _make_provider(responses)
        result = await provider.execute_action(
            "delete_series", {"series_id": "1", "delete_files": "false"}
        )
        assert result.success is True
        assert "deleted" in result.message.lower()

    @pytest.mark.asyncio
    async def test_delete_series_with_files(self):
        responses = _default_responses()
        responses["/api/v3/series/1"] = {"status": 200, "json": {}}
        provider = _make_provider(responses)
        result = await provider.execute_action(
            "delete_series", {"series_id": "1", "delete_files": "true"}
        )
        assert result.success is True
        assert "files removed" in result.message.lower()

    @pytest.mark.asyncio
    async def test_remove_from_queue(self):
        responses = _default_responses()
        responses["/api/v3/queue/101"] = {"status": 200, "json": {}}
        provider = _make_provider(responses)
        result = await provider.execute_action(
            "remove_from_queue", {"queue_id": "101"}
        )
        assert result.success is True
        assert "removed" in result.message.lower()

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
        assert "Sonarr" in msg
        assert "4.0.9.2244" in msg

    @pytest.mark.asyncio
    async def test_invalid_api_key(self):
        responses = _default_responses()
        responses["/api/v3/system/status"] = {"status": 401, "json": {}}
        provider = _make_provider(responses)
        ok, msg = await provider.validate_config()
        assert ok is False
        assert "Invalid API key" in msg

    @pytest.mark.asyncio
    async def test_wrong_app(self):
        responses = _default_responses()
        status = load_fixture("sonarr", "system_status")
        status["appName"] = "Radarr"
        responses["/api/v3/system/status"] = {"status": 200, "json": status}
        provider = _make_provider(responses)
        ok, msg = await provider.validate_config()
        assert ok is False
        assert "Radarr" in msg
        assert "Sonarr" in msg

    @pytest.mark.asyncio
    async def test_connect_error(self):
        provider = SonarrProvider(
            instance_id=1,
            display_name="Test",
            config={"url": "http://badhost:8989", "api_key": "key"},
        )
        transport = _ErrorTransport(httpx.ConnectError("refused"))
        provider.http_client = httpx.AsyncClient(
            transport=transport, base_url="http://badhost:8989"
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
