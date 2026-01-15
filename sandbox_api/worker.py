from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from .dashboard import save_dashboard_payload
from .database import init_db, engine
from .jobs import CommandJob, DashboardUpdateJob, ProvisionJob, parse_job
from .models import SandboxRecord, SandboxStatus
from .provisioner import build_default_provisioner
from .rabbitmq import RabbitMQ
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


logger = logging.getLogger("sandbox.worker")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_runtime_config(
    record: SandboxRecord,
    log_emitter=None,
) -> RuntimeConfig:
    cdp_endpoint = record.cdp_url or (f"http://127.0.0.1:{record.cdp_port}" if record.cdp_port else None)
    artifacts_dir = record.artifacts_path or "/home/neko/artifacts"
    if not cdp_endpoint:
        raise RuntimeError("Missing CDP endpoint.")
    sessions_dir = f"{artifacts_dir}/sessions"
    log_file = f"{artifacts_dir}/agent.log"
    return RuntimeConfig(
        artifacts_dir=artifacts_dir,
        sessions_dir=sessions_dir,
        log_file=log_file,
        cdp_endpoint=cdp_endpoint,
        log_emitter=log_emitter,
    )


async def publish_event(rabbit: RabbitMQ, payload: dict[str, Any]) -> None:
    try:
        await rabbit.publish_event(payload)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to publish event")


async def enforce_ttl(
    repository: SandboxRepository,
    provisioner,
    rabbit: RabbitMQ,
    sandbox_id: str,
) -> None:
    record = repository.get(sandbox_id)
    if not record:
        return
    now = datetime.now(timezone.utc)
    delay = max((record.expires_at - now).total_seconds(), 0)
    await asyncio.sleep(delay)
    latest = repository.get(sandbox_id)
    if not latest or latest.status == SandboxStatus.terminated:
        return
    try:
        await provisioner.stop(latest.sandbox_id, latest.backend_ref)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to stop sandbox %s", latest.sandbox_id)
    repository.set_terminated(latest.sandbox_id, message="Sandbox expired.")
    await publish_event(
        rabbit,
        {
            "type": "sandbox_status",
            "sandbox_id": latest.sandbox_id,
            "status": SandboxStatus.terminated.value,
            "timestamp": _now_iso(),
            "message": "Sandbox expired.",
        },
    )


async def handle_provision(
    job: ProvisionJob,
    repository: SandboxRepository,
    provisioner,
    rabbit: RabbitMQ,
) -> None:
    record = repository.get(job.sandbox_id)
    if not record:
        logger.error("Sandbox record not found for %s", job.sandbox_id)
        return

    repository.set_status(
        job.sandbox_id,
        status=SandboxStatus.provisioning,
        message="Provisioning sandbox.",
    )
    await publish_event(
        rabbit,
        {
            "type": "sandbox_status",
            "sandbox_id": job.sandbox_id,
            "status": SandboxStatus.provisioning.value,
            "timestamp": _now_iso(),
            "message": "Provisioning sandbox.",
        },
    )
    asyncio.create_task(enforce_ttl(repository, provisioner, rabbit, job.sandbox_id))

    try:
        result = await provisioner.provision(job.sandbox_id, job.request, owner_id=job.owner_id)
        repository.set_ready(
            job.sandbox_id,
            browser_url=result.browser_url,
            dashboard_url=result.dashboard_url,
            events_url=result.events_url,
            message=result.message,
            backend_ref=result.backend_ref,
            http_port=result.http_port,
            cdp_port=result.cdp_port,
            artifacts_path=result.artifacts_path,
            cdp_url=result.cdp_url,
        )
        await publish_event(
            rabbit,
            {
                "type": "sandbox_status",
                "sandbox_id": job.sandbox_id,
                "status": SandboxStatus.ready.value,
                "timestamp": _now_iso(),
                "message": result.message,
                "browser_url": result.browser_url,
                "dashboard_url": result.dashboard_url,
                "events_url": result.events_url,
                "cdp_url": result.cdp_url,
            },
        )
    except Exception as exc:  # noqa: BLE001
        repository.set_error(job.sandbox_id, message=f"Provisioning failed: {exc}")
        await publish_event(
            rabbit,
            {
                "type": "sandbox_status",
                "sandbox_id": job.sandbox_id,
                "status": SandboxStatus.error.value,
                "timestamp": _now_iso(),
                "message": f"Provisioning failed: {exc}",
            },
        )


async def handle_command(
    job: CommandJob,
    repository: SandboxRepository,
    rabbit: RabbitMQ,
) -> None:
    record = repository.get(job.sandbox_id)
    if not record:
        await publish_event(
            rabbit,
            {
                "type": "command_status",
                "command_id": job.command_id,
                "sandbox_id": job.sandbox_id,
                "status": "failed",
                "timestamp": _now_iso(),
                "message": "Sandbox not found.",
            },
        )
        return
    if record.status != SandboxStatus.ready:
        await publish_event(
            rabbit,
            {
                "type": "command_status",
                "command_id": job.command_id,
                "sandbox_id": job.sandbox_id,
                "status": "failed",
                "timestamp": _now_iso(),
                "message": f"Sandbox not ready (status={record.status}).",
            },
        )
        return

    loop = asyncio.get_running_loop()

    def log_emitter(line: str) -> None:
        asyncio.run_coroutine_threadsafe(
            publish_event(
                rabbit,
                {
                    "type": "log",
                    "command_id": job.command_id,
                    "sandbox_id": job.sandbox_id,
                    "line": line,
                    "timestamp": _now_iso(),
                },
            ),
            loop,
        )

    await publish_event(
        rabbit,
        {
            "type": "command_status",
            "command_id": job.command_id,
            "sandbox_id": job.sandbox_id,
            "status": "running",
            "timestamp": _now_iso(),
            "message": f"{job.command} started",
        },
    )

    try:
        cfg = build_runtime_config(record, log_emitter=log_emitter)

        def runner() -> dict[str, Any]:
            if job.command == "run_browser":
                artifact_id = run_browser_artifact(
                    cfg,
                    str(job.payload.url),
                    interactive=bool(job.payload.interactive),
                )
                return {"artifact_id": artifact_id}
            if job.command == "record":
                session_id = record_session(
                    cfg,
                    str(job.payload.url),
                    duration=int(job.payload.duration),
                    interactive=bool(job.payload.interactive),
                )
                return {"session_id": session_id}
            if job.command == "replay":
                replay_session(
                    cfg,
                    str(job.payload.session_id),
                    speed=float(job.payload.speed),
                    interactive=bool(job.payload.interactive),
                )
                return {"session_id": job.payload.session_id}
            raise RuntimeError(f"Unknown command: {job.command}")

        result = await loop.run_in_executor(None, runner)
        await publish_event(
            rabbit,
            {
                "type": "command_status",
                "command_id": job.command_id,
                "sandbox_id": job.sandbox_id,
                "status": "completed",
                "timestamp": _now_iso(),
                "message": f"{job.command} complete",
                **result,
            },
        )
    except Exception as exc:  # noqa: BLE001
        await publish_event(
            rabbit,
            {
                "type": "command_status",
                "command_id": job.command_id,
                "sandbox_id": job.sandbox_id,
                "status": "failed",
                "timestamp": _now_iso(),
                "message": f"{job.command} failed: {exc}",
            },
        )


async def handle_dashboard_update(
    job: DashboardUpdateJob,
    repository: SandboxRepository,
    rabbit: RabbitMQ,
) -> None:
    record = repository.get(job.sandbox_id)
    if not record:
        logger.error("Dashboard update sandbox not found: %s", job.sandbox_id)
        return
    if "dashboard" not in record.capabilities:
        logger.error("Dashboard not enabled for %s", job.sandbox_id)
        return

    payload_model = job.payload
    if not payload_model.updated_at:
        payload_model = payload_model.copy(update={"updated_at": _now_iso()})
    data = payload_model.dict()
    save_dashboard_payload(record, data)
    await publish_event(
        rabbit,
        {
            "type": "dashboard_data",
            "sandbox_id": job.sandbox_id,
            "timestamp": _now_iso(),
            "payload": data,
        },
    )


async def handle_job(
    message,
    repository: SandboxRepository,
    provisioner,
    rabbit: RabbitMQ,
) -> None:
    async with message.process():
        try:
            job = parse_job(json.loads(message.body.decode("utf-8")))
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to parse job: %s", exc)
            return
        if isinstance(job, ProvisionJob):
            await handle_provision(job, repository, provisioner, rabbit)
            return
        if isinstance(job, CommandJob):
            await handle_command(job, repository, rabbit)
            return
        if isinstance(job, DashboardUpdateJob):
            await handle_dashboard_update(job, repository, rabbit)
            return
        logger.error("Unhandled job type: %s", job.type)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    init_db()
    repository = SandboxRepository(engine)
    provisioner = build_default_provisioner()
    rabbit = RabbitMQ()
    await rabbit.connect()

    async def handler(message) -> None:
        await handle_job(message, repository, provisioner, rabbit)

    await rabbit.consume_jobs(handler)
    logger.info("Worker listening for jobs.")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
