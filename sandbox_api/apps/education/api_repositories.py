from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlmodel import Session, select

from ...core.database import engine
from .api_models import (
    GradeEntry,
    GradingSessionRecord,
    GradingSessionRow,
    RubricRecord,
    RubricRow,
    SubmissionRecord,
    SubmissionRow,
)


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
        stmt = (
            select(RubricRow)
            .where(RubricRow.owner_id == owner_id, RubricRow.rubric_id == rubric_id)
            .order_by(RubricRow.version.desc())
        )
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
        updated_at: datetime,
    ) -> Optional[GradingSessionRecord]:
        with Session(self.engine) as session:
            row = session.get(GradingSessionRow, session_id)
            if not row:
                return None
            row.scores = [item.dict() for item in scores]
            if status:
                row.status = status
            row.updated_at = updated_at
            if finalized_at:
                row.finalized_at = finalized_at
            session.add(row)
            session.commit()
            session.refresh(row)
            return row.to_record()


submission_repo = SubmissionRepository(engine)
rubric_repo = RubricRepository(engine)
grading_repo = GradingSessionRepository(engine)
