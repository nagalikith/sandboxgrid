import importlib
import os
import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, delete

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def client(tmp_path_factory):
    base_dir = tmp_path_factory.mktemp("artifact-tests")
    db_path = base_dir / "test.db"
    artifacts_root = base_dir / "artifacts"

    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ["SANDBOX_ARTIFACTS_ROOT"] = str(artifacts_root)
    os.environ["INTERNAL_AUTH_SECRET"] = "test-secret"

    SQLModel.metadata.clear()
    import sandbox_api.database as database
    importlib.reload(database)
    import sandbox_api.artifacts as artifacts
    importlib.reload(artifacts)
    import sandbox_api.storage as storage
    importlib.reload(storage)
    import sandbox_api.main as main
    importlib.reload(main)
    import sandbox_api.rabbitmq as rabbitmq_module

    async def _noop(*_args, **_kwargs) -> None:
        return None

    rabbitmq_module.rabbitmq.connect = _noop
    rabbitmq_module.rabbitmq.consume_events = _noop
    rabbitmq_module.rabbitmq.close = _noop
    rabbitmq_module.rabbitmq.publish_job = _noop
    rabbitmq_module.rabbitmq.publish_event = _noop

    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def reset_state(client):
    import sandbox_api.artifacts as artifacts
    import sandbox_api.storage as storage
    from sandbox_api.database import engine

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
