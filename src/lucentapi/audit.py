"""Every write leaves a row in lucentBASE's log table for that table (`l_<table>`).

A log row says what changed (`change`, JSON), when (`t`, UTC), who (`id_editor`, the person
behind the token) and why (`note`, optional). Its `checksum` is the SHA-256 of the previous
log row's checksum for the same record plus this row's content. The rows of one record form a
chain: editing or deleting one breaks every checksum after it, which `verify_chain` finds.
"""

import hashlib
import json
import sys
from datetime import date, datetime

from sqlalchemy import Connection, Table, insert, select

from .db import utcnow
from .schema import log_table, metadata


def _plain(value):
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    return value


def canonical(data) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_plain)


def checksum(previous: str | None, change: str, t: datetime, editor: int, note: str | None) -> str:
    content = canonical({"previous": previous, "change": change, "t": t, "editor": editor, "note": note})
    return hashlib.sha256(content.encode()).hexdigest()


def _previous_checksum(conn: Connection, log: Table, key: str, row_id: int) -> str | None:
    return conn.scalar(
        select(log.c.checksum).where(log.c[key] == row_id).order_by(log.c[f"id_{log.name}"].desc()).limit(1)
    )


def record(conn: Connection, table: Table, row_id: int, editor: int, *, before: dict | None, after: dict | None,
           note: str | None = None) -> None:
    """Log an insert (no `before`), an update (both) or a deletion (no `after`)."""
    if before is None:
        change = {"op": "insert", "row": after}
    elif after is None:
        change = {"op": "delete", "row": before}
    else:
        fields = {k: [before.get(k), v] for k, v in after.items() if before.get(k) != v}
        if not fields:
            return
        change = {"op": "update", "fields": fields}

    log = log_table(table)
    key = f"id_{table.name}"
    text = canonical(change)
    t = utcnow().replace(microsecond=0)  # DATETIME keeps whole seconds; the checksum must survive that
    conn.execute(insert(log).values({
        key: row_id,
        "change": text,
        "t": t,
        "id_editor": editor,
        "note": note,
        "checksum": checksum(_previous_checksum(conn, log, key, row_id), text, t, editor, note),
    }))


def verify_chain(conn: Connection, table: Table, row_id: int) -> list[int]:
    """The log ids of one record whose checksum doesn't match; empty if the chain is intact."""
    log = log_table(table)
    key = f"id_{table.name}"
    rows = conn.execute(select(log).where(log.c[key] == row_id).order_by(log.c[f"id_{log.name}"])).mappings()
    broken, previous = [], None
    for row in rows:
        if checksum(previous, row["change"], row["t"], row["id_editor"], row["note"]) != row["checksum"]:
            broken.append(row[f"id_{log.name}"])
        previous = row["checksum"]
    return broken


def main() -> None:
    """lucentapi-verify-log <table> [<table> …]: check every record's log chain."""
    from .config import Settings
    from .db import make_engine

    tables = sys.argv[1:]
    if not tables:
        sys.exit("usage: lucentapi-verify-log <table> [<table> …], e.g. value collection")
    engine = make_engine(Settings().database_url)
    failed = False
    with engine.connect() as conn:
        for name in tables:
            table = metadata.tables[name]
            log = log_table(table)
            ids = conn.scalars(select(log.c[f"id_{name}"]).distinct()).all()
            for row_id in ids:
                broken = verify_chain(conn, table, row_id)
                if broken:
                    failed = True
                    print(f"{name} {row_id}: log rows {broken} don't match their checksums")
            print(f"{name}: checked {len(ids)} records")
    sys.exit(1 if failed else 0)
