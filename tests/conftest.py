import importlib
import os
import shutil
import sys
from pathlib import Path

# Module-level engines (e.g. sandbox_api.artifacts.engine) are created at
# collection time, before any fixture sets DATABASE_URL. Default them to a
# per-process scratch DB so tests never write into the repo's sandbox.db.
os.environ.setdefault("DATABASE_URL", f"sqlite:////tmp/cua_sandbox_{os.getpid()}.db")
# Tests sign internal requests with a fixed secret (see tests/auth_helpers.py),
# so the app must use that exact secret. Force it rather than setdefault so an
# ambient INTERNAL_AUTH_SECRET (e.g. from sourcing .cursor/dev.env in the same
# shell) cannot break HMAC verification.
os.environ["INTERNAL_AUTH_SECRET"] = "test-secret"
os.environ.setdefault("SANDBOX_ARTIFACTS_ROOT", f"/tmp/cua_sandbox_{os.getpid()}/artifacts")

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, delete

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SANDBOX_TEST_FILES = [
    "test_agent_models.py",
    "test_agent_planner.py",
    "test_artifacts_api.py",
    "test_artifacts_extra.py",
    "test_charts_renderer.py",
    "test_command_models_extra.py",
    "test_commands_api.py",
    "test_dashboard_api.py",
    "test_database.py",
    "test_events.py",
    "test_events_models.py",
    "test_internal_auth.py",
    "test_jobs.py",
    "test_main_api.py",
    "test_models.py",
    "test_orchestrator.py",
    "test_package_init.py",
    "test_paths.py",
    "test_paths_extra.py",
    "test_provisioner.py",
    "test_rabbitmq.py",
    "test_share_session.py",
    "test_storage.py",
    "test_worker.py",
]

if os.environ.get("SKIP_SANDBOX_API_TESTS") == "1":
    collect_ignore = SANDBOX_TEST_FILES


@pytest.fixture(scope="session", autouse=True)
def _sandbox_schema():
    """Ensure module-level engines (created at collection with the scratch
    DATABASE_URL default) have tables for tests that bypass the client fixture."""
    from sandbox_api.core.database import engine

    SQLModel.metadata.create_all(engine)
    yield


@pytest.fixture(scope="session")
def _sandbox_app():
    """Build the sandbox FastAPI app ONCE per session.

    The sandbox modules bind engine/repository/store to env at import time;
    conftest pins DATABASE_URL / SANDBOX_ARTIFACTS_ROOT / INTERNAL_AUTH_SECRET
    at session scope, so one import + one TestClient serve every test. Per-test
    isolation is provided by the client fixture's row/blob cleanup instead of
    the old (very slow) per-test module reload dance.
    """
    import sandbox_api.api.app as _app
    import sandbox_api.artifacts as artifacts
    import sandbox_api.core.database as database
    import sandbox_api.core.rabbitmq as rabbitmq_module
    import sandbox_api.main as main
    import sandbox_api.sandboxes.storage as storage

    async def _noop(*_args, **_kwargs) -> None:
        return None

    rabbitmq_module.rabbitmq.connect = _noop
    rabbitmq_module.rabbitmq.consume_events = _noop
    rabbitmq_module.rabbitmq.close = _noop
    rabbitmq_module.rabbitmq.publish_job = _noop
    rabbitmq_module.rabbitmq.publish_event = _noop

    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture()
def client(_sandbox_app):
    """Function-scoped client with a clean DB + artifacts dir per test."""
    import sandbox_api.artifacts as artifacts
    import sandbox_api.sandboxes.storage as storage
    from sandbox_api.core.database import engine

    with Session(engine) as session:
        session.exec(delete(artifacts.ArtifactLinkRow))
        session.exec(delete(artifacts.ArtifactRow))
        session.exec(delete(storage.SandboxRow))
        session.commit()

    root = artifacts.store.root
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    return _sandbox_app


@pytest.fixture()
def reset_state(client):
    import sandbox_api.artifacts as artifacts
    import sandbox_api.sandboxes.storage as storage
    from sandbox_api.core.database import engine

    with Session(engine) as session:
        session.exec(delete(artifacts.ArtifactLinkRow))
        session.exec(delete(artifacts.ArtifactRow))
        session.exec(delete(storage.SandboxRow))
        session.commit()

    root = artifacts.store.root
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)


@pytest.fixture()
def artifacts_root():
    return Path(os.environ["SANDBOX_ARTIFACTS_ROOT"]).resolve()
