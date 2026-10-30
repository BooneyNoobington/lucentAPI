"""Does the database have what the API expects? Checked at startup, so a lucentBASE that is
older or newer than this API fails loudly instead of in the middle of a request."""

import warnings

from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import SAWarning

from .schema import LUCENT_TABLES, metadata


class SchemaMismatch(RuntimeError):
    pass


def check_schema(engine: Engine) -> None:
    inspector = inspect(engine)
    present = set(inspector.get_table_names())
    problems = []
    for table in metadata.tables.values():
        if table.name not in present:
            problems.append(f"table {table.name} is missing")
            continue
        with warnings.catch_warnings():  # site.borders is a POLYGON, which reflection doesn't know
            warnings.filterwarnings("ignore", "Did not recognize type", SAWarning)
            columns = {column["name"] for column in inspector.get_columns(table.name)}
        problems += [f"column {table.name}.{c.name} is missing" for c in table.columns if c.name not in columns]

    with engine.connect() as conn:
        if conn.dialect.name == "sqlite":
            triggers = set(conn.scalars(text("SELECT name FROM sqlite_master WHERE type = 'trigger'")))
        else:
            triggers = set(conn.scalars(text(
                "SELECT trigger_name FROM information_schema.triggers WHERE trigger_schema = DATABASE()")))
    missing = [f"trg_{t.name}_{e}" for t in LUCENT_TABLES for e in ("insert", "update")
               if f"trg_{t.name}_{e}" not in triggers]
    if missing:
        problems.append(f"log triggers are missing ({', '.join(missing[:3])}…): CALL create_all_log_tables()")

    if problems:
        raise SchemaMismatch("The database doesn't match lucentBASE as lucentAPI expects it:\n  "
                             + "\n  ".join(problems))
