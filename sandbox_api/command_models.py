from __future__ import annotations

from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field, HttpUrl, root_validator


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


class Step(BaseModel):
    action: Literal[
        "goto",
        "click",
        "type",
        "wait",
        "screenshot",
        "wait_for_selector",
        "dom_snapshot",
    ]
    url: Optional[str] = None
    selector: Optional[str] = None
    text: Optional[str] = None
    wait_ms: Optional[int] = None
    name: Optional[str] = None
    timeout_ms: Optional[int] = None
    delay_ms: Optional[int] = None
    snapshot_format: Optional[Literal["html", "a11y_json"]] = Field(default=None, alias="format")

    class Config:
        allow_population_by_field_name = True

    @root_validator
    def validate_fields(cls, values: dict) -> dict:
        action = values.get("action")
        if action == "goto" and not values.get("url"):
            raise ValueError("goto requires url")
        if action in {"click", "type", "wait_for_selector"} and not values.get("selector"):
            raise ValueError(f"{action} requires selector")
        if action == "type" and values.get("text") is None:
            raise ValueError("type requires text")
        if action == "wait" and values.get("wait_ms") is None:
            raise ValueError("wait requires wait_ms")
        if action == "dom_snapshot" and not (values.get("snapshot_format") or values.get("format")):
            raise ValueError("dom_snapshot requires format")
        return values


class StepsRequest(BaseModel):
    steps: List[Step]
    screenshot_every_step: bool = False

    @root_validator
    def validate_steps(cls, values: dict) -> dict:
        steps = values.get("steps") or []
        if not steps:
            raise ValueError("steps cannot be empty")
        return values


CommandPayload = Union[RunBrowserRequest, RecordRequest, ReplayRequest, StepsRequest]
