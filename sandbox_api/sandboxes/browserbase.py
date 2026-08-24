"""Browserbase cloud-browser backend.

Thin async REST client over the Browserbase Sessions/Contexts APIs plus a
drop-in offline mock so the whole platform (provisioner, artifacts, demos)
runs without credentials. See https://docs.browserbase.com.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("sandbox.browserbase")

DEFAULT_BASE_URL = "https://api.browserbase.com/v1"
DEFAULT_CONNECT_URL = "wss://connect.browserbase.com"


@dataclass
class SessionInfo:
    id: str
    connect_url: str
    status: str = "RUNNING"
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LiveView:
    url: str
    pages: List[str] = field(default_factory=list)


class BrowserbaseError(RuntimeError):
    pass


class BrowserbaseClient:
    """Async client for the Browserbase REST API."""

    def __init__(
        self,
        *,
        api_key: str,
        project_id: str,
        base_url: str = DEFAULT_BASE_URL,
        connect_url: str = DEFAULT_CONNECT_URL,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._api_key = api_key
        self._project_id = project_id
        self._base_url = base_url.rstrip("/")
        self._connect_url = connect_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def _headers(self) -> Dict[str, str]:
        return {"x-bb-api-key": self._api_key, "Content-Type": "application/json"}

    async def create_session(
        self,
        *,
        context_id: Optional[str] = None,
        persist: bool = True,
        region: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        proxies: bool = False,
    ) -> SessionInfo:
        body: Dict[str, Any] = {"projectId": self._project_id}
        browser_settings: Dict[str, Any] = {}
        if context_id:
            browser_settings["context"] = {"id": context_id, "persist": persist}
        if region:
            body["region"] = region
        if timeout_seconds:
            body["timeout"] = timeout_seconds
        if proxies:
            body["proxies"] = True
        if browser_settings:
            body["browserSettings"] = browser_settings

        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            response = await client.post(
                f"{self._base_url}/sessions", json=body, headers=self._headers()
            )
        if response.status_code >= 400:
            raise BrowserbaseError(f"create_session failed: {response.status_code} {response.text}")
        data = response.json()
        session_id = data.get("id")
        if not session_id:
            raise BrowserbaseError(f"create_session returned no id: {data}")
        connect_url = data.get("connectUrl") or f"{self._connect_url}?sessionId={session_id}"
        return SessionInfo(
            id=session_id,
            connect_url=f"{connect_url}&apiKey={self._api_key}"
            if "?" in connect_url
            else f"{connect_url}?apiKey={self._api_key}",
            status=data.get("status", "RUNNING"),
            raw=data,
        )

    async def get_session(self, session_id: str) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            response = await client.get(
                f"{self._base_url}/sessions/{session_id}", headers=self._headers()
            )
        if response.status_code >= 400:
            raise BrowserbaseError(f"get_session failed: {response.status_code} {response.text}")
        return response.json()

    async def release_session(self, session_id: str) -> None:
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            response = await client.post(
                f"{self._base_url}/sessions/{session_id}",
                json={"status": "REQUEST_RELEASE"},
                headers=self._headers(),
            )
        if response.status_code >= 400:
            raise BrowserbaseError(
                f"release_session failed: {response.status_code} {response.text}"
            )

    async def get_live_view(self, session_id: str) -> LiveView:
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            response = await client.get(
                f"{self._base_url}/sessions/{session_id}/debug",
                headers=self._headers(),
            )
        if response.status_code >= 400:
            raise BrowserbaseError(f"get_live_view failed: {response.status_code} {response.text}")
        data = response.json()
        url = data.get("debuggerFullscreenUrl") or data.get("debuggerUrl") or ""
        pages = [p.get("debuggerFullscreenUrl", "") for p in data.get("pages", [])]
        return LiveView(url=url, pages=[p for p in pages if p])

    async def create_context(self) -> str:
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            response = await client.post(
                f"{self._base_url}/contexts",
                json={"projectId": self._project_id},
                headers=self._headers(),
            )
        if response.status_code >= 400:
            raise BrowserbaseError(f"create_context failed: {response.status_code} {response.text}")
        data = response.json()
        context_id = data.get("id")
        if not context_id:
            raise BrowserbaseError(f"create_context returned no id: {data}")
        return context_id

    async def get_recording(self, session_id: str) -> bytes:
        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            response = await client.get(
                f"{self._base_url}/sessions/{session_id}/recording",
                headers=self._headers(),
            )
        if response.status_code >= 400:
            raise BrowserbaseError(f"get_recording failed: {response.status_code} {response.text}")
        return response.content


class MockBrowserbaseClient:
    """Offline stand-in with the same surface as :class:`BrowserbaseClient`.

    Sessions are deterministic per-process and the live view points at a
    placeholder URL so demos and tests run without network or credentials.
    """

    def __init__(self, *, project_id: str = "mock-project") -> None:
        self.project_id = project_id
        self._counter = 0
        self.released: List[str] = []
        self.contexts: List[str] = []

    async def create_session(
        self,
        *,
        context_id: Optional[str] = None,
        persist: bool = True,
        region: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        proxies: bool = False,
    ) -> SessionInfo:
        self._counter += 1
        session_id = f"mock-session-{self._counter}"
        suffix = f"&ctx={context_id}" if context_id else ""
        return SessionInfo(
            id=session_id,
            connect_url=f"wss://mock.connect.local?sessionId={session_id}{suffix}",
            status="RUNNING",
            raw={"mock": True},
        )

    async def get_session(self, session_id: str) -> Dict[str, Any]:
        return {"id": session_id, "status": "RUNNING", "mock": True}

    async def release_session(self, session_id: str) -> None:
        self.released.append(session_id)

    async def get_live_view(self, session_id: str) -> LiveView:
        return LiveView(url=f"https://mock.liveview.local/{session_id}", pages=[])

    async def create_context(self) -> str:
        context_id = f"mock-context-{len(self.contexts) + 1}"
        self.contexts.append(context_id)
        return context_id

    async def get_recording(self, session_id: str) -> bytes:
        return b""


def client_from_env() -> BrowserbaseClient | MockBrowserbaseClient:
    """Build a real client when credentials exist, otherwise the mock."""
    api_key = os.getenv("BROWSERBASE_API_KEY")
    project_id = os.getenv("BROWSERBASE_PROJECT_ID", "")
    base_url = os.getenv("BROWSERBASE_BASE_URL", DEFAULT_BASE_URL)
    connect_url = os.getenv("BROWSERBASE_CONNECT_URL", DEFAULT_CONNECT_URL)
    if not api_key:
        logger.warning("BROWSERBASE_API_KEY not set; using MockBrowserbaseClient")
        return MockBrowserbaseClient(project_id=project_id or "mock-project")
    if not project_id:
        raise BrowserbaseError("BROWSERBASE_PROJECT_ID is required with an API key")
    return BrowserbaseClient(
        api_key=api_key,
        project_id=project_id,
        base_url=base_url,
        connect_url=connect_url,
    )
