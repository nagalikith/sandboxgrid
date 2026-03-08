from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse

from .models import DashboardPayload
from ..core.internal_auth import internal_auth_dependency
from ..core.jobs import DashboardUpdateJob
from ..sandboxes.models import SandboxRecord
from ..sandboxes.orchestrator import SandboxOrchestrator
from ..core.rabbitmq import RabbitMQ
from ..web.templates import load_template_text


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dashboard_dir(record: SandboxRecord) -> Path:
    root = record.artifacts_path or os.getenv("SANDBOX_ARTIFACTS_ROOT", "./artifacts")
    base = Path(root).expanduser()
    directory = base / "dashboards"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _dashboard_path(record: SandboxRecord) -> Path:
    return _dashboard_dir(record) / f"{record.sandbox_id}.json"


def load_dashboard_payload(record: SandboxRecord) -> Optional[dict[str, Any]]:
    path = _dashboard_path(record)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def save_dashboard_payload(record: SandboxRecord, payload: dict[str, Any]) -> None:
    path = _dashboard_path(record)
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def build_dashboard_router(orchestrator: SandboxOrchestrator, rabbit: RabbitMQ) -> APIRouter:
    router = APIRouter(prefix="/sandboxes/{sandbox_id}/dashboard", tags=["dashboard"])
    require_internal_auth = internal_auth_dependency()

    async def get_sandbox(
        sandbox_id: str,
        agent_id: str = Depends(require_internal_auth),
    ) -> SandboxRecord:
        record = await orchestrator.get(sandbox_id)
        if not record:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sandbox not found.")
        if record.owner_id != agent_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden.")
        if "dashboard" not in record.capabilities:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dashboard not enabled.")
        return record

    @router.get("", response_class=HTMLResponse)
    async def dashboard_page(  # noqa: ARG001
        sandbox_id: str,
        _record: SandboxRecord = Depends(get_sandbox),
    ) -> HTMLResponse:
        return HTMLResponse(load_template_text("dashboard.html"))

    @router.get("/data", response_model=DashboardPayload)
    async def get_dashboard_data(
        sandbox_id: str,
        record: SandboxRecord = Depends(get_sandbox),
    ) -> DashboardPayload:
        data = load_dashboard_payload(record)
        if not data:
            return DashboardPayload(
                title=f"Sandbox {sandbox_id}",
                subtitle="Waiting for data",
                updated_at=_now_iso(),
            )
        return DashboardPayload.parse_obj(data)

    @router.post("/data", response_model=DashboardPayload, status_code=status.HTTP_202_ACCEPTED)
    async def set_dashboard_data(
        sandbox_id: str,
        payload: DashboardPayload,
        record: SandboxRecord = Depends(get_sandbox),
    ) -> DashboardPayload:
        if not payload.updated_at:
            payload = payload.copy(update={"updated_at": _now_iso()})
        data = payload.dict()
        try:
            await rabbit.publish_job(
                DashboardUpdateJob(
                    sandbox_id=sandbox_id,
                    owner_id=record.owner_id,
                    payload=DashboardPayload.parse_obj(data),
                )
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Queue unavailable. Try again later.",
            ) from exc
        return payload

    return router
