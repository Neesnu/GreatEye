"""Tests for the Pi-hole provider."""

import pytest

from tests.conftest import MockTransport, load_fixture
from src.providers.pihole import PiholeProvider, _is_blocking_enabled
from src.providers.base import HealthStatus

import httpx


def _make_provider(responses: dict | None = None, **config_overrides) -> PiholeProvider:
    """Create a PiholeProvider with a mock HTTP client."""
    config = {
        "url": "http://10.0.0.1",
        "password": "test-password",
        **config_overrides,
    }
    provider = PiholeProvider(
        instance_id=1,
        display_name="Test Pi-hole",
        config=config,
    )
    if responses is None:
        responses = _default_responses()
    provider.http_client = httpx.AsyncClient(
        transport=MockTransport(responses),
        base_url="http://10.0.0.1",
    )
    # Pre-set SID so we skip auth in most tests
    provider._sid = "test-sid"
    return provider


def _default_responses() -> dict:
    """Standard mock responses for a healthy Pi-hole instance."""
    auth = load_fixture("pihole", "auth")
    dns_blocking = load_fixture("pihole", "dns_blocking")
    stats_summary = load_fixture("pihole", "stats_summary")
    top_domains = load_fixture("pihole", "top_domains")
    top_blocked = load_fixture("pihole", "top_blocked")
    top_clients = load_fixture("pihole", "top_clients")
    upstreams = load_fixture("pihole", "upstreams")
    query_types = load_fixture("pihole", "query_types")
    version = load_fixture("pihole", "version")
    return {
        "/api/auth": {"status": 200, "json": auth},
        "/api/dns/blocking": {"status": 200, "json": dns_blocking},
        "/api/stats/summary": {"status": 200, "json": stats_summary},
        "/api/stats/top_domains": {"status": 200, "json": top_domains},
        "/api/stats/top_blocked": {"status": 200, "json": top_blocked},
        "/api/stats/top_clients": {"status": 200, "json": top_clients},
        "/api/stats/upstreams": {"status": 200, "json": upstreams},
        "/api/stats/query_types": {"status": 200, "json": query_types},
        "/api/info/version": {"status": 200, "json": version},
        "/api/action/gravity": {"status": 200, "json": {}},
    }


# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------

class TestMeta:
    def test_type_id(self):
        meta = PiholeProvider.meta()
        assert meta.type_id == "pihole"

    def test_category(self):
        meta = PiholeProvider.meta()
        assert meta.category == "network"

    def test_permissions_count(self):
        meta = PiholeProvider.meta()
        assert len(meta.permissions) == 3

    def test_config_schema_fields(self):
        meta = PiholeProvider.meta()
        keys = [f["key"] for f in meta.config_schema["fields"]]
        assert "url" in keys
        assert "password" in keys


# ---------------------------------------------------------------------------
# Blocking status normalization
# ---------------------------------------------------------------------------

class TestIsBlockingEnabled:
    def test_string_enabled(self):
        assert _is_blocking_enabled("enabled") is True

    def test_string_disabled(self):
        assert _is_blocking_enabled("disabled") is False

    def test_bool_true(self):
        assert _is_blocking_enabled(True) is True

    def test_bool_false(self):
        assert _is_blocking_enabled(False) is False


# ---------------------------------------------------------------------------
# Health Check
# ---------------------------------------------------------------------------

class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_healthy_blocking_active(self):
        provider = _make_provider()
        result = await provider.health_check()
        assert result.status == HealthStatus.UP
        assert "Blocking active" in result.message

    @pytest.mark.asyncio
    async def test_blocking_disabled_string(self):
        responses = _default_responses()
        responses["/api/dns/blocking"] = {"status": 200, "json": {"blocking": "disabled"}}
        provider = _make_provider(responses)
        result = await provider.health_check()
        assert result.status == HealthStatus.DEGRADED
        assert "disabled" in result.message.lower()

    @pytest.mark.asyncio
    async def test_blocking_disabled_bool(self):
        responses = _default_responses()
        responses["/api/dns/blocking"] = {"status": 200, "json": {"blocking": False}}
        provider = _make_provider(responses)
        result = await provider.health_check()
        assert result.status == HealthStatus.DEGRADED
        assert "disabled" in result.message.lower()

    @pytest.mark.asyncio
    async def test_auth_required(self):
        responses = _default_responses()
        responses["/api/dns/blocking"] = {"status": 401, "json": {}}
        responses["/api/auth"] = {"status": 200, "json": {"session": {"valid": False}}}
        provider = _make_provider(responses)
        provider._sid = None
        result = await provider.health_check()
        assert result.status == HealthStatus.DEGRADED
        assert "Auth required" in result.message

    @pytest.mark.asyncio
    async def test_connection_refused(self):
        provider = PiholeProvider(
            instance_id=1, display_name="Test",
            config={"url": "http://badhost", "password": ""},
        )
        transport = _ErrorTransport(httpx.ConnectError("refused"))
        provider.http_client = httpx.AsyncClient(transport=transport, base_url="http://badhost")
        result = await provider.health_check()
        assert result.status == HealthStatus.DOWN
        assert "refused" in result.message.lower()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class TestAuth:
    @pytest.mark.asyncio
    async def test_authenticate_success(self):
        provider = _make_provider()
        provider._sid = None
        result = await provider._authenticate()
        assert result is True
        assert provider._sid == "test-sid-abc123"

    @pytest.mark.asyncio
    async def test_authenticate_failure(self):
        responses = _default_responses()
        responses["/api/auth"] = {"status": 200, "json": {"session": {"valid": False}}}
        provider = _make_provider(responses)
        provider._sid = None
        result = await provider._authenticate()
        assert result is False

    @pytest.mark.asyncio
    async def test_no_password_skips_auth(self):
        provider = _make_provider(password="")
        provider._sid = None
        result = await provider._authenticate()
        assert result is True


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

class TestSummary:
    @pytest.mark.asyncio
    async def test_summary_shape(self):
        provider = _make_provider()
        result = await provider.get_summary()

        assert result.data["queries"]["total"] == 36692
        assert result.data["queries"]["blocked"] == 9013
        assert result.data["queries"]["percent_blocked"] == 24.6
        assert result.data["blocking_enabled"] is True

    @pytest.mark.asyncio
    async def test_clients(self):
        provider = _make_provider()
        result = await provider.get_summary()

        assert result.data["clients"]["active"] == 15
        assert result.data["clients"]["total"] == 22

    @pytest.mark.asyncio
    async def test_gravity(self):
        provider = _make_provider()
        result = await provider.get_summary()

        assert result.data["gravity"]["domains_being_blocked"] == 450350

    @pytest.mark.asyncio
    async def test_cache_and_forwarded(self):
        provider = _make_provider()
        result = await provider.get_summary()

        assert result.data["queries"]["cached"] == 19610
        assert result.data["queries"]["forwarded"] == 7748

    @pytest.mark.asyncio
    async def test_blocking_disabled_summary_string(self):
        responses = _default_responses()
        responses["/api/dns/blocking"] = {"status": 200, "json": {"blocking": "disabled"}}
        provider = _make_provider(responses)
        result = await provider.get_summary()
        assert result.data["blocking_enabled"] is False

    @pytest.mark.asyncio
    async def test_blocking_disabled_summary_bool(self):
        responses = _default_responses()
        responses["/api/dns/blocking"] = {"status": 200, "json": {"blocking": False}}
        provider = _make_provider(responses)
        result = await provider.get_summary()
        assert result.data["blocking_enabled"] is False


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------

class TestDetail:
    @pytest.mark.asyncio
    async def test_detail_shape(self):
        provider = _make_provider()
        result = await provider.get_detail()

        assert "stats" in result.data
        assert "top_domains" in result.data
        assert "top_blocked" in result.data
        assert "top_clients" in result.data
        assert "upstreams" in result.data
        assert "query_types" in result.data
        assert "blocking_enabled" in result.data
        assert "version" in result.data

    @pytest.mark.asyncio
    async def test_top_domains(self):
        provider = _make_provider()
        result = await provider.get_detail()

        td = result.data["top_domains"]
        assert len(td) == 3
        assert td[0]["domain"] == "google.com"

    @pytest.mark.asyncio
    async def test_top_blocked(self):
        provider = _make_provider()
        result = await provider.get_detail()

        tb = result.data["top_blocked"]
        assert len(tb) == 2
        assert tb[0]["domain"] == "ads.example.com"

    @pytest.mark.asyncio
    async def test_top_clients(self):
        provider = _make_provider()
        result = await provider.get_detail()

        tc = result.data["top_clients"]
        assert len(tc) == 3
        assert tc[0]["name"] == "desktop-pc"

    @pytest.mark.asyncio
    async def test_upstreams(self):
        provider = _make_provider()
        result = await provider.get_detail()

        us = result.data["upstreams"]
        assert len(us) == 1
        assert us[0]["name"] == "Unbound"


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

class TestActions:
    def test_action_definitions(self):
        provider = _make_provider()
        actions = provider.get_actions()
        keys = [a.key for a in actions]
        assert "disable_blocking" in keys
        assert "enable_blocking" in keys
        assert "update_gravity" in keys

    @pytest.mark.asyncio
    async def test_disable_blocking(self):
        provider = _make_provider()
        result = await provider.execute_action("disable_blocking", {"duration": "300"})
        assert result.success is True
        assert "disabled" in result.message.lower()

    @pytest.mark.asyncio
    async def test_enable_blocking(self):
        provider = _make_provider()
        result = await provider.execute_action("enable_blocking", {})
        assert result.success is True
        assert "enabled" in result.message.lower()

    @pytest.mark.asyncio
    async def test_update_gravity(self):
        provider = _make_provider()
        result = await provider.execute_action("update_gravity", {})
        assert result.success is True
        assert "gravity" in result.message.lower()

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
        assert "Pi-hole" in msg

    @pytest.mark.asyncio
    async def test_invalid_password(self):
        responses = _default_responses()
        responses["/api/auth"] = {"status": 200, "json": {"session": {"valid": False}}}
        provider = _make_provider(responses)
        provider._sid = None
        ok, msg = await provider.validate_config()
        assert ok is False
        assert "Invalid password" in msg

    @pytest.mark.asyncio
    async def test_connection_refused(self):
        provider = PiholeProvider(
            instance_id=1, display_name="Test",
            config={"url": "http://badhost", "password": ""},
        )
        transport = _ErrorTransport(httpx.ConnectError("refused"))
        provider.http_client = httpx.AsyncClient(transport=transport, base_url="http://badhost")
        ok, msg = await provider.validate_config()
        assert ok is False
        assert "Cannot connect" in msg


# ---------------------------------------------------------------------------
# Test utilities
# ---------------------------------------------------------------------------

class _ErrorTransport(httpx.AsyncBaseTransport):
    def __init__(self, error: Exception) -> None:
        self._error = error

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise self._error
