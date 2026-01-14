from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import Column, JSON
from sqlmodel import Field as SQLField, Session, SQLModel, select

from .database import engine
from .paths import owner_directory


class ArtifactRecord(BaseModel):
    artifact_id: str
    owner_id: str
    session_id: Optional[str] = None
    sandbox_id: Optional[str] = None
    artifact_type: str
    source: Optional[str] = None
    run_id: Optional[str] = None
    volatility: Optional[str] = None
    artifact_format: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    size_bytes: Optional[int] = None
    mime_type: Optional[str] = None
    filename: Optional[str] = None
    checksum_sha256: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    sensitivity: Optional[str] = None
    attributes: Optional[Dict[str, Any]] = None
    blob_path: Optional[str] = None


class ArtifactRow(SQLModel, table=True):
    __tablename__ = "artifacts"

    artifact_id: str = SQLField(primary_key=True, index=True)
    owner_id: str = SQLField(index=True)
    session_id: Optional[str] = SQLField(default=None, index=True)
    sandbox_id: Optional[str] = SQLField(default=None, index=True)
    artifact_type: str = SQLField(index=True)
    source: Optional[str] = SQLField(default=None, index=True)
    run_id: Optional[str] = SQLField(default=None, index=True)
    volatility: Optional[str] = SQLField(default=None, index=True)
    artifact_format: Optional[str] = SQLField(default=None, index=True)
    created_at: datetime
    updated_at: datetime
    size_bytes: Optional[int] = None
    mime_type: Optional[str] = None
    filename: Optional[str] = None
    checksum_sha256: Optional[str] = None
    tags: List[str] = SQLField(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    sensitivity: Optional[str] = SQLField(default=None, index=True)
    attributes: Optional[Dict[str, Any]] = SQLField(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    blob_path: Optional[str] = None

    def to_record(self) -> ArtifactRecord:
        return ArtifactRecord(
            artifact_id=self.artifact_id,
            owner_id=self.owner_id,
            session_id=self.session_id,
            sandbox_id=self.sandbox_id,
            artifact_type=self.artifact_type,
            source=self.source,
            run_id=self.run_id,
            volatility=self.volatility,
            artifact_format=self.artifact_format,
            created_at=self.created_at,
            updated_at=self.updated_at,
            size_bytes=self.size_bytes,
            mime_type=self.mime_type,
            filename=self.filename,
            checksum_sha256=self.checksum_sha256,
            tags=self.tags or [],
            sensitivity=self.sensitivity,
            attributes=self.attributes,
            blob_path=self.blob_path,
        )

    @classmethod
    def from_record(cls, record: ArtifactRecord) -> "ArtifactRow":
        return cls(
            artifact_id=record.artifact_id,
            owner_id=record.owner_id,
            session_id=record.session_id,
            sandbox_id=record.sandbox_id,
            artifact_type=record.artifact_type,
            source=record.source,
            run_id=record.run_id,
            volatility=record.volatility,
            artifact_format=record.artifact_format,
            created_at=record.created_at,
            updated_at=record.updated_at,
            size_bytes=record.size_bytes,
            mime_type=record.mime_type,
            filename=record.filename,
            checksum_sha256=record.checksum_sha256,
            tags=record.tags,
            sensitivity=record.sensitivity,
            attributes=record.attributes,
            blob_path=record.blob_path,
        )


class ArtifactLinkRecord(BaseModel):
    parent_id: str
    child_id: str
    owner_id: str
    relation: Optional[str] = None
    created_at: datetime


class ArtifactLinkRow(SQLModel, table=True):
    __tablename__ = "artifact_links"

    link_id: Optional[int] = SQLField(default=None, primary_key=True)
    owner_id: str = SQLField(index=True)
    parent_id: str = SQLField(foreign_key="artifacts.artifact_id", index=True)
    child_id: str = SQLField(foreign_key="artifacts.artifact_id", index=True)
    relation: Optional[str] = None
    created_at: datetime

    def to_record(self) -> ArtifactLinkRecord:
        return ArtifactLinkRecord(
            parent_id=self.parent_id,
            child_id=self.child_id,
            owner_id=self.owner_id,
            relation=self.relation,
            created_at=self.created_at,
        )


class ArtifactRepository:
    def __init__(self, engine) -> None:
        self.engine = engine

    def create(self, record: ArtifactRecord) -> ArtifactRecord:
        with Session(self.engine) as session:
            row = ArtifactRow.from_record(record)
            session.add(row)
            session.commit()
            session.refresh(row)
            return row.to_record()

    def get(self, artifact_id: str) -> Optional[ArtifactRecord]:
        with Session(self.engine) as session:
            row = session.get(ArtifactRow, artifact_id)
            return row.to_record() if row else None

    def list(
        self,
        *,
        owner_id: str,
        session_id: Optional[str] = None,
        artifact_type: Optional[str] = None,
        source: Optional[str] = None,
        run_id: Optional[str] = None,
        volatility: Optional[str] = None,
        artifact_format: Optional[str] = None,
        sensitivity: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        offset: int = 0,
        limit: int = 100,
    ) -> List[ArtifactRecord]:
        stmt = select(ArtifactRow).where(ArtifactRow.owner_id == owner_id)
        if session_id:
            stmt = stmt.where(ArtifactRow.session_id == session_id)
        if artifact_type:
            stmt = stmt.where(ArtifactRow.artifact_type == artifact_type)
        if source:
            stmt = stmt.where(ArtifactRow.source == source)
        if run_id:
            stmt = stmt.where(ArtifactRow.run_id == run_id)
        if volatility:
            stmt = stmt.where(ArtifactRow.volatility == volatility)
        if artifact_format:
            stmt = stmt.where(ArtifactRow.artifact_format == artifact_format)
        if sensitivity:
            stmt = stmt.where(ArtifactRow.sensitivity == sensitivity)
        if start_time:
            stmt = stmt.where(ArtifactRow.created_at >= start_time)
        if end_time:
            stmt = stmt.where(ArtifactRow.created_at <= end_time)
        stmt = stmt.order_by(ArtifactRow.created_at.desc()).offset(offset).limit(limit)
        with Session(self.engine) as session:
            rows = session.exec(stmt).all()
            return [row.to_record() for row in rows]

    def update_blob(
        self,
        artifact_id: str,
        *,
        blob_path: str,
        size_bytes: int,
        checksum_sha256: str,
        mime_type: Optional[str],
    ) -> Optional[ArtifactRecord]:
        with Session(self.engine) as session:
            row = session.get(ArtifactRow, artifact_id)
            if not row:
                return None
            row.blob_path = blob_path
            row.size_bytes = size_bytes
            row.checksum_sha256 = checksum_sha256
            if mime_type:
                row.mime_type = mime_type
            row.updated_at = datetime.now(timezone.utc)
            session.add(row)
            session.commit()
            session.refresh(row)
            return row.to_record()

    def touch(self, artifact_id: str) -> Optional[ArtifactRecord]:
        with Session(self.engine) as session:
            row = session.get(ArtifactRow, artifact_id)
            if not row:
                return None
            row.updated_at = datetime.now(timezone.utc)
            session.add(row)
            session.commit()
            session.refresh(row)
            return row.to_record()

    def add_links(self, links: Iterable[ArtifactLinkRecord]) -> None:
        with Session(self.engine) as session:
            for link in links:
                session.add(
                    ArtifactLinkRow(
                        owner_id=link.owner_id,
                        parent_id=link.parent_id,
                        child_id=link.child_id,
                        relation=link.relation,
                        created_at=link.created_at,
                    )
                )
            session.commit()

    def parents_for(self, *, owner_id: str, child_ids: Iterable[str]) -> Dict[str, List[str]]:
        unique_ids = list(dict.fromkeys(child_ids))
        if not unique_ids:
            return {}
        stmt = select(ArtifactLinkRow).where(
            ArtifactLinkRow.owner_id == owner_id,
            ArtifactLinkRow.child_id.in_(unique_ids),
        )
        with Session(self.engine) as session:
            rows = session.exec(stmt).all()
        mapping: Dict[str, List[str]] = {child_id: [] for child_id in unique_ids}
        for row in rows:
            mapping.setdefault(row.child_id, []).append(row.parent_id)
        return mapping


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def objects_dir(self, owner_id: str) -> Path:
        return self.root / "users" / owner_directory(owner_id) / "objects"

    def blob_path(self, owner_id: str, artifact_id: str) -> Path:
        return self.objects_dir(owner_id) / artifact_id

    def blob_pointer(self, record: ArtifactRecord) -> Optional["ArtifactBlobPointer"]:
        if not record.blob_path:
            return None
        path = Path(record.blob_path)
        try:
            uri = str(path.relative_to(self.root))
        except ValueError:
            uri = str(path)
        return ArtifactBlobPointer(
            location="local_path",
            uri=uri,
            size_bytes=record.size_bytes,
            checksum_sha256=record.checksum_sha256,
        )


class ArtifactCreate(BaseModel):
    session_id: Optional[str] = None
    sandbox_id: Optional[str] = None
    artifact_type: str = Field(alias="type")
    source: Optional[str] = None
    run_id: Optional[str] = None
    volatility: Optional[str] = None
    artifact_format: Optional[str] = Field(default=None, alias="format")
    tags: List[str] = Field(default_factory=list)
    sensitivity: Optional[str] = None
    attributes: Optional[Dict[str, Any]] = None
    filename: Optional[str] = None
    mime_type: Optional[str] = None

    class Config:
        allow_population_by_field_name = True


class ArtifactBlobPointer(BaseModel):
    location: str
    uri: str
    size_bytes: Optional[int] = None
    checksum_sha256: Optional[str] = None


class ArtifactResponse(BaseModel):
    artifact_id: str
    session_id: Optional[str] = None
    sandbox_id: Optional[str] = None
    artifact_type: str = Field(alias="type")
    source: Optional[str] = None
    run_id: Optional[str] = None
    volatility: Optional[str] = None
    artifact_format: Optional[str] = Field(default=None, alias="format")
    created_at: datetime
    updated_at: datetime
    size_bytes: Optional[int] = None
    mime_type: Optional[str] = None
    filename: Optional[str] = None
    hash_value: Optional[str] = Field(default=None, alias="hash")
    tags: List[str] = Field(default_factory=list)
    sensitivity: Optional[str] = None
    attributes: Optional[Dict[str, Any]] = None
    parents: List[str] = Field(default_factory=list)
    blob: Optional[ArtifactBlobPointer] = None

    class Config:
        allow_population_by_field_name = True


class ArtifactListResponse(BaseModel):
    items: List[ArtifactResponse]
    count: int


class ArtifactUploadInfo(BaseModel):
    upload_url: str
    method: str = "PUT"
    headers: Dict[str, str] = Field(default_factory=dict)
    expires_at: datetime


class ArtifactBlobResult(BaseModel):
    artifact: Optional[ArtifactResponse] = None
    upload: Optional[ArtifactUploadInfo] = None


class ArtifactDeriveRequest(BaseModel):
    parent_ids: List[str]
    relation: Optional[str] = None


class ArtifactDeriveResponse(BaseModel):
    artifact_id: str
    parent_ids: List[str]
    relation: Optional[str] = None


class ArtifactManifestResponse(BaseModel):
    session_id: str
    artifacts: List[ArtifactResponse]
    count: int


repository = ArtifactRepository(engine)
store = ArtifactStore(Path(os.getenv("SANDBOX_ARTIFACTS_ROOT", "./artifacts")))
router = APIRouter(tags=["artifacts"])


async def get_agent_id(x_agent_id: str | None = Header(default=None)) -> str:
    return x_agent_id or "anonymous"


def _build_response(record: ArtifactRecord, *, parents: Optional[List[str]] = None) -> ArtifactResponse:
    return ArtifactResponse(
        artifact_id=record.artifact_id,
        session_id=record.session_id,
        sandbox_id=record.sandbox_id,
        artifact_type=record.artifact_type,
        source=record.source,
        run_id=record.run_id,
        volatility=record.volatility,
        artifact_format=record.artifact_format,
        created_at=record.created_at,
        updated_at=record.updated_at,
        size_bytes=record.size_bytes,
        mime_type=record.mime_type,
        filename=record.filename,
        hash_value=record.checksum_sha256,
        tags=record.tags,
        sensitivity=record.sensitivity,
        attributes=record.attributes,
        parents=parents or [],
        blob=store.blob_pointer(record),
    )


def _parents_map(owner_id: str, records: Iterable[ArtifactRecord]) -> Dict[str, List[str]]:
    child_ids = [record.artifact_id for record in records]
    return repository.parents_for(owner_id=owner_id, child_ids=child_ids)


@router.post(
    "/artifacts",
    status_code=status.HTTP_201_CREATED,
    response_model=ArtifactResponse,
    summary="Create artifact metadata",
)
async def create_artifact(
    payload: ArtifactCreate,
    agent_id: str = Depends(get_agent_id),
) -> ArtifactResponse:
    now = datetime.now(timezone.utc)
    record = ArtifactRecord(
        artifact_id=f"art_{uuid4().hex[:12]}",
        owner_id=agent_id,
        session_id=payload.session_id,
        sandbox_id=payload.sandbox_id,
        artifact_type=payload.artifact_type,
        source=payload.source,
        run_id=payload.run_id,
        volatility=payload.volatility,
        artifact_format=payload.artifact_format,
        created_at=now,
        updated_at=now,
        size_bytes=None,
        mime_type=payload.mime_type,
        filename=payload.filename,
        checksum_sha256=None,
        tags=payload.tags,
        sensitivity=payload.sensitivity,
        attributes=payload.attributes,
        blob_path=None,
    )
    created = repository.create(record)
    return _build_response(created)


@router.put(
    "/artifacts/{artifact_id}/blob",
    response_model=ArtifactBlobResult,
    summary="Upload artifact blob or request upload URL",
)
async def upload_blob(
    artifact_id: str,
    request: Request,
    presign: bool = Query(False, description="Return upload URL instead of uploading."),
    agent_id: str = Depends(get_agent_id),
) -> ArtifactBlobResult:
    record = repository.get(artifact_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found.")
    if record.owner_id != agent_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden.")

    if presign:
        upload_url = str(request.url.remove_query_params("presign"))
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=15)
        return ArtifactBlobResult(
            upload=ArtifactUploadInfo(upload_url=upload_url, expires_at=expires_at)
        )

    blob_path = store.blob_path(record.owner_id, record.artifact_id)
    blob_path.parent.mkdir(parents=True, exist_ok=True)
    size_bytes = 0
    hasher = hashlib.sha256()
    with blob_path.open("wb") as handle:
        async for chunk in request.stream():
            if not chunk:
                continue
            handle.write(chunk)
            size_bytes += len(chunk)
            hasher.update(chunk)

    if size_bytes == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty upload.")

    content_type = request.headers.get("content-type")
    updated = repository.update_blob(
        record.artifact_id,
        blob_path=str(blob_path.resolve()),
        size_bytes=size_bytes,
        checksum_sha256=hasher.hexdigest(),
        mime_type=content_type,
    )
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found.")
    return ArtifactBlobResult(artifact=_build_response(updated))


@router.get(
    "/artifacts/{artifact_id}",
    response_model=ArtifactResponse,
    summary="Get artifact metadata",
)
async def get_artifact(
    artifact_id: str,
    agent_id: str = Depends(get_agent_id),
) -> ArtifactResponse:
    record = repository.get(artifact_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found.")
    if record.owner_id != agent_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden.")
    parents_map = repository.parents_for(owner_id=agent_id, child_ids=[record.artifact_id])
    parents = parents_map.get(record.artifact_id, [])
    return _build_response(record, parents=parents)


@router.get(
    "/artifacts",
    response_model=ArtifactListResponse,
    summary="List artifacts",
)
async def list_artifacts(
    agent_id: str = Depends(get_agent_id),
    session_id: Optional[str] = Query(default=None),
    artifact_type: Optional[str] = Query(default=None, alias="type"),
    source: Optional[str] = Query(default=None),
    run_id: Optional[str] = Query(default=None),
    volatility: Optional[str] = Query(default=None),
    artifact_format: Optional[str] = Query(default=None, alias="format"),
    sensitivity: Optional[str] = Query(default=None),
    tags: Optional[List[str]] = Query(default=None),
    start_time: Optional[datetime] = Query(default=None),
    end_time: Optional[datetime] = Query(default=None),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
) -> ArtifactListResponse:
    records = repository.list(
        owner_id=agent_id,
        session_id=session_id,
        artifact_type=artifact_type,
        source=source,
        run_id=run_id,
        volatility=volatility,
        artifact_format=artifact_format,
        sensitivity=sensitivity,
        start_time=start_time,
        end_time=end_time,
        offset=offset,
        limit=limit,
    )
    if tags:
        tag_set = set(tags)
        records = [record for record in records if tag_set.issubset(set(record.tags or []))]
    parents_map = _parents_map(agent_id, records)
    items = [
        _build_response(record, parents=parents_map.get(record.artifact_id, []))
        for record in records
    ]
    return ArtifactListResponse(items=items, count=len(items))


@router.post(
    "/artifacts/{artifact_id}/derive",
    response_model=ArtifactDeriveResponse,
    summary="Link derived artifact to parents",
)
async def derive_artifact(
    artifact_id: str,
    payload: ArtifactDeriveRequest,
    agent_id: str = Depends(get_agent_id),
) -> ArtifactDeriveResponse:
    child = repository.get(artifact_id)
    if not child:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found.")
    if child.owner_id != agent_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden.")

    parents = []
    for parent_id in payload.parent_ids:
        parent = repository.get(parent_id)
        if not parent:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Parent artifact not found: {parent_id}",
            )
        if parent.owner_id != agent_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden.")
        parents.append(parent)

    now = datetime.now(timezone.utc)
    links = [
        ArtifactLinkRecord(
            parent_id=parent.artifact_id,
            child_id=child.artifact_id,
            owner_id=agent_id,
            relation=payload.relation,
            created_at=now,
        )
        for parent in parents
    ]
    repository.add_links(links)
    repository.touch(child.artifact_id)
    return ArtifactDeriveResponse(
        artifact_id=child.artifact_id,
        parent_ids=payload.parent_ids,
        relation=payload.relation,
    )


@router.get(
    "/sessions/{session_id}/manifest",
    response_model=ArtifactManifestResponse,
    summary="Get session manifest",
)
async def session_manifest(
    session_id: str,
    agent_id: str = Depends(get_agent_id),
) -> ArtifactManifestResponse:
    records = repository.list(owner_id=agent_id, session_id=session_id, limit=500)
    key_tags = {"key", "manifest", "hero"}
    keyed = [
        record for record in records if set(record.tags or []).intersection(key_tags)
    ]
    manifest_records = keyed or records
    parents_map = _parents_map(agent_id, manifest_records)
    artifacts = [
        _build_response(record, parents=parents_map.get(record.artifact_id, []))
        for record in manifest_records
    ]
    return ArtifactManifestResponse(
        session_id=session_id,
        artifacts=artifacts,
        count=len(artifacts),
    )
