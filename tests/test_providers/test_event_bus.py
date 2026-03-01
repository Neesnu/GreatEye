"""Tests for the in-memory event bus."""
import asyncio

import pytest

from src.providers.event_bus import Event, EventBus


@pytest.fixture
def bus():
    """Create a fresh EventBus for each test."""
    return EventBus()


class TestEventBus:
    @pytest.mark.asyncio
    async def test_subscribe(self, bus):
        sub_id, queue = await bus.subscribe()
        assert sub_id >= 0
        assert isinstance(queue, asyncio.Queue)
        assert bus.subscriber_count == 1

    @pytest.mark.asyncio
    async def test_unsubscribe(self, bus):
        sub_id, _ = await bus.subscribe()
        await bus.unsubscribe(sub_id)
        assert bus.subscriber_count == 0

    @pytest.mark.asyncio
    async def test_unsubscribe_nonexistent(self, bus):
        await bus.unsubscribe(999)  # Should not raise

    @pytest.mark.asyncio
    async def test_publish_received(self, bus):
        _, queue = await bus.subscribe()
        await bus.publish(Event(name="test", data={"key": "value"}))
        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event.name == "test"
        assert event.data["key"] == "value"
        assert event.timestamp is not None

    @pytest.mark.asyncio
    async def test_publish_fanout(self, bus):
        _, q1 = await bus.subscribe()
        _, q2 = await bus.subscribe()
        await bus.publish(Event(name="broadcast", data={}))
        e1 = await asyncio.wait_for(q1.get(), timeout=1.0)
        e2 = await asyncio.wait_for(q2.get(), timeout=1.0)
        assert e1.name == "broadcast"
        assert e2.name == "broadcast"

    @pytest.mark.asyncio
    async def test_publish_no_subscribers(self, bus):
        await bus.publish(Event(name="orphan", data={}))  # Should not raise

    @pytest.mark.asyncio
    async def test_publish_drops_when_full(self, bus):
        _, queue = await bus.subscribe()
        # Fill the queue to max (100)
        for i in range(100):
            await bus.publish(Event(name=f"fill-{i}", data={}))
        # This one should be dropped, not raise
        await bus.publish(Event(name="overflow", data={}))
        assert queue.qsize() == 100
