from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from .runner_clients import CanvasClient, InternalAuth, LlmClient, PdfExtractor, SandboxClient, SubmissionLock
from .runner_models import AnnotationPlan, BrowserApplyError, GradeStudentArgs, GradeStudentOutcome, SandboxOps
from .runner_parsing import (
    _response_text,
    _response_text_or_none,
    build_annotation_prompt,
    build_assignment_context,
    build_extraction_messages,
    build_grade_prompt,
    build_text_payload,
    encode_image,
    extract_speedgrader_anonymous_id,
    filter_annotation_plan_for_submission,
    find_latest_grade_result,
    load_saved_grade_result,
    parse_annotation_plan,
    parse_extracted_pages_from_content,
    parse_grade_result,
    render_pdf_images_best_effort,
    select_images_for_vision,
    select_pdf,
    strip_html,
    summarize_messages_for_observability,
    summarize_rubric_for_logs,
    truncate_for_logs,
)
from .runner_selectors import DEFAULT_SELECTORS
from .runner_steps import (
    assert_page_contains,
    build_lock_key,
    build_open_steps,
    build_refresh_steps,
    build_speedgrader_steps,
    load_page_state,
    parse_grade_value_from_html,
    wait_for_artifact,
)


logger = logging.getLogger("sandbox.grading.runner")


def run_grade_student(args: GradeStudentArgs, sandbox_ops: Optional[SandboxOps] = None) -> GradeStudentOutcome:
    if not args.canvas_token:
        raise RuntimeError("Canvas token is required for grading.")

    lock_key = build_lock_key(args.course_id, args.assignment_id, args.student_id)
    source_grade_result_path: Optional[Path] = None
    if args.grade_result_path:
        source_grade_result_path = Path(args.grade_result_path).expanduser().resolve()
    elif args.reuse_latest_grade:
        source_grade_result_path = find_latest_grade_result(args.output_dir, lock_key)
        if not source_grade_result_path:
            raise RuntimeError("No previous grade_result.json found to reuse.")

    with SubmissionLock(Path(args.output_dir) / "locks" / f"{lock_key}.lock"):
        run_dir = Path(args.output_dir) / f"{lock_key}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        run_dir.mkdir(parents=True, exist_ok=True)
        logger.info(
            "grading_run start course_id=%s assignment_id=%s student_id=%s run_dir=%s navigation_mode=%s sandbox_id=%s profile_artifact_id=%s",
            args.course_id,
            args.assignment_id,
            args.student_id,
            run_dir,
            args.navigation_mode,
            args.sandbox_id,
            args.profile_artifact_id,
        )
        if source_grade_result_path:
            logger.info("grading_run reuse_saved_grade source_grade_result_path=%s", source_grade_result_path)

        canvas = CanvasClient(args.canvas_base, args.canvas_token)
        course = canvas.get_course(args.course_id)
        assignment = canvas.get_assignment(args.course_id, args.assignment_id)
        student = canvas.get_user(args.student_id, args.course_id)
        submission = canvas.get_submission(args.course_id, args.assignment_id, args.student_id)
        logger.info("grading_run canvas_fetch_done submission_id=%s attachment_count=%s", submission.submission_id, len(submission.attachments))

        course_name = course.get("name") or course.get("course_code") or course.get("sis_course_id") or args.course_id
        assignment_title = assignment.get("name") or "Assignment"
        assignment_instructions = strip_html(assignment.get("description"))
        rubric = assignment.get("rubric") or []
        student_name = student.get("name") or student.get("short_name") or args.student_id
        assignment_attachments = canvas.get_assignment_attachments(assignment)
        anonymous_id = extract_speedgrader_anonymous_id(assignment, submission)
        logger.info("grading_run rubric_loaded count=%s rubric=%s", len(rubric), json.dumps(summarize_rubric_for_logs(rubric), ensure_ascii=True))

        pdf_attachment = select_pdf(submission.attachments)
        source_llm_observability_path = ""
        extractor: Optional[PdfExtractor] = None
        text_pages: List[Dict[str, Any]] = []
        extracted_pages: List[Dict[str, Any]] = []
        extracted_text = ""
        use_vision = False
        evidence_mode = "reused" if source_grade_result_path else "text"
        submission_text_source = "saved_grade_result" if source_grade_result_path else "pdf_text"
        all_submission_images: List[Path] = []
        images: List[Path] = []
        submission_image_render_error: Optional[str] = None
        extraction_messages: Optional[List[Dict[str, Any]]] = None
        extraction_response: Optional[Dict[str, Any]] = None
        extraction_error: Optional[str] = None

        if source_grade_result_path:
            saved = load_saved_grade_result(source_grade_result_path)
            grade = saved["grade"]
            annotation_plan = saved["annotation_plan"]
            assignment_context = saved["assignment_context"]
            assignment_context_sources = saved["assignment_context_sources"]
            extracted_text = saved["submission_text"]
            submission_text_source = saved["submission_text_source"] or "saved_grade_result"
            course_name = saved["course_name"] or course_name
            assignment_title = saved["assignment_title"] or assignment_title
            student_name = saved["student_name"] or student_name
            source_llm_observability_path = saved["source_llm_observability_path"]
            if not args.profile_artifact_id and saved.get("profile_artifact_id"):
                args.profile_artifact_id = str(saved["profile_artifact_id"])
            annotation_plan = filter_annotation_plan_for_submission(annotation_plan, submission_text_source=submission_text_source)
        else:
            pdf_path = run_dir / pdf_attachment.filename
            canvas.download_attachment(pdf_attachment, pdf_path)
            extractor = PdfExtractor(min_text_chars=args.min_text_chars, max_text_chars=args.text_max_chars)
            text_pages = extractor.extract_text(pdf_path)
            use_vision = extractor.should_use_vision(text_pages)
            evidence_mode = "vision" if use_vision else "text"
            submission_text_source = "vision_direct" if use_vision else "pdf_text"
            extracted_text = build_text_payload(text_pages, args.text_max_chars)
            all_submission_images, submission_image_render_error = render_pdf_images_best_effort(
                extractor,
                pdf_path,
                run_dir / "submission_images",
                max_pages=args.vision_max_pages,
            )
            images = select_images_for_vision(all_submission_images)

            # Keep the OCR/extraction pass separate from grading so the grading model
            # can stay text-first even when the submission is image-heavy.
            if use_vision and args.extraction_llm_model:
                try:
                    extraction_llm = LlmClient(
                        args.extraction_llm_base or args.grading_llm_base,
                        args.extraction_llm_key,
                        args.extraction_llm_model,
                    )
                    extraction_messages = build_extraction_messages(images)
                    extraction_response = extraction_llm.chat(
                        extraction_messages,
                        max_tokens=max(1800, len(images) * 700),
                        temperature=0.0,
                    )
                    extracted_pages = parse_extracted_pages_from_content(_response_text(extraction_response, label="Extraction LLM response"))
                    extracted_text = build_text_payload(extracted_pages, args.text_max_chars)
                    if extracted_text:
                        evidence_mode = "vision_text"
                        submission_text_source = "vision_llm_ocr"
                except Exception as exc:
                    extraction_error = str(exc)

            assignment_context, assignment_context_sources = build_assignment_context(
                assignment_instructions=assignment_instructions,
                attachments=assignment_attachments,
                canvas=canvas,
                extractor=extractor,
                out_dir=run_dir / "assignment",
                max_chars=min(args.text_max_chars, 20000),
                vision_max_pages=args.vision_max_pages,
            )

            grading_llm = LlmClient(args.grading_llm_base, args.grading_llm_key, args.grading_llm_model)
            prompt = build_grade_prompt(
                assignment_title=assignment_title,
                assignment_instructions=assignment_instructions,
                assignment_context=assignment_context,
                rubric=rubric,
                policy=args.policy,
                extracted_text=extracted_text or "(No extractable text found.)",
                evidence_mode=evidence_mode,
            )

            # Only fall back to multimodal grading when we do not have usable OCR text.
            if use_vision and evidence_mode == "vision":
                content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
                for image_path in images:
                    content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encode_image(image_path)}"}})
                grading_messages = [{"role": "system", "content": "Return only JSON matching the schema."}, {"role": "user", "content": content}]
            else:
                grading_messages = [{"role": "system", "content": "Return only JSON matching the schema."}, {"role": "user", "content": prompt}]

            raw_response = grading_llm.chat(grading_messages, response_format={"type": "json_object"})
            grade = parse_grade_result(raw_response)
            grading_response_text = _response_text_or_none(raw_response, label="LLM response")

            annotations_enabled = os.getenv("ENABLE_SPEEDGRADER_ANNOTATIONS", "1").lower() not in {"0", "false", "no"}
            annotation_plan: List[AnnotationPlan] = []
            annotation_images: List[Path] = []
            annotation_messages: Optional[List[Dict[str, Any]]] = None
            annotation_response: Optional[Dict[str, Any]] = None
            annotation_error: Optional[str] = None
            max_annotations = max(1, int(os.getenv("SPEEDGRADER_MAX_ANNOTATIONS", "12")))
            annotation_max_pages = max(1, int(os.getenv("SPEEDGRADER_ANNOTATION_MAX_PAGES", str(max(4, args.vision_max_pages)))))

            if annotations_enabled:
                annotation_images = images
                if not annotation_images:
                    fallback_images, _render_error = render_pdf_images_best_effort(
                        extractor,
                        pdf_path,
                        run_dir / "annotation_vision",
                        max_pages=min(args.vision_max_pages, annotation_max_pages),
                        zoom=1.5,
                    )
                    annotation_images = fallback_images
                annotation_images = select_images_for_vision(annotation_images)
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
                        annotation_content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encode_image(image_path)}"}})
                    annotation_messages = [{"role": "system", "content": "Return only JSON."}, {"role": "user", "content": annotation_content}]
                    try:
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
                        annotation_plan = filter_annotation_plan_for_submission(
                            parse_annotation_plan(annotation_response),
                            submission_text_source=submission_text_source,
                        )
                    except Exception as exc:
                        annotation_error = str(exc)
                        annotation_plan = []
            else:
                annotation_images = []
                annotation_messages = None
                annotation_response = None
                annotation_error = None
        if source_grade_result_path:
            annotations_enabled = os.getenv("ENABLE_SPEEDGRADER_ANNOTATIONS", "1").lower() not in {"0", "false", "no"}
            annotation_images = []
            annotation_messages = None
            annotation_response = None
            annotation_error = None
            grading_messages: List[Dict[str, Any]] = []
            grading_response_text = None

        graded_at = datetime.now(timezone.utc).isoformat()
        llm_observability_path = run_dir / "llm_observability.json"
        llm_observability_path.write_text(
            json.dumps(
                {
                    "graded_at": graded_at,
                    "reused_saved_grade": bool(source_grade_result_path),
                    "reused_from_grade_result_path": str(source_grade_result_path) if source_grade_result_path else None,
                    "reused_from_llm_observability_path": source_llm_observability_path or None,
                    "submission_text": {
                        "used_vision": use_vision,
                        "evidence_mode": evidence_mode,
                        "source": submission_text_source,
                        "pdf_text_pages": text_pages,
                        "all_image_paths": [str(path.relative_to(run_dir)) for path in all_submission_images],
                        "vision_image_paths": [str(path.relative_to(run_dir)) for path in images],
                        "image_render_error": submission_image_render_error,
                        "ocr_pages": extracted_pages,
                        "final_text": extracted_text or "(No extractable text found.)",
                        "extraction": {
                            "attempted": bool(extraction_messages),
                            "model": args.extraction_llm_model,
                            "base_url": args.extraction_llm_base or args.grading_llm_base,
                            "messages": summarize_messages_for_observability(extraction_messages or []),
                            "response_text": _response_text_or_none(extraction_response, label="Extraction LLM response"),
                            "error": extraction_error,
                        },
                    },
                    "grading": {
                        "model": args.grading_llm_model,
                        "base_url": args.grading_llm_base,
                        "messages": summarize_messages_for_observability(grading_messages),
                        "response_text": grading_response_text,
                        "parsed_result": grade.dict(exclude_none=True),
                    },
                    "annotations": {
                        "enabled": annotations_enabled,
                        "model": args.annotation_llm_model or args.grading_llm_model,
                        "base_url": args.annotation_llm_base or args.grading_llm_base,
                        "image_paths": [str(path.relative_to(run_dir)) for path in annotation_images],
                        "messages": summarize_messages_for_observability(annotation_messages or []),
                        "response_text": _response_text_or_none(annotation_response, label="Annotation response"),
                        "parsed_plan": [plan.dict(exclude_none=True) for plan in annotation_plan],
                        "error": annotation_error,
                    },
                },
                ensure_ascii=True,
                indent=2,
            ),
            encoding="utf-8",
        )

        grade_payload = {
            "graded_at": graded_at,
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
            "profile_artifact_id": args.profile_artifact_id,
            "assignment_context_sources": assignment_context_sources,
            "assignment_context": assignment_context,
            "annotation_plan": [plan.dict(exclude_none=True) for plan in annotation_plan],
            "attachment": {"filename": pdf_attachment.filename, "url": pdf_attachment.url},
            "evidence_mode": evidence_mode,
            "submission_text_source": submission_text_source,
            "submission_image_paths": [str(path.relative_to(run_dir)) for path in all_submission_images],
            "submission_text": extracted_text or "(No extractable text found.)",
            "reused_from_grade_result_path": str(source_grade_result_path) if source_grade_result_path else None,
            "llm_observability_path": str(llm_observability_path.resolve()),
            "grade": grade.dict(exclude_none=True),
        }
        grade_result_path = run_dir / "grade_result.json"
        grade_result_path.write_text(json.dumps(grade_payload, ensure_ascii=True, indent=2), encoding="utf-8")

        speedgrader_state_path: Optional[Path] = None
        try:
            if sandbox_ops is None:
                if not args.internal_secret:
                    raise RuntimeError("INTERNAL_AUTH_SECRET is required for sandbox API calls.")
                sandbox_ops = SandboxClient(args.sandbox_api, InternalAuth(args.internal_secret, args.agent_id))
            sandbox_id = args.sandbox_id
            if sandbox_id:
                sandbox_ops.wait_ready(sandbox_id, timeout=120)
            else:
                sandbox_id = sandbox_ops.create_sandbox(ttl_seconds=3600)["sandbox_id"]
                sandbox_ops.wait_ready(sandbox_id)

            selectors = DEFAULT_SELECTORS.copy()
            if args.selectors_json:
                selectors_payload = json.loads(args.selectors_json) if isinstance(args.selectors_json, str) else args.selectors_json
                if not isinstance(selectors_payload, dict):
                    raise RuntimeError("selectors_json must be a dict or JSON object.")
                selectors.update(selectors_payload)

            speedgrader_params = {"assignment_id": args.assignment_id, "student_id": args.student_id}
            if anonymous_id:
                speedgrader_params["anonymous_id"] = anonymous_id
            speedgrader_url = f"{args.canvas_base}/courses/{args.course_id}/gradebook/speed_grader?{urlencode(speedgrader_params)}"

            ui_checks: Dict[str, Any] = {"course": None, "assignment": None, "speedgrader_open": None, "speedgrader_refresh": None}
            if args.navigation_mode == "course":
                # Keep these intermediate checks separate from grading so failures
                # point to the exact page where Canvas navigation drifted.
                for label, url, expected in (
                    ("course", f"{args.canvas_base}/courses/{args.course_id}", course_name),
                    ("assignment", f"{args.canvas_base}/courses/{args.course_id}/assignments/{args.assignment_id}", assignment_title),
                ):
                    payload = build_open_steps(url, selectors)
                    if args.profile_artifact_id:
                        payload["profile_artifact_id"] = args.profile_artifact_id
                    receipt = sandbox_ops.run_steps(sandbox_id, payload)
                    artifact = wait_for_artifact(sandbox_ops, receipt["command_id"], "page_state", timeout=90)
                    state = load_page_state(sandbox_ops, artifact, run_dir)
                    ui_checks[label] = {
                        "url": url,
                        "artifact_id": artifact["artifact_id"],
                        "matched": assert_page_contains(state.get("visible_text", ""), expected, label if label == "course" else "assignment title (course page)", strict=args.strict_ui_checks),
                    }

            load_submission_for_annotations = annotations_enabled and bool(annotation_plan)
            steps_payload = build_speedgrader_steps(
                speedgrader_url,
                grade,
                selectors,
                annotations=annotation_plan,
                load_submission=load_submission_for_annotations,
                rubric=rubric,
                apply_rubric=os.getenv("ENABLE_SPEEDGRADER_RUBRIC", "1").lower() not in {"0", "false", "no"},
            )
            if args.profile_artifact_id:
                steps_payload["profile_artifact_id"] = args.profile_artifact_id

            open_payload = build_open_steps(speedgrader_url, selectors)
            if args.profile_artifact_id:
                open_payload["profile_artifact_id"] = args.profile_artifact_id
            open_receipt = sandbox_ops.run_steps(sandbox_id, open_payload)
            open_state_artifact = wait_for_artifact(sandbox_ops, open_receipt["command_id"], "page_state", timeout=90)
            open_state_payload = load_page_state(sandbox_ops, open_state_artifact, run_dir)
            visible_text = open_state_payload.get("visible_text", "")
            ui_checks["speedgrader_open"] = {
                "url": speedgrader_url,
                "artifact_id": open_state_artifact["artifact_id"],
                "assignment_match": assert_page_contains(visible_text, assignment_title, "assignment title", strict=args.strict_ui_checks),
                "student_match": assert_page_contains(visible_text, student_name, "student name", strict=args.strict_ui_checks),
            }

            receipt = sandbox_ops.run_steps(sandbox_id, steps_payload)
            wait_for_artifact(sandbox_ops, receipt["command_id"], "page_state", timeout=90)

            refresh_payload = build_refresh_steps(speedgrader_url, selectors)
            if args.profile_artifact_id:
                refresh_payload["profile_artifact_id"] = args.profile_artifact_id
            refresh_receipt = sandbox_ops.run_steps(sandbox_id, refresh_payload)
            refresh_state = wait_for_artifact(sandbox_ops, refresh_receipt["command_id"], "page_state", timeout=90)
            refresh_page_payload = load_page_state(sandbox_ops, refresh_state, run_dir)
            refresh_text = refresh_page_payload.get("visible_text", "")
            ui_checks["speedgrader_refresh"] = {
                "artifact_id": refresh_state["artifact_id"],
                "assignment_match": assert_page_contains(refresh_text, assignment_title, "assignment title (refresh)", strict=args.strict_ui_checks),
                "student_match": assert_page_contains(refresh_text, student_name, "student name (refresh)", strict=args.strict_ui_checks),
            }

            snapshot_artifact = wait_for_artifact(sandbox_ops, refresh_receipt["command_id"], "dom_snapshot", timeout=90)
            snapshot_path = run_dir / f"{snapshot_artifact['artifact_id']}.html"
            sandbox_ops.download_artifact_blob(snapshot_artifact["artifact_id"], snapshot_path)
            actual_value = parse_grade_value_from_html(snapshot_path.read_text(encoding="utf-8"))
            if actual_value is None:
                raise RuntimeError("Could not find grade value in SpeedGrader DOM snapshot.")
            try:
                actual_points = float(actual_value)
                expected_points = float(grade.total_points)
            except ValueError as exc:
                raise RuntimeError(f"Grade verification failed: non-numeric value '{actual_value}'.") from exc
            if abs(actual_points - expected_points) > 0.01:
                raise RuntimeError(f"Grade verification failed: expected {expected_points}, found {actual_points}.")

            ui_checks["speedgrader_refresh"]["grade_value"] = actual_value
            grade_payload["ui_checks"] = ui_checks
            grade_result_path.write_text(json.dumps(grade_payload, ensure_ascii=True, indent=2), encoding="utf-8")

            speedgrader_state_path = run_dir / "speedgrader_state.json"
            speedgrader_state_path.write_text(json.dumps(refresh_page_payload, ensure_ascii=True, indent=2), encoding="utf-8")
            return GradeStudentOutcome(
                run_dir=run_dir,
                grade_result_path=grade_result_path,
                speedgrader_state_path=speedgrader_state_path,
                llm_observability_path=llm_observability_path,
            )
        except Exception as exc:
            raise BrowserApplyError(
                str(exc),
                run_dir=run_dir,
                grade_result_path=grade_result_path,
                llm_observability_path=llm_observability_path,
                speedgrader_state_path=speedgrader_state_path,
            ) from exc


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
    parser.add_argument("--grading-llm-base", default=os.getenv("GRADING_LLM_BASE") or os.getenv("FIREWORKS_API_BASE") or os.getenv("LLM_API_BASE") or "https://api.fireworks.ai/inference/v1")
    parser.add_argument("--grading-llm-key", default=os.getenv("GRADING_LLM_API_KEY") or os.getenv("FIREWORKS_API_KEY") or os.getenv("LLM_API_KEY"))
    parser.add_argument("--grading-llm-model", default=os.getenv("GRADING_LLM_MODEL") or os.getenv("FIREWORKS_GRADING_MODEL") or os.getenv("LLM_MODEL") or "accounts/fireworks/models/kimi-k2p5")
    parser.add_argument("--annotation-llm-base", default=os.getenv("ANNOTATION_LLM_BASE"))
    parser.add_argument("--annotation-llm-key", default=os.getenv("ANNOTATION_LLM_API_KEY"))
    parser.add_argument("--annotation-llm-model", default=os.getenv("ANNOTATION_LLM_MODEL"))
    parser.add_argument("--grade-result-path", default=None, help="Reuse an existing grade_result.json and skip LLM stages.")
    parser.add_argument("--reuse-latest-grade", action="store_true", help="Reuse the latest saved grade_result.json for this course/assignment/student.")
    parser.add_argument("--navigation-mode", choices=["course", "direct"], default="course")
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
        grading_llm_model=args.grading_llm_model or args.llm_model or "accounts/fireworks/models/kimi-k2p5",
        annotation_llm_base=args.annotation_llm_base,
        annotation_llm_key=args.annotation_llm_key,
        annotation_llm_model=args.annotation_llm_model,
        grade_result_path=args.grade_result_path,
        reuse_latest_grade=args.reuse_latest_grade,
        navigation_mode=args.navigation_mode,
        strict_ui_checks=args.strict_ui_checks,
    )


def main(argv: Optional[List[str]] = None) -> int:
    run_grade_student(_parse_args(argv))
    return 0
