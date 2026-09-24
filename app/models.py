"""ORM models: the orders domain table plus conversation state."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import JSON, Date, DateTime, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Order(Base):
    __tablename__ = "orders"

    order_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    customer_name: Mapped[str] = mapped_column(String(128))
    item: Mapped[str] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(32))
    last_update: Mapped[date] = mapped_column(Date)
    tracking_number: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ConversationMessage(Base):
    """One Messages-API turn, stored verbatim so history can be replayed to the LLM.

    ``content`` holds either a plain string or the list of content blocks
    (text, thinking, tool_use, tool_result) exactly as sent to / received from
    the model.
    """

    __tablename__ = "conversation_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(128), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[Any] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Escalation(Base):
    __tablename__ = "escalations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticket_id: Mapped[str] = mapped_column(String(32), unique=True)
    session_id: Mapped[str] = mapped_column(String(128), index=True)
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
