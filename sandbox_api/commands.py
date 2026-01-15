from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel

from .command_models import RecordRequest, ReplayRequest, RunBrowserRequest
from .jobs import CommandJob
from .models import SandboxRecord, SandboxStatus
from .orchestrator import SandboxOrchestrator
from .rabbitmq import RabbitMQ


class CommandStatus(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


class CommandReceipt(BaseModel):
    command_id: str
    sandbox_id: str
    status: CommandStatus
    started_at: datetime
    completed_at: datetime | None = None
    artifact_id: str | None = None
    session_id: str | None = None
    message: str | None = None

    class Config:
        use_enum_values = True


def build_commands_router(orchestrator: SandboxOrchestrator, rabbit: RabbitMQ) -> APIRouter:
    router = APIRouter(prefix="/sandboxes/{sandbox_id}/commands", tags=["commands"])

    async def get_agent_id(x_agent_id: str | None = Header(default=None)) -> str:
        return x_agent_id or "anonymous"

    async def get_sandbox(
        sandbox_id: str,
        agent_id: str = Depends(get_agent_id),
    ) -> SandboxRecord:
        record = await orchestrator.get(sandbox_id)
        if not record:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sandbox not found.")
        if record.owner_id != agent_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden.")
        if record.status != SandboxStatus.ready:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Sandbox not ready (status={record.status}).",
            )
        return record

    async def enqueue_command(
        record: SandboxRecord,
        command_id: str,
        command: str,
        payload: dict,
        message: str,
    ) -> None:
        try:
            await rabbit.publish_job(
                CommandJob(
                    sandbox_id=record.sandbox_id,
                    owner_id=record.owner_id,
                    command_id=command_id,
                    command=command,
                    payload=payload,
                )
            )
            await rabbit.publish_event(
                {
                    "type": "command_status",
                    "command_id": command_id,
                    "sandbox_id": record.sandbox_id,
                    "status": CommandStatus.queued.value,
                    "message": message,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Queue unavailable. Try again later.",
            ) from exc

    @router.post("/run_browser", response_model=CommandReceipt, summary="Run a browser action set")
    async def run_browser(
        sandbox_id: str,
        payload: RunBrowserRequest,
        record: SandboxRecord = Depends(get_sandbox),
    ) -> CommandReceipt:
        command_id = f"cmd_{uuid.uuid4().hex[:8]}"
        await enqueue_command(
            record,
            command_id,
            "run_browser",
            payload.dict(),
            "Browser run queued.",
        )

        return CommandReceipt(
            command_id=command_id,
            sandbox_id=record.sandbox_id,
            status=CommandStatus.queued,
            started_at=datetime.now(timezone.utc),
            message="Browser run queued.",
        )

    @router.post("/record", response_model=CommandReceipt, summary="Record a session")
    async def record(
        sandbox_id: str,
        payload: RecordRequest,
        record: SandboxRecord = Depends(get_sandbox),
    ) -> CommandReceipt:
        command_id = f"cmd_{uuid.uuid4().hex[:8]}"
        await enqueue_command(
            record,
            command_id,
            "record",
            payload.dict(),
            "Recording queued.",
        )

        return CommandReceipt(
            command_id=command_id,
            sandbox_id=record.sandbox_id,
            status=CommandStatus.queued,
            started_at=datetime.now(timezone.utc),
            message="Recording queued.",
        )

    @router.post("/replay", response_model=CommandReceipt, summary="Replay a session")
    async def replay(
        sandbox_id: str,
        payload: ReplayRequest,
        record: SandboxRecord = Depends(get_sandbox),
    ) -> CommandReceipt:
        command_id = f"cmd_{uuid.uuid4().hex[:8]}"
        await enqueue_command(
            record,
            command_id,
            "replay",
            payload.dict(),
            "Replay queued.",
        )

        return CommandReceipt(
            command_id=command_id,
            sandbox_id=record.sandbox_id,
            status=CommandStatus.queued,
            started_at=datetime.now(timezone.utc),
            message="Replay queued.",
        )

    return router
