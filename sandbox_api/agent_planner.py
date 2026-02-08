from __future__ import annotations

import json
import os
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel, ValidationError

from .command_models import AgentStepsRequest, Step, StepsRequest


class PlannerResponse(BaseModel):
    steps: list[Step]
    screenshot_every_step: bool = False
    notes: str | None = None


def _llm_provider(request: AgentStepsRequest) -> str:
    return (request.llm.provider if request.llm and request.llm.provider else None) or os.getenv(
        "LLM_PROVIDER", "openai"
    )


def _llm_model(request: AgentStepsRequest) -> str:
    return (request.llm.model if request.llm and request.llm.model else None) or os.getenv(
        "LLM_MODEL", "gpt-4o-mini"
    )


def _llm_temperature(request: AgentStepsRequest) -> float:
    if request.llm and request.llm.temperature is not None:
        return float(request.llm.temperature)
    return float(os.getenv("LLM_TEMPERATURE", "0.0"))


def _call_openai(payload: dict[str, Any], *, timeout: int) -> dict[str, Any]:
    api_key = os.getenv("LLM_API_KEY", "")
    if not api_key:
        raise RuntimeError("LLM_API_KEY is required for OpenAI provider.")
    base = os.getenv("LLM_API_BASE", "https://api.openai.com/v1").rstrip("/")
    url = f"{base}/chat/completions"
    body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    req = Request(url, data=body, headers=headers, method="POST")
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _build_prompt(
    request: AgentStepsRequest,
    page_context: dict[str, Any],
) -> str:
    payload = {
        "task": request.task,
        "raw_steps": [step.dict(exclude_none=True, by_alias=True) for step in (request.steps or [])],
        "page_context": page_context,
        "max_steps": request.max_steps,
        "allowed_actions": [
            "goto",
            "click",
            "type",
            "type_rce",
            "wait",
            "wait_for_selector",
            "screenshot",
            "dom_snapshot",
            "page_state",
            "draw_path",
            "draw_rect",
            "point",
            "freetext",
        ],
        "selector_fields": [
            "selector",
            "selector_fallbacks",
            "role",
            "role_name",
            "label",
            "placeholder",
            "target_text",
        ],
    }
    return (
        "You are a browser automation planner. Convert the task and raw steps into a clean, "
        "robust step list that is compatible with the schema. Insert wait_for_selector "
        "before click/type when needed, and add selector_fallbacks or role/label/placeholder "
        "targets to reduce flakiness. Use role_name for accessible role names; name is only "
        "for naming screenshots/artifacts. Return JSON only with keys: steps, "
        "screenshot_every_step, notes. Steps must use allowed actions.\n\n"
        f"INPUT JSON:\n{json.dumps(payload, ensure_ascii=True)}"
    )


def plan_steps(
    *,
    request: AgentStepsRequest,
    page_context: dict[str, Any],
    log: Callable[[str], None],
) -> StepsRequest:
    provider = _llm_provider(request)
    if provider in {"none", "disabled", "off"}:
        if not request.steps:
            raise RuntimeError("LLM disabled and no steps provided.")
        return StepsRequest(steps=request.steps, screenshot_every_step=request.screenshot_every_step)

    prompt = _build_prompt(request, page_context)
    payload = {
        "model": _llm_model(request),
        "temperature": _llm_temperature(request),
        "messages": [
            {
                "role": "system",
                "content": "Return only valid JSON matching the schema.",
            },
            {"role": "user", "content": prompt},
        ],
        "response_format": {"type": "json_object"},
    }
    timeout = int(os.getenv("LLM_TIMEOUT_SECONDS", "60"))
    try:
        if provider == "openai":
            response = _call_openai(payload, timeout=timeout)
        else:
            raise RuntimeError(f"Unsupported LLM provider: {provider}")
    except (HTTPError, URLError, RuntimeError) as exc:
        log(f"LLM request failed: {exc}")
        if request.steps:
            return StepsRequest(steps=request.steps, screenshot_every_step=request.screenshot_every_step)
        raise

    content = ""
    try:
        content = response["choices"][0]["message"]["content"]
        data = json.loads(content)
        parsed = PlannerResponse.parse_obj(data)
    except (KeyError, json.JSONDecodeError, ValidationError) as exc:
        log(f"LLM response parse error: {exc}")
        if request.steps:
            return StepsRequest(steps=request.steps, screenshot_every_step=request.screenshot_every_step)
        raise RuntimeError("LLM returned invalid steps.") from exc

    steps = parsed.steps[: request.max_steps]
    return StepsRequest(
        steps=steps,
        screenshot_every_step=parsed.screenshot_every_step or request.screenshot_every_step,
    )
