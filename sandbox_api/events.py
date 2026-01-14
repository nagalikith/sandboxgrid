from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict, Set


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EventBus:
    """Simple in-memory pub/sub per sandbox."""

    def __init__(self) -> None:
        self._queues: Dict[str, Set[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()

    async def publish(self, sandbox_id: str, event: Dict[str, Any]) -> None:
        async with self._lock:
            queues = list(self._queues.get(sandbox_id, []))
        for q in queues:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Drop event if subscriber is too slow; SSE client can reconnect.
                pass

    async def subscribe(self, sandbox_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        async with self._lock:
            self._queues.setdefault(sandbox_id, set()).add(queue)
        return queue

    async def unsubscribe(self, sandbox_id: str, queue: asyncio.Queue) -> None:
        async with self._lock:
            subscribers = self._queues.get(sandbox_id)
            if not subscribers:
                return
            subscribers.discard(queue)
            if not subscribers:
                self._queues.pop(sandbox_id, None)


event_bus = EventBus()


def sse_format(event: Dict[str, Any]) -> str:
    return f"data: {json.dumps(event)}\n\n"
