import pytest

from sandbox_api.jobs import CommandJob, DashboardUpdateJob, ProvisionJob, parse_job
from sandbox_api.models import SandboxRequest


def test_parse_job_provision():
    job = parse_job(
        {
            "type": "provision",
            "sandbox_id": "sbx_1",
            "owner_id": "user_a",
            "request": SandboxRequest().dict(),
        }
    )
    assert isinstance(job, ProvisionJob)
    assert job.sandbox_id == "sbx_1"


def test_parse_job_command_payloads():
    job = CommandJob.parse_obj(
        {
            "type": "command",
            "sandbox_id": "sbx_2",
            "owner_id": "user_a",
            "command_id": "cmd_1",
            "command": "run_browser",
            "payload": {"url": "https://example.com"},
        }
    )
    assert job.command == "run_browser"
    assert job.payload.url

    with pytest.raises(ValueError):
        CommandJob.parse_obj(
            {
                "type": "command",
                "sandbox_id": "sbx_2",
                "owner_id": "user_a",
                "command_id": "cmd_1",
                "command": "unknown",
                "payload": {},
            }
        )


def test_parse_job_dashboard_update():
    job = parse_job(
        {
            "type": "dashboard_update",
            "sandbox_id": "sbx_3",
            "owner_id": "user_a",
            "payload": {"title": "demo"},
        }
    )
    assert isinstance(job, DashboardUpdateJob)


def test_parse_job_unknown_type():
    with pytest.raises(ValueError):
        parse_job({"type": "other"})
