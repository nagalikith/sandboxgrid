from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import secrets
import time
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from .artifacts import ArtifactRecord, repository as artifact_repo, store as artifact_store
from .core.internal_auth import internal_auth_dependency


_ALLOWED_HOSTS_ENV = "SESSION_SHARE_ALLOWED_HOSTS"
_ALLOWED_HOSTS_ALT_ENV = "SANDBOX_SESSION_SHARE_ALLOWED_HOSTS"
_TOKENS_ENV = "SESSION_SHARE_TOKENS"
_TOKEN_ENV = "SESSION_SHARE_TOKEN"
_TOKEN_OWNER_ENV = "SESSION_SHARE_OWNER_ID"
_AUTH_HEADER_ENV = "SESSION_SHARE_AUTH_HEADER"
_MAX_COOKIES_ENV = "SESSION_SHARE_MAX_COOKIES"
_MAX_STORAGE_ITEMS_ENV = "SESSION_SHARE_MAX_STORAGE_ITEMS"
_MAX_PAYLOAD_BYTES_ENV = "SESSION_SHARE_MAX_BYTES"

_DOMAIN_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*$", re.IGNORECASE)
_INTERNAL_HEADERS = ("x-internal-timestamp", "x-body-sha256", "x-internal-signature", "x-user-id")

router = APIRouter(tags=["session_share"])


class ShareCookie(BaseModel):
    name: str = Field(min_length=1)
    value: str
    domain: str = Field(min_length=1)
    path: str = "/"
    secure: bool = False
    http_only: bool = Field(default=False, alias="httpOnly")
    same_site: Optional[str] = Field(default=None, alias="sameSite")
    expiration_date: Optional[float] = Field(default=None, alias="expirationDate")

    class Config:
        allow_population_by_field_name = True


class StorageItem(BaseModel):
    name: str = Field(min_length=1)
    value: str


class ShareSessionRequest(BaseModel):
    origin: str = Field(min_length=1)
    url: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    cookies: list[ShareCookie] = Field(default_factory=list)
    local_storage: list[StorageItem] = Field(default_factory=list, alias="localStorage")
    session_storage: list[StorageItem] = Field(default_factory=list, alias="sessionStorage")
    user_agent: Optional[str] = Field(default=None, alias="userAgent")

    class Config:
        allow_population_by_field_name = True


class ShareSessionResponse(BaseModel):
    artifact_id: str
    warnings: list[str] = Field(default_factory=list)
    cookie_count: int


def _parse_allowed_hosts() -> list[str]:
    raw = os.getenv(_ALLOWED_HOSTS_ENV) or os.getenv(_ALLOWED_HOSTS_ALT_ENV, "")
    hosts = [entry.strip().lower() for entry in raw.split(",") if entry.strip()]
    if not hosts:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Session share allowlist not configured.",
        )
    return hosts


def _max_cookies() -> int:
    raw = os.getenv(_MAX_COOKIES_ENV, "200")
    try:
        return int(raw)
    except ValueError:
        return 200


def _max_storage_items() -> int:
    raw = os.getenv(_MAX_STORAGE_ITEMS_ENV, "500")
    try:
        return int(raw)
    except ValueError:
        return 500


def _max_payload_bytes() -> int:
    raw = os.getenv(_MAX_PAYLOAD_BYTES_ENV, "1048576")
    try:
        return int(raw)
    except ValueError:
        return 1048576


def _host_allowed(host: str, allowed_hosts: list[str]) -> bool:
    host = host.lower()
    for entry in allowed_hosts:
        if host == entry or host.endswith(f".{entry}"):
            return True
    return False


def _normalize_domain(domain: str) -> str:
    domain = domain.strip().lower()
    if domain.startswith("."):
        domain = domain[1:]
    return domain


def _valid_domain(domain: str) -> bool:
    if not domain:
        return False
    if any(ch in domain for ch in ("/", " ", "\\")):
        return False
    try:
        ipaddress.ip_address(domain)
        return True
    except ValueError:
        return bool(_DOMAIN_RE.match(domain))


def _domain_matches_origin(domain: str, origin_host: str) -> bool:
    origin_host = origin_host.lower()
    domain = domain.lower()
    return origin_host == domain or origin_host.endswith(f".{domain}")


def _parse_origin(origin: str) -> tuple[str, str]:
    parsed = urlparse(origin)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Origin must be http/https.")
    if not parsed.hostname or not parsed.netloc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Origin is invalid.")
    normalized = f"{parsed.scheme}://{parsed.netloc}"
    return normalized, parsed.hostname.lower()


def _parse_url(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="URL must be http/https.")
    if not parsed.hostname or not parsed.netloc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="URL is invalid.")
    normalized = f"{parsed.scheme}://{parsed.netloc}"
    return normalized, parsed.hostname.lower()


def _parse_token_map() -> dict[str, str]:
    raw = os.getenv(_TOKENS_ENV, "")
    mapping: dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if "=" not in entry:
            continue
        owner_id, token = entry.split("=", 1)
        owner_id = owner_id.strip()
        token = token.strip()
        if owner_id and token:
            mapping[owner_id] = token
    return mapping


def _owner_from_token(token: str) -> str:
    mapping = _parse_token_map()
    if mapping:
        for owner_id, expected in mapping.items():
            if secrets.compare_digest(expected, token):
                return owner_id
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token.")

    fallback = os.getenv(_TOKEN_ENV)
    if not fallback:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Session share token not configured.",
        )
    if not secrets.compare_digest(fallback, token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token.")
    return os.getenv(_TOKEN_OWNER_ENV, "session_share")


async def _maybe_internal_auth(request: Request) -> Optional[str]:
    headers = request.headers
    if any(header in headers for header in _INTERNAL_HEADERS):
        verify = internal_auth_dependency()
        return await verify(
            request,
            x_internal_timestamp=headers.get("x-internal-timestamp"),
            x_body_sha256=headers.get("x-body-sha256"),
            x_internal_signature=headers.get("x-internal-signature"),
            x_user_id=headers.get("x-user-id"),
        )
    return None


def _extract_token(request: Request) -> str:
    header_name = os.getenv(_AUTH_HEADER_ENV, "Authorization")
    header_value = request.headers.get(header_name)
    if not header_value:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing session share auth token.",
        )
    if header_value.lower().startswith("bearer "):
        return header_value[7:].strip()
    return header_value.strip()


async def share_session_auth(request: Request) -> str:
    internal_user = await _maybe_internal_auth(request)
    if internal_user:
        return internal_user
    token = _extract_token(request)
    return _owner_from_token(token)


def _map_cookie(
    cookie: ShareCookie,
    *,
    origin_host: str,
    allowed_hosts: list[str],
    now: int,
) -> tuple[Optional[dict], Optional[str]]:
    domain = _normalize_domain(cookie.domain)
    if not _valid_domain(domain):
        return None, "invalid_domain"
    if not _domain_matches_origin(domain, origin_host):
        return None, "domain_mismatch"
    if not _host_allowed(domain, allowed_hosts):
        return None, "not_allowed"

    expires: int
    if cookie.expiration_date is not None:
        expires = int(cookie.expiration_date)
        if expires <= now:
            return None, "expired"
    else:
        expires = -1

    path = cookie.path or "/"
    if not path.startswith("/"):
        path = "/"

    mapped = {
        "name": cookie.name,
        "value": cookie.value,
        "domain": domain,
        "path": path,
        "expires": expires,
        "httpOnly": cookie.http_only,
        "secure": cookie.secure,
    }
    same_site = (cookie.same_site or "").lower()
    if same_site == "lax":
        mapped["sameSite"] = "Lax"
    elif same_site == "strict":
        mapped["sameSite"] = "Strict"
    return mapped, None


def _build_storage_state(
    payload: ShareSessionRequest,
    *,
    origin: str,
    origin_host: str,
    allowed_hosts: list[str],
) -> tuple[dict, list[str]]:
    warnings: list[str] = []
    dropped: dict[str, int] = {
        "expired": 0,
        "invalid_domain": 0,
        "domain_mismatch": 0,
        "not_allowed": 0,
    }
    now = int(time.time())
    cookies: list[dict] = []
    for cookie in payload.cookies:
        mapped, reason = _map_cookie(cookie, origin_host=origin_host, allowed_hosts=allowed_hosts, now=now)
        if mapped:
            cookies.append(mapped)
        elif reason:
            dropped[reason] += 1

    for key, count in dropped.items():
        if count:
            warnings.append(f"Dropped {count} cookies: {key}.")

    local_storage = [
        {"name": item.name, "value": item.value}
        for item in payload.local_storage
    ]

    if payload.session_storage:
        warnings.append("Session storage ignored.")

    return {"cookies": cookies, "origins": [{"origin": origin, "localStorage": local_storage}]}, warnings


@router.post(
    "/share-session",
    status_code=status.HTTP_201_CREATED,
    response_model=ShareSessionResponse,
    summary="Import a browser storage state from the extension",
)
async def share_session(
    payload: ShareSessionRequest,
    request: Request,
    owner_id: str = Depends(share_session_auth),
) -> ShareSessionResponse:
    max_bytes = _max_payload_bytes()
    body = await request.body()
    if body and len(body) > max_bytes:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Payload too large.")

    if len(payload.cookies) > _max_cookies():
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Too many cookies.")

    if len(payload.local_storage) + len(payload.session_storage) > _max_storage_items():
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Too many storage items.")

    origin, origin_host = _parse_origin(payload.origin)
    url_origin, url_host = _parse_url(payload.url)
    if origin != url_origin or origin_host != url_host:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="URL origin mismatch.")

    domain = _normalize_domain(payload.domain)
    if not _valid_domain(domain):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Domain is invalid.")
    if not _domain_matches_origin(domain, origin_host):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Domain does not match origin.")

    allowed_hosts = _parse_allowed_hosts()
    if not _host_allowed(origin_host, allowed_hosts) or not _host_allowed(domain, allowed_hosts):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Origin not allowed.")

    storage_state, warnings = _build_storage_state(
        payload,
        origin=origin,
        origin_host=origin_host,
        allowed_hosts=allowed_hosts,
    )

    now = datetime.now(timezone.utc)
    record = ArtifactRecord(
        artifact_id=f"art_{uuid4().hex[:12]}",
        owner_id=owner_id,
        session_id=None,
        sandbox_id=None,
        artifact_type="browser_profile",
        source="session_share",
        run_id=None,
        volatility=None,
        artifact_format="json",
        created_at=now,
        updated_at=now,
        size_bytes=None,
        mime_type="application/json",
        filename="storage_state.json",
        checksum_sha256=None,
        tags=["profile", "storage_state", "imported"],
        sensitivity="secret",
        attributes={
            "origin": origin,
            "domain": domain,
            "url": payload.url,
            "user_agent": payload.user_agent,
        },
        blob_path=None,
    )
    created = artifact_repo.create(record)

    blob_path = artifact_store.blob_path(owner_id, created.artifact_id)
    blob_path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(storage_state, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    hasher = hashlib.sha256()
    hasher.update(data)
    blob_path.write_bytes(data)

    updated = artifact_repo.update_blob(
        created.artifact_id,
        blob_path=str(blob_path.resolve()),
        size_bytes=len(data),
        checksum_sha256=hasher.hexdigest(),
        mime_type="application/json",
    )
    if not updated:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to persist artifact.")

    return ShareSessionResponse(
        artifact_id=updated.artifact_id,
        warnings=warnings,
        cookie_count=len(storage_state["cookies"]),
    )
