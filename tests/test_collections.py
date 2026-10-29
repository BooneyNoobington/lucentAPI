from sqlalchemy import func, select

from lucentapi.audit import verify_chain
from lucentapi.numbering import LUCENT
from lucentapi.schema import collection, log_table, value


def register(client, as_user, **body):
    body = {"matrix_id": 10, "class_id": 1, "spot_id": 6, **body}
    response = client.post("/v1/collections", json=body, headers=as_user("worf", "sampler"))
    assert response.status_code == 201, response.text
    return response


def record(client, as_user, collection_id, av_id, reading, user="jd"):
    return client.post(f"/v1/collections/{collection_id}/values",
                       json={"action_variable_id": av_id, "reading": reading}, headers=as_user(user, "analyst"))


def test_list_is_newest_first_and_paged(client, as_user):
    page = client.get("/v1/collections?limit=5", headers=as_user("grindel")).json()
    ids = [c["id"] for c in page["items"]]
    assert ids == sorted(ids, reverse=True)
    assert len(ids) == 5
    assert page["total"] > 5


def test_status_filter(client, as_user):
    page = client.get("/v1/collections?status=in_review&limit=1000", headers=as_user("grindel")).json()
    assert page["total"] == len(page["items"]) > 0
    assert {c["status"] for c in page["items"]} == {"in_review"}


def test_site_includes_its_spots(client, as_user):
    items = client.get("/v1/collections?site_id=3&limit=1000", headers=as_user("grindel")).json()["items"]
    assert {c["spot_id"] for c in items} == {10, None}


def test_register_gives_the_next_identifier(client, engine, as_user):
    with engine.connect() as conn:
        last_id = conn.scalar(select(func.max(collection.c.id_collection)))
    response = register(client, as_user, sampled_at="2026-10-29T08:15:00+01:00")
    created = response.json()
    assert created["id"] == last_id + 1
    assert created["name"] == LUCENT.identifier(last_id + 1 + 3000)
    assert created["status"] == "registered"
    assert created["planned"] == 7
    assert created["sampler_id"] == 26
    assert created["sampled_at"] == "2026-10-29T07:15:00Z"
    assert response.headers["Location"] == f"/v1/collections/{created['id']}"


def test_register_is_logged(client, engine, as_user):
    created = register(client, as_user, note="Sonderprobe").json()
    with engine.connect() as conn:
        log = conn.execute(select(log_table(collection)).where(
            log_table(collection).c.id_collection == created["id"])).mappings().one()
        assert log["id_editor"] == 26
        assert log["note"] == "Sonderprobe"
        assert '"op":"insert"' in log["change"] and created["name"] in log["change"]
        assert verify_chain(conn, collection, created["id"]) == []


def test_register_needs_a_spot_or_a_site(client, as_user):
    for body in ({"spot_id": None}, {"site_id": 1}):
        response = client.post("/v1/collections", json={"matrix_id": 10, "class_id": 1, "spot_id": 6, **body},
                               headers=as_user("worf", "sampler"))
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"


def test_register_checks_references(client, as_user):
    response = client.post("/v1/collections", json={"matrix_id": 99, "class_id": 1, "spot_id": 6},
                           headers=as_user("worf", "sampler"))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unknown_reference"


def test_unknown_fields_are_refused(client, as_user):
    response = client.post("/v1/collections", json={"matrix_id": 10, "class_id": 1, "spot_id": 6, "vis": 0},
                           headers=as_user("worf", "sampler"))
    assert response.status_code == 422


def test_values_move_the_status(client, as_user):
    created = register(client, as_user).json()
    planned = {1: {"decimal": 12.3}, 2: {"text": "farblos"}, 3: {"boolean": False}, 4: {"decimal": 7.6},
               5: {"decimal": 640}, 12: {"decimal": 0}, 13: {"decimal": 0}}
    first = record(client, as_user, created["id"], 1, planned.pop(1), user="worf")
    assert first.status_code == 201
    assert first.json()["person_id"] == 26
    assert first.json()["device_id"] == 11  # the action's active device
    detail = client.get(f"/v1/collections/{created['id']}", headers=as_user("grindel")).json()
    assert detail["status"] == "in_analysis"

    for av, reading in planned.items():
        assert record(client, as_user, created["id"], av, reading).status_code == 201
    assert client.get(f"/v1/collections/{created['id']}", headers=as_user("grindel")).json()["status"] == "in_review"

    accepted = client.post(f"/v1/collections/{created['id']}/accept", headers=as_user("xx121", "reviewer"))
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "approved"


def test_reading_must_match_the_value_class(client, as_user):
    created = register(client, as_user).json()
    response = record(client, as_user, created["id"], 4, {"text": "neutral"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "wrong_value_class"


def test_reading_needs_exactly_one_kind(client, as_user):
    created = register(client, as_user).json()
    assert record(client, as_user, created["id"], 4, {"decimal": 7, "text": "x"}).status_code == 422
    assert record(client, as_user, created["id"], 4, {}).status_code == 422


def test_only_planned_variables(client, as_user):
    created = register(client, as_user).json()  # Routineprobe: no metals
    response = record(client, as_user, created["id"], 9, {"decimal": 3.0})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "not_planned"


def test_another_reading_is_a_repetition_and_limits_are_checked(client, as_user):
    created = register(client, as_user, class_id=3, spot_id=4, matrix_id=9).json()
    first = record(client, as_user, created["id"], 9, {"decimal": 12.4}, user="p47at").json()
    second = record(client, as_user, created["id"], 9, {"decimal": 11.9}, user="p47at").json()
    assert (first["repetition"], second["repetition"]) == (1, 2)
    assert first["exceeds"] and second["exceeds"]
    detail = client.get(f"/v1/collections/{created['id']}", headers=as_user("grindel")).json()
    assert detail["exceedances"] == 2


def test_inactive_device_is_refused(client, as_user):
    created = register(client, as_user).json()
    response = client.post(f"/v1/collections/{created['id']}/values",
                           json={"action_variable_id": 4, "reading": {"decimal": 7.5}, "device_id": 2},
                           headers=as_user("jd", "analyst"))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "device_inactive"


def test_four_eyes(client, engine, as_user):
    created = register(client, as_user).json()
    measured = record(client, as_user, created["id"], 4, {"decimal": 7.5}, user="xx121").json()

    own = client.post(f"/v1/values/{measured['id']}/accept", headers=as_user("xx121", "reviewer"))
    assert own.status_code == 403
    assert own.json()["error"]["code"] == "four_eyes"
    assert own.json()["error"]["details"] == {"value_ids": [measured["id"]]}

    all_own = client.post(f"/v1/collections/{created['id']}/accept", headers=as_user("xx121", "reviewer"))
    assert all_own.status_code == 403

    other = client.post(f"/v1/values/{measured['id']}/accept", headers=as_user("p47at", "reviewer"))
    assert other.status_code == 200
    assert other.json()["accepted"] is True
    with engine.connect() as conn:
        assert verify_chain(conn, value, measured["id"]) == []


def test_accept_honours_if_match(client, as_user):
    created = register(client, as_user).json()
    measured = record(client, as_user, created["id"], 4, {"decimal": 7.5}).json()
    headers = as_user("xx121", "reviewer")
    client.post(f"/v1/values/{measured['id']}/accept", headers=headers)
    client.post(f"/v1/values/{measured['id']}/withdraw", headers=headers)
    # The value went back and forth: same content, same ETag, so the stale check passes …
    fresh = client.post(f"/v1/values/{measured['id']}/accept", headers={**headers, "If-Match": measured["etag"]})
    assert fresh.status_code == 200
    # … but now it differs.
    stale = client.post(f"/v1/values/{measured['id']}/withdraw", headers={**headers, "If-Match": measured["etag"]})
    assert stale.status_code == 412
    assert stale.headers["ETag"] == fresh.json()["etag"]


def test_series(client, as_user):
    points = client.get("/v1/series?spot_id=10&action_variable_id=6", headers=as_user("grindel")).json()
    assert len(points) > 4
    assert [p["sampled_at"] for p in points] == sorted(p["sampled_at"] for p in points)
    assert all(p["exceeds"] == (p["value"] > 50) for p in points)
    assert any(p["exceeds"] for p in points)


def test_series_needs_one_location(client, as_user):
    response = client.get("/v1/series?action_variable_id=6", headers=as_user("grindel"))
    assert response.status_code == 422


def test_values_in_bulk(client, as_user):
    headers = as_user("grindel")
    page = client.get("/v1/values?limit=50", headers=headers).json()
    assert len(page["items"]) == 50 and page["total"] > 50
    ids = [v["id"] for v in page["items"]]
    assert ids == sorted(ids)
    rest = client.get(f"/v1/values?limit=5000&offset=50", headers=headers).json()
    assert len(rest["items"]) == page["total"] - 50

    some = client.get("/v1/values?collection_id=1&collection_id=2&limit=5000", headers=headers).json()["items"]
    assert {v["collection_id"] for v in some} == {1, 2}
    detail = client.get("/v1/collections/1", headers=headers).json()
    assert [v for v in some if v["collection_id"] == 1] == detail["values"]


def test_values_in_a_time_window(client, as_user):
    headers = as_user("grindel")
    recent = client.get("/v1/collections?sampled_from=2026-10-15T00:00:00Z&limit=1000", headers=headers).json()
    values = client.get("/v1/values?sampled_from=2026-10-15T00:00:00Z&limit=5000", headers=headers).json()
    assert {v["collection_id"] for v in values["items"]} <= {c["id"] for c in recent["items"]}
    assert sum(c["measured"] > 0 for c in recent["items"]) == len({v["collection_id"] for v in values["items"]})
