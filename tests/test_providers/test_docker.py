"""Tests for the Docker provider."""

import pytest

from tests.conftest import MockTransport, load_fixture
from src.providers.docker import DockerProvider, STATE_DISPLAY
from src.providers.base import HealthStatus

import httpx


# Container IDs from fixtures
SONARR_ID = "abc123def456"
RADARR_ID = "def456abc789"
OLD_ID = "bad000dead00"
UNHEALTHY_ID = "sick111bad00"


def _make_provider(responses: dict | None = None, **config_overrides) -> DockerProvider:
    """Create a DockerProvider with a mock HTTP client."""
    config = {
        "socket_path": "/var/run/docker.sock",
        "show_all": True,
        **config_overrides,
    }
    provider = DockerProvider(
        instance_id=1,
        display_name="Test Docker",
        config=config,
    )
    if responses is None:
        responses = _default_responses()
    provider.http_client = httpx.AsyncClient(
        transport=DockerMockTransport(responses),
        base_url="http://docker",
    )
    # Disable self-detection (not running in a container during tests)
    provider._self_container_id = ""
    return provider


def _default_responses() -> dict:
    """Standard mock responses for a healthy Docker instance."""
    version = load_fixture("docker", "version")
    containers = load_fixture("docker", "containers")
    info = load_fixture("docker", "info")
    return {
        "/version": {"status": 200, "json": version},
        "/containers/json": {"status": 200, "json": containers},
        "/info": {"status": 200, "json": info},
        # Action endpoints (204 = success, no body)
        f"/containers/{SONARR_ID}/restart": {"status": 204, "json": {}},
        f"/containers/{SONARR_ID}/stop": {"status": 204, "json": {}},
        f"/containers/{OLD_ID}/start": {"status": 204, "json": {}},
        f"/containers/{OLD_ID}/stop": {"status": 304, "json": {}},
        f"/containers/{SONARR_ID}/start": {"status": 304, "json": {}},
    }


class DockerMockTransport(httpx.AsyncBaseTransport):
    """Mock transport for Docker API — matches paths ignoring query params."""

    def __init__(self, responses: dict[str, dict]) -> None:
        self.responses = responses

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.raw_path.decode("utf-8").split("?")[0]
        if path in self.responses:
            resp_data = self.responses[path]
            status = resp_data.get("status", 200)
            if status == 204:
                return httpx.Response(status_code=204)
            if status == 304:
                return httpx.Response(status_code=304)
            return httpx.Response(
                status_code=status,
                json=resp_data.get("json", {}),
            )
        return httpx.Response(status_code=404)


# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------

class TestMeta:
    def test_type_id(self):
        meta = DockerProvider.meta()
        assert meta.type_id == "docker"

    def test_category(self):
        meta = DockerProvider.meta()
        assert meta.category == "infrastructure"

    def test_permissions_count(self):
        meta = DockerProvider.meta()
        assert len(meta.permissions) == 3

    def test_config_schema_fields(self):
        meta = DockerProvider.meta()
        keys = [f["key"] for f in meta.config_schema["fields"]]
        assert "socket_path" in keys
        assert "show_all" in keys


# ---------------------------------------------------------------------------
# Container Normalization
# ---------------------------------------------------------------------------

class TestNormalization:
    def test_normalize_basic(self):
        raw = load_fixture("docker", "containers")[0]  # sonarr
        result = DockerProvider._normalize_container(raw)

        assert result["id"] == SONARR_ID
        assert result["name"] == "sonarr"
        assert result["image"] == "lscr.io/linuxserver/sonarr:latest"
        assert result["state"] == "running"
        assert result["state_display"] == "Running"

    def test_normalize_health_healthy(self):
        raw = load_fixture("docker", "containers")[0]  # sonarr: Up 5 days (healthy)
        result = DockerProvider._normalize_container(raw)
        assert result["health"] == "healthy"

    def test_normalize_health_unhealthy(self):
        raw = load_fixture("docker", "containers")[3]  # unhealthy-app
        result = DockerProvider._normalize_container(raw)
        assert result["health"] == "unhealthy"

    def test_normalize_no_health(self):
        raw = load_fixture("docker", "containers")[1]  # radarr: Up 5 days (no health check)
        result = DockerProvider._normalize_container(raw)
        assert result["health"] == ""

    def test_normalize_exited(self):
        raw = load_fixture("docker", "containers")[2]  # old-container: exited
        result = DockerProvider._normalize_container(raw)
        assert result["state"] == "exited"
        assert result["state_display"] == "Stopped"

    def test_normalize_ports(self):
        raw = load_fixture("docker", "containers")[0]  # sonarr
        result = DockerProvider._normalize_container(raw)
        assert len(result["ports"]) == 1
        assert result["ports"][0]["private"] == 8989
        assert result["ports"][0]["public"] == 8989

    def test_normalize_mounts_count(self):
        raw = load_fixture("docker", "containers")[0]  # sonarr has 3 mounts
        result = DockerProvider._normalize_container(raw)
        assert result["mounts"] == 3

    def test_no_env_in_output(self):
        """H2: Env vars must never appear in normalized output."""
        raw = load_fixture("docker", "containers")[0]
        result = DockerProvider._normalize_container(raw)
        assert "Env" not in result
        assert "env" not in result

    def test_name_strips_leading_slash(self):
        raw = {"Names": ["/my-container"], "State": "running", "Status": "Up"}
        result = DockerProvider._normalize_container(raw)
        assert result["name"] == "my-container"

    def test_id_truncated(self):
        raw = load_fixture("docker", "containers")[0]
        result = DockerProvider._normalize_container(raw)
        assert len(result["id"]) == 12


# ---------------------------------------------------------------------------
# State Display
# ---------------------------------------------------------------------------

class TestStateDisplay:
    def test_all_states_mapped(self):
        assert STATE_DISPLAY["running"] == "Running"
        assert STATE_DISPLAY["exited"] == "Stopped"
        assert STATE_DISPLAY["dead"] == "Dead"
        assert STATE_DISPLAY["paused"] == "Paused"
        assert STATE_DISPLAY["restarting"] == "Restarting"
        assert STATE_DISPLAY["created"] == "Created"


# ---------------------------------------------------------------------------
# Health Check
# ---------------------------------------------------------------------------

class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_healthy(self):
        provider = _make_provider()
        result = await provider.health_check()
        assert result.status == HealthStatus.UP
        assert "24.0.7" in result.message

    @pytest.mark.asyncio
    async def test_bad_status(self):
        responses = _default_responses()
        responses["/version"] = {"status": 500, "json": {}}
        provider = _make_provider(responses)
        result = await provider.health_check()
        assert result.status == HealthStatus.DOWN
        assert "500" in result.message

    @pytest.mark.asyncio
    async def test_connection_error(self):
        provider = DockerProvider(
            instance_id=1, display_name="Test",
            config={"socket_path": "/var/run/docker.sock"},
        )
        transport = _ErrorTransport(httpx.ConnectError("unavailable"))
        provider.http_client = httpx.AsyncClient(transport=transport, base_url="http://docker")
        result = await provider.health_check()
        assert result.status == HealthStatus.DOWN
        assert "not available" in result.message.lower()

    @pytest.mark.asyncio
    async def test_timeout(self):
        provider = DockerProvider(
            instance_id=1, display_name="Test",
            config={"socket_path": "/var/run/docker.sock"},
        )
        transport = _ErrorTransport(httpx.TimeoutException("timeout"))
        provider.http_client = httpx.AsyncClient(transport=transport, base_url="http://docker")
        result = await provider.health_check()
        assert result.status == HealthStatus.DOWN
        assert "not responding" in result.message.lower()


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

class TestSummary:
    @pytest.mark.asyncio
    async def test_summary_shape(self):
        provider = _make_provider()
        result = await provider.get_summary()

        assert result.data["docker_version"] == "24.0.7"
        assert "containers" in result.data
        assert "container_list" in result.data
        assert "system" in result.data

    @pytest.mark.asyncio
    async def test_container_counts(self):
        provider = _make_provider()
        result = await provider.get_summary()

        assert result.data["containers"]["total"] == 4
        assert result.data["containers"]["running"] == 3
        assert result.data["containers"]["stopped"] == 1
        assert result.data["containers"]["unhealthy"] == 1

    @pytest.mark.asyncio
    async def test_container_list(self):
        provider = _make_provider()
        result = await provider.get_summary()

        names = [c["name"] for c in result.data["container_list"]]
        assert "sonarr" in names
        assert "radarr" in names
        assert "old-container" in names
        assert "unhealthy-app" in names

    @pytest.mark.asyncio
    async def test_system_info(self):
        provider = _make_provider()
        result = await provider.get_summary()

        sys = result.data["system"]
        assert sys["images"] == 30
        assert sys["cpus"] == 16
        assert sys["os"] == "Unraid 6.12.6"
        assert sys["kernel"] == "5.15.0-unraid"

    @pytest.mark.asyncio
    async def test_empty_on_error(self):
        provider = DockerProvider(
            instance_id=1, display_name="Test",
            config={"socket_path": "/var/run/docker.sock"},
        )
        transport = _ErrorTransport(httpx.ConnectError("fail"))
        provider.http_client = httpx.AsyncClient(transport=transport, base_url="http://docker")
        result = await provider.get_summary()
        assert result.data["docker_version"] == "unknown"
        assert result.data["containers"]["total"] == 0


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------

class TestDetail:
    @pytest.mark.asyncio
    async def test_detail_shape(self):
        provider = _make_provider()
        result = await provider.get_detail()

        assert "containers" in result.data
        assert "system" in result.data
        assert "version" in result.data

    @pytest.mark.asyncio
    async def test_containers_list(self):
        provider = _make_provider()
        result = await provider.get_detail()

        assert len(result.data["containers"]) == 4
        names = [c["name"] for c in result.data["containers"]]
        assert "sonarr" in names

    @pytest.mark.asyncio
    async def test_system_detail(self):
        provider = _make_provider()
        result = await provider.get_detail()

        sys = result.data["system"]
        assert sys["storage_driver"] == "overlay2"
        assert sys["containers_total"] == 4
        assert sys["containers_running"] == 3

    @pytest.mark.asyncio
    async def test_version_detail(self):
        provider = _make_provider()
        result = await provider.get_detail()

        ver = result.data["version"]
        assert ver["version"] == "24.0.7"
        assert ver["api_version"] == "1.43"
        assert ver["os"] == "linux"
        assert ver["arch"] == "amd64"

    @pytest.mark.asyncio
    async def test_empty_on_error(self):
        provider = DockerProvider(
            instance_id=1, display_name="Test",
            config={"socket_path": "/var/run/docker.sock"},
        )
        transport = _ErrorTransport(Exception("fail"))
        provider.http_client = httpx.AsyncClient(transport=transport, base_url="http://docker")
        result = await provider.get_detail()
        assert result.data["containers"] == []


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

class TestActions:
    def test_action_definitions(self):
        provider = _make_provider()
        actions = provider.get_actions()
        keys = [a.key for a in actions]
        assert "restart_container" in keys
        assert "stop_container" in keys
        assert "start_container" in keys

    @pytest.mark.asyncio
    async def test_restart_container(self):
        provider = _make_provider()
        result = await provider.execute_action("restart_container", {"container_id": SONARR_ID})
        assert result.success is True
        assert "restarted" in result.message.lower()

    @pytest.mark.asyncio
    async def test_stop_container(self):
        provider = _make_provider()
        result = await provider.execute_action("stop_container", {"container_id": SONARR_ID})
        assert result.success is True
        assert "stopped" in result.message.lower()

    @pytest.mark.asyncio
    async def test_start_container(self):
        provider = _make_provider()
        result = await provider.execute_action("start_container", {"container_id": OLD_ID})
        assert result.success is True
        assert "started" in result.message.lower()

    @pytest.mark.asyncio
    async def test_stop_already_stopped(self):
        provider = _make_provider()
        result = await provider.execute_action("stop_container", {"container_id": OLD_ID})
        assert result.success is True
        assert "already stopped" in result.message.lower()

    @pytest.mark.asyncio
    async def test_start_already_running(self):
        provider = _make_provider()
        result = await provider.execute_action("start_container", {"container_id": SONARR_ID})
        assert result.success is True
        assert "already running" in result.message.lower()

    @pytest.mark.asyncio
    async def test_no_container_id(self):
        provider = _make_provider()
        result = await provider.execute_action("restart_container", {})
        assert result.success is False
        assert "No container ID" in result.message

    @pytest.mark.asyncio
    async def test_self_protection(self):
        provider = _make_provider()
        provider._self_container_id = SONARR_ID
        result = await provider.execute_action("restart_container", {"container_id": SONARR_ID})
        assert result.success is False
        assert "Great Eye" in result.message

    @pytest.mark.asyncio
    async def test_unknown_action(self):
        provider = _make_provider()
        result = await provider.execute_action("nonexistent", {})
        assert result.success is False


# ---------------------------------------------------------------------------
# Self-Protection
# ---------------------------------------------------------------------------

class TestSelfProtection:
    def test_is_self_true(self):
        provider = _make_provider()
        provider._self_container_id = "abc123def456"
        assert provider._is_self("abc123def456789") is True

    def test_is_self_false(self):
        provider = _make_provider()
        provider._self_container_id = "abc123def456"
        assert provider._is_self("zzz999aaa888") is False

    def test_is_self_empty(self):
        provider = _make_provider()
        provider._self_container_id = ""
        assert provider._is_self("abc123def456") is False


# ---------------------------------------------------------------------------
# Validate Config
# ---------------------------------------------------------------------------

class TestValidateConfig:
    @pytest.mark.asyncio
    async def test_valid(self):
        provider = _make_provider()
        ok, msg = await provider.validate_config()
        assert ok is True
        assert "24.0.7" in msg

    @pytest.mark.asyncio
    async def test_bad_status(self):
        responses = _default_responses()
        responses["/version"] = {"status": 500, "json": {}}
        provider = _make_provider(responses)
        ok, msg = await provider.validate_config()
        assert ok is False
        assert "500" in msg

    @pytest.mark.asyncio
    async def test_connection_error(self):
        provider = DockerProvider(
            instance_id=1, display_name="Test",
            config={"socket_path": "/var/run/docker.sock"},
        )
        transport = _ErrorTransport(httpx.ConnectError("unavailable"))
        provider.http_client = httpx.AsyncClient(transport=transport, base_url="http://docker")
        ok, msg = await provider.validate_config()
        assert ok is False
        assert "not available" in msg.lower()


# ---------------------------------------------------------------------------
# Test utilities
# ---------------------------------------------------------------------------

class _ErrorTransport(httpx.AsyncBaseTransport):
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise self._error
