import pytest
from pydantic import ValidationError

from sandbox_api.sandboxes.command_models import Step, StepsRequest


def test_step_requires_url_for_goto():
    with pytest.raises(ValidationError):
        Step(action="goto")


def test_step_requires_text_for_type():
    with pytest.raises(ValidationError):
        Step(action="type", selector="#id")


def test_step_requires_wait_ms():
    with pytest.raises(ValidationError):
        Step(action="wait")


def test_step_requires_dom_snapshot_format():
    with pytest.raises(ValidationError):
        Step(action="dom_snapshot", selector="div")


def test_steps_request_requires_steps():
    with pytest.raises(ValidationError):
        StepsRequest(steps=[])


def test_step_allows_optional_name():
    step = Step(action="screenshot", name="after-login")
    assert step.name == "after-login"


def test_step_allows_optional_flag():
    step = Step(action="click", selector="#id", optional=True)
    assert step.optional is True
