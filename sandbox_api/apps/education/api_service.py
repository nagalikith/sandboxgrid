from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List
from uuid import uuid4

from fastapi import HTTPException, status

from .api_models import (
    GradeEntry,
    GradingSessionRecord,
    GradingSessionResponse,
    RubricCriterion,
    RubricRecord,
    RubricResponse,
    SubmissionRecord,
    SubmissionResponse,
)
from .api_repositories import grading_repo, rubric_repo, submission_repo
from .jobs import GradingJobRecord, GradingJobRequest, GradingJobResponse


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def normalize_criteria(criteria: List[RubricCriterion]) -> List[RubricCriterion]:
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


def submission_response(record: SubmissionRecord) -> SubmissionResponse:
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


def rubric_response(record: RubricRecord) -> RubricResponse:
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


def session_response(record: GradingSessionRecord) -> GradingSessionResponse:
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


def grading_job_response(record: GradingJobRecord) -> GradingJobResponse:
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


def load_submission(owner_id: str, submission_id: str) -> SubmissionRecord:
    record = submission_repo.get(submission_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Submission not found.")
    if record.owner_id != owner_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden.")
    return record


def load_rubric_latest(owner_id: str, rubric_id: str) -> RubricRecord:
    record = rubric_repo.get_latest(owner_id=owner_id, rubric_id=rubric_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rubric not found.")
    return record


def load_session(owner_id: str, session_id: str) -> GradingSessionRecord:
    record = grading_repo.get(session_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
    if record.owner_id != owner_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden.")
    return record


def merge_scores(existing: List[GradeEntry], updates: List[GradeEntry]) -> List[GradeEntry]:
    score_map = {entry.criterion_id: entry for entry in existing}
    for entry in updates:
        score_map[entry.criterion_id] = entry
    return list(score_map.values())


def redact_grading_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    redacted = dict(payload)
    for key in ("canvas_token",):
        if redacted.get(key):
            redacted[key] = "<redacted>"
    return redacted
