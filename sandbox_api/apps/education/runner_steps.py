from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .runner_models import AnnotationPlan, GradeResult, SandboxOps


def build_annotation_steps(
    annotations: List[AnnotationPlan],
    selectors: Dict[str, List[str]],
) -> List[Dict[str, Any]]:
    if not annotations:
        return []

    def selector_fallbacks(values: Iterable[str]) -> Tuple[str, List[str]]:
        items = [value for value in values if value]
        return (items[0], items[1:]) if items else ("body", [])

    preview_iframe_sel, preview_iframe_fallbacks = selector_fallbacks(selectors.get("submission_preview_iframe", []))

    def with_frame_target(step: Dict[str, Any]) -> Dict[str, Any]:
        if preview_iframe_sel != "body":
            step["frame_selector"] = preview_iframe_sel
            if preview_iframe_fallbacks:
                step["frame_selector_fallbacks"] = preview_iframe_fallbacks
        return step

    def with_skip_if_annotation_exists(
        step: Dict[str, Any],
        *,
        target_sel: str,
        target_fallbacks: List[str],
    ) -> Dict[str, Any]:
        skip_selectors = [f"{target_sel} .PageAnnotations > *"]
        skip_selectors.extend(f"{selector} .PageAnnotations > *" for selector in target_fallbacks)
        skip_selectors.extend(
            [
                f"{target_sel} .PageContainerComments .CommentContainerOffset:not([style*='height: 0'])",
                f"{target_sel} [data-annotation-id]",
            ]
        )
        step["skip_if_selector"] = skip_selectors[0]
        if len(skip_selectors) > 1:
            step["skip_if_selector_fallbacks"] = skip_selectors[1:]
        return step

    def optional_frame_step(step: Dict[str, Any]) -> Dict[str, Any]:
        step["optional"] = True
        return with_frame_target(step)

    def format_page_template(template: str, page: int) -> str:
        return template.format(page=page, page_zero_based=max(page - 1, 0))

    steps: List[Dict[str, Any]] = []
    if preview_iframe_sel != "body":
        steps.append({"action": "wait_for_selector", "selector": preview_iframe_sel, "selector_fallbacks": preview_iframe_fallbacks})

    toolbar_sel, toolbar_fallbacks = selector_fallbacks(selectors.get("annotation_toolbar", []))
    if toolbar_sel != "body":
        steps.append(with_frame_target({"action": "wait_for_selector", "selector": toolbar_sel, "selector_fallbacks": toolbar_fallbacks}))

    zoom_out_sel, zoom_out_fallbacks = selector_fallbacks(selectors.get("annotation_zoom_out", []))
    if zoom_out_sel != "body":
        for _ in range(2):
            steps.append(
                optional_frame_step(
                    {"action": "click", "selector": zoom_out_sel, "selector_fallbacks": zoom_out_fallbacks, "timeout_ms": 500}
                )
            )
        steps.append({"action": "wait", "wait_ms": 150})

    canvas_sel, canvas_fallbacks = selector_fallbacks(selectors.get("annotation_canvas", []))
    draft_modal_sel, draft_modal_fallbacks = selector_fallbacks(selectors.get("annotation_draft_modal", []))
    draft_checkbox_sel, draft_checkbox_fallbacks = selector_fallbacks(selectors.get("annotation_draft_checkbox", []))
    draft_proceed_sel, draft_proceed_fallbacks = selector_fallbacks(selectors.get("annotation_draft_proceed", []))
    page_templates = selectors.get("annotation_page", [])
    last_tool_key: Optional[str] = None
    last_color_key: Optional[str] = None
    draft_prompt_handled = False

    def append_draft_prompt_steps() -> None:
        nonlocal draft_prompt_handled
        if draft_prompt_handled:
            return
        if draft_modal_sel != "body":
            steps.append(
                optional_frame_step(
                    {
                        "action": "wait_for_selector",
                        "selector": draft_modal_sel,
                        "selector_fallbacks": draft_modal_fallbacks,
                        "timeout_ms": 400,
                    }
                )
            )
        if draft_checkbox_sel != "body":
            steps.append(
                optional_frame_step(
                    {"action": "click", "selector": draft_checkbox_sel, "selector_fallbacks": draft_checkbox_fallbacks, "timeout_ms": 500}
                )
            )
        if draft_proceed_sel != "body":
            steps.append(
                optional_frame_step(
                    {"action": "click", "selector": draft_proceed_sel, "selector_fallbacks": draft_proceed_fallbacks, "timeout_ms": 500}
                )
            )
        draft_prompt_handled = True

    for annotation in annotations:
        target_sel = canvas_sel
        target_fallbacks = canvas_fallbacks
        skip_target_fallbacks = canvas_fallbacks
        if page_templates and annotation.page:
            formatted = [format_page_template(template, annotation.page) for template in page_templates if template]
            if formatted:
                target_sel = formatted[0]
                target_fallbacks = formatted[1:] + [canvas_sel] + canvas_fallbacks
                skip_target_fallbacks = formatted[1:]

        def frame_annotation_step(step: Dict[str, Any]) -> Dict[str, Any]:
            return with_frame_target(step)

        def guarded_annotation_step(step: Dict[str, Any]) -> Dict[str, Any]:
            return with_skip_if_annotation_exists(
                with_frame_target(step),
                target_sel=target_sel,
                target_fallbacks=skip_target_fallbacks,
            )

        tool_key = {
            "free_draw": "annotation_free_draw",
            "highlight": "annotation_highlight",
            "strike": "annotation_strike",
            "point": "annotation_point",
            "freetext": "annotation_freetext",
            "area": "annotation_area",
            "select": "annotation_select",
        }.get(annotation.tool)

        if tool_key and tool_key != last_tool_key and selectors.get(tool_key):
            tool_sel, tool_fallbacks = selector_fallbacks(selectors.get(tool_key, []))
            if tool_sel != "body":
                steps.append(frame_annotation_step({"action": "click", "selector": tool_sel, "selector_fallbacks": tool_fallbacks}))
                last_tool_key = tool_key

        color = (annotation.color or "").lower()
        if annotation.tool == "highlight" and not color:
            color = "yellow"
        if color and annotation.tool != "select":
            if selectors.get("annotation_color_button"):
                color_button_sel, color_button_fallbacks = selector_fallbacks(selectors.get("annotation_color_button", []))
                if color_button_sel != "body":
                    steps.append(frame_annotation_step({"action": "click", "selector": color_button_sel, "selector_fallbacks": color_button_fallbacks}))
            color_key = f"annotation_color_{color}"
            if color_key != last_color_key and selectors.get(color_key):
                color_sel, color_fallbacks = selector_fallbacks(selectors.get(color_key, []))
                if color_sel != "body":
                    steps.append(frame_annotation_step({"action": "click", "selector": color_sel, "selector_fallbacks": color_fallbacks}))
                    last_color_key = color_key

        if annotation.tool in {"free_draw", "strike", "highlight"}:
            steps.append(
                guarded_annotation_step(
                    {
                        "action": "draw_path",
                        "selector": target_sel,
                        "selector_fallbacks": target_fallbacks,
                        "points": [{"x": point.x, "y": point.y} for point in annotation.path],
                    }
                )
            )
            steps.append({"action": "wait", "wait_ms": 200})
            append_draft_prompt_steps()
        elif annotation.tool == "area":
            steps.append(
                guarded_annotation_step(
                    {
                        "action": "draw_rect",
                        "selector": target_sel,
                        "selector_fallbacks": target_fallbacks,
                        "points": [{"x": point.x, "y": point.y} for point in annotation.path[:2]],
                    }
                )
            )
            steps.append({"action": "wait", "wait_ms": 200})
            append_draft_prompt_steps()
        elif annotation.tool == "point" and annotation.point is not None:
            steps.append(
                guarded_annotation_step(
                    {
                        "action": "point",
                        "selector": target_sel,
                        "selector_fallbacks": target_fallbacks,
                        "point": {"x": annotation.point.x, "y": annotation.point.y},
                    }
                )
            )
            steps.append({"action": "wait", "wait_ms": 150})
            append_draft_prompt_steps()
        elif annotation.tool == "freetext" and annotation.point is not None and annotation.text:
            steps.append(
                guarded_annotation_step(
                    {
                        "action": "freetext",
                        "selector": target_sel,
                        "selector_fallbacks": target_fallbacks,
                        "point": {"x": annotation.point.x, "y": annotation.point.y},
                        "text": annotation.text,
                    }
                )
            )
            steps.append({"action": "wait", "wait_ms": 200})
            append_draft_prompt_steps()
    return steps


def _grading_screenshot_every_step() -> bool:
    return os.getenv("GRADING_SCREENSHOT_EVERY_STEP", "1").lower() not in {"0", "false", "no"}


def _format_points(points: float) -> str:
    return str(int(points)) if float(points).is_integer() else f"{points:.2f}".rstrip("0").rstrip(".")


def _normalize_rubric_text(value: str) -> str:
    cleaned = " ".join(value.split())
    return f"{cleaned[:77]}..." if len(cleaned) > 80 else cleaned


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


def _build_rubric_points_selectors(criterion_id: str, rows: List[str], inputs: List[str]) -> List[str]:
    selectors = [f"input[data-testid='criterion-score-{criterion_id}']"]
    selectors.extend(_build_scoped_selectors(rows, inputs))
    return selectors


def _build_rubric_comment_steps(criterion_id: str, comment_text: str) -> List[Dict[str, Any]]:
    if not comment_text:
        return []
    return [
        {"action": "click", "selector": f"button[data-testid='toggle-comment-{criterion_id}']", "optional": True, "timeout_ms": 500},
        {
            "action": "type",
            "selector": f"textarea[data-testid='criterion-comment-{criterion_id}']",
            "selector_fallbacks": [
                f"[data-testid='criterion-comment-{criterion_id}'] textarea",
                f"[data-testid='criterion-comment-{criterion_id}'] [contenteditable='true']",
            ],
            "text": comment_text,
            "optional": True,
            "timeout_ms": 1000,
        },
    ]


def build_rubric_steps(
    grade: GradeResult,
    rubric: List[Dict[str, Any]],
    selectors: Dict[str, List[str]],
) -> List[Dict[str, Any]]:
    def selector_fallbacks(values: Iterable[str]) -> Tuple[str, List[str]]:
        items = [value for value in values if value]
        return (items[0], items[1:]) if items else ("body", [])

    rubric_button_sel, rubric_button_fallbacks = selector_fallbacks(selectors.get("rubric_button", []))
    if rubric_button_sel == "body":
        return []

    steps: List[Dict[str, Any]] = [{"action": "click", "selector": rubric_button_sel, "selector_fallbacks": rubric_button_fallbacks}]

    rubric_panel_sel, rubric_panel_fallbacks = selector_fallbacks(selectors.get("rubric_panel", []))
    if rubric_panel_sel != "body":
        steps.append({"action": "wait_for_selector", "selector": rubric_panel_sel, "selector_fallbacks": rubric_panel_fallbacks})

    row_bases = selectors.get("rubric_row", []) or []
    points_inputs = selectors.get("rubric_points_input", []) or []
    comment_inputs = selectors.get("rubric_comment_input", []) or []
    rubric_map = _rubric_lookup(rubric)

    for criterion in grade.criteria:
        criterion_id = str(criterion.id)
        label = _escape_has_text(_rubric_label(rubric_map.get(criterion_id), criterion_id))
        rows = [f"{base}:has-text(\"{label}\")" for base in row_bases if base and base != "body"] or [f"*:has-text(\"{label}\")"]
        points_selectors = _build_rubric_points_selectors(criterion_id, rows, points_inputs)
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
            steps.extend(_build_rubric_comment_steps(criterion_id, comment_text))
            comment_selectors = _build_scoped_selectors(rows, comment_inputs)
            if comment_selectors:
                steps.append(
                    {
                        "action": "type",
                        "selector": comment_selectors[0],
                        "selector_fallbacks": comment_selectors[1:],
                        "text": comment_text,
                        "optional": True,
                        "timeout_ms": 1000,
                    }
                )

    rubric_save_sel, rubric_save_fallbacks = selector_fallbacks(selectors.get("rubric_save", []))
    if rubric_save_sel != "body":
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
        items = [value for value in values if value]
        return (items[0], items[1:]) if items else ("body", [])

    page_ready_sel, page_ready_fallbacks = selector_fallbacks(selectors.get("page_ready", []))
    grade_sel, grade_fallbacks = selector_fallbacks(selectors.get("grade_input", []))
    comment_sel, comment_fallbacks = selector_fallbacks(selectors.get("comment_box", []))
    comment_iframe_sel, comment_iframe_fallbacks = selector_fallbacks(selectors.get("comment_iframe", []))
    comment_submit_sel, comment_submit_fallbacks = selector_fallbacks(selectors.get("comment_submit", []))
    save_sel, save_fallbacks = selector_fallbacks(selectors.get("save_indicator", []))
    attachments_container_sel, attachments_container_fallbacks = selector_fallbacks(selectors.get("submission_attachments_container", []))
    attachments_list_sel, attachments_list_fallbacks = selector_fallbacks(selectors.get("submission_attachments_list", []))
    attachment_link_sel, attachment_link_fallbacks = selector_fallbacks(selectors.get("submission_attachment_link", []))
    preview_iframe_sel, preview_iframe_fallbacks = selector_fallbacks(selectors.get("submission_preview_iframe", []))
    annotation_canvas_sel, annotation_canvas_fallbacks = selector_fallbacks(selectors.get("annotation_canvas", []))

    steps: List[Dict[str, Any]] = [
        {"action": "goto", "url": url},
        {"action": "wait_for_selector", "selector": page_ready_sel, "selector_fallbacks": page_ready_fallbacks},
    ]

    if load_submission:
        if attachments_container_sel != "body":
            steps.append({"action": "wait_for_selector", "selector": attachments_container_sel, "selector_fallbacks": attachments_container_fallbacks})
        if attachments_list_sel != "body":
            steps.append({"action": "wait_for_selector", "selector": attachments_list_sel, "selector_fallbacks": attachments_list_fallbacks})
        if attachment_link_sel != "body":
            steps.append({"action": "click", "selector": attachment_link_sel, "selector_fallbacks": attachment_link_fallbacks})
            if preview_iframe_sel != "body":
                steps.append({"action": "wait_for_selector", "selector": preview_iframe_sel, "selector_fallbacks": preview_iframe_fallbacks})
            if annotation_canvas_sel != "body":
                steps.append(
                    {
                        "action": "wait_for_selector",
                        "selector": annotation_canvas_sel,
                        "selector_fallbacks": annotation_canvas_fallbacks,
                        "frame_selector": preview_iframe_sel if preview_iframe_sel != "body" else None,
                        "frame_selector_fallbacks": preview_iframe_fallbacks if preview_iframe_sel != "body" else None,
                    }
                )

    steps.extend(build_annotation_steps(annotations or [], selectors))
    if apply_rubric and rubric:
        steps.extend(build_rubric_steps(grade, rubric, selectors))

    steps.append({"action": "type", "selector": grade_sel, "selector_fallbacks": grade_fallbacks, "text": str(grade.total_points)})
    if comment_iframe_sel != "body":
        steps.append({"action": "type_rce", "selector": comment_iframe_sel, "selector_fallbacks": comment_iframe_fallbacks, "text": grade.overall_feedback})
    else:
        steps.append({"action": "type", "selector": comment_sel, "selector_fallbacks": comment_fallbacks, "text": grade.overall_feedback})
    if comment_submit_sel != "body":
        steps.append({"action": "click", "selector": comment_submit_sel, "selector_fallbacks": comment_submit_fallbacks})
    steps.extend(
        [
            {"action": "wait_for_selector", "selector": save_sel, "selector_fallbacks": save_fallbacks},
            {"action": "wait", "wait_ms": 1500},
            {"action": "page_state"},
        ]
    )
    return {"steps": steps, "screenshot_every_step": _grading_screenshot_every_step()}


def build_open_steps(url: str, selectors: Dict[str, List[str]]) -> Dict[str, Any]:
    page_ready_sel, page_ready_fallbacks = _selector_fallbacks(selectors.get("page_ready", []))
    return {
        "steps": [
            {"action": "goto", "url": url},
            {"action": "wait_for_selector", "selector": page_ready_sel, "selector_fallbacks": page_ready_fallbacks},
            {"action": "page_state"},
        ],
        "screenshot_every_step": _grading_screenshot_every_step(),
    }


def build_refresh_steps(url: str, selectors: Dict[str, List[str]]) -> Dict[str, Any]:
    page_ready_sel, page_ready_fallbacks = _selector_fallbacks(selectors.get("page_ready", []))
    grade_sel, grade_fallbacks = _selector_fallbacks(selectors.get("grade_input", []))
    return {
        "steps": [
            {"action": "goto", "url": url},
            {"action": "wait_for_selector", "selector": page_ready_sel, "selector_fallbacks": page_ready_fallbacks},
            {"action": "page_state"},
            {"action": "dom_snapshot", "selector": grade_sel, "selector_fallbacks": grade_fallbacks, "format": "html"},
        ],
        "screenshot_every_step": _grading_screenshot_every_step(),
    }


def _selector_fallbacks(values: Iterable[str]) -> Tuple[str, List[str]]:
    items = [value for value in values if value]
    return (items[0], items[1:]) if items else ("body", [])


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
    return match.group(1).strip() if match else None


def build_lock_key(course_id: str, assignment_id: str, student_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", f"{course_id}_{assignment_id}_{student_id}")
