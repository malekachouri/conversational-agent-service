"""Binds each tool function to its JSON schema.

The schema file is the single source of truth: it is sent to the LLM as the
tool definition *and* used to validate the model's arguments before the
Python function runs.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import jsonschema

from app.tools.functions import ToolError, check_order_status, escalate_to_human

logger = logging.getLogger(__name__)

SCHEMA_DIR = Path(__file__).parent / "schemas"


def load_schema(name: str) -> dict[str, Any]:
    return json.loads((SCHEMA_DIR / f"{name}.json").read_text())


@dataclass(frozen=True)
class Tool:
    definition: dict[str, Any]
    func: Callable[..., dict[str, Any]]
    # Keyword-only dependencies injected by the caller (never supplied by the LLM).
    context_params: tuple[str, ...] = ()

    @property
    def name(self) -> str:
        return self.definition["name"]


@dataclass(frozen=True)
class ToolOutcome:
    ok: bool
    output: dict[str, Any]


class ToolRegistry:
    def __init__(self, tools: list[Tool]):
        self._tools = {tool.name: tool for tool in tools}

    @property
    def definitions(self) -> list[dict[str, Any]]:
        return [tool.definition for tool in self._tools.values()]

    def execute(self, name: str, args: dict[str, Any], **context: Any) -> ToolOutcome:
        tool = self._tools.get(name)
        if tool is None:
            return ToolOutcome(False, {"error": f"Unknown tool: {name}"})

        try:
            jsonschema.validate(args, tool.definition["input_schema"])
        except jsonschema.ValidationError as exc:
            return ToolOutcome(False, {"error": f"Invalid arguments: {exc.message}"})

        injected = {key: context[key] for key in tool.context_params}
        try:
            return ToolOutcome(True, tool.func(**args, **injected))
        except ToolError as exc:
            return ToolOutcome(False, {"error": str(exc)})
        except Exception:
            logger.exception("Tool %s crashed", name)
            return ToolOutcome(False, {"error": "Internal error while running the tool."})


def default_registry() -> ToolRegistry:
    return ToolRegistry(
        [
            Tool(load_schema("check_order_status"), check_order_status, ("db",)),
            Tool(load_schema("escalate_to_human"), escalate_to_human, ("db", "session_id")),
        ]
    )
