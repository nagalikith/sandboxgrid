from __future__ import annotations

import uuid
import asyncio
from datetime import datetime, timezone
from enum import Enum
from typing import Callable

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, HttpUrl

from .events import event_bus
from .models import SandboxRecord, SandboxStatus
from .orchestrator import SandboxOrchestrator
from .storage import SandboxRepository

try:
    from run_artifact import (
        RuntimeConfig,
        record_session,
        replay_session,
        run_artifact as run_browser_artifact,
    )
except Exception as exc:  # noqa: BLE001
    raise RuntimeError(f"Failed to import run_artifact helpers: {exc}")


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


class RunBrowserRequest(BaseModel):
    url: HttpUrl
    interactive: bool = False


class RecordRequest(BaseModel):
    url: HttpUrl
    duration: int = 30
    interactive: bool = False


class ReplayRequest(BaseModel):
    session_id: str
    speed: float = 1.0
    interactive: bool = False


def build_commands_router(orchestrator: SandboxOrchestrator, repository: SandboxRepository) -> APIRouter:
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

    def build_runtime_config(record: SandboxRecord, log_emitter: Callable[[str], None] | None = None) -> RuntimeConfig:
        cdp_endpoint = record.cdp_url or (
            f"http://127.0.0.1:{record.cdp_port}" if record.cdp_port else None
        )
        artifacts_dir = record.artifacts_path or "/home/neko/artifacts"
        if not cdp_endpoint:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Missing CDP endpoint.")
        sessions_dir = f"{artifacts_dir}/sessions"
        log_file = f"{artifacts_dir}/agent.log"
        return RuntimeConfig(
            artifacts_dir=artifacts_dir,
            sessions_dir=sessions_dir,
            log_file=log_file,
            cdp_endpoint=cdp_endpoint,
            log_emitter=log_emitter,
        )

    async def _run_command(
        record: SandboxRecord,
        command_id: str,
        runner_fn,
        *,
        log_prefix: str,
        emit_started: bool = True,
    ) -> dict:
        started_at = datetime.now(timezone.utc)
        if emit_started:
            await event_bus.publish(
                record.sandbox_id,
                {
                    "type": "command_status",
                    "command_id": command_id,
                    "sandbox_id": record.sandbox_id,
                    "status": CommandStatus.running.value,
                    "message": f"{log_prefix} started",
                    "timestamp": started_at.isoformat(),
                },
            )

        def log_emitter(line: str) -> None:
            asyncio.create_task(
                event_bus.publish(
                    record.sandbox_id,
                    {
                        "type": "log",
                        "command_id": command_id,
                        "sandbox_id": record.sandbox_id,
                        "line": line,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                )
            )

        cfg = build_runtime_config(record, log_emitter=log_emitter)

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, runner_fn, cfg)
            await event_bus.publish(
                record.sandbox_id,
                {
                    "type": "command_status",
                    "command_id": command_id,
                    "sandbox_id": record.sandbox_id,
                    "status": CommandStatus.completed.value,
                    "message": f"{log_prefix} complete",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    **result,
                },
            )
            return result
        except Exception as exc:  # noqa: BLE001
            await event_bus.publish(
                record.sandbox_id,
                {
                    "type": "command_status",
                    "command_id": command_id,
                    "sandbox_id": record.sandbox_id,
                    "status": CommandStatus.failed.value,
                    "message": f"{log_prefix} failed: {exc}",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )
            raise

    @router.post("/run_browser", response_model=CommandReceipt, summary="Run a browser action set")
    async def run_browser(
        sandbox_id: str,
        payload: RunBrowserRequest,
        record: SandboxRecord = Depends(get_sandbox),
    ) -> CommandReceipt:
        command_id = f"cmd_{uuid.uuid4().hex[:8]}"
        def runner(cfg: RuntimeConfig):
            artifact_id = run_browser_artifact(cfg, str(payload.url), interactive=payload.interactive)
            return {"artifact_id": artifact_id}

        asyncio.create_task(
            _run_command(
                record,
                command_id,
                runner_fn=runner,
                log_prefix="Browser run",
            )
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
        def runner(cfg: RuntimeConfig):
            session_id = record_session(cfg, str(payload.url), duration=payload.duration, interactive=payload.interactive)
            return {"session_id": session_id}

        asyncio.create_task(
            _run_command(
                record,
                command_id,
                runner_fn=runner,
                log_prefix="Recording",
            )
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
        def runner(cfg: RuntimeConfig):
            replay_session(cfg, payload.session_id, speed=payload.speed, interactive=payload.interactive)
            return {"session_id": payload.session_id}

        asyncio.create_task(
            _run_command(
                record,
                command_id,
                runner_fn=runner,
                log_prefix="Replay",
            )
        )

        return CommandReceipt(
            command_id=command_id,
            sandbox_id=record.sandbox_id,
            status=CommandStatus.queued,
            started_at=datetime.now(timezone.utc),
            message="Replay queued.",
        )

    return router
