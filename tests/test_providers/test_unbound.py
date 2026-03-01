"""Tests for the Unbound provider."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from src.providers.unbound import UnboundProvider
from src.providers.base import HealthStatus


# Sample stats_noreset output
SAMPLE_STATS = {
    "total.num.queries": "218959",
    "total.num.cachehits": "216339",
    "total.num.cachemiss": "2620",
    "total.num.prefetch": "28326",
    "total.num.expired": "21661",
    "total.num.recursivereplies": "2620",
    "total.requestlist.avg": "0.5",
    "total.requestlist.max": "15",
    "total.recursion.time.avg": "0.0452",
    "total.recursion.time.median": "0.0121",
    "msg.cache.count": "2333",
    "rrset.cache.count": "2034",
    "infra.cache.count": "3",
    "unwanted.queries": "0",
    "unwanted.replies": "0",
}

SAMPLE_STATS_WITH_UNWANTED = {
    **SAMPLE_STATS,
    "unwanted.queries": "5",
    "unwanted.replies": "3",
}


def _make_provider(**config_overrides) -> UnboundProvider:
    """Create an UnboundProvider."""
    config = {
        "host": "10.0.0.1",
        "port": "8953",
        "server_cert": "/tmp/fake_server.pem",
        "control_key": "/tmp/fake_control.key",
        "control_cert": "/tmp/fake_control.pem",
        **config_overrides,
    }
    return UnboundProvider(
        instance_id=1,
        display_name="Test Unbound",
        config=config,
    )


# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------

class TestMeta:
    def test_type_id(self):
        meta = UnboundProvider.meta()
        assert meta.type_id == "unbound"

    def test_category(self):
        meta = UnboundProvider.meta()
        assert meta.category == "network"

    def test_permissions_count(self):
        meta = UnboundProvider.meta()
        assert len(meta.permissions) == 2

    def test_config_schema_fields(self):
        meta = UnboundProvider.meta()
        keys = [f["key"] for f in meta.config_schema["fields"]]
        assert "host" in keys
        assert "port" in keys
        assert "server_cert" in keys
        assert "control_key" in keys
        assert "control_cert" in keys


# ---------------------------------------------------------------------------
# Stats Parsing
# ---------------------------------------------------------------------------

class TestStatsParsing:
    def test_parse_queries(self):
        provider = _make_provider()
        parsed = provider._parse_stats(SAMPLE_STATS)

        assert parsed["queries"]["total"] == 218959
        assert parsed["queries"]["cache_hits"] == 216339
        assert parsed["queries"]["cache_misses"] == 2620
        assert parsed["queries"]["cache_hit_rate"] == 98.8
        assert parsed["queries"]["prefetch"] == 28326

    def test_parse_performance(self):
        provider = _make_provider()
        parsed = provider._parse_stats(SAMPLE_STATS)

        assert parsed["performance"]["recursion_time_avg_ms"] == 45.2
        assert parsed["performance"]["recursion_time_median_ms"] == 12.1
        assert parsed["performance"]["request_list_avg"] == 0.5
        assert parsed["performance"]["request_list_max"] == 15

    def test_parse_cache(self):
        provider = _make_provider()
        parsed = provider._parse_stats(SAMPLE_STATS)

        assert parsed["cache"]["message_count"] == 2333
        assert parsed["cache"]["rrset_count"] == 2034
        assert parsed["cache"]["infra_count"] == 3

    def test_parse_security(self):
        provider = _make_provider()
        parsed = provider._parse_stats(SAMPLE_STATS)

        assert parsed["security"]["unwanted_queries"] == 0
        assert parsed["security"]["unwanted_replies"] == 0

    def test_parse_unwanted(self):
        provider = _make_provider()
        parsed = provider._parse_stats(SAMPLE_STATS_WITH_UNWANTED)

        assert parsed["security"]["unwanted_queries"] == 5
        assert parsed["security"]["unwanted_replies"] == 3

    def test_parse_empty_stats(self):
        provider = _make_provider()
        parsed = provider._parse_stats({})

        assert parsed["queries"]["total"] == 0
        assert parsed["queries"]["cache_hit_rate"] == 0.0

    def test_cache_hit_rate_calculation(self):
        provider = _make_provider()
        stats = {"total.num.queries": "1000", "total.num.cachehits": "900", "total.num.cachemiss": "100"}
        parsed = provider._parse_stats(stats)
        assert parsed["queries"]["cache_hit_rate"] == 90.0


# ---------------------------------------------------------------------------
# Health Check
# ---------------------------------------------------------------------------

class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_healthy(self):
        provider = _make_provider()
        provider._fetch_stats = AsyncMock(return_value=SAMPLE_STATS)
        result = await provider.health_check()
        assert result.status == HealthStatus.UP
        assert "218959" in result.message

    @pytest.mark.asyncio
    async def test_high_unwanted(self):
        provider = _make_provider()
        provider._fetch_stats = AsyncMock(return_value=SAMPLE_STATS_WITH_UNWANTED)
        result = await provider.health_check()
        assert result.status == HealthStatus.DEGRADED
        assert "unwanted" in result.message.lower()

    @pytest.mark.asyncio
    async def test_unexpected_format(self):
        provider = _make_provider()
        provider._fetch_stats = AsyncMock(return_value={"some.other.key": "123"})
        result = await provider.health_check()
        assert result.status == HealthStatus.DOWN
        assert "Unexpected" in result.message

    @pytest.mark.asyncio
    async def test_connection_refused(self):
        provider = _make_provider()
        provider._fetch_stats = AsyncMock(side_effect=ConnectionRefusedError("refused"))
        result = await provider.health_check()
        assert result.status == HealthStatus.DOWN
        assert "refused" in result.message.lower()

    @pytest.mark.asyncio
    async def test_timeout(self):
        provider = _make_provider()
        provider._fetch_stats = AsyncMock(side_effect=asyncio.TimeoutError())
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
        provider._fetch_stats = AsyncMock(return_value=SAMPLE_STATS)
        result = await provider.get_summary()

        assert result.data["queries"]["total"] == 218959
        assert result.data["queries"]["cache_hit_rate"] == 98.8
        assert result.data["performance"]["recursion_time_avg_ms"] == 45.2
        assert result.data["cache"]["message_count"] == 2333

    @pytest.mark.asyncio
    async def test_empty_on_error(self):
        provider = _make_provider()
        provider._fetch_stats = AsyncMock(side_effect=ConnectionRefusedError("err"))
        result = await provider.get_summary()
        assert result.data["queries"]["total"] is None


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------

class TestDetail:
    @pytest.mark.asyncio
    async def test_detail_shape(self):
        provider = _make_provider()
        provider._fetch_stats = AsyncMock(return_value=SAMPLE_STATS)
        result = await provider.get_detail()

        assert "stats" in result.data
        assert "raw_stats" in result.data
        assert result.data["stats"]["queries"]["total"] == 218959

    @pytest.mark.asyncio
    async def test_raw_stats(self):
        provider = _make_provider()
        provider._fetch_stats = AsyncMock(return_value=SAMPLE_STATS)
        result = await provider.get_detail()

        assert result.data["raw_stats"]["total.num.queries"] == "218959"

    @pytest.mark.asyncio
    async def test_empty_on_error(self):
        provider = _make_provider()
        provider._fetch_stats = AsyncMock(side_effect=Exception("fail"))
        result = await provider.get_detail()
        assert result.data["stats"] == {}


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

class TestActions:
    def test_action_definitions(self):
        provider = _make_provider()
        actions = provider.get_actions()
        keys = [a.key for a in actions]
        assert "flush_cache" in keys

    @pytest.mark.asyncio
    async def test_flush_cache(self):
        provider = _make_provider()
        provider._send_command = AsyncMock(return_value="ok\n")
        result = await provider.execute_action("flush_cache", {})
        assert result.success is True
        assert "flushed" in result.message.lower()
        provider._send_command.assert_called_once_with("flush_zone .")

    @pytest.mark.asyncio
    async def test_flush_cache_empty_response(self):
        provider = _make_provider()
        provider._send_command = AsyncMock(return_value="")
        result = await provider.execute_action("flush_cache", {})
        assert result.success is True

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
        provider._fetch_stats = AsyncMock(return_value=SAMPLE_STATS)
        ok, msg = await provider.validate_config()
        assert ok is True
        assert "218959" in msg

    @pytest.mark.asyncio
    async def test_unexpected_format(self):
        provider = _make_provider()
        provider._fetch_stats = AsyncMock(return_value={"some.key": "val"})
        ok, msg = await provider.validate_config()
        assert ok is False
        assert "unexpected" in msg.lower()

    @pytest.mark.asyncio
    async def test_connection_refused(self):
        provider = _make_provider()
        provider._fetch_stats = AsyncMock(side_effect=ConnectionRefusedError("err"))
        ok, msg = await provider.validate_config()
        assert ok is False
        assert "control-enable" in msg
