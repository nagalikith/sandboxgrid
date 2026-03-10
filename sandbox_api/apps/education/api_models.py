from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from fastapi import status
from pydantic import AnyUrl, BaseModel, Field
from sqlalchemy import Column, JSON
from sqlmodel import Field as SQLField, SQLModel


class SubmissionRecord(BaseModel):
    submission_id: str
    owner_id: str
    source_type: str
    source_url: Optional[str] = None
    filename: Optional[str] = None
    content_type: Optional[str] = None
    size_bytes: Optional[int] = None
    metadata_payload: Optional[Dict[str, Any]] = Field(default=None, alias="metadata")
    blob_path: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        allow_population_by_field_name = True


class SubmissionRow(SQLModel, table=True):
    __tablename__ = "submissions"

    submission_id: str = SQLField(primary_key=True, index=True)
    owner_id: str = SQLField(index=True)
    source_type: str
    source_url: Optional[str] = None
    filename: Optional[str] = None
    content_type: Optional[str] = None
    size_bytes: Optional[int] = None
    metadata_payload: Optional[Dict[str, Any]] = SQLField(
        default=None,
        sa_column=Column("metadata", JSON, nullable=True),
    )
    blob_path: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    def to_record(self) -> SubmissionRecord:
        return SubmissionRecord(
            submission_id=self.submission_id,
            owner_id=self.owner_id,
            source_type=self.source_type,
            source_url=self.source_url,
            filename=self.filename,
            content_type=self.content_type,
            size_bytes=self.size_bytes,
            metadata_payload=self.metadata_payload,
            blob_path=self.blob_path,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )

    @classmethod
    def from_record(cls, record: SubmissionRecord) -> "SubmissionRow":
        return cls(
            submission_id=record.submission_id,
            owner_id=record.owner_id,
            source_type=record.source_type,
            source_url=record.source_url,
            filename=record.filename,
            content_type=record.content_type,
            size_bytes=record.size_bytes,
            metadata_payload=record.metadata_payload,
            blob_path=record.blob_path,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class RubricCriterion(BaseModel):
    criterion_id: Optional[str] = None
    label: str = Field(..., min_length=1)
    description: Optional[str] = None
    max_score: float = Field(ge=0)
    weight: Optional[float] = Field(default=None, ge=0)


class RubricRecord(BaseModel):
    rubric_version_id: str
    rubric_id: str
    owner_id: str
    version: int
    title: str
    description: Optional[str] = None
    criteria: List[RubricCriterion]
    metadata_payload: Optional[Dict[str, Any]] = Field(default=None, alias="metadata")
    created_at: datetime
    updated_at: datetime

    class Config:
        allow_population_by_field_name = True


class RubricRow(SQLModel, table=True):
    __tablename__ = "rubrics"

    rubric_version_id: str = SQLField(primary_key=True, index=True)
    rubric_id: str = SQLField(index=True)
    owner_id: str = SQLField(index=True)
    version: int
    title: str
    description: Optional[str] = None
    criteria: List[Dict[str, Any]] = SQLField(default_factory=list, sa_column=Column(JSON, nullable=False))
    metadata_payload: Optional[Dict[str, Any]] = SQLField(
        default=None,
        sa_column=Column("metadata", JSON, nullable=True),
    )
    created_at: datetime
    updated_at: datetime

    def to_record(self) -> RubricRecord:
        return RubricRecord(
            rubric_version_id=self.rubric_version_id,
            rubric_id=self.rubric_id,
            owner_id=self.owner_id,
            version=self.version,
            title=self.title,
            description=self.description,
            criteria=[RubricCriterion(**item) for item in (self.criteria or [])],
            metadata_payload=self.metadata_payload,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


class GradeEntry(BaseModel):
    criterion_id: str = Field(..., min_length=1)
    score: float = Field(ge=0)
    comment: Optional[str] = None


class GradingSessionRecord(BaseModel):
    session_id: str
    owner_id: str
    submission_id: str
    rubric_id: str
    rubric_version_id: str
    rubric_version: int
    status: str
    scores: List[GradeEntry]
    metadata_payload: Optional[Dict[str, Any]] = Field(default=None, alias="metadata")
    created_at: datetime
    updated_at: datetime
    finalized_at: Optional[datetime] = None

    class Config:
        allow_population_by_field_name = True


class GradingSessionRow(SQLModel, table=True):
    __tablename__ = "grading_sessions"

    session_id: str = SQLField(primary_key=True, index=True)
    owner_id: str = SQLField(index=True)
    submission_id: str = SQLField(index=True)
    rubric_id: str = SQLField(index=True)
    rubric_version_id: str = SQLField(index=True)
    rubric_version: int
    status: str
    scores: List[Dict[str, Any]] = SQLField(default_factory=list, sa_column=Column(JSON, nullable=False))
    metadata_payload: Optional[Dict[str, Any]] = SQLField(
        default=None,
        sa_column=Column("metadata", JSON, nullable=True),
    )
    created_at: datetime
    updated_at: datetime
    finalized_at: Optional[datetime] = None

    def to_record(self) -> GradingSessionRecord:
        return GradingSessionRecord(
            session_id=self.session_id,
            owner_id=self.owner_id,
            submission_id=self.submission_id,
            rubric_id=self.rubric_id,
            rubric_version_id=self.rubric_version_id,
            rubric_version=self.rubric_version,
            status=self.status,
            scores=[GradeEntry(**item) for item in (self.scores or [])],
            metadata_payload=self.metadata_payload,
            created_at=self.created_at,
            updated_at=self.updated_at,
            finalized_at=self.finalized_at,
        )


class SubmissionCreate(BaseModel):
    source_url: AnyUrl
    metadata_payload: Optional[Dict[str, Any]] = Field(default=None, alias="metadata")

    class Config:
        allow_population_by_field_name = True


class SubmissionResponse(BaseModel):
    submission_id: str
    source_type: str
    source_url: Optional[str] = None
    filename: Optional[str] = None
    content_type: Optional[str] = None
    size_bytes: Optional[int] = None
    metadata_payload: Optional[Dict[str, Any]] = Field(default=None, alias="metadata")
    created_at: datetime
    updated_at: datetime

    class Config:
        allow_population_by_field_name = True


class RubricUpsert(BaseModel):
    title: str = Field(..., min_length=1)
    description: Optional[str] = None
    criteria: List[RubricCriterion]
    metadata_payload: Optional[Dict[str, Any]] = Field(default=None, alias="metadata")

    class Config:
        allow_population_by_field_name = True


class RubricResponse(BaseModel):
    rubric_id: str
    rubric_version_id: str
    version: int
    title: str
    description: Optional[str] = None
    criteria: List[RubricCriterion]
    metadata_payload: Optional[Dict[str, Any]] = Field(default=None, alias="metadata")
    created_at: datetime
    updated_at: datetime

    class Config:
        allow_population_by_field_name = True


class RubricListResponse(BaseModel):
    items: List[RubricResponse]


class RubricVersionsResponse(BaseModel):
    items: List[RubricResponse]


class GradingSessionCreate(BaseModel):
    submission_id: str
    rubric_id: str
    metadata_payload: Optional[Dict[str, Any]] = Field(default=None, alias="metadata")

    class Config:
        allow_population_by_field_name = True


class GradingSessionUpdate(BaseModel):
    scores: List[GradeEntry]


class GradingSessionResponse(BaseModel):
    session_id: str
    submission_id: str
    rubric_id: str
    rubric_version_id: str
    rubric_version: int
    status: str
    scores: List[GradeEntry]
    metadata_payload: Optional[Dict[str, Any]] = Field(default=None, alias="metadata")
    created_at: datetime
    updated_at: datetime
    finalized_at: Optional[datetime] = None

    class Config:
        allow_population_by_field_name = True


class AssessmentDashboardRequest(BaseModel):
    sandbox_id: str = Field(..., min_length=1)
    assignment_id: str = Field(..., min_length=1)
    course_id: Optional[str] = None
    output_dir: Optional[str] = None
    top_clusters: int = Field(default=8, ge=1, le=25)
    similarity_threshold: float = Field(default=0.6, ge=0.1, le=0.95)
    min_cluster_size: int = Field(default=2, ge=1, le=50)
    score_bins: int = Field(default=10, ge=3, le=30)
    chart_variant: Optional[Literal["v1", "v2", "v3"]] = None


HTTP_422 = status.HTTP_422_UNPROCESSABLE_ENTITY
