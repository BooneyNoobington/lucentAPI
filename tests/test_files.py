"""Generated files and the additions script stay in line with the code."""

import json
import re
from pathlib import Path

from lucentapi.schema import ADDITION, metadata

ROOT = Path(__file__).parent.parent


def load_script(name: str):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_additions_script_adds_exactly_the_marked_columns():
    marked = {(t.name, c.name) for t in metadata.tables.values() for c in t.columns if c.info.get(ADDITION)}
    script = (ROOT / "sql" / "lucentbase-additions.sql").read_text()
    added, table = set(), None
    for line in script.splitlines():
        if m := re.match(r"ALTER TABLE `(\w+)`", line):
            table = m[1]
        if m := re.search(r"ADD COLUMN IF NOT EXISTS `(\w+)`", line):
            added.add((table, m[1]))
    assert added == marked


def test_schema_file_is_current():
    expected = load_script("dump_schema").ddl()
    assert (ROOT / "sql" / "lucentbase-schema.sql").read_text() == expected, "run scripts/dump_schema.py"


def test_openapi_file_is_current():
    expected = load_script("export_openapi").spec()
    assert json.loads((ROOT / "openapi.json").read_text()) == json.loads(expected), "run scripts/export_openapi.py"


def test_example_responses_are_current(tmp_path):
    load_script("export_examples").write(tmp_path)
    committed = ROOT / "examples" / "responses"
    assert sorted(p.name for p in committed.glob("*.json")) == sorted(p.name for p in tmp_path.glob("*.json"))
    for path in tmp_path.glob("*.json"):
        assert json.loads(path.read_text()) == json.loads((committed / path.name).read_text()), path.name
