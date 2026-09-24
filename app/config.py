"""Runtime configuration, read from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    # SQLAlchemy URL. Postgres in docker-compose / prod, SQLite for local hacking.
    database_url: str = "sqlite:///dev.db"
    # "anthropic" calls the real Claude API; "mock" uses a deterministic,
    # rule-based stand-in so the service and its tests run without an API key.
    llm_provider: str = "anthropic"
    anthropic_model: str = "claude-opus-5"
    # Customer-support chat is latency sensitive; medium effort keeps turns snappy.
    anthropic_effort: str = "medium"
    anthropic_max_tokens: int = 16000
    # Upper bound on LLM round-trips per user message (guards against tool loops).
    max_agent_iterations: int = 6

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.getenv("DATABASE_URL", cls.database_url),
            llm_provider=os.getenv("LLM_PROVIDER", cls.llm_provider).lower(),
            anthropic_model=os.getenv("ANTHROPIC_MODEL", cls.anthropic_model),
            anthropic_effort=os.getenv("ANTHROPIC_EFFORT", cls.anthropic_effort),
            anthropic_max_tokens=int(
                os.getenv("ANTHROPIC_MAX_TOKENS", cls.anthropic_max_tokens)
            ),
            max_agent_iterations=int(
                os.getenv("MAX_AGENT_ITERATIONS", cls.max_agent_iterations)
            ),
        )
