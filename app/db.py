"""Database engine/session helpers and demo seed data."""

from __future__ import annotations

from datetime import date

from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import Base, Order

SEED_ORDERS: list[dict] = [
    dict(
        order_id="ORD-1001",
        customer_name="Ada Lovelace",
        item="Wireless headphones",
        status="shipped",
        last_update=date(2026, 9, 20),
        tracking_number="1Z999AA10123456784",
    ),
    dict(
        order_id="ORD-1002",
        customer_name="Alan Turing",
        item="Mechanical keyboard",
        status="processing",
        last_update=date(2026, 9, 22),
    ),
    dict(
        order_id="ORD-1003",
        customer_name="Grace Hopper",
        item="Standing desk",
        status="delivered",
        last_update=date(2026, 9, 15),
        tracking_number="1Z999AA10123456785",
    ),
    dict(
        order_id="ORD-1004",
        customer_name="Linus Torvalds",
        item="Coffee grinder",
        status="cancelled",
        last_update=date(2026, 9, 10),
    ),
]


def make_engine(database_url: str) -> Engine:
    connect_args = {}
    if database_url.startswith("sqlite"):
        # FastAPI runs sync endpoints in a threadpool.
        connect_args["check_same_thread"] = False
    return create_engine(database_url, connect_args=connect_args, pool_pre_ping=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def init_db(engine: Engine) -> None:
    """Create tables and seed the demo orders if the table is empty."""
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        if session.scalar(select(Order.order_id).limit(1)) is None:
            session.add_all(Order(**row) for row in SEED_ORDERS)
            session.commit()
