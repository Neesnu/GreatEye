"""Tests for the health service and GET /health endpoint."""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from src.database import Base
from src.models.provider import ProviderInstance, ProviderInstanceState, ProviderType
from src.services import health as health_module


def _create_health_app(db_factory) -> FastAPI:
    """Create a minimal FastAPI app with just the /health route."""
    test_app = FastAPI()

    @test_app.get("/health")
    async def health_route():
        status = await health_module.get_health_status()
        http_status = 200 if status["status"] == "ok" else 503
        return JSONResponse(content=status, status_code=http_status)

    return test_app


@pytest_asyncio.fixture
async def health_engine():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def health_session(health_engine):
    factory = async_sessionmaker(
        health_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with factory() as session:
        yield session


@pytest_asyncio.fixture
async def health_factory(health_engine):
    return async_sessionmaker(
        health_engine, class_=AsyncSession, expire_on_commit=False
    )


@pytest_asyncio.fixture
async def client(health_factory, monkeypatch):
    """HTTP client for the health endpoint."""
    monkeypatch.setattr("src.services.health.async_session_factory", health_factory)
    # Ensure scheduler.is_running returns True by setting _running flag
    from src.providers.scheduler import scheduler
    monkeypatch.setattr(scheduler, "_running", True)
    app = _create_health_app(health_factory)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_ok_no_providers(self, client):
        """Health endpoint returns 200 with empty system."""
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["version"] == "1.0.0"
        assert data["database"] == "connected"
        assert isinstance(data["uptime_seconds"], int)
        assert data["providers"]["configured"] == 0
        assert data["providers"]["enabled"] == 0

    @pytest.mark.asyncio
    async def test_health_response_shape(self, client):
        """All required fields are present."""
        resp = await client.get("/health")
        data = resp.json()
        assert "status" in data
        assert "version" in data
        assert "database" in data
        assert "scheduler" in data
        assert "providers" in data
        assert "uptime_seconds" in data
        providers = data["providers"]
        assert "configured" in providers
        assert "enabled" in providers
        assert "healthy" in providers
        assert "degraded" in providers
        assert "down" in providers


class TestHealthService:
    @pytest.mark.asyncio
    async def test_providers_counted(self, health_factory, health_session, monkeypatch):
        """Provider stats reflect actual database state."""
        monkeypatch.setattr("src.services.health.async_session_factory", health_factory)

        # Add provider type
        health_session.add(ProviderType(
            id="test", display_name="Test", icon="t",
            category="test", config_schema="{}", default_intervals="{}",
        ))
        await health_session.flush()

        # Add 3 instances (2 enabled, 1 disabled)
        for i in range(3):
            health_session.add(ProviderInstance(
                provider_type_id="test",
                display_name=f"Test {i}",
                config="{}",
                health_interval=30,
                summary_interval=60,
                detail_cache_ttl=300,
                is_enabled=(i < 2),
            ))
        await health_session.commit()

        status = await health_module.get_health_status()
        assert status["providers"]["configured"] == 3
        assert status["providers"]["enabled"] == 2

    @pytest.mark.asyncio
    async def test_health_status_counts(self, health_factory, health_session, monkeypatch):
        """Health statuses are counted correctly."""
        monkeypatch.setattr("src.services.health.async_session_factory", health_factory)

        # Add provider type and instances
        health_session.add(ProviderType(
            id="test", display_name="Test", icon="t",
            category="test", config_schema="{}", default_intervals="{}",
        ))
        await health_session.flush()

        for i in range(4):
            inst = ProviderInstance(
                provider_type_id="test",
                display_name=f"Test {i}",
                config="{}",
                health_interval=30,
                summary_interval=60,
                detail_cache_ttl=300,
                is_enabled=True,
            )
            health_session.add(inst)
        await health_session.flush()

        # Query instance IDs
        from sqlalchemy import select
        result = await health_session.execute(select(ProviderInstance))
        instances = result.scalars().all()

        # Set health states: 2 up, 1 degraded, 1 down
        states = ["up", "up", "degraded", "down"]
        for inst, st in zip(instances, states):
            health_session.add(ProviderInstanceState(
                instance_id=inst.id,
                health_status=st,
                failure_count=0 if st == "up" else 1,
            ))
        await health_session.commit()

        status = await health_module.get_health_status()
        assert status["providers"]["healthy"] == 2
        assert status["providers"]["degraded"] == 1
        assert status["providers"]["down"] == 1
        assert status["status"] == "ok"

    @pytest.mark.asyncio
    async def test_all_down_degraded(self, health_factory, health_session, monkeypatch):
        """Status is degraded when all enabled providers are down."""
        monkeypatch.setattr("src.services.health.async_session_factory", health_factory)

        health_session.add(ProviderType(
            id="test", display_name="Test", icon="t",
            category="test", config_schema="{}", default_intervals="{}",
        ))
        await health_session.flush()

        inst = ProviderInstance(
            provider_type_id="test",
            display_name="Test",
            config="{}",
            health_interval=30,
            summary_interval=60,
            detail_cache_ttl=300,
            is_enabled=True,
        )
        health_session.add(inst)
        await health_session.flush()

        health_session.add(ProviderInstanceState(
            instance_id=inst.id,
            health_status="down",
            failure_count=5,
        ))
        await health_session.commit()

        status = await health_module.get_health_status()
        assert status["status"] == "degraded"
        assert status["providers"]["down"] == 1

    @pytest.mark.asyncio
    async def test_db_error_degraded(self, monkeypatch):
        """Status is degraded when database is unreachable."""
        async def broken_factory():
            raise RuntimeError("DB down")

        class BrokenCtx:
            async def __aenter__(self):
                raise RuntimeError("DB down")

            async def __aexit__(self, *args):
                pass

        monkeypatch.setattr(
            "src.services.health.async_session_factory",
            lambda: BrokenCtx(),
        )

        status = await health_module.get_health_status()
        assert status["database"] == "error"
        assert status["status"] == "degraded"
