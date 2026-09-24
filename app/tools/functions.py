"""Tool implementations.

These are plain Python functions with no knowledge of FastAPI or the LLM:
they take validated arguments plus a database session and return a
JSON-serialisable dict. That keeps them unit-testable in isolation.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models import Escalation, Order


class ToolError(Exception):
    """Raised for bad input a tool cannot act on; reported back to the model."""


def check_order_status(order_id: str, *, db: Session) -> dict[str, Any]:
    normalized = order_id.strip().upper()
    if not normalized:
        raise ToolError("order_id must not be empty")

    order = db.get(Order, normalized)
    if order is None:
        return {
            "found": False,
            "order_id": normalized,
            "message": "No order with this ID exists in the system.",
        }
    return {
        "found": True,
        "order_id": order.order_id,
        "status": order.status,
        "item": order.item,
        "last_update": order.last_update.isoformat(),
        "tracking_number": order.tracking_number,
    }


def escalate_to_human(reason: str, *, db: Session, session_id: str) -> dict[str, Any]:
    reason = reason.strip()
    if not reason:
        raise ToolError("reason must not be empty")

    ticket_id = f"ESC-{uuid.uuid4().hex[:8].upper()}"
    db.add(Escalation(ticket_id=ticket_id, session_id=session_id, reason=reason))
    db.flush()
    return {
        "escalated": True,
        "ticket_id": ticket_id,
        "message": "A human support agent will pick this up and contact the customer.",
    }
