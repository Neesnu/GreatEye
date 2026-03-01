import asyncio
import time
from datetime import datetime
from typing import Any

import structlog

from src.providers.base import BaseProvider, HealthResult, HealthStatus
from src.providers.cache import mark_stale, write_cache
from src.providers.event_bus import Event, event_bus
from src.services.metrics import metrics_store

logger = structlog.get_logger()


class PollJob:
    """A single polling job for one instance+tier."""

    def __init__(
        self,
        instance_id: int,
        tier: str,
        interval_seconds: int,
        provider: BaseProvider,
    ) -> None:
        self.instance_id = instance_id
        self.tier = tier
        self.interval_seconds = interval_seconds
        self.provider = provider
        self._task: asyncio.Task[None] | None = None
        self._running = False

    async def start(self) -> None:
        """Start the polling loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.debug(
            "poll_job_started",
            instance_id=self.instance_id,
            tier=self.tier,
            interval=self.interval_seconds,
        )

    async def stop(self) -> None:
        """Stop the polling loop."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.debug(
            "poll_job_stopped",
            instance_id=self.instance_id,
            tier=self.tier,
        )

    async def _poll_loop(self) -> None:
        """Run the poll in a loop with the configured interval."""
        while self._running:
            try:
                await self._execute_poll()
            except Exception as e:
                logger.error(
                    "poll_unexpected_error",
                    instance_id=self.instance_id,
                    tier=self.tier,
                    error=str(e),
                )
            try:
                await asyncio.sleep(self.interval_seconds)
            except asyncio.CancelledError:
                break

    async def _execute_poll(self) -> None:
        """Execute a single poll for this tier."""
        type_id = self.provider.meta().type_id
        tags = {"provider_type": type_id, "instance_id": str(self.instance_id)}

        if self.tier == "health":
            timeout = 5.0
        elif self.tier == "summary":
            timeout = 10.0
        else:
            timeout = 15.0

        t0 = time.monotonic()
        now = datetime.utcnow()

        try:
            if self.tier == "health":
                result = await asyncio.wait_for(
                    self.provider.health_check(), timeout=timeout
                )
                duration_ms = (time.monotonic() - t0) * 1000
                await self._handle_health_result(result, type_id)
                await self._write_health_metrics(result, duration_ms, tags, now)
            elif self.tier == "summary":
                result = await asyncio.wait_for(
                    self.provider.get_summary(), timeout=timeout
                )
                duration_ms = (time.monotonic() - t0) * 1000
                await write_cache(
                    self.instance_id, "summary", result.data, result.fetched_at
                )
                await event_bus.publish(Event(
                    name=f"summary:{self.instance_id}",
                    data={"instance_id": self.instance_id, "tier": "summary"},
                ))
                await metrics_store.write(
                    "poll.summary.duration_ms", duration_ms, tags, now
                )
        except asyncio.TimeoutError:
            duration_ms = (time.monotonic() - t0) * 1000
            logger.warning(
                "poll_timeout",
                provider_type=type_id,
                instance_id=self.instance_id,
                tier=self.tier,
                timeout=timeout,
            )
            if self.tier == "health":
                await self._handle_health_result(
                    HealthResult(
                        status=HealthStatus.DOWN,
                        message="Health check timed out",
                    ),
                    type_id,
                )
                await self._write_health_metrics(
                    HealthResult(status=HealthStatus.DOWN, message="timeout"),
                    duration_ms, tags, now,
                )
            else:
                await mark_stale(self.instance_id, self.tier)
                await metrics_store.write(
                    "poll.summary.duration_ms", duration_ms, tags, now
                )

    async def _write_health_metrics(
        self,
        result: HealthResult,
        duration_ms: float,
        tags: dict[str, str],
        now: datetime,
    ) -> None:
        """Write health-related metrics after a health poll."""
        try:
            await metrics_store.write("poll.health.duration_ms", duration_ms, tags, now)
            health_value = 1.0 if result.status == HealthStatus.UP else 0.0
            await metrics_store.write("poll.health.status", health_value, tags, now)
        except Exception as e:
            logger.debug("metrics_write_failed", error=str(e))

    async def _handle_health_result(
        self, result: HealthResult, type_id: str
    ) -> None:
        """Process a health check result: update cache and state, publish event."""
        health_data = {
            "status": result.status.value,
            "message": result.message,
            "response_time_ms": result.response_time_ms,
        }
        await write_cache(
            self.instance_id, "health", health_data, datetime.utcnow()
        )
        await event_bus.publish(Event(
            name=f"health:{self.instance_id}",
            data={"instance_id": self.instance_id, "tier": "health", **health_data},
        ))
        logger.debug(
            "health_polled",
            provider_type=type_id,
            instance_id=self.instance_id,
            status=result.status.value,
            message=result.message,
        )


class Scheduler:
    """Manages polling jobs for all provider instances."""

    def __init__(self) -> None:
        self._jobs: dict[str, PollJob] = {}
        self._retention_task: asyncio.Task[None] | None = None
        self._running = False

    def _job_key(self, instance_id: int, tier: str) -> str:
        return f"{instance_id}:{tier}"

    async def schedule_instance(
        self,
        provider: BaseProvider,
        health_interval: int,
        summary_interval: int,
    ) -> None:
        """Schedule health and summary polling for a provider instance."""
        instance_id = provider.instance_id

        health_job = PollJob(instance_id, "health", health_interval, provider)
        summary_job = PollJob(instance_id, "summary", summary_interval, provider)

        self._jobs[self._job_key(instance_id, "health")] = health_job
        self._jobs[self._job_key(instance_id, "summary")] = summary_job

        await health_job.start()
        await summary_job.start()

        logger.info(
            "instance_scheduled",
            instance_id=instance_id,
            health_interval=health_interval,
            summary_interval=summary_interval,
        )

    async def unschedule_instance(self, instance_id: int) -> None:
        """Stop all polling jobs for an instance."""
        keys_to_remove = [
            k for k in self._jobs if k.startswith(f"{instance_id}:")
        ]
        for key in keys_to_remove:
            job = self._jobs.pop(key)
            await job.stop()

        logger.info("instance_unscheduled", instance_id=instance_id)

    async def start_retention_cleanup(self, retention_days: int) -> None:
        """Start a daily retention cleanup task for metrics."""
        self._running = True
        self._retention_task = asyncio.create_task(
            self._retention_loop(retention_days)
        )
        logger.info("retention_cleanup_started", retention_days=retention_days)

    async def _retention_loop(self, retention_days: int) -> None:
        """Run retention cleanup once per day."""
        from datetime import timedelta
        interval = 86400  # 24 hours in seconds
        while self._running:
            try:
                cutoff = datetime.utcnow() - timedelta(days=retention_days)
                deleted = await metrics_store.retention_cleanup(cutoff)
                logger.info(
                    "retention_cleanup_run",
                    retention_days=retention_days,
                    deleted=deleted,
                )
            except Exception as e:
                logger.error("retention_cleanup_error", error=str(e))
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break

    async def stop_all(self) -> None:
        """Stop all polling jobs and the retention cleanup task."""
        self._running = False
        if self._retention_task and not self._retention_task.done():
            self._retention_task.cancel()
            try:
                await self._retention_task
            except asyncio.CancelledError:
                pass
            self._retention_task = None
        for job in self._jobs.values():
            await job.stop()
        self._jobs.clear()
        logger.info("scheduler_stopped")

    @property
    def is_running(self) -> bool:
        """Check if the scheduler has any active jobs."""
        return len(self._jobs) > 0 or self._running

    @property
    def job_count(self) -> int:
        return len(self._jobs)


# Singleton instance
scheduler = Scheduler()
