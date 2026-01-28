import json
import time

import pytest

from sandbox_api.share_session import ShareCookie, ShareSessionRequest, _build_storage_state, _map_cookie
from tests.auth_helpers import build_internal_headers


def _payload(**overrides):
    now = int(time.time())
    payload = {
        "origin": "https://example.com",
        "url": "https://example.com/path",
        "domain": "example.com",
        "cookies": [
            {
                "name": "sid",
                "value": "abc",
                "domain": "example.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
                "sameSite": "lax",
                "expirationDate": now + 3600,
            }
        ],
        "localStorage": [{"name": "k", "value": "v"}],
        "sessionStorage": [{"name": "ignored", "value": "x"}],
        "userAgent": "test-agent",
    }
    payload.update(overrides)
    return payload


def test_map_cookie_filters_expired():
    now = int(time.time())
    cookie = ShareCookie(
        name="sid",
        value="abc",
        domain="example.com",
        path="/",
        secure=True,
        httpOnly=True,
        sameSite="lax",
        expirationDate=now - 10,
    )
    mapped, reason = _map_cookie(
        cookie,
        origin_host="example.com",
        allowed_hosts=["example.com"],
        now=now,
    )
    assert mapped is None
    assert reason == "expired"


def test_map_cookie_strips_domain_and_maps_samesite():
    now = int(time.time())
    cookie = ShareCookie(
        name="sid",
        value="abc",
        domain=".example.com",
        path="",
        secure=False,
        httpOnly=False,
        sameSite="strict",
        expirationDate=now + 100,
    )
    mapped, reason = _map_cookie(
        cookie,
        origin_host="app.example.com",
        allowed_hosts=["example.com"],
        now=now,
    )
    assert reason is None
    assert mapped["domain"] == "example.com"
    assert mapped["sameSite"] == "Strict"
    assert mapped["path"] == "/"


def test_build_storage_state_warns_on_session_storage():
    payload = ShareSessionRequest.parse_obj(_payload())
    storage_state, warnings = _build_storage_state(
        payload,
        origin="https://example.com",
        origin_host="example.com",
        allowed_hosts=["example.com"],
    )
    assert storage_state["cookies"]
    assert warnings
    assert "Session storage ignored." in warnings


def test_share_session_endpoint_creates_profile(client, artifacts_root, monkeypatch):
    monkeypatch.setenv("SESSION_SHARE_ALLOWED_HOSTS", "example.com")
    monkeypatch.setenv("SESSION_SHARE_TOKENS", "user_a=token123")

    payload = _payload()
    response = client.post(
        "/share-session",
        data=json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8"),
        headers={"Authorization": "Bearer token123", "Content-Type": "application/json"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["artifact_id"]
    assert data["cookie_count"] == 1

    headers = build_internal_headers("GET", f"/artifacts/{data['artifact_id']}")
    artifact_response = client.get(f"/artifacts/{data['artifact_id']}", headers=headers)
    assert artifact_response.status_code == 200
    artifact = artifact_response.json()
    assert artifact["type"] == "browser_profile"
    assert artifact["blob"]["uri"]

    blob_path = artifacts_root / artifact["blob"]["uri"]
    assert blob_path.exists()
    stored = json.loads(blob_path.read_text(encoding="utf-8"))
    assert stored["cookies"]


def test_share_session_rejects_missing_auth(client, monkeypatch):
    monkeypatch.setenv("SESSION_SHARE_ALLOWED_HOSTS", "example.com")
    monkeypatch.setenv("SESSION_SHARE_TOKENS", "user_a=token123")
    response = client.post(
        "/share-session",
        json=_payload(),
    )
    assert response.status_code == 401


def test_share_session_rejects_unapproved_origin(client, monkeypatch):
    monkeypatch.setenv("SESSION_SHARE_ALLOWED_HOSTS", "example.com")
    monkeypatch.setenv("SESSION_SHARE_TOKENS", "user_a=token123")
    payload = _payload(origin="https://evil.com", url="https://evil.com/path", domain="evil.com")
    response = client.post(
        "/share-session",
        json=payload,
        headers={"Authorization": "Bearer token123"},
    )
    assert response.status_code == 403
