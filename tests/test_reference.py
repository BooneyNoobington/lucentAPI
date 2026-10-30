def test_people(client, as_user):
    people = client.get("/v1/people", headers=as_user("grindel")).json()
    assert [p["linux_user"] for p in people][:2] == ["jd", "worf"]  # by surname
    assert {"given_name": "System", "surname": "User", "linux_user": "grindel"}.items() <= people[-1].items()
    assert len(people) == 6


def test_configuration(client, as_user):
    configuration = client.get("/v1/configuration", headers=as_user("grindel")).json()
    assert configuration["sampling_time_parameter_id"] == 1
    assert "time series" in configuration["collection_classes"]


def test_sites_have_address_and_border(client, as_user):
    sites = {s["id"]: s for s in client.get("/v1/sites", headers=as_user("grindel")).json()}
    assert sites[2]["address"] == {"street": "Am Wasserwerk", "house_number": 1, "addition": "a",
                                   "zip": "76227", "district": "Durlach", "city": "Karlsruhe"}
    assert sites[2]["border"][0] == {"latitude": 48.999, "longitude": 8.451}
    assert len(sites[2]["border"]) == 4


def test_spots(client, as_user):
    spots = {s["id"]: s for s in client.get("/v1/spots", headers=as_user("grindel")).json()}
    assert spots[3]["name"] == "Garten"
    assert (spots[3]["x"], spots[3]["y"]) == (49.006097, 8.382112)


def test_actions_carry_effects_documents_and_dependencies(client, as_user):
    actions = {a["id"]: a for a in client.get("/v1/actions", headers=as_user("grindel")).json()}
    anions = actions[5]
    assert anions["iteration"] == "2025-02-01T00:00:00Z"
    assert [e["parameter_id"] for e in anions["effects"]] == [7, 8, 9]
    assert anions["effects"][0]["uncertainty_class"] == "relative"
    assert anions["document_ids"] == [4, 11, 12, 13]
    assert anions["dependencies"] == [
        {"action_id": 1, "dependency_class": "strict", "description": "Erst nach der Probenahme"},
        {"action_id": 4, "dependency_class": "possibility",
         "description": "Ionenbilanz mit der Leitfähigkeit plausibilisieren"},
    ]
    assert {"person_id": 5, "role": "manager"} in anions["people"]
    assert [a["active"] for a in actions.values() if a["name"] == "pH-Wert"] == [True, False]


def test_catalog(client, as_user):
    headers = as_user("grindel")
    units = {u["id"]: u for u in client.get("/v1/units", headers=headers).json()}
    assert units[2]["notations"] == ["µS/cm", "µS cm⁻¹"]
    parameters = {p["id"]: p for p in client.get("/v1/parameters", headers=headers).json()}
    assert len(parameters) == 17
    assert (parameters[1]["value_class"], parameters[4]["value_class"]) == ("datetime", "boolean")
    assert client.get("/v1/collection-classes", headers=headers).json()[0] == "spot sample"
    assert len(client.get("/v1/matrices", headers=headers).json()) == 4
    assert len(client.get("/v1/dimensions", headers=headers).json()) == 4
    assert client.get("/v1/crs", headers=headers).json()[0]["epsg"] == 4326
    assert client.get("/v1/coefficients", headers=headers).json() == [
        {"id": 1, "effect1_id": 6, "effect2_id": 2,
         "description": "Die Leitfähigkeit wird auf die Probentemperatur bezogen"}]


def test_documents_and_thresholds(client, as_user):
    headers = as_user("grindel")
    documents = {d["name"]: d for d in client.get("/v1/documents", headers=headers).json()}
    assert documents["Trinkwasserverordnung (TrinkwV)"]["action_ids"] == [1, 2, 3, 4, 5, 6, 7]
    assert documents["SOP-005 Entwurf Rev. 3"]["accepted"] is False
    assert len(documents["SOP-002 pH-Wert"]["sha256"]) == 64
    thresholds = client.get("/v1/thresholds", headers=headers).json()
    nitrate = [t for t in thresholds if t["parameter_id"] == 7]
    assert {(t["document_id"], t["upper_limit"]) for t in nitrate} == {(12, 50), (13, 50)}


def test_devices(client, as_user):
    devices = client.get("/v1/devices", headers=as_user("grindel")).json()
    assert all(isinstance(d["number"], int) and d["etag"].startswith('"') for d in devices)
    assert len({d["etag"] for d in devices}) == len(devices)
    assert {d["operator_id"] for d in devices} == {2}  # an organization, as in lucentBASE
