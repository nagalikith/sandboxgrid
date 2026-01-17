from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from playwright.sync_api import sync_playwright

from .artifacts import ArtifactRecord, ArtifactRepository, ArtifactStore
from .dashboard import save_dashboard_payload
from .database import init_db, engine
from .jobs import CommandJob, DashboardUpdateJob, ProvisionJob, parse_job
from .models import SandboxRecord, SandboxStatus
from .provisioner import build_default_provisioner
from .rabbitmq import RabbitMQ
from .storage import SandboxRepository

try:
    from run_artifact import (
        BrowserRunner,
        RuntimeConfig,
        record_session,
        replay_session,
        run_artifact as run_browser_artifact,
        update_overlay,
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

def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _safe_filename(name: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in name)
    return safe.strip("_") or "screenshot"


def _register_screenshot(
    repository: ArtifactRepository,
    store: ArtifactStore,
    *,
    owner_id: str,
    sandbox_id: str,
    run_id: str,
    file_path: Path,
    filename: str,
) -> ArtifactRecord:
    now = datetime.now(timezone.utc)
    record = ArtifactRecord(
        artifact_id=f"art_{uuid4().hex[:12]}",
        owner_id=owner_id,
        session_id=None,
        sandbox_id=sandbox_id,
        artifact_type="screenshot",
        source="steps",
        run_id=run_id,
        volatility=None,
        artifact_format="png",
        created_at=now,
        updated_at=now,
        size_bytes=None,
        mime_type="image/png",
        filename=filename,
        checksum_sha256=None,
        tags=["steps"],
        sensitivity=None,
        attributes=None,
        blob_path=None,
    )
    created = repository.create(record)
    size_bytes = file_path.stat().st_size
    hasher = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            hasher.update(chunk)
    updated = repository.update_blob(
        created.artifact_id,
        blob_path=str(file_path.resolve()),
        size_bytes=size_bytes,
        checksum_sha256=hasher.hexdigest(),
        mime_type="image/png",
    )
    return updated or created


def _register_text_artifact(
    repository: ArtifactRepository,
    *,
    owner_id: str,
    sandbox_id: str,
    run_id: str,
    file_path: Path,
    filename: str,
    artifact_format: str,
    mime_type: str,
) -> ArtifactRecord:
    now = datetime.now(timezone.utc)
    record = ArtifactRecord(
        artifact_id=f"art_{uuid4().hex[:12]}",
        owner_id=owner_id,
        session_id=None,
        sandbox_id=sandbox_id,
        artifact_type="dom_snapshot",
        source="steps",
        run_id=run_id,
        volatility=None,
        artifact_format=artifact_format,
        created_at=now,
        updated_at=now,
        size_bytes=None,
        mime_type=mime_type,
        filename=filename,
        checksum_sha256=None,
        tags=["steps", "dom_snapshot"],
        sensitivity=None,
        attributes=None,
        blob_path=None,
    )
    created = repository.create(record)
    size_bytes = file_path.stat().st_size
    hasher = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            hasher.update(chunk)
    updated = repository.update_blob(
        created.artifact_id,
        blob_path=str(file_path.resolve()),
        size_bytes=size_bytes,
        checksum_sha256=hasher.hexdigest(),
        mime_type=mime_type,
    )
    return updated or created


def _run_steps(
    cfg: RuntimeConfig,
    request: StepsRequest,
    *,
    command_id: str,
    record: SandboxRecord,
    artifact_repo: ArtifactRepository,
    artifact_store: ArtifactStore,
    emit_event: Callable[[dict[str, Any]], None],
    log: Callable[[str], None],
) -> dict[str, Any]:
    base_dir = Path(record.artifacts_path or str(artifact_store.root)) / "steps" / command_id
    _ensure_dir(base_dir)
    artifact_ids: list[str] = []

    with sync_playwright() as playwright:
        browser, context, page = BrowserRunner(cfg, log).attach(playwright)
        for index, step in enumerate(request.steps, start=1):
            step_label = f"{index:02d}_{step.action}"
            log(f"Step {index}: {step.action}")
            update_overlay(page, step.action, step.dict())
            timeout = step.timeout_ms or 30000
            if step.action == "goto":
                page.goto(step.url, wait_until="domcontentloaded", timeout=timeout)
            elif step.action == "click":
                page.click(step.selector, timeout=timeout)
            elif step.action == "type":
                if step.delay_ms:
                    page.type(step.selector, step.text, delay=step.delay_ms, timeout=timeout)
                else:
                    page.fill(step.selector, step.text, timeout=timeout)
            elif step.action == "wait":
                page.wait_for_timeout(step.wait_ms)
            elif step.action == "wait_for_selector":
                page.wait_for_selector(step.selector, timeout=timeout)
            elif step.action == "dom_snapshot":
                pass
            elif step.action == "screenshot":
                pass
            else:
                raise RuntimeError(f"Unsupported step action: {step.action}")

            should_capture = step.action == "screenshot" or request.screenshot_every_step
            if should_capture:
                name = _safe_filename(step.name or step_label)
                filename = f"{name}.png"
                file_path = base_dir / filename
                page.screenshot(path=str(file_path))
                artifact = _register_screenshot(
                    artifact_repo,
                    artifact_store,
                    owner_id=record.owner_id,
                    sandbox_id=record.sandbox_id,
                    run_id=command_id,
                    file_path=file_path,
                    filename=filename,
                )
                artifact_ids.append(artifact.artifact_id)
                emit_event(
                    {
                        "type": "artifact_ready",
                        "artifact_id": artifact.artifact_id,
                        "sandbox_id": record.sandbox_id,
                        "command_id": command_id,
                        "filename": filename,
                        "artifact_type": "screenshot",
                        "artifact_format": "png",
                        "timestamp": _now_iso(),
                    }
                )

            if step.action == "dom_snapshot":
                name = _safe_filename(step.name or step_label)
                if step.snapshot_format == "a11y_json":
                    root = page.query_selector(step.selector) if step.selector else None
                    snapshot = page.accessibility.snapshot(root=root)
                    content = json.dumps(snapshot or {}, ensure_ascii=True, indent=2)
                    filename = f"{name}.json"
                    file_path = base_dir / filename
                    file_path.write_text(content, encoding="utf-8")
                    artifact = _register_text_artifact(
                        artifact_repo,
                        owner_id=record.owner_id,
                        sandbox_id=record.sandbox_id,
                        run_id=command_id,
                        file_path=file_path,
                        filename=filename,
                        artifact_format="json",
                        mime_type="application/json",
                    )
                else:
                    if step.selector:
                        element = page.query_selector(step.selector)
                        if not element:
                            raise RuntimeError(f"Selector not found: {step.selector}")
                        content = element.evaluate("el => el.outerHTML")
                    else:
                        content = page.content()
                    filename = f"{name}.html"
                    file_path = base_dir / filename
                    file_path.write_text(content, encoding="utf-8")
                    artifact = _register_text_artifact(
                        artifact_repo,
                        owner_id=record.owner_id,
                        sandbox_id=record.sandbox_id,
                        run_id=command_id,
                        file_path=file_path,
                        filename=filename,
                        artifact_format="html",
                        mime_type="text/html",
                    )
                artifact_ids.append(artifact.artifact_id)
                emit_event(
                    {
                        "type": "artifact_ready",
                        "artifact_id": artifact.artifact_id,
                        "sandbox_id": record.sandbox_id,
                        "command_id": command_id,
                        "filename": filename,
                        "artifact_type": artifact.artifact_type,
                        "artifact_format": artifact.artifact_format,
                        "timestamp": _now_iso(),
                    }
                )

        try:
            browser.close()
        except Exception:
            pass

    return {"artifact_ids": artifact_ids}


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
    artifact_repo: ArtifactRepository,
    artifact_store: ArtifactStore,
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

    def emit_event(payload: dict[str, Any]) -> None:
        asyncio.run_coroutine_threadsafe(publish_event(rabbit, payload), loop)

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
            if job.command == "steps":
                return _run_steps(
                    cfg,
                    job.payload,
                    command_id=job.command_id,
                    record=record,
                    artifact_repo=artifact_repo,
                    artifact_store=artifact_store,
                    emit_event=emit_event,
                    log=log_emitter,
                )
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
    artifact_repo: ArtifactRepository,
    artifact_store: ArtifactStore,
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
            await handle_command(job, repository, artifact_repo, artifact_store, rabbit)
            return
        if isinstance(job, DashboardUpdateJob):
            await handle_dashboard_update(job, repository, rabbit)
            return
        logger.error("Unhandled job type: %s", job.type)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    init_db()
    repository = SandboxRepository(engine)
    artifact_repo = ArtifactRepository(engine)
    artifact_store = ArtifactStore(Path(os.getenv("SANDBOX_ARTIFACTS_ROOT", "./artifacts")))
    provisioner = build_default_provisioner()
    rabbit = RabbitMQ()
    await rabbit.connect()

    async def handler(message) -> None:
        await handle_job(message, repository, artifact_repo, artifact_store, provisioner, rabbit)

    await rabbit.consume_jobs(handler)
    logger.info("Worker listening for jobs.")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
