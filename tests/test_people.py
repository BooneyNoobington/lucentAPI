from sqlalchemy import select

from lucentapi import demo
from lucentapi.schema import j_person_role, log_table


def person(client, as_user, person_id: int) -> dict:
    return client.get(f"/v1/people/{person_id}", headers=as_user("grindel")).json()


def test_people_with_roles(client, as_user):
    people = {p["linux_user"]: p for p in client.get("/v1/people", headers=as_user("jd")).json()}
    assert people["grindel"]["roles"] == ["admin"]
    assert people["worf"]["roles"] == ["sampler", "analyst"]
    assert people["p47at"]["roles"] == ["reviewer", "admin"]
    assert all(p["active"] and p["etag"] for p in people.values())


def test_roles(client, as_user):
    roles = client.get("/v1/roles", headers=as_user("jd")).json()
    assert [r["name"] for r in roles] == ["sampler", "analyst", "reviewer", "admin"]
    assert roles[2]["description"] == "Accepts and withdraws raw values, except their own."


def test_create_person(client, as_user):
    body = {"given_name": "Ash", "surname": "Science Officer", "linux_user": "ash", "organization_id": demo.LAB,
            "position": "Mikrobiologie", "roles": ["analyst", "sampler"], "note": "Neu im Labor"}
    created = client.post("/v1/people", json=body, headers=as_user("grindel"))
    assert created.status_code == 201, created.text
    ash = created.json()
    assert created.headers["Location"].endswith(f"/v1/people/{ash['id']}")
    assert (ash["roles"], ash["position"], ash["organization_id"]) == (["sampler", "analyst"], "Mikrobiologie", demo.LAB)
    # The new person can sign in at once, with what lucentBASE grants.
    assert client.get("/v1/me", headers=as_user("ash")).json()["roles"] == ["sampler", "analyst"]


def test_only_admins_manage_people(client, as_user):
    body = {"given_name": "Ash", "surname": "Science Officer"}
    response = client.post("/v1/people", json=body, headers=as_user("p47at", "reviewer"))
    assert response.status_code == 403 and response.json()["error"]["code"] == "missing_role"
    ripley = person(client, as_user, demo.RIPLEY)
    response = client.put(f"/v1/people/{demo.RIPLEY}/roles", json={"roles": ["reviewer"]},
                          headers=as_user("xx121", **{"If-Match": ripley["etag"]}))
    assert response.status_code == 403


def test_linux_user_must_be_free(client, as_user):
    body = {"given_name": "Another", "surname": "Worf", "linux_user": "worf"}
    response = client.post("/v1/people", json=body, headers=as_user("grindel"))
    assert response.status_code == 409 and response.json()["error"]["code"] == "linux_user_taken"
    dorn = person(client, as_user, demo.DORN)
    response = client.patch(f"/v1/people/{demo.DORN}", json={"linux_user": "jd"},
                            headers=as_user("grindel", **{"If-Match": dorn["etag"]}))
    assert response.status_code == 409


def test_grant_reviewer_to_ripley(client, as_user):
    """What Picard asks for in the demo inbox."""
    ripley = person(client, as_user, demo.RIPLEY)
    response = client.put(f"/v1/people/{demo.RIPLEY}/roles",
                          json={"roles": ["analyst", "reviewer"], "note": "Übernimmt die QS"},
                          headers=as_user("grindel", **{"If-Match": ripley["etag"]}))
    assert response.status_code == 200, response.text
    assert response.json()["roles"] == ["analyst", "reviewer"]
    assert response.json()["etag"] != ripley["etag"]
    # Her next request may accept raw values.
    assert client.get("/v1/me", headers=as_user("xx121")).json()["roles"] == ["analyst", "reviewer"]
    # The old ETag no longer fits.
    stale = client.put(f"/v1/people/{demo.RIPLEY}/roles", json={"roles": []},
                       headers=as_user("grindel", **{"If-Match": ripley["etag"]}))
    assert stale.status_code == 412


def test_changing_people_needs_the_etag(client, as_user):
    response = client.patch(f"/v1/people/{demo.DORN}", json={"surname": "Worf"}, headers=as_user("grindel"))
    assert response.status_code == 428


def test_update_person(client, as_user, engine):
    dorn = person(client, as_user, demo.DORN)
    response = client.patch(f"/v1/people/{demo.DORN}", json={"surname": "Worf", "position": "Sicherheit"},
                            headers=as_user("grindel", **{"If-Match": dorn["etag"]}))
    assert response.status_code == 200, response.text
    assert (response.json()["surname"], response.json()["position"]) == ("Worf", "Sicherheit")
    history = client.get(f"/v1/people/{demo.DORN}/history", headers=as_user("jd")).json()
    assert [(e["subject"], e["note"]) for e in history[:2]] == [("organization", "Changed position"),
                                                                ("person", "Changed surname")]


def test_revoked_role_can_be_granted_again(client, as_user, engine):
    worf = person(client, as_user, demo.DORN)
    revoked = client.put(f"/v1/people/{demo.DORN}/roles", json={"roles": ["sampler"]},
                         headers=as_user("grindel", **{"If-Match": worf["etag"]}))
    granted = client.put(f"/v1/people/{demo.DORN}/roles", json={"roles": ["sampler", "analyst"]},
                         headers=as_user("grindel", **{"If-Match": revoked.json()["etag"]}))
    assert granted.json()["roles"] == ["sampler", "analyst"]
    with engine.connect() as conn:
        # One junction row per role, shown and hidden again; the log has every step.
        rows = conn.execute(select(j_person_role).where(j_person_role.c.id_person == demo.DORN)).all()
        assert len(rows) == 2
        notes = conn.scalars(select(log_table(j_person_role).c.note)
                             .where(log_table(j_person_role).c.id_j_person_role.in_([r.id_j_person_role for r in rows]))
                             .order_by(log_table(j_person_role).c.id_l_j_person_role)).all()
    assert notes[-2:] == ["Revoked analyst", "Granted analyst"]


def test_the_last_admin_stays_admin(client, as_user):
    picard = person(client, as_user, demo.PICARD)
    demoted = client.put(f"/v1/people/{demo.PICARD}/roles", json={"roles": ["reviewer"]},
                         headers=as_user("grindel", **{"If-Match": picard["etag"]}))
    assert demoted.status_code == 200  # grindel is still admin
    grindel = person(client, as_user, demo.SYSTEM)
    response = client.put(f"/v1/people/{demo.SYSTEM}/roles", json={"roles": []},
                          headers=as_user("grindel", **{"If-Match": grindel["etag"]}))
    assert response.status_code == 409 and response.json()["error"]["code"] == "last_admin"


def test_deactivated_people_cannot_sign_in(client, as_user):
    worf = person(client, as_user, demo.DORN)
    response = client.patch(f"/v1/people/{demo.DORN}", json={"active": False, "note": "Zur Enterprise gewechselt"},
                            headers=as_user("grindel", **{"If-Match": worf["etag"]}))
    assert response.status_code == 200 and response.json()["active"] is False
    me = client.get("/v1/me", headers=as_user("worf"))
    assert me.status_code == 403 and me.json()["error"]["code"] == "inactive_person"
    # They stay in the list and in the history.
    assert any(p["id"] == demo.DORN and not p["active"] for p in client.get("/v1/people", headers=as_user("jd")).json())
    history = client.get(f"/v1/people/{demo.DORN}/history", headers=as_user("jd")).json()
    assert history[0]["note"] == "Deactivated: Zur Enterprise gewechselt"


def test_nobody_deactivates_themselves(client, as_user):
    picard = person(client, as_user, demo.PICARD)
    response = client.patch(f"/v1/people/{demo.PICARD}", json={"active": False},
                            headers=as_user("p47at", **{"If-Match": picard["etag"]}))
    assert response.status_code == 409 and response.json()["error"]["code"] == "cannot_deactivate_yourself"


def test_the_last_admin_stays_active(client, as_user):
    picard = person(client, as_user, demo.PICARD)
    client.put(f"/v1/people/{demo.PICARD}/roles", json={"roles": ["reviewer"]},
               headers=as_user("grindel", **{"If-Match": picard["etag"]}))
    # grindel is the last admin; Picard, no longer admin, can't change anyone.
    grindel = person(client, as_user, demo.SYSTEM)
    response = client.patch(f"/v1/people/{demo.SYSTEM}", json={"active": False},
                            headers=as_user("p47at", **{"If-Match": grindel["etag"]}))
    assert response.status_code == 403


def test_position_needs_an_organization(client, as_user):
    body = {"given_name": "Ash", "surname": "Science Officer", "position": "Mikrobiologie"}
    response = client.post("/v1/people", json=body, headers=as_user("grindel"))
    assert response.status_code == 422 and response.json()["error"]["code"] == "position_needs_organization"
