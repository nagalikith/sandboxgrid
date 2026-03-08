from datetime import datetime, timezone

from sandbox_api.sandboxes.models import SandboxRecord, SandboxRequest, SandboxStatus, sandbox_response_from_record


def test_sandbox_request_defaults():
    req = SandboxRequest()
    assert req.ttl_seconds == 1800
    assert req.cpu_limit == "2"
    assert req.memory_limit_mb == 4096
    assert "browser" in req.capabilities


def test_sandbox_response_from_record():
    now = datetime.now(timezone.utc)
    record = SandboxRecord(
        sandbox_id="sbx_1",
        status=SandboxStatus.ready,
        created_at=now,
        expires_at=now,
        browser_url="http://browser",
        dashboard_url="http://dash",
        events_url="http://events",
        cdp_url="http://cdp",
        message="ok",
        owner_id="user_a",
        cpu_limit="2",
        memory_limit_mb=1024,
        capabilities=["browser"],
        allow_network=None,
    )
    response = sandbox_response_from_record(record)
    assert response.sandbox_id == "sbx_1"
    assert response.status == SandboxStatus.ready
    assert response.browser_url == record.browser_url
