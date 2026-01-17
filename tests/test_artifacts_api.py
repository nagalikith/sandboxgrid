import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from sandbox_api.paths import owner_directory


_INTERNAL_SECRET = b"test-secret"


def _auth_headers(
    method: str,
    path: str,
    *,
    body: bytes = b"",
    user_id: str = "user_a",
    params=None,
    content_type: str | None = None,
):
    timestamp = str(int(time.time()))
    query = urlencode(params, doseq=True) if params else ""
    full_path = f"{path}?{query}" if query else path
    body_hash = hashlib.sha256(body).hexdigest()
    signature_payload = f"{timestamp}\n{method.upper()}\n{full_path}\n{body_hash}"
    signature = hmac.new(
        _INTERNAL_SECRET,
        signature_payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    headers = {
        "X-Internal-Timestamp": timestamp,
        "X-Body-SHA256": body_hash,
        "X-Internal-Signature": signature,
        "X-User-Id": user_id,
    }
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def _create_artifact(client, payload, agent_id="user_a"):
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    headers = _auth_headers(
        "POST",
        "/artifacts",
        body=body,
        user_id=agent_id,
        content_type="application/json",
    )
    response = client.post("/artifacts", data=body, headers=headers)
    assert response.status_code == 201
    return response.json()


def test_create_and_get_artifact(client):
    payload = {
        "type": "screenshot",
        "source": "app://chat",
        "run_id": "run_001",
        "sensitivity": "Internal",
        "volatility": "Session",
        "format": "raw",
        "tags": ["project-x", "ui"],
    }
    created = _create_artifact(client, payload)

    assert created["type"] == payload["type"]
    assert created["source"] == payload["source"]
    assert created["run_id"] == payload["run_id"]
    assert created["sensitivity"] == payload["sensitivity"]
    assert created["volatility"] == payload["volatility"]
    assert created["format"] == payload["format"]
    assert created["hash"] is None
    assert created["parents"] == []
    assert created["created_at"]

    artifact_id = created["artifact_id"]
    headers = _auth_headers("GET", f"/artifacts/{artifact_id}")
    response = client.get(f"/artifacts/{artifact_id}", headers=headers)
    assert response.status_code == 200
    fetched = response.json()
    assert fetched["artifact_id"] == artifact_id
    assert fetched["type"] == payload["type"]
    assert fetched["parents"] == []


def test_upload_blob_sets_hash_and_persists(client, artifacts_root):
    created = _create_artifact(
        client,
        {
            "type": "log",
            "source": "app://chat",
            "run_id": "run_002",
            "volatility": "Session",
            "format": "raw",
        },
    )
    artifact_id = created["artifact_id"]
    body = b"hello world"
    headers = _auth_headers(
        "PUT",
        f"/artifacts/{artifact_id}/blob",
        body=body,
        content_type="application/octet-stream",
    )
    response = client.put(
        f"/artifacts/{artifact_id}/blob",
        headers=headers,
        data=body,
    )
    assert response.status_code == 200
    updated = response.json()["artifact"]
    assert updated["hash"] == hashlib.sha256(body).hexdigest()
    assert updated["size_bytes"] == len(body)

    owner_dir = owner_directory("user_a")
    blob_path = artifacts_root / "users" / owner_dir / "objects" / artifact_id
    assert blob_path.exists()
    assert blob_path.read_bytes() == body


def test_list_filters_and_tags(client):
    first = _create_artifact(
        client,
        {
            "type": "screenshot",
            "source": "app://chat",
            "run_id": "run_a",
            "volatility": "Session",
            "format": "raw",
            "tags": ["project-x", "alpha"],
        },
    )
    _create_artifact(
        client,
        {
            "type": "log",
            "source": "app://chat",
            "run_id": "run_b",
            "volatility": "Durable",
            "format": "structured",
            "tags": ["project-x", "beta"],
        },
    )

    params = {"type": "screenshot"}
    headers = _auth_headers("GET", "/artifacts", params=params)
    response = client.get("/artifacts", headers=headers, params=params)
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["artifact_id"] == first["artifact_id"]

    params = [("tags", "project-x"), ("tags", "alpha")]
    headers = _auth_headers("GET", "/artifacts", params=params)
    response = client.get("/artifacts", headers=headers, params=params)
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["artifact_id"] == first["artifact_id"]


def test_derive_and_manifest(client):
    parent = _create_artifact(
        client,
        {"type": "screenshot", "source": "app://chat", "run_id": "run_003", "format": "raw"},
    )
    child = _create_artifact(
        client,
        {"type": "thumbnail", "source": "app://chat", "run_id": "run_003", "format": "raw"},
    )

    payload = {"parent_ids": [parent["artifact_id"]], "relation": "derived"}
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    headers = _auth_headers(
        "POST",
        f"/artifacts/{child['artifact_id']}/derive",
        body=body,
        content_type="application/json",
    )
    response = client.post(
        f"/artifacts/{child['artifact_id']}/derive",
        data=body,
        headers=headers,
    )
    assert response.status_code == 200

    headers = _auth_headers("GET", f"/artifacts/{child['artifact_id']}")
    response = client.get(f"/artifacts/{child['artifact_id']}", headers=headers)
    assert response.status_code == 200
    assert response.json()["parents"] == [parent["artifact_id"]]

    key_artifact = _create_artifact(
        client,
        {
            "type": "screenshot",
            "session_id": "sess_001",
            "tags": ["key"],
        },
    )
    _create_artifact(
        client,
        {
            "type": "log",
            "session_id": "sess_001",
            "tags": ["misc"],
        },
    )

    headers = _auth_headers("GET", "/sessions/sess_001/manifest")
    response = client.get("/sessions/sess_001/manifest", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 1
    assert data["artifacts"][0]["artifact_id"] == key_artifact["artifact_id"]


def test_owner_isolation(client):
    created = _create_artifact(
        client,
        {"type": "screenshot", "source": "app://chat"},
        agent_id="user_a",
    )
    response = client.get(
        f"/artifacts/{created['artifact_id']}",
        headers=_auth_headers("GET", f"/artifacts/{created['artifact_id']}", user_id="user_b"),
    )
    assert response.status_code == 403
