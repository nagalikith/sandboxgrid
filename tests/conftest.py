import importlib
import os
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete


@pytest.fixture(scope="session")
def client(tmp_path_factory):
    base_dir = tmp_path_factory.mktemp("artifact-tests")
    db_path = base_dir / "test.db"
    artifacts_root = base_dir / "artifacts"

    os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
    os.environ["SANDBOX_ARTIFACTS_ROOT"] = str(artifacts_root)
    os.environ["INTERNAL_AUTH_SECRET"] = "test-secret"

    import sandbox_api.database as database
    importlib.reload(database)
    import sandbox_api.artifacts as artifacts
    importlib.reload(artifacts)
    import sandbox_api.main as main
    importlib.reload(main)

    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def reset_state(client):
    import sandbox_api.artifacts as artifacts
    from sandbox_api.database import engine

    with Session(engine) as session:
        session.exec(delete(artifacts.ArtifactLinkRow))
        session.exec(delete(artifacts.ArtifactRow))
        session.commit()

    root = artifacts.store.root
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)


@pytest.fixture()
def artifacts_root():
    return Path(os.environ["SANDBOX_ARTIFACTS_ROOT"]).resolve()
