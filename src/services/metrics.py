"""MetricsStore abstraction and SQLite implementation.

All time-series data access goes through MetricsStore — no direct
queries against the metrics table anywhere else in the application.
"""

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import delete, func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import async_session_factory
from src.models.metrics import Metric

logger = structlog.get_logger()


@dataclass
class DataPoint:
    """A single aggregated data point."""

    timestamp: datetime
    value: float


class MetricsStore(ABC):
    """Abstract interface for time-series metric storage."""

    @abstractmethod
    async def write(
        self, metric: str, value: float, tags: dict[str, Any], timestamp: datetime
    ) -> None:
        """Write a single metric data point."""

    @abstractmethod
    async def query(
        self,
        metric: str,
        start: datetime,
        end: datetime,
        tags: dict[str, Any] | None = None,
        aggregation: str = "avg",
        bucket: str = "1h",
    ) -> list[DataPoint]:
        """Query metrics with time bucketing and aggregation."""

    @abstractmethod
    async def retention_cleanup(self, older_than: datetime) -> int:
        """Delete metrics older than the given datetime. Returns count deleted."""


class SQLiteMetricsStore(MetricsStore):
    """SQLite-backed MetricsStore using the Metric ORM model."""

    _AGG_FUNCS = {
        "avg": func.avg,
        "sum": func.sum,
        "min": func.min,
        "max": func.max,
        "count": func.count,
    }

    _BUCKET_FORMATS = {
        "1m": "%Y-%m-%d %H:%M",
        "5m": "%Y-%m-%d %H:%M",
        "1h": "%Y-%m-%d %H",
        "1d": "%Y-%m-%d",
    }

    _BUCKET_SECONDS = {
        "1m": 60,
        "5m": 300,
        "1h": 3600,
        "1d": 86400,
    }

    async def write(
        self, metric: str, value: float, tags: dict[str, Any], timestamp: datetime
    ) -> None:
        """Write a single metric data point."""
        async with async_session_factory() as session:
            session.add(Metric(
                metric=metric,
                value=value,
                tags=json.dumps(tags, default=str),
                timestamp=timestamp,
            ))
            await session.commit()

    async def query(
        self,
        metric: str,
        start: datetime,
        end: datetime,
        tags: dict[str, Any] | None = None,
        aggregation: str = "avg",
        bucket: str = "1h",
    ) -> list[DataPoint]:
        """Query metrics with time bucketing and aggregation.

        Uses SQLite strftime for bucketing. For 5m buckets, rounds down
        to the nearest 5-minute boundary.
        """
        agg_func = self._AGG_FUNCS.get(aggregation)
        if agg_func is None:
            raise ValueError(f"Unknown aggregation: {aggregation}")

        bucket_fmt = self._BUCKET_FORMATS.get(bucket)
        if bucket_fmt is None:
            raise ValueError(f"Unknown bucket size: {bucket}")

        bucket_seconds = self._BUCKET_SECONDS[bucket]

        async with async_session_factory() as session:
            # Build the bucket expression
            if bucket == "5m":
                # Round down to 5-minute boundary using integer division
                bucket_expr = literal_column(
                    "strftime('%Y-%m-%d %H:', timestamp) || "
                    "printf('%02d', (cast(strftime('%M', timestamp) as integer) / 5) * 5)"
                )
            else:
                bucket_expr = func.strftime(bucket_fmt, Metric.timestamp)

            # Build query
            agg_value = agg_func(Metric.value).label("agg_value")
            stmt = (
                select(bucket_expr.label("bucket"), agg_value)
                .where(
                    Metric.metric == metric,
                    Metric.timestamp >= start,
                    Metric.timestamp < end,
                )
                .group_by("bucket")
                .order_by("bucket")
            )

            # Optional tag filter — exact match on serialized tags
            if tags:
                tags_json = json.dumps(tags, default=str)
                stmt = stmt.where(Metric.tags == tags_json)

            result = await session.execute(stmt)
            rows = result.all()

            points: list[DataPoint] = []
            for bucket_str, value in rows:
                # Parse bucket string back to datetime
                try:
                    if bucket == "1d":
                        ts = datetime.strptime(bucket_str, "%Y-%m-%d")
                    elif bucket in ("1h",):
                        ts = datetime.strptime(bucket_str, "%Y-%m-%d %H")
                    else:
                        ts = datetime.strptime(bucket_str, "%Y-%m-%d %H:%M")
                except (ValueError, TypeError):
                    continue
                points.append(DataPoint(timestamp=ts, value=float(value)))

            return points

    async def retention_cleanup(self, older_than: datetime) -> int:
        """Delete metrics older than the given datetime."""
        async with async_session_factory() as session:
            result = await session.execute(
                delete(Metric).where(Metric.timestamp < older_than)
            )
            await session.commit()
            count = result.rowcount
            if count > 0:
                logger.info("metrics_retention_cleanup", deleted=count)
            return count


# Singleton instance
metrics_store = SQLiteMetricsStore()
