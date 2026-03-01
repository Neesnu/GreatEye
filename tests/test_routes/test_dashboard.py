"""Tests for dashboard routes, SSE formatting, and instance data helpers."""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.providers.base import HealthResult, HealthStatus, SummaryResult
from src.routes.dashboard import _build_instance_data, _format_sse, _get_visible_instances


# ---------------------------------------------------------------------------
# SSE formatting
# ---------------------------------------------------------------------------

class TestFormatSSE:
    def test_single_line(self):
        result = _format_sse("summary:1", "<div>hello</div>")
        assert result == "event: summary:1\ndata: <div>hello</div>\n\n"

    def test_multi_line(self):
        html = "<div>\n  <span>hi</span>\n</div>"
        result = _format_sse("health:2", html)
        lines = result.split("\n")
        assert lines[0] == "event: health:2"
        assert lines[1] == "data: <div>"
        assert lines[2] == "data:   <span>hi</span>"
        assert lines[3] == "data: </div>"
        # Trailing double newline
        assert result.endswith("\n\n")

    def test_empty_data(self):
        result = _format_sse("test:0", "")
        assert result == "event: test:0\ndata: \n\n"

    def test_crlf_normalized(self):
        result = _format_sse("evt:1", "a\r\nb")
        assert "data: a\n" in result
        assert "data: b\n" in result


# ---------------------------------------------------------------------------
# _build_instance_data
# ---------------------------------------------------------------------------

class TestBuildInstanceData:
    @pytest.mark.asyncio
    async def test_builds_dict_from_cache(self):
        provider = MagicMock()
        provider.instance_id = 42
        provider.display_name = "My Provider"
        meta = MagicMock()
        meta.type_id = "mock"
        meta.display_name = "Mock Provider"
        meta.icon = "mock.svg"
        meta.category = "media"
        provider.meta.return_value = meta

        now = datetime.utcnow()
        health_cache = ({"status": "up", "message": "OK"}, now, False)
        summary_cache = ({"items": 10}, now, False)

        with patch("src.routes.dashboard.read_cache", new_callable=AsyncMock) as mock_read:
            mock_read.side_effect = [health_cache, summary_cache]
            result = await _build_instance_data(provider)

        assert result["instance_id"] == 42
        assert result["display_name"] == "My Provider"
        assert result["type_id"] == "mock"
        assert result["health_status"] == "up"
        assert result["health_message"] == "OK"
        assert result["summary"] == {"items": 10}
        assert result["summary_stale"] is False

    @pytest.mark.asyncio
    async def test_handles_empty_cache(self):
        provider = MagicMock()
        provider.instance_id = 1
        provider.display_name = "Empty"
        meta = MagicMock()
        meta.type_id = "test"
        meta.display_name = "Test"
        meta.icon = ""
        meta.category = "other"
        provider.meta.return_value = meta

        with patch("src.routes.dashboard.read_cache", new_callable=AsyncMock) as mock_read:
            mock_read.side_effect = [(None, None, False), (None, None, False)]
            result = await _build_instance_data(provider)

        assert result["health_status"] == "unknown"
        assert result["health_message"] == ""
        assert result["summary"] is None
        assert result["summary_stale"] is False

    @pytest.mark.asyncio
    async def test_stale_summary(self):
        provider = MagicMock()
        provider.instance_id = 5
        provider.display_name = "Stale"
        meta = MagicMock()
        meta.type_id = "stale"
        meta.display_name = "Stale"
        meta.icon = ""
        meta.category = "other"
        provider.meta.return_value = meta

        old = datetime.utcnow() - timedelta(hours=1)
        with patch("src.routes.dashboard.read_cache", new_callable=AsyncMock) as mock_read:
            mock_read.side_effect = [
                ({"status": "degraded", "message": "slow"}, old, False),
                ({"items": 5}, old, True),
            ]
            result = await _build_instance_data(provider)

        assert result["health_status"] == "degraded"
        assert result["summary_stale"] is True


# ---------------------------------------------------------------------------
# _get_visible_instances
# ---------------------------------------------------------------------------

class TestGetVisibleInstances:
    @pytest.mark.asyncio
    async def test_admin_sees_all(self):
        user = MagicMock()
        user.permission_keys = {"system.admin"}

        p1 = MagicMock()
        p1.instance_id = 1
        p1.display_name = "P1"
        meta1 = MagicMock(type_id="alpha", display_name="Alpha", icon="", category="media")
        p1.meta.return_value = meta1

        p2 = MagicMock()
        p2.instance_id = 2
        p2.display_name = "P2"
        meta2 = MagicMock(type_id="beta", display_name="Beta", icon="", category="infra")
        p2.meta.return_value = meta2

        with patch("src.routes.dashboard.registry") as mock_registry, \
             patch("src.routes.dashboard.read_cache", new_callable=AsyncMock) as mock_read:
            mock_registry.get_all_instances.return_value = [p1, p2]
            mock_read.return_value = (None, None, False)
            instances = await _get_visible_instances(user)

        assert len(instances) == 2

    @pytest.mark.asyncio
    async def test_filters_by_permission(self):
        user = MagicMock()
        user.permission_keys = {"alpha.view"}

        p1 = MagicMock()
        p1.instance_id = 1
        p1.display_name = "P1"
        meta1 = MagicMock(type_id="alpha", display_name="Alpha", icon="", category="media")
        p1.meta.return_value = meta1

        p2 = MagicMock()
        p2.instance_id = 2
        p2.display_name = "P2"
        meta2 = MagicMock(type_id="beta", display_name="Beta", icon="", category="infra")
        p2.meta.return_value = meta2

        with patch("src.routes.dashboard.registry") as mock_registry, \
             patch("src.routes.dashboard.read_cache", new_callable=AsyncMock) as mock_read:
            mock_registry.get_all_instances.return_value = [p1, p2]
            mock_read.return_value = (None, None, False)
            instances = await _get_visible_instances(user)

        assert len(instances) == 1
        assert instances[0]["instance_id"] == 1

    @pytest.mark.asyncio
    async def test_no_permissions_sees_nothing(self):
        user = MagicMock()
        user.permission_keys = set()

        p1 = MagicMock()
        p1.instance_id = 1
        p1.display_name = "P1"
        meta1 = MagicMock(type_id="alpha", display_name="Alpha", icon="", category="media")
        p1.meta.return_value = meta1

        with patch("src.routes.dashboard.registry") as mock_registry:
            mock_registry.get_all_instances.return_value = [p1]
            instances = await _get_visible_instances(user)

        assert len(instances) == 0

    @pytest.mark.asyncio
    async def test_empty_registry(self):
        user = MagicMock()
        user.permission_keys = {"system.admin"}

        with patch("src.routes.dashboard.registry") as mock_registry:
            mock_registry.get_all_instances.return_value = []
            instances = await _get_visible_instances(user)

        assert instances == []
