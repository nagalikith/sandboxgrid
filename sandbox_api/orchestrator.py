from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4

from .models import SandboxRecord, SandboxRequest, SandboxStatus
from .provisioner import ProvisionResult, Provisioner, build_default_provisioner
from .storage import SandboxRepository


class SandboxOrchestrator:
    """DB-backed orchestrator that calls a provisioner and stores state."""

    def __init__(
        self,
        repository: SandboxRepository,
        provisioner: Provisioner | None = None,
    ) -> None:
        self._repository = repository
        self._provisioner = provisioner or build_default_provisioner()

    async def provision(self, request: SandboxRequest, owner_id: str) -> SandboxRecord:
        now = datetime.now(timezone.utc)
        sandbox_id = f"sbx_{uuid4().hex[:8]}"
        expires_at = now + timedelta(seconds=request.ttl_seconds)

        record = SandboxRecord(
            sandbox_id=sandbox_id,
            status=SandboxStatus.provisioning,
            created_at=now,
            expires_at=expires_at,
            browser_url=None,
            dashboard_url=None,
            events_url=None,
            message="Provisioning sandbox.",
            owner_id=owner_id,
            cpu_limit=request.cpu_limit,
            memory_limit_mb=request.memory_limit_mb,
            capabilities=request.capabilities,
            allow_network=request.allow_network,
        )

        record = self._repository.create(record)
        asyncio.create_task(self._provision_async(record, request))
        asyncio.create_task(self._enforce_ttl(record))
        return record

    async def get(self, sandbox_id: str) -> Optional[SandboxRecord]:
        return self._repository.get(sandbox_id)

    async def _provision_async(self, record: SandboxRecord, request: SandboxRequest) -> None:
        try:
            result: ProvisionResult = await self._provisioner.provision(
                record.sandbox_id,
                request,
                owner_id=record.owner_id,
            )
            self._repository.set_ready(
                record.sandbox_id,
                browser_url=result.browser_url,
                dashboard_url=result.dashboard_url,
                events_url=result.events_url,
                message=result.message,
                backend_ref=result.backend_ref,
                http_port=result.http_port,
                cdp_port=result.cdp_port,
                artifacts_path=result.artifacts_path,
                cdp_url=result.cdp_url,
            )
        except Exception as exc:  # noqa: BLE001
            self._repository.set_error(
                record.sandbox_id,
                message=f"Provisioning failed: {exc}",
            )

    async def _enforce_ttl(self, record: SandboxRecord) -> None:
        now = datetime.now(timezone.utc)
        delay = max((record.expires_at - now).total_seconds(), 0)
        await asyncio.sleep(delay)
        latest = self._repository.get(record.sandbox_id)
        if not latest or latest.status == SandboxStatus.terminated:
            return
        try:
            await self._provisioner.stop(latest.sandbox_id, latest.backend_ref)
        finally:
            self._repository.set_terminated(latest.sandbox_id, message="Sandbox expired.")
