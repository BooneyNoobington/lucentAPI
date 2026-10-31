from sqlalchemy import select

from lucentapi.geometry import crosses_itself
from lucentapi.schema import log_table, spot

# A square in the Rheinhafen, Karlsruhe, corner by corner.
HARBOUR = [{"latitude": 49.0160, "longitude": 8.3250}, {"latitude": 49.0160, "longitude": 8.3320},
           {"latitude": 49.0120, "longitude": 8.3320}, {"latitude": 49.0120, "longitude": 8.3250}]


def test_create_site(client, as_user):
    body = {"name": "Rheinhafen", "description": "Hafenbecken 2", "border": HARBOUR, "note": "Neues Messgebiet"}
    created = client.post("/v1/sites", json=body, headers=as_user("worf"))  # a sampler
    assert created.status_code == 201, created.text
    site = created.json()
    assert site["border"] == HARBOUR and site["crs_id"] == 1 and site["address"] is None
    listed = {s["name"]: s for s in client.get("/v1/sites", headers=as_user("jd")).json()}
    assert listed["Rheinhafen"]["border"] == HARBOUR


def test_create_spot_in_a_site(client, as_user):
    site = client.post("/v1/sites", json={"name": "Rheinhafen", "border": HARBOUR}, headers=as_user("worf")).json()
    body = {"name": "Pegel Hafen", "description": "Am Steg", "x": 49.0141, "y": 8.3290, "site_id": site["id"]}
    created = client.post("/v1/spots", json=body, headers=as_user("worf"))
    assert created.status_code == 201, created.text
    added = created.json()
    assert (added["x"], added["y"], added["site_id"], added["crs_id"]) == (49.0141, 8.3290, site["id"], 1)
    assert any(s["name"] == "Pegel Hafen" for s in client.get("/v1/spots", headers=as_user("jd")).json())


def test_adding_locations_needs_the_sampler_role(client, as_user):
    response = client.post("/v1/spots", json={"name": "Pegel", "x": 49.0, "y": 8.4}, headers=as_user("jd"))
    assert response.status_code == 403 and response.json()["error"]["code"] == "missing_role"


def test_names_are_unique(client, as_user):
    response = client.post("/v1/spots", json={"name": "GWM 12", "x": 49.0, "y": 8.4}, headers=as_user("worf"))
    assert response.status_code == 409 and response.json()["error"]["code"] == "name_taken"
    response = client.post("/v1/sites", json={"name": "Wasserwerk Ost", "border": HARBOUR}, headers=as_user("worf"))
    assert response.status_code == 409


def test_coordinates_are_checked(client, as_user):
    response = client.post("/v1/spots", json={"name": "Nirgendwo", "x": 91, "y": 8.4}, headers=as_user("worf"))
    assert response.status_code == 422
    response = client.post("/v1/sites", json={"name": "Zu klein", "border": HARBOUR[:2]}, headers=as_user("worf"))
    assert response.status_code == 422


def test_a_border_must_not_cross_itself(client, as_user):
    bowtie = [HARBOUR[0], HARBOUR[2], HARBOUR[1], HARBOUR[3]]
    response = client.post("/v1/sites", json={"name": "Schleife", "border": bowtie}, headers=as_user("worf"))
    assert response.status_code == 422 and response.json()["error"]["code"] == "border_crosses_itself"
    response = client.post("/v1/sites", json={"name": "Doppelt", "border": [HARBOUR[0]] * 3}, headers=as_user("worf"))
    assert response.json()["error"]["code"] == "border_too_small"


def test_crosses_itself():
    square = [(0, 0), (1, 0), (1, 1), (0, 1)]
    assert not crosses_itself(square)
    assert crosses_itself([(0, 0), (1, 1), (1, 0), (0, 1)])
    assert not crosses_itself([(0, 0), (2, 0), (2, 2), (1, 1), (0, 2)])  # concave is fine
    assert crosses_itself([(0, 0), (2, 0), (2, 2), (1, 0), (0, 2)])  # a corner on another edge


def test_move_and_rename_a_spot(client, as_user, engine):
    spots = {s["name"]: s for s in client.get("/v1/spots", headers=as_user("jd")).json()}
    gwm = spots["GWM 12"]
    changed = client.patch(f"/v1/spots/{gwm['id']}", json={"name": "GWM 12a", "x": 49.0320, "y": 8.3050,
                                                          "site_id": None, "note": "Neu eingemessen"},
                           headers=as_user("worf", **{"If-Match": gwm["etag"]}))
    assert changed.status_code == 200, changed.text
    moved = changed.json()
    assert (moved["name"], moved["x"], moved["y"], moved["site_id"]) == ("GWM 12a", 49.0320, 8.3050, None)
    assert moved["etag"] != gwm["etag"]
    stale = client.patch(f"/v1/spots/{gwm['id']}", json={"name": "GWM 12b"},
                         headers=as_user("worf", **{"If-Match": gwm["etag"]}))
    assert stale.status_code == 412
    with engine.connect() as conn:
        log = log_table(spot)
        last = conn.scalars(select(log.c.note).where(log.c.id_spot == gwm["id"]).order_by(log.c.id_l_spot.desc())).first()
    assert last == "Changed name, position, site: Neu eingemessen"


def test_redraw_a_site(client, as_user):
    site = client.post("/v1/sites", json={"name": "Rheinhafen", "border": HARBOUR}, headers=as_user("worf")).json()
    bigger = [*HARBOUR[:2], {"latitude": 49.0100, "longitude": 8.3340}, HARBOUR[3]]
    changed = client.patch(f"/v1/sites/{site['id']}", json={"border": bigger, "description": "Becken 2 und 3"},
                           headers=as_user("worf", **{"If-Match": site["etag"]}))
    assert changed.status_code == 200, changed.text
    assert changed.json()["border"] == bigger and changed.json()["description"] == "Becken 2 und 3"
    crossing = [bigger[0], bigger[2], bigger[1], bigger[3]]
    refused = client.patch(f"/v1/sites/{site['id']}", json={"border": crossing},
                           headers=as_user("worf", **{"If-Match": changed.json()["etag"]}))
    assert refused.status_code == 422


def test_changing_locations_needs_the_etag_and_the_role(client, as_user):
    site = client.get("/v1/sites", headers=as_user("jd")).json()[0]
    assert client.patch(f"/v1/sites/{site['id']}", json={"name": "X"}, headers=as_user("worf")).status_code == 428
    response = client.patch(f"/v1/sites/{site['id']}", json={"name": "X"},
                            headers=as_user("jd", **{"If-Match": site["etag"]}))
    assert response.status_code == 403
