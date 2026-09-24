"""Shared fixtures.

The conversation suite runs in one of two modes:

* ``AGENT_BASE_URL`` set  -> replay against an already-running service over
  HTTP (docker-compose locally, the freshly built container in CI).
* otherwise              -> start the app in-process on a throwaway SQLite DB.
  ``LLM_PROVIDER`` defaults to ``mock`` here; set ``LLM_PROVIDER=anthropic``
  (plus ``ANTHROPIC_API_KEY``) to replay the same scenarios against real Claude.
"""

from __future__ import annotations

import dataclasses
import os

import httpx
import pytest

AGENT_BASE_URL = os.getenv("AGENT_BASE_URL")


@pytest.fixture(scope="session")
def api_client(tmp_path_factory):
    if AGENT_BASE_URL:
        with httpx.Client(base_url=AGENT_BASE_URL, timeout=180) as client:
            yield client
        return

    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    db_path = tmp_path_factory.mktemp("db") / "test.db"
    settings = dataclasses.replace(
        Settings.from_env(),
        database_url=f"sqlite:///{db_path}",
        llm_provider=os.getenv("LLM_PROVIDER", "mock"),
    )
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.fixture
def db_session():
    """An in-memory database with the demo orders seeded, for tool unit tests."""
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool
    from sqlalchemy import create_engine

    from app.db import init_db

    engine = create_engine("sqlite://", poolclass=StaticPool)
    init_db(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()
