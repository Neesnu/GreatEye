"""Tests for the provider cache layer."""
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from src.database import Base
from src.models.provider import ProviderCache, ProviderInstance, ProviderType
from src.providers.cache import (
    invalidate_cache,
    is_within_ttl,
    mark_stale,
    read_cache,
    write_cache,
)


@pytest_asyncio.fixture
async def cache_db(db_engine, monkeypatch):
    """Set up cache tests with a real provider instance in the DB."""
    factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)

    # Seed a provider type and instance for FK constraints
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

    # Monkeypatch cache module to use our test session factory
    import src.providers.cache as cache_mod
    monkeypatch.setattr(cache_mod, "async_session_factory", factory)
    yield factory


class TestReadWriteCache:
    @pytest.mark.asyncio
    async def test_read_empty(self, cache_db):
        data, ts, stale = await read_cache(1, "health")
        assert data is None
        assert ts is None
        assert stale is False

    @pytest.mark.asyncio
    async def test_write_then_read(self, cache_db):
        now = datetime.utcnow()
        await write_cache(1, "health", {"status": "up"}, now)
        data, ts, stale = await read_cache(1, "health")
        assert data == {"status": "up"}
        assert ts is not None
        assert stale is False

    @pytest.mark.asyncio
    async def test_upsert_overwrites(self, cache_db):
        now = datetime.utcnow()
        await write_cache(1, "summary", {"v": 1}, now)
        await write_cache(1, "summary", {"v": 2}, now)
        data, _, _ = await read_cache(1, "summary")
        assert data == {"v": 2}


class TestStaleAndInvalidate:
    @pytest.mark.asyncio
    async def test_mark_stale(self, cache_db):
        await write_cache(1, "health", {"status": "up"}, datetime.utcnow())
        await mark_stale(1, "health")
        _, _, stale = await read_cache(1, "health")
        assert stale is True

    @pytest.mark.asyncio
    async def test_write_clears_stale(self, cache_db):
        await write_cache(1, "health", {"status": "up"}, datetime.utcnow())
        await mark_stale(1, "health")
        await write_cache(1, "health", {"status": "up"}, datetime.utcnow())
        _, _, stale = await read_cache(1, "health")
        assert stale is False

    @pytest.mark.asyncio
    async def test_invalidate_specific_tier(self, cache_db):
        await write_cache(1, "health", {"a": 1}, datetime.utcnow())
        await write_cache(1, "summary", {"b": 2}, datetime.utcnow())
        await invalidate_cache(1, "health")
        data_h, _, _ = await read_cache(1, "health")
        data_s, _, _ = await read_cache(1, "summary")
        assert data_h is None
        assert data_s is not None

    @pytest.mark.asyncio
    async def test_invalidate_all_tiers(self, cache_db):
        await write_cache(1, "health", {"a": 1}, datetime.utcnow())
        await write_cache(1, "summary", {"b": 2}, datetime.utcnow())
        await invalidate_cache(1)
        data_h, _, _ = await read_cache(1, "health")
        data_s, _, _ = await read_cache(1, "summary")
        assert data_h is None
        assert data_s is None


class TestTTL:
    def test_within_ttl(self):
        assert is_within_ttl(datetime.utcnow(), 60) is True

    def test_expired_ttl(self):
        old = datetime.utcnow() - timedelta(seconds=120)
        assert is_within_ttl(old, 60) is False

    def test_none_timestamp(self):
        assert is_within_ttl(None, 60) is False
