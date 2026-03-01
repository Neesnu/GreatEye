"""Tests for the qBittorrent provider."""

import pytest

from tests.conftest import MockTransport, load_fixture
from src.providers.qbittorrent import (
    QBittorrentProvider,
    STATE_MAP,
    _extract_tracker,
)
from src.providers.base import HealthStatus

import httpx


def _make_provider(responses: dict | None = None, **config_overrides) -> QBittorrentProvider:
    """Create a QBittorrentProvider with a mock HTTP client."""
    config = {
        "url": "http://10.0.0.45:8080",
        "username": "admin",
        "password": "adminadmin",
        "recent_limit": 30,
        **config_overrides,
    }
    provider = QBittorrentProvider(
        instance_id=1,
        display_name="Test qBit",
        config=config,
    )
    if responses is None:
        responses = _default_responses()
    provider.http_client = httpx.AsyncClient(
        transport=MockTransport(responses),
        base_url="http://10.0.0.45:8080",
    )
    return provider


def _default_responses() -> dict:
    """Standard mock responses for a healthy qBittorrent instance."""
    transfer = load_fixture("qbittorrent", "transfer_info")
    torrents = load_fixture("qbittorrent", "torrents_info")
    maindata = load_fixture("qbittorrent", "maindata")
    return {
        "/api/v2/auth/login": {"status": 200, "text": "Ok."},
        "/api/v2/app/version": {"status": 200, "text": "v4.6.5"},
        "/api/v2/transfer/info": {"status": 200, "json": transfer},
        "/api/v2/torrents/info": {"status": 200, "json": torrents},
        "/api/v2/sync/maindata": {"status": 200, "json": maindata},
        "/api/v2/torrents/pause": {"status": 200, "text": ""},
        "/api/v2/torrents/resume": {"status": 200, "text": ""},
        "/api/v2/torrents/delete": {"status": 200, "text": ""},
        "/api/v2/transfer/toggleSpeedLimitsMode": {"status": 200, "text": ""},
        "/api/v2/transfer/setDownloadLimit": {"status": 200, "text": ""},
        "/api/v2/transfer/setUploadLimit": {"status": 200, "text": ""},
    }


# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------

class TestMeta:
    def test_type_id(self):
        meta = QBittorrentProvider.meta()
        assert meta.type_id == "qbittorrent"

    def test_category(self):
        meta = QBittorrentProvider.meta()
        assert meta.category == "download_client"

    def test_permissions_count(self):
        meta = QBittorrentProvider.meta()
        assert len(meta.permissions) == 4

    def test_config_schema_has_url(self):
        meta = QBittorrentProvider.meta()
        keys = [f["key"] for f in meta.config_schema["fields"]]
        assert "url" in keys
        assert "username" in keys
        assert "password" in keys
        assert "recent_limit" in keys


# ---------------------------------------------------------------------------
# Health Check
# ---------------------------------------------------------------------------

class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_healthy(self):
        provider = _make_provider()
        provider._sid = "fakesid"  # Skip auth
        result = await provider.health_check()
        assert result.status == HealthStatus.UP
        assert "v4.6.5" in result.message

    @pytest.mark.asyncio
    async def test_stores_version(self):
        provider = _make_provider()
        provider._sid = "fakesid"
        await provider.health_check()
        assert provider._qbit_version == "v4.6.5"
        assert provider._is_v5 is False

    @pytest.mark.asyncio
    async def test_detects_v5(self):
        responses = _default_responses()
        responses["/api/v2/app/version"] = {"status": 200, "text": "v5.1.4"}
        provider = _make_provider(responses)
        provider._sid = "fakesid"
        await provider.health_check()
        assert provider._is_v5 is True

    @pytest.mark.asyncio
    async def test_auth_failure(self):
        responses = _default_responses()
        responses["/api/v2/app/version"] = {"status": 403, "text": "Forbidden"}
        responses["/api/v2/auth/login"] = {"status": 200, "text": "Fails."}
        provider = _make_provider(responses)
        result = await provider.health_check()
        assert result.status == HealthStatus.DOWN
        assert "Authentication" in result.message

    @pytest.mark.asyncio
    async def test_connection_refused(self):
        provider = QBittorrentProvider(
            instance_id=1,
            display_name="Test",
            config={"url": "http://10.0.0.45:8080", "username": "", "password": ""},
        )
        # Use a transport that raises ConnectError
        transport = _ErrorTransport(httpx.ConnectError("refused"))
        provider.http_client = httpx.AsyncClient(
            transport=transport, base_url="http://10.0.0.45:8080"
        )
        result = await provider.health_check()
        assert result.status == HealthStatus.DOWN
        assert "refused" in result.message.lower()

    @pytest.mark.asyncio
    async def test_no_auth_needed(self):
        """Provider with no credentials should skip auth."""
        provider = _make_provider(username="", password="")
        result = await provider.health_check()
        assert result.status == HealthStatus.UP


# ---------------------------------------------------------------------------
# Version Detection
# ---------------------------------------------------------------------------

class TestVersionDetection:
    def test_v4(self):
        assert QBittorrentProvider._detect_v5("v4.6.5") is False

    def test_v5(self):
        assert QBittorrentProvider._detect_v5("v5.1.4") is True

    def test_v3(self):
        assert QBittorrentProvider._detect_v5("v3.3.16") is False

    def test_malformed(self):
        assert QBittorrentProvider._detect_v5("unknown") is False

    def test_no_v_prefix(self):
        assert QBittorrentProvider._detect_v5("5.0.0") is True


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

class TestSummary:
    @pytest.mark.asyncio
    async def test_summary_shape(self):
        provider = _make_provider()
        provider._sid = "fakesid"
        result = await provider.get_summary()

        assert result.data["global_download_speed"] == 15728640
        assert result.data["global_upload_speed"] == 5242880
        assert "MB/s" in result.data["global_download_speed_formatted"]
        assert result.data["active_downloads"] == 1
        assert result.data["active_uploads"] == 1
        assert result.data["paused"] == 1
        assert result.data["errored"] == 1
        assert result.data["free_disk_space"] == 1099511627776
        assert result.data["alt_speed_enabled"] is False
        assert result.data["connection_status"] == "connected"

    @pytest.mark.asyncio
    async def test_recent_torrents(self):
        provider = _make_provider()
        provider._sid = "fakesid"
        result = await provider.get_summary()

        recent = result.data["recent_torrents"]
        assert len(recent) == 4  # fixture has 4 torrents
        assert recent[0]["name"] == "Ubuntu.22.04.3.LTS.Desktop.amd64"
        assert recent[0]["state_display"] == "Downloading"
        assert recent[0]["progress"] == 0.45

    @pytest.mark.asyncio
    async def test_empty_on_error(self):
        responses = _default_responses()
        responses["/api/v2/transfer/info"] = {"status": 500, "json": {}}
        provider = _make_provider(responses)
        provider._sid = "fakesid"
        result = await provider.get_summary()
        assert result.data["global_download_speed"] is None
        assert result.data["recent_torrents"] == []

    @pytest.mark.asyncio
    async def test_infinity_eta(self):
        """ETA of 8640000 should be treated as infinity."""
        provider = _make_provider()
        provider._sid = "fakesid"
        result = await provider.get_summary()
        # The uploading torrent has eta=8640000
        uploading = result.data["recent_torrents"][1]
        assert uploading["eta"] == -1
        assert uploading["eta_formatted"] == "∞"


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------

class TestDetail:
    @pytest.mark.asyncio
    async def test_detail_shape(self):
        provider = _make_provider()
        provider._sid = "fakesid"
        result = await provider.get_detail()

        assert "transfer" in result.data
        assert "torrents" in result.data
        assert "categories" in result.data
        assert "tags" in result.data

    @pytest.mark.asyncio
    async def test_transfer_data(self):
        provider = _make_provider()
        provider._sid = "fakesid"
        result = await provider.get_detail()

        t = result.data["transfer"]
        assert t["download_speed"] == 15728640
        assert t["dht_nodes"] == 450
        assert t["connection_status"] == "connected"
        assert t["alt_speed_enabled"] is False

    @pytest.mark.asyncio
    async def test_torrent_list(self):
        provider = _make_provider()
        provider._sid = "fakesid"
        result = await provider.get_detail()

        torrents = result.data["torrents"]
        assert len(torrents) == 4
        assert torrents[0]["hash"] == "abc123def456abc123def456abc123def456abc1"
        assert torrents[0]["state_display"] == "Downloading"
        assert "size_formatted" in torrents[0]
        assert "tracker" in torrents[0]

    @pytest.mark.asyncio
    async def test_categories_and_tags(self):
        provider = _make_provider()
        provider._sid = "fakesid"
        result = await provider.get_detail()

        assert "linux" in result.data["categories"]
        assert "iso" in result.data["tags"]


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

class TestActions:
    def test_action_definitions(self):
        provider = _make_provider()
        actions = provider.get_actions()
        keys = [a.key for a in actions]
        assert "pause" in keys
        assert "resume" in keys
        assert "delete" in keys
        assert "toggle_alt_speed" in keys
        assert "set_download_limit" in keys
        assert "set_upload_limit" in keys

    @pytest.mark.asyncio
    async def test_pause(self):
        provider = _make_provider()
        provider._sid = "fakesid"
        result = await provider.execute_action("pause", {"hashes": "abc123"})
        assert result.success is True

    @pytest.mark.asyncio
    async def test_resume(self):
        provider = _make_provider()
        provider._sid = "fakesid"
        result = await provider.execute_action("resume", {"hashes": "abc123"})
        assert result.success is True

    @pytest.mark.asyncio
    async def test_delete(self):
        provider = _make_provider()
        provider._sid = "fakesid"
        result = await provider.execute_action(
            "delete", {"hashes": "abc123", "delete_files": "false"}
        )
        assert result.success is True
        assert "deleted" in result.message.lower()

    @pytest.mark.asyncio
    async def test_delete_with_files(self):
        provider = _make_provider()
        provider._sid = "fakesid"
        result = await provider.execute_action(
            "delete", {"hashes": "abc123", "delete_files": "true"}
        )
        assert result.success is True
        assert "files removed" in result.message.lower()

    @pytest.mark.asyncio
    async def test_toggle_alt_speed(self):
        provider = _make_provider()
        provider._sid = "fakesid"
        result = await provider.execute_action("toggle_alt_speed", {})
        assert result.success is True

    @pytest.mark.asyncio
    async def test_set_download_limit(self):
        provider = _make_provider()
        provider._sid = "fakesid"
        result = await provider.execute_action(
            "set_download_limit", {"limit": 10485760}
        )
        assert result.success is True
        assert "download" in result.message.lower()

    @pytest.mark.asyncio
    async def test_set_upload_limit(self):
        provider = _make_provider()
        provider._sid = "fakesid"
        result = await provider.execute_action(
            "set_upload_limit", {"limit": 5242880}
        )
        assert result.success is True
        assert "upload" in result.message.lower()

    @pytest.mark.asyncio
    async def test_unknown_action(self):
        provider = _make_provider()
        result = await provider.execute_action("nonexistent", {})
        assert result.success is False

    @pytest.mark.asyncio
    async def test_pause_no_hashes(self):
        provider = _make_provider()
        provider._sid = "fakesid"
        result = await provider.execute_action("pause", {})
        assert result.success is False

    @pytest.mark.asyncio
    async def test_v5_uses_stop_start(self):
        """v5.x should use /torrents/stop instead of /torrents/pause."""
        responses = _default_responses()
        responses["/api/v2/torrents/stop"] = {"status": 200, "text": ""}
        responses["/api/v2/torrents/start"] = {"status": 200, "text": ""}
        provider = _make_provider(responses)
        provider._sid = "fakesid"
        provider._is_v5 = True
        result = await provider.execute_action("pause", {"hashes": "abc"})
        assert result.success is True
        result = await provider.execute_action("resume", {"hashes": "abc"})
        assert result.success is True


# ---------------------------------------------------------------------------
# Validate Config
# ---------------------------------------------------------------------------

class TestValidateConfig:
    @pytest.mark.asyncio
    async def test_valid(self):
        provider = _make_provider()
        ok, msg = await provider.validate_config()
        assert ok is True
        assert "v4.6.5" in msg

    @pytest.mark.asyncio
    async def test_auth_fail(self):
        responses = _default_responses()
        responses["/api/v2/auth/login"] = {"status": 200, "text": "Fails."}
        responses["/api/v2/app/version"] = {"status": 403, "text": "Forbidden"}
        provider = _make_provider(responses)
        ok, msg = await provider.validate_config()
        assert ok is False
        assert "Authentication" in msg

    @pytest.mark.asyncio
    async def test_connect_error(self):
        provider = QBittorrentProvider(
            instance_id=1,
            display_name="Test",
            config={"url": "http://badhost:8080", "username": "", "password": ""},
        )
        transport = _ErrorTransport(httpx.ConnectError("refused"))
        provider.http_client = httpx.AsyncClient(
            transport=transport, base_url="http://badhost:8080"
        )
        ok, msg = await provider.validate_config()
        assert ok is False
        assert "Cannot connect" in msg


# ---------------------------------------------------------------------------
# State Mapping
# ---------------------------------------------------------------------------

class TestStateMapping:
    def test_all_states_mapped(self):
        expected_states = [
            "downloading", "stalledDL", "uploading", "stalledUP",
            "pausedDL", "pausedUP", "stoppedDL", "stoppedUP",
            "queuedDL", "queuedUP", "checkingDL", "checkingUP",
            "forcedDL", "forcedUP", "error", "missingFiles",
            "moving", "unknown",
        ]
        for state in expected_states:
            assert state in STATE_MAP, f"Missing state: {state}"

    def test_v5_stopped_states(self):
        assert STATE_MAP["stoppedDL"] == "Paused"
        assert STATE_MAP["stoppedUP"] == "Paused"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class TestExtractTracker:
    def test_full_url(self):
        assert _extract_tracker("https://tracker.ubuntu.com:443/announce") == "tracker.ubuntu.com"

    def test_empty(self):
        assert _extract_tracker("") == ""

    def test_plain_hostname(self):
        result = _extract_tracker("http://example.com/announce")
        assert result == "example.com"


# ---------------------------------------------------------------------------
# Test utilities
# ---------------------------------------------------------------------------

class _ErrorTransport(httpx.AsyncBaseTransport):
    """Transport that raises a given exception on every request."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise self._error
