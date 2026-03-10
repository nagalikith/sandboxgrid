from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from ...core.internal_auth import internal_auth_dependency
from ...core.jobs import DashboardUpdateJob, GradingJob
from ...core.rabbitmq import rabbitmq
from ...dashboards.models import DashboardPayload
from .api_models import (
    AssessmentDashboardRequest,
    GradingSessionCreate,
    GradingSessionRecord,
    GradingSessionResponse,
    GradingSessionUpdate,
    RubricListResponse,
    RubricRecord,
    RubricResponse,
    RubricUpsert,
    RubricVersionsResponse,
    SubmissionCreate,
    SubmissionRecord,
    SubmissionResponse,
)
from .api_repositories import grading_repo, rubric_repo, submission_repo
from .api_service import (
    grading_job_response,
    load_rubric_latest,
    load_session,
    load_submission,
    merge_scores,
    normalize_criteria,
    now_utc,
    redact_grading_payload,
    rubric_response,
    session_response,
    submission_response,
)
from .dashboard import build_assessment_dashboard
from .jobs import (
    GradingJobListResponse,
    GradingJobRecord,
    GradingJobRequest,
    GradingJobResponse,
    GradingJobStatus,
    grading_job_repo,
)


logger = logging.getLogger("sandbox.api.grading")
router = APIRouter(prefix="/grading", tags=["grading"])
require_internal_auth = internal_auth_dependency()
require_internal_auth_no_hash = internal_auth_dependency(enforce_body_hash=False)


def safe_filename(name: str) -> str:
    cleaned = Path(name).name
    return cleaned or "submission.bin"


def submission_storage_root() -> Path:
    artifacts_root = Path(os.getenv("SANDBOX_ARTIFACTS_ROOT", "./artifacts"))
    return artifacts_root / "submissions"


@router.post("/submissions", response_model=SubmissionResponse, status_code=status.HTTP_201_CREATED)
async def create_submission(
    payload: SubmissionCreate,
    owner_id: str = Depends(require_internal_auth),
) -> SubmissionResponse:
    submission_id = f"sub_{uuid4().hex[:10]}"
    now = now_utc()
    record = SubmissionRecord(
        submission_id=submission_id,
        owner_id=owner_id,
        source_type="url",
        source_url=str(payload.source_url),
        metadata_payload=payload.metadata_payload,
        created_at=now,
        updated_at=now,
    )
    return submission_response(submission_repo.create(record))


@router.post("/submissions/upload", response_model=SubmissionResponse, status_code=status.HTTP_201_CREATED)
async def upload_submission(
    file: UploadFile = File(...),
    metadata: Optional[str] = Form(default=None),
    owner_id: str = Depends(require_internal_auth_no_hash),
) -> SubmissionResponse:
    submission_id = f"sub_{uuid4().hex[:10]}"
    now = now_utc()
    metadata_payload: Optional[Dict[str, Any]] = None
    if metadata:
        try:
            metadata_payload = json.loads(metadata)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid metadata JSON.",
            ) from exc

    filename = safe_filename(file.filename or "submission.bin")
    base_dir = submission_storage_root() / submission_id
    base_dir.mkdir(parents=True, exist_ok=True)
    file_path = base_dir / filename
    size_bytes = 0

    # Stream uploads to disk so large student submissions do not sit in memory.
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
    return submission_response(submission_repo.create(record))


@router.get("/submissions/{submission_id}", response_model=SubmissionResponse)
async def get_submission(
    submission_id: str,
    owner_id: str = Depends(require_internal_auth),
) -> SubmissionResponse:
    return submission_response(load_submission(owner_id, submission_id))


@router.get("/submissions", response_model=List[SubmissionResponse])
async def list_submissions(
    owner_id: str = Depends(require_internal_auth),
    offset: int = 0,
    limit: int = 100,
) -> List[SubmissionResponse]:
    return [submission_response(record) for record in submission_repo.list(owner_id=owner_id, offset=offset, limit=limit)]


@router.post("/rubrics", response_model=RubricResponse, status_code=status.HTTP_201_CREATED)
async def create_rubric(
    payload: RubricUpsert,
    owner_id: str = Depends(require_internal_auth),
) -> RubricResponse:
    now = now_utc()
    record = RubricRecord(
        rubric_version_id=f"rbv_{uuid4().hex[:10]}",
        rubric_id=f"rbr_{uuid4().hex[:10]}",
        owner_id=owner_id,
        version=1,
        title=payload.title,
        description=payload.description,
        criteria=normalize_criteria(payload.criteria),
        metadata_payload=payload.metadata_payload,
        created_at=now,
        updated_at=now,
    )
    return rubric_response(rubric_repo.create(record))


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
    return rubric_response(record)


@router.get("/rubrics", response_model=RubricListResponse)
async def list_rubrics(
    owner_id: str = Depends(require_internal_auth),
) -> RubricListResponse:
    items = [rubric_response(record) for record in rubric_repo.list_latest(owner_id=owner_id)]
    return RubricListResponse(items=items)


@router.get("/rubrics/{rubric_id}/versions", response_model=RubricVersionsResponse)
async def list_rubric_versions(
    rubric_id: str,
    owner_id: str = Depends(require_internal_auth),
) -> RubricVersionsResponse:
    records = rubric_repo.list_versions(owner_id=owner_id, rubric_id=rubric_id)
    if not records:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rubric not found.")
    return RubricVersionsResponse(items=[rubric_response(record) for record in records])


@router.put("/rubrics/{rubric_id}", response_model=RubricResponse)
async def update_rubric(
    rubric_id: str,
    payload: RubricUpsert,
    owner_id: str = Depends(require_internal_auth),
) -> RubricResponse:
    current = load_rubric_latest(owner_id, rubric_id)
    now = now_utc()
    record = RubricRecord(
        rubric_version_id=f"rbv_{uuid4().hex[:10]}",
        rubric_id=rubric_id,
        owner_id=owner_id,
        version=current.version + 1,
        title=payload.title,
        description=payload.description,
        criteria=normalize_criteria(payload.criteria),
        metadata_payload=payload.metadata_payload,
        created_at=now,
        updated_at=now,
    )
    return rubric_response(rubric_repo.create(record))


@router.delete("/rubrics/{rubric_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_rubric(
    rubric_id: str,
    owner_id: str = Depends(require_internal_auth),
) -> None:
    if not rubric_repo.delete(owner_id=owner_id, rubric_id=rubric_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rubric not found.")
    return None


@router.post("/sessions", response_model=GradingSessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    payload: GradingSessionCreate,
    owner_id: str = Depends(require_internal_auth),
) -> GradingSessionResponse:
    submission = load_submission(owner_id, payload.submission_id)
    rubric = load_rubric_latest(owner_id, payload.rubric_id)
    now = now_utc()
    created = grading_repo.create(
        GradingSessionRecord(
            session_id=f"grd_{uuid4().hex[:10]}",
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
    )
    return session_response(created)


@router.get("/sessions/{session_id}", response_model=GradingSessionResponse)
async def get_session(
    session_id: str,
    owner_id: str = Depends(require_internal_auth),
) -> GradingSessionResponse:
    return session_response(load_session(owner_id, session_id))


@router.patch("/sessions/{session_id}", response_model=GradingSessionResponse)
async def update_session(
    session_id: str,
    payload: GradingSessionUpdate,
    owner_id: str = Depends(require_internal_auth),
) -> GradingSessionResponse:
    record = load_session(owner_id, session_id)
    rubric = rubric_repo.get_version(owner_id=owner_id, rubric_id=record.rubric_id, version=record.rubric_version)
    if not rubric:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rubric not found.")

    criteria_map = {criterion.criterion_id: criterion for criterion in rubric.criteria}
    for entry in payload.scores:
        criterion = criteria_map.get(entry.criterion_id)
        if not criterion:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid criterion.")
        if entry.score > criterion.max_score:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Score exceeds max_score.")

    updated = grading_repo.update_scores(
        session_id,
        scores=merge_scores(record.scores, payload.scores),
        updated_at=now_utc(),
    )
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
    return session_response(updated)


@router.post("/sessions/{session_id}/finalize", response_model=GradingSessionResponse)
async def finalize_session(
    session_id: str,
    owner_id: str = Depends(require_internal_auth),
) -> GradingSessionResponse:
    record = load_session(owner_id, session_id)
    if record.status == "finalized":
        return session_response(record)

    finalized_at = now_utc()
    updated = grading_repo.update_scores(
        session_id,
        scores=record.scores,
        status="finalized",
        finalized_at=finalized_at,
        updated_at=finalized_at,
    )
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
    return session_response(updated)


@router.post("/jobs", response_model=GradingJobResponse, status_code=status.HTTP_201_CREATED)
async def create_grading_job(
    payload: GradingJobRequest,
    owner_id: str = Depends(require_internal_auth),
) -> GradingJobResponse:
    job_id = f"grj_{uuid4().hex[:10]}"
    logger.info(
        "grading_job_create request job_id=%s owner_id=%s payload=%s",
        job_id,
        owner_id,
        json.dumps(redact_grading_payload(payload.dict()), ensure_ascii=True, sort_keys=True),
    )

    now = now_utc()
    created = grading_job_repo.create(
        GradingJobRecord(
            job_id=job_id,
            owner_id=owner_id,
            status=GradingJobStatus.queued,
            payload=payload.dict(),
            created_at=now,
            updated_at=now,
            started_at=None,
            finished_at=None,
        )
    )
    try:
        await rabbitmq.publish_job(GradingJob(type="grading", job_id=job_id, owner_id=owner_id, payload=payload))
        logger.info("grading_job_create queued job_id=%s owner_id=%s", job_id, owner_id)
    except Exception as exc:  # noqa: BLE001
        failed_at = now_utc()
        grading_job_repo.update(
            job_id,
            status=GradingJobStatus.failed.value,
            error_message=f"Queue error: {exc}",
            updated_at=failed_at,
            finished_at=failed_at,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Queue unavailable. Try again later.",
        ) from exc
    return grading_job_response(created)


@router.get("/jobs/{job_id}", response_model=GradingJobResponse)
async def get_grading_job(
    job_id: str,
    owner_id: str = Depends(require_internal_auth),
) -> GradingJobResponse:
    logger.info("grading_job_get request job_id=%s owner_id=%s", job_id, owner_id)
    record = grading_job_repo.get(job_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.")
    if record.owner_id != owner_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden.")
    logger.info(
        "grading_job_get response job_id=%s owner_id=%s status=%s started_at=%s finished_at=%s error=%s",
        job_id,
        owner_id,
        record.status,
        record.started_at,
        record.finished_at,
        record.error_message,
    )
    return grading_job_response(record)


@router.get("/jobs", response_model=GradingJobListResponse)
async def list_grading_jobs(
    owner_id: str = Depends(require_internal_auth),
    offset: int = 0,
    limit: int = 100,
) -> GradingJobListResponse:
    records = grading_job_repo.list(owner_id=owner_id, offset=offset, limit=limit)
    return GradingJobListResponse(items=[grading_job_response(record) for record in records])


@router.post("/assessment-dashboard", response_model=DashboardPayload, status_code=status.HTTP_202_ACCEPTED)
async def build_assessment_dashboard_payload(
    payload: AssessmentDashboardRequest,
    owner_id: str = Depends(require_internal_auth),
) -> DashboardPayload:
    dashboard = build_assessment_dashboard(
        output_dir=Path(payload.output_dir or "./artifacts/grading_runs").expanduser(),
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
