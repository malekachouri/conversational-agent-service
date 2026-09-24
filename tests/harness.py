"""Conversation replay harness.

Replays a scenario's user turns against POST /chat (in-process or over HTTP)
and checks *outcomes* - which tools ran, with what arguments, the session's
end state, and loose reply content - never exact wording. That keeps the
suite meaningful against a non-deterministic LLM.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
SCENARIO_DIR = FIXTURES_DIR / "conversations"
SCENARIO_SCHEMA = json.loads((FIXTURES_DIR / "scenario.schema.json").read_text())


@dataclass
class Transcript:
    session_id: str
    turns: list[dict[str, Any]] = field(default_factory=list)  # {user, reply, tool_calls}
    session_state: dict[str, Any] | None = None

    @property
    def tool_calls(self) -> list[dict[str, Any]]:
        return [call for turn in self.turns for call in turn["tool_calls"]]

    @property
    def tools_used(self) -> set[str]:
        return {call["name"] for call in self.tool_calls}

    @property
    def final_reply(self) -> str:
        return self.turns[-1]["reply"] if self.turns else ""

    def render(self) -> str:
        lines = [f"session {self.session_id}"]
        for turn in self.turns:
            lines.append(f"  USER:  {turn['user']}")
            for call in turn["tool_calls"]:
                lines.append(f"  TOOL:  {call['name']}({json.dumps(call['input'])}) -> {json.dumps(call['output'])}")
            lines.append(f"  AGENT: {turn['reply']}")
        return "\n".join(lines)


def load_scenarios() -> list[dict[str, Any]]:
    scenarios = []
    for path in sorted(SCENARIO_DIR.glob("*.json")):
        scenario = json.loads(path.read_text())
        jsonschema.validate(scenario, SCENARIO_SCHEMA)
        scenarios.append(scenario)
    return scenarios


def replay(client, scenario: dict[str, Any]) -> Transcript:
    """Send each user turn in order on a fresh session; ``client`` is httpx-compatible."""
    transcript = Transcript(session_id=f"test-{scenario['name']}-{uuid.uuid4().hex[:8]}")
    for message in scenario["turns"]:
        resp = client.post("/chat", json={"session_id": transcript.session_id, "message": message})
        assert resp.status_code == 200, f"POST /chat failed ({resp.status_code}): {resp.text}"
        body = resp.json()
        transcript.turns.append(
            {"user": message, "reply": body["reply"], "tool_calls": body["tool_calls"]}
        )

    resp = client.get(f"/sessions/{transcript.session_id}")
    assert resp.status_code == 200, f"GET /sessions failed ({resp.status_code}): {resp.text}"
    transcript.session_state = resp.json()
    return transcript


def check_outcomes(transcript: Transcript, expected: dict[str, Any]) -> list[str]:
    """Return a list of human-readable violations (empty means the scenario passed)."""
    failures: list[str] = []
    used = transcript.tools_used

    for tool in expected.get("tools_called", []):
        if tool not in used:
            failures.append(f"expected tool {tool!r} to be called; tools used: {sorted(used)}")

    for tool in expected.get("tools_not_called", []):
        if tool in used:
            failures.append(f"tool {tool!r} must not be called, but it was")

    if expected.get("no_tools_called") and used:
        failures.append(f"expected no tool calls, got: {sorted(used)}")

    for tool, want_args in expected.get("tool_args", {}).items():
        calls = [c["input"] for c in transcript.tool_calls if c["name"] == tool]
        if not any(_args_match(call, want_args) for call in calls):
            failures.append(f"no call to {tool!r} had args {want_args}; calls: {calls}")

    reply = transcript.final_reply.lower()
    if wanted := expected.get("final_reply_contains_any"):
        if not any(s.lower() in reply for s in wanted):
            failures.append(f"final reply contains none of {wanted}")
    for banned in expected.get("final_reply_excludes", []):
        if banned.lower() in reply:
            failures.append(f"final reply must not contain {banned!r}")

    if "escalated" in expected:
        actual = bool(transcript.session_state and transcript.session_state["escalated"])
        if actual != expected["escalated"]:
            failures.append(f"expected escalated={expected['escalated']}, session has escalated={actual}")

    return failures


def _args_match(actual: dict[str, Any], wanted: dict[str, Any]) -> bool:
    for key, value in wanted.items():
        got = actual.get(key)
        if isinstance(value, str) and isinstance(got, str):
            if got.strip().lower() != value.strip().lower():
                return False
        elif got != value:
            return False
    return True
