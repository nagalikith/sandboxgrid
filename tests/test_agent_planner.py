import json

import pytest

from sandbox_api.sandboxes.agent_planner import _build_prompt, _call_openai, plan_steps
from sandbox_api.sandboxes.command_models import AgentLlmConfig, AgentStepsRequest, Step


def test_build_prompt_includes_fields():
    request = AgentStepsRequest(task="Open", steps=[Step(action="page_state")])
    prompt = _build_prompt(request, {"url": "https://example.com"})
    assert "allowed_actions" in prompt
    assert "Open" in prompt


def test_call_openai_requires_api_key(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        _call_openai({"model": "gpt"}, timeout=1)


def test_plan_steps_disabled_requires_steps(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "off")
    request = AgentStepsRequest(steps=[Step(action="page_state")])
    result = plan_steps(request=request, page_context={}, log=lambda _msg: None)
    assert len(result.steps) == 1

    with pytest.raises(RuntimeError):
        plan_steps(
            request=AgentStepsRequest(task="Task", steps=None),
            page_context={},
            log=lambda _msg: None,
        )


def test_plan_steps_uses_llm_response(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    payload = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "steps": [{"action": "page_state"}],
                            "screenshot_every_step": True,
                        }
                    )
                }
            }
        ]
    }

    def fake_call(_payload, timeout):
        assert timeout
        return payload

    monkeypatch.setattr("sandbox_api.agent_planner._call_openai", fake_call)
    request = AgentStepsRequest(task="Do", steps=None)
    result = plan_steps(request=request, page_context={}, log=lambda _msg: None)
    assert result.screenshot_every_step
    assert result.steps[0].action == "page_state"


def test_plan_steps_fallback_on_error(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")

    def fake_call(_payload, timeout):
        return {"choices": [{"message": {"content": "bad json"}}]}

    monkeypatch.setattr("sandbox_api.agent_planner._call_openai", fake_call)
    request = AgentStepsRequest(task="Do", steps=[Step(action="page_state")])
    result = plan_steps(request=request, page_context={}, log=lambda _msg: None)
    assert len(result.steps) == 1


def test_plan_steps_unsupported_provider_fallback(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "custom")
    request = AgentStepsRequest(task="Task", steps=[Step(action="page_state")])
    result = plan_steps(request=request, page_context={}, log=lambda _msg: None)
    assert result.steps


def test_llm_config_overrides():
    request = AgentStepsRequest(
        task="Task",
        steps=[Step(action="page_state")],
        llm=AgentLlmConfig(provider="openai", model="test", temperature=0.5),
    )
    assert request.llm.provider == "openai"
