from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import AnyUrl, BaseModel, Field
from sqlalchemy import Column, JSON
from sqlmodel import Field as SQLField, Session, SQLModel, select

from .dashboard_models import DashboardPayload
from .database import engine
from .grading_jobs import (
    GradingJobListResponse,
    GradingJobRecord,
    GradingJobRequest,
    GradingJobResponse,
    GradingJobStatus,
    grading_job_repo,
)
from .internal_auth import internal_auth_dependency
from .jobs import DashboardUpdateJob, GradingJob
from .rabbitmq import rabbitmq
from .grading_dashboard import build_assessment_dashboard


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_filename(name: str) -> str:
    cleaned = Path(name).name
    return cleaned or "submission.bin"


def _submission_storage_root() -> Path:
    artifacts_root = Path(os.getenv("SANDBOX_ARTIFACTS_ROOT", "./artifacts"))
    return artifacts_root / "submissions"


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


class SubmissionRepository:
    def __init__(self, engine) -> None:
        self.engine = engine

    def create(self, record: SubmissionRecord) -> SubmissionRecord:
        with Session(self.engine) as session:
            row = SubmissionRow.from_record(record)
            session.add(row)
            session.commit()
            session.refresh(row)
            return row.to_record()

    def get(self, submission_id: str) -> Optional[SubmissionRecord]:
        with Session(self.engine) as session:
            row = session.get(SubmissionRow, submission_id)
            return row.to_record() if row else None

    def list(self, *, owner_id: str, offset: int = 0, limit: int = 100) -> List[SubmissionRecord]:
        stmt = select(SubmissionRow).where(SubmissionRow.owner_id == owner_id).offset(offset).limit(limit)
        with Session(self.engine) as session:
            rows = session.exec(stmt).all()
            return [row.to_record() for row in rows]


class RubricRepository:
    def __init__(self, engine) -> None:
        self.engine = engine

    def create(self, record: RubricRecord) -> RubricRecord:
        with Session(self.engine) as session:
            row = RubricRow(
                rubric_version_id=record.rubric_version_id,
                rubric_id=record.rubric_id,
                owner_id=record.owner_id,
                version=record.version,
                title=record.title,
                description=record.description,
                criteria=[item.dict() for item in record.criteria],
                metadata_payload=record.metadata_payload,
                created_at=record.created_at,
                updated_at=record.updated_at,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return row.to_record()

    def get_latest(self, *, owner_id: str, rubric_id: str) -> Optional[RubricRecord]:
        stmt = (
            select(RubricRow)
            .where(RubricRow.owner_id == owner_id, RubricRow.rubric_id == rubric_id)
            .order_by(RubricRow.version.desc())
            .limit(1)
        )
        with Session(self.engine) as session:
            row = session.exec(stmt).first()
            return row.to_record() if row else None

    def get_version(self, *, owner_id: str, rubric_id: str, version: int) -> Optional[RubricRecord]:
        stmt = select(RubricRow).where(
            RubricRow.owner_id == owner_id,
            RubricRow.rubric_id == rubric_id,
            RubricRow.version == version,
        )
        with Session(self.engine) as session:
            row = session.exec(stmt).first()
            return row.to_record() if row else None

    def list_versions(self, *, owner_id: str, rubric_id: str) -> List[RubricRecord]:
        stmt = select(RubricRow).where(
            RubricRow.owner_id == owner_id,
            RubricRow.rubric_id == rubric_id,
        ).order_by(RubricRow.version.desc())
        with Session(self.engine) as session:
            rows = session.exec(stmt).all()
            return [row.to_record() for row in rows]

    def list_latest(self, *, owner_id: str) -> List[RubricRecord]:
        stmt = select(RubricRow).where(RubricRow.owner_id == owner_id).order_by(RubricRow.rubric_id, RubricRow.version)
        with Session(self.engine) as session:
            rows = session.exec(stmt).all()
            latest_by_id: Dict[str, RubricRecord] = {}
            for row in rows:
                record = row.to_record()
                latest_by_id[record.rubric_id] = record
            return list(latest_by_id.values())

    def delete(self, *, owner_id: str, rubric_id: str) -> int:
        stmt = select(RubricRow).where(
            RubricRow.owner_id == owner_id,
            RubricRow.rubric_id == rubric_id,
        )
        with Session(self.engine) as session:
            rows = session.exec(stmt).all()
            for row in rows:
                session.delete(row)
            session.commit()
            return len(rows)


class GradingSessionRepository:
    def __init__(self, engine) -> None:
        self.engine = engine

    def create(self, record: GradingSessionRecord) -> GradingSessionRecord:
        with Session(self.engine) as session:
            row = GradingSessionRow(
                session_id=record.session_id,
                owner_id=record.owner_id,
                submission_id=record.submission_id,
                rubric_id=record.rubric_id,
                rubric_version_id=record.rubric_version_id,
                rubric_version=record.rubric_version,
                status=record.status,
                scores=[item.dict() for item in record.scores],
                metadata_payload=record.metadata_payload,
                created_at=record.created_at,
                updated_at=record.updated_at,
                finalized_at=record.finalized_at,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return row.to_record()

    def get(self, session_id: str) -> Optional[GradingSessionRecord]:
        with Session(self.engine) as session:
            row = session.get(GradingSessionRow, session_id)
            return row.to_record() if row else None

    def update_scores(
        self,
        session_id: str,
        *,
        scores: List[GradeEntry],
        status: Optional[str] = None,
        finalized_at: Optional[datetime] = None,
    ) -> Optional[GradingSessionRecord]:
        with Session(self.engine) as session:
            row = session.get(GradingSessionRow, session_id)
            if not row:
                return None
            row.scores = [item.dict() for item in scores]
            if status:
                row.status = status
            row.updated_at = _now()
            if finalized_at:
                row.finalized_at = finalized_at
            session.add(row)
            session.commit()
            session.refresh(row)
            return row.to_record()


submission_repo = SubmissionRepository(engine)
rubric_repo = RubricRepository(engine)
grading_repo = GradingSessionRepository(engine)


router = APIRouter(prefix="/grading", tags=["grading"])
require_internal_auth = internal_auth_dependency()
require_internal_auth_no_hash = internal_auth_dependency(enforce_body_hash=False)


def _normalize_criteria(criteria: List[RubricCriterion]) -> List[RubricCriterion]:
    if not criteria:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Rubric must include criteria.")
    normalized: List[RubricCriterion] = []
    total_weight = 0.0
    for criterion in criteria:
        criterion_id = criterion.criterion_id or f"crit_{uuid4().hex[:8]}"
        weight = criterion.weight if criterion.weight is not None else 1.0
        total_weight += weight
        normalized.append(
            RubricCriterion(
                criterion_id=criterion_id,
                label=criterion.label,
                description=criterion.description,
                max_score=criterion.max_score,
                weight=weight,
            )
        )
    if total_weight <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Rubric weights must sum to a positive value.",
        )
    return normalized


def _submission_response(record: SubmissionRecord) -> SubmissionResponse:
    return SubmissionResponse(
        submission_id=record.submission_id,
        source_type=record.source_type,
        source_url=record.source_url,
        filename=record.filename,
        content_type=record.content_type,
        size_bytes=record.size_bytes,
        metadata_payload=record.metadata_payload,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _rubric_response(record: RubricRecord) -> RubricResponse:
    return RubricResponse(
        rubric_id=record.rubric_id,
        rubric_version_id=record.rubric_version_id,
        version=record.version,
        title=record.title,
        description=record.description,
        criteria=record.criteria,
        metadata_payload=record.metadata_payload,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _session_response(record: GradingSessionRecord) -> GradingSessionResponse:
    return GradingSessionResponse(
        session_id=record.session_id,
        submission_id=record.submission_id,
        rubric_id=record.rubric_id,
        rubric_version_id=record.rubric_version_id,
        rubric_version=record.rubric_version,
        status=record.status,
        scores=record.scores,
        metadata_payload=record.metadata_payload,
        created_at=record.created_at,
        updated_at=record.updated_at,
        finalized_at=record.finalized_at,
    )


def _grading_job_response(record: GradingJobRecord) -> GradingJobResponse:
    payload_data = dict(record.payload)
    if "canvas_token" in payload_data:
        payload_data["canvas_token"] = None
    return GradingJobResponse(
        job_id=record.job_id,
        status=record.status,
        payload=GradingJobRequest.parse_obj(payload_data),
        result=record.result,
        error_message=record.error_message,
        created_at=record.created_at,
        updated_at=record.updated_at,
        started_at=record.started_at,
        finished_at=record.finished_at,
    )


def _load_submission(owner_id: str, submission_id: str) -> SubmissionRecord:
    record = submission_repo.get(submission_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Submission not found.")
    if record.owner_id != owner_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden.")
    return record


def _load_rubric_latest(owner_id: str, rubric_id: str) -> RubricRecord:
    record = rubric_repo.get_latest(owner_id=owner_id, rubric_id=rubric_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rubric not found.")
    return record


def _load_session(owner_id: str, session_id: str) -> GradingSessionRecord:
    record = grading_repo.get(session_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
    if record.owner_id != owner_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden.")
    return record


def _merge_scores(existing: List[GradeEntry], updates: List[GradeEntry]) -> List[GradeEntry]:
    score_map = {entry.criterion_id: entry for entry in existing}
    for entry in updates:
        score_map[entry.criterion_id] = entry
    return list(score_map.values())


@router.post("/submissions", response_model=SubmissionResponse, status_code=status.HTTP_201_CREATED)
async def create_submission(
    payload: SubmissionCreate,
    owner_id: str = Depends(require_internal_auth),
) -> SubmissionResponse:
    submission_id = f"sub_{uuid4().hex[:10]}"
    now = _now()
    record = SubmissionRecord(
        submission_id=submission_id,
        owner_id=owner_id,
        source_type="url",
        source_url=str(payload.source_url),
        metadata_payload=payload.metadata_payload,
        created_at=now,
        updated_at=now,
    )
    created = submission_repo.create(record)
    return _submission_response(created)


@router.post("/submissions/upload", response_model=SubmissionResponse, status_code=status.HTTP_201_CREATED)
async def upload_submission(
    file: UploadFile = File(...),
    metadata: Optional[str] = Form(default=None),
    owner_id: str = Depends(require_internal_auth_no_hash),
) -> SubmissionResponse:
    submission_id = f"sub_{uuid4().hex[:10]}"
    now = _now()
    metadata_payload: Optional[Dict[str, Any]] = None
    if metadata:
        try:
            metadata_payload = json.loads(metadata)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid metadata JSON.",
            ) from exc

    filename = _safe_filename(file.filename or "submission.bin")
    base_dir = _submission_storage_root() / submission_id
    base_dir.mkdir(parents=True, exist_ok=True)
    file_path = base_dir / filename
    size_bytes = 0
    with file_path.open("wb") as handle:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)
            size_bytes += len(chunk)
    await file.close()

    record = SubmissionRecord(
        submission_id=submission_id,
        owner_id=owner_id,
        source_type="file",
        filename=filename,
        content_type=file.content_type,
        size_bytes=size_bytes,
        metadata_payload=metadata_payload,
        blob_path=str(file_path.resolve()),
        created_at=now,
        updated_at=now,
    )
    created = submission_repo.create(record)
    return _submission_response(created)


@router.get("/submissions/{submission_id}", response_model=SubmissionResponse)
async def get_submission(
    submission_id: str,
    owner_id: str = Depends(require_internal_auth),
) -> SubmissionResponse:
    record = _load_submission(owner_id, submission_id)
    return _submission_response(record)


@router.get("/submissions", response_model=List[SubmissionResponse])
async def list_submissions(
    owner_id: str = Depends(require_internal_auth),
    offset: int = 0,
    limit: int = 100,
) -> List[SubmissionResponse]:
    records = submission_repo.list(owner_id=owner_id, offset=offset, limit=limit)
    return [_submission_response(record) for record in records]


@router.post("/rubrics", response_model=RubricResponse, status_code=status.HTTP_201_CREATED)
async def create_rubric(
    payload: RubricUpsert,
    owner_id: str = Depends(require_internal_auth),
) -> RubricResponse:
    rubric_id = f"rbr_{uuid4().hex[:10]}"
    version_id = f"rbv_{uuid4().hex[:10]}"
    now = _now()
    criteria = _normalize_criteria(payload.criteria)
    record = RubricRecord(
        rubric_version_id=version_id,
        rubric_id=rubric_id,
        owner_id=owner_id,
        version=1,
        title=payload.title,
        description=payload.description,
        criteria=criteria,
        metadata_payload=payload.metadata_payload,
        created_at=now,
        updated_at=now,
    )
    created = rubric_repo.create(record)
    return _rubric_response(created)


@router.get("/rubrics/{rubric_id}", response_model=RubricResponse)
async def get_rubric(
    rubric_id: str,
    owner_id: str = Depends(require_internal_auth),
    version: Optional[int] = None,
) -> RubricResponse:
    if version is None:
        record = rubric_repo.get_latest(owner_id=owner_id, rubric_id=rubric_id)
    else:
        record = rubric_repo.get_version(owner_id=owner_id, rubric_id=rubric_id, version=version)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rubric not found.")
    return _rubric_response(record)


@router.get("/rubrics", response_model=RubricListResponse)
async def list_rubrics(
    owner_id: str = Depends(require_internal_auth),
) -> RubricListResponse:
    records = rubric_repo.list_latest(owner_id=owner_id)
    items = [_rubric_response(record) for record in records]
    return RubricListResponse(items=items)


@router.get("/rubrics/{rubric_id}/versions", response_model=RubricVersionsResponse)
async def list_rubric_versions(
    rubric_id: str,
    owner_id: str = Depends(require_internal_auth),
) -> RubricVersionsResponse:
    records = rubric_repo.list_versions(owner_id=owner_id, rubric_id=rubric_id)
    if not records:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rubric not found.")
    return RubricVersionsResponse(items=[_rubric_response(record) for record in records])


@router.put("/rubrics/{rubric_id}", response_model=RubricResponse)
async def update_rubric(
    rubric_id: str,
    payload: RubricUpsert,
    owner_id: str = Depends(require_internal_auth),
) -> RubricResponse:
    current = _load_rubric_latest(owner_id, rubric_id)
    version_id = f"rbv_{uuid4().hex[:10]}"
    now = _now()
    criteria = _normalize_criteria(payload.criteria)
    record = RubricRecord(
        rubric_version_id=version_id,
        rubric_id=rubric_id,
        owner_id=owner_id,
        version=current.version + 1,
        title=payload.title,
        description=payload.description,
        criteria=criteria,
        metadata_payload=payload.metadata_payload,
        created_at=now,
        updated_at=now,
    )
    created = rubric_repo.create(record)
    return _rubric_response(created)


@router.delete("/rubrics/{rubric_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_rubric(
    rubric_id: str,
    owner_id: str = Depends(require_internal_auth),
) -> None:
    deleted = rubric_repo.delete(owner_id=owner_id, rubric_id=rubric_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rubric not found.")
    return None


@router.post("/sessions", response_model=GradingSessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    payload: GradingSessionCreate,
    owner_id: str = Depends(require_internal_auth),
) -> GradingSessionResponse:
    submission = _load_submission(owner_id, payload.submission_id)
    rubric = _load_rubric_latest(owner_id, payload.rubric_id)
    session_id = f"grd_{uuid4().hex[:10]}"
    now = _now()
    record = GradingSessionRecord(
        session_id=session_id,
        owner_id=owner_id,
        submission_id=submission.submission_id,
        rubric_id=rubric.rubric_id,
        rubric_version_id=rubric.rubric_version_id,
        rubric_version=rubric.version,
        status="in_progress",
        scores=[],
        metadata_payload=payload.metadata_payload,
        created_at=now,
        updated_at=now,
        finalized_at=None,
    )
    created = grading_repo.create(record)
    return _session_response(created)


@router.get("/sessions/{session_id}", response_model=GradingSessionResponse)
async def get_session(
    session_id: str,
    owner_id: str = Depends(require_internal_auth),
) -> GradingSessionResponse:
    record = _load_session(owner_id, session_id)
    return _session_response(record)


@router.patch("/sessions/{session_id}", response_model=GradingSessionResponse)
async def update_session(
    session_id: str,
    payload: GradingSessionUpdate,
    owner_id: str = Depends(require_internal_auth),
) -> GradingSessionResponse:
    record = _load_session(owner_id, session_id)
    rubric = rubric_repo.get_version(
        owner_id=owner_id,
        rubric_id=record.rubric_id,
        version=record.rubric_version,
    )
    if not rubric:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rubric not found.")
    criteria_map = {criterion.criterion_id: criterion for criterion in rubric.criteria}
    for entry in payload.scores:
        criterion = criteria_map.get(entry.criterion_id)
        if not criterion:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid criterion.")
        if entry.score > criterion.max_score:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Score exceeds max_score.",
            )
    merged_scores = _merge_scores(record.scores, payload.scores)
    updated = grading_repo.update_scores(session_id, scores=merged_scores)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
    return _session_response(updated)


@router.post("/sessions/{session_id}/finalize", response_model=GradingSessionResponse)
async def finalize_session(
    session_id: str,
    owner_id: str = Depends(require_internal_auth),
) -> GradingSessionResponse:
    record = _load_session(owner_id, session_id)
    if record.status == "finalized":
        return _session_response(record)
    updated = grading_repo.update_scores(
        session_id,
        scores=record.scores,
        status="finalized",
        finalized_at=_now(),
    )
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
    return _session_response(updated)


@router.post("/jobs", response_model=GradingJobResponse, status_code=status.HTTP_201_CREATED)
async def create_grading_job(
    payload: GradingJobRequest,
    owner_id: str = Depends(require_internal_auth),
) -> GradingJobResponse:
    job_id = f"grj_{uuid4().hex[:10]}"
    now = _now()
    record = GradingJobRecord(
        job_id=job_id,
        owner_id=owner_id,
        status=GradingJobStatus.queued,
        payload=payload.dict(),
        created_at=now,
        updated_at=now,
        started_at=None,
        finished_at=None,
    )
    created = grading_job_repo.create(record)
    try:
        await rabbitmq.publish_job(
            GradingJob(
                job_id=job_id,
                owner_id=owner_id,
                payload=payload,
            )
        )
    except Exception as exc:  # noqa: BLE001
        grading_job_repo.update(
            job_id,
            status=GradingJobStatus.failed.value,
            error_message=f"Queue error: {exc}",
            updated_at=_now(),
            finished_at=_now(),
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Queue unavailable. Try again later.",
        ) from exc
    return _grading_job_response(created)


@router.get("/jobs/{job_id}", response_model=GradingJobResponse)
async def get_grading_job(
    job_id: str,
    owner_id: str = Depends(require_internal_auth),
) -> GradingJobResponse:
    record = grading_job_repo.get(job_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")
    if record.owner_id != owner_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden.")
    return _grading_job_response(record)


@router.get("/jobs", response_model=GradingJobListResponse)
async def list_grading_jobs(
    owner_id: str = Depends(require_internal_auth),
    offset: int = 0,
    limit: int = 100,
) -> GradingJobListResponse:
    records = grading_job_repo.list(owner_id=owner_id, offset=offset, limit=limit)
    return GradingJobListResponse(items=[_grading_job_response(record) for record in records])


@router.post("/assessment-dashboard", response_model=DashboardPayload, status_code=status.HTTP_202_ACCEPTED)
async def build_assessment_dashboard_payload(
    payload: AssessmentDashboardRequest,
    owner_id: str = Depends(require_internal_auth),
) -> DashboardPayload:
    output_dir = Path(payload.output_dir or "./artifacts/grading_runs").expanduser()
    dashboard = build_assessment_dashboard(
        output_dir=output_dir,
        assignment_id=payload.assignment_id,
        course_id=payload.course_id,
        top_clusters=payload.top_clusters,
        similarity_threshold=payload.similarity_threshold,
        min_cluster_size=payload.min_cluster_size,
        score_bins=payload.score_bins,
    )
    if payload.chart_variant:
        dashboard = dashboard.copy(update={"chart_variant": payload.chart_variant})
    try:
        await rabbitmq.publish_job(
            DashboardUpdateJob(
                sandbox_id=payload.sandbox_id,
                owner_id=owner_id,
                payload=DashboardPayload.parse_obj(dashboard.dict()),
            )
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Queue unavailable. Try again later.",
        ) from exc
    return dashboard
