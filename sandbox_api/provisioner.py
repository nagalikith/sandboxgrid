from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
import socket
import subprocess
from urllib.parse import quote
from typing import Optional, Protocol, Tuple

from .models import SandboxRequest, SandboxStatus
from .paths import normalize_artifacts_mode, resolve_artifacts_path


@dataclass
class ProvisionResult:
    status: SandboxStatus
    browser_url: Optional[str]
    dashboard_url: Optional[str]
    events_url: str
    message: str = "Sandbox ready."
    backend_ref: Optional[str] = None
    http_port: Optional[int] = None
    cdp_port: Optional[int] = None
    artifacts_path: Optional[str] = None
    cdp_url: Optional[str] = None


class Provisioner(Protocol):
    async def provision(self, sandbox_id: str, request: SandboxRequest, *, owner_id: str) -> ProvisionResult:
        ...
    async def stop(self, sandbox_id: str, backend_ref: Optional[str]) -> None:
        ...
    def cdp_host(self) -> str:
        """Return host/IP to reach CDP from API node (override in remote provisioners)."""
        ...


class LocalProvisioner:
    """Default provisioner that returns URLs and optional simulated delay.

    Replace this with a real implementation that boots containers/VMs and
    returns connection URLs for the sandbox components.
    """

    def __init__(
        self,
        *,
        sandbox_base_url: str,
        api_base_url: str,
        provision_delay_seconds: float = 0.0,
        artifacts_root: Optional[Path] = None,
        artifacts_mode: str = "per-user",
    ) -> None:
        self._sandbox_base_url = sandbox_base_url.rstrip("/")
        self._api_base_url = api_base_url.rstrip("/")
        self._provision_delay_seconds = provision_delay_seconds
        self._artifacts_root = artifacts_root
        self._artifacts_mode = normalize_artifacts_mode(artifacts_mode)

    async def provision(self, sandbox_id: str, request: SandboxRequest, *, owner_id: str) -> ProvisionResult:
        if self._provision_delay_seconds > 0:
            await asyncio.sleep(self._provision_delay_seconds)
        browser_url, dashboard_url, events_url = self._build_urls(sandbox_id)
        if "browser" not in request.capabilities:
            browser_url = None
        if "dashboard" not in request.capabilities:
            dashboard_url = None
        elif dashboard_url:
            dashboard_url = f"{dashboard_url}?agent_id={quote(owner_id, safe='')}"
        artifacts_path = None
        if self._artifacts_root:
            artifacts_path = resolve_artifacts_path(
                self._artifacts_root,
                sandbox_id=sandbox_id,
                owner_id=owner_id,
                mode=self._artifacts_mode,
            )
            artifacts_path.mkdir(parents=True, exist_ok=True)
        return ProvisionResult(
            status=SandboxStatus.ready,
            browser_url=browser_url,
            dashboard_url=dashboard_url,
            events_url=events_url,
            message="Sandbox ready.",
            backend_ref="local",
            artifacts_path=str(artifacts_path.resolve()) if artifacts_path else None,
        )

    def _build_urls(self, sandbox_id: str) -> Tuple[Optional[str], Optional[str], str]:
        browser_url = f"{self._sandbox_base_url}/b/{sandbox_id}"
        dashboard_url = f"{self._api_base_url}/sandboxes/{sandbox_id}/dashboard"
        events_url = f"{self._api_base_url}/sandboxes/{sandbox_id}/events"
        return browser_url, dashboard_url, events_url

    async def stop(self, sandbox_id: str, backend_ref: Optional[str]) -> None:  # noqa: ARG002
        return None

    def cdp_host(self) -> str:
        return "127.0.0.1"


def build_default_provisioner() -> Provisioner:
    mode = os.getenv("SANDBOX_PROVISIONER", "local").lower()
    if mode == "docker":
        return ChromiumContainerProvisioner.from_env()
    sandbox_base = os.getenv("SANDBOX_PUBLIC_BASE", "http://localhost:8080")
    api_base = os.getenv("API_BASE_URL", "http://localhost:8000")
    provision_delay_seconds = float(os.getenv("SANDBOX_PROVISION_DELAY_SECONDS", "0"))
    artifacts_root = Path(os.getenv("SANDBOX_ARTIFACTS_ROOT", "./artifacts"))
    artifacts_root.mkdir(parents=True, exist_ok=True)
    artifacts_mode = os.getenv("SANDBOX_ARTIFACTS_MODE", "per-user")
    return LocalProvisioner(
        sandbox_base_url=sandbox_base,
        api_base_url=api_base,
        provision_delay_seconds=provision_delay_seconds,
        artifacts_root=artifacts_root,
        artifacts_mode=artifacts_mode,
    )


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


class ChromiumContainerProvisioner:
    """Provisioner that launches a Chromium + noVNC sandbox via Docker."""

    def __init__(
        self,
        *,
        docker_bin: str,
        image: str,
        artifacts_root: Path,
        artifacts_mode: str,
        public_host: str,
        api_base_url: str,
        bind_host: str = "127.0.0.1",
        shm_size: str = "2g",
        screen_width: str = "1920",
        screen_height: str = "1080",
        screen_depth: str = "24",
        chromium_flags: str = "",
    ) -> None:
        self.docker_bin = docker_bin
        self.image = image
        self.artifacts_root = artifacts_root
        self.artifacts_mode = normalize_artifacts_mode(artifacts_mode)
        self.public_host = public_host.rstrip("/")
        self.api_base_url = api_base_url.rstrip("/")
        self.bind_host = bind_host
        self.shm_size = shm_size
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.screen_depth = screen_depth
        self.chromium_flags = chromium_flags

    @classmethod
    def from_env(cls) -> "ChromiumContainerProvisioner":
        docker_bin = os.getenv("DOCKER_BIN", "docker")
        image = os.getenv("SANDBOX_IMAGE", "cua-browser:latest")
        artifacts_root = Path(os.getenv("SANDBOX_ARTIFACTS_ROOT", "./artifacts"))
        public_host = os.getenv("SANDBOX_PUBLIC_HOST", "http://localhost")
        api_base_url = os.getenv("API_BASE_URL", "http://localhost:8000")
        bind_host = os.getenv("SANDBOX_BIND_HOST", "127.0.0.1")
        shm_size = os.getenv("SANDBOX_SHM_SIZE", "2g")
        screen_width = os.getenv("SANDBOX_SCREEN_WIDTH", "1920")
        screen_height = os.getenv("SANDBOX_SCREEN_HEIGHT", "1080")
        screen_depth = os.getenv("SANDBOX_SCREEN_DEPTH", "24")
        chromium_flags = os.getenv("SANDBOX_CHROMIUM_FLAGS", "")
        artifacts_mode = os.getenv("SANDBOX_ARTIFACTS_MODE", "per-user")
        artifacts_root.mkdir(parents=True, exist_ok=True)
        return cls(
            docker_bin=docker_bin,
            image=image,
            artifacts_root=artifacts_root,
            artifacts_mode=artifacts_mode,
            public_host=public_host,
            api_base_url=api_base_url,
            bind_host=bind_host,
            shm_size=shm_size,
            screen_width=screen_width,
            screen_height=screen_height,
            screen_depth=screen_depth,
            chromium_flags=chromium_flags,
        )

    async def provision(self, sandbox_id: str, request: SandboxRequest, *, owner_id: str) -> ProvisionResult:
        http_port = _find_free_port()
        cdp_port = _find_free_port()
        container_name = f"cua_{sandbox_id}"
        artifacts_path = resolve_artifacts_path(
            self.artifacts_root,
            sandbox_id=sandbox_id,
            owner_id=owner_id,
            mode=self.artifacts_mode,
        )
        artifacts_path.mkdir(parents=True, exist_ok=True)

        cmd = [
            self.docker_bin,
            "run",
            "-d",
            "--name",
            container_name,
            "-p",
            f"{self.bind_host}:{http_port}:8080",
            "-p",
            f"{self.bind_host}:{cdp_port}:9222",
            "--shm-size",
            self.shm_size,
            "-e",
            f"SCREEN_WIDTH={self.screen_width}",
            "-e",
            f"SCREEN_HEIGHT={self.screen_height}",
            "-e",
            f"SCREEN_DEPTH={self.screen_depth}",
            "-e",
            f"CHROMIUM_FLAGS={self.chromium_flags}",
            "-v",
            f"{artifacts_path.resolve()}:/home/neko/artifacts",
            self.image,
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(f"Docker run failed: {stderr.decode().strip()}")

        container_id = stdout.decode().strip()
        await self._wait_for_port(http_port)
        await self._wait_for_port(cdp_port)

        host = self.public_host.rstrip("/")
        browser_url = None
        if "browser" in request.capabilities:
            browser_url = f"{host}:{http_port}/vnc.html?autoconnect=1&resize=remote"
        dashboard_url = None
        if "dashboard" in request.capabilities:
            dashboard_url = (
                f"{self.api_base_url}/sandboxes/{sandbox_id}/dashboard"
                f"?agent_id={quote(owner_id, safe='')}"
            )
        events_url = f"{self.api_base_url}/sandboxes/{sandbox_id}/events"
        cdp_url = f"http://127.0.0.1:{cdp_port}"

        return ProvisionResult(
            status=SandboxStatus.ready,
            browser_url=browser_url,
            dashboard_url=dashboard_url,
            events_url=events_url,
            message="Sandbox ready.",
            backend_ref=container_id,
            http_port=http_port,
            cdp_port=cdp_port,
            artifacts_path=str(artifacts_path.resolve()),
            cdp_url=cdp_url,
        )

    async def stop(self, sandbox_id: str, backend_ref: Optional[str]) -> None:
        container_name = backend_ref or f"cua_{sandbox_id}"
        cmd = [self.docker_bin, "rm", "-f", container_name]
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(f"Docker rm failed: {stderr.decode().strip()}")

    def cdp_host(self) -> str:
        # API node reaches container via published port on localhost by default.
        return "127.0.0.1"

    async def _wait_for_port(self, port: int, timeout: float = 10.0) -> None:
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                    return
            except OSError:
                await asyncio.sleep(0.25)
        raise TimeoutError(f"Timed out waiting for port {port}")
