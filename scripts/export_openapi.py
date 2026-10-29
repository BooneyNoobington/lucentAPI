"""Writes openapi.json, the API description that clients (e.g. swift-openapi-generator) build on.

    uv run python scripts/export_openapi.py
"""

import json
from pathlib import Path

from lucentapi.config import Settings
from lucentapi.main import create_app


def spec() -> str:
    settings = Settings(database_url="sqlite://", oidc_issuer="https://id.example.org", oidc_audience="lucentapi",
                        _env_file=None)
    return json.dumps(create_app(settings).openapi(), indent=2, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    target = Path(__file__).parent.parent / "openapi.json"
    target.write_text(spec())
    print(f"wrote {target}")
