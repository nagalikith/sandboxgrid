from __future__ import annotations

from typing import Union

from pydantic import BaseModel, HttpUrl


class RunBrowserRequest(BaseModel):
    url: HttpUrl
    interactive: bool = False


class RecordRequest(BaseModel):
    url: HttpUrl
    duration: int = 30
    interactive: bool = False


class ReplayRequest(BaseModel):
    session_id: str
    speed: float = 1.0
    interactive: bool = False


CommandPayload = Union[RunBrowserRequest, RecordRequest, ReplayRequest]
