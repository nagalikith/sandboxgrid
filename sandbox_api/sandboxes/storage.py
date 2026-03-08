from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from sqlalchemy import JSON, Column, Enum as SAEnum
from sqlmodel import Field, Session, SQLModel

from .models import SandboxRecord, SandboxStatus


class SandboxRow(SQLModel, table=True):
    sandbox_id: str = Field(primary_key=True, index=True)
    status: SandboxStatus = Field(sa_column=Column(SAEnum(SandboxStatus)))
    created_at: datetime
    expires_at: datetime
    browser_url: Optional[str] = None
    dashboard_url: Optional[str] = None
    events_url: Optional[str] = None
    cdp_url: Optional[str] = None
    message: Optional[str] = None
    owner_id: str
    cpu_limit: str
    memory_limit_mb: int
    capabilities: List[str] = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    allow_network: Optional[List[str]] = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    backend_ref: Optional[str] = None
    http_port: Optional[int] = None
    cdp_port: Optional[int] = None
    artifacts_path: Optional[str] = None

    def to_record(self) -> SandboxRecord:
        return SandboxRecord(
            sandbox_id=self.sandbox_id,
            status=self.status,
            created_at=self.created_at,
            expires_at=self.expires_at,
            browser_url=self.browser_url,
            dashboard_url=self.dashboard_url,
            events_url=self.events_url,
            cdp_url=self.cdp_url,
            message=self.message,
            owner_id=self.owner_id,
            cpu_limit=self.cpu_limit,
            memory_limit_mb=self.memory_limit_mb,
            capabilities=self.capabilities or [],
            allow_network=self.allow_network,
            backend_ref=self.backend_ref,
            http_port=self.http_port,
            cdp_port=self.cdp_port,
            artifacts_path=self.artifacts_path,
        )

    @classmethod
    def from_record(cls, record: SandboxRecord) -> "SandboxRow":
        return cls(
            sandbox_id=record.sandbox_id,
            status=record.status,
            created_at=record.created_at,
            expires_at=record.expires_at,
            browser_url=record.browser_url,
            dashboard_url=record.dashboard_url,
            events_url=record.events_url,
            cdp_url=record.cdp_url,
            message=record.message,
            owner_id=record.owner_id,
            cpu_limit=record.cpu_limit,
            memory_limit_mb=record.memory_limit_mb,
            capabilities=record.capabilities,
            allow_network=record.allow_network,
            backend_ref=record.backend_ref,
            http_port=record.http_port,
            cdp_port=record.cdp_port,
            artifacts_path=record.artifacts_path,
        )


class SandboxRepository:
    """DB-backed repository for sandbox records."""

    def __init__(self, engine) -> None:
        self.engine = engine

    def create(self, record: SandboxRecord) -> SandboxRecord:
        with Session(self.engine) as session:
            row = SandboxRow.from_record(record)
            session.add(row)
            session.commit()
            session.refresh(row)
            return row.to_record()

    def get(self, sandbox_id: str) -> Optional[SandboxRecord]:
        with Session(self.engine) as session:
            row = session.get(SandboxRow, sandbox_id)
            return row.to_record() if row else None

    def set_ready(
        self,
        sandbox_id: str,
        *,
        browser_url: Optional[str],
        dashboard_url: Optional[str],
        events_url: str,
        message: str = "Sandbox ready.",
        backend_ref: Optional[str] = None,
        http_port: Optional[int] = None,
        cdp_port: Optional[int] = None,
        artifacts_path: Optional[str] = None,
        cdp_url: Optional[str] = None,
    ) -> Optional[SandboxRecord]:
        with Session(self.engine) as session:
            row = session.get(SandboxRow, sandbox_id)
            if not row:
                return None
            row.status = SandboxStatus.ready
            row.browser_url = browser_url
            row.dashboard_url = dashboard_url
            row.events_url = events_url
            row.message = message
            row.backend_ref = backend_ref or row.backend_ref
            row.http_port = http_port or row.http_port
            row.cdp_port = cdp_port or row.cdp_port
            row.artifacts_path = artifacts_path or row.artifacts_path
            row.cdp_url = cdp_url or row.cdp_url
            session.add(row)
            session.commit()
            session.refresh(row)
            return row.to_record()

    def set_status(
        self,
        sandbox_id: str,
        *,
        status: SandboxStatus,
        message: Optional[str] = None,
    ) -> Optional[SandboxRecord]:
        with Session(self.engine) as session:
            row = session.get(SandboxRow, sandbox_id)
            if not row:
                return None
            row.status = status
            if message is not None:
                row.message = message
            session.add(row)
            session.commit()
            session.refresh(row)
            return row.to_record()

    def set_error(self, sandbox_id: str, *, message: str) -> Optional[SandboxRecord]:
        with Session(self.engine) as session:
            row = session.get(SandboxRow, sandbox_id)
            if not row:
                return None
            row.status = SandboxStatus.error
            row.message = message
            session.add(row)
            session.commit()
            session.refresh(row)
            return row.to_record()

    def set_terminated(self, sandbox_id: str, *, message: str = "Sandbox terminated.") -> Optional[SandboxRecord]:
        with Session(self.engine) as session:
            row = session.get(SandboxRow, sandbox_id)
            if not row:
                return None
            row.status = SandboxStatus.terminated
            row.message = message
            session.add(row)
            session.commit()
            session.refresh(row)
            return row.to_record()
