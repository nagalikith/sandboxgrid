from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse, StreamingResponse

from .artifacts import router as artifacts_router
from .commands import build_commands_router
from .dashboard import build_dashboard_router
from .database import init_db, engine
from .events import event_bus, sse_format
from .jobs import ProvisionJob
from .models import SandboxRequest, SandboxResponse, sandbox_response_from_record
from .orchestrator import SandboxOrchestrator
from .rabbitmq import rabbitmq
from .storage import SandboxRepository


app = FastAPI(title="Sandbox API", version="0.1.0")
router = APIRouter(prefix="/sandboxes", tags=["sandboxes"])
repository = SandboxRepository(engine)
orchestrator = SandboxOrchestrator(repository=repository)
commands_router = build_commands_router(orchestrator, rabbitmq)
dashboard_router = build_dashboard_router(orchestrator, rabbitmq)
dashboard_router = build_dashboard_router(orchestrator)


async def get_agent_id(x_agent_id: str | None = Header(default=None)) -> str:
    """Resolve agent identity from header; placeholder until auth is wired."""
    return x_agent_id or "anonymous"


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=SandboxResponse,
    summary="Create a sandbox",
)
async def create_sandbox(
    request: SandboxRequest,
    agent_id: str = Depends(get_agent_id),
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
    agent_id: str = Depends(get_agent_id),
) -> SandboxResponse:
    record = await orchestrator.get(sandbox_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sandbox not found.")
    if record.owner_id != agent_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden.")
    return sandbox_response_from_record(record)


app.include_router(router)
app.include_router(commands_router)
app.include_router(artifacts_router)
app.include_router(dashboard_router)

_UI_PATH = Path(__file__).with_name("ui.html")


@app.get("/ui", response_class=HTMLResponse)
def sandbox_ui() -> HTMLResponse:
    return HTMLResponse(_UI_PATH.read_text(encoding="utf-8"))


@app.on_event("startup")
async def startup() -> None:
    init_db()

    async def handle_event(message) -> None:
        async with message.process():
            payload = json.loads(message.body.decode("utf-8"))
            sandbox_id = payload.get("sandbox_id")
            if sandbox_id:
                await event_bus.publish(sandbox_id, payload)

    await rabbitmq.connect()
    await rabbitmq.consume_events(handle_event)


@app.on_event("shutdown")
async def shutdown() -> None:
    await rabbitmq.close()


@app.get("/sandboxes/{sandbox_id}/events")
async def sandbox_events(sandbox_id: str, request: Request) -> StreamingResponse:
    queue = await event_bus.subscribe(sandbox_id)

    async def event_stream():
        try:
            # Send initial connected event
            yield sse_format(
                {"type": "connected", "sandbox_id": sandbox_id, "timestamp": datetime.now(timezone.utc).isoformat()}
            )
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield sse_format(event)
                except asyncio.TimeoutError:
                    # keep-alive
                    yield ":\n\n"
        finally:
            await event_bus.unsubscribe(sandbox_id, queue)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
