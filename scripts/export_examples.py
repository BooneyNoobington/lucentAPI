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

from lucentapi import demo, devdb
from lucentapi.config import Settings
from lucentapi.db import make_engine
from lucentapi.devtokens import AUDIENCE, ISSUER, DevSigner
from lucentapi.main import create_app

NOW = datetime(2026, 10, 29, 12, 0)
TARGET = Path(__file__).parent.parent / "examples" / "responses"


def responses() -> dict[str, object]:
    with tempfile.TemporaryDirectory() as keys:
        signer = DevSigner(Path(keys))
        settings = Settings(database_url="sqlite://", oidc_issuer=ISSUER, oidc_audience=AUDIENCE,
                            oidc_jwks_file=signer.jwks_file, numbering_offset=demo.NUMBERING_OFFSET,
                            sampling_time_parameter=demo.SAMPLING_TIME, _env_file=None)
        engine = make_engine(settings.database_url)
        devdb.create(engine)
        with engine.begin() as conn:  # the log's times, e.g. when collections were registered
            conn.exec_driver_sql(f"UPDATE lucentapi_session SET frozen_t = '{NOW:%Y-%m-%d %H:%M:%S}'")
        with engine.begin() as conn:
            demo.load(conn, now=NOW)
        client = TestClient(create_app(settings, engine))

        def as_user(username, **extra):  # roles as the demo data grants them
            return {"Authorization": f"Bearer {signer.token(username)}", "X-Request-ID": "example", **extra}

        def get(path, user="grindel"):
            response = client.get(path, headers=as_user(user))
            assert response.status_code == 200, response.text
            return response.json()

        result = {name: get(f"/v1/{name}") for name in (
            "people", "organizations", "crs", "sites", "spots", "matrices", "collection-classes", "dimensions",
            "units", "parameters", "devices", "documents", "actions", "coefficients", "thresholds", "tasks",
            "messages", "configuration", "roles")}
        result["me"] = get("/v1/me", "xx121")
        result["collections"] = get("/v1/collections?limit=3")
        result["values"] = get("/v1/values?limit=3")
        result["series"] = get("/v1/series?spot_id=10&parameter_id=7")

        created = client.post("/v1/collections", headers=as_user("worf"), json={
            "matrix_id": 2, "collection_class": "spot sample", "spot_id": 4, "action_ids": [1, 6],
            "sampled_at": "2026-10-29T09:30:00Z", "note": "Example"})
        result["collection-created"] = created.json()
        lead = next(v for v in created.json()["values"] if v["effect_id"] == 10)
        for hour, reading in ((11, 12.4), (14, 11.9)):
            recorded = client.post(f"/v1/values/{lead['id']}/raw-values", headers=as_user("p47at"),
                                   json={"reading": {"decimal": reading}, "t": f"2026-10-29T{hour}:00:00Z"})
        result["raw-value-recorded"] = recorded.json()
        result["collection"] = get(f"/v1/collections/{created.json()['id']}")
        result["error-four-eyes"] = client.post(f"/v1/raw-values/{recorded.json()['id']}/accept",
                                                headers=as_user("p47at")).json()

        result["person-created"] = client.post("/v1/people", headers=as_user("grindel"), json={
            "given_name": "Ash", "surname": "Science Officer", "linux_user": "ash", "organization_id": demo.LAB,
            "position": "Mikrobiologie", "roles": ["analyst"], "note": "Neu im Labor"}).json()
        ripley = get(f"/v1/people/{demo.RIPLEY}")
        result["person-roles-set"] = client.put(
            f"/v1/people/{demo.RIPLEY}/roles", json={"roles": ["analyst", "reviewer"], "note": "Übernimmt die QS"},
            headers=as_user("grindel", **{"If-Match": ripley["etag"]})).json()
        result["person-history"] = get(f"/v1/people/{demo.RIPLEY}/history")
        return result


def write(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for name, body in responses().items():
        (target / f"{name}.json").write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else TARGET
    write(target)
    print(f"wrote {target}")
