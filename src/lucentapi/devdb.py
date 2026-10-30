"""lucentBASE on SQLite, for development and tests.

The tables of `schema.py`, plus triggers that write the log tables like lucentBASE's
`create_all_log_tables()` does on MariaDB. SQLite has no session variables, so the editor and
note come from the one-row table `lucentapi_session`, which `db.set_editor()` fills. Its
`frozen_t`, if set, is the time the triggers log instead of the clock (for reproducible examples).
"""

from sqlalchemy import Engine

from .schema import LUCENT_TABLES, metadata


def create(engine: Engine) -> None:
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS lucentapi_session"
            " (id INTEGER PRIMARY KEY CHECK (id = 1), current_user_id INTEGER, change_note TEXT, frozen_t TEXT)")
        conn.exec_driver_sql("INSERT OR IGNORE INTO lucentapi_session (id) VALUES (1)")
        for table in LUCENT_TABLES:
            checksum = " || '|' || ".join(f'COALESCE(NEW."{column.name}", \'NULL\')' for column in table.columns)
            for event, letter, default in (("INSERT", "I", "Inserted"), ("UPDATE", "U", "Updated")):
                conn.exec_driver_sql(f"""
                    CREATE TRIGGER IF NOT EXISTS trg_{table.name}_{event.lower()} AFTER {event} ON "{table.name}"
                    BEGIN
                        INSERT INTO "l_{table.name}" (id_{table.name}, modification, t, id_editor, note, checksum)
                        VALUES (NEW.id_{table.name}, '{letter}',
                                COALESCE((SELECT frozen_t FROM lucentapi_session WHERE id = 1), CURRENT_TIMESTAMP),
                                (SELECT current_user_id FROM lucentapi_session WHERE id = 1),
                                COALESCE((SELECT change_note FROM lucentapi_session WHERE id = 1), '{default}'),
                                sha2({checksum}, 256));
                    END""")
