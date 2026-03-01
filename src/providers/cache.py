import json
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import async_session_factory
from src.models.provider import ProviderCache

logger = structlog.get_logger()


async def read_cache(
    instance_id: int, tier: str
) -> tuple[dict[str, Any] | None, datetime | None, bool]:
    """Read cached data for an instance+tier.

    Returns (data, fetched_at, is_stale). All None if no cache entry exists.
    Short-lived DB transaction per H3.
    """
    async with async_session_factory() as session:
        result = await session.execute(
            select(ProviderCache).where(
                ProviderCache.instance_id == instance_id,
                ProviderCache.tier == tier,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None, None, False
        return json.loads(row.data), row.fetched_at, row.is_stale


async def write_cache(
    instance_id: int, tier: str, data: dict[str, Any], fetched_at: datetime
) -> None:
    """Write (upsert) cached data for an instance+tier.

    Short-lived DB transaction per H3.
    """
    serialized = json.dumps(data, default=str)
    async with async_session_factory() as session:
        result = await session.execute(
            select(ProviderCache).where(
                ProviderCache.instance_id == instance_id,
                ProviderCache.tier == tier,
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.data = serialized
            existing.fetched_at = fetched_at
            existing.is_stale = False
        else:
            session.add(ProviderCache(
                instance_id=instance_id,
                tier=tier,
                data=serialized,
                fetched_at=fetched_at,
                is_stale=False,
            ))
        await session.commit()

    logger.debug(
        "cache_written",
        instance_id=instance_id,
        tier=tier,
    )


async def mark_stale(instance_id: int, tier: str | None = None) -> None:
    """Mark cache entries as stale. If tier is None, marks all tiers for the instance."""
    async with async_session_factory() as session:
        stmt = update(ProviderCache).where(
            ProviderCache.instance_id == instance_id
        ).values(is_stale=True)
        if tier:
            stmt = stmt.where(ProviderCache.tier == tier)
        await session.execute(stmt)
        await session.commit()

    logger.debug("cache_marked_stale", instance_id=instance_id, tier=tier)


async def invalidate_cache(instance_id: int, tier: str | None = None) -> None:
    """Delete cache entries. If tier is None, deletes all tiers for the instance."""
    async with async_session_factory() as session:
        stmt = delete(ProviderCache).where(
            ProviderCache.instance_id == instance_id
        )
        if tier:
            stmt = stmt.where(ProviderCache.tier == tier)
        await session.execute(stmt)
        await session.commit()

    logger.debug("cache_invalidated", instance_id=instance_id, tier=tier)


def is_within_ttl(fetched_at: datetime | None, ttl_seconds: int) -> bool:
    """Check if a cached entry is within its TTL."""
    if fetched_at is None:
        return False
    elapsed = (datetime.utcnow() - fetched_at).total_seconds()
    return elapsed < ttl_seconds
