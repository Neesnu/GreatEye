"""Tests for the Radarr provider."""

import pytest

from tests.conftest import MockTransport, load_fixture
from src.providers.radarr import RadarrProvider
from src.providers.base import HealthStatus

import httpx


def _make_provider(responses: dict | None = None, **config_overrides) -> RadarrProvider:
    """Create a RadarrProvider with a mock HTTP client."""
    config = {
        "url": "http://10.0.0.45:7878",
        "api_key": "test-api-key-456",
        **config_overrides,
    }
    provider = RadarrProvider(
        instance_id=1,
        display_name="Test Radarr",
        config=config,
    )
    if responses is None:
        responses = _default_responses()
    provider.http_client = httpx.AsyncClient(
        transport=MockTransport(responses),
        base_url="http://10.0.0.45:7878",
    )
    return provider


def _default_responses() -> dict:
    """Standard mock responses for a healthy Radarr instance."""
    system_status = load_fixture("radarr", "system_status")
    health = load_fixture("radarr", "health")
    movie = load_fixture("radarr", "movie")
    queue = load_fixture("radarr", "queue")
    calendar = load_fixture("radarr", "calendar")
    diskspace = load_fixture("radarr", "diskspace")
    rootfolder = load_fixture("radarr", "rootfolder")
    qualityprofile = load_fixture("radarr", "qualityprofile")
    tag = load_fixture("radarr", "tag")
    command = load_fixture("radarr", "command")
    return {
        "/api/v3/system/status": {"status": 200, "json": system_status},
        "/api/v3/health": {"status": 200, "json": health},
        "/api/v3/movie": {"status": 200, "json": movie},
        "/api/v3/queue": {"status": 200, "json": queue},
        "/api/v3/calendar": {"status": 200, "json": calendar},
        "/api/v3/diskspace": {"status": 200, "json": diskspace},
        "/api/v3/rootfolder": {"status": 200, "json": rootfolder},
        "/api/v3/qualityprofile": {"status": 200, "json": qualityprofile},
        "/api/v3/tag": {"status": 200, "json": tag},
        "/api/v3/command": {"status": 201, "json": command},
        "/api/v3/movie/1": {"status": 200, "json": {}},
    }


# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------

class TestMeta:
    def test_type_id(self):
        meta = RadarrProvider.meta()
        assert meta.type_id == "radarr"

    def test_category(self):
        meta = RadarrProvider.meta()
        assert meta.category == "media"

    def test_permissions_count(self):
        meta = RadarrProvider.meta()
        assert len(meta.permissions) == 5

    def test_config_schema_fields(self):
        meta = RadarrProvider.meta()
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
        assert result.status == HealthStatus.UP
        assert "5.8.3.8933" in result.message

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
        status = load_fixture("radarr", "system_status")
        status["appName"] = "Sonarr"
        responses["/api/v3/system/status"] = {"status": 200, "json": status}
        provider = _make_provider(responses)
        result = await provider.health_check()
        assert result.status == HealthStatus.DOWN
        assert "Expected Radarr" in result.message

    @pytest.mark.asyncio
    async def test_connection_refused(self):
        provider = RadarrProvider(
            instance_id=1,
            display_name="Test",
            config={"url": "http://badhost:7878", "api_key": "key"},
        )
        transport = _ErrorTransport(httpx.ConnectError("refused"))
        provider.http_client = httpx.AsyncClient(
            transport=transport, base_url="http://badhost:7878"
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

        assert result.data["movie_count"] == 4
        assert result.data["movie_monitored"] == 3
        assert result.data["movie_downloaded"] == 2
        assert result.data["missing_count"] == 1  # monitored + no file

    @pytest.mark.asyncio
    async def test_queue_in_summary(self):
        provider = _make_provider()
        result = await provider.get_summary()

        queue = result.data["queue"]
        assert queue["total"] == 1
        assert queue["downloading"] == 1
        assert len(queue["records"]) == 1

    @pytest.mark.asyncio
    async def test_queue_item_normalization(self):
        provider = _make_provider()
        result = await provider.get_summary()

        item = result.data["queue"]["records"][0]
        assert item["media_title"] == "Upcoming Movie"
        assert item["detail_title"] == "(2025)"
        assert item["quality"] == "Bluray-1080p"
        assert item["progress"] == 75.0

    @pytest.mark.asyncio
    async def test_size_on_disk(self):
        provider = _make_provider()
        result = await provider.get_summary()
        # 15032385536 + 42949672960 + 0 + 0
        expected = 15032385536 + 42949672960
        assert result.data["size_on_disk"] == expected

    @pytest.mark.asyncio
    async def test_calendar_upcoming(self):
        provider = _make_provider()
        result = await provider.get_summary()

        cal = result.data["calendar_upcoming"]
        assert len(cal) == 1
        assert cal[0]["title"] == "Future Film"
        assert cal[0]["year"] == 2025

    @pytest.mark.asyncio
    async def test_health_warnings(self):
        provider = _make_provider()
        result = await provider.get_summary()
        assert result.data["health_warnings"] == []

    @pytest.mark.asyncio
    async def test_empty_on_error(self):
        responses = _default_responses()
        responses["/api/v3/movie"] = {"status": 500, "json": {}}
        provider = _make_provider(responses)
        result = await provider.get_summary()
        assert result.data["movie_count"] == 0


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------

class TestDetail:
    @pytest.mark.asyncio
    async def test_detail_shape(self):
        provider = _make_provider()
        result = await provider.get_detail()

        assert "movies" in result.data
        assert "queue" in result.data
        assert "calendar" in result.data
        assert "health" in result.data
        assert "disk_space" in result.data
        assert "root_folders" in result.data
        assert "quality_profiles" in result.data
        assert "tags" in result.data

    @pytest.mark.asyncio
    async def test_movie_data(self):
        provider = _make_provider()
        result = await provider.get_detail()

        movies = result.data["movies"]
        assert len(movies) == 4
        assert movies[0]["title"] == "The Matrix"
        assert movies[0]["year"] == 1999
        assert movies[0]["has_file"] is True

    @pytest.mark.asyncio
    async def test_disk_space(self):
        provider = _make_provider()
        result = await provider.get_detail()

        disks = result.data["disk_space"]
        assert len(disks) == 1
        assert disks[0]["path"] == "/00_media"

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
        assert tags[0]["label"] == "oscar"

    @pytest.mark.asyncio
    async def test_calendar(self):
        provider = _make_provider()
        result = await provider.get_detail()

        cal = result.data["calendar"]
        assert len(cal) == 1
        assert cal[0]["title"] == "Future Film"


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

class TestActions:
    def test_action_definitions(self):
        provider = _make_provider()
        actions = provider.get_actions()
        keys = [a.key for a in actions]
        assert "search_movie" in keys
        assert "search_missing" in keys
        assert "refresh_movie" in keys
        assert "delete_movie" in keys
        assert "remove_from_queue" in keys

    @pytest.mark.asyncio
    async def test_search_movie(self):
        provider = _make_provider()
        result = await provider.execute_action("search_movie", {"movie_ids": "1,2"})
        assert result.success is True
        assert "MoviesSearch" in result.message

    @pytest.mark.asyncio
    async def test_search_movie_no_ids(self):
        provider = _make_provider()
        result = await provider.execute_action("search_movie", {})
        assert result.success is False

    @pytest.mark.asyncio
    async def test_search_missing(self):
        provider = _make_provider()
        result = await provider.execute_action("search_missing", {})
        assert result.success is True
        assert "MissingMoviesSearch" in result.message

    @pytest.mark.asyncio
    async def test_refresh_movie(self):
        provider = _make_provider()
        result = await provider.execute_action("refresh_movie", {"movie_id": "1"})
        assert result.success is True

    @pytest.mark.asyncio
    async def test_delete_movie(self):
        responses = _default_responses()
        responses["/api/v3/movie/1"] = {"status": 200, "json": {}}
        provider = _make_provider(responses)
        result = await provider.execute_action(
            "delete_movie", {"movie_id": "1", "delete_files": "false"}
        )
        assert result.success is True
        assert "deleted" in result.message.lower()

    @pytest.mark.asyncio
    async def test_delete_movie_with_files(self):
        responses = _default_responses()
        responses["/api/v3/movie/1"] = {"status": 200, "json": {}}
        provider = _make_provider(responses)
        result = await provider.execute_action(
            "delete_movie", {"movie_id": "1", "delete_files": "true"}
        )
        assert result.success is True
        assert "files removed" in result.message.lower()

    @pytest.mark.asyncio
    async def test_remove_from_queue(self):
        responses = _default_responses()
        responses["/api/v3/queue/201"] = {"status": 200, "json": {}}
        provider = _make_provider(responses)
        result = await provider.execute_action(
            "remove_from_queue", {"queue_id": "201"}
        )
        assert result.success is True
        assert "removed" in result.message.lower()

    @pytest.mark.asyncio
    async def test_grab_queue_item(self):
        responses = _default_responses()
        responses["/api/v3/queue/grab/201"] = {"status": 200, "json": {}}
        provider = _make_provider(responses)
        result = await provider.execute_action(
            "grab_queue_item", {"queue_id": "201"}
        )
        assert result.success is True
        assert "grabbed" in result.message.lower()

    @pytest.mark.asyncio
    async def test_grab_queue_item_no_id(self):
        provider = _make_provider()
        result = await provider.execute_action("grab_queue_item", {})
        assert result.success is False

    @pytest.mark.asyncio
    async def test_unknown_action(self):
        provider = _make_provider()
        result = await provider.execute_action("nonexistent", {})
        assert result.success is False


# ---------------------------------------------------------------------------
# Manual Import
# ---------------------------------------------------------------------------

class TestManualImport:
    @pytest.mark.asyncio
    async def test_manual_import_preview(self):
        preview_data = load_fixture("radarr", "manualimport_preview")
        responses = _default_responses()
        responses["/api/v3/manualimport"] = {"status": 200, "json": preview_data}
        provider = _make_provider(responses)
        result = await provider._fetch_manual_import_preview("movie123abc456")
        assert len(result) == 2
        assert result[0]["movie"]["title"] == "Upcoming Movie"

    @pytest.mark.asyncio
    async def test_manual_import_preview_empty(self):
        responses = _default_responses()
        responses["/api/v3/manualimport"] = {"status": 200, "json": []}
        provider = _make_provider(responses)
        result = await provider._fetch_manual_import_preview("nonexistent")
        assert result == []

    @pytest.mark.asyncio
    async def test_manual_import_preview_error(self):
        responses = _default_responses()
        responses["/api/v3/manualimport"] = {"status": 500, "json": {}}
        provider = _make_provider(responses)
        result = await provider._fetch_manual_import_preview("abc")
        assert result == []

    def test_normalize_manual_import_file(self):
        preview_data = load_fixture("radarr", "manualimport_preview")
        provider = _make_provider()
        normalized = provider._normalize_manual_import_file(preview_data[0])
        assert normalized["movie_title"] == "Upcoming Movie"
        assert normalized["movie_id"] == 3
        assert normalized["movie_year"] == 2025
        assert normalized["quality_name"] == "Bluray-1080p"
        assert normalized["language_names"] == ["English"]
        assert normalized["has_rejections"] is False

    def test_normalize_manual_import_file_with_rejections(self):
        preview_data = load_fixture("radarr", "manualimport_preview")
        provider = _make_provider()
        normalized = provider._normalize_manual_import_file(preview_data[1])
        assert normalized["has_rejections"] is True
        assert len(normalized["rejections"]) == 1

    @pytest.mark.asyncio
    async def test_manual_import_execute(self):
        responses = _default_responses()
        provider = _make_provider(responses)
        files = [{
            "path": "/downloads/movies/movie.mkv",
            "movieId": 3,
            "quality": {"quality": {"id": 7, "name": "Bluray-1080p"}, "revision": {"version": 1}},
            "languages": [{"id": 1, "name": "English"}],
            "releaseGroup": "x265",
            "downloadId": "movie123abc456",
        }]
        result = await provider._execute_manual_import(files)
        assert result.success is True
        assert "ManualImport" in result.message

    @pytest.mark.asyncio
    async def test_manual_import_action_no_files(self):
        provider = _make_provider()
        result = await provider.execute_action("manual_import", {"file_count": "0"})
        assert result.success is False

    def test_download_id_in_queue_record(self):
        """Verify download_id is included in normalized queue records."""
        provider = _make_provider()
        record = {
            "id": 201,
            "movie": {"id": 3, "title": "Test Movie", "year": 2025},
            "quality": {"quality": {"id": 7, "name": "Bluray-1080p"}},
            "customFormats": [],
            "size": 1000, "sizeleft": 500,
            "status": "downloading",
            "trackedDownloadStatus": "ok",
            "trackedDownloadState": "importing",
            "statusMessages": [],
            "downloadClient": "qBit",
            "indexer": "Prowlarr",
            "outputPath": "/downloads/",
            "downloadId": "movie123",
        }
        result = provider._normalize_queue_record(record)
        assert result["download_id"] == "movie123"


# ---------------------------------------------------------------------------
# Validate Config (inherited from ArrBaseProvider)
# ---------------------------------------------------------------------------

class TestValidateConfig:
    @pytest.mark.asyncio
    async def test_valid(self):
        provider = _make_provider()
        ok, msg = await provider.validate_config()
        assert ok is True
        assert "Radarr" in msg
        assert "5.8.3.8933" in msg

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
        status = load_fixture("radarr", "system_status")
        status["appName"] = "Sonarr"
        responses["/api/v3/system/status"] = {"status": 200, "json": status}
        provider = _make_provider(responses)
        ok, msg = await provider.validate_config()
        assert ok is False
        assert "Sonarr" in msg
        assert "Radarr" in msg


# ---------------------------------------------------------------------------
# Test utilities
# ---------------------------------------------------------------------------

class _ErrorTransport(httpx.AsyncBaseTransport):
    """Transport that raises a given exception on every request."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise self._error
