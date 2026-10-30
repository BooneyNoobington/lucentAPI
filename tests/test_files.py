"""Generated files stay in line with the code."""

import json
from pathlib import Path


ROOT = Path(__file__).parent.parent


def load_script(name: str):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_openapi_file_is_current():
    expected = load_script("export_openapi").spec()
    assert json.loads((ROOT / "openapi.json").read_text()) == json.loads(expected), "run scripts/export_openapi.py"


def test_example_responses_are_current(tmp_path):
    load_script("export_examples").write(tmp_path)
    committed = ROOT / "examples" / "responses"
    assert sorted(p.name for p in committed.glob("*.json")) == sorted(p.name for p in tmp_path.glob("*.json"))
    for path in tmp_path.glob("*.json"):
        assert json.loads(path.read_text()) == json.loads((committed / path.name).read_text()), path.name
