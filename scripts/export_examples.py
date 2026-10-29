"""Writes examples/responses/*.json: real answers of the API on the demo data, for client authors
and client tests.

    uv run python scripts/export_examples.py
"""

import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from lucentapi import demo
from lucentapi.config import Settings
from lucentapi.db import make_engine
from lucentapi.devtokens import AUDIENCE, ISSUER, DevSigner
from lucentapi.main import create_app
from lucentapi.schema import metadata

NOW = datetime(2026, 10, 29, 12, 0)
TARGET = Path(__file__).parent.parent / "examples" / "responses"


def responses() -> dict[str, object]:
    with tempfile.TemporaryDirectory() as keys:
        signer = DevSigner(Path(keys))
        settings = Settings(database_url="sqlite://", oidc_issuer=ISSUER, oidc_audience=AUDIENCE,
                            oidc_jwks_file=signer.jwks_file, numbering_offset=demo.NUMBERING_OFFSET, _env_file=None)
        engine = make_engine(settings.database_url)
        metadata.create_all(engine)
        with engine.begin() as conn:
            demo.load(conn, now=NOW)
        client = TestClient(create_app(settings, engine))

        def as_user(username, *roles):
            return {"Authorization": f"Bearer {signer.token(username, list(roles))}", "X-Request-ID": "example"}

        def get(path, user="grindel"):
            response = client.get(path, headers=as_user(user))
            assert response.status_code == 200, response.text
            return response.json()

        result = {name: get(f"/v1/{name}") for name in (
            "people", "organizations", "crs", "sites", "spots", "matrices", "collection-classes", "dimensions",
            "units", "variables", "devices", "sops", "actions", "tasks", "messages")}
        result["me"] = get("/v1/me", "xx121")
        result["collections"] = get("/v1/collections?limit=3")
        result["values"] = get("/v1/values?limit=3")
        result["series"] = get("/v1/series?spot_id=10&action_variable_id=6")

        created = client.post("/v1/collections", headers=as_user("worf", "sampler"), json={
            "matrix_id": 9, "class_id": 3, "spot_id": 4, "sampled_at": "2026-10-29T09:30:00Z", "note": "Example"})
        result["collection-created"] = created.json()
        collection_id = created.json()["id"]
        for reading in (12.4, 11.9):
            recorded = client.post(f"/v1/collections/{collection_id}/values", headers=as_user("p47at", "analyst"),
                                   json={"action_variable_id": 9, "reading": {"decimal": reading},
                                         "measured_at": "2026-10-29T11:00:00Z"})
        result["value-recorded"] = recorded.json()
        result["collection"] = get(f"/v1/collections/{collection_id}")
        result["error-four-eyes"] = client.post(f"/v1/values/{recorded.json()['id']}/accept",
                                                headers=as_user("p47at", "reviewer")).json()
        return result


def write(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for name, body in responses().items():
        (target / f"{name}.json").write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else TARGET
    write(target)
    print(f"wrote {target}")
