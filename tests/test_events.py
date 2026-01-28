import asyncio

import pytest

from sandbox_api import events


@pytest.mark.asyncio
async def test_event_bus_publish_and_unsubscribe():
    bus = events.EventBus()
    queue, backlog = await bus.subscribe("sbx_1")
    assert backlog == []
    await bus.publish("sbx_1", {"type": "hello"})
    event = await asyncio.wait_for(queue.get(), timeout=1)
    assert event["type"] == "hello"
    assert event["sequence"] == 1

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


def test_sse_format_includes_id():
    payload = {"type": "status", "sequence": 42}
    formatted = events.sse_format(payload)
    assert formatted.startswith("id: 42\n")
    assert "data: " in formatted


@pytest.mark.asyncio
async def test_event_bus_replay_from_sequence():
    bus = events.EventBus()
    await bus.publish("sbx_1", {"type": "one"})
    await bus.publish("sbx_1", {"type": "two"})
    queue, backlog = await bus.subscribe("sbx_1", last_sequence=1)
    assert [event["type"] for event in backlog] == ["two"]
    await bus.unsubscribe("sbx_1", queue)
