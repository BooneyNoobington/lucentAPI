"""Creates a SQLite database with the lucentBASE schema and the demo data.

    uv run python dev/seed.py            # dev/lucent.db
    uv run python dev/seed.py other.db
"""

import sys
from pathlib import Path

from sqlalchemy import create_engine

from lucentapi import demo
from lucentapi.schema import metadata

path = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent / "lucent.db")
path.unlink(missing_ok=True)
engine = create_engine(f"sqlite:///{path}")
metadata.create_all(engine)
with engine.begin() as conn:
    demo.load(conn)
print(f"{path}: lucentBASE schema with demo data")
