from pathlib import Path

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


# Importing

from sqlalchemy import select  # noqa: E402

from lucentapi import demo, devdb, package_import  # noqa: E402
from lucentapi.db import make_engine  # noqa: E402
from lucentapi.package import export  # noqa: E402
from lucentapi.schema import action, log_table, parameter  # noqa: E402

YAML = {"Content-Type": "application/yaml"}

CHANGES = """format: lucent/1
description: Mehr Anionen
parameters:
- {name: Fluorid, unit: mg/L, description: F⁻}
thresholds:
- {parameter: Fluorid, document: Trinkwasserverordnung (TrinkwV), upper: 1.5}
- {parameter: Nitrat, document: Trinkwasserverordnung (TrinkwV), upper: 45}
"""


def test_the_demo_package_changes_nothing_in_the_demo(client, as_user):
    text = (Path(demo.__file__).parent / "data" / "demo.lucent.yaml").read_text()
    plan = client.post("/v1/package/plan", content=text, headers=as_user("jd", **YAML)).json()
    assert plan["problems"] == [] and plan["changes"] == [] and not plan["applied"]
    assert plan["summary"]["actions"] == {"create": 0, "update": 0, "unchanged": 10}


def test_plan_then_apply(client, as_user, engine):
    plan = client.post("/v1/package/plan", content=CHANGES, headers=as_user("jd", **YAML)).json()
    assert plan["problems"] == []
    assert [(c["section"], c["key"], c["change"], c["fields"]) for c in plan["changes"]] == [
        ("parameters", "Fluorid", "create", []),
        ("thresholds", "Fluorid in Trinkwasserverordnung (TrinkwV)", "create", []),
        ("thresholds", "Nitrat in Trinkwasserverordnung (TrinkwV)", "update", ["upper"]),
    ]
    with engine.connect() as conn:  # a plan changes nothing
        assert conn.scalar(select(parameter.c.id_parameter).where(parameter.c.name == "Fluorid")) is None

    assert client.post("/v1/package/apply", content=CHANGES, headers=as_user("jd", **YAML)).status_code == 403
    applied = client.post("/v1/package/apply", content=CHANGES, headers=as_user("grindel", **YAML))
    assert applied.status_code == 200 and applied.json()["applied"]
    exported = Package.model_validate(client.get("/v1/package", headers=as_user("jd")).json())
    assert next(t for t in exported.thresholds if t.parameter == "Nitrat" and "TrinkwV" in t.document).upper == 45
    with engine.connect() as conn:
        log = log_table(parameter)
        assert conn.scalars(select(log.c.note).order_by(log.c.id_l_parameter.desc())).first() == \
            "Created from a package: Mehr Anionen"


def test_a_changed_method_needs_a_new_iteration(client, as_user):
    changed = """format: lucent/1
actions:
- name: pH-Wert
  iteration: 2024-09-15
  effects:
  - {parameter: pH-Wert, uncertainty: 0.02, uncertainty_class: absolute}
"""
    result = client.post("/v1/package/apply", content=changed, headers=as_user("grindel", **YAML))
    assert result.status_code == 422 and not result.json()["applied"]
    problem = result.json()["problems"][0]
    assert (problem["path"], problem["line"]) == ("actions[0].effects", 5)
    assert "new iteration" in problem["message"]

    new_version = changed.replace("2024-09-15", "2026-11-01")
    result = client.post("/v1/package/apply", content=new_version, headers=as_user("grindel", **YAML)).json()
    assert result["applied"] and result["changes"][0]["key"] == "pH-Wert (2026-11-01)"
    versions = [a for a in client.get("/v1/actions", headers=as_user("jd")).json() if a["name"] == "pH-Wert"]
    assert len(versions) == 3  # 2021, 2024 and the new one


def test_problems_come_with_path_and_line(client, as_user):
    broken = """format: lucent/1
parameters:
- {name: Fluorid, unit: mg/dl}
devices:
- {name: Waage, number: 7, operater: Umweltlabor Lucent}
"""
    response = client.post("/v1/package/plan", content=broken, headers=as_user("jd", **YAML))
    assert response.status_code == 422 and response.json()["error"]["code"] == "package_invalid"
    assert response.json()["error"]["details"][0]["line"] == 5  # the misspelled operator
    plan = client.post("/v1/package/plan", content=broken.replace("operater", "operator"),
                       headers=as_user("jd", **YAML)).json()
    assert [(p["path"], p["line"]) for p in plan["problems"]] == [("parameters[0].unit", 3)]
    assert "mg/dl" in plan["problems"][0]["message"]


def test_yaml_is_read_strictly(client, as_user):
    norway = "format: lucent/1\nmatrices:\n- {name: NO, description: Norwegisches Grundwasser}\n"
    plan = client.post("/v1/package/plan", content=norway, headers=as_user("jd", **YAML)).json()
    assert plan["changes"][0]["key"] == "NO"  # a name, not false
    unreadable = client.post("/v1/package/plan", content="format: lucent/1\nmatrices: [", headers=as_user("jd", **YAML))
    assert unreadable.status_code == 422 and unreadable.json()["error"]["code"] == "package_unreadable"


def test_json_packages_work_too(client, as_user):
    body = {"format": "lucent/1", "matrices": [{"name": "Oberflächenwasser"}]}
    plan = client.post("/v1/package/plan", json=body, headers=as_user("jd")).json()
    assert plan["summary"]["matrices"] == {"create": 1, "update": 0, "unchanged": 0}


def test_nobody_deactivates_themselves_by_package(client, as_user):
    me = "format: lucent/1\npeople:\n- {given_name: System, surname: User, linux_user: grindel, active: false}\n"
    plan = client.post("/v1/package/plan", content=me, headers=as_user("grindel", **YAML)).json()
    assert plan["problems"][0]["message"] == "Nobody can deactivate themselves."


def test_a_fresh_lucentbase_from_the_package_exports_the_same_package():
    """What moving a lab's master data to a new lucentBASE does."""
    engine = make_engine("sqlite://")
    devdb.create(engine)
    with engine.begin() as conn:
        demo.ensure_initial_data(conn)
    result = package_import.apply(engine, demo.master_data(), demo.SYSTEM, "Umzug")
    assert result.applied and result.summary["thresholds"]["create"] == 18
    with engine.connect() as conn:
        assert export(conn, demo.master_data().description) == demo.master_data()
        assert conn.scalar(select(action.c.id_action).where(action.c.name == "Anionen (IC)")) == 5  # the same ids
