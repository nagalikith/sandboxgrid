from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from ...core.events import event_bus, sse_format
from ...core.events_models import AgentEventPayload
from ...core.jobs import ProvisionJob
from ...sandboxes.models import SandboxRequest, SandboxResponse, sandbox_response_from_record
from ...core.rabbitmq import rabbitmq
from ..dependencies import orchestrator, repository, require_internal_auth


router = APIRouter(prefix="/sandboxes", tags=["sandboxes"])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=SandboxResponse,
    summary="Create a sandbox",
)
async def create_sandbox(
    request: SandboxRequest,
    agent_id: str = Depends(require_internal_auth),
) -> SandboxResponse:
    record = await orchestrator.provision(request, owner_id=agent_id)
    try:
        await rabbitmq.publish_job(
            ProvisionJob(
                sandbox_id=record.sandbox_id,
                owner_id=agent_id,
                request=request,
            )
        )
    except Exception as exc:  # noqa: BLE001
        repository.set_error(record.sandbox_id, message=f"Queue error: {exc}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Queue unavailable. Try again later.",
        ) from exc
    return sandbox_response_from_record(record)


@router.get(
    "/{sandbox_id}",
    response_model=SandboxResponse,
    summary="Get sandbox status",
)
async def get_sandbox(
    sandbox_id: str,
    agent_id: str = Depends(require_internal_auth),
) -> SandboxResponse:
    record = await orchestrator.get(sandbox_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sandbox not found.")
    if record.owner_id != agent_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden.")
    return sandbox_response_from_record(record)


@router.get("/{sandbox_id}/events")
async def sandbox_events(
    sandbox_id: str,
    request: Request,
    max_events: int | None = Query(default=None, ge=1),
    agent_id: str = Depends(require_internal_auth),
) -> StreamingResponse:
    record = await orchestrator.get(sandbox_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sandbox not found.")
    if record.owner_id != agent_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden.")
    last_event_id = request.headers.get("last-event-id")
    last_sequence = None
    if last_event_id:
        try:
            last_sequence = int(last_event_id)
        except ValueError:
            last_sequence = None
    queue, backlog = event_bus.subscribe(sandbox_id, last_sequence=last_sequence)

    async def event_stream():
        emitted = 1  # the connected frame counts toward max_events
        try:
            yield sse_format(
                {"type": "connected", "sandbox_id": sandbox_id, "timestamp": datetime.now(timezone.utc).isoformat()}
            )
            if backlog:
                for event in backlog:
                    yield sse_format(event)
                    emitted += 1
                    if max_events is not None and emitted > max_events:
                        return
            while True:
                if max_events is not None and emitted >= max_events:
                    return
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield sse_format(event)
                    emitted += 1
                except asyncio.TimeoutError:
                    yield ":\n\n"
        finally:
            event_bus.unsubscribe(sandbox_id, queue)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/{sandbox_id}/events/agent", status_code=status.HTTP_202_ACCEPTED)
async def publish_agent_event(
    sandbox_id: str,
    payload: AgentEventPayload,
    agent_id: str = Depends(require_internal_auth),
) -> dict:
    record = await orchestrator.get(sandbox_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sandbox not found.")
    if record.owner_id != agent_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden.")
    event = payload.dict()
    event["sandbox_id"] = sandbox_id
    await rabbitmq.publish_event(event)
    return {"status": "queued"}
