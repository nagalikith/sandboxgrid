import json
from datetime import datetime, timezone

import pytest
from sqlmodel import SQLModel

from sandbox_api.platform.artifacts import ArtifactLinkRecord, ArtifactRecord, ArtifactRepository, ArtifactStore
from sandbox_api.core.database import engine
from sandbox_api.platform.core.paths import owner_directory
from tests.auth_helpers import build_internal_headers


def _create_artifact(client, payload, agent_id="user_a"):
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    headers = build_internal_headers(
        "POST",
        "/artifacts",
        body=body,
        user_id=agent_id,
        content_type="application/json",
    )
    response = client.post("/artifacts", data=body, headers=headers)
    assert response.status_code == 201
    return response.json()


def test_artifact_store_blob_pointer(tmp_path):
    store = ArtifactStore(tmp_path)
    record = ArtifactRecord(
        artifact_id="art_1",
        owner_id="user_a",
        session_id=None,
        sandbox_id=None,
        artifact_type="log",
        source=None,
        run_id=None,
        volatility=None,
        artifact_format=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        blob_path=str(tmp_path / "users" / owner_directory("user_a") / "objects" / "art_1"),
    )
    pointer = store.blob_pointer(record)
    assert pointer
    assert pointer.location == "local_path"

    record.blob_path = "/outside/path"
    pointer = store.blob_pointer(record)
    assert pointer
    assert pointer.uri == "/outside/path"


def test_repository_parents_for_and_touch():
    SQLModel.metadata.create_all(engine)
    repo = ArtifactRepository(engine)
    now = datetime.now(timezone.utc)
    record = repo.create(
        ArtifactRecord(
            artifact_id="art_parent",
            owner_id="user_a",
            session_id=None,
            sandbox_id=None,
            artifact_type="log",
            source=None,
            run_id=None,
            volatility=None,
            artifact_format=None,
            created_at=now,
            updated_at=now,
        )
    )
    child = repo.create(
        ArtifactRecord(
            artifact_id="art_child",
            owner_id="user_a",
            session_id=None,
            sandbox_id=None,
            artifact_type="log",
            source=None,
            run_id=None,
            volatility=None,
            artifact_format=None,
            created_at=now,
            updated_at=now,
        )
    )
    repo.add_links(
        [
            ArtifactLinkRecord(
                parent_id=record.artifact_id,
                child_id=child.artifact_id,
                owner_id="user_a",
                relation="derived",
                created_at=now,
            )
        ]
    )
    mapping = repo.parents_for(owner_id="user_a", child_ids=[child.artifact_id])
    assert mapping[child.artifact_id] == [record.artifact_id]

    touched = repo.touch(child.artifact_id)
    assert touched


def test_upload_blob_errors(client, artifacts_root):
    created = _create_artifact(client, {"type": "log", "format": "raw"})
    artifact_id = created["artifact_id"]

    headers = build_internal_headers(
        "PUT",
        f"/artifacts/{artifact_id}/blob",
        body=b"",
        content_type="application/octet-stream",
    )
    response = client.put(f"/artifacts/{artifact_id}/blob", headers=headers, data=b"")
    assert response.status_code == 400

    body = b"hello"
    headers = build_internal_headers(
        "PUT",
        f"/artifacts/{artifact_id}/blob",
        body=body,
        content_type="application/octet-stream",
    )
    headers["X-Body-SHA256"] = "bad"
    response = client.put(f"/artifacts/{artifact_id}/blob", headers=headers, data=body)
    assert response.status_code == 401

    headers = build_internal_headers(
        "PUT",
        f"/artifacts/{artifact_id}/blob",
        body=body,
        content_type="application/octet-stream",
        user_id="other",
    )
    response = client.put(f"/artifacts/{artifact_id}/blob", headers=headers, data=body)
    assert response.status_code == 403

    headers = build_internal_headers(
        "PUT",
        "/artifacts/missing/blob",
        body=body,
        content_type="application/octet-stream",
    )
    response = client.put("/artifacts/missing/blob", headers=headers, data=body)
    assert response.status_code == 404


def test_upload_blob_presign(client):
    created = _create_artifact(client, {"type": "log", "format": "raw"})
    artifact_id = created["artifact_id"]
    params = {"presign": "true"}
    headers = build_internal_headers(
        "PUT",
        f"/artifacts/{artifact_id}/blob",
        body=b"",
        params=params,
    )
    response = client.put(f"/artifacts/{artifact_id}/blob", headers=headers, params=params)
    assert response.status_code == 200
    assert response.json()["upload"]["upload_url"]
