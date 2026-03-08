from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timezone
import json
import os
from typing import Any, Deque, Dict, List, Set


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


DEFAULT_EVENT_BUFFER_SIZE = int(os.getenv("SANDBOX_EVENT_BUFFER_SIZE", "500"))


class EventBus:
    """Simple in-memory pub/sub per sandbox with replay buffer."""

    def __init__(self, *, buffer_size: int = DEFAULT_EVENT_BUFFER_SIZE) -> None:
        self._queues: Dict[str, Set[asyncio.Queue]] = {}
        self._buffers: Dict[str, Deque[Dict[str, Any]]] = {}
        self._sequences: Dict[str, int] = {}
        self._lock = asyncio.Lock()
        self._buffer_size = max(buffer_size, 0)

    async def publish(self, sandbox_id: str, event: Dict[str, Any]) -> None:
        async with self._lock:
            event_with_sequence = dict(event)
            sequence = event_with_sequence.get("sequence")
            if sequence is None:
                sequence = self._sequences.get(sandbox_id, 0) + 1
                event_with_sequence["sequence"] = sequence
            else:
                try:
                    sequence = int(sequence)
                    event_with_sequence["sequence"] = sequence
                except (TypeError, ValueError):
                    sequence = self._sequences.get(sandbox_id, 0) + 1
                    event_with_sequence["sequence"] = sequence
            self._sequences[sandbox_id] = max(self._sequences.get(sandbox_id, 0), int(sequence))
            if self._buffer_size > 0:
                buffer = self._buffers.setdefault(sandbox_id, deque(maxlen=self._buffer_size))
                buffer.append(event_with_sequence)
            queues = list(self._queues.get(sandbox_id, []))
        for q in queues:
            try:
                q.put_nowait(event_with_sequence)
            except asyncio.QueueFull:
                # Drop event if subscriber is too slow; SSE client can reconnect.
                pass

    async def subscribe(
        self,
        sandbox_id: str,
        *,
        last_sequence: int | None = None,
    ) -> tuple[asyncio.Queue, List[Dict[str, Any]]]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        async with self._lock:
            backlog: List[Dict[str, Any]] = []
            if last_sequence is not None:
                buffer = self._buffers.get(sandbox_id)
                if buffer:
                    backlog = [event for event in buffer if _event_sequence(event) > last_sequence]
            self._queues.setdefault(sandbox_id, set()).add(queue)
        return queue, backlog

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
    payload = json.dumps(event)
    sequence = event.get("sequence")
    if sequence is None:
        return f"data: {payload}\n\n"
    sequence_text = str(sequence).replace("\n", "")
    return f"id: {sequence_text}\ndata: {payload}\n\n"


def _event_sequence(event: Dict[str, Any]) -> int:
    sequence = event.get("sequence", 0)
    try:
        return int(sequence)
    except (TypeError, ValueError):
        return 0
