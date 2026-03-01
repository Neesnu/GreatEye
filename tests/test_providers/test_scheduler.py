"""Tests for the async polling scheduler."""
import asyncio

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.models.provider import ProviderInstance, ProviderType
from src.providers.cache import read_cache
from src.providers.scheduler import PollJob, Scheduler
from tests.conftest import MockProvider


@pytest_asyncio.fixture
async def scheduler_db(db_engine, monkeypatch):
    """Set up scheduler tests with cache monkeypatched to use test DB."""
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        session.add(ProviderType(
            id="mock", display_name="Mock", icon="", category="media",
            config_schema="{}", default_intervals="{}",
        ))
        await session.flush()
        session.add(ProviderInstance(
            id=1, provider_type_id="mock", display_name="Test",
            config="{}", health_interval=30, summary_interval=60,
            detail_cache_ttl=300,
        ))
        await session.commit()

    import src.providers.cache as cache_mod
    monkeypatch.setattr(cache_mod, "async_session_factory", factory)
    yield factory


class TestScheduler:
    @pytest.mark.asyncio
    async def test_starts_empty(self):
        sched = Scheduler()
        assert sched.job_count == 0

    @pytest.mark.asyncio
    async def test_schedule_instance(self, scheduler_db):
        sched = Scheduler()
        mock = MockProvider(instance_id=1, display_name="Test", config={})
        await sched.schedule_instance(mock, health_interval=1, summary_interval=1)
        assert sched.job_count == 2
        await sched.stop_all()

    @pytest.mark.asyncio
    async def test_unschedule_instance(self, scheduler_db):
        sched = Scheduler()
        mock = MockProvider(instance_id=1, display_name="Test", config={})
        await sched.schedule_instance(mock, health_interval=1, summary_interval=1)
        await sched.unschedule_instance(1)
        assert sched.job_count == 0

    @pytest.mark.asyncio
    async def test_poll_writes_cache(self, scheduler_db):
        sched = Scheduler()
        mock = MockProvider(instance_id=1, display_name="Test", config={})
        await sched.schedule_instance(mock, health_interval=1, summary_interval=1)

        await asyncio.sleep(2)

        health_data, _, _ = await read_cache(1, "health")
        assert health_data is not None
        assert health_data["status"] == "up"

        summary_data, _, _ = await read_cache(1, "summary")
        assert summary_data is not None
        assert summary_data["items"] == 42

        await sched.stop_all()

    @pytest.mark.asyncio
    async def test_stop_all(self, scheduler_db):
        sched = Scheduler()
        mock = MockProvider(instance_id=1, display_name="Test", config={})
        await sched.schedule_instance(mock, health_interval=1, summary_interval=1)
        await sched.stop_all()
        assert sched.job_count == 0
