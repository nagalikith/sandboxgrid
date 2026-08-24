import json

from sandbox_api import main
from tests.auth_helpers import build_internal_headers


def _headers(method: str, path: str, body: bytes = b"", user_id: str = "user_a", params=None):
    content_type = "application/json" if body else None
    return build_internal_headers(
        method, path, body=body, user_id=user_id, content_type=content_type, params=params
    )


def test_ui_routes(client):
    response = client.get("/ui")
    assert response.status_code == 200
    response = client.get("/chat-ui")
    assert response.status_code == 200


def test_create_and_get_sandbox(client):
    payload = {"ttl_seconds": 120, "capabilities": ["browser", "dashboard"]}
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    response = client.post("/sandboxes", data=body, headers=_headers("POST", "/sandboxes", body))
    assert response.status_code == 201
    sandbox_id = response.json()["sandbox_id"]

    headers = _headers("GET", f"/sandboxes/{sandbox_id}")
    response = client.get(f"/sandboxes/{sandbox_id}", headers=headers)
    assert response.status_code == 200
    assert response.json()["sandbox_id"] == sandbox_id


def test_get_sandbox_forbidden(client):
    payload = {"ttl_seconds": 120, "capabilities": ["browser"]}
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    response = client.post("/sandboxes", data=body, headers=_headers("POST", "/sandboxes", body))
    sandbox_id = response.json()["sandbox_id"]

    headers = _headers("GET", f"/sandboxes/{sandbox_id}", user_id="other")
    response = client.get(f"/sandboxes/{sandbox_id}", headers=headers)
    assert response.status_code == 403


def test_create_sandbox_queue_failure(client, monkeypatch):
    async def fail_publish(_job):
        raise RuntimeError("queue")

    monkeypatch.setattr(main.rabbitmq, "publish_job", fail_publish)

    body = json.dumps({"ttl_seconds": 120}).encode("utf-8")
    response = client.post("/sandboxes", data=body, headers=_headers("POST", "/sandboxes", body))
    assert response.status_code == 503


def test_publish_agent_event(client):
    payload = {"ttl_seconds": 120, "capabilities": ["browser"]}
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    response = client.post("/sandboxes", data=body, headers=_headers("POST", "/sandboxes", body))
    sandbox_id = response.json()["sandbox_id"]

    event_body = json.dumps({"type": "agent", "label": "ok"}).encode("utf-8")
    headers = _headers("POST", f"/sandboxes/{sandbox_id}/events/agent", event_body)
    response = client.post(f"/sandboxes/{sandbox_id}/events/agent", data=event_body, headers=headers)
    assert response.status_code == 202


def test_sandbox_events_stream(client):
    payload = {"ttl_seconds": 120, "capabilities": ["browser"]}
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    response = client.post("/sandboxes", data=body, headers=_headers("POST", "/sandboxes", body))
    sandbox_id = response.json()["sandbox_id"]

    path = f"/sandboxes/{sandbox_id}/events"
    params = {"max_events": 1}
    headers = _headers("GET", path, params=params)
    with client.stream("GET", f"{path}?max_events=1", headers=headers) as response:
        assert response.status_code == 200
        lines = [
            chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
            for chunk in response.iter_lines()
        ]
    assert any("connected" in line for line in lines)
