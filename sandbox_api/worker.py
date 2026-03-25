from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlparse
from uuid import uuid4

from playwright.sync_api import sync_playwright

from .core.env import load_repo_env

load_repo_env()

from .sandboxes.agent_planner import plan_steps
from .artifacts import ArtifactRecord, ArtifactRepository, ArtifactStore
from .dashboards.charts_renderer import render_dashboard_charts
from .dashboards.router import save_dashboard_payload
from .core.database import init_db, engine
from .apps.education.api_service import grade_result_summary_from_path
from .apps.education.jobs import GradingJobStatus, grading_job_repo
from .apps.education.runner import BrowserApplyError, GradeStudentArgs, run_grade_student
from .core.jobs import CommandJob, DashboardUpdateJob, GradingJob, ProvisionJob, parse_job
from .sandboxes.models import SandboxRecord, SandboxRequest, SandboxStatus
from .sandboxes.command_models import StepsRequest
from .sandboxes.provisioner import build_default_provisioner
from .core.rabbitmq import RabbitMQ
from .sandboxes.storage import SandboxRepository

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
_PAGE_POINTER_STATE: dict[int, tuple[float, float]] = {}
_PAGE_CDP_SESSIONS: dict[int, Any] = {}


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


def _resolve_profile_path(
    artifact_repo: ArtifactRepository,
    *,
    record: SandboxRecord,
    artifact_id: str | None,
) -> str | None:
    if not artifact_id:
        return None
    artifact = artifact_repo.get(artifact_id)
    if not artifact:
        raise RuntimeError(f"Profile artifact not found: {artifact_id}")
    if artifact.owner_id != record.owner_id:
        raise RuntimeError("Profile artifact ownership mismatch.")
    if not artifact.blob_path:
        raise RuntimeError("Profile artifact missing blob path.")
    path = Path(artifact.blob_path)
    if not path.exists():
        raise RuntimeError("Profile artifact file missing.")
    return str(path.resolve())


def _truncate(value: str, limit: int) -> str:
    if limit <= 0 or len(value) <= limit:
        return value
    return value[:limit]


def _write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _artifact_matches_canvas_base(artifact: ArtifactRecord, canvas_base: str) -> bool:
    canvas_host = (urlparse(canvas_base).hostname or "").lower()
    if not canvas_host:
        return True
    attributes = artifact.attributes or {}
    candidate_hosts: list[str] = []
    for key in ("origin", "url"):
        value = attributes.get(key)
        if isinstance(value, str):
            host = urlparse(value).hostname
            if host:
                candidate_hosts.append(host.lower())
    domain = attributes.get("domain")
    if isinstance(domain, str) and domain.strip():
        candidate_hosts.append(domain.strip().lower().lstrip("."))
    for host in candidate_hosts:
        if canvas_host == host or canvas_host.endswith(f".{host}") or host.endswith(f".{canvas_host}"):
            return True
    return False


def _resolve_latest_session_share_profile_artifact_id(
    artifact_repo: ArtifactRepository,
    *,
    owner_id: Optional[str],
    canvas_base: str,
) -> Optional[str]:
    if not owner_id:
        return None
    records = artifact_repo.list(
        owner_id=owner_id,
        artifact_type="browser_profile",
        source="session_share",
        limit=20,
    )
    if not records:
        return None
    for artifact in records:
        if _artifact_matches_canvas_base(artifact, canvas_base):
            return artifact.artifact_id
    return records[0].artifact_id


def _build_grade_student_args(
    payload,
    internal_secret: str,
    owner_id: Optional[str] = None,
    artifact_repo: Optional[ArtifactRepository] = None,
) -> GradeStudentArgs:
    canvas_token = payload.canvas_token or os.getenv("CANVAS_TOKEN")
    if not canvas_token:
        raise RuntimeError("Missing Canvas token; set canvas_token or CANVAS_TOKEN.")
    # Resolve three independent LLM roles so OCR/extraction can run on vLLM while
    # rubric grading and annotation planning use a different Fireworks-hosted model.
    grading_llm_base = (
        payload.grading_llm_base
        or payload.llm_base
        or os.getenv("GRADING_LLM_BASE")
        or os.getenv("FIREWORKS_API_BASE")
        or os.getenv("LLM_API_BASE")
        or "https://api.fireworks.ai/inference/v1"
    )
    grading_llm_model = (
        payload.grading_llm_model
        or payload.llm_model
        or os.getenv("GRADING_LLM_MODEL")
        or os.getenv("FIREWORKS_GRADING_MODEL")
        or os.getenv("LLM_MODEL")
        or "accounts/fireworks/models/kimi-k2p5"
    )
    grading_llm_key = (
        os.getenv("GRADING_LLM_API_KEY")
        or os.getenv("FIREWORKS_API_KEY")
        or os.getenv("LLM_API_KEY")
    )
    extraction_llm_base = (
        payload.extraction_llm_base
        or os.getenv("EXTRACTION_LLM_BASE")
        or os.getenv("VLLM_API_BASE")
        or os.getenv("FIREWORKS_API_BASE")
        or grading_llm_base
    )
    extraction_llm_model = (
        payload.extraction_llm_model
        or os.getenv("EXTRACTION_LLM_MODEL")
        or os.getenv("VLLM_MODEL")
        or os.getenv("FIREWORKS_VISION_MODEL")
    )
    extraction_llm_key = (
        os.getenv("EXTRACTION_LLM_API_KEY")
        or os.getenv("VLLM_API_KEY")
        or os.getenv("FIREWORKS_API_KEY")
        or os.getenv("LLM_API_KEY")
    )
    annotation_llm_base = (
        payload.annotation_llm_base
        or os.getenv("ANNOTATION_LLM_BASE")
        or grading_llm_base
    )
    annotation_llm_model = (
        payload.annotation_llm_model
        or os.getenv("ANNOTATION_LLM_MODEL")
        or grading_llm_model
    )
    annotation_llm_key = (
        os.getenv("ANNOTATION_LLM_API_KEY")
        or os.getenv("GRADING_LLM_API_KEY")
        or os.getenv("FIREWORKS_API_KEY")
        or os.getenv("LLM_API_KEY")
    )
    sandbox_api = os.getenv("SANDBOX_API", "http://localhost:8000")
    agent_id = payload.agent_id or owner_id or os.getenv("SANDBOX_AGENT_ID", "grader")
    output_dir = payload.output_dir or "./artifacts/grading_runs"
    policy = payload.policy or ""
    resolved_profile_artifact_id = payload.profile_artifact_id
    if not resolved_profile_artifact_id:
        repo = artifact_repo or ArtifactRepository(engine)
        resolved_profile_artifact_id = _resolve_latest_session_share_profile_artifact_id(
            repo,
            owner_id=owner_id,
            canvas_base=str(payload.canvas_base),
        )
    logger.info(
        "grading_args_resolved owner_id=%s course_id=%s assignment_id=%s student_id=%s sandbox_id=%s profile_artifact_id=%s grading_model=%s extraction_model=%s annotation_model=%s",
        owner_id,
        payload.course_id,
        payload.assignment_id,
        payload.student_id,
        payload.sandbox_id,
        resolved_profile_artifact_id,
        grading_llm_model,
        extraction_llm_model,
        annotation_llm_model,
    )

    return GradeStudentArgs(
        course_id=payload.course_id,
        assignment_id=payload.assignment_id,
        student_id=payload.student_id,
        canvas_base=str(payload.canvas_base),
        canvas_token=canvas_token,
        internal_secret=internal_secret,
        sandbox_api=sandbox_api,
        agent_id=agent_id,
        sandbox_id=payload.sandbox_id,
        profile_artifact_id=resolved_profile_artifact_id,
        policy=policy,
        selectors_json=payload.selectors_json,
        output_dir=output_dir,
        vision_max_pages=payload.vision_max_pages,
        text_max_chars=payload.text_max_chars,
        min_text_chars=payload.min_text_chars,
        extraction_llm_base=extraction_llm_base,
        extraction_llm_key=extraction_llm_key,
        extraction_llm_model=extraction_llm_model,
        grading_llm_base=grading_llm_base,
        grading_llm_key=grading_llm_key,
        grading_llm_model=grading_llm_model,
        annotation_llm_base=annotation_llm_base,
        annotation_llm_key=annotation_llm_key,
        annotation_llm_model=annotation_llm_model,
        grade_result_path=payload.grade_result_path,
        reuse_latest_grade=payload.reuse_latest_grade,
        navigation_mode=payload.navigation_mode,
        strict_ui_checks=payload.strict_ui_checks,
    )


def _truncate_text(value: str, limit: int = 2000) -> str:
    if len(value) <= limit:
        return value
    return value[:limit]


class InternalSandboxOps:
    def __init__(self, owner_id: str) -> None:
        self.owner_id = owner_id
        self._repository = SandboxRepository(engine)
        self._provisioner = build_default_provisioner()
        self._artifact_repo = ArtifactRepository(engine)
        self._artifact_store = ArtifactStore(Path(os.getenv("SANDBOX_ARTIFACTS_ROOT", "./artifacts")))

    def create_sandbox(self, ttl_seconds: int = 3600) -> dict[str, Any]:
        logger.info("internal_sandbox create_start owner_id=%s ttl_seconds=%s", self.owner_id, ttl_seconds)
        request = SandboxRequest(ttl_seconds=ttl_seconds)
        now = datetime.now(timezone.utc)
        sandbox_id = f"sbx_{uuid4().hex[:8]}"
        expires_at = now + timedelta(seconds=request.ttl_seconds)
        record = SandboxRecord(
            sandbox_id=sandbox_id,
            status=SandboxStatus.provisioning,
            created_at=now,
            expires_at=expires_at,
            browser_url=None,
            dashboard_url=None,
            events_url=None,
            cdp_url=None,
            message="Provisioning sandbox.",
            owner_id=self.owner_id,
            cpu_limit=request.cpu_limit,
            memory_limit_mb=request.memory_limit_mb,
            capabilities=request.capabilities,
            allow_network=request.allow_network,
            backend_ref=None,
            http_port=None,
            cdp_port=None,
            artifacts_path=None,
        )
        self._repository.create(record)
        try:
            result = asyncio.run(self._provisioner.provision(sandbox_id, request, owner_id=self.owner_id))
            self._repository.set_ready(
                sandbox_id,
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
            logger.info(
                "internal_sandbox create_done sandbox_id=%s browser_url=%s cdp_url=%s cdp_port=%s artifacts_path=%s",
                sandbox_id,
                result.browser_url,
                result.cdp_url,
                result.cdp_port,
                result.artifacts_path,
            )
        except Exception as exc:  # noqa: BLE001
            self._repository.set_error(sandbox_id, message=f"Provisioning failed: {exc}")
            logger.exception("internal_sandbox create_failed sandbox_id=%s error=%s", sandbox_id, exc)
            raise
        return {"sandbox_id": sandbox_id}

    def wait_ready(self, sandbox_id: str, timeout: int = 180) -> dict[str, Any]:
        logger.info("internal_sandbox wait_ready_start sandbox_id=%s timeout=%s", sandbox_id, timeout)
        deadline = time.time() + timeout
        while time.time() < deadline:
            record = self._repository.get(sandbox_id)
            if not record:
                raise RuntimeError("Sandbox not found.")
            if record.owner_id != self.owner_id:
                raise RuntimeError("Sandbox owner mismatch.")
            if record.status == SandboxStatus.ready:
                logger.info("internal_sandbox wait_ready_done sandbox_id=%s", sandbox_id)
                return record.dict()
            if record.status == SandboxStatus.error:
                raise RuntimeError(f"Sandbox error: {record.message}")
            time.sleep(2)
        raise TimeoutError("Sandbox not ready in time.")

    def run_steps(self, sandbox_id: str, steps_payload: Dict[str, Any]) -> Dict[str, Any]:
        record = self._repository.get(sandbox_id)
        if not record:
            raise RuntimeError("Sandbox not found.")
        if record.owner_id != self.owner_id:
            raise RuntimeError("Sandbox owner mismatch.")
        if record.status != SandboxStatus.ready:
            raise RuntimeError(f"Sandbox not ready (status={record.status}).")

        command_id = f"cmd_{uuid4().hex[:8]}"
        request = StepsRequest.parse_obj(steps_payload)
        cfg = build_runtime_config(record)
        logger.info(
            "internal_sandbox run_steps sandbox_id=%s command_id=%s step_count=%s profile_artifact_id=%s",
            sandbox_id,
            command_id,
            len(request.steps),
            getattr(request, "profile_artifact_id", None),
        )

        def emit_event(payload: dict[str, Any]) -> None:  # noqa: ARG001
            return None

        def log(message: str) -> None:
            logger.info("grading_steps[%s]: %s", command_id, message)

        _run_steps(
            cfg,
            request,
            command_id=command_id,
            record=record,
            artifact_repo=self._artifact_repo,
            artifact_store=self._artifact_store,
            emit_event=emit_event,
            log=log,
        )
        return {"command_id": command_id}

    def list_artifacts(
        self,
        *,
        run_id: Optional[str] = None,
        artifact_type: Optional[str] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        items = self._artifact_repo.list(
            owner_id=self.owner_id,
            run_id=run_id,
            artifact_type=artifact_type,
            limit=limit,
        )
        return {
            "items": [item.dict() for item in items],
            "count": len(items),
        }

    def download_artifact_blob(self, artifact_id: str, dest: Path) -> Path:
        record = self._artifact_repo.get(artifact_id)
        if not record:
            raise RuntimeError("Artifact not found.")
        if record.owner_id != self.owner_id:
            raise RuntimeError("Artifact owner mismatch.")
        if not record.blob_path:
            raise RuntimeError("Artifact missing blob path.")
        src = Path(record.blob_path)
        if not src.exists():
            raise RuntimeError("Artifact blob missing.")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        return dest

def _capture_page_state(
    page,
    *,
    base_dir: Path,
    record: SandboxRecord,
    run_id: str,
    artifact_repo: ArtifactRepository,
    emit_event: Callable[[dict[str, Any]], None],
) -> tuple[dict[str, Any], list[str]]:
    html = page.content()
    try:
        text = page.evaluate("() => document.body ? document.body.innerText : ''")
    except Exception:
        text = ""
    try:
        a11y = page.accessibility.snapshot()
    except Exception:
        a11y = {}
    try:
        forms = page.evaluate(
            """
            () => Array.from(document.querySelectorAll('form')).map((form) => {
              const inputs = Array.from(form.querySelectorAll('input, textarea, select')).map((el) => {
                const label = el.labels && el.labels.length ? el.labels[0].innerText : null;
                return {
                  tag: el.tagName.toLowerCase(),
                  name: el.getAttribute('name'),
                  id: el.id || null,
                  type: el.getAttribute('type') || null,
                  placeholder: el.getAttribute('placeholder'),
                  label: label,
                  aria_label: el.getAttribute('aria-label'),
                  value_present: el.value ? true : false,
                };
              });
              return {
                id: form.id || null,
                name: form.getAttribute('name'),
                action: form.getAttribute('action'),
                method: form.getAttribute('method'),
                inputs: inputs,
              };
            })
            """
        )
    except Exception:
        forms = []

    viewport = page.viewport_size or {}
    state = {
        "url": page.url,
        "title": page.title(),
        "viewport": viewport,
        "visible_text": text,
        "forms": forms,
    }

    state_dir = base_dir / "state"
    _ensure_dir(state_dir)
    base_name = _safe_filename(f"{run_id}_state")
    html_path = state_dir / f"{base_name}.html"
    text_path = state_dir / f"{base_name}.txt"
    a11y_path = state_dir / f"{base_name}_a11y.json"
    state_path = state_dir / f"{base_name}.json"

    _write_text(html_path, html)
    _write_text(text_path, text)
    _write_text(a11y_path, json.dumps(a11y or {}, ensure_ascii=True, indent=2))
    _write_text(state_path, json.dumps(state, ensure_ascii=True, indent=2))

    artifact_ids: list[str] = []

    html_artifact = _register_text_artifact(
        artifact_repo,
        owner_id=record.owner_id,
        sandbox_id=record.sandbox_id,
        run_id=run_id,
        file_path=html_path,
        filename=html_path.name,
        artifact_format="html",
        mime_type="text/html",
        artifact_type="dom_snapshot",
        tags=["page_state", "html"],
    )
    artifact_ids.append(html_artifact.artifact_id)
    emit_event(
        {
            "type": "artifact_ready",
            "artifact_id": html_artifact.artifact_id,
            "sandbox_id": record.sandbox_id,
            "command_id": run_id,
            "filename": html_path.name,
            "artifact_type": html_artifact.artifact_type,
            "artifact_format": html_artifact.artifact_format,
            "timestamp": _now_iso(),
        }
    )

    text_artifact = _register_text_artifact(
        artifact_repo,
        owner_id=record.owner_id,
        sandbox_id=record.sandbox_id,
        run_id=run_id,
        file_path=text_path,
        filename=text_path.name,
        artifact_format="txt",
        mime_type="text/plain",
        artifact_type="page_text",
        tags=["page_state", "text"],
    )
    artifact_ids.append(text_artifact.artifact_id)
    emit_event(
        {
            "type": "artifact_ready",
            "artifact_id": text_artifact.artifact_id,
            "sandbox_id": record.sandbox_id,
            "command_id": run_id,
            "filename": text_path.name,
            "artifact_type": text_artifact.artifact_type,
            "artifact_format": text_artifact.artifact_format,
            "timestamp": _now_iso(),
        }
    )

    a11y_artifact = _register_text_artifact(
        artifact_repo,
        owner_id=record.owner_id,
        sandbox_id=record.sandbox_id,
        run_id=run_id,
        file_path=a11y_path,
        filename=a11y_path.name,
        artifact_format="json",
        mime_type="application/json",
        artifact_type="dom_snapshot",
        tags=["page_state", "a11y"],
    )
    artifact_ids.append(a11y_artifact.artifact_id)
    emit_event(
        {
            "type": "artifact_ready",
            "artifact_id": a11y_artifact.artifact_id,
            "sandbox_id": record.sandbox_id,
            "command_id": run_id,
            "filename": a11y_path.name,
            "artifact_type": a11y_artifact.artifact_type,
            "artifact_format": a11y_artifact.artifact_format,
            "timestamp": _now_iso(),
        }
    )

    state_artifact = _register_text_artifact(
        artifact_repo,
        owner_id=record.owner_id,
        sandbox_id=record.sandbox_id,
        run_id=run_id,
        file_path=state_path,
        filename=state_path.name,
        artifact_format="json",
        mime_type="application/json",
        artifact_type="page_state",
        tags=["page_state", "structured"],
    )
    artifact_ids.append(state_artifact.artifact_id)
    emit_event(
        {
            "type": "artifact_ready",
            "artifact_id": state_artifact.artifact_id,
            "sandbox_id": record.sandbox_id,
            "command_id": run_id,
            "filename": state_path.name,
            "artifact_type": state_artifact.artifact_type,
            "artifact_format": state_artifact.artifact_format,
            "timestamp": _now_iso(),
        }
    )

    llm_dom_limit = int(os.getenv("LLM_DOM_CHAR_LIMIT", "40000"))
    llm_text_limit = int(os.getenv("LLM_TEXT_CHAR_LIMIT", "20000"))
    llm_a11y_limit = int(os.getenv("LLM_A11Y_CHAR_LIMIT", "40000"))
    llm_context = {
        "url": state["url"],
        "title": state["title"],
        "viewport": state["viewport"],
        "visible_text": _truncate(text, llm_text_limit),
        "forms": state["forms"],
        "dom_html": _truncate(html, llm_dom_limit),
        "a11y_tree": _truncate(json.dumps(a11y or {}, ensure_ascii=True), llm_a11y_limit),
    }

    return llm_context, artifact_ids


def _build_selector_locators(target, selector, selector_fallbacks) -> list:
    locators = []
    selectors: list[str] = []
    if selector:
        selectors.append(selector)
    if selector_fallbacks:
        selectors.extend(selector_fallbacks)
    for value in selectors:
        locators.append(target.locator(value))
    return locators


def _build_locators(page, step) -> list:
    locators = []
    locators.extend(_build_selector_locators(page, step.selector, step.selector_fallbacks))
    if step.role:
        try:
            locators.append(page.get_by_role(step.role, name=step.role_name))
        except Exception:
            pass
    if step.label:
        try:
            locators.append(page.get_by_label(step.label))
        except Exception:
            pass
    if step.placeholder:
        try:
            locators.append(page.get_by_placeholder(step.placeholder))
        except Exception:
            pass
    if step.target_text:
        try:
            locators.append(page.get_by_text(step.target_text))
        except Exception:
            pass
    return locators


def _ensure_visual_cursor(page) -> None:
    try:
        page.evaluate(
            """
            (() => {
              if (document.getElementById('agent-pointer')) return;
              const dot = document.createElement('div');
              dot.id = 'agent-pointer';
              dot.style.cssText = [
                'position:fixed',
                'left:0',
                'top:0',
                'width:18px',
                'height:18px',
                'margin-left:-9px',
                'margin-top:-9px',
                'border-radius:9999px',
                'background:rgba(255,64,64,0.9)',
                'border:2px solid #fff',
                'box-shadow:0 0 0 2px rgba(255,64,64,0.35)',
                'z-index:2147483646',
                'pointer-events:none',
                'transform:translate(-100px,-100px)',
                'transition:transform 40ms linear, background 80ms ease'
              ].join(';');
              document.documentElement.appendChild(dot);
            })();
            """
        )
    except Exception:
        pass


def _move_visual_cursor(page, x: float, y: float, *, pressed: bool = False) -> None:
    _ensure_visual_cursor(page)
    color = "rgba(255,64,64,0.95)" if pressed else "rgba(255,179,0,0.95)"
    scale = "0.9" if pressed else "1"
    try:
        page.evaluate(
            """
            ([px, py, bg, scale]) => {
              const dot = document.getElementById('agent-pointer');
              if (!dot) return;
              dot.style.transform = `translate(${px}px, ${py}px) scale(${scale})`;
              dot.style.background = bg;
            }
            """,
            [x, y, color, scale],
        )
    except Exception:
        pass


def _get_cdp_session(page):
    page_id = id(page)
    cached = _PAGE_CDP_SESSIONS.get(page_id)
    if cached is not None:
        return cached
    try:
        session = page.context.new_cdp_session(page)
    except Exception:
        session = None
    _PAGE_CDP_SESSIONS[page_id] = session
    return session


def _dispatch_mouse_event(page, event_type: str, x: float, y: float, *, button: str = "left", buttons: int = 0) -> bool:
    session = _get_cdp_session(page)
    if session is None:
        return False
    try:
        session.send(
            "Input.dispatchMouseEvent",
            {
                "type": event_type,
                "x": x,
                "y": y,
                "button": button,
                "buttons": buttons,
                "pointerType": "mouse",
                "clickCount": 1,
            },
        )
        return True
    except Exception:
        return False


def _pointer_move(page, x: float, y: float, *, steps: int = 1, pressed: bool = False) -> None:
    page_id = id(page)
    prev = _PAGE_POINTER_STATE.get(page_id, (x, y))
    steps = max(1, steps)
    for index in range(1, steps + 1):
        ratio = index / steps
        px = prev[0] + (x - prev[0]) * ratio
        py = prev[1] + (y - prev[1]) * ratio
        _move_visual_cursor(page, px, py, pressed=pressed)
        if not _dispatch_mouse_event(page, "mouseMoved", px, py, buttons=1 if pressed else 0):
            page.mouse.move(px, py, steps=1)
    _PAGE_POINTER_STATE[page_id] = (x, y)


def _pointer_down(page, x: float, y: float) -> None:
    _pointer_move(page, x, y, steps=1, pressed=False)
    _move_visual_cursor(page, x, y, pressed=True)
    if not _dispatch_mouse_event(page, "mousePressed", x, y, button="left", buttons=1):
        page.mouse.down()
    _PAGE_POINTER_STATE[id(page)] = (x, y)


def _pointer_up(page, x: float, y: float) -> None:
    _pointer_move(page, x, y, steps=1, pressed=True)
    if not _dispatch_mouse_event(page, "mouseReleased", x, y, button="left", buttons=0):
        page.mouse.up()
    _move_visual_cursor(page, x, y, pressed=False)
    _PAGE_POINTER_STATE[id(page)] = (x, y)


def _pointer_click(page, x: float, y: float) -> None:
    _pointer_move(page, x, y, steps=6, pressed=False)
    _pointer_down(page, x, y)
    page.wait_for_timeout(60)
    _pointer_up(page, x, y)


def _describe_step(step) -> str:
    parts: list[str] = []
    if step.name:
        parts.append(f"name={step.name!r}")
    if step.url:
        parts.append(f"url={step.url!r}")
    if step.frame_selector:
        parts.append(f"frame_selector={step.frame_selector!r}")
    if step.frame_selector_fallbacks:
        parts.append(f"frame_selector_fallbacks={step.frame_selector_fallbacks!r}")
    if step.skip_if_selector:
        parts.append(f"skip_if_selector={step.skip_if_selector!r}")
    if step.skip_if_selector_fallbacks:
        parts.append(f"skip_if_selector_fallbacks={step.skip_if_selector_fallbacks!r}")
    if step.optional:
        parts.append("optional=True")
    if step.selector:
        parts.append(f"selector={step.selector!r}")
    if step.selector_fallbacks:
        parts.append(f"selector_fallbacks={step.selector_fallbacks!r}")
    if step.role:
        parts.append(f"role={step.role!r}")
    if step.role_name:
        parts.append(f"role_name={step.role_name!r}")
    if step.label:
        parts.append(f"label={step.label!r}")
    if step.placeholder:
        parts.append(f"placeholder={step.placeholder!r}")
    if step.target_text:
        parts.append(f"target_text={step.target_text!r}")
    if step.timeout_ms:
        parts.append(f"timeout_ms={step.timeout_ms}")
    if step.retries:
        parts.append(f"retries={step.retries}")
    if step.wait_ms:
        parts.append(f"wait_ms={step.wait_ms}")
    return ", ".join(parts)


def _resolve_frame_from_locators(page, locators, *, timeout: int, retries: int):
    if not locators:
        raise RuntimeError("No locators available for frame.")
    last_exc: Exception | None = None
    for attempt in range(retries):
        for locator in locators:
            try:
                target = locator.first
                target.wait_for(state="visible", timeout=timeout)
                handle = target.element_handle()
                if handle:
                    frame = handle.content_frame()
                    if frame is not None:
                        return frame
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
        if attempt < retries - 1:
            page.wait_for_timeout(300)
    if last_exc:
        raise last_exc
    raise RuntimeError("Failed to resolve target frame.")


def _resolve_step_scope(page, step):
    if not step.frame_selector and not step.frame_selector_fallbacks:
        return page
    timeout = step.timeout_ms or 30000
    retries = step.retries or int(os.getenv("SANDBOX_STEP_RETRIES", "1"))
    locators = _build_selector_locators(page, step.frame_selector, step.frame_selector_fallbacks)
    return _resolve_frame_from_locators(page, locators, timeout=timeout, retries=retries)


def _step_should_skip(page, step) -> bool:
    if not step.skip_if_selector and not step.skip_if_selector_fallbacks:
        return False
    scope = _resolve_step_scope(page, step)
    timeout = min(step.timeout_ms or 30000, 500)
    locators = _build_selector_locators(scope, step.skip_if_selector, step.skip_if_selector_fallbacks)
    for locator in locators:
        try:
            locator.first.wait_for(state="visible", timeout=timeout)
            return True
        except Exception:
            continue
    return False


def _resolve_target_box(page, step):
    scope = _resolve_step_scope(page, step)
    locators = _build_locators(scope, step)
    if not locators:
        raise RuntimeError("No locators available for draw_path.")
    timeout = step.timeout_ms or 30000
    retries = step.retries or int(os.getenv("SANDBOX_STEP_RETRIES", "1"))
    last_exc: Exception | None = None
    for attempt in range(retries):
        for locator in locators:
            try:
                target = locator.first
                target.wait_for(state="visible", timeout=timeout)
                try:
                    target.scroll_into_view_if_needed(timeout=timeout)
                except Exception:
                    pass
                box = target.bounding_box()
                if box:
                    return box
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
        if attempt < retries - 1:
            page.wait_for_timeout(300)
    if last_exc:
        raise last_exc
    raise RuntimeError("Failed to resolve draw target bounding box.")


def _resolve_target_frame(page, step):
    locators = _build_locators(page, step)
    timeout = step.timeout_ms or 30000
    retries = step.retries or int(os.getenv("SANDBOX_STEP_RETRIES", "1"))
    return _resolve_frame_from_locators(page, locators, timeout=timeout, retries=retries)


def _perform_locator_action(page, step, action_name: str) -> None:
    scope = _resolve_step_scope(page, step)
    locators = _build_locators(scope, step)
    if not locators:
        if step.optional:
            return
        raise RuntimeError(f"No locators available for {action_name}.")
    timeout = step.timeout_ms or 30000
    retries = step.retries or int(os.getenv("SANDBOX_STEP_RETRIES", "1"))
    last_exc: Exception | None = None
    for attempt in range(retries):
        for locator in locators:
            try:
                target = locator.first
                target.wait_for(state="visible", timeout=timeout)
                try:
                    target.scroll_into_view_if_needed(timeout=timeout)
                except Exception:
                    pass
                if action_name == "click":
                    target.click(timeout=timeout)
                elif action_name == "type":
                    if step.delay_ms:
                        target.type(step.text, delay=step.delay_ms, timeout=timeout)
                    else:
                        target.fill(step.text, timeout=timeout)
                elif action_name == "wait_for_selector":
                    target.wait_for(state="visible", timeout=timeout)
                else:
                    raise RuntimeError(f"Unsupported locator action: {action_name}")
                return
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
        if attempt < retries - 1:
            page.wait_for_timeout(300)
    if last_exc:
        if step.optional:
            return
        details = _describe_step(step)
        if details:
            raise RuntimeError(f"Failed to perform {action_name} ({details}): {last_exc}") from last_exc
        raise last_exc
    details = _describe_step(step)
    if step.optional:
        return
    if details:
        raise RuntimeError(f"Failed to perform {action_name} ({details}).")
    raise RuntimeError(f"Failed to perform {action_name}.")

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
    artifact_type: str = "dom_snapshot",
    tags: list[str] | None = None,
    source: str = "steps",
) -> ArtifactRecord:
    now = datetime.now(timezone.utc)
    record = ArtifactRecord(
        artifact_id=f"art_{uuid4().hex[:12]}",
        owner_id=owner_id,
        session_id=None,
        sandbox_id=sandbox_id,
        artifact_type=artifact_type,
        source=source,
        run_id=run_id,
        volatility=None,
        artifact_format=artifact_format,
        created_at=now,
        updated_at=now,
        size_bytes=None,
        mime_type=mime_type,
        filename=filename,
        checksum_sha256=None,
        tags=tags or ["steps", artifact_type],
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


def _execute_steps(
    page,
    request: StepsRequest,
    *,
    command_id: str,
    record: SandboxRecord,
    base_dir: Path,
    artifact_repo: ArtifactRepository,
    artifact_store: ArtifactStore,
    emit_event: Callable[[dict[str, Any]], None],
    log: Callable[[str], None],
) -> list[str]:
    artifact_ids: list[str] = []
    for index, step in enumerate(request.steps, start=1):
        step_label = f"{index:02d}_{step.action}"
        details = _describe_step(step)
        if details:
            log(f"Step {index}: {step.action} [{details}]")
        else:
            log(f"Step {index}: {step.action}")
        if _step_should_skip(page, step):
            log(f"Step {index}: skipped because existing selector matched")
            continue
        update_overlay(page, step.action, step.dict())
        timeout = step.timeout_ms or 30000
        if step.action == "goto":
            page.goto(step.url, wait_until="domcontentloaded", timeout=timeout)
        elif step.action == "click":
            _perform_locator_action(page, step, "click")
        elif step.action == "type":
            _perform_locator_action(page, step, "type")
        elif step.action == "type_rce":
            frame = _resolve_target_frame(page, step)
            timeout = step.timeout_ms or 30000
            body = frame.locator("body")
            body.wait_for(state="visible", timeout=timeout)
            try:
                body.fill(step.text or "", timeout=timeout)
            except Exception:
                body.click(timeout=timeout)
                try:
                    frame.keyboard.press("Control+A")
                except Exception:
                    pass
                frame.keyboard.type(step.text or "")
        elif step.action == "wait":
            page.wait_for_timeout(step.wait_ms)
        elif step.action == "wait_for_selector":
            _perform_locator_action(page, step, "wait_for_selector")
        elif step.action == "draw_path":
            box = _resolve_target_box(page, step)
            points = step.points or []
            if len(points) < 2:
                raise RuntimeError("draw_path requires at least 2 points.")
            start = points[0]
            start_x = box["x"] + start.x * box["width"]
            start_y = box["y"] + start.y * box["height"]
            _pointer_move(page, start_x, start_y, steps=12, pressed=False)
            _pointer_down(page, start_x, start_y)
            for point in points[1:]:
                x = box["x"] + point.x * box["width"]
                y = box["y"] + point.y * box["height"]
                _pointer_move(page, x, y, steps=12, pressed=True)
            _pointer_up(page, x, y)
        elif step.action == "draw_rect":
            box = _resolve_target_box(page, step)
            points = step.points or []
            if len(points) < 2:
                raise RuntimeError("draw_rect requires at least 2 points.")
            start = points[0]
            end = points[1]
            start_x = box["x"] + start.x * box["width"]
            start_y = box["y"] + start.y * box["height"]
            end_x = box["x"] + end.x * box["width"]
            end_y = box["y"] + end.y * box["height"]
            _pointer_move(page, start_x, start_y, steps=12, pressed=False)
            _pointer_down(page, start_x, start_y)
            _pointer_move(page, end_x, end_y, steps=18, pressed=True)
            _pointer_up(page, end_x, end_y)
        elif step.action == "point":
            box = _resolve_target_box(page, step)
            target = step.point
            if target is None:
                raise RuntimeError("point requires point.")
            x = box["x"] + target.x * box["width"]
            y = box["y"] + target.y * box["height"]
            _pointer_click(page, x, y)
        elif step.action == "freetext":
            box = _resolve_target_box(page, step)
            target = step.point
            if target is None:
                raise RuntimeError("freetext requires point.")
            x = box["x"] + target.x * box["width"]
            y = box["y"] + target.y * box["height"]
            _pointer_click(page, x, y)
            if step.text:
                page.keyboard.type(step.text)
        elif step.action == "dom_snapshot":
            pass
        elif step.action == "screenshot":
            pass
        elif step.action == "page_state":
            llm_context, state_artifacts = _capture_page_state(
                page,
                base_dir=base_dir,
                record=record,
                run_id=command_id,
                artifact_repo=artifact_repo,
                emit_event=emit_event,
            )
            artifact_ids.extend(state_artifacts)
            log(f"Captured page state (context keys: {', '.join(llm_context.keys())})")
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
            scope = _resolve_step_scope(page, step)
            if step.snapshot_format == "a11y_json":
                root = scope.query_selector(step.selector) if step.selector else None
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
                    artifact_type="dom_snapshot",
                    tags=["steps", "dom_snapshot", "a11y"],
                )
            else:
                if step.selector:
                    element = scope.query_selector(step.selector)
                    if not element:
                        raise RuntimeError(f"Selector not found: {step.selector}")
                    content = element.evaluate("el => el.outerHTML")
                else:
                    content = scope.content()
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
                    artifact_type="dom_snapshot",
                    tags=["steps", "dom_snapshot", "html"],
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
    return artifact_ids


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
    base_dir: Path | None = None,
) -> dict[str, Any]:
    base_dir = base_dir or Path(record.artifacts_path or str(artifact_store.root)) / "steps" / command_id
    _ensure_dir(base_dir)
    profile_path = _resolve_profile_path(
        artifact_repo,
        record=record,
        artifact_id=getattr(request, "profile_artifact_id", None),
    )

    with sync_playwright() as playwright:
        browser, context, page = BrowserRunner(cfg, log).attach(playwright, storage_state_path=profile_path)
        artifact_ids = _execute_steps(
            page,
            request,
            command_id=command_id,
            record=record,
            base_dir=base_dir,
            artifact_repo=artifact_repo,
            artifact_store=artifact_store,
            emit_event=emit_event,
            log=log,
        )
        try:
            browser.close()
        except Exception:
            pass

    return {"artifact_ids": artifact_ids}


def _run_agent(
    cfg: RuntimeConfig,
    request,
    *,
    command_id: str,
    record: SandboxRecord,
    artifact_repo: ArtifactRepository,
    artifact_store: ArtifactStore,
    emit_event: Callable[[dict[str, Any]], None],
    log: Callable[[str], None],
) -> dict[str, Any]:
    base_dir = Path(record.artifacts_path or str(artifact_store.root)) / "agent" / command_id
    _ensure_dir(base_dir)
    artifact_ids: list[str] = []
    profile_path = _resolve_profile_path(
        artifact_repo,
        record=record,
        artifact_id=getattr(request, "profile_artifact_id", None),
    )

    with sync_playwright() as playwright:
        browser, context, page = BrowserRunner(cfg, log).attach(playwright, storage_state_path=profile_path)
        llm_context: dict[str, Any] = {}
        if request.capture_state:
            llm_context, state_artifacts = _capture_page_state(
                page,
                base_dir=base_dir,
                record=record,
                run_id=command_id,
                artifact_repo=artifact_repo,
                emit_event=emit_event,
            )
            artifact_ids.extend(state_artifacts)

        planned = plan_steps(
            request=request,
            page_context=llm_context,
            log=log,
        )

        plan_payload = {
            "task": request.task,
            "steps": [step.dict(exclude_none=True, by_alias=True) for step in planned.steps],
            "screenshot_every_step": planned.screenshot_every_step,
        }
        plan_path = base_dir / "agent_plan.json"
        _write_text(plan_path, json.dumps(plan_payload, ensure_ascii=True, indent=2))
        plan_artifact = _register_text_artifact(
            artifact_repo,
            owner_id=record.owner_id,
            sandbox_id=record.sandbox_id,
            run_id=command_id,
            file_path=plan_path,
            filename=plan_path.name,
            artifact_format="json",
            mime_type="application/json",
            artifact_type="agent_plan",
            tags=["agent_plan"],
        )
        artifact_ids.append(plan_artifact.artifact_id)
        emit_event(
            {
                "type": "artifact_ready",
                "artifact_id": plan_artifact.artifact_id,
                "sandbox_id": record.sandbox_id,
                "command_id": command_id,
                "filename": plan_path.name,
                "artifact_type": plan_artifact.artifact_type,
                "artifact_format": plan_artifact.artifact_format,
                "timestamp": _now_iso(),
            }
        )

        artifact_ids.extend(
            _execute_steps(
                page,
                planned,
                command_id=command_id,
                record=record,
                base_dir=base_dir,
                artifact_repo=artifact_repo,
                artifact_store=artifact_store,
                emit_event=emit_event,
                log=log,
            )
        )

        try:
            browser.close()
        except Exception:
            pass

    return {"artifact_ids": artifact_ids}


def _capture_profile(
    cfg: RuntimeConfig,
    request,
    *,
    command_id: str,
    record: SandboxRecord,
    artifact_repo: ArtifactRepository,
    artifact_store: ArtifactStore,
    emit_event: Callable[[dict[str, Any]], None],
    log: Callable[[str], None],
) -> dict[str, Any]:
    base_dir = Path(record.artifacts_path or str(artifact_store.root)) / "profiles" / command_id
    _ensure_dir(base_dir)

    name = _safe_filename(request.name or f"profile_{command_id}")
    filename = f"{name}.json"
    file_path = base_dir / filename

    with sync_playwright() as playwright:
        browser, context, page = BrowserRunner(cfg, log).attach(playwright)
        context.storage_state(path=str(file_path))
        try:
            browser.close()
        except Exception:
            pass

    artifact = _register_text_artifact(
        artifact_repo,
        owner_id=record.owner_id,
        sandbox_id=record.sandbox_id,
        run_id=command_id,
        file_path=file_path,
        filename=filename,
        artifact_format="json",
        mime_type="application/json",
        artifact_type="browser_profile",
        tags=["profile", "storage_state"],
        source="profile",
    )
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

    return {"artifact_id": artifact.artifact_id}


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
            profile_path = None
            if hasattr(job.payload, "profile_artifact_id"):
                profile_path = _resolve_profile_path(
                    artifact_repo,
                    record=record,
                    artifact_id=job.payload.profile_artifact_id,
                )
            if job.command == "run_browser":
                artifact_id = run_browser_artifact(
                    cfg,
                    str(job.payload.url),
                    interactive=bool(job.payload.interactive),
                    storage_state_path=profile_path,
                )
                return {"artifact_id": artifact_id}
            if job.command == "record":
                session_id = record_session(
                    cfg,
                    str(job.payload.url),
                    duration=int(job.payload.duration),
                    interactive=bool(job.payload.interactive),
                    storage_state_path=profile_path,
                )
                return {"session_id": session_id}
            if job.command == "replay":
                replay_session(
                    cfg,
                    str(job.payload.session_id),
                    speed=float(job.payload.speed),
                    interactive=bool(job.payload.interactive),
                    storage_state_path=profile_path,
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
            if job.command == "agent":
                return _run_agent(
                    cfg,
                    job.payload,
                    command_id=job.command_id,
                    record=record,
                    artifact_repo=artifact_repo,
                    artifact_store=artifact_store,
                    emit_event=emit_event,
                    log=log_emitter,
                )
            if job.command == "capture_profile":
                return _capture_profile(
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
    artifact_repo: ArtifactRepository,
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

    loop = asyncio.get_running_loop()

    def emit_event(payload: dict[str, Any]) -> None:
        asyncio.run_coroutine_threadsafe(publish_event(rabbit, payload), loop)

    def log_line(message: str) -> None:
        logger.info("dashboard_render: %s", message)

    await asyncio.to_thread(
        render_dashboard_charts,
        payload_model,
        record=record,
        artifact_repo=artifact_repo,
        emit_event=emit_event,
        log=log_line,
    )


async def handle_grading_job(job: GradingJob) -> None:
    logger.info("grading_job_worker start job_id=%s owner_id=%s", job.job_id, job.owner_id)
    now = datetime.now(timezone.utc)
    grading_job_repo.update(
        job.job_id,
        status=GradingJobStatus.running.value,
        started_at=now,
        updated_at=now,
    )
    internal_secret = os.getenv("INTERNAL_AUTH_SECRET")
    if not internal_secret:
        logger.error("grading_job_worker missing_internal_auth_secret job_id=%s", job.job_id)
        now = datetime.now(timezone.utc)
        grading_job_repo.update(
            job.job_id,
            status=GradingJobStatus.failed.value,
            error_message="Missing INTERNAL_AUTH_SECRET.",
            updated_at=now,
            finished_at=now,
        )
        return

    try:
        args = _build_grade_student_args(job.payload, internal_secret, owner_id=job.owner_id)
        sandbox_ops = InternalSandboxOps(owner_id=job.owner_id)
        outcome = await asyncio.to_thread(run_grade_student, args, sandbox_ops)
        logger.info(
            "grading_job_worker success job_id=%s run_dir=%s grade_result_path=%s llm_observability_path=%s",
            job.job_id,
            outcome.run_dir,
            outcome.grade_result_path,
            outcome.llm_observability_path,
        )
        grading_job_repo.update(
            job.job_id,
            status=GradingJobStatus.succeeded.value,
            result={
                "run_dir": str(outcome.run_dir.resolve()),
                "grade_result_path": str(outcome.grade_result_path.resolve()),
                "grade_summary": grade_result_summary_from_path(outcome.grade_result_path),
                "speedgrader_state_path": str(outcome.speedgrader_state_path.resolve()),
                "llm_observability_path": str(outcome.llm_observability_path.resolve()),
                "sandbox_id": outcome.sandbox_id,
                "browser_url": outcome.browser_url,
                "dashboard_url": outcome.dashboard_url,
                "stdout_tail": "",
                "stderr_tail": "",
            },
            updated_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        )
    except BrowserApplyError as exc:
        logger.exception("grading_job_worker browser_apply_failed job_id=%s error=%s", job.job_id, exc)
        grading_job_repo.update(
            job.job_id,
            status=GradingJobStatus.failed.value,
            error_message=_truncate_text(str(exc)),
            result={
                "run_dir": str(exc.run_dir.resolve()),
                "grade_result_path": str(exc.grade_result_path.resolve()),
                "grade_summary": grade_result_summary_from_path(exc.grade_result_path),
                "llm_observability_path": str(exc.llm_observability_path.resolve()),
                "speedgrader_state_path": str(exc.speedgrader_state_path.resolve()) if exc.speedgrader_state_path else None,
                "sandbox_id": exc.sandbox_id,
                "browser_url": exc.browser_url,
                "dashboard_url": exc.dashboard_url,
                "traceback": _truncate_text(traceback.format_exc()),
            },
            updated_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("grading_job_worker failed job_id=%s error=%s", job.job_id, exc)
        grading_job_repo.update(
            job.job_id,
            status=GradingJobStatus.failed.value,
            error_message=_truncate_text(str(exc)),
            result={"traceback": _truncate_text(traceback.format_exc())},
            updated_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        )


async def dispatch_job(
    job,
    repository: SandboxRepository,
    artifact_repo: ArtifactRepository,
    artifact_store: ArtifactStore,
    provisioner,
    rabbit: RabbitMQ,
) -> None:
    if isinstance(job, ProvisionJob):
        await handle_provision(job, repository, provisioner, rabbit)
        return
    if isinstance(job, CommandJob):
        await handle_command(job, repository, artifact_repo, artifact_store, rabbit)
        return
    if isinstance(job, DashboardUpdateJob):
        await handle_dashboard_update(job, repository, rabbit, artifact_repo)
        return
    if isinstance(job, GradingJob):
        await handle_grading_job(job)
        return
    logger.error("Unhandled job type: %s", job.type)


async def handle_message(
    message,
    repository: SandboxRepository,
    artifact_repo: ArtifactRepository,
    artifact_store: ArtifactStore,
    provisioner,
    rabbit: RabbitMQ,
    grading_semaphore: asyncio.Semaphore,
) -> None:
    async with message.process():
        try:
            job = parse_job(json.loads(message.body.decode("utf-8")))
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to parse job: %s", exc)
            return
        if isinstance(job, GradingJob):
            async with grading_semaphore:
                await dispatch_job(job, repository, artifact_repo, artifact_store, provisioner, rabbit)
        else:
            await dispatch_job(job, repository, artifact_repo, artifact_store, provisioner, rabbit)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    init_db()
    repository = SandboxRepository(engine)
    artifact_repo = ArtifactRepository(engine)
    artifact_store = ArtifactStore(Path(os.getenv("SANDBOX_ARTIFACTS_ROOT", "./artifacts")))
    provisioner = build_default_provisioner()
    grading_concurrency = max(1, int(os.getenv("GRADING_WORKER_CONCURRENCY", "1")))
    prefetch_env = os.getenv("RABBITMQ_PREFETCH")
    if prefetch_env:
        prefetch = max(1, int(prefetch_env))
    else:
        prefetch = grading_concurrency
    grading_semaphore = asyncio.Semaphore(grading_concurrency)
    rabbit = RabbitMQ(prefetch=prefetch)
    await rabbit.connect()

    async def handler(message) -> None:
        await handle_message(
            message,
            repository,
            artifact_repo,
            artifact_store,
            provisioner,
            rabbit,
            grading_semaphore,
        )

    await rabbit.consume_jobs(handler)
    logger.info("Worker listening for jobs.")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
