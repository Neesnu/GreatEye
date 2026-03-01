"""Tests for MetricsStore (SQLite implementation)."""

import json
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.database import Base
from src.models.metrics import Metric
from src.services.metrics import DataPoint, SQLiteMetricsStore


@pytest_asyncio.fixture
async def metrics_engine():
    """Create an in-memory SQLite engine with metrics table."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def store(metrics_engine, monkeypatch):
    """Create a SQLiteMetricsStore wired to the test engine."""
    factory = async_sessionmaker(
        metrics_engine, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr("src.services.metrics.async_session_factory", factory)
    return SQLiteMetricsStore()


@pytest_asyncio.fixture
async def session(metrics_engine):
    """Raw DB session for verification queries."""
    factory = async_sessionmaker(
        metrics_engine, class_=AsyncSession, expire_on_commit=False
    )
    async with factory() as session:
        yield session


class TestWrite:
    @pytest.mark.asyncio
    async def test_write_single(self, store, session):
        now = datetime(2025, 1, 15, 12, 0, 0)
        await store.write("cpu.usage", 42.5, {"host": "server1"}, now)

        result = await session.execute(select(Metric))
        rows = result.scalars().all()
        assert len(rows) == 1
        assert rows[0].metric == "cpu.usage"
        assert rows[0].value == 42.5
        assert json.loads(rows[0].tags) == {"host": "server1"}
        assert rows[0].timestamp == now

    @pytest.mark.asyncio
    async def test_write_multiple(self, store, session):
        now = datetime(2025, 1, 15, 12, 0, 0)
        await store.write("cpu.usage", 40.0, {}, now)
        await store.write("cpu.usage", 60.0, {}, now + timedelta(minutes=1))
        await store.write("mem.usage", 80.0, {}, now)

        result = await session.execute(select(Metric))
        rows = result.scalars().all()
        assert len(rows) == 3

    @pytest.mark.asyncio
    async def test_write_empty_tags(self, store, session):
        now = datetime(2025, 1, 15, 12, 0, 0)
        await store.write("test.metric", 1.0, {}, now)

        result = await session.execute(select(Metric))
        row = result.scalar_one()
        assert json.loads(row.tags) == {}


class TestQuery:
    @pytest.mark.asyncio
    async def test_query_avg_1h(self, store):
        """Average aggregation with 1-hour buckets."""
        base = datetime(2025, 1, 15, 12, 0, 0)
        # Hour 12: values 10, 20, 30 → avg 20
        await store.write("cpu", 10.0, {}, base)
        await store.write("cpu", 20.0, {}, base + timedelta(minutes=15))
        await store.write("cpu", 30.0, {}, base + timedelta(minutes=30))
        # Hour 13: values 50, 70 → avg 60
        await store.write("cpu", 50.0, {}, base + timedelta(hours=1))
        await store.write("cpu", 70.0, {}, base + timedelta(hours=1, minutes=30))

        points = await store.query(
            "cpu", base, base + timedelta(hours=2), aggregation="avg", bucket="1h"
        )
        assert len(points) == 2
        assert points[0].value == pytest.approx(20.0)
        assert points[1].value == pytest.approx(60.0)

    @pytest.mark.asyncio
    async def test_query_sum(self, store):
        """Sum aggregation."""
        base = datetime(2025, 1, 15, 12, 0, 0)
        await store.write("requests", 100.0, {}, base)
        await store.write("requests", 200.0, {}, base + timedelta(minutes=10))

        points = await store.query(
            "requests", base, base + timedelta(hours=1), aggregation="sum", bucket="1h"
        )
        assert len(points) == 1
        assert points[0].value == pytest.approx(300.0)

    @pytest.mark.asyncio
    async def test_query_min_max(self, store):
        """Min and max aggregations."""
        base = datetime(2025, 1, 15, 12, 0, 0)
        await store.write("temp", 22.0, {}, base)
        await store.write("temp", 35.0, {}, base + timedelta(minutes=10))
        await store.write("temp", 18.0, {}, base + timedelta(minutes=20))

        min_pts = await store.query(
            "temp", base, base + timedelta(hours=1), aggregation="min", bucket="1h"
        )
        assert min_pts[0].value == pytest.approx(18.0)

        max_pts = await store.query(
            "temp", base, base + timedelta(hours=1), aggregation="max", bucket="1h"
        )
        assert max_pts[0].value == pytest.approx(35.0)

    @pytest.mark.asyncio
    async def test_query_count(self, store):
        """Count aggregation."""
        base = datetime(2025, 1, 15, 12, 0, 0)
        await store.write("events", 1.0, {}, base)
        await store.write("events", 1.0, {}, base + timedelta(minutes=5))
        await store.write("events", 1.0, {}, base + timedelta(minutes=10))

        points = await store.query(
            "events", base, base + timedelta(hours=1), aggregation="count", bucket="1h"
        )
        assert points[0].value == 3.0

    @pytest.mark.asyncio
    async def test_query_1d_bucket(self, store):
        """Daily bucket aggregation."""
        day1 = datetime(2025, 1, 15, 10, 0, 0)
        day2 = datetime(2025, 1, 16, 14, 0, 0)
        await store.write("daily", 10.0, {}, day1)
        await store.write("daily", 20.0, {}, day1 + timedelta(hours=3))
        await store.write("daily", 30.0, {}, day2)

        points = await store.query(
            "daily",
            datetime(2025, 1, 15),
            datetime(2025, 1, 17),
            aggregation="avg",
            bucket="1d",
        )
        assert len(points) == 2
        assert points[0].value == pytest.approx(15.0)  # (10+20)/2
        assert points[1].value == pytest.approx(30.0)

    @pytest.mark.asyncio
    async def test_query_1m_bucket(self, store):
        """1-minute bucket aggregation."""
        base = datetime(2025, 1, 15, 12, 0, 0)
        await store.write("fast", 1.0, {}, base)
        await store.write("fast", 3.0, {}, base + timedelta(seconds=30))
        await store.write("fast", 10.0, {}, base + timedelta(minutes=1))

        points = await store.query(
            "fast", base, base + timedelta(minutes=2), aggregation="avg", bucket="1m"
        )
        assert len(points) == 2
        assert points[0].value == pytest.approx(2.0)  # (1+3)/2
        assert points[1].value == pytest.approx(10.0)

    @pytest.mark.asyncio
    async def test_query_5m_bucket(self, store):
        """5-minute bucket aggregation."""
        base = datetime(2025, 1, 15, 12, 0, 0)
        # 12:00-12:04 → bucket 12:00
        await store.write("poll", 5.0, {}, base)
        await store.write("poll", 15.0, {}, base + timedelta(minutes=3))
        # 12:05-12:09 → bucket 12:05
        await store.write("poll", 25.0, {}, base + timedelta(minutes=5))

        points = await store.query(
            "poll", base, base + timedelta(minutes=10), aggregation="avg", bucket="5m"
        )
        assert len(points) == 2
        assert points[0].value == pytest.approx(10.0)  # (5+15)/2
        assert points[1].value == pytest.approx(25.0)

    @pytest.mark.asyncio
    async def test_query_with_tags(self, store):
        """Tag filtering returns only matching metrics."""
        base = datetime(2025, 1, 15, 12, 0, 0)
        await store.write("cpu", 40.0, {"host": "a"}, base)
        await store.write("cpu", 80.0, {"host": "b"}, base)

        points = await store.query(
            "cpu",
            base,
            base + timedelta(hours=1),
            tags={"host": "a"},
            aggregation="avg",
            bucket="1h",
        )
        assert len(points) == 1
        assert points[0].value == pytest.approx(40.0)

    @pytest.mark.asyncio
    async def test_query_empty_range(self, store):
        """Query with no matching data returns empty list."""
        points = await store.query(
            "nonexistent",
            datetime(2025, 1, 1),
            datetime(2025, 1, 2),
        )
        assert points == []

    @pytest.mark.asyncio
    async def test_query_time_boundary(self, store):
        """End time is exclusive."""
        base = datetime(2025, 1, 15, 12, 0, 0)
        await store.write("test", 1.0, {}, base)
        # Exactly at end time — should not be included
        await store.write("test", 99.0, {}, base + timedelta(hours=1))

        points = await store.query(
            "test", base, base + timedelta(hours=1), aggregation="avg", bucket="1h"
        )
        assert len(points) == 1
        assert points[0].value == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_query_invalid_aggregation(self, store):
        with pytest.raises(ValueError, match="Unknown aggregation"):
            await store.query(
                "test", datetime(2025, 1, 1), datetime(2025, 1, 2),
                aggregation="median",
            )

    @pytest.mark.asyncio
    async def test_query_invalid_bucket(self, store):
        with pytest.raises(ValueError, match="Unknown bucket"):
            await store.query(
                "test", datetime(2025, 1, 1), datetime(2025, 1, 2),
                bucket="2h",
            )

    @pytest.mark.asyncio
    async def test_query_ordered_by_time(self, store):
        """Results are ordered by bucket timestamp."""
        base = datetime(2025, 1, 15, 12, 0, 0)
        # Write out of order
        await store.write("ordered", 3.0, {}, base + timedelta(hours=2))
        await store.write("ordered", 1.0, {}, base)
        await store.write("ordered", 2.0, {}, base + timedelta(hours=1))

        points = await store.query(
            "ordered", base, base + timedelta(hours=3),
            aggregation="avg", bucket="1h",
        )
        assert len(points) == 3
        assert points[0].value == pytest.approx(1.0)
        assert points[1].value == pytest.approx(2.0)
        assert points[2].value == pytest.approx(3.0)


class TestRetentionCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_deletes_old(self, store, session):
        old = datetime(2025, 1, 1, 0, 0, 0)
        recent = datetime(2025, 1, 20, 0, 0, 0)
        await store.write("metric", 1.0, {}, old)
        await store.write("metric", 2.0, {}, recent)

        cutoff = datetime(2025, 1, 10, 0, 0, 0)
        deleted = await store.retention_cleanup(cutoff)

        assert deleted == 1
        result = await session.execute(select(Metric))
        rows = result.scalars().all()
        assert len(rows) == 1
        assert rows[0].value == 2.0

    @pytest.mark.asyncio
    async def test_cleanup_nothing_to_delete(self, store):
        now = datetime(2025, 1, 15, 12, 0, 0)
        await store.write("metric", 1.0, {}, now)

        deleted = await store.retention_cleanup(datetime(2025, 1, 1))
        assert deleted == 0

    @pytest.mark.asyncio
    async def test_cleanup_deletes_all_old(self, store, session):
        old = datetime(2024, 1, 1, 0, 0, 0)
        for i in range(5):
            await store.write("metric", float(i), {}, old + timedelta(days=i))

        deleted = await store.retention_cleanup(datetime(2025, 1, 1))
        assert deleted == 5

        result = await session.execute(select(Metric))
        rows = result.scalars().all()
        assert len(rows) == 0
