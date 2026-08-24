import asyncio
from pathlib import Path

import pytest

from sandbox_api.sandboxes.models import SandboxRequest, SandboxStatus
from sandbox_api.sandboxes.provisioner import (
    ChromiumContainerProvisioner,
    LocalProvisioner,
    ProvisionResult,
    _find_free_port,
    build_default_provisioner,
)


@pytest.mark.asyncio
async def test_local_provisioner_creates_urls(tmp_path):
    prov = LocalProvisioner(
        sandbox_base_url="http://sandbox",
        api_base_url="http://api",
        provision_delay_seconds=0,
        artifacts_root=tmp_path,
        artifacts_mode="per-user",
    )
    request = SandboxRequest(capabilities=["browser", "dashboard"])
    result = await prov.provision("sbx_1", request, owner_id="user_a")
    assert result.status == SandboxStatus.ready
    assert result.browser_url
    assert "agent_id" in result.dashboard_url
    assert Path(result.artifacts_path).exists()


def test_find_free_port_returns_port():
    port = _find_free_port()
    assert isinstance(port, int)
    assert port > 0


def test_build_default_provisioner_local(monkeypatch, tmp_path):
    monkeypatch.setenv("SANDBOX_PROVISIONER", "local")
    monkeypatch.setenv("SANDBOX_ARTIFACTS_ROOT", str(tmp_path))
    provisioner = build_default_provisioner()
    assert isinstance(provisioner, LocalProvisioner)


def test_chromium_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SANDBOX_PROVISIONER", "docker")
    monkeypatch.setenv("SANDBOX_ARTIFACTS_ROOT", str(tmp_path))
    prov = ChromiumContainerProvisioner.from_env()
    assert prov.docker_bin


@pytest.mark.asyncio
@pytest.mark.slow
async def test_chromium_provision_success(monkeypatch, tmp_path):
    prov = ChromiumContainerProvisioner(
        docker_bin="docker",
        image="image",
        artifacts_root=tmp_path,
        artifacts_mode="per-sandbox",
        public_host="http://host",
        api_base_url="http://api",
    )

    async def fake_wait(self, _port, timeout=10.0):
        return None

    async def fake_wait_for_cdp(self, _port, timeout=45.0):
        return None

    async def fake_run(self, *args):
        assert args[0] == "run"
        return 0, "cid", ""

    monkeypatch.setattr("sandbox_api.sandboxes.provisioner._find_free_port", lambda: 1111)
    monkeypatch.setattr(ChromiumContainerProvisioner, "_run_docker_command", fake_run)
    monkeypatch.setattr(ChromiumContainerProvisioner, "_wait_for_port", fake_wait)
    monkeypatch.setattr(ChromiumContainerProvisioner, "_wait_for_cdp_ready", fake_wait_for_cdp)

    result = await prov.provision("sbx_1", SandboxRequest(), owner_id="user_a")
    assert isinstance(result, ProvisionResult)
    assert result.browser_url
    assert result.events_url


@pytest.mark.asyncio
async def test_chromium_provision_failure(monkeypatch, tmp_path):
    prov = ChromiumContainerProvisioner(
        docker_bin="docker",
        image="image",
        artifacts_root=tmp_path,
        artifacts_mode="per-sandbox",
        public_host="http://host",
        api_base_url="http://api",
    )

    async def fake_run(self, *args):
        if args[0] == "inspect":
            return 0, "", ""
        assert args[0] == "run"
        return 1, "", "boom"

    monkeypatch.setattr("sandbox_api.sandboxes.provisioner._find_free_port", lambda: 1111)
    monkeypatch.setattr(ChromiumContainerProvisioner, "_run_docker_command", fake_run)

    with pytest.raises(RuntimeError):
        await prov.provision("sbx_1", SandboxRequest(), owner_id="user_a")


@pytest.mark.asyncio
async def test_chromium_stop_failure(monkeypatch, tmp_path):
    prov = ChromiumContainerProvisioner(
        docker_bin="docker",
        image="image",
        artifacts_root=tmp_path,
        artifacts_mode="per-sandbox",
        public_host="http://host",
        api_base_url="http://api",
    )

    async def fake_run(self, *args):
        assert args[:2] == ("rm", "-f")
        return 1, "", "bad"

    monkeypatch.setattr(ChromiumContainerProvisioner, "_run_docker_command", fake_run)

    with pytest.raises(RuntimeError):
        await prov.stop("sbx_1", backend_ref="cid")


@pytest.mark.asyncio
async def test_wait_for_port_timeout(tmp_path, monkeypatch):
    prov = ChromiumContainerProvisioner(
        docker_bin="docker",
        image="image",
        artifacts_root=tmp_path,
        artifacts_mode="per-sandbox",
        public_host="http://host",
        api_base_url="http://api",
    )

    async def fake_sleep(_delay):
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    with pytest.raises(TimeoutError):
        await prov._wait_for_port(1234, timeout=0)


@pytest.mark.asyncio
@pytest.mark.slow
async def test_chromium_provision_timeout_collects_diagnostics(monkeypatch, tmp_path):
    prov = ChromiumContainerProvisioner(
        docker_bin="docker",
        image="image",
        artifacts_root=tmp_path,
        artifacts_mode="per-sandbox",
        public_host="http://host",
        api_base_url="http://api",
    )

    calls = []

    async def fake_wait(self, _port, timeout=10.0):
        return None

    async def fake_wait_for_cdp(self, _port, timeout=45.0):
        raise TimeoutError("Timed out waiting for CDP readiness at http://127.0.0.1:1111/json/version")

    async def fake_run(self, *args):
        calls.append(args)
        if args[0] == "run":
            return 0, "cid", ""
        if args[0] == "inspect":
            return 0, "status=exited exit_code=1 started_at=a finished_at=b error=boom", ""
        if args[0] == "logs":
            return 0, "Error: CDP not accessible", ""
        if args[0] == "rm":
            return 0, "cid", ""
        raise AssertionError(args)

    monkeypatch.setattr("sandbox_api.sandboxes.provisioner._find_free_port", lambda: 1111)
    monkeypatch.setattr(ChromiumContainerProvisioner, "_run_docker_command", fake_run)
    monkeypatch.setattr(ChromiumContainerProvisioner, "_wait_for_port", fake_wait)
    monkeypatch.setattr(ChromiumContainerProvisioner, "_wait_for_cdp_ready", fake_wait_for_cdp)

    with pytest.raises(TimeoutError, match="container_state=status=exited"):
        await prov.provision("sbx_1", SandboxRequest(), owner_id="user_a")

    assert ("rm", "-f", "cua_sbx_1") in calls


@pytest.mark.asyncio
@pytest.mark.slow
async def test_chromium_provision_falls_back_to_container_ip(monkeypatch, tmp_path):
    prov = ChromiumContainerProvisioner(
        docker_bin="docker",
        image="image",
        artifacts_root=tmp_path,
        artifacts_mode="per-sandbox",
        public_host="http://host",
        api_base_url="http://api",
    )

    ports = iter([2111, 3222])
    seen_urls = []

    async def fake_wait_for_port(self, port, timeout=10.0):
        assert port in {2111, 3222}
        return None

    async def fake_wait_for_cdp_ready_url(self, url, timeout=45.0):
        seen_urls.append(url)
        if url == "http://127.0.0.1:3222":
            raise TimeoutError("host probe failed")
        if url == "http://172.17.0.5:9222":
            return None
        raise AssertionError(url)

    async def fake_run(self, *args):
        if args[0] == "run":
            return 0, "cid", ""
        if args[0] == "inspect":
            return 0, "172.17.0.5", ""
        raise AssertionError(args)

    monkeypatch.setattr("sandbox_api.sandboxes.provisioner._find_free_port", lambda: next(ports))
    monkeypatch.setattr(ChromiumContainerProvisioner, "_run_docker_command", fake_run)
    monkeypatch.setattr(ChromiumContainerProvisioner, "_wait_for_port", fake_wait_for_port)
    monkeypatch.setattr(ChromiumContainerProvisioner, "_wait_for_cdp_ready_url", fake_wait_for_cdp_ready_url)

    result = await prov.provision("sbx_1", SandboxRequest(), owner_id="user_a")

    assert result.cdp_url == "http://172.17.0.5:9222"
    assert seen_urls == ["http://127.0.0.1:3222", "http://172.17.0.5:9222"]
