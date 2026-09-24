"""FastAPI application.

Run with:  uvicorn --factory app.main:create_app
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.agent import Agent
from app.config import Settings
from app.db import init_db, make_engine, make_session_factory
from app.llm import LLMUnavailableError, build_llm
from app.metrics import CHAT_LATENCY, CHAT_REQUESTS
from app.models import ConversationMessage, Escalation
from app.tools import default_registry

logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4000)


class ToolCallOut(BaseModel):
    name: str
    input: dict[str, Any]
    output: dict[str, Any]
    is_error: bool


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    tool_calls: list[ToolCallOut]


class EscalationOut(BaseModel):
    ticket_id: str
    reason: str


class SessionOut(BaseModel):
    session_id: str
    message_count: int
    escalated: bool
    escalations: list[EscalationOut]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(engine)
        app.state.agent = Agent(
            build_llm(settings), default_registry(), settings.max_agent_iterations
        )
        logger.info("Started with LLM provider %s", settings.llm_provider)
        yield
        engine.dispose()

    app = FastAPI(title="conversational-agent-service", version="0.1.0", lifespan=lifespan)

    @app.post("/chat", response_model=ChatResponse)
    def chat(body: ChatRequest, request: Request, response: Response) -> ChatResponse:
        agent: Agent = request.app.state.agent
        start = time.perf_counter()
        status = "error"
        try:
            with session_factory() as db, db.begin():
                result = agent.chat(db, body.session_id, body.message)
            status = "success"
        except LLMUnavailableError as exc:
            headers = {"Retry-After": exc.retry_after} if exc.retry_after else None
            raise HTTPException(exc.status_code, str(exc), headers=headers) from exc
        finally:
            CHAT_REQUESTS.labels(status=status).inc()
            CHAT_LATENCY.observe(time.perf_counter() - start)

        return ChatResponse(
            session_id=body.session_id,
            reply=result.reply,
            tool_calls=[ToolCallOut(**vars(call)) for call in result.tool_calls],
        )

    @app.get("/sessions/{session_id}", response_model=SessionOut)
    def get_session(session_id: str) -> SessionOut:
        with session_factory() as db:
            count = db.scalar(
                select(func.count())
                .select_from(ConversationMessage)
                .where(ConversationMessage.session_id == session_id)
            )
            escalations = db.scalars(
                select(Escalation)
                .where(Escalation.session_id == session_id)
                .order_by(Escalation.id)
            ).all()
        if not count:
            raise HTTPException(404, "Unknown session")
        return SessionOut(
            session_id=session_id,
            message_count=count,
            escalated=bool(escalations),
            escalations=[EscalationOut(ticket_id=e.ticket_id, reason=e.reason) for e in escalations],
        )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics")
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app
