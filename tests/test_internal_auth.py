import hashlib
import hmac
import time

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from sandbox_api.platform.core import internal_auth


def _make_request(method: str, path: str, body: bytes, *, query: str = "") -> Request:
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": query.encode("utf-8"),
        "headers": [],
        "scheme": "http",
        "client": ("testclient", 123),
        "server": ("testserver", 80),
    }

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def _sign_headers(method: str, path: str, body: bytes, *, user_id: str, secret: bytes) -> dict[str, str]:
    timestamp = str(int(time.time()))
    body_hash = hashlib.sha256(body).hexdigest()
    signature_payload = f"{timestamp}\n{method.upper()}\n{path}\n{body_hash}"
    signature = hmac.new(secret, signature_payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return {
        "X-Internal-Timestamp": timestamp,
        "X-Body-SHA256": body_hash,
        "X-Internal-Signature": signature,
        "X-User-Id": user_id,
    }


def test_auth_secret_missing(monkeypatch):
    monkeypatch.delenv("INTERNAL_AUTH_SECRET", raising=False)
    with pytest.raises(HTTPException) as exc:
        internal_auth._auth_secret()
    assert exc.value.status_code == 503


def test_max_skew_seconds_invalid(monkeypatch):
    monkeypatch.setenv("INTERNAL_AUTH_MAX_SKEW_SECONDS", "bad")
    assert internal_auth._max_skew_seconds() == 300


def test_canonical_path_with_query():
    request = _make_request("GET", "/artifacts", b"", query="a=1&b=two")
    assert internal_auth._canonical_path(request) == "/artifacts?a=1&b=two"


@pytest.mark.asyncio
async def test_internal_auth_dependency_success(monkeypatch):
    secret = b"test-secret"
    monkeypatch.setenv("INTERNAL_AUTH_SECRET", secret.decode("utf-8"))
    body = b"hello"
    request = _make_request("POST", "/artifacts", body)
    headers = _sign_headers("POST", "/artifacts", body, user_id="user_a", secret=secret)
    verify = internal_auth.internal_auth_dependency()
    user_id = await verify(
        request,
        x_internal_timestamp=headers["X-Internal-Timestamp"],
        x_body_sha256=headers["X-Body-SHA256"],
        x_internal_signature=headers["X-Internal-Signature"],
        x_user_id=headers["X-User-Id"],
    )
    assert user_id == "user_a"
    assert request.state.body_sha256 == headers["X-Body-SHA256"]
    assert request.state.expected_body_sha256 == headers["X-Body-SHA256"]


@pytest.mark.asyncio
async def test_internal_auth_dependency_invalid_signature(monkeypatch):
    secret = b"test-secret"
    monkeypatch.setenv("INTERNAL_AUTH_SECRET", secret.decode("utf-8"))
    body = b"payload"
    request = _make_request("POST", "/artifacts", body)
    headers = _sign_headers("POST", "/artifacts", body, user_id="user_a", secret=secret)
    headers["X-Internal-Signature"] = "bad"
    verify = internal_auth.internal_auth_dependency()
    with pytest.raises(HTTPException) as exc:
        await verify(
            request,
            x_internal_timestamp=headers["X-Internal-Timestamp"],
            x_body_sha256=headers["X-Body-SHA256"],
            x_internal_signature=headers["X-Internal-Signature"],
            x_user_id=headers["X-User-Id"],
        )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_internal_auth_dependency_without_body_hash(monkeypatch):
    secret = b"test-secret"
    monkeypatch.setenv("INTERNAL_AUTH_SECRET", secret.decode("utf-8"))
    body = b"payload"
    request = _make_request("POST", "/artifacts", body)
    headers = _sign_headers("POST", "/artifacts", body, user_id="user_b", secret=secret)
    verify = internal_auth.internal_auth_dependency(enforce_body_hash=False)
    user_id = await verify(
        request,
        x_internal_timestamp=headers["X-Internal-Timestamp"],
        x_body_sha256=headers["X-Body-SHA256"],
        x_internal_signature=headers["X-Internal-Signature"],
        x_user_id=headers["X-User-Id"],
    )
    assert user_id == "user_b"
    assert not hasattr(request.state, "body_sha256")
    assert request.state.expected_body_sha256 == headers["X-Body-SHA256"]
