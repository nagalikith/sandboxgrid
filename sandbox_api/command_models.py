from __future__ import annotations

from typing import List, Literal, Optional, Union

from pydantic import BaseModel, Field, HttpUrl, root_validator


class DrawPoint(BaseModel):
    x: float = Field(..., ge=0.0, le=1.0)
    y: float = Field(..., ge=0.0, le=1.0)


class RunBrowserRequest(BaseModel):
    url: HttpUrl
    interactive: bool = False
    profile_artifact_id: Optional[str] = None


class RecordRequest(BaseModel):
    url: HttpUrl
    duration: int = 30
    interactive: bool = False
    profile_artifact_id: Optional[str] = None


class ReplayRequest(BaseModel):
    session_id: str
    speed: float = 1.0
    interactive: bool = False
    profile_artifact_id: Optional[str] = None


class Step(BaseModel):
    action: Literal[
        "goto",
        "click",
        "type",
        "type_rce",
        "wait",
        "screenshot",
        "wait_for_selector",
        "dom_snapshot",
        "page_state",
        "draw_path",
        "draw_rect",
        "point",
        "freetext",
    ]
    url: Optional[str] = None
    selector: Optional[str] = None
    selector_fallbacks: Optional[List[str]] = None
    role: Optional[str] = None
    role_name: Optional[str] = None
    label: Optional[str] = None
    placeholder: Optional[str] = None
    target_text: Optional[str] = None
    text: Optional[str] = None
    points: Optional[List[DrawPoint]] = None
    point: Optional[DrawPoint] = None
    wait_ms: Optional[int] = None
    timeout_ms: Optional[int] = None
    delay_ms: Optional[int] = None
    retries: Optional[int] = None
    snapshot_format: Optional[Literal["html", "a11y_json"]] = Field(default=None, alias="format")

    class Config:
        allow_population_by_field_name = True

    @root_validator
    def validate_fields(cls, values: dict) -> dict:
        action = values.get("action")
        selector = values.get("selector")
        selector_fallbacks = values.get("selector_fallbacks") or []
        role = values.get("role")
        label = values.get("label")
        placeholder = values.get("placeholder")
        target_text = values.get("target_text")
        has_target = bool(selector or selector_fallbacks or role or label or placeholder or target_text)
        if action == "goto" and not values.get("url"):
            raise ValueError("goto requires url")
        if action in {"click", "type", "wait_for_selector"} and not has_target:
            raise ValueError(f"{action} requires selector or target fields")
        if action == "type" and values.get("text") is None:
            raise ValueError("type requires text")
        if action == "type_rce":
            if not has_target:
                raise ValueError("type_rce requires selector or target fields")
            if values.get("text") is None:
                raise ValueError("type_rce requires text")
        if action == "wait" and values.get("wait_ms") is None:
            raise ValueError("wait requires wait_ms")
        if action == "dom_snapshot" and not (values.get("snapshot_format") or values.get("format")):
            raise ValueError("dom_snapshot requires format")
        if action == "draw_path":
            points = values.get("points") or []
            if not has_target:
                raise ValueError("draw_path requires selector or target fields")
            if len(points) < 2:
                raise ValueError("draw_path requires at least 2 points")
        if action == "draw_rect":
            points = values.get("points") or []
            if not has_target:
                raise ValueError("draw_rect requires selector or target fields")
            if len(points) < 2:
                raise ValueError("draw_rect requires at least 2 points")
        if action == "point":
            if not has_target:
                raise ValueError("point requires selector or target fields")
            if values.get("point") is None:
                raise ValueError("point requires point")
        if action == "freetext":
            if not has_target:
                raise ValueError("freetext requires selector or target fields")
            if values.get("point") is None:
                raise ValueError("freetext requires point")
            if values.get("text") is None:
                raise ValueError("freetext requires text")
        return values


class StepsRequest(BaseModel):
    steps: List[Step]
    screenshot_every_step: bool = False
    profile_artifact_id: Optional[str] = None

    @root_validator
    def validate_steps(cls, values: dict) -> dict:
        steps = values.get("steps") or []
        if not steps:
            raise ValueError("steps cannot be empty")
        return values


class AgentLlmConfig(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = None


class AgentStepsRequest(BaseModel):
    task: Optional[str] = None
    steps: Optional[List[Step]] = None
    screenshot_every_step: bool = False
    capture_state: bool = True
    max_steps: int = 24
    profile_artifact_id: Optional[str] = None
    llm: Optional[AgentLlmConfig] = None

    @root_validator
    def validate_agent(cls, values: dict) -> dict:
        task = values.get("task")
        steps = values.get("steps")
        if not task and not steps:
            raise ValueError("agent request requires task or steps")
        if steps is not None and not steps:
            raise ValueError("steps cannot be empty")
        return values


class CaptureProfileRequest(BaseModel):
    name: Optional[str] = None


CommandPayload = Union[
    RunBrowserRequest,
    RecordRequest,
    ReplayRequest,
    StepsRequest,
    AgentStepsRequest,
    CaptureProfileRequest,
]
