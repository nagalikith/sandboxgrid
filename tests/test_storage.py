from datetime import datetime, timedelta, timezone

from sqlmodel import SQLModel, create_engine

from sandbox_api.sandboxes.models import SandboxRecord, SandboxStatus
from sandbox_api.sandboxes.storage import SandboxRepository


def _make_record() -> SandboxRecord:
    now = datetime.now(timezone.utc)
    return SandboxRecord(
        sandbox_id="sbx_test",
        status=SandboxStatus.requested,
        created_at=now,
        expires_at=now + timedelta(minutes=5),
        browser_url=None,
        dashboard_url=None,
        events_url=None,
        message="queued",
        owner_id="user_a",
        cpu_limit="2",
        memory_limit_mb=2048,
        capabilities=["browser"],
        allow_network=None,
    )


def test_storage_lifecycle():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    repo = SandboxRepository(engine)

    record = repo.create(_make_record())
    assert record.sandbox_id == "sbx_test"

    fetched = repo.get("sbx_test")
    assert fetched
    assert fetched.status == SandboxStatus.requested

    ready = repo.set_ready(
        "sbx_test",
        browser_url="http://browser",
        dashboard_url="http://dash",
        events_url="http://events",
        message="ok",
        backend_ref="local",
        http_port=1234,
        cdp_port=9222,
        artifacts_path="/tmp",
        cdp_url="http://cdp",
    )
    assert ready
    assert ready.status == SandboxStatus.ready
    assert str(ready.events_url).rstrip("/") == "http://events"

    updated = repo.set_status("sbx_test", status=SandboxStatus.provisioning, message="booting")
    assert updated
    assert updated.message == "booting"

    errored = repo.set_error("sbx_test", message="boom")
    assert errored
    assert errored.status == SandboxStatus.error

    terminated = repo.set_terminated("sbx_test")
    assert terminated
    assert terminated.status == SandboxStatus.terminated
