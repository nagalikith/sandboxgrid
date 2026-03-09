from __future__ import annotations

import json
import os
from typing import Any, Awaitable, Callable, Optional

import aio_pika

from .jobs import JobBase, parse_job

DEFAULT_RABBITMQ_URL = "amqp://admin:change_me@localhost:5672/"


class RabbitMQ:
    def __init__(
        self,
        *,
        url: Optional[str] = None,
        queue_name: Optional[str] = None,
        events_exchange: Optional[str] = None,
        prefetch: Optional[int] = None,
    ) -> None:
        self.url = url or os.getenv("RABBITMQ_URL", DEFAULT_RABBITMQ_URL)
        self.queue_name = queue_name or os.getenv("RABBITMQ_QUEUE", "sandbox.jobs")
        self.events_exchange = events_exchange or os.getenv(
            "RABBITMQ_EVENTS_EXCHANGE", "sandbox.events"
        )
        self.prefetch = prefetch or int(os.getenv("RABBITMQ_PREFETCH", "1"))
        self._connection: Optional[aio_pika.RobustConnection] = None
        self._channel: Optional[aio_pika.abc.AbstractChannel] = None
        self._queue: Optional[aio_pika.abc.AbstractQueue] = None
        self._events: Optional[aio_pika.abc.AbstractExchange] = None

    async def connect(self) -> None:
        if self._connection:
            return
        self._connection = await aio_pika.connect_robust(self.url)
        self._channel = await self._connection.channel()
        await self._channel.set_qos(prefetch_count=self.prefetch)
        self._queue = await self._channel.declare_queue(self.queue_name, durable=True)
        self._events = await self._channel.declare_exchange(
            self.events_exchange,
            aio_pika.ExchangeType.FANOUT,
            durable=True,
        )

    async def close(self) -> None:
        if self._connection:
            await self._connection.close()
        self._connection = None
        self._channel = None
        self._queue = None
        self._events = None

    async def publish_job(self, job: JobBase) -> None:
        await self.connect()
        body = job.json(exclude_none=True, ensure_ascii=True).encode("utf-8")
        message = aio_pika.Message(body=body, delivery_mode=aio_pika.DeliveryMode.PERSISTENT)
        assert self._channel is not None
        await self._channel.default_exchange.publish(message, routing_key=self.queue_name)

    async def publish_event(self, payload: dict[str, Any]) -> None:
        await self.connect()
        body = json.dumps(payload).encode("utf-8")
        message = aio_pika.Message(body=body, delivery_mode=aio_pika.DeliveryMode.NOT_PERSISTENT)
        assert self._events is not None
        await self._events.publish(message, routing_key="")

    def decode_job(self, body: bytes) -> JobBase:
        data = json.loads(body.decode("utf-8"))
        return parse_job(data)

    async def consume_jobs(
        self,
        handler: Callable[[aio_pika.IncomingMessage], Awaitable[None]],
    ) -> None:
        await self.connect()
        assert self._queue is not None
        await self._queue.consume(handler)

    async def consume_events(
        self,
        handler: Callable[[aio_pika.IncomingMessage], Awaitable[None]],
    ) -> None:
        await self.connect()
        assert self._channel is not None
        assert self._events is not None
        queue = await self._channel.declare_queue(exclusive=True, auto_delete=True)
        await queue.bind(self._events)
        await queue.consume(handler)


rabbitmq = RabbitMQ()
