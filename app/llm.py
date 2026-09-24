"""LLM backends.

Both backends speak the Anthropic Messages API shape: messages are dicts with
``role`` and ``content``, and a response is a ``stop_reason`` plus a list of
content-block dicts (``text``, ``thinking``, ``tool_use``, ...). The agent loop
only depends on that shape, so the real Claude client and the deterministic
mock are interchangeable.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

import anthropic

from app.config import Settings

# Server-side refusal fallback: if Claude's safety classifiers decline a request,
# the API re-runs it on Anthropic's recommended fallback model in the same call.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass
class LLMResponse:
    stop_reason: str | None
    content: list[dict[str, Any]]


class LLMUnavailableError(Exception):
    """The upstream model API failed; surfaced to clients as a 5xx."""

    def __init__(self, message: str, status_code: int = 502, retry_after: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


class LLMClient(Protocol):
    def create(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LLMResponse: ...


class AnthropicLLM:
    def __init__(self, settings: Settings):
        # Credentials come from the environment (ANTHROPIC_API_KEY et al.).
        self._client = anthropic.Anthropic()
        self._settings = settings

    def create(self, *, system, messages, tools) -> LLMResponse:
        try:
            response = self._client.beta.messages.create(
                model=self._settings.anthropic_model,
                max_tokens=self._settings.anthropic_max_tokens,
                system=system,
                messages=messages,
                tools=tools,
                thinking={"type": "adaptive"},
                output_config={"effort": self._settings.anthropic_effort},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.RateLimitError as exc:
            raise LLMUnavailableError(
                "LLM rate limited", 503, exc.response.headers.get("retry-after")
            ) from exc
        except anthropic.APIStatusError as exc:
            status = 503 if exc.status_code >= 500 else 502
            raise LLMUnavailableError(f"LLM API error ({exc.status_code})", status) from exc
        except anthropic.APIConnectionError as exc:
            raise LLMUnavailableError("Could not reach the LLM API", 503) from exc

        # Keep every block (including thinking blocks) verbatim: they must be
        # passed back unchanged on the next request of the tool loop.
        content = [block.model_dump(mode="json", exclude_none=True) for block in response.content]
        return LLMResponse(stop_reason=response.stop_reason, content=content)


ORDER_ID_RE = re.compile(r"\b([A-Za-z]{3}-\d{3,})\b")
HUMAN_RE = re.compile(r"\b(human|real person|agent|representative|manager|someone)\b", re.I)


class MockLLM:
    """Deterministic, rule-based stand-in for Claude.

    It emits the same content-block shapes as the real API (tool_use blocks,
    then text after tool results), so the whole API -> agent loop -> tools ->
    DB path is exercised in CI without network access or API spend.
    """

    def create(self, *, system, messages, tools) -> LLMResponse:
        last = messages[-1]
        if isinstance(last["content"], list) and any(
            block.get("type") == "tool_result" for block in last["content"]
        ):
            return self._after_tool_results(messages)
        return self._respond_to_user(_text_of(last["content"]))

    def _respond_to_user(self, text: str) -> LLMResponse:
        order_match = ORDER_ID_RE.search(text)
        if HUMAN_RE.search(text) and not order_match:
            return _tool_use(
                "escalate_to_human", {"reason": f"Customer asked for a human agent: {text}"}
            )
        if order_match:
            return _tool_use("check_order_status", {"order_id": order_match.group(1)})
        if "order" in text.lower():
            return _text(
                "I'd be happy to look into your order. Could you share your order ID? "
                "It looks like ORD-1234."
            )
        return _text(
            "I'm the support assistant for order questions, so I can't help with that one. "
            "If you have an order ID, I can check its status for you."
        )

    def _after_tool_results(self, messages: list[dict[str, Any]]) -> LLMResponse:
        tool_uses = {
            block["id"]: block
            for block in messages[-2]["content"]
            if block.get("type") == "tool_use"
        }
        for result in messages[-1]["content"]:
            call = tool_uses[result["tool_use_id"]]
            data = json.loads(result["content"])
            if result.get("is_error"):
                return _text(
                    "Sorry, I ran into a problem while checking that. Could you try again?"
                )
            if call["name"] == "escalate_to_human":
                return _text(
                    f"I've escalated this to a human agent (ticket {data['ticket_id']}). "
                    "Someone from our team will contact you shortly."
                )
            if call["name"] == "check_order_status":
                if not data["found"]:
                    return _tool_use(
                        "escalate_to_human",
                        {
                            "reason": f"Customer asked about order {data['order_id']}, "
                            "which does not exist in the system."
                        },
                    )
                return _text(_describe_order(data))
        return _text("Is there anything else I can help you with?")


def _describe_order(order: dict[str, Any]) -> str:
    status = order["status"]
    base = f"Your order {order['order_id']} ({order['item']}) "
    if status == "delivered":
        return base + f"was delivered on {order['last_update']}. Enjoy!"
    if status == "shipped":
        return base + (
            f"has shipped and is on its way. Tracking number: {order['tracking_number']}."
        )
    return base + f"is currently {status} (last update {order['last_update']})."


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    return " ".join(b.get("text", "") for b in content if b.get("type") == "text")


def _text(text: str) -> LLMResponse:
    return LLMResponse("end_turn", [{"type": "text", "text": text}])


def _tool_use(name: str, tool_input: dict[str, Any]) -> LLMResponse:
    block = {"type": "tool_use", "id": f"toolu_mock_{uuid.uuid4().hex[:12]}", "name": name, "input": tool_input}
    return LLMResponse("tool_use", [block])


def build_llm(settings: Settings) -> LLMClient:
    if settings.llm_provider == "mock":
        return MockLLM()
    if settings.llm_provider == "anthropic":
        return AnthropicLLM(settings)
    raise ValueError(f"Unknown LLM_PROVIDER: {settings.llm_provider!r}")
