"""Self-health service per H6.

Provides system health status for external monitoring tools.
No authentication required, no sensitive data exposed.
"""

import time
from typing import Any

import structlog
from sqlalchemy import func, select, text

from src.database import async_session_factory
from src.models.provider import ProviderInstance, ProviderInstanceState

logger = structlog.get_logger()

# Set at app startup
_start_time: float = time.monotonic()


def reset_start_time() -> None:
    """Reset the start time (called on app startup)."""
    global _start_time
    _start_time = time.monotonic()


async def get_health_status() -> dict[str, Any]:
    """Build the health status response.

    Returns a dict matching the H6 spec shape:
    {
        "status": "ok" | "degraded",
        "version": "1.0.0",
        "database": "connected" | "error",
        "scheduler": "running" | "stopped",
        "providers": {
            "configured": N,
            "enabled": N,
            "healthy": N,
            "degraded": N,
            "down": N
        },
        "uptime_seconds": N
    }
    """
    from src.providers.scheduler import scheduler

    result: dict[str, Any] = {
        "status": "ok",
        "version": "1.0.0",
        "database": "connected",
        "scheduler": "running" if scheduler.is_running else "stopped",
        "providers": {
            "configured": 0,
            "enabled": 0,
            "healthy": 0,
            "degraded": 0,
            "down": 0,
        },
        "uptime_seconds": int(time.monotonic() - _start_time),
    }

    # Check database connectivity
    try:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception as e:
        logger.error("health_db_check_failed", error=str(e))
        result["database"] = "error"
        result["status"] = "degraded"

    # Count provider instances
    try:
        async with async_session_factory() as session:
            # Total configured
            row = await session.execute(
                select(func.count()).select_from(ProviderInstance)
            )
            result["providers"]["configured"] = row.scalar() or 0

            # Enabled
            row = await session.execute(
                select(func.count()).select_from(ProviderInstance).where(
                    ProviderInstance.is_enabled == True
                )
            )
            result["providers"]["enabled"] = row.scalar() or 0

            # Health status counts from state table
            rows = await session.execute(
                select(
                    ProviderInstanceState.health_status,
                    func.count(),
                ).group_by(ProviderInstanceState.health_status)
            )
            for status, count in rows.all():
                if status == "up":
                    result["providers"]["healthy"] = count
                elif status == "degraded":
                    result["providers"]["degraded"] = count
                elif status == "down":
                    result["providers"]["down"] = count
    except Exception as e:
        logger.error("health_provider_check_failed", error=str(e))
        result["status"] = "degraded"

    # Determine overall status
    providers = result["providers"]
    if result["database"] == "error":
        result["status"] = "degraded"
    elif providers["enabled"] > 0 and providers["down"] == providers["enabled"]:
        # All enabled providers are down
        result["status"] = "degraded"

    return result
