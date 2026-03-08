from __future__ import annotations

from typing import Any, Literal, Union

from pydantic import BaseModel, Field, root_validator

from ..sandboxes.command_models import (
    AgentStepsRequest,
    CaptureProfileRequest,
    RecordRequest,
    ReplayRequest,
    RunBrowserRequest,
    StepsRequest,
)
from ..dashboards.models import DashboardPayload
from ..apps.education.jobs import GradingJobRequest
from ..sandboxes.models import SandboxRequest

JOB_VERSION = 1


class JobBase(BaseModel):
    version: int = Field(default=JOB_VERSION, const=True)
    type: str


class ProvisionJob(JobBase):
    type: Literal["provision"]
    sandbox_id: str
    owner_id: str
    request: SandboxRequest


CommandPayload = Union[
    RunBrowserRequest,
    RecordRequest,
    ReplayRequest,
    StepsRequest,
    AgentStepsRequest,
    CaptureProfileRequest,
]


class CommandJob(JobBase):
    type: Literal["command"]
    sandbox_id: str
    owner_id: str
    command_id: str
    command: Literal["run_browser", "record", "replay", "steps", "agent", "capture_profile"]
    payload: CommandPayload

    @root_validator(pre=True)
    def coerce_payload(cls, values: dict[str, Any]) -> dict[str, Any]:
        command = values.get("command")
        payload = values.get("payload") or {}
        if command == "run_browser":
            values["payload"] = RunBrowserRequest.parse_obj(payload)
        elif command == "record":
            values["payload"] = RecordRequest.parse_obj(payload)
        elif command == "replay":
            values["payload"] = ReplayRequest.parse_obj(payload)
        elif command == "steps":
            values["payload"] = StepsRequest.parse_obj(payload)
        elif command == "agent":
            values["payload"] = AgentStepsRequest.parse_obj(payload)
        elif command == "capture_profile":
            values["payload"] = CaptureProfileRequest.parse_obj(payload)
        else:
            raise ValueError(f"Unsupported command type: {command}")
        return values


class DashboardUpdateJob(JobBase):
    type: Literal["dashboard_update"]
    sandbox_id: str
    owner_id: str
    payload: DashboardPayload


class GradingJob(JobBase):
    type: Literal["grading"]
    job_id: str
    owner_id: str
    payload: GradingJobRequest


Job = Union[ProvisionJob, CommandJob, DashboardUpdateJob, GradingJob]


def parse_job(data: dict[str, Any]) -> Job:
    job_type = data.get("type")
    if job_type == "provision":
        return ProvisionJob.parse_obj(data)
    if job_type == "command":
        return CommandJob.parse_obj(data)
    if job_type == "dashboard_update":
        return DashboardUpdateJob.parse_obj(data)
    if job_type == "grading":
        return GradingJob.parse_obj(data)
    raise ValueError(f"Unknown job type: {job_type}")
