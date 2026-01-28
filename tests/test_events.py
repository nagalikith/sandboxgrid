import asyncio

import pytest

from sandbox_api import events


@pytest.mark.asyncio
async def test_event_bus_publish_and_unsubscribe():
    bus = events.EventBus()
    queue = await bus.subscribe("sbx_1")
    await bus.publish("sbx_1", {"type": "hello"})
    event = await asyncio.wait_for(queue.get(), timeout=1)
    assert event == {"type": "hello"}

    await bus.unsubscribe("sbx_1", queue)
    await bus.publish("sbx_1", {"type": "ignored"})
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(queue.get(), timeout=0.1)


@pytest.mark.asyncio
async def test_event_bus_unsubscribe_missing_queue():
    bus = events.EventBus()
    queue = asyncio.Queue()
    await bus.unsubscribe("sbx_missing", queue)


def test_sse_format():
    payload = {"type": "status", "message": "ok"}
    formatted = events.sse_format(payload)
    assert formatted.startswith("data: ")
    assert formatted.endswith("\n\n")
