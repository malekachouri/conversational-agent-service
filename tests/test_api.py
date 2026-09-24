"""API contract and observability checks."""

import re
import uuid


def _metric(text: str, name: str, labels: str = "") -> float:
    pattern = rf"^{re.escape(name)}{re.escape(labels)} (\S+)$"
    match = re.search(pattern, text, re.M)
    return float(match.group(1)) if match else 0.0


def test_healthz(api_client):
    assert api_client.get("/healthz").json() == {"status": "ok"}


def test_chat_validates_input(api_client):
    assert api_client.post("/chat", json={"session_id": "s", "message": ""}).status_code == 422
    assert api_client.post("/chat", json={"message": "hi"}).status_code == 422


def test_unknown_session_is_404(api_client):
    assert api_client.get(f"/sessions/does-not-exist-{uuid.uuid4()}").status_code == 404


def test_history_persists_across_turns(api_client):
    session_id = f"test-history-{uuid.uuid4().hex[:8]}"
    api_client.post("/chat", json={"session_id": session_id, "message": "Hello there"})
    first = api_client.get(f"/sessions/{session_id}").json()["message_count"]
    api_client.post("/chat", json={"session_id": session_id, "message": "Status of ORD-1002?"})
    second = api_client.get(f"/sessions/{session_id}").json()["message_count"]
    assert first >= 2  # user + assistant
    assert second >= first + 2


def test_metrics_track_requests_and_tool_outcomes(api_client):
    before = api_client.get("/metrics").text
    resp = api_client.post(
        "/chat", json={"session_id": f"test-metrics-{uuid.uuid4().hex[:8]}", "message": "Where is ORD-1001?"}
    )
    assert resp.status_code == 200
    after = api_client.get("/metrics").text

    assert _metric(after, "chat_requests_total", '{status="success"}') == (
        _metric(before, "chat_requests_total", '{status="success"}') + 1
    )
    tool_label = '{outcome="success",tool="check_order_status"}'
    assert _metric(after, "agent_tool_calls_total", tool_label) > _metric(
        before, "agent_tool_calls_total", tool_label
    )
    assert _metric(after, "chat_request_duration_seconds_count") > _metric(
        before, "chat_request_duration_seconds_count"
    )
