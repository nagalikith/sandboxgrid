from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import AnyUrl, BaseModel, Field


class SandboxStatus(str, Enum):
    requested = "requested"
    provisioning = "provisioning"
    ready = "ready"
    error = "error"
    terminated = "terminated"


class SandboxRequest(BaseModel):
    ttl_seconds: int = Field(
        1800, ge=60, le=86400, description="Lifetime in seconds before auto-termination."
    )
    cpu_limit: str = Field(
        "2", description="CPU allocation (cores or millicores), e.g. '2' or '500m'."
    )
    memory_limit_mb: int = Field(
        4096, ge=256, description="Memory allocation for the sandbox in megabytes."
    )
    capabilities: List[str] = Field(
        default_factory=lambda: ["browser", "dashboard"],
        description="Capabilities enabled for this sandbox.",
    )
    allow_network: Optional[List[AnyUrl]] = Field(
        None, description="Optional allowlist of outbound network destinations."
    )


class SandboxRecord(BaseModel):
    sandbox_id: str
    status: SandboxStatus
    created_at: datetime
    expires_at: datetime
    browser_url: Optional[AnyUrl] = None
    dashboard_url: Optional[AnyUrl] = None
    events_url: Optional[AnyUrl] = None
    cdp_url: Optional[AnyUrl] = None
    message: Optional[str] = None
    owner_id: str
    cpu_limit: str
    memory_limit_mb: int
    capabilities: List[str] = Field(default_factory=list)
    allow_network: Optional[List[AnyUrl]] = None
    backend_ref: Optional[str] = Field(
        default=None, description="Provider-specific handle (e.g., container ID)."
    )
    http_port: Optional[int] = None
    cdp_port: Optional[int] = None
    artifacts_path: Optional[str] = None

    class Config:
        use_enum_values = True


class SandboxResponse(BaseModel):
    sandbox_id: str
    status: SandboxStatus
    expires_at: datetime
    browser_url: Optional[AnyUrl] = None
    dashboard_url: Optional[AnyUrl] = None
    events_url: Optional[AnyUrl] = None
    cdp_url: Optional[AnyUrl] = None
    message: Optional[str] = None

    class Config:
        use_enum_values = True


def sandbox_response_from_record(record: SandboxRecord) -> SandboxResponse:
    """Map internal record to public response schema."""
    return SandboxResponse(
        sandbox_id=record.sandbox_id,
        status=record.status,
        expires_at=record.expires_at,
        browser_url=record.browser_url,
        dashboard_url=record.dashboard_url,
        events_url=record.events_url,
        cdp_url=record.cdp_url,
        message=record.message,
    )
