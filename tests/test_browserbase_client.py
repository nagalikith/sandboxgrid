from __future__ import annotations

import pytest

from sandbox_api.sandboxes.browserbase import (
    MockBrowserbaseClient,
    client_from_env,
)


@pytest.mark.asyncio
async def test_mock_client_creates_deterministic_sessions():
    client = MockBrowserbaseClient()
    first = await client.create_session()
    second = await client.create_session(context_id="ctx-9")
    assert first.id == "mock-session-1"
    assert second.id == "mock-session-2"
    assert "ctx=ctx-9" in second.connect_url
    live = await client.get_live_view(first.id)
    assert first.id in live.url


@pytest.mark.asyncio
async def test_mock_client_tracks_releases_and_contexts():
    client = MockBrowserbaseClient()
    session = await client.create_session()
    await client.release_session(session.id)
    context_id = await client.create_context()
    assert client.released == [session.id]
    assert client.contexts == [context_id]


@pytest.mark.asyncio
async def test_client_from_env_falls_back_to_mock_without_key(monkeypatch):
    monkeypatch.delenv("BROWSERBASE_API_KEY", raising=False)
    client = client_from_env()
    assert isinstance(client, MockBrowserbaseClient)


def test_client_from_env_requires_project_with_key(monkeypatch):
    monkeypatch.setenv("BROWSERBASE_API_KEY", "key")
    monkeypatch.delenv("BROWSERBASE_PROJECT_ID", raising=False)
    with pytest.raises(Exception, match="BROWSERBASE_PROJECT_ID"):
        client_from_env()


def test_client_from_env_builds_real_client(monkeypatch):
    monkeypatch.setenv("BROWSERBASE_API_KEY", "key")
    monkeypatch.setenv("BROWSERBASE_PROJECT_ID", "proj")
    from sandbox_api.sandboxes.browserbase import BrowserbaseClient

    client = client_from_env()
    assert isinstance(client, BrowserbaseClient)
    assert client._project_id == "proj"
