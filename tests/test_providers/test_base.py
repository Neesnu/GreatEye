"""Tests for the BaseProvider ABC and result dataclasses."""
import pytest

from src.providers.base import (
    ActionDefinition,
    ActionResult,
    DetailResult,
    HealthResult,
    HealthStatus,
    PermissionDef,
    ProviderMeta,
    SummaryResult,
)
from tests.conftest import MockProvider


class TestHealthStatus:
    def test_enum_values(self):
        assert HealthStatus.UP.value == "up"
        assert HealthStatus.DEGRADED.value == "degraded"
        assert HealthStatus.DOWN.value == "down"
        assert HealthStatus.UNKNOWN.value == "unknown"
        assert HealthStatus.DISABLED.value == "disabled"

    def test_round_trip(self):
        assert HealthStatus("up") == HealthStatus.UP


class TestMockProvider:
    def test_meta(self):
        meta = MockProvider.meta()
        assert meta.type_id == "mock"
        assert meta.display_name == "Mock Provider"
        assert meta.category == "media"
        assert len(meta.permissions) == 3
        assert len(meta.config_schema["fields"]) == 2

    @pytest.mark.asyncio
    async def test_health_check(self, mock_provider):
        result = await mock_provider.health_check()
        assert result.status == HealthStatus.UP
        assert result.response_time_ms == 5.0

    @pytest.mark.asyncio
    async def test_get_summary(self, mock_provider):
        result = await mock_provider.get_summary()
        assert result.data["items"] == 42
        assert result.data["active"] == 5
        assert result.fetched_at is not None

    @pytest.mark.asyncio
    async def test_get_detail(self, mock_provider):
        result = await mock_provider.get_detail()
        assert len(result.data["items"]) == 1

    def test_get_actions(self, mock_provider):
        actions = mock_provider.get_actions()
        assert len(actions) == 1
        assert actions[0].key == "refresh"
        assert actions[0].permission == "mock.refresh"

    @pytest.mark.asyncio
    async def test_execute_action_known(self, mock_provider):
        result = await mock_provider.execute_action("refresh", {})
        assert result.success
        assert result.message == "Mock refreshed"

    @pytest.mark.asyncio
    async def test_execute_action_unknown(self, mock_provider):
        result = await mock_provider.execute_action("unknown", {})
        assert not result.success

    @pytest.mark.asyncio
    async def test_validate_config(self, mock_provider):
        valid, msg = await mock_provider.validate_config()
        assert valid
        assert msg == "Mock OK"

    @pytest.mark.asyncio
    async def test_cleanup_default(self, mock_provider):
        await mock_provider.cleanup()  # Should not raise
