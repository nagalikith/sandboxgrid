import pytest
from pydantic import ValidationError

from sandbox_api.sandboxes.command_models import AgentStepsRequest, CaptureProfileRequest, Step


def test_page_state_step_requires_no_target():
    step = Step(action="page_state")
    assert step.action == "page_state"


def test_click_requires_target():
    with pytest.raises(ValidationError):
        Step(action="click")


def test_click_accepts_role_target():
    step = Step(action="click", role="button", role_name="Submit")
    assert step.role == "button"
    assert step.role_name == "Submit"


def test_agent_requires_task_or_steps():
    with pytest.raises(ValidationError):
        AgentStepsRequest()


def test_agent_accepts_task_only():
    req = AgentStepsRequest(task="Open homepage")
    assert req.task == "Open homepage"


def test_agent_accepts_steps_only():
    req = AgentStepsRequest(steps=[Step(action="page_state")])
    assert req.steps


def test_capture_profile_accepts_name():
    req = CaptureProfileRequest(name="user-profile")
    assert req.name == "user-profile"
