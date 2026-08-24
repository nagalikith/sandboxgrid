from __future__ import annotations

from pathlib import Path

import pytest

from sandbox_api.sandboxes.browserbase import MockBrowserbaseClient
from sandbox_api.sandboxes.models import SandboxRequest, SandboxStatus
from sandbox_api.sandboxes.provisioner import (
    BrowserbaseProvisioner,
    build_default_provisioner,
)


@pytest.fixture()
def tmp_artifacts(tmp_path):
    return tmp_path / "artifacts"


def _provisioner(tmp_artifacts) -> BrowserbaseProvisioner:
    return BrowserbaseProvisioner(
        client=MockBrowserbaseClient(),
        api_base_url="http://api.local",
        artifacts_root=tmp_artifacts,
        artifacts_mode="per-user",
    )


@pytest.mark.asyncio
async def test_browserbase_provision_maps_urls_and_refs(tmp_artifacts):
    provisioner = _provisioner(tmp_artifacts)
    request = SandboxRequest(ttl_seconds=300, capabilities=["browser", "dashboard"])
    result = await provisioner.provision("sbx_bb1", request, owner_id="user_a")

    assert result.status == SandboxStatus.ready
    assert result.backend_ref == "mock-session-1"
    assert result.cdp_url and result.cdp_url.startswith("wss://")
    assert "ttl" not in (result.cdp_url or "")
    assert result.browser_url == "https://mock.liveview.local/mock-session-1"
    assert result.dashboard_url == "http://api.local/sandboxes/sbx_bb1/dashboard"
    assert result.events_url == "http://api.local/sandboxes/sbx_bb1/events"
    assert Path(result.artifacts_path).exists()


@pytest.mark.asyncio
async def test_browserbase_provision_respects_capabilities(tmp_artifacts):
    provisioner = _provisioner(tmp_artifacts)
    request = SandboxRequest(capabilities=["browser"])
    result = await provisioner.provision("sbx_bb2", request, owner_id="user_a")
    assert result.dashboard_url is None


@pytest.mark.asyncio
async def test_browserbase_stop_releases_session(tmp_artifacts):
    client = MockBrowserbaseClient()
    provisioner = BrowserbaseProvisioner(
        client=client, api_base_url="http://api.local", artifacts_root=tmp_artifacts
    )
    await provisioner.stop("sbx_x", backend_ref="sess-7")
    assert client.released == ["sess-7"]


def test_factory_selects_browserbase(monkeypatch, tmp_path):
    monkeypatch.setenv("SANDBOX_PROVISIONER", "browserbase")
    monkeypatch.delenv("BROWSERBASE_API_KEY", raising=False)
    monkeypatch.setenv("SANDBOX_ARTIFACTS_ROOT", str(tmp_path / "art"))
    provisioner = build_default_provisioner()
    assert isinstance(provisioner, BrowserbaseProvisioner)
    from sandbox_api.sandboxes.browserbase import MockBrowserbaseClient as M

    assert isinstance(provisioner._client, M)
