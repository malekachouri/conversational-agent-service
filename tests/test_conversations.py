"""Replays every fixture in fixtures/conversations/ and asserts its expected outcomes."""

import pytest

from tests.harness import check_outcomes, load_scenarios, replay

SCENARIOS = load_scenarios()


@pytest.mark.conversation
@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["name"] for s in SCENARIOS])
def test_conversation_outcomes(api_client, scenario):
    transcript = replay(api_client, scenario)
    failures = check_outcomes(transcript, scenario["expected"])
    assert not failures, (
        f"Scenario {scenario['name']!r} failed:\n  - "
        + "\n  - ".join(failures)
        + f"\n\nTranscript:\n{transcript.render()}"
    )


def test_required_scenarios_present():
    names = {s["name"] for s in SCENARIOS}
    assert {
        "order_status_lookup",
        "unknown_order_escalates",
        "unrelated_question",
        "delivered_order_no_escalation",
    } <= names
