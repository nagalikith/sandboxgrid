from __future__ import annotations

import base64
import difflib
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import ValidationError

try:
    import fitz  # PyMuPDF
except Exception:  # noqa: BLE001
    fitz = None

from .runner_clients import CanvasClient, LlmClient, PdfExtractor
from .runner_models import (
    AnnotationPlan,
    AnnotationPlanPayload,
    AnnotationPoint,
    AttachmentInfo,
    CanvasSubmission,
    ConfidenceMarker,
    CriterionVerificationResult,
    GradeResult,
    GradeVerificationResult,
)
from .runner_selectors import FIREWORKS_VISION_MAX_IMAGES, FIREWORKS_VISION_MAX_TOTAL_BASE64


def _response_text(raw_response: Dict[str, Any], *, label: str) -> str:
    # OpenAI-compatible APIs can return plain text or block content. Normalize
    # both so the downstream parsers only need to handle one shape.
    try:
        content = raw_response["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"{label} missing content.") from exc
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("text"):
                parts.append(str(item["text"]))
        if parts:
            return "\n".join(parts)
    raise RuntimeError(f"{label} returned unsupported content format.")


def _response_text_or_none(raw_response: Optional[Dict[str, Any]], *, label: str) -> Optional[str]:
    if not raw_response:
        return None
    try:
        return _response_text(raw_response, label=label)
    except Exception:
        return None


def summarize_messages_for_observability(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    summarized: List[Dict[str, Any]] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, list):
            blocks: List[Dict[str, Any]] = []
            image_index = 0
            for item in content:
                if not isinstance(item, dict):
                    continue
                item_type = item.get("type")
                if item_type == "text":
                    blocks.append({"type": "text", "text": str(item.get("text") or "")})
                elif item_type == "image_url":
                    image_index += 1
                    blocks.append({"type": "image_url", "image_index": image_index, "image_url": "omitted"})
                else:
                    blocks.append({"type": str(item_type or "unknown")})
            summarized.append({"role": message.get("role"), "content": blocks})
        else:
            summarized.append({"role": message.get("role"), "content": str(content or "")})
    return summarized


def strip_html(value: Optional[str]) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", value)).strip()


def safe_filename(name: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in name)
    return safe.strip("_") or "file"


def summarize_rubric_for_logs(rubric: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    summary: List[Dict[str, Any]] = []
    for item in rubric:
        if isinstance(item, dict):
            summary.append(
                {
                    "id": item.get("id") or item.get("criterion_id") or item.get("name"),
                    "description": item.get("description") or item.get("long_description") or item.get("name"),
                    "points": item.get("points") or item.get("max_score"),
                }
            )
    return summary


def truncate_for_logs(value: str, limit: int = 1500) -> str:
    text = " ".join((value or "").split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _find_first_nested_value(payload: Any, keys: set[str]) -> Optional[str]:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in keys and value is not None:
                text = str(value).strip()
                if text:
                    return text
        for value in payload.values():
            found = _find_first_nested_value(value, keys)
            if found:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _find_first_nested_value(item, keys)
            if found:
                return found
    return None


def extract_speedgrader_anonymous_id(assignment: Dict[str, Any], submission: CanvasSubmission) -> Optional[str]:
    candidate_keys = {"anonymous_id", "anonymous_student_id", "anonymous_user_id"}
    for payload in (submission.raw, assignment):
        found = _find_first_nested_value(payload, candidate_keys)
        if found:
            return found
    return None


def find_latest_grade_result(output_dir: str, lock_key: str) -> Optional[Path]:
    base = Path(output_dir)
    if not base.exists():
        return None
    candidates = sorted(
        base.glob(f"{lock_key}_*/grade_result.json"),
        key=lambda path: (path.stat().st_mtime_ns, str(path.parent)),
        reverse=True,
    )
    for path in candidates:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict) and isinstance(data.get("grade"), dict):
            return path
    return None


def _normalize_evidence_item(item: Any) -> Optional[Dict[str, Any]]:
    if isinstance(item, str):
        text = item.strip()
        return {"page": 1, "quote": text} if text else None
    if not isinstance(item, dict):
        return None
    quote = str(item.get("quote") or item.get("text") or item.get("evidence") or item.get("excerpt") or "").strip()
    if not quote:
        return None
    raw_page = item.get("page") or item.get("page_number") or 1
    try:
        page = max(int(raw_page), 1)
    except (TypeError, ValueError):
        page = 1
    return {"page": page, "quote": quote}


def _normalize_grade_result_payload(data: Any) -> Any:
    if not isinstance(data, dict) or not isinstance(data.get("criteria"), list):
        return data
    normalized_criteria: List[Any] = []
    for criterion in data["criteria"]:
        if not isinstance(criterion, dict):
            normalized_criteria.append(criterion)
            continue
        normalized = dict(criterion)
        evidence = normalized.get("evidence")
        if isinstance(evidence, list):
            normalized["evidence"] = [item for item in (_normalize_evidence_item(entry) for entry in evidence) if item]
        elif evidence is None:
            normalized["evidence"] = []
        else:
            item = _normalize_evidence_item(evidence)
            normalized["evidence"] = [item] if item else []
        normalized_criteria.append(normalized)
    normalized_data = dict(data)
    normalized_data["criteria"] = normalized_criteria
    return normalized_data


def load_saved_grade_result(path: Path) -> Dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"Saved grade result is invalid: {path}")
    grade_payload = data.get("grade")
    if not isinstance(grade_payload, dict):
        raise RuntimeError(f"Saved grade result missing grade payload: {path}")
    grade = GradeResult.parse_obj(_normalize_grade_result_payload(grade_payload))
    annotation_plan: List[AnnotationPlan] = []
    for item in data.get("annotation_plan") or []:
        if not isinstance(item, dict):
            continue
        try:
            annotation_plan.append(AnnotationPlan.parse_obj(item))
        except ValidationError:
            continue
    return {
        "grade": grade,
        "annotation_plan": annotation_plan,
        "grading_confidence": data.get("grading_confidence") or {},
        "assignment_context": str(data.get("assignment_context") or ""),
        "assignment_context_sources": data.get("assignment_context_sources") or [],
        "assignment_title": str(data.get("assignment_title") or "Assignment"),
        "course_name": str(data.get("course_name") or ""),
        "student_name": str(data.get("student_name") or ""),
        "submission_id": data.get("submission_id"),
        "submission_text": str(data.get("submission_text") or ""),
        "submission_text_source": str(data.get("submission_text_source") or "saved_grade_result"),
        "profile_artifact_id": data.get("profile_artifact_id"),
        "source_grade_result_path": str(path.resolve()),
        "source_llm_observability_path": str(data.get("llm_observability_path") or ""),
    }


def select_pdf(attachments: List[AttachmentInfo]) -> AttachmentInfo:
    if not attachments:
        raise RuntimeError("No attachments found in submission.")
    pdfs = [item for item in attachments if item.content_type == "application/pdf" or item.filename.lower().endswith(".pdf")]
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


def is_fireworks_base_url(base_url: Optional[str]) -> bool:
    return "fireworks.ai" in (base_url or "").lower()


def build_response_format_for_model(model_cls: Any, *, use_fireworks: bool) -> Dict[str, Any]:
    if use_fireworks:
        return {
            "type": "json_schema",
            "json_schema": {
                "name": getattr(model_cls, "__name__", "ResponsePayload"),
                "schema": model_cls.schema(),
            },
        }
    return {"type": "json_object"}


def build_grade_response_format(*, use_fireworks: bool) -> Dict[str, Any]:
    return build_response_format_for_model(GradeResult, use_fireworks=use_fireworks)


def build_annotation_response_format(*, use_fireworks: bool) -> Dict[str, Any]:
    return build_response_format_for_model(AnnotationPlanPayload, use_fireworks=use_fireworks)


def build_grade_verification_response_format(*, use_fireworks: bool) -> Dict[str, Any]:
    return build_response_format_for_model(GradeVerificationResult, use_fireworks=use_fireworks)


def build_confidence_label_response_format(*, use_fireworks: bool) -> Optional[Dict[str, Any]]:
    if not use_fireworks:
        return None
    return {"type": "grammar", "grammar": 'root ::= "S" | "W" | "U" | "A" | "R"'}


def normalize_for_matching(value: Optional[str]) -> str:
    text = (value or "").lower()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^a-z0-9 ]+", "", text)
    return text.strip()


def _candidate_snippets(text: str) -> List[str]:
    candidates: List[str] = []
    for chunk in re.split(r"[\n\r]+", text):
        chunk = chunk.strip()
        if chunk:
            candidates.append(chunk)
    if not candidates:
        stripped = text.strip()
        if stripped:
            candidates.append(stripped)
    return candidates[:40]


def match_quote_to_submission_pages(
    quote: str,
    submission_pages: List[Dict[str, Any]],
    *,
    page_hint: Optional[int] = None,
) -> Dict[str, Any]:
    clean_quote = quote.strip()
    if not clean_quote:
        return {
            "quote": quote,
            "matched": False,
            "matched_page": None,
            "match_type": "missing",
            "match_score": 0.0,
        }
    normalized_quote = normalize_for_matching(clean_quote)
    exact_candidate: Optional[Dict[str, Any]] = None
    normalized_candidate: Optional[Dict[str, Any]] = None
    fuzzy_candidate = {
        "quote": clean_quote,
        "matched": False,
        "matched_page": None,
        "match_type": "missing",
        "match_score": 0.0,
    }
    for page in submission_pages:
        page_num = page.get("page") or 1
        text = str(page.get("text") or "")
        if not text.strip():
            continue
        if clean_quote in text:
            exact_candidate = {
                "quote": clean_quote,
                "matched": True,
                "matched_page": page_num,
                "match_type": "exact",
                "match_score": 1.0,
            }
            if page_hint is None or page_hint == page_num:
                return exact_candidate
        normalized_text = normalize_for_matching(text)
        if normalized_quote and normalized_quote in normalized_text:
            candidate = {
                "quote": clean_quote,
                "matched": True,
                "matched_page": page_num,
                "match_type": "normalized",
                "match_score": 0.97,
            }
            if page_hint is None or page_hint == page_num:
                return candidate
            if normalized_candidate is None:
                normalized_candidate = candidate
        for snippet in _candidate_snippets(text):
            ratio = difflib.SequenceMatcher(None, normalized_quote, normalize_for_matching(snippet)).ratio()
            if ratio > fuzzy_candidate["match_score"]:
                fuzzy_candidate = {
                    "quote": clean_quote,
                    "matched": ratio >= 0.72,
                    "matched_page": page_num,
                    "match_type": "fuzzy" if ratio >= 0.72 else "missing",
                    "match_score": round(ratio, 4),
                    "matched_text": snippet[:280],
                }
    return exact_candidate or normalized_candidate or fuzzy_candidate


def _criterion_max_points(rubric: List[Dict[str, Any]], criterion_id: str) -> Optional[float]:
    for criterion in rubric:
        rubric_id = criterion.get("id") or criterion.get("criterion_id") or criterion.get("name")
        if str(rubric_id) != criterion_id:
            continue
        raw_points = criterion.get("points") or criterion.get("max_score")
        try:
            return float(raw_points)
        except (TypeError, ValueError):
            return None
    return None


def build_submission_pages_for_matching(
    *,
    extracted_pages: List[Dict[str, Any]],
    text_pages: List[Dict[str, Any]],
    extracted_text: str,
) -> List[Dict[str, Any]]:
    if extracted_pages:
        return extracted_pages
    if text_pages:
        return text_pages
    text = extracted_text.strip()
    if not text or text == "(No extractable text found.)":
        return []
    return [{"page": 1, "text": text}]


def assess_grade_grounding(
    grade: GradeResult,
    *,
    submission_pages: List[Dict[str, Any]],
    rubric: List[Dict[str, Any]],
) -> Dict[str, Any]:
    criteria_summary: List[Dict[str, Any]] = []
    total_evidence = 0
    matched_evidence = 0
    for criterion in grade.criteria:
        evidence_matches: List[Dict[str, Any]] = []
        for evidence in criterion.evidence:
            total_evidence += 1
            match = match_quote_to_submission_pages(
                evidence.quote,
                submission_pages,
                page_hint=evidence.page,
            )
            match["expected_page"] = evidence.page
            evidence_matches.append(match)
            if match.get("matched"):
                matched_evidence += 1
        evidence_count = len(evidence_matches)
        matched_count = sum(1 for item in evidence_matches if item.get("matched"))
        rubric_max_points = _criterion_max_points(rubric, criterion.id)
        points_within_rubric = True
        if rubric_max_points is not None:
            points_within_rubric = criterion.points <= rubric_max_points + 1e-9
        criteria_summary.append(
            {
                "id": criterion.id,
                "points": criterion.points,
                "rubric_max_points": rubric_max_points,
                "points_within_rubric": points_within_rubric,
                "evidence_count": evidence_count,
                "matched_evidence_count": matched_count,
                "citation_match_ratio": round(matched_count / evidence_count, 4) if evidence_count else 0.0,
                "evidence_matches": evidence_matches,
            }
        )
    overall_ratio = round(matched_evidence / total_evidence, 4) if total_evidence else 0.0
    return {
        "submission_page_count": len(submission_pages),
        "total_evidence_count": total_evidence,
        "matched_evidence_count": matched_evidence,
        "citation_match_ratio": overall_ratio,
        "criteria": criteria_summary,
    }


def render_pdf_images_best_effort(
    extractor: PdfExtractor,
    pdf_path: Path,
    out_dir: Path,
    *,
    max_pages: int,
    zoom: float = 1.5,
) -> Tuple[List[Path], Optional[str]]:
    try:
        return extractor.render_images(pdf_path, out_dir, max_pages=max_pages, zoom=zoom), None
    except Exception as exc:
        return [], str(exc)


def encode_image(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def estimate_base64_size(path: Path) -> int:
    size = path.stat().st_size
    return ((size + 2) // 3) * 4


def select_images_for_vision(
    images: List[Path],
    max_total_base64: int = FIREWORKS_VISION_MAX_TOTAL_BASE64,
    max_images: int = FIREWORKS_VISION_MAX_IMAGES,
) -> List[Path]:
    selected: List[Path] = []
    total = 0
    for path in images:
        if len(selected) >= max_images:
            break
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
        lines: List[str] = []
        for raw in content.splitlines():
            line = raw.strip().lstrip("-•").strip()
            if line:
                lines.append(line)
        return lines
    if isinstance(data, dict):
        questions = data.get("questions") or data.get("items") or data.get("prompts")
        if isinstance(questions, list):
            return [str(question).strip() for question in questions if str(question).strip()]
        if isinstance(questions, str) and questions.strip():
            return [questions.strip()]
        return []
    if isinstance(data, list):
        return [str(question).strip() for question in data if str(question).strip()]
    if isinstance(data, str) and data.strip():
        return [data.strip()]
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
        text = str(item.get("text") or item.get("content") or item.get("ocr_text") or "").strip()
        if not text:
            continue
        raw_page = item.get("page") or item.get("page_number") or index
        try:
            page_num = int(raw_page)
        except (TypeError, ValueError):
            page_num = index
        parsed.append({"page": max(page_num, 1), "text": text})
    return parsed


def build_extraction_messages(images: List[Path]) -> List[Dict[str, Any]]:
    prompt = (
        "Transcribe the student submission pages shown in these images. "
        "Return JSON only with key 'pages' as an array of objects. "
        "Each object must contain 'page' (1-based integer) and 'text' (string). "
        "Do not grade, summarize, or interpret the work."
    )
    content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
    for image_path in images:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encode_image(image_path)}"}})
    return [{"role": "system", "content": "Return only JSON."}, {"role": "user", "content": content}]


def extract_submission_text_with_llm(*, images: List[Path], client: LlmClient) -> List[Dict[str, Any]]:
    if not images:
        return []
    messages = build_extraction_messages(images)
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
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encode_image(image_path)}"}})
    raw = client.chat(
        [{"role": "system", "content": "Return only JSON."}, {"role": "user", "content": content}],
        max_tokens=1200,
        temperature=0.0,
    )
    return parse_questions_from_content(_response_text(raw, label="Vision LLM response"))


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

    pdfs = [item for item in attachments if item.content_type == "application/pdf" or item.filename.lower().endswith(".pdf")]
    if pdfs:
        out_dir.mkdir(parents=True, exist_ok=True)
        remaining = max_chars - len("\n\n".join(parts))
        vision_key = os.getenv("FIREWORKS_API_KEY")
        vision_base = os.getenv("FIREWORKS_API_BASE", "https://api.fireworks.ai/inference/v1")
        vision_model = os.getenv("FIREWORKS_VISION_MODEL", "accounts/fireworks/models/kimi-k2p5")
        for index, attachment in enumerate(pdfs, start=1):
            if remaining <= 200:
                break
            filename = safe_filename(attachment.filename)
            dest = out_dir / f"{index:02d}_{filename}"
            canvas.download_attachment(attachment, dest)
            block = ""
            if vision_key and fitz is not None:
                try:
                    images = extractor.render_images(dest, out_dir / f"{index:02d}_{filename}_vision", max_pages=vision_max_pages, zoom=1.5)
                    questions = extract_questions_with_fireworks(
                        images=select_images_for_vision(images),
                        model=vision_model,
                        api_key=vision_key,
                        base_url=vision_base,
                    )
                    if questions:
                        block = f"[Assignment Attachment Questions: {attachment.filename}]\n" + "\n".join(
                            f"- {question}" for question in questions
                        )
                except Exception:
                    block = ""
            if not block:
                pages = extractor.extract_text(dest)
                text = build_text_payload(pages, min(8000, max(0, remaining))) or "(No extractable text found.)"
                block = f"[Assignment Attachment: {attachment.filename}]\n{text}"
            if len(block) > remaining:
                block = block[:remaining]
            parts.append(block)
            sources.append(attachment.filename)
            remaining = max_chars - len("\n\n".join(parts))

    return ("\n\n".join(parts), sources) if parts else ("(No assignment context available.)", sources)


def build_annotation_prompt(
    *,
    assignment_title: str,
    assignment_context: str,
    rubric: List[Dict[str, Any]],
    grade: GradeResult,
    max_annotations: int,
) -> str:
    rubric_block = "\n".join(
        (
            f"- id={criterion.get('id') or criterion.get('criterion_id') or criterion.get('name')}: "
            f"{criterion.get('description') or criterion.get('long_description') or criterion.get('description', '')} "
            f"(points={criterion.get('points') or criterion.get('max_score')})"
        )
        for criterion in rubric
    ) or "(No rubric provided)"
    criteria_block = "\n".join(
        (
            f"- id={item.id} points={item.points} comment={item.comment} "
            f"evidence={'; '.join([f'p{e.page}: {e.quote}' for e in item.evidence]) if item.evidence else ''}"
        )
        for item in grade.criteria
    ) or "(No criteria provided)"
    return (
        "You are annotating a student submission in Canvas SpeedGrader. "
        "Use the question context and rubric to decide where to mark up the submission. "
        "Reply in JSON matching the requested schema. "
        "Return JSON with key 'annotations' as a list. Each annotation must include: "
        "page (1-based), tool (one of 'select', 'point', 'freetext', 'highlight', 'strike', 'free_draw', 'area'), "
        "criterion_id, rationale, color (red/orange/yellow/green/blue/dark blue/pink/purple/brown/black), "
        "and coordinates. You may also include evidence_quote when the annotation is tied to a specific quoted passage. "
        "All coordinates are normalized 0-1 for the page image. "
        "For tools that draw (highlight/strike/free_draw), include path (list of {x,y} points). "
        "For area, include path with exactly two points (top-left and bottom-right). "
        "For point, include point {x,y}. For freetext, include point {x,y} and text. "
        "For select, omit coordinates. "
        f"Limit to at most {max_annotations} annotations and only use pages shown in the images.\n\n"
        f"Assignment title: {assignment_title}\n"
        f"Assignment question context:\n{assignment_context}\n\n"
        f"Rubric:\n{rubric_block}\n\n"
        f"Grading result:\n{criteria_block}\n"
    )


def parse_annotation_plan(raw_response: Dict[str, Any]) -> List[AnnotationPlan]:
    try:
        data = json.loads(_response_text(raw_response, label="Annotation response"))
    except json.JSONDecodeError:
        return []
    items = data.get("annotations") if isinstance(data, dict) else data if isinstance(data, list) else []
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
    for item in items or []:
        try:
            plan = AnnotationPlan.parse_obj(item)
        except ValidationError:
            continue
        clamped_path = [AnnotationPoint(x=min(max(point.x, 0.0), 1.0), y=min(max(point.y, 0.0), 1.0)) for point in plan.path or []]
        clamped_point = (
            AnnotationPoint(x=min(max(plan.point.x, 0.0), 1.0), y=min(max(plan.point.y, 0.0), 1.0))
            if plan.point is not None
            else (clamped_path[0] if clamped_path else None)
        )
        color = color_map.get((plan.color or "").strip().lower(), (plan.color or "").strip().lower()) or None
        text = (plan.text or "").strip() or None
        if plan.tool in {"free_draw", "strike", "highlight", "area"} and len(clamped_path) < 2:
            continue
        if plan.tool == "point" and clamped_point is None:
            continue
        if plan.tool == "freetext" and (clamped_point is None or not text):
            continue
        parsed.append(
            AnnotationPlan(
                page=plan.page,
                tool=plan.tool,
                criterion_id=(plan.criterion_id or "").strip() or None,
                evidence_quote=(plan.evidence_quote or "").strip() or None,
                rationale=(plan.rationale or "").strip() or None,
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
    rubric_block = "\n".join(
        (
            f"- id={criterion.get('id') or criterion.get('criterion_id') or criterion.get('name')}: "
            f"{criterion.get('description') or criterion.get('long_description') or criterion.get('description', '')} "
            f"(points={criterion.get('points') or criterion.get('max_score')})"
        )
        for criterion in rubric
    ) or "(No rubric provided)"
    return (
        "You are an exacting grader. Grade the student submission using the rubric, "
        "assignment instructions, and question context (including any assignment PDFs). "
        "Reply in JSON matching the requested schema. "
        "Return strict JSON with keys: total_points, criteria, overall_feedback, overall_rationale. "
        "Each criteria item must include id, points, comment, rationale, evidence. "
        "Evidence must include page and quote. "
        "Do not invent evidence. If a score cannot be justified from the submission, score conservatively and explain the uncertainty in the rationale. "
        f"Evidence mode: {evidence_mode}.\n\n"
        f"Assignment title: {assignment_title}\n"
        f"Assignment instructions: {assignment_instructions}\n\n"
        "Assignment question context (use this to interpret the rubric and expected answers):\n"
        f"{assignment_context}\n\n"
        f"Rubric criteria (interpret using the question context above):\n{rubric_block}\n\n"
        f"Policy: {policy}\n\n"
        f"Submission content:\n{extracted_text}\n"
    )


def build_grade_verification_prompt(
    *,
    assignment_title: str,
    assignment_context: str,
    rubric: List[Dict[str, Any]],
    extracted_text: str,
    grade: GradeResult,
    grounding_summary: Dict[str, Any],
) -> str:
    rubric_block = "\n".join(
        (
            f"- id={criterion.get('id') or criterion.get('criterion_id') or criterion.get('name')}: "
            f"{criterion.get('description') or criterion.get('long_description') or criterion.get('description', '')} "
            f"(points={criterion.get('points') or criterion.get('max_score')})"
        )
        for criterion in rubric
    ) or "(No rubric provided)"
    return (
        "You are a grading verifier. Assess whether the draft grade is supported by the supplied student submission. "
        "Reply in JSON matching the requested schema. "
        "Do not produce a new grade. Only judge support. "
        "Allowed markers are supported, weak_support, uncertain, abstain, review_required. "
        "Use abstain if the submission text is insufficient to verify the draft. "
        "Use review_required if the draft conflicts with the evidence, cites text that is missing, or exceeds rubric bounds.\n\n"
        f"Assignment title: {assignment_title}\n"
        f"Assignment context:\n{assignment_context}\n\n"
        f"Rubric:\n{rubric_block}\n\n"
        f"Submission content:\n{extracted_text}\n\n"
        f"Draft grade JSON:\n{json.dumps(grade.dict(exclude_none=True), ensure_ascii=True, indent=2)}\n\n"
        f"Deterministic evidence checks:\n{json.dumps(grounding_summary, ensure_ascii=True, indent=2)}\n"
    )


def build_confidence_label_messages(
    *,
    grade: GradeResult,
    grounding_summary: Dict[str, Any],
    verification: GradeVerificationResult,
) -> List[Dict[str, Any]]:
    prompt = (
        "You are assigning a final confidence routing code for a draft grade. "
        "Choose exactly one code based only on support from the provided submission evidence.\n"
        "S = supported\n"
        "W = weak_support\n"
        "U = uncertain\n"
        "A = abstain\n"
        "R = review_required\n\n"
        f"Draft grade JSON:\n{json.dumps(grade.dict(exclude_none=True), ensure_ascii=True, indent=2)}\n\n"
        f"Deterministic evidence checks:\n{json.dumps(grounding_summary, ensure_ascii=True, indent=2)}\n\n"
        f"Verifier JSON:\n{json.dumps(verification.dict(exclude_none=True), ensure_ascii=True, indent=2)}\n"
    )
    return [
        {"role": "system", "content": "Return exactly one code: S, W, U, A, or R."},
        {"role": "user", "content": prompt},
    ]


def parse_grade_result(raw_response: Dict[str, Any]) -> GradeResult:
    try:
        data = json.loads(_response_text(raw_response, label="LLM response"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("LLM response is not valid JSON.") from exc
    try:
        return GradeResult.parse_obj(_normalize_grade_result_payload(data))
    except ValidationError as exc:
        raise RuntimeError(f"LLM response failed validation: {exc}") from exc


def parse_grade_verification_result(raw_response: Dict[str, Any]) -> GradeVerificationResult:
    try:
        data = json.loads(_response_text(raw_response, label="Grade verification response"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("Grade verification response is not valid JSON.") from exc
    try:
        return GradeVerificationResult.parse_obj(data)
    except ValidationError as exc:
        raise RuntimeError(f"Grade verification response failed validation: {exc}") from exc


def parse_confidence_label(raw_response: Dict[str, Any]) -> Optional[ConfidenceMarker]:
    marker_map: Dict[str, ConfidenceMarker] = {
        "S": "supported",
        "W": "weak_support",
        "U": "uncertain",
        "A": "abstain",
        "R": "review_required",
    }
    text = _response_text_or_none(raw_response, label="Confidence label response")
    if not text:
        return None
    return marker_map.get(text.strip().upper())


def extract_logprob_margin(raw_response: Optional[Dict[str, Any]]) -> Optional[float]:
    if not raw_response:
        return None
    try:
        entries = raw_response["choices"][0]["logprobs"]["content"]
    except (KeyError, IndexError, TypeError):
        return None
    for entry in entries or []:
        top = entry.get("top_logprobs")
        if not isinstance(top, list) or len(top) < 2:
            continue
        try:
            ordered = sorted(top, key=lambda item: float(item.get("logprob", float("-inf"))), reverse=True)
            return round(float(ordered[0]["logprob"]) - float(ordered[1]["logprob"]), 4)
        except (KeyError, TypeError, ValueError):
            continue
    return None


def _choose_confidence_marker(
    *,
    verifier_marker: Optional[ConfidenceMarker],
    citation_match_ratio: float,
    evidence_count: int,
    contradictions_found: int,
    points_within_rubric: bool = True,
    verifier_margin: Optional[float] = None,
) -> Tuple[ConfidenceMarker, str]:
    reasons: List[str] = []
    if not points_within_rubric:
        return "review_required", "Score exceeds rubric maximum."
    if contradictions_found > 0:
        return "review_required", "Verifier found contradictions against the submission evidence."
    if evidence_count == 0:
        if verifier_marker == "abstain":
            return "abstain", "Verifier abstained because the draft grade has no supporting evidence."
        return "review_required", "Draft grade has no supporting evidence quotes."
    if verifier_marker == "abstain":
        return "abstain", "Verifier could not support the draft from the supplied submission."
    if verifier_marker == "uncertain":
        return "uncertain", "Verifier marked the draft as uncertain."
    if citation_match_ratio >= 0.9:
        reasons.append("Evidence quotes matched the submission.")
        if verifier_margin is not None and verifier_margin < 0.75:
            reasons.append("Label margin was weak.")
            return "weak_support", " ".join(reasons)
        if verifier_marker in {None, "supported", "weak_support"}:
            return "supported", " ".join(reasons)
    if citation_match_ratio >= 0.5:
        reasons.append("Some evidence quotes matched the submission.")
        if verifier_marker == "supported":
            reasons.append("Verifier still supported the draft.")
        elif verifier_marker == "weak_support":
            reasons.append("Verifier marked the draft as weakly supported.")
        return "weak_support", " ".join(reasons)
    if verifier_marker == "supported":
        return "weak_support", "Verifier supported the draft, but evidence quote matching was weak."
    if verifier_marker == "weak_support":
        return "uncertain", "Verifier reported weak support and the evidence matching was weak."
    return "review_required", "Evidence quotes did not match the submission strongly enough."


def apply_grade_confidence_markers(
    grade: GradeResult,
    *,
    grounding_summary: Dict[str, Any],
    verification: Optional[GradeVerificationResult] = None,
    overall_label: Optional[ConfidenceMarker] = None,
    overall_margin: Optional[float] = None,
) -> Dict[str, Any]:
    verifier_by_id: Dict[str, CriterionVerificationResult] = {
        item.id: item for item in (verification.criteria if verification else [])
    }
    criteria_summary: List[Dict[str, Any]] = []
    for criterion in grade.criteria:
        summary = next((item for item in grounding_summary.get("criteria", []) if item.get("id") == criterion.id), None) or {}
        verifier = verifier_by_id.get(criterion.id)
        marker, reason = _choose_confidence_marker(
            verifier_marker=verifier.marker if verifier else None,
            citation_match_ratio=float(summary.get("citation_match_ratio") or 0.0),
            evidence_count=int(summary.get("evidence_count") or 0),
            contradictions_found=int((verifier.contradictions_found if verifier else 0) or 0),
            points_within_rubric=bool(summary.get("points_within_rubric", True)),
        )
        criterion.confidence_marker = marker
        criterion.confidence_reason = reason
        criteria_summary.append(
            {
                "id": criterion.id,
                "confidence_marker": marker,
                "confidence_reason": reason,
                "citation_match_ratio": float(summary.get("citation_match_ratio") or 0.0),
                "points_within_rubric": bool(summary.get("points_within_rubric", True)),
                "verifier_marker": verifier.marker if verifier else None,
                "verifier_notes": verifier.notes if verifier else None,
                "verifier_contradictions_found": verifier.contradictions_found if verifier else 0,
                "evidence_matches": summary.get("evidence_matches") or [],
            }
        )
    overall_verifier_marker = overall_label or (verification.overall_marker if verification else None)
    marker, reason = _choose_confidence_marker(
        verifier_marker=overall_verifier_marker,
        citation_match_ratio=float(grounding_summary.get("citation_match_ratio") or 0.0),
        evidence_count=int(grounding_summary.get("total_evidence_count") or 0),
        contradictions_found=int((verification.contradictions_found if verification else 0) or 0),
        verifier_margin=overall_margin,
    )
    grade.confidence_marker = marker
    grade.confidence_reason = reason
    grade.verification_notes = verification.overall_notes if verification else None
    return {
        "overall": {
            "confidence_marker": marker,
            "confidence_reason": reason,
            "citation_match_ratio": float(grounding_summary.get("citation_match_ratio") or 0.0),
            "total_evidence_count": int(grounding_summary.get("total_evidence_count") or 0),
            "matched_evidence_count": int(grounding_summary.get("matched_evidence_count") or 0),
            "verifier_marker": verification.overall_marker if verification else None,
            "verifier_notes": verification.overall_notes if verification else None,
            "verifier_contradictions_found": verification.contradictions_found if verification else 0,
            "label_marker": overall_label,
            "label_margin": overall_margin,
        },
        "criteria": criteria_summary,
    }


def annotate_annotation_plan_with_confidence(
    annotations: List[AnnotationPlan],
    *,
    grade: GradeResult,
    submission_pages: List[Dict[str, Any]],
) -> List[AnnotationPlan]:
    criterion_markers = {criterion.id: criterion.confidence_marker for criterion in grade.criteria}
    updated: List[AnnotationPlan] = []
    for annotation in annotations:
        criterion_marker = criterion_markers.get(annotation.criterion_id or "")
        if criterion_marker in {"review_required", "abstain", "uncertain"}:
            annotation.confidence_marker = criterion_marker
            annotation.confidence_reason = "Linked grading criterion did not clear verification."
            updated.append(annotation)
            continue
        if annotation.evidence_quote:
            match = match_quote_to_submission_pages(
                annotation.evidence_quote,
                submission_pages,
                page_hint=annotation.page,
            )
            if match.get("matched") and float(match.get("match_score") or 0.0) >= 0.9:
                annotation.confidence_marker = "supported"
                annotation.confidence_reason = "Annotation evidence quote matched the submission."
            elif match.get("matched"):
                annotation.confidence_marker = "weak_support"
                annotation.confidence_reason = "Annotation evidence quote only matched fuzzily."
            else:
                annotation.confidence_marker = "review_required"
                annotation.confidence_reason = "Annotation evidence quote did not match the submission."
        else:
            annotation.confidence_marker = "weak_support" if criterion_marker == "supported" else criterion_marker or "uncertain"
            annotation.confidence_reason = "Annotation inherited confidence from the linked grading criterion."
        updated.append(annotation)
    return updated


def filter_annotation_plan_by_confidence(
    annotations: List[AnnotationPlan],
    *,
    allowed_markers: List[ConfidenceMarker],
) -> List[AnnotationPlan]:
    allowed = set(allowed_markers)
    return [annotation for annotation in annotations if annotation.confidence_marker in allowed]


def filter_annotation_plan_for_submission(
    annotations: List[AnnotationPlan],
    *,
    submission_text_source: str,
) -> List[AnnotationPlan]:
    if submission_text_source not in {"vision_direct", "vision_llm_ocr", "vision_text"}:
        return annotations
    return [annotation for annotation in annotations if annotation.tool not in {"highlight", "strike"}]
