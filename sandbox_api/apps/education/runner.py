from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Protocol, Tuple, Literal
from urllib.parse import urlencode

import pdfplumber
import requests
from pydantic import BaseModel, Field, ValidationError

try:
    import fitz  # PyMuPDF
except Exception:  # noqa: BLE001
    fitz = None

try:
    import fcntl
except Exception:  # noqa: BLE001
    fcntl = None


DEFAULT_SELECTORS = {
    "page_ready": [
        "[data-testid='speedgrader-feedback']",
        "#speedgrader",  # common container
        "#content",  # canvas main content
        "body",
    ],
    "assignment_title": [
        "#assignment_name",
        "#assignment_title",
        ".assignment-title",
        ".assignment-title__title",
        "h1",
    ],
    "student_name": [
        ".student-name",
        "#students_selectmenu-button",
        ".student_name",
    ],
    "grade_input": [
        "input[data-testid='grade-input']",
        "#grading-box-extended",
        "#grading-box",
        "input[name='grade']",
        "input[aria-label='Grade']",
        "input[placeholder*='Grade']",
    ],
    "rubric_button": [
        "[data-testid='rubric-button']",
        "[data-testid='rubric-assessment-button']",
        "button[aria-label*='Rubric']",
        "button:has-text('Rubric')",
        "a:has-text('Rubric')",
    ],
    "rubric_panel": [
        "[data-testid='rubric-assessment']",
        ".rubric-assessment",
        ".rubric_container",
        ".Rubric",
    ],
    "rubric_row": [
        "[data-testid='rubric-criterion']",
        ".rubric__criterion",
        ".rubric_criterion",
        "tr",
        "li",
    ],
    "rubric_points_input": [
        "input[data-testid='rubric-criterion-points']",
        "input[name*='points']",
        "input[aria-label*='points']",
        "input[type='number']",
        "input[type='text']",
    ],
    "rubric_comment_input": [
        "textarea[data-testid='rubric-criterion-comment']",
        "textarea[name*='comment']",
        "textarea[aria-label*='comment']",
        "textarea",
    ],
    "rubric_save": [
        "button[aria-label*='Save']",
        "button:has-text('Save')",
        "button:has-text('Update')",
        "button:has-text('Apply')",
    ],
    "comment_box": [
        "textarea[data-rich_text='true']",
        "[data-testid='assignment-comment-input'] textarea",
        "#submission_comment",
        "textarea[name='comment[text_comment]']",
        "textarea[aria-label='Comment']",
        "textarea[placeholder*='Comment']",
        "textarea",
    ],
    "comment_iframe": [
        "[data-testid='assignment-comment-input'] iframe",
        "iframe[id^='rce-']",
    ],
    "comment_submit": [
        "button[data-testid='submit-comment-button']",
        "#comment_submit_button",
    ],
    "save_indicator": [
        ".saving",  # appears during save
        ".saved",  # appears after save
        "#speedgrader",  # fallback: just wait on container
    ],
    "submission_attachments_container": [
        "[data-testid='submission-attachments-container']",
    ],
    "submission_attachments_list": [
        "[data-testid='submission-attachments']",
    ],
    "submission_attachment_link": [
        "button[data-testid^='submission-attachment-link-']",
        "[data-testid^='submission-attachment-link-']",
    ],
    "annotation_toolbar": [
        "nav.AnnotationControls",
        ".AnnotationControls",
        "#viewer-annotation-toolbar",
        "#annotation-tools",
        ".AnnotationToolbar",
        "[data-testid='annotation-toolbar']",
    ],
    "annotation_canvas": [
        ".Draw",
        ".PDFAnnotationLayer-container .annotationLayer",
        ".annotationLayer",
        "canvas.annotationLayer",
    ],
    "annotation_page": [
        ".Page-container:nth-of-type({page}) .Draw",
        ".Page-container:nth-of-type({page}) .annotationLayer",
        ".Page-container:nth-of-type({page})",
    ],
    "annotation_select": [
        "button[aria-label='Enter selection mode']",
        "button[title='Selection']",
    ],
    "annotation_point": [
        "button[aria-label='Point annotation']",
        "button[title='Point annotation']",
    ],
    "annotation_free_draw": [
        "button[title='Free draw annotation']",
        "button[aria-label*='Free draw']",
        "button[title*='Free draw']",
        "[data-tool='free_draw']",
        "[data-tool='freedraw']",
        "[data-crocodoc-tool='free_draw']",
    ],
    "annotation_strike": [
        "button[title='Strikeout annotation']",
        "button[aria-label*='Strike']",
        "button[title*='Strike']",
        "[data-tool='strikeout']",
        "[data-tool='strike']",
        "[data-crocodoc-tool='strikeout']",
    ],
    "annotation_highlight": [
        "button[title='Highlight annotation']",
        "button[aria-label*='Highlight']",
        "button[title*='Highlight']",
        "[data-tool='highlight']",
        "[data-tool='highlighter']",
        "[data-crocodoc-tool='highlight']",
    ],
    "annotation_freetext": [
        "button[title='Freetext annotation']",
        "button[aria-label*='Freetext']",
    ],
    "annotation_area": [
        "button[title='Area annotation']",
        "button[aria-label*='Area']",
    ],
    "annotation_color_button": [],
    "annotation_color_red": [
        "button.ColorButton[title='Red']",
        "button.ColorButton[aria-label*='Red']",
        "button[aria-label*='Red']",
        "button[title*='Red']",
    ],
    "annotation_color_orange": [
        "button.ColorButton[title='Orange']",
        "button.ColorButton[aria-label*='Orange']",
        "button[aria-label*='Orange']",
        "button[title*='Orange']",
    ],
    "annotation_color_yellow": [
        "button.ColorButton[title='Yellow']",
        "button.ColorButton[aria-label*='Yellow']",
        "button[aria-label*='Yellow']",
        "button[title*='Yellow']",
    ],
    "annotation_color_brown": [
        "button.ColorButton[title='Brown']",
        "button.ColorButton[aria-label*='Brown']",
        "button[aria-label*='Brown']",
        "button[title*='Brown']",
    ],
    "annotation_color_green": [
        "button.ColorButton[title='Green']",
        "button.ColorButton[aria-label*='Green']",
        "button[aria-label*='Green']",
        "button[title*='Green']",
    ],
    "annotation_color_dark_blue": [
        "button.ColorButton[title='Dark Blue']",
        "button.ColorButton[aria-label*='Dark Blue']",
        "button[aria-label*='Dark Blue']",
        "button[title*='Dark Blue']",
    ],
    "annotation_color_blue": [
        "button.ColorButton[title='Blue']",
        "button.ColorButton[aria-label*='Blue']",
        "button[aria-label*='Blue']",
        "button[title*='Blue']",
    ],
    "annotation_color_pink": [
        "button.ColorButton[title='Pink']",
        "button.ColorButton[aria-label*='Pink']",
        "button[aria-label*='Pink']",
        "button[title*='Pink']",
    ],
    "annotation_color_purple": [
        "button.ColorButton[title='Purple']",
        "button.ColorButton[aria-label*='Purple']",
        "button[aria-label*='Purple']",
        "button[title*='Purple']",
    ],
    "annotation_color_black": [
        "button.ColorButton[title='Dark Gray']",
        "button.ColorButton[aria-label*='Dark Gray']",
        "button.ColorButton[title='Black']",
        "button.ColorButton[aria-label*='Black']",
        "button[aria-label*='Black']",
        "button[title*='Black']",
    ],
}


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
    grading_llm_model: str = "accounts/fireworks/models/llama-v3p1-70b-instruct"
    annotation_llm_base: Optional[str] = None
    annotation_llm_key: Optional[str] = None
    annotation_llm_model: Optional[str] = None
    navigation_mode: str = "course"
    strict_ui_checks: bool = True


@dataclass
class GradeStudentOutcome:
    run_dir: Path
    grade_result_path: Path
    speedgrader_state_path: Path


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
    evidence: List[GradeEvidence]


class GradeResult(BaseModel):
    total_points: float = Field(..., ge=0)
    criteria: List[GradeCriterionResult]
    overall_feedback: str = Field(..., min_length=1)


class AnnotationPoint(BaseModel):
    x: float = Field(..., ge=0.0, le=1.0)
    y: float = Field(..., ge=0.0, le=1.0)


class AnnotationPlan(BaseModel):
    page: int = Field(..., ge=1)
    tool: Literal["free_draw", "strike", "highlight", "area", "point", "freetext", "select"]
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
            params={
                "include[]": ["submission_history", "rubric", "assignment", "user"],
            },
        )
        attachments = self._extract_attachments(data)
        return CanvasSubmission(
            submission_id=data.get("id"),
            attempt=data.get("attempt"),
            attachments=attachments,
            raw=data,
        )

    def _extract_attachments(self, payload: Dict[str, Any]) -> List[AttachmentInfo]:
        attachments = payload.get("attachments") or []
        if not attachments and payload.get("submission_history"):
            history = payload.get("submission_history") or []
            if history:
                attachments = history[-1].get("attachments") or []
        results = []
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
        path_with_query = f"?{query}" if query else ""
        return query, path_with_query

    def _request(self, method: str, path: str, *, params: Optional[Dict[str, Any]] = None, payload: Any = None) -> Any:
        body = b""
        content_type = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
            content_type = "application/json"
        query, path_query = self._encode_params(params)
        full_path = f"{path}{path_query}"
        url = f"{self.base_url}{full_path}"
        headers = self.auth.headers(method, full_path, body=body, content_type=content_type)
        resp = requests.request(method, url, headers=headers, data=body if payload is not None else None, timeout=self.timeout)
        resp.raise_for_status()
        if resp.content:
            return resp.json()
        return None

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
        url = f"{self.base_url}{path}"
        resp = requests.get(url, headers=headers, timeout=self.timeout)
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
                text = page.extract_text() or ""
                pages.append({"page": idx, "text": text})
        return pages

    def should_use_vision(self, pages: List[Dict[str, Any]]) -> bool:
        if not pages:
            return True
        total_chars = sum(len(p["text"].strip()) for p in pages)
        empty_pages = sum(1 for p in pages if len(p["text"].strip()) < 20)
        empty_ratio = empty_pages / max(len(pages), 1)
        if total_chars < self.min_text_chars:
            return True
        return empty_ratio > 0.6

    def render_images(self, pdf_path: Path, out_dir: Path, max_pages: int = 6, zoom: float = 2.0) -> List[Path]:
        if fitz is None:
            raise RuntimeError("PyMuPDF is required for vision extraction but is not installed.")
        out_dir.mkdir(parents=True, exist_ok=True)
        doc = fitz.open(str(pdf_path))
        paths: List[Path] = []
        try:
            for i in range(min(len(doc), max_pages)):
                page = doc[i]
                matrix = fitz.Matrix(zoom, zoom)
                pix = page.get_pixmap(matrix=matrix)
                path = out_dir / f"page_{i+1:03d}.png"
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
        url = f"{self.base_url}/chat/completions"
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
        resp = requests.post(url, headers=headers, data=json.dumps(payload), timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()


def _response_text(raw_response: Dict[str, Any], *, label: str) -> str:
    # OpenAI-compatible providers can return either a plain string or structured
    # content blocks; normalize both into a single text value for downstream parsers.
    try:
        content = raw_response["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"{label} missing content.") from exc
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if text:
                parts.append(str(text))
        if parts:
            return "\n".join(parts)
    raise RuntimeError(f"{label} returned unsupported content format.")


def strip_html(value: Optional[str]) -> str:
    if not value:
        return ""
    text = re.sub(r"<[^>]+>", " ", value)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def safe_filename(name: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in name)
    return safe.strip("_") or "file"


def select_pdf(attachments: List[AttachmentInfo]) -> AttachmentInfo:
    if not attachments:
        raise RuntimeError("No attachments found in submission.")
    pdfs = [a for a in attachments if a.content_type == "application/pdf" or a.filename.lower().endswith(".pdf")]
    if not pdfs:
        raise RuntimeError("No PDF attachments found in submission.")
    return pdfs[0]


def build_text_payload(pages: List[Dict[str, Any]], max_chars: int) -> str:
    parts: List[str] = []
    count = 0
    for page in pages:
        text = page["text"].strip()
        if not text:
            continue
        block = f"[Page {page['page']}]\n{text}\n"
        if count + len(block) > max_chars:
            break
        parts.append(block)
        count += len(block)
    return "\n".join(parts)


def build_assignment_context(
    *,
    assignment_instructions: str,
    attachments: List[AttachmentInfo],
    canvas: CanvasClient,
    extractor: PdfExtractor,
    out_dir: Path,
    max_chars: int,
    vision_max_pages: int,
) -> Tuple[str, List[str]]:
    parts: List[str] = []
    sources: List[str] = []

    if assignment_instructions:
        parts.append(f"[Assignment Description]\n{assignment_instructions}")
        sources.append("description")

    pdfs = [a for a in attachments if a.content_type == "application/pdf" or a.filename.lower().endswith(".pdf")]
    if pdfs:
        out_dir.mkdir(parents=True, exist_ok=True)
        remaining = max_chars - len("\n\n".join(parts))
        vision_key = os.getenv("FIREWORKS_API_KEY")
        vision_base = os.getenv("FIREWORKS_API_BASE", "https://api.fireworks.ai/inference/v1")
        vision_model = os.getenv(
            "FIREWORKS_VISION_MODEL",
            "accounts/fireworks/models/qwen2p5-vl-32b-instruct",
        )
        for idx, attachment in enumerate(pdfs, start=1):
            if remaining <= 200:
                break
            filename = safe_filename(attachment.filename)
            dest = out_dir / f"{idx:02d}_{filename}"
            canvas.download_attachment(attachment, dest)
            block = ""
            if vision_key and fitz is not None:
                try:
                    images = extractor.render_images(
                        dest,
                        out_dir / f"{idx:02d}_{filename}_vision",
                        max_pages=vision_max_pages,
                        zoom=1.5,
                    )
                    images = select_images_for_vision(images, max_total_base64=9_000_000)
                    questions = extract_questions_with_fireworks(
                        images=images,
                        model=vision_model,
                        api_key=vision_key,
                        base_url=vision_base,
                    )
                    if questions:
                        questions_text = "\n".join(f"- {q}" for q in questions)
                        block = (
                            f"[Assignment Attachment Questions: {attachment.filename}]\n{questions_text}"
                        )
                except Exception:
                    block = ""

            if not block:
                pages = extractor.extract_text(dest)
                per_pdf_limit = min(8000, max(0, remaining))
                text = build_text_payload(pages, per_pdf_limit)
                if not text:
                    text = "(No extractable text found.)"
                block = f"[Assignment Attachment: {attachment.filename}]\n{text}"

            if len(block) > remaining:
                block = block[:remaining]
            parts.append(block)
            sources.append(attachment.filename)
            remaining = max_chars - len("\n\n".join(parts))

    if not parts:
        return "(No assignment context available.)", sources
    return "\n\n".join(parts), sources


def encode_image(path: Path) -> str:
    data = path.read_bytes()
    return base64.b64encode(data).decode("ascii")


def estimate_base64_size(path: Path) -> int:
    size = path.stat().st_size
    return ((size + 2) // 3) * 4


def select_images_for_vision(images: List[Path], max_total_base64: int) -> List[Path]:
    selected: List[Path] = []
    total = 0
    for path in images:
        b64_size = estimate_base64_size(path)
        if total + b64_size > max_total_base64:
            break
        selected.append(path)
        total += b64_size
    return selected


def parse_questions_from_content(content: str) -> List[str]:
    content = content.strip()
    if not content:
        return []
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        lines = []
        for raw in content.splitlines():
            line = raw.strip()
            if not line:
                continue
            line = line.lstrip("-•").strip()
            if line:
                lines.append(line)
        return lines

    if isinstance(data, dict):
        questions = data.get("questions") or data.get("items") or data.get("prompts")
        if isinstance(questions, list):
            return [str(q).strip() for q in questions if str(q).strip()]
        if isinstance(questions, str):
            return [questions.strip()] if questions.strip() else []
        return []
    if isinstance(data, list):
        return [str(q).strip() for q in data if str(q).strip()]
    if isinstance(data, str):
        return [data.strip()] if data.strip() else []
    return []


def parse_extracted_pages_from_content(content: str) -> List[Dict[str, Any]]:
    content = content.strip()
    if not content:
        return []
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return [{"page": 1, "text": content}]

    if isinstance(data, dict):
        items = data.get("pages") or data.get("items") or data.get("content") or []
    elif isinstance(data, list):
        items = data
    else:
        items = []

    parsed: List[Dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        if isinstance(item, str):
            text = item.strip()
            if text:
                parsed.append({"page": index, "text": text})
            continue
        if not isinstance(item, dict):
            continue
        raw_page = item.get("page") or item.get("page_number") or index
        raw_text = item.get("text") or item.get("content") or item.get("ocr_text") or ""
        text = str(raw_text).strip()
        if not text:
            continue
        try:
            page_num = int(raw_page)
        except (TypeError, ValueError):
            page_num = index
        parsed.append({"page": max(page_num, 1), "text": text})
    return parsed


def extract_submission_text_with_llm(
    *,
    images: List[Path],
    client: LlmClient,
) -> List[Dict[str, Any]]:
    # This stage is intentionally extraction-only: it converts page images into
    # OCR-like text so the grading model can score from text instead of vision.
    if not images:
        return []
    prompt = (
        "Transcribe the student submission pages shown in these images. "
        "Return JSON only with key 'pages' as an array of objects. "
        "Each object must contain 'page' (1-based integer) and 'text' (string). "
        "Do not grade, summarize, or interpret the work."
    )
    content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
    for image_path in images:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{encode_image(image_path)}"},
            }
        )
    messages = [
        {"role": "system", "content": "Return only JSON."},
        {"role": "user", "content": content},
    ]
    raw = client.chat(messages, max_tokens=max(1800, len(images) * 700), temperature=0.0)
    return parse_extracted_pages_from_content(_response_text(raw, label="Extraction LLM response"))


def extract_questions_with_fireworks(
    *,
    images: List[Path],
    model: str,
    api_key: str,
    base_url: str,
    timeout: int = 120,
) -> List[str]:
    if not images:
        return []
    client = LlmClient(base_url, api_key, model, timeout=timeout)
    prompt = (
        "Extract the assignment questions and prompts from these pages. "
        "Return JSON with key 'questions' as an array of strings. "
        "If no questions are found, return {\"questions\": []}."
    )
    content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
    for image_path in images:
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{encode_image(image_path)}",
                },
            }
        )
    messages = [
        {"role": "system", "content": "Return only JSON."},
        {"role": "user", "content": content},
    ]
    raw = client.chat(messages, max_tokens=1200, temperature=0.0)
    text = _response_text(raw, label="Vision LLM response")
    return parse_questions_from_content(text)


def build_annotation_prompt(
    *,
    assignment_title: str,
    assignment_context: str,
    rubric: List[Dict[str, Any]],
    grade: GradeResult,
    max_annotations: int,
) -> str:
    rubric_lines = []
    for criterion in rubric:
        rubric_lines.append(
            f"- id={criterion.get('id') or criterion.get('criterion_id') or criterion.get('name')}: "
            f"{criterion.get('description') or criterion.get('long_description') or criterion.get('description', '')} "
            f"(points={criterion.get('points') or criterion.get('max_score')})"
        )
    rubric_block = "\n".join(rubric_lines) if rubric_lines else "(No rubric provided)"

    criteria_lines = []
    for item in grade.criteria:
        evidence = "; ".join([f"p{e.page}: {e.quote}" for e in item.evidence]) if item.evidence else ""
        criteria_lines.append(
            f"- id={item.id} points={item.points} comment={item.comment} evidence={evidence}"
        )
    criteria_block = "\n".join(criteria_lines) if criteria_lines else "(No criteria provided)"

    return (
        "You are annotating a student submission in Canvas SpeedGrader. "
        "Use the question context and rubric to decide where to mark up the submission. "
        "Return JSON with key 'annotations' as a list. Each annotation must include: "
        "page (1-based), tool (one of 'select', 'point', 'freetext', 'highlight', 'strike', 'free_draw', 'area'), "
        "color (red/orange/yellow/green/blue/dark blue/pink/purple/brown/black), and coordinates. "
        "All coordinates are normalized 0-1 for the page image. "
        "For tools that draw (highlight/strike/free_draw), include path (list of {x,y} points). "
        "For area, include path with exactly two points (top-left and bottom-right). "
        "For point, include point {x,y}. "
        "For freetext, include point {x,y} and text. "
        "For select, omit coordinates. "
        f"Limit to at most {max_annotations} annotations and only use pages shown in the images.\n\n"
        f"Assignment title: {assignment_title}\n"
        f"Assignment question context:\n{assignment_context}\n\n"
        f"Rubric:\n{rubric_block}\n\n"
        f"Grading result:\n{criteria_block}\n"
    )


def parse_annotation_plan(raw_response: Dict[str, Any]) -> List[AnnotationPlan]:
    content = _response_text(raw_response, label="Annotation response")
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return []
    items: List[Any] = []
    if isinstance(data, dict):
        items = data.get("annotations") or []
    elif isinstance(data, list):
        items = data
    parsed: List[AnnotationPlan] = []
    color_map = {
        "dark gray": "black",
        "dark grey": "black",
        "gray": "black",
        "grey": "black",
        "black": "black",
        "red": "red",
        "orange": "orange",
        "yellow": "yellow",
        "green": "green",
        "dark blue": "dark_blue",
        "darkblue": "dark_blue",
        "blue": "blue",
        "pink": "pink",
        "purple": "purple",
        "brown": "brown",
    }
    for item in items:
        try:
            plan = AnnotationPlan.parse_obj(item)
        except ValidationError:
            continue
        clamped_path: List[AnnotationPoint] = []
        for point in plan.path or []:
            clamped_path.append(
                AnnotationPoint(
                    x=min(max(point.x, 0.0), 1.0),
                    y=min(max(point.y, 0.0), 1.0),
                )
            )
        clamped_point = None
        if plan.point is not None:
            clamped_point = AnnotationPoint(
                x=min(max(plan.point.x, 0.0), 1.0),
                y=min(max(plan.point.y, 0.0), 1.0),
            )
        if clamped_point is None and clamped_path:
            clamped_point = clamped_path[0]

        color = (plan.color or "").strip().lower()
        color = color_map.get(color, color) or None
        text = (plan.text or "").strip() or None

        if plan.tool in {"free_draw", "strike", "highlight"} and len(clamped_path) < 2:
            continue
        if plan.tool == "area" and len(clamped_path) < 2:
            continue
        if plan.tool == "point" and clamped_point is None:
            continue
        if plan.tool == "freetext" and (clamped_point is None or not text):
            continue

        parsed.append(
            AnnotationPlan(
                page=plan.page,
                tool=plan.tool,
                color=color,
                path=clamped_path,
                point=clamped_point,
                text=text,
            )
        )
    return parsed

def build_grade_prompt(
    assignment_title: str,
    assignment_instructions: str,
    assignment_context: str,
    rubric: List[Dict[str, Any]],
    policy: str,
    extracted_text: str,
    evidence_mode: str,
) -> str:
    rubric_lines = []
    for criterion in rubric:
        rubric_lines.append(
            f"- id={criterion.get('id') or criterion.get('criterion_id') or criterion.get('name')}: "
            f"{criterion.get('description') or criterion.get('long_description') or criterion.get('description', '')} "
            f"(points={criterion.get('points') or criterion.get('max_score')})"
        )
    rubric_block = "\n".join(rubric_lines) if rubric_lines else "(No rubric provided)"

    return (
        "You are an exacting grader. Grade the student submission using the rubric, "
        "assignment instructions, and question context (including any assignment PDFs). "
        "Return strict JSON with keys: total_points, criteria, overall_feedback. "
        "Each criteria item must include id, points, comment, evidence. Evidence must include page and quote. "
        f"Evidence mode: {evidence_mode}.\n\n"
        f"Assignment title: {assignment_title}\n"
        f"Assignment instructions: {assignment_instructions}\n\n"
        "Assignment question context (use this to interpret the rubric and expected answers):\n"
        f"{assignment_context}\n\n"
        f"Rubric criteria (interpret using the question context above):\n{rubric_block}\n\n"
        f"Policy: {policy}\n\n"
        f"Submission content:\n{extracted_text}\n"
    )


def parse_grade_result(raw_response: Dict[str, Any]) -> GradeResult:
    content = _response_text(raw_response, label="LLM response")
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise RuntimeError("LLM response is not valid JSON.") from exc
    try:
        return GradeResult.parse_obj(data)
    except ValidationError as exc:
        raise RuntimeError(f"LLM response failed validation: {exc}") from exc


def build_annotation_steps(
    annotations: List[AnnotationPlan],
    selectors: Dict[str, List[str]],
) -> List[Dict[str, Any]]:
    if not annotations:
        return []

    def selector_fallbacks(values: Iterable[str]) -> Tuple[str, List[str]]:
        items = [v for v in values if v]
        primary = items[0] if items else "body"
        fallbacks = items[1:] if len(items) > 1 else []
        return primary, fallbacks

    steps: List[Dict[str, Any]] = []
    toolbar_sel, toolbar_fallbacks = selector_fallbacks(selectors.get("annotation_toolbar", []))
    if toolbar_sel and toolbar_sel != "body":
        steps.append({"action": "wait_for_selector", "selector": toolbar_sel, "selector_fallbacks": toolbar_fallbacks})

    canvas_sel, canvas_fallbacks = selector_fallbacks(selectors.get("annotation_canvas", []))
    page_templates = selectors.get("annotation_page", [])

    for annotation in annotations:
        tool_key = None
        if annotation.tool == "free_draw":
            tool_key = "annotation_free_draw"
        elif annotation.tool == "highlight":
            tool_key = "annotation_highlight"
        elif annotation.tool == "strike":
            tool_key = "annotation_strike"
        elif annotation.tool == "point":
            tool_key = "annotation_point"
        elif annotation.tool == "freetext":
            tool_key = "annotation_freetext"
        elif annotation.tool == "area":
            tool_key = "annotation_area"
        elif annotation.tool == "select":
            tool_key = "annotation_select"

        if tool_key and selectors.get(tool_key):
            tool_sel, tool_fallbacks = selector_fallbacks(selectors.get(tool_key, []))
            if tool_sel and tool_sel != "body":
                steps.append({"action": "click", "selector": tool_sel, "selector_fallbacks": tool_fallbacks})

        color = (annotation.color or "").lower()
        if annotation.tool == "highlight" and not color:
            color = "yellow"
        if color and annotation.tool != "select":
            if selectors.get("annotation_color_button"):
                color_button_sel, color_button_fallbacks = selector_fallbacks(
                    selectors.get("annotation_color_button", [])
                )
                if color_button_sel and color_button_sel != "body":
                    steps.append(
                        {
                            "action": "click",
                            "selector": color_button_sel,
                            "selector_fallbacks": color_button_fallbacks,
                        }
                    )
            color_key = f"annotation_color_{color}"
            if selectors.get(color_key):
                color_sel, color_fallbacks = selector_fallbacks(selectors.get(color_key, []))
                if color_sel and color_sel != "body":
                    steps.append({"action": "click", "selector": color_sel, "selector_fallbacks": color_fallbacks})

        target_sel = canvas_sel
        target_fallbacks = canvas_fallbacks
        if page_templates and annotation.page:
            formatted = [tpl.format(page=annotation.page) for tpl in page_templates if tpl]
            if formatted:
                target_sel = formatted[0]
                target_fallbacks = formatted[1:] + [canvas_sel] + canvas_fallbacks

        if annotation.tool in {"free_draw", "strike", "highlight"}:
            steps.append(
                {
                    "action": "draw_path",
                    "selector": target_sel,
                    "selector_fallbacks": target_fallbacks,
                    "points": [{"x": p.x, "y": p.y} for p in annotation.path],
                }
            )
            steps.append({"action": "wait", "wait_ms": 200})
        elif annotation.tool == "area":
            steps.append(
                {
                    "action": "draw_rect",
                    "selector": target_sel,
                    "selector_fallbacks": target_fallbacks,
                    "points": [{"x": p.x, "y": p.y} for p in annotation.path[:2]],
                }
            )
            steps.append({"action": "wait", "wait_ms": 200})
        elif annotation.tool == "point":
            if annotation.point is not None:
                steps.append(
                    {
                        "action": "point",
                        "selector": target_sel,
                        "selector_fallbacks": target_fallbacks,
                        "point": {"x": annotation.point.x, "y": annotation.point.y},
                    }
                )
                steps.append({"action": "wait", "wait_ms": 150})
        elif annotation.tool == "freetext":
            if annotation.point is not None and annotation.text:
                steps.append(
                    {
                        "action": "freetext",
                        "selector": target_sel,
                        "selector_fallbacks": target_fallbacks,
                        "point": {"x": annotation.point.x, "y": annotation.point.y},
                        "text": annotation.text,
                    }
                )
                steps.append({"action": "wait", "wait_ms": 200})

    return steps


def _format_points(points: float) -> str:
    if float(points).is_integer():
        return str(int(points))
    return f"{points:.2f}".rstrip("0").rstrip(".")


def _normalize_rubric_text(value: str) -> str:
    cleaned = " ".join(value.split())
    if len(cleaned) > 80:
        return f"{cleaned[:77]}..."
    return cleaned


def _escape_has_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _rubric_lookup(rubric: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    lookup: Dict[str, Dict[str, Any]] = {}
    for item in rubric:
        key = item.get("id") or item.get("criterion_id") or item.get("name")
        if key:
            lookup[str(key)] = item
    return lookup


def _rubric_label(item: Optional[Dict[str, Any]], fallback_id: str) -> str:
    if item:
        label = item.get("description") or item.get("long_description") or item.get("name")
        if label:
            return _normalize_rubric_text(str(label))
    return _normalize_rubric_text(fallback_id)


def _build_scoped_selectors(rows: List[str], inputs: List[str]) -> List[str]:
    selectors: List[str] = []
    for row in rows:
        for input_sel in inputs:
            selectors.append(f"{row} {input_sel}")
    return selectors


def build_rubric_steps(
    grade: GradeResult,
    rubric: List[Dict[str, Any]],
    selectors: Dict[str, List[str]],
) -> List[Dict[str, Any]]:
    def selector_fallbacks(values: Iterable[str]) -> Tuple[str, List[str]]:
        items = [v for v in values if v]
        primary = items[0] if items else "body"
        fallbacks = items[1:] if len(items) > 1 else []
        return primary, fallbacks

    rubric_button_sel, rubric_button_fallbacks = selector_fallbacks(selectors.get("rubric_button", []))
    if not rubric_button_sel or rubric_button_sel == "body":
        return []

    steps: List[Dict[str, Any]] = [
        {"action": "click", "selector": rubric_button_sel, "selector_fallbacks": rubric_button_fallbacks},
    ]

    rubric_panel_sel, rubric_panel_fallbacks = selector_fallbacks(selectors.get("rubric_panel", []))
    if rubric_panel_sel and rubric_panel_sel != "body":
        steps.append(
            {"action": "wait_for_selector", "selector": rubric_panel_sel, "selector_fallbacks": rubric_panel_fallbacks}
        )

    row_bases = selectors.get("rubric_row", []) or []
    points_inputs = selectors.get("rubric_points_input", []) or []
    comment_inputs = selectors.get("rubric_comment_input", []) or []
    rubric_map = _rubric_lookup(rubric)

    for criterion in grade.criteria:
        rubric_item = rubric_map.get(str(criterion.id))
        label = _escape_has_text(_rubric_label(rubric_item, str(criterion.id)))
        rows = [f"{base}:has-text(\"{label}\")" for base in row_bases if base and base != "body"]
        if not rows:
            rows = [f"*:has-text(\"{label}\")"]

        points_selectors = _build_scoped_selectors(rows, points_inputs)
        if points_selectors:
            steps.append(
                {
                    "action": "type",
                    "selector": points_selectors[0],
                    "selector_fallbacks": points_selectors[1:],
                    "text": _format_points(criterion.points),
                }
            )

        comment_text = (criterion.comment or "").strip()
        if comment_text:
            comment_selectors = _build_scoped_selectors(rows, comment_inputs)
            if comment_selectors:
                steps.append(
                    {
                        "action": "type",
                        "selector": comment_selectors[0],
                        "selector_fallbacks": comment_selectors[1:],
                        "text": comment_text,
                    }
                )

    rubric_save_sel, rubric_save_fallbacks = selector_fallbacks(selectors.get("rubric_save", []))
    if rubric_save_sel and rubric_save_sel != "body":
        steps.append({"action": "click", "selector": rubric_save_sel, "selector_fallbacks": rubric_save_fallbacks})

    return steps


def build_speedgrader_steps(
    url: str,
    grade: GradeResult,
    selectors: Dict[str, List[str]],
    annotations: Optional[List[AnnotationPlan]] = None,
    load_submission: bool = False,
    rubric: Optional[List[Dict[str, Any]]] = None,
    apply_rubric: bool = True,
) -> Dict[str, Any]:
    def selector_fallbacks(values: Iterable[str]) -> Tuple[str, List[str]]:
        items = [v for v in values if v]
        primary = items[0] if items else "body"
        fallbacks = items[1:] if len(items) > 1 else []
        return primary, fallbacks

    page_ready_sel, page_ready_fallbacks = selector_fallbacks(selectors.get("page_ready", []))
    grade_sel, grade_fallbacks = selector_fallbacks(selectors.get("grade_input", []))
    comment_sel, comment_fallbacks = selector_fallbacks(selectors.get("comment_box", []))
    comment_iframe_sel, comment_iframe_fallbacks = selector_fallbacks(selectors.get("comment_iframe", []))
    comment_submit_sel, comment_submit_fallbacks = selector_fallbacks(selectors.get("comment_submit", []))
    save_sel, save_fallbacks = selector_fallbacks(selectors.get("save_indicator", []))
    attachments_container_sel, attachments_container_fallbacks = selector_fallbacks(
        selectors.get("submission_attachments_container", [])
    )
    attachments_list_sel, attachments_list_fallbacks = selector_fallbacks(
        selectors.get("submission_attachments_list", [])
    )
    attachment_link_sel, attachment_link_fallbacks = selector_fallbacks(
        selectors.get("submission_attachment_link", [])
    )
    annotation_canvas_sel, annotation_canvas_fallbacks = selector_fallbacks(selectors.get("annotation_canvas", []))

    steps: List[Dict[str, Any]] = [
        {"action": "goto", "url": url},
        {"action": "wait_for_selector", "selector": page_ready_sel, "selector_fallbacks": page_ready_fallbacks},
    ]

    if load_submission:
        if attachments_container_sel and attachments_container_sel != "body":
            steps.append(
                {
                    "action": "wait_for_selector",
                    "selector": attachments_container_sel,
                    "selector_fallbacks": attachments_container_fallbacks,
                }
            )
        if attachments_list_sel and attachments_list_sel != "body":
            steps.append(
                {
                    "action": "wait_for_selector",
                    "selector": attachments_list_sel,
                    "selector_fallbacks": attachments_list_fallbacks,
                }
            )
        if attachment_link_sel and attachment_link_sel != "body":
            steps.append(
                {
                    "action": "click",
                    "selector": attachment_link_sel,
                    "selector_fallbacks": attachment_link_fallbacks,
                }
            )
            if annotation_canvas_sel and annotation_canvas_sel != "body":
                steps.append(
                    {
                        "action": "wait_for_selector",
                        "selector": annotation_canvas_sel,
                        "selector_fallbacks": annotation_canvas_fallbacks,
                    }
                )

    annotation_steps = build_annotation_steps(annotations or [], selectors)
    steps.extend(annotation_steps)

    if apply_rubric and rubric:
        steps.extend(build_rubric_steps(grade, rubric, selectors))

    steps.append(
        {
            "action": "type",
            "selector": grade_sel,
            "selector_fallbacks": grade_fallbacks,
            "text": str(grade.total_points),
        }
    )

    if comment_iframe_sel and comment_iframe_sel != "body":
        steps.append(
            {
                "action": "type_rce",
                "selector": comment_iframe_sel,
                "selector_fallbacks": comment_iframe_fallbacks,
                "text": grade.overall_feedback,
            }
        )
    else:
        steps.append(
            {
                "action": "type",
                "selector": comment_sel,
                "selector_fallbacks": comment_fallbacks,
                "text": grade.overall_feedback,
            }
        )

    if comment_submit_sel and comment_submit_sel != "body":
        steps.append(
            {
                "action": "click",
                "selector": comment_submit_sel,
                "selector_fallbacks": comment_submit_fallbacks,
            }
        )

    steps.extend(
        [
            {"action": "wait_for_selector", "selector": save_sel, "selector_fallbacks": save_fallbacks},
            {"action": "wait", "wait_ms": 1500},
            {"action": "page_state"},
        ]
    )

    return {
        "steps": steps,
        "screenshot_every_step": False,
    }


def build_open_steps(url: str, selectors: Dict[str, List[str]]) -> Dict[str, Any]:
    def selector_fallbacks(values: Iterable[str]) -> Tuple[str, List[str]]:
        items = [v for v in values if v]
        primary = items[0] if items else "body"
        fallbacks = items[1:] if len(items) > 1 else []
        return primary, fallbacks

    page_ready_sel, page_ready_fallbacks = selector_fallbacks(selectors.get("page_ready", []))
    return {
        "steps": [
            {"action": "goto", "url": url},
            {"action": "wait_for_selector", "selector": page_ready_sel, "selector_fallbacks": page_ready_fallbacks},
            {"action": "page_state"},
        ],
        "screenshot_every_step": False,
    }


def build_refresh_steps(url: str, selectors: Dict[str, List[str]]) -> Dict[str, Any]:
    def selector_fallbacks(values: Iterable[str]) -> Tuple[str, List[str]]:
        items = [v for v in values if v]
        primary = items[0] if items else "body"
        fallbacks = items[1:] if len(items) > 1 else []
        return primary, fallbacks

    page_ready_sel, page_ready_fallbacks = selector_fallbacks(selectors.get("page_ready", []))
    grade_sel, grade_fallbacks = selector_fallbacks(selectors.get("grade_input", []))
    return {
        "steps": [
            {"action": "goto", "url": url},
            {"action": "wait_for_selector", "selector": page_ready_sel, "selector_fallbacks": page_ready_fallbacks},
            {"action": "page_state"},
            {
                "action": "dom_snapshot",
                "selector": grade_sel,
                "selector_fallbacks": grade_fallbacks,
                "format": "html",
            },
        ],
        "screenshot_every_step": False,
    }


def wait_for_artifact(client: SandboxOps, run_id: str, artifact_type: str, timeout: int = 60) -> Dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = client.list_artifacts(run_id=run_id, artifact_type=artifact_type)
        items = payload.get("items") or []
        if items:
            return items[-1]
        time.sleep(1)
    raise TimeoutError(f"Timed out waiting for artifact type={artifact_type} run_id={run_id}.")


def load_page_state(client: SandboxOps, artifact: Dict[str, Any], dest_dir: Path) -> Dict[str, Any]:
    artifact_id = artifact["artifact_id"]
    dest = dest_dir / f"{artifact_id}.json"
    client.download_artifact_blob(artifact_id, dest)
    return json.loads(dest.read_text(encoding="utf-8"))


def assert_page_contains(value: str, expected: str, label: str, *, strict: bool = True) -> bool:
    if expected.lower() not in value.lower():
        if strict:
            raise RuntimeError(f"Page verification failed for {label}: expected '{expected}'.")
        return False
    return True


def parse_grade_value_from_html(html: str) -> Optional[str]:
    match = re.search(r"value=[\"']([^\"']+)[\"']", html, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def build_lock_key(course_id: str, assignment_id: str, student_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", f"{course_id}_{assignment_id}_{student_id}")


def run_grade_student(args: GradeStudentArgs, sandbox_ops: Optional[SandboxOps] = None) -> GradeStudentOutcome:
    if not args.canvas_token:
        raise RuntimeError("Canvas token is required for grading.")

    lock_key = build_lock_key(args.course_id, args.assignment_id, args.student_id)
    lock_path = Path(args.output_dir) / "locks" / f"{lock_key}.lock"
    with SubmissionLock(lock_path):
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_dir = Path(args.output_dir) / f"{lock_key}_{run_id}"
        run_dir.mkdir(parents=True, exist_ok=True)

        canvas = CanvasClient(args.canvas_base, args.canvas_token)
        course = canvas.get_course(args.course_id)
        assignment = canvas.get_assignment(args.course_id, args.assignment_id)
        student = canvas.get_user(args.student_id, args.course_id)
        submission = canvas.get_submission(args.course_id, args.assignment_id, args.student_id)

        course_name = (
            course.get("name")
            or course.get("course_code")
            or course.get("sis_course_id")
            or args.course_id
        )
        assignment_title = assignment.get("name") or "Assignment"
        assignment_instructions = strip_html(assignment.get("description"))
        rubric = assignment.get("rubric") or []
        student_name = student.get("name") or student.get("short_name") or args.student_id
        assignment_attachments = canvas.get_assignment_attachments(assignment)

        pdf_attachment = select_pdf(submission.attachments)
        pdf_path = run_dir / pdf_attachment.filename
        canvas.download_attachment(pdf_attachment, pdf_path)

        extractor = PdfExtractor(min_text_chars=args.min_text_chars, max_text_chars=args.text_max_chars)
        text_pages = extractor.extract_text(pdf_path)
        use_vision = extractor.should_use_vision(text_pages)
        evidence_mode = "vision" if use_vision else "text"

        extracted_text = build_text_payload(text_pages, args.text_max_chars)
        images: List[Path] = []
        if use_vision:
            images = extractor.render_images(pdf_path, run_dir / "vision", max_pages=args.vision_max_pages)
            if args.extraction_llm_model:
                try:
                    # If a dedicated extraction model is configured, transcribe the
                    # submission first and only send the resulting text to the grader.
                    extraction_llm = LlmClient(
                        args.extraction_llm_base or args.grading_llm_base,
                        args.extraction_llm_key,
                        args.extraction_llm_model,
                    )
                    extracted_pages = extract_submission_text_with_llm(images=images, client=extraction_llm)
                    extracted_text = build_text_payload(extracted_pages, args.text_max_chars)
                    if extracted_text:
                        evidence_mode = "vision_text"
                except Exception:
                    extracted_text = extracted_text or ""

        assignment_context, assignment_context_sources = build_assignment_context(
            assignment_instructions=assignment_instructions,
            attachments=assignment_attachments,
            canvas=canvas,
            extractor=extractor,
            out_dir=run_dir / "assignment",
            max_chars=min(args.text_max_chars, 20000),
            vision_max_pages=args.vision_max_pages,
        )

        prompt = build_grade_prompt(
            assignment_title=assignment_title,
            assignment_instructions=assignment_instructions,
            assignment_context=assignment_context,
            rubric=rubric,
            policy=args.policy,
            extracted_text=extracted_text if extracted_text else "(No extractable text found.)",
            evidence_mode=evidence_mode,
        )

        # Keep true vision grading only as a fallback. When extraction succeeds,
        # the grader sees text and can use a text-optimized model.
        grading_llm = LlmClient(args.grading_llm_base, args.grading_llm_key, args.grading_llm_model)
        if use_vision and evidence_mode == "vision":
            content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
            for image_path in images:
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{encode_image(image_path)}",
                        },
                    }
                )
            messages = [
                {"role": "system", "content": "Return only JSON matching the schema."},
                {"role": "user", "content": content},
            ]
        else:
            messages = [
                {"role": "system", "content": "Return only JSON matching the schema."},
                {"role": "user", "content": prompt},
            ]

        raw_response = grading_llm.chat(messages, response_format={"type": "json_object"})
        grade = parse_grade_result(raw_response)

        annotations_enabled = os.getenv("ENABLE_SPEEDGRADER_ANNOTATIONS", "1").lower() not in {"0", "false", "no"}
        rubric_enabled = os.getenv("ENABLE_SPEEDGRADER_RUBRIC", "1").lower() not in {"0", "false", "no"}
        max_annotations = max(1, int(os.getenv("SPEEDGRADER_MAX_ANNOTATIONS", "12")))
        annotation_max_pages = max(
            1,
            int(os.getenv("SPEEDGRADER_ANNOTATION_MAX_PAGES", str(max(4, args.vision_max_pages)))),
        )
        annotation_plan: List[AnnotationPlan] = []
        if annotations_enabled:
            annotation_images = images
            if not annotation_images:
                annotation_images = extractor.render_images(
                    pdf_path,
                    run_dir / "annotation_vision",
                    max_pages=min(args.vision_max_pages, annotation_max_pages),
                    zoom=1.5,
                )
            annotation_images = select_images_for_vision(annotation_images, max_total_base64=9_000_000)
            if annotation_images:
                annotation_prompt = build_annotation_prompt(
                    assignment_title=assignment_title,
                    assignment_context=assignment_context,
                    rubric=rubric,
                    grade=grade,
                    max_annotations=max_annotations,
                )
                annotation_content: List[Dict[str, Any]] = [{"type": "text", "text": annotation_prompt}]
                for image_path in annotation_images:
                    annotation_content.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{encode_image(image_path)}"},
                        }
                    )
                annotation_messages = [
                    {"role": "system", "content": "Return only JSON."},
                    {"role": "user", "content": annotation_content},
                ]
                try:
                    # Annotation planning can stay on a separate multimodal model even
                    # when grading itself is handled by a text-only model.
                    annotation_llm = LlmClient(
                        args.annotation_llm_base or args.grading_llm_base,
                        args.annotation_llm_key or args.grading_llm_key,
                        args.annotation_llm_model or args.grading_llm_model,
                    )
                    annotation_response = annotation_llm.chat(
                        annotation_messages,
                        response_format={"type": "json_object"},
                        max_tokens=max(1200, max_annotations * 180),
                        temperature=0.0,
                    )
                    annotation_plan = parse_annotation_plan(annotation_response)
                except Exception:
                    annotation_plan = []

        grade_payload = {
            "graded_at": datetime.now(timezone.utc).isoformat(),
            "grader_version": "speedgrader_e2e_v1",
            "model_version": args.grading_llm_model,
            "extraction_model_version": args.extraction_llm_model,
            "annotation_model_version": args.annotation_llm_model or args.grading_llm_model,
            "course_id": args.course_id,
            "assignment_id": args.assignment_id,
            "student_id": args.student_id,
            "student_name": student_name,
            "course_name": course_name,
            "assignment_title": assignment_title,
            "submission_id": submission.submission_id,
            "assignment_context_sources": assignment_context_sources,
            "assignment_context": assignment_context,
            "annotation_plan": [plan.dict() for plan in annotation_plan],
            "attachment": {
                "filename": pdf_attachment.filename,
                "url": pdf_attachment.url,
            },
            "evidence_mode": evidence_mode,
            "grade": grade.dict(),
        }
        grade_result_path = run_dir / "grade_result.json"
        grade_result_path.write_text(
            json.dumps(grade_payload, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )

        if sandbox_ops is None:
            if not args.internal_secret:
                raise RuntimeError("INTERNAL_AUTH_SECRET is required for sandbox API calls.")
            auth = InternalAuth(args.internal_secret, args.agent_id)
            sandbox_ops = SandboxClient(args.sandbox_api, auth)
        if args.sandbox_id:
            sandbox_id = args.sandbox_id
            sandbox_ops.wait_ready(sandbox_id, timeout=120)
        else:
            sandbox = sandbox_ops.create_sandbox(ttl_seconds=3600)
            sandbox_id = sandbox["sandbox_id"]
            sandbox_ops.wait_ready(sandbox_id)

        selectors = DEFAULT_SELECTORS.copy()
        if args.selectors_json:
            if isinstance(args.selectors_json, str):
                selectors_payload = json.loads(args.selectors_json)
            else:
                selectors_payload = args.selectors_json
            if not isinstance(selectors_payload, dict):
                raise RuntimeError("selectors_json must be a dict or JSON object.")
            selectors.update(selectors_payload)

        speedgrader_url = (
            f"{args.canvas_base}/courses/{args.course_id}/gradebook/speed_grader"
            f"?assignment_id={args.assignment_id}&student_id={args.student_id}"
        )

        ui_checks: Dict[str, Any] = {
            "course": None,
            "assignment": None,
            "speedgrader_open": None,
            "speedgrader_refresh": None,
        }

        if args.navigation_mode == "course":
            course_url = f"{args.canvas_base}/courses/{args.course_id}"
            course_payload = build_open_steps(course_url, selectors)
            if args.profile_artifact_id:
                course_payload["profile_artifact_id"] = args.profile_artifact_id
            course_receipt = sandbox_ops.run_steps(sandbox_id, course_payload)
            course_command_id = course_receipt["command_id"]
            course_state_artifact = wait_for_artifact(sandbox_ops, course_command_id, "page_state", timeout=90)
            course_state_payload = load_page_state(sandbox_ops, course_state_artifact, run_dir)
            course_visible_text = course_state_payload.get("visible_text", "")
            course_match = assert_page_contains(
                course_visible_text, course_name, "course name", strict=args.strict_ui_checks
            )
            ui_checks["course"] = {
                "url": course_url,
                "artifact_id": course_state_artifact["artifact_id"],
                "matched": course_match,
            }

            assignment_url = f"{args.canvas_base}/courses/{args.course_id}/assignments/{args.assignment_id}"
            assignment_payload = build_open_steps(assignment_url, selectors)
            if args.profile_artifact_id:
                assignment_payload["profile_artifact_id"] = args.profile_artifact_id
            assignment_receipt = sandbox_ops.run_steps(sandbox_id, assignment_payload)
            assignment_command_id = assignment_receipt["command_id"]
            assignment_state_artifact = wait_for_artifact(
                sandbox_ops, assignment_command_id, "page_state", timeout=90
            )
            assignment_state_payload = load_page_state(sandbox_ops, assignment_state_artifact, run_dir)
            assignment_visible_text = assignment_state_payload.get("visible_text", "")
            assignment_match = assert_page_contains(
                assignment_visible_text,
                assignment_title,
                "assignment title (course page)",
                strict=args.strict_ui_checks,
            )
            ui_checks["assignment"] = {
                "url": assignment_url,
                "artifact_id": assignment_state_artifact["artifact_id"],
                "matched": assignment_match,
            }

        steps_payload = build_speedgrader_steps(
            speedgrader_url,
            grade,
            selectors,
            annotations=annotation_plan,
            load_submission=True,
            rubric=rubric,
            apply_rubric=rubric_enabled,
        )
        if args.profile_artifact_id:
            steps_payload["profile_artifact_id"] = args.profile_artifact_id

        open_payload = build_open_steps(speedgrader_url, selectors)
        if args.profile_artifact_id:
            open_payload["profile_artifact_id"] = args.profile_artifact_id
        open_receipt = sandbox_ops.run_steps(sandbox_id, open_payload)
        open_command_id = open_receipt["command_id"]
        open_state_artifact = wait_for_artifact(sandbox_ops, open_command_id, "page_state", timeout=90)
        open_state_payload = load_page_state(sandbox_ops, open_state_artifact, run_dir)
        visible_text = open_state_payload.get("visible_text", "")
        assignment_match = assert_page_contains(
            visible_text, assignment_title, "assignment title", strict=args.strict_ui_checks
        )
        student_match = assert_page_contains(
            visible_text, student_name, "student name", strict=args.strict_ui_checks
        )
        ui_checks["speedgrader_open"] = {
            "url": speedgrader_url,
            "artifact_id": open_state_artifact["artifact_id"],
            "assignment_match": assignment_match,
            "student_match": student_match,
        }

        receipt = sandbox_ops.run_steps(sandbox_id, steps_payload)
        command_id = receipt["command_id"]
        wait_for_artifact(sandbox_ops, command_id, "page_state", timeout=90)

        refresh_payload = build_refresh_steps(speedgrader_url, selectors)
        if args.profile_artifact_id:
            refresh_payload["profile_artifact_id"] = args.profile_artifact_id
        receipt = sandbox_ops.run_steps(sandbox_id, refresh_payload)
        refresh_command_id = receipt["command_id"]
        refresh_state = wait_for_artifact(sandbox_ops, refresh_command_id, "page_state", timeout=90)
        refresh_payload = load_page_state(sandbox_ops, refresh_state, run_dir)
        refresh_text = refresh_payload.get("visible_text", "")
        refresh_assignment_match = assert_page_contains(
            refresh_text, assignment_title, "assignment title (refresh)", strict=args.strict_ui_checks
        )
        refresh_student_match = assert_page_contains(
            refresh_text, student_name, "student name (refresh)", strict=args.strict_ui_checks
        )

        snapshot_artifact = wait_for_artifact(sandbox_ops, refresh_command_id, "dom_snapshot", timeout=90)
        snapshot_path = run_dir / f"{snapshot_artifact['artifact_id']}.html"
        sandbox_ops.download_artifact_blob(snapshot_artifact["artifact_id"], snapshot_path)
        html = snapshot_path.read_text(encoding="utf-8")
        actual_value = parse_grade_value_from_html(html)
        if actual_value is None:
            raise RuntimeError("Could not find grade value in SpeedGrader DOM snapshot.")
        try:
            actual_points = float(actual_value)
            expected_points = float(grade.total_points)
            if abs(actual_points - expected_points) > 0.01:
                raise RuntimeError(
                    f"Grade verification failed: expected {expected_points}, found {actual_points}."
                )
        except ValueError as exc:
            raise RuntimeError(f"Grade verification failed: non-numeric value '{actual_value}'.") from exc

        ui_checks["speedgrader_refresh"] = {
            "artifact_id": refresh_state["artifact_id"],
            "assignment_match": refresh_assignment_match,
            "student_match": refresh_student_match,
            "grade_value": actual_value,
        }

        grade_payload["ui_checks"] = ui_checks
        grade_result_path.write_text(
            json.dumps(grade_payload, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )

        speedgrader_state_path = run_dir / "speedgrader_state.json"
        speedgrader_state_path.write_text(
            json.dumps(refresh_payload, ensure_ascii=True, indent=2),
            encoding="utf-8",
        )

        return GradeStudentOutcome(
            run_dir=run_dir,
            grade_result_path=grade_result_path,
            speedgrader_state_path=speedgrader_state_path,
        )


def _parse_args(argv: Optional[List[str]] = None) -> GradeStudentArgs:
    parser = argparse.ArgumentParser(description="Grade a single student via Canvas + SpeedGrader browser agent.")
    parser.add_argument("--course-id", required=True)
    parser.add_argument("--assignment-id", required=True)
    parser.add_argument("--student-id", required=True)
    parser.add_argument("--canvas-base", required=True, help="Canvas base URL, e.g. https://canvas.example.edu")
    parser.add_argument("--canvas-token", required=True)
    parser.add_argument("--sandbox-api", default=os.getenv("SANDBOX_API", "http://localhost:8000"))
    parser.add_argument("--agent-id", default=os.getenv("SANDBOX_AGENT_ID", "grader"))
    parser.add_argument("--internal-secret", default=os.getenv("INTERNAL_AUTH_SECRET"))
    parser.add_argument("--sandbox-id", default=None, help="Reuse an existing sandbox if provided")
    parser.add_argument("--profile-artifact-id", default=None, help="Browser profile artifact id for Canvas login")
    parser.add_argument("--policy", default="")
    parser.add_argument("--selectors-json", default=os.getenv("SPEEDGRADER_SELECTORS_JSON"))
    parser.add_argument("--output-dir", default="./artifacts/grading_runs")
    parser.add_argument("--vision-max-pages", type=int, default=6)
    parser.add_argument("--text-max-chars", type=int, default=40000)
    parser.add_argument("--min-text-chars", type=int, default=200)
    parser.add_argument("--llm-base", default=os.getenv("LLM_API_BASE"))
    parser.add_argument("--llm-key", default=os.getenv("LLM_API_KEY"))
    parser.add_argument("--llm-model", default=os.getenv("LLM_MODEL"))
    parser.add_argument("--extraction-llm-base", default=os.getenv("EXTRACTION_LLM_BASE") or os.getenv("VLLM_API_BASE"))
    parser.add_argument("--extraction-llm-key", default=os.getenv("EXTRACTION_LLM_API_KEY") or os.getenv("VLLM_API_KEY"))
    parser.add_argument("--extraction-llm-model", default=os.getenv("EXTRACTION_LLM_MODEL") or os.getenv("VLLM_MODEL"))
    parser.add_argument(
        "--grading-llm-base",
        default=os.getenv("GRADING_LLM_BASE") or os.getenv("FIREWORKS_API_BASE") or os.getenv("LLM_API_BASE") or "https://api.fireworks.ai/inference/v1",
    )
    parser.add_argument(
        "--grading-llm-key",
        default=os.getenv("GRADING_LLM_API_KEY") or os.getenv("FIREWORKS_API_KEY") or os.getenv("LLM_API_KEY"),
    )
    parser.add_argument(
        "--grading-llm-model",
        default=os.getenv("GRADING_LLM_MODEL") or os.getenv("FIREWORKS_GRADING_MODEL") or os.getenv("LLM_MODEL") or "accounts/fireworks/models/llama-v3p1-70b-instruct",
    )
    parser.add_argument("--annotation-llm-base", default=os.getenv("ANNOTATION_LLM_BASE"))
    parser.add_argument("--annotation-llm-key", default=os.getenv("ANNOTATION_LLM_API_KEY"))
    parser.add_argument("--annotation-llm-model", default=os.getenv("ANNOTATION_LLM_MODEL"))
    parser.add_argument(
        "--navigation-mode",
        choices=["course", "direct"],
        default="course",
        help="Navigate to course/assignment before SpeedGrader or deep-link directly.",
    )
    parser.add_argument("--strict-ui-checks", dest="strict_ui_checks", action="store_true", default=True)
    parser.add_argument("--no-strict-ui-checks", dest="strict_ui_checks", action="store_false")
    args = parser.parse_args(argv)
    return GradeStudentArgs(
        course_id=args.course_id,
        assignment_id=args.assignment_id,
        student_id=args.student_id,
        canvas_base=args.canvas_base,
        canvas_token=args.canvas_token,
        internal_secret=args.internal_secret,
        sandbox_api=args.sandbox_api,
        agent_id=args.agent_id,
        sandbox_id=args.sandbox_id,
        profile_artifact_id=args.profile_artifact_id,
        policy=args.policy,
        selectors_json=args.selectors_json,
        output_dir=args.output_dir,
        vision_max_pages=args.vision_max_pages,
        text_max_chars=args.text_max_chars,
        min_text_chars=args.min_text_chars,
        extraction_llm_base=args.extraction_llm_base,
        extraction_llm_key=args.extraction_llm_key,
        extraction_llm_model=args.extraction_llm_model,
        grading_llm_base=args.grading_llm_base or args.llm_base or "https://api.fireworks.ai/inference/v1",
        grading_llm_key=args.grading_llm_key or args.llm_key,
        grading_llm_model=args.grading_llm_model or args.llm_model or "accounts/fireworks/models/llama-v3p1-70b-instruct",
        annotation_llm_base=args.annotation_llm_base,
        annotation_llm_key=args.annotation_llm_key,
        annotation_llm_model=args.annotation_llm_model,
        navigation_mode=args.navigation_mode,
        strict_ui_checks=args.strict_ui_checks,
    )


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    run_grade_student(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
