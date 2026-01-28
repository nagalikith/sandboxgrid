import json
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sandbox_api.commands import build_commands_router
from sandbox_api.models import SandboxRecord, SandboxStatus
from tests.auth_helpers import build_internal_headers


class FakeOrchestrator:
    def __init__(self, record):
        self.record = record

    async def get(self, sandbox_id):
        return self.record


class FakeRabbit:
    def __init__(self, *, should_fail=False):
        self.should_fail = should_fail
        self.jobs = []
        self.events = []

    async def publish_job(self, job):
        if self.should_fail:
            raise RuntimeError("queue")
        self.jobs.append(job)

    async def publish_event(self, payload):
        if self.should_fail:
            raise RuntimeError("queue")
        self.events.append(payload)


def _record(status=SandboxStatus.ready, owner_id="user_a"):
    now = datetime.now(timezone.utc)
    return SandboxRecord(
        sandbox_id="sbx_1",
        status=status,
        created_at=now,
        expires_at=now,
        browser_url=None,
        dashboard_url=None,
        events_url=None,
        message=None,
        owner_id=owner_id,
        cpu_limit="2",
        memory_limit_mb=1024,
        capabilities=["browser", "dashboard"],
        allow_network=None,
    )


def _client(record, rabbit):
    app = FastAPI()
    app.include_router(build_commands_router(FakeOrchestrator(record), rabbit))
    return TestClient(app)


def _headers(method: str, path: str, body: bytes = b""):
    return build_internal_headers(method, path, body=body, user_id="user_a", content_type="application/json")


def test_commands_success():
    rabbit = FakeRabbit()
    client = _client(_record(), rabbit)

    payloads = [
        ("/sandboxes/sbx_1/commands/run_browser", {"url": "https://example.com"}),
        ("/sandboxes/sbx_1/commands/record", {"url": "https://example.com"}),
        ("/sandboxes/sbx_1/commands/replay", {"session_id": "sess_1"}),
        ("/sandboxes/sbx_1/commands/steps", {"steps": [{"action": "page_state"}]}),
        ("/sandboxes/sbx_1/commands/agent", {"task": "Do", "steps": [{"action": "page_state"}]}),
        ("/sandboxes/sbx_1/commands/capture_profile", {"name": "profile"}),
    ]

    for path, payload in payloads:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        response = client.post(path, data=body, headers=_headers("POST", path, body))
        assert response.status_code == 200
        assert response.json()["status"] == "queued"

    assert rabbit.jobs
    assert rabbit.events


def test_commands_not_found_and_forbidden():
    client = _client(None, FakeRabbit())
    body = json.dumps({"url": "https://example.com"}).encode("utf-8")
    response = client.post(
        "/sandboxes/sbx_1/commands/run_browser",
        data=body,
        headers=_headers("POST", "/sandboxes/sbx_1/commands/run_browser", body),
    )
    assert response.status_code == 404

    client = _client(_record(owner_id="other"), FakeRabbit())
    response = client.post(
        "/sandboxes/sbx_1/commands/run_browser",
        data=body,
        headers=_headers("POST", "/sandboxes/sbx_1/commands/run_browser", body),
    )
    assert response.status_code == 403


def test_commands_not_ready():
    client = _client(_record(status=SandboxStatus.requested), FakeRabbit())
    body = json.dumps({"url": "https://example.com"}).encode("utf-8")
    response = client.post(
        "/sandboxes/sbx_1/commands/run_browser",
        data=body,
        headers=_headers("POST", "/sandboxes/sbx_1/commands/run_browser", body),
    )
    assert response.status_code == 409


def test_commands_queue_failure():
    client = _client(_record(), FakeRabbit(should_fail=True))
    body = json.dumps({"url": "https://example.com"}).encode("utf-8")
    response = client.post(
        "/sandboxes/sbx_1/commands/run_browser",
        data=body,
        headers=_headers("POST", "/sandboxes/sbx_1/commands/run_browser", body),
    )
    assert response.status_code == 503
