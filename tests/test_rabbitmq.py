import pytest

from sandbox_api.core.jobs import ProvisionJob
from sandbox_api.sandboxes.models import SandboxRequest
from sandbox_api.core.rabbitmq import DEFAULT_RABBITMQ_URL, RabbitMQ


class DummyExchange:
    def __init__(self) -> None:
        self.published = []

    async def publish(self, message, routing_key=""):
        self.published.append((message, routing_key))


class DummyQueue:
    def __init__(self) -> None:
        self.consumed = []
        self.bound = False

    async def consume(self, handler):
        self.consumed.append(handler)

    async def bind(self, exchange):
        self.bound = True


class DummyChannel:
    def __init__(self) -> None:
        self.default_exchange = DummyExchange()
        self.queue = DummyQueue()
        self.exchange = DummyExchange()
        self.qos = None

    async def set_qos(self, prefetch_count):
        self.qos = prefetch_count

    async def declare_queue(self, _name=None, durable=False, exclusive=False, auto_delete=False):
        return self.queue

    async def declare_exchange(self, _name, _type, durable=False):
        return self.exchange


class DummyConnection:
    def __init__(self) -> None:
        self.closed = False
        self.channel_obj = DummyChannel()

    async def channel(self):
        return self.channel_obj

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_rabbitmq_connect_publish_and_close(monkeypatch):
    dummy_connection = DummyConnection()

    async def fake_connect(_url):
        return dummy_connection

    monkeypatch.setattr("sandbox_api.rabbitmq.aio_pika.connect_robust", fake_connect)

    rabbit = RabbitMQ(url="amqp://test", queue_name="jobs", events_exchange="events", prefetch=2)
    await rabbit.connect()
    assert rabbit._channel
    assert rabbit._queue
    assert rabbit._events

    job = ProvisionJob(
        sandbox_id="sbx_1",
        owner_id="user_a",
        request=SandboxRequest(),
    )
    await rabbit.publish_job(job)
    assert dummy_connection.channel_obj.default_exchange.published

    await rabbit.publish_event({"type": "status"})
    assert dummy_connection.channel_obj.exchange.published

    await rabbit.consume_jobs(lambda _msg: None)
    assert dummy_connection.channel_obj.queue.consumed

    await rabbit.consume_events(lambda _msg: None)
    assert dummy_connection.channel_obj.queue.bound

    await rabbit.close()
    assert dummy_connection.closed


def test_rabbitmq_decode_job():
    rabbit = RabbitMQ()
    body = ProvisionJob(
        sandbox_id="sbx_1",
        owner_id="user_a",
        request=SandboxRequest(),
    ).json().encode("utf-8")
    job = rabbit.decode_job(body)
    assert job.sandbox_id == "sbx_1"


def test_rabbitmq_uses_local_compose_default_url(monkeypatch):
    monkeypatch.delenv("RABBITMQ_URL", raising=False)
    rabbit = RabbitMQ()
    assert rabbit.url == DEFAULT_RABBITMQ_URL
