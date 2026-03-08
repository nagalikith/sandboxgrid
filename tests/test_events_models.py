from sandbox_api.platform.core.events_models import AgentEventPayload


def test_agent_event_payload_allows_extra():
    payload = AgentEventPayload(type="status", extra_field="value")
    assert payload.type == "status"
    assert payload.extra_field == "value"
