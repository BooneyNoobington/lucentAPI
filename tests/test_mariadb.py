"""lucentAPI against the real lucentBASE on MariaDB: its DDL, CHECK constraints and log triggers.

Runs only when two environment variables are set, for example:

    LUCENTBASE_DIR=../lucentBase
    MARIADB_TEST_URL='mysql+pymysql://lucentapi_test:<password>@127.0.0.1/lucent_test'

The test database is built fresh from lucentBase's DATABASE_GENERATION.SQL with the `mariadb`
client, run as the current OS user, who must be allowed to create databases and users
(MARIADB_TEST_ADMIN can name another client command). Its name must end in `_test`;
it is dropped and recreated. The tests then connect as the URL's user, which gets only
SELECT, INSERT and UPDATE, like the API's account (sql/lucentapi-account.sql).
"""

import os
import re
import subprocess
import warnings
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, make_url, select, text
from sqlalchemy.exc import OperationalError, SAWarning
from sqlalchemy.types import String

from lucentapi import demo
from lucentapi.checks import check_schema
from lucentapi.db import make_engine
from lucentapi.main import create_app
from lucentapi.schema import Polygon, collection, log_table, metadata, raw_value

from conftest import NOW, signed_in

URL = os.environ.get("MARIADB_TEST_URL")
BASE = os.environ.get("LUCENTBASE_DIR")
pytestmark = pytest.mark.skipif(not (URL and BASE), reason="set MARIADB_TEST_URL and LUCENTBASE_DIR")


def generation_sql(base: Path, database: str) -> str:
    """lucentBASE's generation script, aimed at `database` instead of `lucent`."""
    sql = (base / "DATABASE_GENERATION.SQL").read_text().replace("{linux_user}", "lucent_test_owner")
    for old, new in [("DATABASE IF EXISTS lucent;", f"DATABASE IF EXISTS {database};"),
                     ("DATABASE lucent\n", f"DATABASE {database}\n"),
                     ("ON lucent.*", f"ON {database}.*"),
                     ("USE lucent;", f"USE {database};")]:
        assert old in sql, f"lucentBase's DATABASE_GENERATION.SQL changed: {old!r} is gone"
        sql = sql.replace(old, new)
    assert not re.search(r"\blucent\s*(;|\.\*|\n)", sql), "the script still names the database lucent"
    return sql


@pytest.fixture(scope="module")
def mariadb():
    url = make_url(URL)
    assert url.database.endswith("_test"), "the test database is dropped: its name must end in _test"
    admin = os.environ.get("MARIADB_TEST_ADMIN", "mariadb").split()
    run = dict(text=True, check=True, capture_output=True)
    subprocess.run(admin, input=generation_sql(Path(BASE), url.database), cwd=BASE, **run)
    accounts = "".join(
        f"CREATE OR REPLACE USER '{url.username}'@'{host}' IDENTIFIED BY '{url.password}';"
        f"GRANT SELECT, INSERT, UPDATE ON `{url.database}`.* TO '{url.username}'@'{host}';"
        for host in ("localhost", "127.0.0.1"))
    subprocess.run(admin, input=accounts, **run)
    engine = make_engine(URL)
    with engine.begin() as conn:
        demo.load(conn, now=NOW)
    yield engine
    engine.dispose()


@pytest.fixture
def as_user(signer, mariadb):
    return signed_in(signer, mariadb)


@pytest.fixture
def maria(mariadb, settings) -> TestClient:
    return TestClient(create_app(settings, mariadb))


def test_schema_matches_lucentbase(mariadb):
    check_schema(mariadb)  # tables, columns, log triggers
    inspector = inspect(mariadb)
    differences = []
    for table in metadata.tables.values():
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", "Did not recognize type", SAWarning)
            real = {c["name"]: c for c in inspector.get_columns(table.name)}
        for column in table.columns:
            found = real[column.name]
            if not column.primary_key and found["nullable"] != column.nullable:
                differences.append(f"{table.name}.{column.name}: nullable {found['nullable']}")
            if isinstance(column.type, String) and getattr(found["type"], "length", None) != column.type.length:
                differences.append(f"{table.name}.{column.name}: length {found['type'].length}")
    with mariadb.connect() as conn:  # reflection doesn't know spatial types
        polygons = {(row[0], row[1]) for row in conn.execute(text(
            "SELECT table_name, column_name FROM information_schema.columns"
            " WHERE table_schema = DATABASE() AND data_type = 'polygon'"))}
    expected = {(t.name, c.name) for t in metadata.tables.values() for c in t.columns if isinstance(c.type, Polygon)}
    assert polygons == expected
    assert differences == []


def test_workflow_logged_by_lucentbase(maria, mariadb, as_user):
    created = maria.post("/v1/collections", headers=as_user("worf", "sampler"), json={
        "matrix_id": 3, "collection_class": "spot sample", "spot_id": 6, "action_ids": [1, 3],
        "sampled_at": "2026-10-29T08:15:00Z", "note": "Sonderprobe"})
    assert created.status_code == 201, created.text
    detail = created.json()
    assert (detail["status"], detail["planned"], detail["sampled_at"]) == ("in_analysis", 3, "2026-10-29T08:15:00Z")
    assert detail["registered_by"] == 3 and detail["registered_at"] is not None

    pH = next(v for v in detail["values"] if v["effect_id"] == 5)
    measured = maria.post(f"/v1/values/{pH['id']}/raw-values", json={"reading": {"decimal": 9.8}},
                          headers=as_user("jd", "analyst"))
    assert measured.status_code == 201, measured.text
    assert measured.json()["exceeds"] is True  # TrinkwV: pH 6.5–9.5

    own = maria.post(f"/v1/raw-values/{measured.json()['id']}/accept", headers=as_user("jd", "reviewer"))
    assert own.json()["error"]["code"] == "four_eyes"
    accepted = maria.post(f"/v1/raw-values/{measured.json()['id']}/accept", json={"note": "plausibel"},
                          headers=as_user("xx121", "reviewer"))
    assert accepted.status_code == 200 and accepted.json()["accepted"] is True
    stale = maria.post(f"/v1/raw-values/{measured.json()['id']}/withdraw",
                       headers=as_user("xx121", "reviewer", **{"If-Match": measured.json()["etag"]}))
    assert stale.status_code == 412

    with mariadb.connect() as conn:
        log = log_table(collection)
        assert conn.execute(select(log.c.modification, log.c.id_editor, log.c.note)
                            .where(log.c.id_collection == detail["id"]).order_by(log.c.id_l_collection)).all() \
            == [("I", 3, "Sonderprobe"), ("U", 3, "Sonderprobe")]
        log = log_table(raw_value)
        entries = conn.execute(select(log.c.modification, log.c.id_editor, log.c.note, log.c.checksum)
                               .where(log.c.id_raw_value == measured.json()["id"])
                               .order_by(log.c.id_l_raw_value)).all()
        assert [e[:3] for e in entries] == [("I", 2, "Inserted"), ("U", 6, "plausibel")]
        assert all(re.fullmatch(r"[0-9a-f]{64}", e.checksum) for e in entries)
        # Session variables don't leak into the next use of a pooled connection.
        assert conn.execute(text("SELECT @current_user_id, @change_note")).one() == (None, None)


def test_tasks_and_messages(maria, as_user):
    tasks = {t["id"]: t for t in maria.get("/v1/tasks", headers=as_user("jd")).json()}
    done = maria.patch("/v1/tasks/1", json={"done": True}, headers=as_user("jd", **{"If-Match": tasks[1]["etag"]}))
    assert done.status_code == 200 and done.json()["done"] is True
    inbox = maria.get("/v1/messages", headers=as_user("grindel")).json()
    assert len(inbox) == 3 and all(m["sent_at"] for m in inbox)


def test_people_and_roles_logged_by_lucentbase(maria, mariadb, as_user):
    # lucentBASE's initial data makes grindel admin; the token only says who's calling.
    assert maria.get("/v1/me", headers=as_user("grindel")).json()["roles"] == ["admin"]
    created = maria.post("/v1/people", headers=as_user("grindel"), json={
        "given_name": "Ash", "surname": "Science Officer", "linux_user": "ash", "organization_id": demo.LAB,
        "position": "Mikrobiologie", "roles": ["analyst"], "note": "Neu im Labor"})
    assert created.status_code == 201, created.text
    ash = created.json()
    assert (ash["roles"], ash["position"], ash["active"]) == (["analyst"], "Mikrobiologie", True)

    roles = maria.put(f"/v1/people/{ash['id']}/roles", json={"roles": ["reviewer"], "note": "Übernimmt die QS"},
                      headers=as_user("grindel", **{"If-Match": ash["etag"]}))
    assert roles.status_code == 200, roles.text
    assert maria.get("/v1/me", headers=as_user("ash")).json()["roles"] == ["reviewer"]

    history = maria.get(f"/v1/people/{ash['id']}/history", headers=as_user("grindel")).json()
    assert [entry["note"] for entry in history][:2] == ["Granted reviewer: Übernimmt die QS",
                                                         "Revoked analyst: Übernimmt die QS"]
    assert history[-1]["note"] == "Created: Neu im Labor" and all(e["editor_id"] == demo.SYSTEM for e in history)

    gone = maria.patch(f"/v1/people/{ash['id']}", json={"active": False},
                       headers=as_user("grindel", **{"If-Match": roles.json()["etag"]}))
    assert gone.status_code == 200 and gone.json()["active"] is False
    assert maria.get("/v1/me", headers=as_user("ash")).json()["error"]["code"] == "inactive_person"


def test_sites_are_spatial_polygons(maria, mariadb, as_user):
    border = [{"latitude": 49.016, "longitude": 8.325}, {"latitude": 49.016, "longitude": 8.332},
              {"latitude": 49.012, "longitude": 8.332}, {"latitude": 49.012, "longitude": 8.325}]
    created = maria.post("/v1/sites", json={"name": "Rheinhafen", "border": border}, headers=as_user("worf"))
    assert created.status_code == 201, created.text
    assert created.json()["border"] == border
    with mariadb.connect() as conn:
        kind, area, inside = conn.execute(text(
            "SELECT ST_GeometryType(borders), ST_Area(borders), ST_Contains(borders, POINT(8.329, 49.014))"
            " FROM site WHERE name = 'Rheinhafen'")).one()
    assert kind == "POLYGON" and round(area, 6) == 0.000028 and inside == 1
    spot = maria.post("/v1/spots", json={"name": "Pegel Hafen", "x": 49.014, "y": 8.329,
                                         "site_id": created.json()["id"]}, headers=as_user("worf"))
    assert spot.status_code == 201 and spot.json()["site_id"] == created.json()["id"]

    smaller = border[:3]
    changed = maria.patch(f"/v1/sites/{created.json()['id']}", json={"border": smaller},
                          headers=as_user("worf", **{"If-Match": created.json()["etag"]}))
    assert changed.status_code == 200, changed.text
    with mariadb.connect() as conn:
        area = conn.execute(text("SELECT ST_Area(borders) FROM site WHERE name = 'Rheinhafen'")).scalar_one()
    assert round(area, 6) == 0.000014


def test_the_api_account_cannot_delete(mariadb):
    with mariadb.connect() as conn, pytest.raises(OperationalError, match="DELETE command denied"):
        conn.execute(text("DELETE FROM task"))
