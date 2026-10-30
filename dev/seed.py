"""Loads the demo data.

    uv run python dev/seed.py                       # a new SQLite lucentBASE in dev/lucent.db
    uv run python dev/seed.py 'mysql+pymysql://…'   # a lucentBASE fresh from DATABASE_GENERATION.SQL

On SQLite it creates the tables and imitates lucentBASE's log triggers (devdb.py). On MariaDB
the database must hold nothing but lucentBASE's initial data.
"""

import sys
from pathlib import Path

from lucentapi import demo, devdb
from lucentapi.db import make_engine

target = sys.argv[1] if len(sys.argv) > 1 else f"sqlite:///{Path(__file__).parent / 'lucent.db'}"
if target.startswith("sqlite:///"):
    Path(target.removeprefix("sqlite:///")).unlink(missing_ok=True)
engine = make_engine(target)
if engine.dialect.name == "sqlite":
    devdb.create(engine)
with engine.begin() as conn:
    demo.load(conn)
print(f"{engine.url.render_as_string(hide_password=True)}: demo data loaded")
