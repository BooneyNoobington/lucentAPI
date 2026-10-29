def test_people(client, as_user):
    people = client.get("/v1/people", headers=as_user("grindel")).json()
    assert [p["unix_account"] for p in people][:2] == ["jd", "worf"]  # by surname
    assert len(people) == 6


def test_sites_have_address_and_border(client, as_user):
    sites = {s["id"]: s for s in client.get("/v1/sites", headers=as_user("grindel")).json()}
    assert sites[2]["address"] == {"street": "Am Wasserwerk", "house_number": "1", "addition": "a",
                                   "zip": "76227", "district": "Durlach", "city": "Karlsruhe"}
    assert sites[2]["border"][0] == {"latitude": 48.999, "longitude": 8.451}
    assert len(sites[2]["border"]) == 4


def test_spots(client, as_user):
    spots = {s["id"]: s for s in client.get("/v1/spots", headers=as_user("grindel")).json()}
    assert spots[3]["name"] == "Garten"
    assert (spots[3]["x"], spots[3]["y"]) == (49.006097, 8.382112)


def test_actions_carry_what_they_measure(client, as_user):
    actions = {a["id"]: a for a in client.get("/v1/actions", headers=as_user("grindel")).json()}
    anions = actions[5]
    assert anions["standard"] == "DIN EN ISO 10304-1"
    assert [v["variable_id"] for v in anions["variables"]] == [4, 5, 6]
    assert anions["variables"][0]["upper_limit"] == 50
    assert anions["depends_on"] == [1]
    assert anions["implies"] == [4]
    assert sorted(anions["collection_class_ids"]) == [3, 4]


def test_catalog(client, as_user):
    headers = as_user("grindel")
    units = {u["id"]: u for u in client.get("/v1/units", headers=headers).json()}
    assert units[3]["notations"] == ["µS/cm", "µS cm⁻¹"]
    classes = {c["id"]: c for c in client.get("/v1/collection-classes", headers=headers).json()}
    assert classes[2]["action_ids"] == [8, 9]
    assert len(client.get("/v1/variables", headers=headers).json()) == 16
    assert len(client.get("/v1/matrices", headers=headers).json()) == 4
    assert len(client.get("/v1/dimensions", headers=headers).json()) == 5
    assert client.get("/v1/crs", headers=headers).json()[0]["epsg"] == 4326


def test_sops(client, as_user):
    sops = {s["id"]: s for s in client.get("/v1/sops", headers=as_user("grindel")).json()}
    assert [d["name"] for d in sops[4]["documents"]] == ["Anionen mittels IC, Rev. 4", "Gerätehandbuch IC-3"]
    assert {"person_id": 28, "role": "Freigabe"} in sops[4]["people"]
    assert sops[10]["active"] is False


def test_devices_carry_etags(client, as_user):
    devices = client.get("/v1/devices", headers=as_user("grindel")).json()
    assert all(d["etag"].startswith('"') for d in devices)
    assert len({d["etag"] for d in devices}) == len(devices)
