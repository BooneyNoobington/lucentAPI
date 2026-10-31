"""Writes lucent-package.schema.json, the format's JSON Schema, and the demo data's master data as
a package (src/lucentapi/data/demo.lucent.yaml).

    uv run python scripts/export_package.py
"""

import json
import sys
from datetime import datetime
from pathlib import Path

from lucentapi import demo, devdb, package
from lucentapi.db import make_engine

ROOT = Path(__file__).parent.parent
SCHEMA = ROOT / "lucent-package.schema.json"
DEMO = ROOT / "src" / "lucentapi" / "data" / "demo.lucent.yaml"
NOW = datetime(2026, 10, 29, 12, 0)


def schema() -> str:
    return json.dumps(package.json_schema(), indent=2, ensure_ascii=False) + "\n"


def demo_package() -> str:
    engine = make_engine("sqlite://")
    devdb.create(engine)
    with engine.begin() as conn:
        demo.load(conn, now=NOW)
    with engine.connect() as conn:
        exported = package.export(conn, description="The demo data of lucentAPI, an environmental lab in Karlsruhe")
    # The editor finds the schema relative to this file.
    return package.as_yaml(exported, schema_url="../../../lucent-package.schema.json")


if __name__ == "__main__":
    SCHEMA.write_text(schema())
    DEMO.write_text(demo_package())
    print(f"wrote {SCHEMA.relative_to(ROOT)} and {DEMO.relative_to(ROOT)}", file=sys.stderr)
