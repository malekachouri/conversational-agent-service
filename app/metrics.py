"""Prometheus metrics exposed on /metrics."""

from prometheus_client import Counter, Histogram

CHAT_REQUESTS = Counter(
    "chat_requests_total",
    "Chat requests received, by result.",
    ["status"],  # success | error
)

TOOL_CALLS = Counter(
    "agent_tool_calls_total",
    "Tool calls made by the agent, by tool and outcome.",
    ["tool", "outcome"],  # outcome: success | failure
)

CHAT_LATENCY = Histogram(
    "chat_request_duration_seconds",
    "End-to-end latency of POST /chat, including all LLM round-trips and tool calls.",
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 30, 60),
)
