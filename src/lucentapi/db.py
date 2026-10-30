"""The database engine, and writing as a person.

lucentBASE's triggers log every change with the editor and note from the session variables
`@current_user_id` and `@change_note`. Endpoints that change data open their transaction with
`writing(engine, person, note)`, which sets them, and reads use `engine.connect()`.
"""

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime

from fastapi import Request
from sqlalchemy import Connection, Engine, Table, create_engine, event, text
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
        def sqlite_setup(dbapi_connection, _):
            dbapi_connection.execute("PRAGMA foreign_keys = ON")
            # For the imitated log triggers (devdb.py), like MariaDB's SHA2().
            dbapi_connection.create_function(
                "sha2", 2, lambda content, _bits: hashlib.sha256(str(content).encode()).hexdigest(),
                deterministic=True)

        return engine

    engine = create_engine(url, pool_pre_ping=True, pool_recycle=3600)

    @event.listens_for(engine, "connect")
    def mariadb_setup(dbapi_connection, _):
        # TIMESTAMP columns (the log tables' t) in UTC, like everything else.
        with dbapi_connection.cursor() as cursor:
            cursor.execute("SET time_zone = '+00:00'")

    return engine


@contextmanager
def writing(engine: Engine, editor_id: int, note: str | None = None) -> Iterator[Connection]:
    """A transaction whose changes lucentBASE logs under `editor_id`, with `note` as reason."""
    with engine.begin() as conn:
        set_editor(conn, editor_id, note)
        try:
            yield conn
        finally:
            # Pooled connections keep session variables; nobody else should write as this person.
            with suppress(Exception):
                set_editor(conn, None, None)


def set_editor(conn: Connection, editor_id: int | None, note: str | None) -> None:
    if conn.dialect.name == "sqlite":
        conn.execute(text("UPDATE lucentapi_session SET current_user_id = :editor, change_note = :note WHERE id = 1"),
                     {"editor": editor_id, "note": note})
    else:
        conn.execute(text("SET @current_user_id = :editor, @change_note = :note"), {"editor": editor_id, "note": note})


def db(request: Request) -> Engine:
    return request.app.state.engine


def visible(table: Table):
    return table.c.vis == True  # noqa: E712 (SQL comparison)


def utcnow() -> datetime:
    """The database stores naive UTC times, to the second (DATETIME has no fractions)."""
    return datetime.now(UTC).replace(tzinfo=None, microsecond=0)


def to_db_time(value: datetime | None) -> datetime | None:
    """Aware times are converted to UTC; naive ones are taken as UTC. Seconds only."""
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    return value.replace(microsecond=0)


def from_db_time(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=UTC) if value is not None else None
