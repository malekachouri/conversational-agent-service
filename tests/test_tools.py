"""Unit tests for the tool functions and registry - no API, no LLM."""

import pytest
from sqlalchemy import select

from app.models import Escalation
from app.tools import (
    ToolError,
    check_order_status,
    default_registry,
    escalate_to_human,
    load_schema,
)


def test_check_order_status_found(db_session):
    result = check_order_status("ORD-1001", db=db_session)
    assert result["found"] is True
    assert result["status"] == "shipped"
    assert result["tracking_number"]


def test_check_order_status_normalizes_id(db_session):
    assert check_order_status("  ord-1003 ", db=db_session)["status"] == "delivered"


def test_check_order_status_not_found(db_session):
    result = check_order_status("ORD-9999", db=db_session)
    assert result == {
        "found": False,
        "order_id": "ORD-9999",
        "message": "No order with this ID exists in the system.",
    }


def test_check_order_status_rejects_blank(db_session):
    with pytest.raises(ToolError):
        check_order_status("   ", db=db_session)


def test_escalate_to_human_creates_ticket(db_session):
    result = escalate_to_human("Order missing", db=db_session, session_id="s1")
    assert result["escalated"] is True
    assert result["ticket_id"].startswith("ESC-")

    row = db_session.scalars(select(Escalation)).one()
    assert (row.session_id, row.reason, row.ticket_id) == ("s1", "Order missing", result["ticket_id"])


def test_registry_definitions_come_from_schema_files():
    definitions = {d["name"]: d for d in default_registry().definitions}
    assert set(definitions) == {"check_order_status", "escalate_to_human"}
    for name, definition in definitions.items():
        assert definition == load_schema(name)
        assert definition["input_schema"]["additionalProperties"] is False


def test_registry_executes_valid_call(db_session):
    outcome = default_registry().execute("check_order_status", {"order_id": "ORD-1002"}, db=db_session, session_id="s")
    assert outcome.ok
    assert outcome.output["status"] == "processing"


@pytest.mark.parametrize(
    "name,args",
    [
        ("check_order_status", {}),  # missing required arg
        ("check_order_status", {"order_id": 1001}),  # wrong type
        ("check_order_status", {"order_id": "ORD-1", "extra": True}),  # unexpected arg
        ("check_order_status", {"order_id": " "}),  # ToolError from the function
        ("delete_all_orders", {}),  # unknown tool
    ],
)
def test_registry_reports_failures_instead_of_raising(db_session, name, args):
    outcome = default_registry().execute(name, args, db=db_session, session_id="s")
    assert not outcome.ok
    assert "error" in outcome.output
