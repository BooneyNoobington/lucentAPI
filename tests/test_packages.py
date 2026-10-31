import yaml

from lucentapi.package import FORMAT, Package


def test_export_as_json(client, as_user):
    response = client.get("/v1/package", headers=as_user("jd"))  # reading needs no role
    assert response.status_code == 200
    package = Package.model_validate(response.json())
    assert package.format == FORMAT
    anions = next(a for a in package.actions if a.name == "Anionen (IC)")
    assert [e.parameter for e in anions.effects] == ["Nitrat", "Chlorid", "Sulfat"]
    assert "Trinkwasserverordnung (TrinkwV)" in anions.documents
    assert anions.devices[0].number == 98 and str(anions.iteration) == "2025-02-01"
    nitrate = {(t.document, t.upper) for t in package.thresholds if t.parameter == "Nitrat"}
    assert nitrate == {("Trinkwasserverordnung (TrinkwV)", 50), ("Grundwasserverordnung (GrwV)", 50)}
    ripley = next(p for p in package.people if p.linux_user == "xx121")
    assert (ripley.organization, ripley.position, ripley.roles) == ("Umweltlabor Lucent", "Qualitätssicherung",
                                                                     ["analyst"])
    nitrate_parameter = next(p for p in package.parameters if p.name == "Nitrat")
    assert nitrate_parameter.unit == "mg/L"  # a unit by its first notation


def test_export_as_yaml_reads_back_the_same(client, as_user):
    as_json = client.get("/v1/package", headers=as_user("jd")).json()
    response = client.get("/v1/package?format=yaml", headers=as_user("jd"))
    assert response.status_code == 200 and response.headers["content-type"].startswith("application/yaml")
    text = response.text
    assert text.startswith("# yaml-language-server: $schema=http://testserver/schemas/lucent-package-1.json\n")
    assert Package.model_validate(yaml.safe_load(text)) == Package.model_validate(as_json)


def test_the_export_follows_changes(client, as_user):
    site = client.post("/v1/sites", headers=as_user("worf"), json={"name": "Rheinhafen", "border": [
        {"latitude": 49.016, "longitude": 8.325}, {"latitude": 49.016, "longitude": 8.332},
        {"latitude": 49.012, "longitude": 8.332}]}).json()
    package = Package.model_validate(client.get("/v1/package", headers=as_user("jd")).json())
    exported = next(s for s in package.sites if s.name == "Rheinhafen")
    assert [(p.latitude, p.longitude) for p in exported.border] == [(p["latitude"], p["longitude"])
                                                                    for p in site["border"]]


def test_exporting_needs_a_token(client):
    assert client.get("/v1/package").status_code == 401


def test_the_schema_is_public(client):
    response = client.get("/schemas/lucent-package-1.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["$schema"].startswith("https://json-schema.org/")
    assert schema["properties"]["format"]["const"] == FORMAT
    assert {"Action", "Threshold", "Unit"} <= set(schema["$defs"])


def test_unknown_fields_are_refused():
    # A typo in a package must not pass silently.
    try:
        Package.model_validate({"format": FORMAT, "parameters": [{"name": "Nitrat", "unti": "mg/L"}]})
    except ValueError as error:
        assert "unti" in str(error)
    else:
        raise AssertionError("an unknown field passed")
