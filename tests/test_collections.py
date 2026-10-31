from sqlalchemy import func, select

from lucentapi.numbering import LUCENT
from lucentapi.schema import collection, log_table, raw_value, value

WATER_ROUTINE = [1, 2, 3, 4, 7]  # sampling, sensory, pH, conductivity, microbiology: 8 effects


def register(client, as_user, **body):
    body = {"matrix_id": 3, "collection_class": "spot sample", "spot_id": 5, "action_ids": WATER_ROUTINE, **body}
    response = client.post("/v1/collections", json=body, headers=as_user("worf", "sampler"))
    assert response.status_code == 201, response.text
    return response.json()


def record(client, as_user, value_id, reading, user="jd", **extra):
    return client.post(f"/v1/values/{value_id}/raw-values", json={"reading": reading, **extra},
                       headers=as_user(user, "analyst"))


def value_for(detail: dict, effect_id: int) -> dict:
    return next(v for v in detail["values"] if v["effect_id"] == effect_id)


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
    assert {c["spot_id"] for c in items} == {9, None}


def test_sampling_time_comes_from_its_raw_value(client, as_user):
    approved = client.get("/v1/collections?status=approved&limit=1", headers=as_user("grindel")).json()["items"][0]
    detail = client.get(f"/v1/collections/{approved['id']}", headers=as_user("grindel")).json()
    sampling = value_for(detail, 1 if detail["collection_class"] != "time series" else 15)
    assert detail["sampled_at"] == sampling["raw_values"][0]["reading"]["datetime"]
    assert detail["sampled_by"] == sampling["raw_values"][0]["person_id"]
    assert detail["registered_by"] == 1  # the demo data was loaded as the System User


def test_register_plans_values_and_records_the_sampling_time(client, engine, as_user):
    with engine.connect() as conn:
        last_id = conn.scalar(select(func.max(collection.c.id_collection)))
    created = register(client, as_user, sampled_at="2026-10-29T08:15:00+01:00", note="Sonderprobe")
    assert created["id"] == last_id + 1
    assert created["name"] == LUCENT.identifier(last_id + 1 + 3000)
    assert (created["planned"], created["measured"], created["status"]) == (8, 1, "in_analysis")
    assert created["sampled_at"] == "2026-10-29T07:15:00Z"
    assert (created["sampled_by"], created["registered_by"]) == (3, 3)
    assert sorted(v["effect_id"] for v in created["values"]) == [1, 2, 3, 4, 5, 6, 13, 14]


def test_register_is_logged_by_the_triggers(client, engine, as_user):
    created = register(client, as_user, note="Sonderprobe")
    log = log_table(collection)
    with engine.connect() as conn:
        entries = conn.execute(select(log.c.modification, log.c.id_editor, log.c.note)
                               .where(log.c.id_collection == created["id"]).order_by(log.c.id_l_collection)).all()
        values_logged = conn.scalar(select(func.count()).select_from(log_table(value))
                                    .where(log_table(value).c.id_editor == 3, log_table(value).c.note == "Sonderprobe"))
    assert entries == [("I", 3, "Sonderprobe"), ("U", 3, "Sonderprobe")]  # inserted, then named
    assert values_logged == 8


def test_sampling_time_needs_its_parameter_planned(client, as_user):
    response = client.post("/v1/collections", headers=as_user("worf", "sampler"), json={
        "matrix_id": 3, "collection_class": "spot sample", "spot_id": 5, "action_ids": [3],
        "sampled_at": "2026-10-29T08:15:00Z"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "sampling_time_not_planned"


def test_register_checks_its_input(client, as_user):
    headers = as_user("worf", "sampler")
    base = {"matrix_id": 3, "collection_class": "spot sample", "spot_id": 5}
    for body, code in [
        ({"spot_id": None}, "invalid_request"),
        ({"site_id": 1}, "invalid_request"),
        ({"collection_class": "grab sample"}, "invalid_request"),
        ({"matrix_id": 99}, "unknown_reference"),
        ({"action_ids": [99]}, "unknown_reference"),
        ({"vis": 0}, "invalid_request"),
    ]:
        response = client.post("/v1/collections", json={**base, **body}, headers=headers)
        assert (response.status_code, response.json()["error"]["code"]) == (422, code), body
    inactive = client.post("/v1/collections", json={**base, "action_ids": [10]}, headers=headers)
    assert (inactive.status_code, inactive.json()["error"]["code"]) == (409, "action_inactive")


def test_plan_more(client, as_user):
    created = register(client, as_user)
    response = client.post(f"/v1/collections/{created['id']}/plan", json={"action_ids": [5, 3]},
                           headers=as_user("worf", "sampler"))
    assert response.status_code == 200
    assert response.json()["planned"] == 11  # anions add three, pH was planned already


def test_values_move_the_status(client, as_user):
    created = register(client, as_user, action_ids=[2, 3])
    readings = {3: {"text": "farblos"}, 4: {"boolean": False}, 5: {"decimal": 7.6}}
    first = record(client, as_user, value_for(created, 5)["id"], readings.pop(5))
    assert first.status_code == 201
    assert (first.json()["person_id"], first.json()["device_id"]) == (2, 1)  # jd, the action's active device
    detail = client.get(f"/v1/collections/{created['id']}", headers=as_user("grindel")).json()
    assert detail["status"] == "in_analysis"

    for effect_id, reading in readings.items():
        assert record(client, as_user, value_for(created, effect_id)["id"], reading).status_code == 201
    assert client.get(f"/v1/collections/{created['id']}", headers=as_user("grindel")).json()["status"] == "in_review"

    accepted = client.post(f"/v1/collections/{created['id']}/accept", headers=as_user("xx121", "reviewer"))
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "approved"


def test_reading_must_match_the_value_class(client, as_user):
    created = register(client, as_user)
    response = record(client, as_user, value_for(created, 5)["id"], {"text": "neutral"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "wrong_value_class"


def test_reading_needs_exactly_one_kind(client, as_user):
    created = register(client, as_user)
    assert record(client, as_user, value_for(created, 5)["id"], {"decimal": 7, "text": "x"}).status_code == 422
    assert record(client, as_user, value_for(created, 5)["id"], {}).status_code == 422


def test_repetitions_and_limits(client, as_user):
    created = register(client, as_user, matrix_id=2, spot_id=3, action_ids=[6])
    lead = value_for(created, 10)
    assert {limit["document_id"] for limit in lead["limits"]} == {12, 13}  # TrinkwV and GrwV, from the action
    first = record(client, as_user, lead["id"], {"decimal": 12.4}, user="p47at").json()
    second = record(client, as_user, lead["id"], {"decimal": 9.1}, user="p47at").json()
    assert first["t"] < second["t"]  # one raw value per value and time: the next free second
    assert (first["exceeds"], second["exceeds"]) == (True, False)
    assert first["unit_id"] == 4  # the parameter's unit, µg/L
    detail = client.get(f"/v1/collections/{created['id']}", headers=as_user("grindel")).json()
    assert [r["reading"]["decimal"] for r in value_for(detail, 10)["raw_values"]] == [12.4, 9.1]
    assert detail["exceedances"] == 1


def test_one_raw_value_per_value_and_time(client, as_user):
    created = register(client, as_user)
    pH = value_for(created, 5)["id"]
    assert record(client, as_user, pH, {"decimal": 7.5}, t="2026-10-29T10:00:00Z").status_code == 201
    taken = record(client, as_user, pH, {"decimal": 7.6}, t="2026-10-29T10:00:00Z")
    assert (taken.status_code, taken.json()["error"]["code"]) == (409, "time_taken")


def test_inactive_device_is_refused(client, as_user):
    created = register(client, as_user)
    response = record(client, as_user, value_for(created, 5)["id"], {"decimal": 7.5}, device_id=2)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "device_inactive"


def test_four_eyes(client, engine, as_user):
    created = register(client, as_user)
    measured = record(client, as_user, value_for(created, 5)["id"], {"decimal": 7.5}, user="xx121").json()

    own = client.post(f"/v1/raw-values/{measured['id']}/accept", headers=as_user("xx121", "reviewer"))
    assert own.status_code == 403
    assert own.json()["error"]["code"] == "four_eyes"
    assert own.json()["error"]["details"] == {"raw_value_ids": [measured["id"]]}
    all_own = client.post(f"/v1/collections/{created['id']}/accept", headers=as_user("xx121", "reviewer"))
    assert all_own.status_code == 403

    other = client.post(f"/v1/raw-values/{measured['id']}/accept", json={"note": "plausibel"},
                        headers=as_user("p47at", "reviewer"))
    assert other.status_code == 200
    assert other.json()["accepted"] is True
    log = log_table(raw_value)
    with engine.connect() as conn:
        last = conn.execute(select(log.c.modification, log.c.id_editor, log.c.note)
                            .where(log.c.id_raw_value == measured["id"]).order_by(log.c.id_l_raw_value.desc())).first()
    assert tuple(last) == ("U", 5, "plausibel")


def test_accept_honours_if_match(client, as_user):
    created = register(client, as_user)
    measured = record(client, as_user, value_for(created, 5)["id"], {"decimal": 7.5}).json()
    headers = as_user("xx121", "reviewer")
    client.post(f"/v1/raw-values/{measured['id']}/accept", headers=headers)
    client.post(f"/v1/raw-values/{measured['id']}/withdraw", headers=headers)
    # Accepted and withdrawn again: same content, same ETag, so the check passes …
    fresh = client.post(f"/v1/raw-values/{measured['id']}/accept", headers={**headers, "If-Match": measured["etag"]})
    assert fresh.status_code == 200
    # … but now it differs.
    stale = client.post(f"/v1/raw-values/{measured['id']}/withdraw",
                        headers={**headers, "If-Match": measured["etag"]})
    assert stale.status_code == 412
    assert stale.headers["ETag"] == fresh.json()["etag"]


def test_series(client, as_user):
    points = client.get("/v1/series?spot_id=9&parameter_id=7", headers=as_user("grindel")).json()
    assert len(points) > 4
    assert [p["sampled_at"] for p in points] == sorted(p["sampled_at"] for p in points)
    assert all(p["exceeds"] == (p["value"] > 50) for p in points)
    assert any(p["exceeds"] for p in points)


def test_series_needs_one_location(client, as_user):
    response = client.get("/v1/series?parameter_id=7", headers=as_user("grindel"))
    assert response.status_code == 422


def test_values_in_bulk(client, as_user):
    headers = as_user("grindel")
    page = client.get("/v1/values?limit=50", headers=headers).json()
    assert len(page["items"]) == 50 and page["total"] > 50
    ids = [v["id"] for v in page["items"]]
    assert ids == sorted(ids)
    some = client.get("/v1/values?collection_id=1&collection_id=2&limit=5000", headers=headers).json()["items"]
    assert {v["collection_id"] for v in some} == {1, 2}
    detail = client.get("/v1/collections/1", headers=headers).json()
    assert [v for v in some if v["collection_id"] == 1] == detail["values"]


def test_values_in_a_time_window(client, as_user):
    headers = as_user("grindel")
    recent = client.get("/v1/collections?sampled_from=2026-10-15T00:00:00Z&limit=1000", headers=headers).json()
    values = client.get("/v1/values?sampled_from=2026-10-15T00:00:00Z&limit=5000", headers=headers).json()
    assert {v["collection_id"] for v in values["items"]} == {c["id"] for c in recent["items"]}
    assert all(c["sampled_at"] >= "2026-10-15" for c in recent["items"])
