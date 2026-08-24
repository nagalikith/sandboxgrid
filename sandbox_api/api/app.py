from __future__ import annotations

import asyncio
import json
import logging
import os

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from ..artifacts import router as artifacts_router
from ..sandboxes.commands import build_commands_router
from ..dashboards.router import build_dashboard_router
from ..core.database import init_db
from ..core.rabbitmq import rabbitmq
from ..share_session import router as share_session_router
from ..web.templates import load_template_text
from .dependencies import orchestrator
from .routes.sandboxes import router as sandboxes_router


logger = logging.getLogger("sandbox.api")


def _rabbitmq_connect_timeout_seconds() -> float:
    raw = os.getenv("RABBITMQ_CONNECT_TIMEOUT_SECONDS", "3")
    try:
        return max(float(raw), 0.1)
    except ValueError:
        return 3.0


def create_app() -> FastAPI:
    app = FastAPI(title="Sandbox API", version="0.1.0")
    app.include_router(sandboxes_router)
    app.include_router(build_commands_router(orchestrator, rabbitmq))
    app.include_router(artifacts_router)
    app.include_router(build_dashboard_router(orchestrator, rabbitmq))
    app.include_router(share_session_router)

    @app.get("/ui", response_class=HTMLResponse)
    def sandbox_ui() -> HTMLResponse:
        return HTMLResponse(load_template_text("ui.html"))

    @app.get("/chat-ui", response_class=HTMLResponse)
    def chat_ui() -> HTMLResponse:
        return HTMLResponse(load_template_text("chat_ui.html"))

    @app.on_event("startup")
    async def startup() -> None:
        init_db()

        async def handle_event(message) -> None:
            from ..core.events import event_bus

            async with message.process():
                payload = json.loads(message.body.decode("utf-8"))
                sandbox_id = payload.get("sandbox_id")
                if sandbox_id:
                    event_bus.publish(sandbox_id, payload)

        try:
            timeout = _rabbitmq_connect_timeout_seconds()
            await asyncio.wait_for(rabbitmq.connect(), timeout=timeout)
            await asyncio.wait_for(rabbitmq.consume_events(handle_event), timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            logger.warning("RabbitMQ unavailable during startup; continuing without event consumer: %s", exc)

    @app.on_event("shutdown")
    async def shutdown() -> None:
        await rabbitmq.close()

    return app


app = create_app()
