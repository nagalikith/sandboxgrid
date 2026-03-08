from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class AgentEventPayload(BaseModel):
    type: str
    run_id: Optional[str] = None
    step_id: Optional[str] = None
    label: Optional[str] = None

    class Config:
        extra = "allow"
