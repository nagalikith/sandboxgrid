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
async def test_chromium_provision_success(monkeypatch, tmp_path):
    prov = ChromiumContainerProvisioner(
        docker_bin="docker",
        image="image",
        artifacts_root=tmp_path,
        artifacts_mode="per-sandbox",
        public_host="http://host",
        api_base_url="http://api",
    )

    async def fake_wait(_port, timeout=10.0):
        return None

    class DummyProcess:
        def __init__(self, returncode=0, stdout=b"cid", stderr=b"") -> None:
            self.returncode = returncode
            self._stdout = stdout
            self._stderr = stderr

        async def communicate(self):
            return self._stdout, self._stderr

    async def fake_create(*_args, **_kwargs):
        return DummyProcess()

    monkeypatch.setattr("sandbox_api.provisioner._find_free_port", lambda: 1111)
    monkeypatch.setattr("sandbox_api.provisioner.asyncio.create_subprocess_exec", fake_create)
    monkeypatch.setattr(ChromiumContainerProvisioner, "_wait_for_port", fake_wait)

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

    class DummyProcess:
        def __init__(self) -> None:
            self.returncode = 1

        async def communicate(self):
            return b"", b"boom"

    async def fake_create(*_args, **_kwargs):
        return DummyProcess()

    monkeypatch.setattr("sandbox_api.provisioner._find_free_port", lambda: 1111)
    monkeypatch.setattr("sandbox_api.provisioner.asyncio.create_subprocess_exec", fake_create)

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

    class DummyProcess:
        def __init__(self) -> None:
            self.returncode = 1

        async def communicate(self):
            return b"", b"bad"

    async def fake_create(*_args, **_kwargs):
        return DummyProcess()

    monkeypatch.setattr("sandbox_api.provisioner.asyncio.create_subprocess_exec", fake_create)

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
