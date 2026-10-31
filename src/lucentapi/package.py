"""lucent packages: master data as one document, in YAML or JSON.

A package describes a lucentBASE's master data: organizations, people and their roles, units,
parameters, methods (actions with their effects), documents and their thresholds, devices and
locations. It is what lucentBASE holds, written for people: entries refer to each other by
their natural keys (a parameter by name, an action by name and iteration, a device by name and
number), never by database ids, so a package can be written by hand, kept in git, and moved
from one lucentBASE to another.

`Package.model_json_schema()` is the format's JSON Schema; lucentAPI publishes it at
`/schemas/lucent-package-1.json`, and editors use it to check and complete package files.
`export(conn)` writes a lucentBASE's master data as a package.

Measurements (collections, values, raw values) are not master data and have their own import.
"""

import datetime as dt
import re
from typing import Literal

import yaml

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, select

from .config import ROLES
from .db import visible
from .geometry import as_text, parse_polygon
from .models import Role, ValueClass
from .schema import (
    ACTION_ROLES,
    city,
    coefficient,
    crs,
    device,
    dimension,
    district,
    effect,
    house,
    j_action_action,
    j_action_device,
    j_action_link,
    j_action_person,
    j_person_organization,
    j_person_role,
    link,
    matrix,
    organization,
    parameter,
    person,
    role,
    site,
    spot,
    street,
    threshold,
    unit,
    unit_notation,
)
from .schema import action as action_table  # beside Action, the package entry

FORMAT = "lucent/1"
SCHEMA_PATH = "/schemas/lucent-package-1.json"

ActionRole = Literal[ACTION_ROLES]


class Entry(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Organization(Entry):
    name: str = Field(max_length=50)
    shorthand: str | None = Field(None, max_length=15)
    relation: str | None = Field(None, max_length=25, description="e.g. Labor, Hersteller, Kunde")


class Person(Entry):
    given_name: str = Field(max_length=50)
    surname: str = Field(max_length=50)
    linux_user: str | None = Field(None, max_length=50,
                                   description="The account the person signs in with. People are referred to by it.")
    organization: str | None = Field(None, description="An organization's name.")
    position: str | None = Field(None, max_length=20, description="In their organization.")
    active: bool = Field(True, description="Whether the person can sign in.")
    roles: list[Role] = Field([], description="What the person may do.")


class ReferenceSystem(Entry):
    name: str = Field(max_length=25)
    epsg: int
    description: str | None = Field(None, max_length=150)


class Dimension(Entry):
    name: str = Field(max_length=40)
    description: str | None = Field(None, max_length=50)


class Unit(Entry):
    name: str = Field(max_length=40)
    description: str | None = Field(None, max_length=100)
    dimension: str | None = Field(None, description="A dimension's name.")
    notations: list[str] = Field([], description="Ways to write the unit, e.g. mg/L. Each is unique, and others "
                                                 "refer to the unit by its name or by any of them.")


class Matrix(Entry):
    name: str = Field(max_length=40)
    description: str | None = Field(None, max_length=250)


class Parameter(Entry):
    name: str = Field(max_length=30)
    description: str | None = Field(None, max_length=80)
    unit: str | None = Field(None, description="A unit's name or notation.")
    value_class: ValueClass = Field("decimal", description="Which kind of raw value the parameter takes.")


class Device(Entry):
    name: str = Field(max_length=50)
    number: int = Field(description="Together with the name, it identifies the device.")
    manufacturer: str | None = Field(None, description="An organization's name.")
    operator: str | None = Field(None, description="An organization's name.")
    active: bool = True


class Document(Entry):
    """A link in lucentBASE: an SOP, a standard, a regulation, a manual; a file or a web page."""

    name: str = Field(max_length=50)
    uri: str | None = Field(None, max_length=500)
    file_path: str | None = Field(None, max_length=500)
    accepted: bool = False
    sha256: str | None = Field(None, max_length=64)


class GeoPoint(Entry):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class Address(Entry):
    street: str = Field(max_length=100)
    house_number: int
    addition: str | None = Field(None, max_length=5)
    zip: str = Field(max_length=10)
    district: str = Field(max_length=60)
    city: str = Field(max_length=60)


class Site(Entry):
    name: str = Field(max_length=20)
    description: str | None = Field(None, max_length=50)
    address: Address | None = None
    border: list[GeoPoint] = Field([], description="The corners in order, without repeating the first.")
    reference_system: str | None = Field(None, description="A reference system's name.")


class Spot(Entry):
    name: str = Field(max_length=50)
    description: str | None = Field(None, max_length=50)
    latitude: float | None = Field(None, ge=-90, le=90, description="lucentBASE's spot.x.")
    longitude: float | None = Field(None, ge=-180, le=180, description="lucentBASE's spot.y.")
    z: float | None = None
    z_class: str | None = Field(None, max_length=20)
    z_unit: str | None = Field(None, description="A unit's name or notation.")
    reference_system: str | None = Field(None, description="A reference system's name.")
    site: str | None = Field(None, description="The site the spot lies in.")


class DeviceRef(Entry):
    name: str
    number: int


class Assignment(Entry):
    person: str = Field(description="A person's linux_user, or their given name and surname if they have none.")
    role: ActionRole


class Dependency(Entry):
    action: str = Field(description="The name of the action this one depends on.")
    iteration: dt.date | dt.datetime = Field(description="Which version of it.")
    dependency_class: Literal["strict", "possibility"] = "strict"
    description: str | None = Field(None, max_length=100)


class Effect(Entry):
    parameter: str = Field(description="A parameter's name. Each parameter once per action.")
    uncertainty: float | None = None
    uncertainty_class: Literal["absolute", "relative"] | None = None
    uncertainty_unit: str | None = Field(None, description="A unit's name or notation, for an absolute uncertainty.")
    calculation: str | None = Field(None, max_length=40)


class Action(Entry):
    """A method, in one version: the same name with a later iteration is a new version."""

    name: str = Field(max_length=50)
    iteration: dt.date | dt.datetime = Field(description="The version, as the date it took effect.")
    active: bool = True
    description: str | None = Field(None, max_length=250)
    documents: list[str] = Field([], description="Documents' names. Their thresholds are this action's limits.")
    devices: list[DeviceRef] = []
    people: list[Assignment] = Field([], description="Who works on, manages or audits the action.")
    depends_on: list[Dependency] = []
    effects: list[Effect] = Field([], description="What the action yields: one parameter each.")


class Threshold(Entry):
    parameter: str = Field(description="A parameter's name.")
    document: str = Field(description="The document (e.g. a regulation) that sets the limit.")
    lower: float | None = None
    upper: float | None = None
    description: str | None = Field(None, max_length=100)


class EffectRef(Entry):
    action: str
    iteration: dt.date | dt.datetime
    parameter: str


class Coefficient(Entry):
    effects: list[EffectRef] = Field(min_length=2, max_length=2, description="The two effects that belong together.")
    description: str | None = Field(None, max_length=250)


class Package(Entry):
    """A lucentBASE's master data. Sections may be left out; entries refer to each other by name."""

    model_config = ConfigDict(extra="forbid", title="lucent package")

    format: Literal["lucent/1"] = Field(description="The package format and its version.")
    description: str | None = None
    organizations: list[Organization] = []
    people: list[Person] = []
    reference_systems: list[ReferenceSystem] = []
    dimensions: list[Dimension] = []
    units: list[Unit] = []
    matrices: list[Matrix] = []
    parameters: list[Parameter] = []
    devices: list[Device] = []
    documents: list[Document] = []
    sites: list[Site] = []
    spots: list[Spot] = []
    actions: list[Action] = []
    thresholds: list[Threshold] = []
    coefficients: list[Coefficient] = []


def json_schema() -> dict:
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", **Package.model_json_schema()}


# Export


def iteration(value: dt.datetime) -> dt.date | dt.datetime:
    """A version date as a date when it has no time of day, as people write it."""
    return value.date() if value.time() == dt.time() else value


def export(conn: Connection, description: str | None = None) -> Package:
    """The visible master data of a lucentBASE, in the order it was entered: the same data gives the same
    file, new entries come last, and a package imported into a fresh lucentBASE gets the same ids."""

    def rows(table, *order):
        return conn.execute(select(table).where(visible(table)).order_by(*order)).mappings().all()

    organizations = {r["id_organization"]: r for r in rows(organization, organization.c.id_organization)}
    units = {r["id_unit"]: r for r in rows(unit, unit.c.id_unit)}
    notations: dict[int, list[str]] = {}
    for r in rows(unit_notation, unit_notation.c.id_unit_notation):
        if r["notation"]:
            notations.setdefault(r["id_unit"], []).append(r["notation"])
    dimensions = {r["id_dimension"]: r for r in rows(dimension, dimension.c.id_dimension)}
    systems = {r["id_crs"]: r for r in rows(crs, crs.c.id_crs)}
    parameters = {r["id_parameter"]: r for r in rows(parameter, parameter.c.id_parameter)}
    documents = {r["id_link"]: r for r in rows(link, link.c.id_link)}
    devices = {r["id_device"]: r for r in rows(device, device.c.id_device)}
    actions = {r["id_action"]: r for r in rows(action_table, action_table.c.id_action)}
    effects = {r["id_effect"]: r for r in rows(effect, effect.c.id_effect)}
    sites = {r["id_site"]: r for r in rows(site, site.c.id_site)}
    people = {r["id_person"]: r for r in rows(person, person.c.id_person)}

    def name(table: dict, id_, column="name"):
        row = table.get(id_)
        return row[column] if row is not None else None

    def unit_ref(id_):
        """A unit by its first notation, which reads best, else by its name."""
        if id_ not in units:
            return None
        return (notations.get(id_) or [units[id_]["name"]])[0]

    def person_ref(id_):
        p = people[id_]
        return p["linux_user"] or f"{p['given_name']} {p['surname']}"

    positions = {(r["id_person"], r["id_organization"]): r["position"] for r in rows(j_person_organization)}
    granted: dict[int, set[str]] = {}
    for person_id, role_name in conn.execute(
            select(j_person_role.c.id_person, role.c.name).select_from(j_person_role.join(role))
            .where(visible(j_person_role), visible(role))):
        granted.setdefault(person_id, set()).add(role_name)

    addresses = {r["id_house"]: r for r in conn.execute(
        select(house.c.id_house, house.c.number, house.c.number_addition, street.c.name.label("street"),
               district.c.name.label("district"), district.c.zip, city.c.name.label("city"))
        .join(street, street.c.id_street == house.c.id_street)
        .join(district, district.c.id_district == street.c.id_district)
        .join(city, city.c.id_city == district.c.id_city)
        .where(visible(house))).mappings()}
    borders = dict(conn.execute(select(site.c.id_site, as_text(site.c.borders))).all())

    def address(id_house):
        a = addresses.get(id_house)
        return None if a is None else Address(street=a["street"], house_number=a["number"],
                                              addition=a["number_addition"], zip=a["zip"], district=a["district"],
                                              city=a["city"])

    by_action: dict[str, dict[int, list]] = {key: {} for key in ("documents", "devices", "people", "depends_on")}
    for r in rows(j_action_link, j_action_link.c.id_j_action_link):
        if r["id_link"] in documents:
            by_action["documents"].setdefault(r["id_action"], []).append(documents[r["id_link"]]["name"])
    for r in rows(j_action_device, j_action_device.c.id_j_action_device):
        if r["id_device"] in devices:
            d = devices[r["id_device"]]
            by_action["devices"].setdefault(r["id_action"], []).append(DeviceRef(name=d["name"], number=d["number"]))
    for r in rows(j_action_person, j_action_person.c.id_j_action_person):
        if r["id_person"] in people:
            by_action["people"].setdefault(r["id_action"], []).append(
                Assignment(person=person_ref(r["id_person"]), role=r["role"]))
    for r in rows(j_action_action, j_action_action.c.id_j_action_action):
        if r["id_action2"] in actions:
            other = actions[r["id_action2"]]
            by_action["depends_on"].setdefault(r["id_action1"], []).append(Dependency(
                action=other["name"], iteration=iteration(other["iteration"]),
                dependency_class=r["dependency_class"], description=r["description"]))

    def effect_ref(id_effect):
        e = effects[id_effect]
        a = actions[e["id_action"]]
        return EffectRef(action=a["name"], iteration=iteration(a["iteration"]),
                         parameter=parameters[e["id_parameter"]]["name"])

    return Package(
        format=FORMAT,
        description=description,
        organizations=[Organization(name=r["name"], shorthand=r["shorthand"], relation=r["relation"])
                       for r in organizations.values() if r["name"]],
        people=[Person(given_name=p["given_name"], surname=p["surname"], linux_user=p["linux_user"],
                       organization=name(organizations, p["id_organization"]),
                       position=positions.get((p["id_person"], p["id_organization"])), active=bool(p["active"]),
                       roles=sorted(granted.get(p["id_person"], set()) & set(ROLES), key=ROLES.index))
                for p in people.values()],
        reference_systems=[ReferenceSystem(name=r["name"], epsg=r["epsg"], description=r["description"])
                           for r in systems.values()],
        dimensions=[Dimension(name=r["name"], description=r["description"]) for r in dimensions.values()],
        units=[Unit(name=r["name"], description=r["description"], dimension=name(dimensions, r["id_dimension"]),
                    notations=notations.get(r["id_unit"], [])) for r in units.values()],
        matrices=[Matrix(name=r["name"], description=r["description"]) for r in rows(matrix, matrix.c.id_matrix)],
        parameters=[Parameter(name=r["name"], description=r["description"], unit=unit_ref(r["id_unit"]),
                              value_class=r["value_class"]) for r in parameters.values()],
        devices=[Device(name=r["name"], number=r["number"], manufacturer=name(organizations, r["id_manufacturer"]),
                        operator=name(organizations, r["id_operator"]), active=bool(r["active"]))
                 for r in devices.values()],
        documents=[Document(name=r["name"], uri=r["uri"], file_path=r["file_path"], accepted=bool(r["accepted"]),
                            sha256=r["sha256"]) for r in documents.values()],
        sites=[Site(name=r["name"], description=r["description"], address=address(r["id_house"]),
                    border=[GeoPoint(latitude=lat, longitude=lon) for lon, lat in parse_polygon(borders.get(r["id_site"]))],
                    reference_system=name(systems, r["id_crs"])) for r in sites.values()],
        spots=[Spot(name=r["name"], description=r["description"], latitude=r["x"], longitude=r["y"], z=r["z"],
                    z_class=r["z_class"], z_unit=unit_ref(r["z_id_unit"]), reference_system=name(systems, r["id_crs"]),
                    site=name(sites, r["id_site"])) for r in rows(spot, spot.c.id_spot)],
        actions=[Action(
            name=a["name"], iteration=iteration(a["iteration"]), active=bool(a["active"]), description=a["description"],
            documents=by_action["documents"].get(a["id_action"], []), devices=by_action["devices"].get(a["id_action"], []),
            people=by_action["people"].get(a["id_action"], []), depends_on=by_action["depends_on"].get(a["id_action"], []),
            effects=[Effect(parameter=parameters[e["id_parameter"]]["name"], uncertainty=e["uncertainty"],
                            uncertainty_class=e["uncertainty_class"], uncertainty_unit=unit_ref(e["uncertainty_id_unit"]),
                            calculation=e["calculation"])
                     for e in effects.values() if e["id_action"] == a["id_action"] and e["id_parameter"] in parameters],
        ) for a in actions.values()],
        thresholds=[Threshold(parameter=parameters[r["id_parameter"]]["name"], document=documents[r["id_link"]]["name"],
                              lower=r["lower_limit"], upper=r["upper_limit"], description=r["description"])
                    for r in rows(threshold, threshold.c.id_threshold)
                    if r["id_parameter"] in parameters and r["id_link"] in documents],
        coefficients=[Coefficient(effects=[effect_ref(r["id_effect1"]), effect_ref(r["id_effect2"])],
                                  description=r["description"])
                      for r in rows(coefficient, coefficient.c.id_coefficient)
                      if r["id_effect1"] in effects and r["id_effect2"] in effects],
    )


class StrictLoader(yaml.SafeLoader):
    """YAML 1.2's booleans: only true and false, so that a matrix called NO stays a name."""


StrictLoader.yaml_implicit_resolvers = {
    first: [(tag, regexp) for tag, regexp in resolvers if tag != "tag:yaml.org,2002:bool"]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
StrictLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF"))


def load_yaml(text: str):
    return yaml.load(text, Loader=StrictLoader)  # noqa: S506 (a safe loader)


def yaml_lines(text: str):
    """A function from a path in the package (("actions", 3, "effects")) to its line in the YAML text."""
    try:
        root = yaml.compose(text, Loader=StrictLoader)  # noqa: S506
    except yaml.YAMLError:
        root = None

    def line_of(path) -> int | None:
        node, line = root, None
        for part in path:
            if isinstance(node, yaml.MappingNode):  # the key's line: where `effects:` stands
                key, node = next(((k, v) for k, v in node.value if k.value == part), (None, None))
                mark = key.start_mark if key is not None else None
            elif isinstance(node, yaml.SequenceNode) and isinstance(part, int) and part < len(node.value):
                node = node.value[part]
                mark = node.start_mark
            else:
                node = mark = None
            if node is None:
                break
            line = mark.line + 1
        return line

    return line_of


def as_yaml(package: Package, schema_url: str | None = None) -> str:
    """The package as YAML, leaving out what is empty or a default, with a header for editors."""
    data = package.model_dump(mode="python", exclude_none=True, exclude_defaults=True)
    data = {"format": package.format, **data}  # every package names its format
    header = f"# yaml-language-server: $schema={schema_url}\n" if schema_url else ""
    return header + yaml.dump(data, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=120)


class _Dumper(yaml.SafeDumper):
    """Short entries on one line ({latitude: …, longitude: …}), longer ones as blocks; 50, not 50.0."""

    INLINE = 96

    def represent_dict(self, data):
        inline = all(not isinstance(v, dict | list) for v in data.values()) and _width(data) <= self.INLINE
        return self.represent_mapping("tag:yaml.org,2002:map", data, flow_style=inline)

    def represent_list(self, data):
        inline = all(not isinstance(v, dict | list) for v in data) and _width(data) <= self.INLINE
        return self.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=inline)

    def represent_float(self, data):
        return self.represent_int(int(data)) if data.is_integer() else super().represent_float(data)


_Dumper.add_representer(dict, _Dumper.represent_dict)
_Dumper.add_representer(list, _Dumper.represent_list)
_Dumper.add_representer(float, _Dumper.represent_float)


def _width(data) -> int:
    items = data.items() if isinstance(data, dict) else ((None, v) for v in data)
    return sum(len(str(k or "")) + len(str(v)) + 4 for k, v in items)
