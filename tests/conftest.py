import json
import os
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

# Override env before any app imports
os.environ["DATABASE_URL"] = "sqlite+aiosqlite://"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing")

from src.database import Base
from src.models import *  # noqa: F401,F403 — ensure all models registered
from src.providers.base import BaseProvider, ProviderMeta, PermissionDef
from src.providers.base import (
    ActionDefinition,
    ActionResult,
    DetailResult,
    HealthResult,
    HealthStatus,
    SummaryResult,
)


class MockTransport(httpx.AsyncBaseTransport):
    """Mock HTTP transport that returns predefined responses."""

    def __init__(self, responses: dict[str, dict]) -> None:
        self.responses = responses

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.raw_path.decode("utf-8").split("?")[0]
        if path in self.responses:
            resp_data = self.responses[path]
            status = resp_data.get("status", 200)
            headers = resp_data.get("headers", {})
            # Support plain text responses via "text" key
            if "text" in resp_data:
                return httpx.Response(
                    status_code=status,
                    text=resp_data["text"],
                    headers=headers,
                )
            return httpx.Response(
                status_code=status,
                json=resp_data.get("json", {}),
                headers=headers,
            )
        return httpx.Response(status_code=404)


def load_fixture(provider: str, name: str) -> dict:
    """Load a JSON fixture file."""
    path = Path(f"tests/fixtures/{provider}/{name}.json")
    return json.loads(path.read_text())


@pytest_asyncio.fixture
async def db_engine():
    """Create an in-memory SQLite engine with all tables."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    """Create a DB session for testing."""
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session


class MockProvider(BaseProvider):
    """Mock provider for testing the framework."""

    _health_status = HealthStatus.UP
    _summary_data = {"items": 42, "active": 5}
    _detail_data = {"items": [{"id": 1, "name": "test"}]}

    @staticmethod
    def meta() -> ProviderMeta:
        from datetime import datetime
        return ProviderMeta(
            type_id="mock",
            display_name="Mock Provider",
            icon="mock-icon",
            category="media",
            config_schema={
                "fields": [
                    {"key": "url", "label": "URL", "type": "url", "required": True},
                    {"key": "api_key", "label": "API Key", "type": "secret", "required": True},
                ]
            },
            default_intervals={
                "health_seconds": 30,
                "summary_seconds": 60,
                "detail_cache_seconds": 300,
            },
            permissions=[
                PermissionDef("mock.view", "View Mock Data", "View mock data", "read"),
                PermissionDef("mock.refresh", "Refresh Mock", "Trigger mock refresh", "action"),
                PermissionDef("mock.admin", "Admin Mock", "Admin mock operations", "admin"),
            ],
        )

    async def health_check(self) -> HealthResult:
        return HealthResult(
            status=self._health_status,
            message="Mock is up",
            response_time_ms=5.0,
        )

    async def get_summary(self) -> SummaryResult:
        from datetime import datetime
        return SummaryResult(data=dict(self._summary_data), fetched_at=datetime.utcnow())

    async def get_detail(self) -> DetailResult:
        from datetime import datetime
        return DetailResult(data=dict(self._detail_data), fetched_at=datetime.utcnow())

    def get_actions(self) -> list[ActionDefinition]:
        return [
            ActionDefinition(
                key="refresh",
                display_name="Refresh",
                permission="mock.refresh",
                category="action",
                params_schema={
                    "properties": {
                        "force": {"type": "boolean", "required": False},
                    }
                },
            ),
        ]

    async def execute_action(self, action: str, params: dict) -> ActionResult:
        if action == "refresh":
            return ActionResult(success=True, message="Mock refreshed")
        return ActionResult(success=False, message=f"Unknown: {action}")

    async def validate_config(self) -> tuple[bool, str]:
        return True, "Mock OK"


@pytest.fixture
def mock_provider() -> MockProvider:
    """Create a MockProvider instance."""
    return MockProvider(
        instance_id=1,
        display_name="Test Mock",
        config={"url": "http://mock", "api_key": "test-key"},
    )
