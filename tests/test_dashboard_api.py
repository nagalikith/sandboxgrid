import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sandbox_api.dashboards.router import (
    _dashboard_path,
    load_dashboard_payload,
    save_dashboard_payload,
    build_dashboard_router,
)
from sandbox_api.sandboxes.models import SandboxRecord, SandboxStatus
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

    async def publish_job(self, job):
        if self.should_fail:
            raise RuntimeError("queue")
        self.jobs.append(job)


def _record(*, enable_dashboard=True, owner_id="user_a", artifacts_root=None):
    now = datetime.now(timezone.utc)
    return SandboxRecord(
        sandbox_id="sbx_1",
        status=SandboxStatus.ready,
        created_at=now,
        expires_at=now,
        browser_url=None,
        dashboard_url=None,
        events_url=None,
        message=None,
        owner_id=owner_id,
        cpu_limit="2",
        memory_limit_mb=1024,
        capabilities=["dashboard"] if enable_dashboard else [],
        allow_network=None,
        artifacts_path=str(artifacts_root) if artifacts_root else None,
    )


def _client(record, rabbit):
    app = FastAPI()
    app.include_router(build_dashboard_router(FakeOrchestrator(record), rabbit))
    return TestClient(app)


def _headers(method: str, path: str, body: bytes = b""):
    return build_internal_headers(method, path, body=body, user_id="user_a", content_type="application/json")


def test_dashboard_load_and_save(tmp_path):
    record = _record(artifacts_root=tmp_path)
    assert load_dashboard_payload(record) is None

    payload = {"title": "Demo"}
    save_dashboard_payload(record, payload)
    loaded = load_dashboard_payload(record)
    assert loaded
    assert loaded["title"] == "Demo"

    path = _dashboard_path(record)
    path.write_text("bad", encoding="utf-8")
    assert load_dashboard_payload(record) is None


def test_dashboard_endpoints_success(tmp_path):
    record = _record(artifacts_root=tmp_path)
    client = _client(record, FakeRabbit())

    response = client.get(
        "/sandboxes/sbx_1/dashboard",
        headers=_headers("GET", "/sandboxes/sbx_1/dashboard"),
    )
    assert response.status_code == 200

    response = client.get(
        "/sandboxes/sbx_1/dashboard/data",
        headers=_headers("GET", "/sandboxes/sbx_1/dashboard/data"),
    )
    assert response.status_code == 200
    assert response.json()["title"]

    body = json.dumps({"title": "New"}).encode("utf-8")
    response = client.post(
        "/sandboxes/sbx_1/dashboard/data",
        data=body,
        headers=_headers("POST", "/sandboxes/sbx_1/dashboard/data", body),
    )
    assert response.status_code == 202
    assert response.json()["updated_at"]


def test_dashboard_endpoints_errors(tmp_path):
    client = _client(None, FakeRabbit())
    response = client.get(
        "/sandboxes/sbx_1/dashboard/data",
        headers=_headers("GET", "/sandboxes/sbx_1/dashboard/data"),
    )
    assert response.status_code == 404

    client = _client(_record(owner_id="other", artifacts_root=tmp_path), FakeRabbit())
    response = client.get(
        "/sandboxes/sbx_1/dashboard/data",
        headers=_headers("GET", "/sandboxes/sbx_1/dashboard/data"),
    )
    assert response.status_code == 403

    client = _client(_record(enable_dashboard=False, artifacts_root=tmp_path), FakeRabbit())
    response = client.get(
        "/sandboxes/sbx_1/dashboard/data",
        headers=_headers("GET", "/sandboxes/sbx_1/dashboard/data"),
    )
    assert response.status_code == 404

    client = _client(_record(artifacts_root=tmp_path), FakeRabbit(should_fail=True))
    body = json.dumps({"title": "New"}).encode("utf-8")
    response = client.post(
        "/sandboxes/sbx_1/dashboard/data",
        data=body,
        headers=_headers("POST", "/sandboxes/sbx_1/dashboard/data", body),
    )
    assert response.status_code == 503
