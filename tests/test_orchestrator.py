import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import SQLModel, create_engine

from sandbox_api.sandboxes.models import SandboxRequest, SandboxStatus
from sandbox_api.sandboxes.orchestrator import SandboxOrchestrator
from sandbox_api.sandboxes.provisioner import ProvisionResult
from sandbox_api.sandboxes.storage import SandboxRepository


class FakeProvisioner:
    def __init__(self, *, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.stopped = []

    async def provision(self, sandbox_id, request, *, owner_id):
        if self.should_fail:
            raise RuntimeError("boom")
        return ProvisionResult(
            status=SandboxStatus.ready,
            browser_url="http://browser",
            dashboard_url="http://dash",
            events_url="http://events",
            message="ready",
            backend_ref="local",
        )

    async def stop(self, sandbox_id, backend_ref):
        self.stopped.append((sandbox_id, backend_ref))
        if self.should_fail:
            raise RuntimeError("stop")

    def cdp_host(self):
        return "127.0.0.1"


@pytest.mark.asyncio
async def test_orchestrator_provision_and_get():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    repo = SandboxRepository(engine)
    orchestrator = SandboxOrchestrator(repository=repo, provisioner=FakeProvisioner())

    record = await orchestrator.provision(SandboxRequest(), owner_id="user_a")
    assert record.sandbox_id
    fetched = await orchestrator.get(record.sandbox_id)
    assert fetched


@pytest.mark.asyncio
async def test_orchestrator_provision_async_sets_ready(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    repo = SandboxRepository(engine)
    provisioner = FakeProvisioner()
    orchestrator = SandboxOrchestrator(repository=repo, provisioner=provisioner)
    record = await orchestrator.provision(SandboxRequest(), owner_id="user_a")

    await orchestrator._provision_async(record, SandboxRequest())
    updated = repo.get(record.sandbox_id)
    assert updated
    assert updated.status == SandboxStatus.ready


@pytest.mark.asyncio
async def test_orchestrator_provision_async_error_sets_failed():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    repo = SandboxRepository(engine)
    provisioner = FakeProvisioner(should_fail=True)
    orchestrator = SandboxOrchestrator(repository=repo, provisioner=provisioner)
    record = await orchestrator.provision(SandboxRequest(), owner_id="user_a")

    await orchestrator._provision_async(record, SandboxRequest())
    updated = repo.get(record.sandbox_id)
    assert updated
    assert updated.status == SandboxStatus.error


@pytest.mark.asyncio
async def test_orchestrator_enforce_ttl(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    repo = SandboxRepository(engine)
    provisioner = FakeProvisioner(should_fail=True)
    orchestrator = SandboxOrchestrator(repository=repo, provisioner=provisioner)

    record = await orchestrator.provision(SandboxRequest(ttl_seconds=60), owner_id="user_a")
    record = record.copy(update={"expires_at": datetime.now(timezone.utc)})
    repo.set_status(record.sandbox_id, status=SandboxStatus.ready)

    async def fake_sleep(_delay):
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    await orchestrator._enforce_ttl(record)
    updated = repo.get(record.sandbox_id)
    assert updated
    assert updated.status == SandboxStatus.terminated
