from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Awaitable, Callable

from fastapi import Header, HTTPException, Request, status


def _auth_secret() -> bytes:
    secret = os.getenv("INTERNAL_AUTH_SECRET")
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Internal auth not configured.",
        )
    return secret.encode("utf-8")


def _max_skew_seconds() -> int:
    raw = os.getenv("INTERNAL_AUTH_MAX_SKEW_SECONDS", "300")
    try:
        return int(raw)
    except ValueError:
        return 300


def _canonical_path(request: Request) -> str:
    path = request.url.path
    query = request.url.query
    if query:
        return f"{path}?{query}"
    return path


def internal_auth_dependency(enforce_body_hash: bool = True) -> Callable[..., Awaitable[str]]:
    async def _verify(
        request: Request,
        x_internal_timestamp: str | None = Header(default=None, alias="X-Internal-Timestamp"),
        x_body_sha256: str | None = Header(default=None, alias="X-Body-SHA256"),
        x_internal_signature: str | None = Header(default=None, alias="X-Internal-Signature"),
        x_user_id: str | None = Header(default=None, alias="X-User-Id"),
    ) -> str:
        if not x_internal_timestamp or not x_body_sha256 or not x_internal_signature or not x_user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing internal auth headers.",
            )

        try:
            timestamp = int(x_internal_timestamp)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid timestamp.",
            ) from exc

        now = int(time.time())
        if abs(now - timestamp) > _max_skew_seconds():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Signature expired.",
            )

        if enforce_body_hash:
            body = await request.body()
            body_hash = hashlib.sha256(body).hexdigest()
            if body_hash != x_body_sha256:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Body hash mismatch.",
                )
            request.state.body_sha256 = body_hash

        request.state.expected_body_sha256 = x_body_sha256

        signature_payload = (
            f"{x_internal_timestamp}\n"
            f"{request.method.upper()}\n"
            f"{_canonical_path(request)}\n"
            f"{x_body_sha256}"
        )
        expected = hmac.new(
            _auth_secret(),
            signature_payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, x_internal_signature):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid signature.",
            )
        return x_user_id

    return _verify
