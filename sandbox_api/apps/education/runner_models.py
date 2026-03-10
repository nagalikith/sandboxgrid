from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Protocol

from pydantic import BaseModel, Field


@dataclass
class GradeStudentArgs:
    course_id: str
    assignment_id: str
    student_id: str
    canvas_base: str
    canvas_token: str
    internal_secret: str
    sandbox_api: str = "http://localhost:8000"
    agent_id: str = "grader"
    sandbox_id: Optional[str] = None
    profile_artifact_id: Optional[str] = None
    policy: str = ""
    selectors_json: Optional[Any] = None
    output_dir: str = "./artifacts/grading_runs"
    vision_max_pages: int = 6
    text_max_chars: int = 40000
    min_text_chars: int = 200
    extraction_llm_base: Optional[str] = None
    extraction_llm_key: Optional[str] = None
    extraction_llm_model: Optional[str] = None
    grading_llm_base: str = "https://api.fireworks.ai/inference/v1"
    grading_llm_key: Optional[str] = None
    grading_llm_model: str = "accounts/fireworks/models/kimi-k2p5"
    annotation_llm_base: Optional[str] = None
    annotation_llm_key: Optional[str] = None
    annotation_llm_model: Optional[str] = None
    grade_result_path: Optional[str] = None
    reuse_latest_grade: bool = False
    navigation_mode: str = "course"
    strict_ui_checks: bool = True


@dataclass
class GradeStudentOutcome:
    run_dir: Path
    grade_result_path: Path
    speedgrader_state_path: Path
    llm_observability_path: Path


class BrowserApplyError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        run_dir: Path,
        grade_result_path: Path,
        llm_observability_path: Path,
        speedgrader_state_path: Optional[Path] = None,
    ) -> None:
        super().__init__(message)
        self.run_dir = run_dir
        self.grade_result_path = grade_result_path
        self.llm_observability_path = llm_observability_path
        self.speedgrader_state_path = speedgrader_state_path


class SandboxOps(Protocol):
    def create_sandbox(self, ttl_seconds: int = 3600) -> Dict[str, Any]:
        ...

    def wait_ready(self, sandbox_id: str, timeout: int = 180) -> Dict[str, Any]:
        ...

    def run_steps(self, sandbox_id: str, steps_payload: Dict[str, Any]) -> Dict[str, Any]:
        ...

    def list_artifacts(
        self,
        *,
        run_id: Optional[str] = None,
        artifact_type: Optional[str] = None,
        limit: int = 100,
    ) -> Dict[str, Any]:
        ...

    def download_artifact_blob(self, artifact_id: str, dest: Path) -> Path:
        ...


class GradeEvidence(BaseModel):
    page: int = Field(..., ge=1)
    quote: str = Field(..., min_length=1)


class GradeCriterionResult(BaseModel):
    id: str = Field(..., min_length=1)
    points: float = Field(..., ge=0)
    comment: str = Field(..., min_length=1)
    rationale: Optional[str] = None
    evidence: List[GradeEvidence]


class GradeResult(BaseModel):
    total_points: float = Field(..., ge=0)
    criteria: List[GradeCriterionResult]
    overall_feedback: str = Field(..., min_length=1)
    overall_rationale: Optional[str] = None


class AnnotationPoint(BaseModel):
    x: float = Field(..., ge=0.0, le=1.0)
    y: float = Field(..., ge=0.0, le=1.0)


class AnnotationPlan(BaseModel):
    page: int = Field(..., ge=1)
    tool: Literal["free_draw", "strike", "highlight", "area", "point", "freetext", "select"]
    criterion_id: Optional[str] = None
    evidence_quote: Optional[str] = None
    rationale: Optional[str] = None
    color: Optional[str] = None
    path: List[AnnotationPoint] = Field(default_factory=list)
    point: Optional[AnnotationPoint] = None
    text: Optional[str] = None


@dataclass
class AttachmentInfo:
    filename: str
    content_type: str
    url: str
    size: Optional[int] = None


@dataclass
class CanvasSubmission:
    submission_id: Optional[int]
    attempt: Optional[int]
    attachments: List[AttachmentInfo]
    raw: Dict[str, Any]
