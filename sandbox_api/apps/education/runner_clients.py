from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import pdfplumber
import requests

try:
    import fitz  # PyMuPDF
except Exception:  # noqa: BLE001
    fitz = None

try:
    import fcntl
except Exception:  # noqa: BLE001
    fcntl = None

from .runner_models import AttachmentInfo, CanvasSubmission


class CanvasClient:
    def __init__(self, base_url: str, token: str, timeout: int = 60) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}"})

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        resp = self.session.get(self._url(path), params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def get_assignment(self, course_id: str, assignment_id: str) -> Dict[str, Any]:
        return self.get(
            f"/api/v1/courses/{course_id}/assignments/{assignment_id}",
            params={"include[]": ["rubric", "description", "attachments"]},
        )

    def get_course(self, course_id: str) -> Dict[str, Any]:
        return self.get(f"/api/v1/courses/{course_id}")

    def get_user(self, student_id: str, course_id: Optional[str] = None) -> Dict[str, Any]:
        if course_id:
            try:
                return self.get(f"/api/v1/courses/{course_id}/users/{student_id}")
            except requests.HTTPError:
                pass
        return self.get(f"/api/v1/users/{student_id}")

    def get_submission(self, course_id: str, assignment_id: str, student_id: str) -> CanvasSubmission:
        data = self.get(
            f"/api/v1/courses/{course_id}/assignments/{assignment_id}/submissions/{student_id}",
            params={"include[]": ["submission_history", "rubric", "assignment", "user"]},
        )
        return CanvasSubmission(
            submission_id=data.get("id"),
            attempt=data.get("attempt"),
            attachments=self._extract_attachments(data),
            raw=data,
        )

    def _extract_attachments(self, payload: Dict[str, Any]) -> List[AttachmentInfo]:
        attachments = payload.get("attachments") or []
        if not attachments and payload.get("submission_history"):
            history = payload.get("submission_history") or []
            if history:
                attachments = history[-1].get("attachments") or []
        results: List[AttachmentInfo] = []
        for item in attachments:
            url = item.get("url") or item.get("download_url") or item.get("href")
            if not url:
                continue
            results.append(
                AttachmentInfo(
                    filename=item.get("filename") or "submission.bin",
                    content_type=item.get("content-type") or item.get("content_type") or item.get("mime_type") or "",
                    url=url,
                    size=item.get("size"),
                )
            )
        return results

    def get_assignment_attachments(self, assignment: Dict[str, Any]) -> List[AttachmentInfo]:
        return self._extract_attachments(assignment)

    def download_attachment(self, attachment: AttachmentInfo, dest: Path) -> Path:
        resp = self.session.get(attachment.url, timeout=self.timeout, stream=True)
        resp.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("wb") as handle:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)
        return dest


class InternalAuth:
    def __init__(self, secret: str, user_id: str) -> None:
        self.secret = secret.encode("utf-8")
        self.user_id = user_id

    def headers(self, method: str, path: str, body: bytes = b"", content_type: Optional[str] = None) -> Dict[str, str]:
        timestamp = str(int(time.time()))
        body_hash = hashlib.sha256(body).hexdigest()
        signature_payload = f"{timestamp}\n{method.upper()}\n{path}\n{body_hash}"
        signature = hmac.new(self.secret, signature_payload.encode("utf-8"), hashlib.sha256).hexdigest()
        headers = {
            "X-Internal-Timestamp": timestamp,
            "X-Body-SHA256": body_hash,
            "X-Internal-Signature": signature,
            "X-User-Id": self.user_id,
        }
        if content_type:
            headers["Content-Type"] = content_type
        return headers


class SandboxClient:
    def __init__(self, base_url: str, auth: InternalAuth, timeout: int = 60) -> None:
        self.base_url = base_url.rstrip("/")
        self.auth = auth
        self.timeout = timeout

    def _encode_params(self, params: Optional[Dict[str, Any]]) -> Tuple[str, str]:
        if not params:
            return "", ""
        pairs: List[Tuple[str, Any]] = []
        for key in sorted(params.keys()):
            value = params[key]
            if isinstance(value, list):
                for item in value:
                    pairs.append((key, item))
            else:
                pairs.append((key, value))
        query = urlencode(pairs, doseq=True)
        return query, f"?{query}" if query else ""

    def _request(self, method: str, path: str, *, params: Optional[Dict[str, Any]] = None, payload: Any = None) -> Any:
        body = b""
        content_type = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
            content_type = "application/json"
        _query, path_query = self._encode_params(params)
        full_path = f"{path}{path_query}"
        headers = self.auth.headers(method, full_path, body=body, content_type=content_type)
        resp = requests.request(
            method,
            f"{self.base_url}{full_path}",
            headers=headers,
            data=body if payload is not None else None,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json() if resp.content else None

    def create_sandbox(self, ttl_seconds: int = 3600) -> Dict[str, Any]:
        return self._request("POST", "/sandboxes", payload={"ttl_seconds": ttl_seconds})

    def get_sandbox(self, sandbox_id: str) -> Dict[str, Any]:
        return self._request("GET", f"/sandboxes/{sandbox_id}")

    def wait_ready(self, sandbox_id: str, timeout: int = 180) -> Dict[str, Any]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            record = self.get_sandbox(sandbox_id)
            status = record.get("status")
            if status == "ready":
                return record
            if status == "error":
                raise RuntimeError(f"Sandbox error: {record.get('message')}")
            time.sleep(2)
        raise TimeoutError("Sandbox not ready in time.")

    def run_steps(self, sandbox_id: str, steps_payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request("POST", f"/sandboxes/{sandbox_id}/commands/steps", payload=steps_payload)

    def list_artifacts(self, *, run_id: Optional[str] = None, artifact_type: Optional[str] = None, limit: int = 100) -> Dict[str, Any]:
        params: Dict[str, Any] = {"limit": limit}
        if run_id:
            params["run_id"] = run_id
        if artifact_type:
            params["type"] = artifact_type
        return self._request("GET", "/artifacts", params=params)

    def download_artifact_blob(self, artifact_id: str, dest: Path) -> Path:
        path = f"/artifacts/{artifact_id}/blob"
        headers = self.auth.headers("GET", path, body=b"")
        resp = requests.get(f"{self.base_url}{path}", headers=headers, timeout=self.timeout)
        resp.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.content)
        return dest


class SubmissionLock:
    def __init__(self, lock_path: Path) -> None:
        self.lock_path = lock_path
        self.handle = None

    def __enter__(self):
        if fcntl is None:
            return self
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.lock_path.open("w+")
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError(f"Lock already held for {self.lock_path.name}") from exc
        return self

    def __exit__(self, exc_type, exc, tb):
        if fcntl is None:
            return False
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            self.handle.close()
        except Exception:
            pass
        return False


class PdfExtractor:
    def __init__(self, min_text_chars: int = 200, max_text_chars: int = 40000) -> None:
        self.min_text_chars = min_text_chars
        self.max_text_chars = max_text_chars

    def extract_text(self, pdf_path: Path) -> List[Dict[str, Any]]:
        pages: List[Dict[str, Any]] = []
        with pdfplumber.open(str(pdf_path)) as pdf:
            for idx, page in enumerate(pdf.pages, start=1):
                pages.append({"page": idx, "text": page.extract_text() or ""})
        return pages

    def should_use_vision(self, pages: List[Dict[str, Any]]) -> bool:
        if not pages:
            return True
        total_chars = sum(len(page["text"].strip()) for page in pages)
        empty_pages = sum(1 for page in pages if len(page["text"].strip()) < 20)
        return total_chars < self.min_text_chars or (empty_pages / max(len(pages), 1)) > 0.6

    def render_images(self, pdf_path: Path, out_dir: Path, max_pages: int = 6, zoom: float = 2.0) -> List[Path]:
        if fitz is None:
            raise RuntimeError("PyMuPDF is required for vision extraction but is not installed.")
        out_dir.mkdir(parents=True, exist_ok=True)
        doc = fitz.open(str(pdf_path))
        paths: List[Path] = []
        try:
            for index in range(min(len(doc), max_pages)):
                page = doc[index]
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
                path = out_dir / f"page_{index + 1:03d}.png"
                pix.save(str(path))
                paths.append(path)
        finally:
            doc.close()
        return paths


class LlmClient:
    def __init__(self, base_url: str, api_key: Optional[str], model: str, timeout: int = 120) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def chat(
        self,
        messages: List[Dict[str, Any]],
        response_format: Optional[Dict[str, Any]] = None,
        *,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.0 if temperature is None else temperature,
        }
        if response_format:
            payload["response_format"] = response_format
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        resp = requests.post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            data=json.dumps(payload),
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()
