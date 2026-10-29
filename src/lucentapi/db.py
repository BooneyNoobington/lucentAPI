"""The database engine. Endpoints open their own transaction with `db(request).begin()`."""

from datetime import UTC, datetime

from fastapi import Request
from sqlalchemy import Engine, Table, create_engine, event
from sqlalchemy.pool import StaticPool


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        memory = ":memory:" in url or url in ("sqlite://", "sqlite:///")
        engine = create_engine(
            url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool if memory else None,
        )

        @event.listens_for(engine, "connect")
        def foreign_keys(dbapi_connection, _):
            dbapi_connection.execute("PRAGMA foreign_keys = ON")

        return engine
    return create_engine(url, pool_pre_ping=True, pool_recycle=3600)


def db(request: Request) -> Engine:
    return request.app.state.engine


def visible(table: Table):
    return table.c.vis == True  # noqa: E712 (SQL comparison)


def utcnow() -> datetime:
    """The database stores naive UTC times."""
    return datetime.now(UTC).replace(tzinfo=None)


def to_db_time(value: datetime | None) -> datetime | None:
    """Aware times are converted to UTC; naive ones are taken as UTC."""
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def from_db_time(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=UTC) if value is not None else None
