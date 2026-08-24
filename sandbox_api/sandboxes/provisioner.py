from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
import socket
from urllib.parse import quote
from urllib.request import urlopen
from typing import Optional, Protocol, Tuple

from .models import SandboxRequest, SandboxStatus
from ..core.paths import normalize_artifacts_mode, resolve_artifacts_path

logger = logging.getLogger("sandbox.provisioner")
CDP_EXPOSED_PORT = 9223


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
        logger.info(
            "provision_local start sandbox_id=%s owner_id=%s capabilities=%s",
            sandbox_id,
            owner_id,
            request.capabilities,
        )
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
        result = ProvisionResult(
            status=SandboxStatus.ready,
            browser_url=browser_url,
            dashboard_url=dashboard_url,
            events_url=events_url,
            message="Sandbox ready.",
            backend_ref="local",
            artifacts_path=str(artifacts_path.resolve()) if artifacts_path else None,
        )
        logger.info(
            "provision_local done sandbox_id=%s browser_url=%s cdp_url=%s artifacts_path=%s",
            sandbox_id,
            result.browser_url,
            result.cdp_url,
            result.artifacts_path,
        )
        return result

    def _build_urls(self, sandbox_id: str) -> Tuple[Optional[str], Optional[str], str]:
        browser_url = f"{self._sandbox_base_url}/b/{sandbox_id}"
        dashboard_url = f"{self._api_base_url}/sandboxes/{sandbox_id}/dashboard"
        events_url = f"{self._api_base_url}/sandboxes/{sandbox_id}/events"
        return browser_url, dashboard_url, events_url

    async def stop(self, sandbox_id: str, backend_ref: Optional[str]) -> None:  # noqa: ARG002
        return None

    def cdp_host(self) -> str:
        return "127.0.0.1"


class BrowserbaseProvisioner:
    """Provisions sandboxes as Browserbase cloud-browser sessions.

    Maps the platform lifecycle onto Browserbase's Sessions API:
    provision -> create_session, browser_url -> live view,
    cdp_url -> websocket connect endpoint, backend_ref -> session id.
    """

    def __init__(
        self,
        *,
        client,
        api_base_url: str = "http://localhost:8000",
        artifacts_root: Optional[Path] = None,
        artifacts_mode: str = "per-user",
    ) -> None:
        self._client = client
        self._api_base_url = api_base_url.rstrip("/")
        self._artifacts_root = artifacts_root
        self._artifacts_mode = normalize_artifacts_mode(artifacts_mode)

    @classmethod
    def from_env(cls) -> "BrowserbaseProvisioner":
        from .browserbase import client_from_env

        return cls(
            client=client_from_env(),
            api_base_url=os.getenv("API_BASE_URL", "http://localhost:8000"),
            artifacts_root=Path(os.getenv("SANDBOX_ARTIFACTS_ROOT", "./artifacts")),
            artifacts_mode=os.getenv("SANDBOX_ARTIFACTS_MODE", "per-user"),
        )

    async def provision(self, sandbox_id: str, request: SandboxRequest, *, owner_id: str) -> ProvisionResult:
        logger.info(
            "provision_browserbase start sandbox_id=%s owner_id=%s capabilities=%s",
            sandbox_id,
            owner_id,
            request.capabilities,
        )
        session = await self._client.create_session(timeout_seconds=request.ttl_seconds)
        cdp_url: Optional[str] = session.connect_url if "browser" in request.capabilities else None
        browser_url: Optional[str] = None
        if "browser" in request.capabilities:
            live_view = await self._client.get_live_view(session.id)
            browser_url = live_view.url or None
        dashboard_url = f"{self._api_base_url}/sandboxes/{sandbox_id}/dashboard"
        events_url = f"{self._api_base_url}/sandboxes/{sandbox_id}/events"
        if "dashboard" not in request.capabilities:
            dashboard_url = None
        artifacts_path = None
        if self._artifacts_root:
            resolved = resolve_artifacts_path(
                self._artifacts_root,
                sandbox_id=sandbox_id,
                owner_id=owner_id,
                mode=self._artifacts_mode,
            )
            resolved.mkdir(parents=True, exist_ok=True)
            artifacts_path = str(resolved.resolve())
        logger.info(
            "provision_browserbase done sandbox_id=%s session=%s",
            sandbox_id,
            session.id,
        )
        return ProvisionResult(
            status=SandboxStatus.ready,
            browser_url=browser_url,
            dashboard_url=dashboard_url,
            events_url=events_url,
            message="Sandbox ready.",
            backend_ref=session.id,
            artifacts_path=artifacts_path,
            cdp_url=cdp_url,
        )

    async def stop(self, sandbox_id: str, backend_ref: Optional[str]) -> None:
        if not backend_ref:
            return
        await self._client.release_session(backend_ref)

    def cdp_host(self) -> str:
        return "connect.browserbase.com"


def build_default_provisioner() -> Provisioner:
    mode = os.getenv("SANDBOX_PROVISIONER", "local").lower()
    logger.info("provisioner_select mode=%s", mode)
    if mode == "docker":
        return ChromiumContainerProvisioner.from_env()
    if mode == "browserbase":
        from .browserbase import client_from_env

        return BrowserbaseProvisioner(
            client=client_from_env(),
            api_base_url=os.getenv("API_BASE_URL", "http://localhost:8000"),
            artifacts_root=Path(os.getenv("SANDBOX_ARTIFACTS_ROOT", "./artifacts")),
            artifacts_mode=os.getenv("SANDBOX_ARTIFACTS_MODE", "per-user"),
        )
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
        port_ready_timeout: float = 10.0,
        cdp_ready_timeout: float = 45.0,
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
        self.port_ready_timeout = port_ready_timeout
        self.cdp_ready_timeout = cdp_ready_timeout

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
        port_ready_timeout = float(os.getenv("SANDBOX_PORT_READY_TIMEOUT_SECONDS", "10"))
        cdp_ready_timeout = float(os.getenv("SANDBOX_CDP_READY_TIMEOUT_SECONDS", "45"))
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
            port_ready_timeout=port_ready_timeout,
            cdp_ready_timeout=cdp_ready_timeout,
        )

    async def provision(self, sandbox_id: str, request: SandboxRequest, *, owner_id: str) -> ProvisionResult:
        logger.info(
            "provision_docker start sandbox_id=%s owner_id=%s image=%s capabilities=%s",
            sandbox_id,
            owner_id,
            self.image,
            request.capabilities,
        )
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
            "run",
            "-d",
            "--name",
            container_name,
            "-p",
            f"{self.bind_host}:{http_port}:8080",
            "-p",
            f"{self.bind_host}:{cdp_port}:{CDP_EXPOSED_PORT}",
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

        returncode, stdout, stderr = await self._run_docker_command(*cmd)
        if returncode != 0:
            raise RuntimeError(f"Docker run failed: {stderr}")

        container_id = stdout
        cdp_url = f"http://{self._probe_host()}:{cdp_port}"
        try:
            await self._wait_for_port(http_port, timeout=self.port_ready_timeout)
            try:
                await self._wait_for_port(cdp_port, timeout=self.port_ready_timeout)
                await self._wait_for_cdp_ready_url(cdp_url, timeout=self.cdp_ready_timeout)
            except Exception as host_exc:  # noqa: BLE001
                container_cdp_url = await self._container_cdp_url(container_name)
                if not container_cdp_url:
                    raise host_exc
                logger.warning(
                    "provision_docker cdp_host_probe_failed container=%s published_url=%s fallback_url=%s error=%s",
                    container_name,
                    cdp_url,
                    container_cdp_url,
                    host_exc,
                )
                await self._wait_for_cdp_ready_url(container_cdp_url, timeout=self.cdp_ready_timeout)
                cdp_url = container_cdp_url
        except Exception as exc:  # noqa: BLE001
            diagnostics = await self._collect_startup_diagnostics(container_name)
            await self._safe_remove_container(container_name)
            message = f"{exc}"
            if diagnostics:
                message = f"{message}. {diagnostics}"
            raise type(exc)(message) from exc

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

        result = ProvisionResult(
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
        logger.info(
            "provision_docker done sandbox_id=%s container_id=%s browser_url=%s cdp_url=%s artifacts_path=%s",
            sandbox_id,
            container_id,
            result.browser_url,
            result.cdp_url,
            result.artifacts_path,
        )
        return result

    async def stop(self, sandbox_id: str, backend_ref: Optional[str]) -> None:
        container_name = backend_ref or f"cua_{sandbox_id}"
        returncode, _stdout, stderr = await self._run_docker_command("rm", "-f", container_name)
        if returncode != 0:
            raise RuntimeError(f"Docker rm failed: {stderr}")

    def cdp_host(self) -> str:
        # API node reaches container via published port on localhost by default.
        return self._probe_host()

    async def _run_docker_command(self, *args: str) -> tuple[int, str, str]:
        process = await asyncio.create_subprocess_exec(
            self.docker_bin,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        return process.returncode, stdout.decode().strip(), stderr.decode().strip()

    def _probe_host(self) -> str:
        if self.bind_host in {"", "0.0.0.0", "::"}:
            return "127.0.0.1"
        return self.bind_host

    async def _wait_for_port(self, port: int, timeout: float = 10.0) -> None:
        deadline = asyncio.get_event_loop().time() + timeout
        host = self._probe_host()
        while asyncio.get_event_loop().time() < deadline:
            try:
                with socket.create_connection((host, port), timeout=1.0):
                    return
            except OSError:
                await asyncio.sleep(0.25)
        raise TimeoutError(f"Timed out waiting for port {host}:{port}")

    async def _wait_for_cdp_ready(self, port: int, timeout: float = 45.0) -> None:
        url = f"http://{self._probe_host()}:{port}"
        await self._wait_for_cdp_ready_url(url, timeout=timeout)

    async def _wait_for_cdp_ready_url(self, base_url: str, timeout: float = 45.0) -> None:
        deadline = asyncio.get_event_loop().time() + timeout
        url = f"{base_url.rstrip('/')}/json/version"
        last_error = None
        while asyncio.get_event_loop().time() < deadline:
            try:
                def fetch() -> bytes:
                    with urlopen(url, timeout=2.0) as response:
                        return response.read()

                payload = await asyncio.to_thread(fetch)
                data = json.loads(payload.decode())
                if data.get("webSocketDebuggerUrl"):
                    logger.info("provision_docker cdp_ready url=%s", url)
                    return
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                await asyncio.sleep(0.5)
        raise TimeoutError(f"Timed out waiting for CDP readiness at {url}: {last_error}")

    async def _container_cdp_url(self, container_name: str) -> Optional[str]:
        returncode, stdout, _stderr = await self._run_docker_command(
            "inspect",
            "--format",
            "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
            container_name,
        )
        if returncode != 0:
            return None
        ip_address = stdout.strip()
        if not ip_address:
            return None
        return f"http://{ip_address}:{CDP_EXPOSED_PORT}"

    async def _collect_startup_diagnostics(self, container_name: str) -> str:
        diagnostics: list[str] = []
        returncode, stdout, stderr = await self._run_docker_command(
            "inspect",
            "--format",
            (
                "status={{.State.Status}} "
                "exit_code={{.State.ExitCode}} "
                "started_at={{.State.StartedAt}} "
                "finished_at={{.State.FinishedAt}} "
                "error={{.State.Error}}"
            ),
            container_name,
        )
        if returncode == 0 and stdout:
            diagnostics.append(f"container_state={stdout}")
        elif stderr:
            diagnostics.append(f"inspect_error={stderr}")

        returncode, stdout, stderr = await self._run_docker_command("logs", "--tail", "50", container_name)
        log_output = stdout or stderr
        if returncode == 0 and log_output:
            diagnostics.append(f"container_logs={log_output}")
        elif stderr:
            diagnostics.append(f"logs_error={stderr}")

        details = "; ".join(diagnostics)
        if details:
            logger.warning("provision_docker startup_failed container=%s details=%s", container_name, details)
        return details

    async def _safe_remove_container(self, container_name: str) -> None:
        try:
            await self.stop(container_name.removeprefix("cua_"), backend_ref=container_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("provision_docker cleanup_failed container=%s error=%s", container_name, exc)
