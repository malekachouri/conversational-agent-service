"""The agent loop: LLM <-> tools, with conversation history persisted per session."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm import LLMClient
from app.metrics import TOOL_CALLS
from app.models import ConversationMessage
from app.tools import ToolRegistry

SYSTEM_PROMPT = """\
You are the customer-support assistant for an online store. You help customers \
with questions about their orders.

- To answer anything about an order's status, call check_order_status with the \
order ID. Never guess an order's status. If the customer hasn't given an order \
ID, ask for it.
- If check_order_status reports that the order does not exist, apologise and \
call escalate_to_human so a person can investigate. Don't ask the customer to \
re-check the ID more than once.
- Call escalate_to_human when the customer explicitly asks for a human, or needs \
something you have no tool for (refunds, replacements, address changes).
- Do not escalate issues you have resolved yourself - for example, telling a \
customer their order was delivered.
- For questions unrelated to orders, briefly say you can only help with order \
questions. Don't call any tool.
- Keep replies short, friendly and plain-text (no markdown). Mention the order \
status in plain words (e.g. "shipped", "delivered")."""

REFUSAL_REPLY = "Sorry, I can't help with that request."
LOOP_LIMIT_REPLY = (
    "Sorry, I wasn't able to finish that. Please try again, or ask for a human agent."
)


@dataclass
class ToolCallRecord:
    name: str
    input: dict[str, Any]
    output: dict[str, Any]
    is_error: bool


@dataclass
class ChatResult:
    reply: str
    tool_calls: list[ToolCallRecord] = field(default_factory=list)


class Agent:
    def __init__(self, llm: LLMClient, tools: ToolRegistry, max_iterations: int = 6):
        self.llm = llm
        self.tools = tools
        self.max_iterations = max_iterations

    def chat(self, db: Session, session_id: str, user_message: str) -> ChatResult:
        """Run one user turn to completion and persist it.

        The caller owns the transaction: nothing is written unless it commits,
        so a failed LLM call never leaves half a turn in the history.
        """
        history = load_history(db, session_id)
        turn: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
        tool_calls: list[ToolCallRecord] = []
        reply = LOOP_LIMIT_REPLY

        for _ in range(self.max_iterations):
            response = self.llm.create(
                system=SYSTEM_PROMPT, messages=history + turn, tools=self.tools.definitions
            )

            if response.stop_reason == "refusal":
                reply = REFUSAL_REPLY
                break

            turn.append({"role": "assistant", "content": response.content})
            tool_uses = [b for b in response.content if b.get("type") == "tool_use"]
            if response.stop_reason != "tool_use" or not tool_uses:
                reply = _join_text(response.content) or LOOP_LIMIT_REPLY
                break

            # All results for one assistant message go back in a single user message.
            results = []
            for block in tool_uses:
                outcome = self.tools.execute(
                    block["name"], block["input"], db=db, session_id=session_id
                )
                TOOL_CALLS.labels(
                    tool=block["name"], outcome="success" if outcome.ok else "failure"
                ).inc()
                tool_calls.append(
                    ToolCallRecord(block["name"], block["input"], outcome.output, not outcome.ok)
                )
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block["id"],
                        "content": json.dumps(outcome.output),
                        "is_error": not outcome.ok,
                    }
                )
            turn.append({"role": "user", "content": results})

        # Always close the turn with an assistant message so the stored history
        # keeps strict user/assistant alternation.
        if turn[-1]["role"] != "assistant":
            turn.append({"role": "assistant", "content": [{"type": "text", "text": reply}]})

        db.add_all(
            ConversationMessage(session_id=session_id, role=m["role"], content=m["content"])
            for m in turn
        )
        return ChatResult(reply=reply, tool_calls=tool_calls)


def load_history(db: Session, session_id: str) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(ConversationMessage)
        .where(ConversationMessage.session_id == session_id)
        .order_by(ConversationMessage.id)
    )
    return [{"role": row.role, "content": row.content} for row in rows]


def _join_text(content: list[dict[str, Any]]) -> str:
    return "\n".join(b["text"] for b in content if b.get("type") == "text").strip()
