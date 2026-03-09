from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import AnyUrl, BaseModel, Field
from sqlalchemy import Column, JSON
from sqlmodel import Field as SQLField, Session, SQLModel, select

from ...core.database import engine


class GradingJobStatus(str, Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class GradingJobRequest(BaseModel):
    course_id: str = Field(..., min_length=1)
    assignment_id: str = Field(..., min_length=1)
    student_id: str = Field(..., min_length=1)
    canvas_base: AnyUrl
    canvas_token: Optional[str] = None
    sandbox_id: Optional[str] = None
    profile_artifact_id: Optional[str] = None
    agent_id: Optional[str] = None
    navigation_mode: Literal["course", "direct"] = Field(default="course")
    selectors_json: Optional[Dict[str, Any]] = None
    policy: Optional[str] = None
    vision_max_pages: int = Field(default=6, ge=1)
    text_max_chars: int = Field(default=40000, ge=1000)
    min_text_chars: int = Field(default=200, ge=0)
    llm_base: Optional[str] = None
    llm_model: Optional[str] = None
    extraction_llm_base: Optional[str] = None
    extraction_llm_model: Optional[str] = None
    grading_llm_base: Optional[str] = None
    grading_llm_model: Optional[str] = None
    annotation_llm_base: Optional[str] = None
    annotation_llm_model: Optional[str] = None
    strict_ui_checks: bool = True
    output_dir: Optional[str] = None


class GradingJobRecord(BaseModel):
    job_id: str
    owner_id: str
    status: GradingJobStatus
    payload: Dict[str, Any]
    result: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    class Config:
        use_enum_values = True


class GradingJobRow(SQLModel, table=True):
    __tablename__ = "grading_jobs"

    job_id: str = SQLField(primary_key=True, index=True)
    owner_id: str = SQLField(index=True)
    status: str = SQLField(index=True)
    payload: Dict[str, Any] = SQLField(sa_column=Column(JSON, nullable=False))
    result: Optional[Dict[str, Any]] = SQLField(default=None, sa_column=Column(JSON, nullable=True))
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    def to_record(self) -> GradingJobRecord:
        return GradingJobRecord(
            job_id=self.job_id,
            owner_id=self.owner_id,
            status=GradingJobStatus(self.status),
            payload=self.payload,
            result=self.result,
            error_message=self.error_message,
            created_at=self.created_at,
            updated_at=self.updated_at,
            started_at=self.started_at,
            finished_at=self.finished_at,
        )

    @classmethod
    def from_record(cls, record: GradingJobRecord) -> "GradingJobRow":
        return cls(
            job_id=record.job_id,
            owner_id=record.owner_id,
            status=record.status.value if isinstance(record.status, GradingJobStatus) else record.status,
            payload=record.payload,
            result=record.result,
            error_message=record.error_message,
            created_at=record.created_at,
            updated_at=record.updated_at,
            started_at=record.started_at,
            finished_at=record.finished_at,
        )


class GradingJobResponse(BaseModel):
    job_id: str
    status: GradingJobStatus
    payload: GradingJobRequest
    result: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    class Config:
        use_enum_values = True


class GradingJobListResponse(BaseModel):
    items: List[GradingJobResponse]


class GradingJobRepository:
    def __init__(self, engine) -> None:
        self.engine = engine

    def create(self, record: GradingJobRecord) -> GradingJobRecord:
        with Session(self.engine) as session:
            row = GradingJobRow.from_record(record)
            session.add(row)
            session.commit()
            session.refresh(row)
            return row.to_record()

    def get(self, job_id: str) -> Optional[GradingJobRecord]:
        with Session(self.engine) as session:
            row = session.get(GradingJobRow, job_id)
            return row.to_record() if row else None

    def list(self, *, owner_id: str, offset: int = 0, limit: int = 100) -> List[GradingJobRecord]:
        stmt = select(GradingJobRow).where(GradingJobRow.owner_id == owner_id)
        stmt = stmt.order_by(GradingJobRow.created_at.desc()).offset(offset).limit(limit)
        with Session(self.engine) as session:
            rows = session.exec(stmt).all()
            return [row.to_record() for row in rows]

    def update(self, job_id: str, **updates: Any) -> Optional[GradingJobRecord]:
        with Session(self.engine) as session:
            row = session.get(GradingJobRow, job_id)
            if not row:
                return None
            for key, value in updates.items():
                if value is None and key not in {"result", "error_message", "started_at", "finished_at"}:
                    continue
                setattr(row, key, value)
            session.add(row)
            session.commit()
            session.refresh(row)
            return row.to_record()


grading_job_repo = GradingJobRepository(engine)
