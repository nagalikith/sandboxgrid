import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlmodel import SQLModel

from sandbox_api.apps.education.jobs import GradingJobRequest
from sandbox_api.platform.artifacts import ArtifactRecord, ArtifactRepository, ArtifactStore
from sandbox_api.sandboxes.command_models import AgentStepsRequest, Step, StepsRequest
from sandbox_api.core.database import engine
from sandbox_api.core.jobs import CommandJob, DashboardUpdateJob, ProvisionJob
from sandbox_api.sandboxes.models import SandboxRecord, SandboxRequest, SandboxStatus
from sandbox_api.sandboxes.storage import SandboxRepository
from sandbox_api.platform import worker


class DummyLocator:
    def __init__(self, *, fail_once=False, element_handle=None, box=None) -> None:
        self.fail_once = fail_once
        self.failed = False
        self.calls = []
        self._element_handle = element_handle
        self._box = box or {"x": 10, "y": 20, "width": 100, "height": 200}

    @property
    def first(self):
        return self

    def wait_for(self, **_kwargs):
        self.calls.append("wait_for")
        if self.fail_once and not self.failed:
            self.failed = True
            raise RuntimeError("wait")

    def click(self, **_kwargs):
        self.calls.append("click")

    def fill(self, _text, **_kwargs):
        self.calls.append("fill")

    def type(self, _text, **_kwargs):
        self.calls.append("type")

    def element_handle(self):
        self.calls.append("element_handle")
        return self._element_handle

    def bounding_box(self):
        self.calls.append("bounding_box")
        return self._box

    def scroll_into_view_if_needed(self, **_kwargs):
        self.calls.append("scroll_into_view_if_needed")


class DummyFrameHandle:
    def __init__(self, frame) -> None:
        self._frame = frame

    def content_frame(self):
        return self._frame


class DummyElement:
    def evaluate(self, _script):
        return "<div>snapshot</div>"


class DummyAccessibility:
    def snapshot(self, **_kwargs):
        return {"role": "root"}


class DummyMouse:
    def __init__(self) -> None:
        self.calls = []

    def move(self, x, y, **_kwargs):
        self.calls.append(("move", x, y))

    def down(self, **_kwargs):
        self.calls.append(("down",))

    def up(self, **_kwargs):
        self.calls.append(("up",))

    def click(self, x, y, **_kwargs):
        self.calls.append(("click", x, y))


class DummyPage:
    def __init__(self, *, fail_role=False, locator_map=None) -> None:
        self._locator = DummyLocator(fail_once=True)
        self._role_fail = fail_role
        self._locator_map = locator_map or {}
        self.accessibility = DummyAccessibility()
        self.mouse = DummyMouse()
        self.viewport_size = {"width": 800, "height": 600}
        self.url = "https://example.com"
        self._title = "Example"

    def locator(self, selector):
        return self._locator_map.get(selector, self._locator)

    def get_by_role(self, _role, name=None):
        if self._role_fail:
            raise RuntimeError("role")
        return DummyLocator()

    def get_by_label(self, _label):
        return DummyLocator()

    def get_by_placeholder(self, _placeholder):
        return DummyLocator()

    def get_by_text(self, _text):
        return DummyLocator()

    def wait_for_timeout(self, _ms):
        return None

    def goto(self, _url, **_kwargs):
        return None

    def screenshot(self, *, path):
        Path(path).write_bytes(b"image")

    def query_selector(self, _selector):
        return DummyElement()

    def content(self):
        return "<html></html>"

    def title(self):
        return self._title

    def evaluate(self, script, *_args):
        if "innerText" in script:
            return "Hello"
        if "querySelectorAll('form')" in script:
            return [{"id": "f1", "inputs": []}]
        return ""


class DummyBrowser:
    def close(self):
        return None


class DummyContext:
    def storage_state(self, path):
        Path(path).write_text("{}", encoding="utf-8")


class DummyRunner:
    def __init__(self, _cfg, _log) -> None:
        return None

    def attach(self, _playwright, storage_state_path=None):
        return DummyBrowser(), DummyContext(), DummyPage()


class DummySyncPlaywright:
    def __enter__(self):
        return object()

    def __exit__(self, _exc_type, _exc, _tb):
        return False


class FakeRabbit:
    def __init__(self) -> None:
        self.events = []

    async def publish_event(self, payload):
        self.events.append(payload)


class FakeProvisioner:
    async def provision(self, sandbox_id, request, *, owner_id):
        return worker.ProvisionResult(
            status=SandboxStatus.ready,
            browser_url="http://browser",
            dashboard_url="http://dash",
            events_url="http://events",
            message="ready",
            backend_ref="local",
            artifacts_path=None,
        )

    async def stop(self, _sandbox_id, _backend_ref):
        return None


def _record(tmp_path, status=SandboxStatus.ready, sandbox_id="sbx_1"):
    now = datetime.now(timezone.utc)
    return SandboxRecord(
        sandbox_id=sandbox_id,
        status=status,
        created_at=now,
        expires_at=now + timedelta(seconds=1),
        browser_url=None,
        dashboard_url=None,
        events_url="http://events",
        message=None,
        owner_id="user_a",
        cpu_limit="2",
        memory_limit_mb=1024,
        capabilities=["browser", "dashboard"],
        allow_network=None,
        artifacts_path=str(tmp_path),
        cdp_url="http://127.0.0.1:9222",
    )


def test_worker_helpers(tmp_path):
    assert worker._safe_filename("Name 1") == "Name_1"
    assert worker._truncate("abc", 1) == "a"
    assert worker._truncate("abc", 0) == "abc"

    path = tmp_path / "dir"
    worker._ensure_dir(path)
    assert path.exists()

    file_path = tmp_path / "file.txt"
    worker._write_text(file_path, "hello")
    assert file_path.read_text(encoding="utf-8") == "hello"

    record = _record(tmp_path)
    cfg = worker.build_runtime_config(record)
    assert cfg.cdp_endpoint

    record = record.copy(update={"cdp_url": None, "cdp_port": None})
    with pytest.raises(RuntimeError):
        worker.build_runtime_config(record)


def test_build_grade_student_args_prefers_split_llm_envs(monkeypatch):
    monkeypatch.setenv("CANVAS_TOKEN", "canvas-token")
    monkeypatch.setenv("VLLM_API_BASE", "http://127.0.0.1:8001/v1")
    monkeypatch.setenv("VLLM_MODEL", "qwen-ocr")
    monkeypatch.setenv("GRADING_LLM_BASE", "https://api.fireworks.ai/inference/v1")
    monkeypatch.setenv("GRADING_LLM_API_KEY", "fw-key")
    monkeypatch.setenv("GRADING_LLM_MODEL", "accounts/fireworks/models/llama-v3p1-70b-instruct")
    monkeypatch.setenv("ANNOTATION_LLM_MODEL", "accounts/fireworks/models/qwen2p5-vl-32b-instruct")

    payload = GradingJobRequest(
        course_id="course-1",
        assignment_id="assign-1",
        student_id="student-1",
        canvas_base="https://canvas.example.edu",
    )
    args = worker._build_grade_student_args(payload, "internal-secret", owner_id="grader")

    assert args.extraction_llm_base == "http://127.0.0.1:8001/v1"
    assert args.extraction_llm_model == "qwen-ocr"
    assert args.grading_llm_base == "https://api.fireworks.ai/inference/v1"
    assert args.grading_llm_key == "fw-key"
    assert args.grading_llm_model == "accounts/fireworks/models/llama-v3p1-70b-instruct"
    assert args.annotation_llm_model == "accounts/fireworks/models/qwen2p5-vl-32b-instruct"


def test_build_grade_student_args_auto_resolves_latest_shared_profile(monkeypatch):
    monkeypatch.setenv("CANVAS_TOKEN", "canvas-token")

    now = datetime.now(timezone.utc)

    class FakeArtifactRepo:
        def list(self, **_kwargs):
            return [
                ArtifactRecord(
                    artifact_id="art_other",
                    owner_id="grader",
                    session_id=None,
                    sandbox_id=None,
                    artifact_type="browser_profile",
                    source="session_share",
                    run_id=None,
                    volatility=None,
                    artifact_format="json",
                    created_at=now,
                    updated_at=now,
                    tags=[],
                    attributes={"origin": "https://example.com", "domain": "example.com"},
                    blob_path="/tmp/art_other.json",
                ),
                ArtifactRecord(
                    artifact_id="art_canvas",
                    owner_id="grader",
                    session_id=None,
                    sandbox_id=None,
                    artifact_type="browser_profile",
                    source="session_share",
                    run_id=None,
                    volatility=None,
                    artifact_format="json",
                    created_at=now,
                    updated_at=now,
                    tags=[],
                    attributes={
                        "origin": "https://canvas.instructure.com",
                        "domain": "canvas.instructure.com",
                    },
                    blob_path="/tmp/art_canvas.json",
                ),
            ]

    payload = GradingJobRequest(
        course_id="course-1",
        assignment_id="assign-1",
        student_id="student-1",
        canvas_base="https://canvas.instructure.com",
    )

    args = worker._build_grade_student_args(
        payload,
        "internal-secret",
        owner_id="grader",
        artifact_repo=FakeArtifactRepo(),
    )

    assert args.profile_artifact_id == "art_canvas"


def test_resolve_profile_path_errors(tmp_path):
    SQLModel.metadata.create_all(engine)
    repo = ArtifactRepository(engine)
    record = _record(tmp_path)

    with pytest.raises(RuntimeError):
        worker._resolve_profile_path(repo, record=record, artifact_id="missing")

    now = datetime.now(timezone.utc)
    artifact = repo.create(
        ArtifactRecord(
            artifact_id="art_1",
            owner_id="user_a",
            session_id=None,
            sandbox_id=None,
            artifact_type="profile",
            source=None,
            run_id=None,
            volatility=None,
            artifact_format=None,
            created_at=now,
            updated_at=now,
            blob_path=None,
        )
    )
    with pytest.raises(RuntimeError):
        worker._resolve_profile_path(repo, record=record, artifact_id=artifact.artifact_id)

    record = record.copy(update={"owner_id": "other"})
    with pytest.raises(RuntimeError):
        worker._resolve_profile_path(repo, record=record, artifact_id=artifact.artifact_id)

    record = record.copy(update={"owner_id": "user_a"})
    missing_path = tmp_path / "missing.json"
    repo.update_blob(
        artifact.artifact_id,
        blob_path=str(missing_path),
        size_bytes=2,
        checksum_sha256="abc",
        mime_type=None,
    )
    with pytest.raises(RuntimeError):
        worker._resolve_profile_path(repo, record=record, artifact_id=artifact.artifact_id)

    blob_path = tmp_path / "profile.json"
    blob_path.write_text("{}", encoding="utf-8")
    repo.update_blob(
        artifact.artifact_id,
        blob_path=str(blob_path),
        size_bytes=2,
        checksum_sha256="abc",
        mime_type=None,
    )
    assert worker._resolve_profile_path(repo, record=record, artifact_id=artifact.artifact_id)


def test_build_locators_and_perform_actions(monkeypatch):
    page = DummyPage(fail_role=True)
    step = Step(action="click", selector="#id", selector_fallbacks=[".fallback"], role="button")
    locators = worker._build_locators(page, step)
    assert locators

    worker._perform_locator_action(page, step, "click")
    assert "scroll_into_view_if_needed" in page._locator.calls

    step = Step(action="type", selector="#id", text="hi", delay_ms=5)
    worker._perform_locator_action(page, step, "type")

    step = Step(action="wait_for_selector", selector="#id")
    worker._perform_locator_action(page, step, "wait_for_selector")

    with pytest.raises(RuntimeError):
        worker._perform_locator_action(page, Step(action="click"), "click")

    with pytest.raises(RuntimeError):
        worker._perform_locator_action(page, Step(action="click", selector="#id"), "unknown")


def test_perform_locator_action_uses_frame_scope():
    inner = DummyLocator()
    frame = DummyPage(locator_map={"#inner": inner})
    frame_handle = DummyFrameHandle(frame)
    page = DummyPage(locator_map={"#frame": DummyLocator(element_handle=frame_handle)})

    worker._perform_locator_action(
        page,
        Step(action="click", selector="#inner", frame_selector="#frame"),
        "click",
    )

    assert "click" in inner.calls


def test_step_should_skip_when_guard_selector_exists():
    guard = DummyLocator()
    page = DummyPage(locator_map={"#guard": guard})

    assert worker._step_should_skip(page, Step(action="click", selector="#id", skip_if_selector="#guard")) is True


def test_optional_locator_action_ignores_missing_target():
    page = DummyPage(locator_map={})

    worker._perform_locator_action(page, Step(action="click", selector="#missing", optional=True), "click")


def test_pointer_click_falls_back_to_page_mouse():
    page = DummyPage()

    worker._pointer_click(page, 12, 24)

    assert any(call[0] == "move" for call in page.mouse.calls)


def test_capture_page_state_and_execute_steps(tmp_path, monkeypatch):
    SQLModel.metadata.create_all(engine)
    repo = ArtifactRepository(engine)
    store = ArtifactStore(tmp_path)
    record = _record(tmp_path)

    monkeypatch.setattr(worker, "update_overlay", lambda *_args, **_kwargs: None)

    steps = StepsRequest(
        steps=[
            Step(action="goto", url="https://example.com"),
            Step(action="click", selector="#id"),
            Step(action="type", selector="#id", text="hello"),
            Step(action="wait", wait_ms=10),
            Step(action="wait_for_selector", selector="#id"),
            Step(action="dom_snapshot", snapshot_format="a11y_json", selector="#id"),
            Step(action="dom_snapshot", snapshot_format="html"),
            Step(action="screenshot"),
            Step(action="page_state"),
        ],
        screenshot_every_step=True,
    )

    events = []
    artifact_ids = worker._execute_steps(
        DummyPage(),
        steps,
        command_id="cmd_1",
        record=record,
        base_dir=tmp_path,
        artifact_repo=repo,
        artifact_store=store,
        emit_event=events.append,
        log=lambda _msg: None,
    )
    assert artifact_ids
    assert events


def test_run_steps_and_agent_and_profile(tmp_path, monkeypatch):
    SQLModel.metadata.create_all(engine)
    repo = ArtifactRepository(engine)
    store = ArtifactStore(tmp_path)
    record = _record(tmp_path)

    monkeypatch.setattr(worker, "BrowserRunner", DummyRunner)
    monkeypatch.setattr(worker, "sync_playwright", lambda: DummySyncPlaywright())
    monkeypatch.setattr(worker, "update_overlay", lambda *_args, **_kwargs: None)

    steps = StepsRequest(steps=[Step(action="page_state")])
    result = worker._run_steps(
        worker.build_runtime_config(record),
        steps,
        command_id="cmd_1",
        record=record,
        artifact_repo=repo,
        artifact_store=store,
        emit_event=lambda _e: None,
        log=lambda _m: None,
        base_dir=tmp_path,
    )
    assert result["artifact_ids"]

    monkeypatch.setattr(worker, "plan_steps", lambda **_kwargs: steps)
    agent_req = AgentStepsRequest(task="Do", steps=[Step(action="page_state")])
    result = worker._run_agent(
        worker.build_runtime_config(record),
        agent_req,
        command_id="cmd_2",
        record=record,
        artifact_repo=repo,
        artifact_store=store,
        emit_event=lambda _e: None,
        log=lambda _m: None,
    )
    assert result["artifact_ids"]

    result = worker._capture_profile(
        worker.build_runtime_config(record),
        type("Req", (), {"name": "profile"})(),
        command_id="cmd_3",
        record=record,
        artifact_repo=repo,
        artifact_store=store,
        emit_event=lambda _e: None,
        log=lambda _m: None,
    )
    assert result["artifact_id"]


@pytest.mark.asyncio
async def test_publish_event_and_enforce_ttl(monkeypatch, tmp_path):
    repo = SandboxRepository(engine)
    record = repo.create(_record(tmp_path))
    rabbit = FakeRabbit()

    async def fake_sleep(_delay):
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    await worker.enforce_ttl(repo, FakeProvisioner(), rabbit, record.sandbox_id)
    updated = repo.get(record.sandbox_id)
    assert updated.status == SandboxStatus.terminated
    assert rabbit.events


@pytest.mark.asyncio
async def test_handle_provision_and_command_and_job(monkeypatch, tmp_path):
    repo = SandboxRepository(engine)
    artifact_repo = ArtifactRepository(engine)
    artifact_store = ArtifactStore(tmp_path)
    rabbit = FakeRabbit()

    record = repo.create(_record(tmp_path, status=SandboxStatus.requested, sandbox_id="sbx_1"))
    job = ProvisionJob(sandbox_id=record.sandbox_id, owner_id="user_a", request=SandboxRequest())

    monkeypatch.setattr(asyncio, "create_task", lambda _task: None)
    await worker.handle_provision(job, repo, FakeProvisioner(), rabbit)
    updated = repo.get(record.sandbox_id)
    assert updated.status == SandboxStatus.ready

    record = repo.create(_record(tmp_path, status=SandboxStatus.ready, sandbox_id="sbx_2"))

    class DummyLoop:
        async def run_in_executor(self, _executor, func):
            return func()

    monkeypatch.setattr(asyncio, "get_running_loop", lambda: DummyLoop())
    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(worker, "build_runtime_config", lambda *_args, **_kwargs: "cfg")
    monkeypatch.setattr(worker, "run_browser_artifact", lambda *_args, **_kwargs: "art")
    monkeypatch.setattr(worker, "record_session", lambda *_args, **_kwargs: "sess")
    monkeypatch.setattr(worker, "replay_session", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(worker, "_run_steps", lambda *_args, **_kwargs: {"artifact_ids": ["a1"]})
    monkeypatch.setattr(worker, "_run_agent", lambda *_args, **_kwargs: {"artifact_ids": ["a2"]})
    monkeypatch.setattr(worker, "_capture_profile", lambda *_args, **_kwargs: {"artifact_id": "p1"})

    commands = [
        ("run_browser", {"url": "https://example.com"}),
        ("record", {"url": "https://example.com"}),
        ("replay", {"session_id": "sess"}),
        ("steps", {"steps": [{"action": "page_state"}]}),
        ("agent", {"task": "Do", "steps": [{"action": "page_state"}]}),
        ("capture_profile", {"name": "profile"}),
    ]

    for command, payload in commands:
        job = CommandJob(
            sandbox_id=record.sandbox_id,
            owner_id="user_a",
            command_id=f"cmd_{command}",
            command=command,
            payload=payload,
        )
        await worker.handle_command(job, repo, artifact_repo, artifact_store, rabbit)

    class DummyMessage:
        def __init__(self, body: bytes) -> None:
            self.body = body

        def process(self):
            class Dummy:
                async def __aenter__(self):
                    return None

                async def __aexit__(self, *_exc):
                    return None

            return Dummy()

    message = DummyMessage(json.dumps(job.dict()).encode("utf-8"))
    await worker.handle_job(message, repo, artifact_repo, artifact_store, FakeProvisioner(), rabbit)


@pytest.mark.asyncio
async def test_handle_dashboard_update(monkeypatch, tmp_path):
    repo = SandboxRepository(engine)
    artifact_repo = ArtifactRepository(engine)
    rabbit = FakeRabbit()

    record = repo.create(_record(tmp_path))
    job = DashboardUpdateJob(
        sandbox_id=record.sandbox_id,
        owner_id="user_a",
        payload={"title": "Demo"},
    )

    monkeypatch.setattr(worker, "render_dashboard_charts", lambda *_args, **_kwargs: [])
    await worker.handle_dashboard_update(job, repo, rabbit, artifact_repo)
    assert rabbit.events
