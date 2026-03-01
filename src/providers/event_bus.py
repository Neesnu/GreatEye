import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog

logger = structlog.get_logger()


@dataclass
class Event:
    name: str
    data: dict[str, Any]
    timestamp: datetime = field(default_factory=datetime.utcnow)


class EventBus:
    """In-memory pub/sub event bus with per-connection asyncio.Queues.

    SSE connections subscribe via subscribe(), receiving an asyncio.Queue.
    The scheduler publishes events via publish(), which fans out to all
    connected queues. Per H3, this decouples SSE from SQLite — no long-lived
    DB transactions.
    """

    def __init__(self) -> None:
        self._subscribers: dict[int, asyncio.Queue[Event]] = {}
        self._next_id: int = 0
        self._lock: asyncio.Lock = asyncio.Lock()

    async def subscribe(self) -> tuple[int, asyncio.Queue[Event]]:
        """Create a new subscription. Returns (subscriber_id, queue)."""
        async with self._lock:
            sub_id = self._next_id
            self._next_id += 1
            queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=100)
            self._subscribers[sub_id] = queue

        logger.debug("event_bus_subscribe", subscriber_id=sub_id)
        return sub_id, queue

    async def unsubscribe(self, subscriber_id: int) -> None:
        """Remove a subscription."""
        async with self._lock:
            self._subscribers.pop(subscriber_id, None)

        logger.debug("event_bus_unsubscribe", subscriber_id=subscriber_id)

    async def publish(self, event: Event) -> None:
        """Publish an event to all subscribers. Non-blocking — drops if queue is full."""
        async with self._lock:
            subscribers = list(self._subscribers.items())

        dropped = 0
        for sub_id, queue in subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                dropped += 1

        if dropped:
            logger.warning(
                "event_bus_dropped",
                event_name=event.name,
                dropped_count=dropped,
                total_subscribers=len(subscribers),
            )

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


# Singleton instance
event_bus = EventBus()
